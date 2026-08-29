"""Drive live traffic for several episodes and report how it drove, not whether it refused.

    <metadrive python> tools/traffic_probe.py workspaces/junction-1/scenarionet-10hz \
        --episodes 8 --count 25

`drive.py` runs one episode per process, so the eight- and twelve-episode tables in
`docs/reference/live-traffic.md` were built by hand each time somebody needed them. This is
that harness, kept: it holds one engine open across episodes and writes the acceptance
numbers a change to the driving has to be judged on.

**The number this exists for is `stop-go`** - how often a car comes to a standstill and pulls
away again while nothing is in front of it. Nothing else in the repo measures it, and it is
what "the cars keep starting and stopping through the turn" looks like as a figure.

Two traps, both already paid for elsewhere:

- **`--lost-lateral` off is how a collision count is compared.** `LOST_LATERAL_M` culls
  exactly the cars that were about to crash, and the first sweep of the speed profile read
  5 collisions against 17 purely because one column had 33 cars removed and the other 4.
- **Off the road is measured against the road surface**, the union of the drivable polygons
  the scenario carries, not against a distance to the nearest route vertex - which is
  meaningless when a lane feature carries only a polygon.
"""

import argparse
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import traffic as traffic_module  # noqa: E402

STOPPED_MPS = 0.2
"""Below this a car is standing still. The same figure `drive.py` reports the ego against."""

MOVING_MPS = 1.0
"""And above this it is going again. A band rather than one threshold, so a car trembling
either side of a single number is not counted as a hundred stop-go cycles."""


def _road_surface(scenario):
    """The union of the drivable polygons, in the simulator's frame."""
    from shapely.geometry import Polygon
    from shapely.ops import unary_union

    pieces = []
    for feature in (scenario.get("map_features") or {}).values():
        polygon = feature.get("polygon")
        if polygon is None or len(polygon) < 3:
            continue
        try:
            shape = Polygon(np.asarray(polygon)[:, :2])
        except (ValueError, TypeError):
            continue
        if shape.is_valid and shape.area > 0.0:
            pieces.append(shape)
    return unary_union(pieces) if pieces else None


def _episode(env, surface, cars):
    """One episode driven to its end, and what the traffic did in it."""
    from shapely.geometry import Point

    state = {}
    off_road = {}
    speeds = []
    steps = 0
    while True:
        _obs, _r, terminated, truncated, _info = env.step([0.0, 0.0])
        steps += 1
        for vehicle in list(cars.spawned_objects.values()):
            speed = float(vehicle.speed)
            speeds.append(speed)
            # `moved` before `stopped`: a car is placed at rest, and pulling away from
            # its own spawn is not a car that stopped. Only a standstill reached after it
            # was going counts.
            was = state.setdefault(vehicle.name, {"moved": False, "stopped": False, "cycles": 0})
            if speed > MOVING_MPS:
                if was["stopped"]:
                    was["stopped"] = False
                    was["cycles"] += 1
                was["moved"] = True
            elif speed < STOPPED_MPS and was["moved"]:
                was["stopped"] = True
            if surface is not None:
                point = Point(float(vehicle.position[0]), float(vehicle.position[1]))
                # By distance rather than by `contains`: a point exactly on the boundary is
                # on the road, and `contains` calls it off with a distance of zero.
                distance = float(surface.distance(point))
                if distance > 0.0:
                    off_road[vehicle.name] = max(off_road.get(vehicle.name, 0.0), distance)
        if terminated or truncated:
            break
    return {
        "steps": steps,
        "car_steps": len(speeds),
        "stop_go": sum(entry["cycles"] for entry in state.values()),
        "cars_seen": len(state),
        "mean_kph": 3.6 * float(np.mean(speeds)) if speeds else 0.0,
        "off_road_cars": len(off_road),
        "worst_off_road_m": max(off_road.values()) if off_road else 0.0,
        # Kept per car rather than reduced here: a count of cars that ever left the surface
        # says nothing about whether they clipped a kerb or drove a field, and the two are
        # the difference between a fault and a rounding.
        "off_road_m": sorted(off_road.values()),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset")
    parser.add_argument("--episodes", type=int, default=5)
    parser.add_argument("--count", type=int, default=25)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--traffic-file", default=None)
    parser.add_argument("--traffic-speed", choices=("profile", "flat"), default="profile")
    parser.add_argument("--traffic-give-way", choices=("on", "off"), default="on")
    parser.add_argument(
        "--lost-lateral",
        choices=("on", "off"),
        default="off",
        help="Whether a car more than LOST_LATERAL_M off its route is taken off the map. OFF "
        "by default here, and that is deliberate: it culls exactly the cars that were about "
        "to crash, so a collision count taken with it on is not comparable with one taken "
        "without. Turn it on to measure the rule itself, never to compare anything else.",
    )
    parser.add_argument("--json", default=None, help="Write the per-episode rows here too.")
    arguments = parser.parse_args(argv)

    from metadrive.envs.scenario_env import ScenarioEnv
    from metadrive.policy.replay_policy import ReplayEgoCarPolicy

    dataset = os.path.abspath(arguments.dataset)
    plan_path = arguments.traffic_file or os.path.join(
        os.path.dirname(dataset), "traffic", "traffic.json"
    )
    plan = traffic_module.load_plan(plan_path)

    if arguments.lost_lateral == "off":
        # A module global, read inside `before_step`, so this really does disable the cull.
        traffic_module.LOST_LATERAL_M = float("inf")

    env_class = traffic_module.traffic_env(
        ScenarioEnv,
        plan=plan,
        count=arguments.count,
        seed=arguments.seed,
        give_way=arguments.traffic_give_way == "on",
        follow_speed_profile=arguments.traffic_speed == "profile",
    )
    env = env_class(
        dict(
            data_directory=dataset,
            num_scenarios=1,
            use_render=False,
            horizon=100000,
            log_level=50,
            # The ego is replayed, as it was for every figure in
            # `docs/reference/live-traffic.md`: it is not the subject here, and left on
            # `EnvInputPolicy` with a zero action it coasts out of its own corridor and ends
            # the episode at step 99 - measured - taking the traffic measurement with it.
            agent_policy=ReplayEgoCarPolicy,
        )
    )
    rows = []
    try:
        for episode in range(arguments.episodes):
            env.reset(seed=0)
            cars = env.engine.live_traffic_manager
            surface = _road_surface(env.engine.data_manager.current_scenario)
            row = _episode(env, surface, cars)
            row.update(
                episode=episode,
                collisions=cars.collisions,
                retired=cars.cars_retired,
                lost=cars.cars_lost,
                gave_way=cars.gave_way,
            )
            rows.append(row)
            print(
                "episode {episode}: {steps} steps, {cars_seen} cars, "
                "stop-go {stop_go}, off-road {off_road_cars} (worst {worst_off_road_m:.2f} m), "
                "collisions {collisions}, retired {retired}, lost {lost}, "
                "mean {mean_kph:.1f} km/h".format(**row)
            )
    finally:
        env.close()

    car_minutes = sum(row["car_steps"] for row in rows) * 0.1 / 60.0
    totals = {key: sum(row[key] for row in rows) for key in
              ("stop_go", "off_road_cars", "collisions", "retired", "lost", "gave_way")}
    excursions = sorted(d for row in rows for d in row["off_road_m"])
    totals["p50"] = float(np.median(excursions)) if excursions else 0.0
    totals["p90"] = float(np.percentile(excursions, 90)) if excursions else 0.0
    totals["past_1m"] = sum(1 for d in excursions if d > 1.0)
    print(
        "\ntotal over {n} episode(s), {m:.1f} car-minutes\n"
        "  stop-go cycles      {stop_go} ({sgm:.2f} /car-min)   <- the number this exists for\n"
        "  cars off the road   {off_road_cars} (median {p50:.2f} m, p90 {p90:.2f} m, "
        "worst {worst:.2f} m, {past_1m} past 1 m)\n"
        "  collisions          {collisions} ({cm:.2f} /car-min)\n"
        "  routes completed    {retired}\n"
        "  cars lost           {lost}\n"
        "  gave way            {gave_way}\n"
        "  mean speed          {mean:.1f} km/h".format(
            n=len(rows), m=car_minutes,
            worst=max((row["worst_off_road_m"] for row in rows), default=0.0),
            sgm=totals["stop_go"] / car_minutes if car_minutes else 0.0,
            cm=totals["collisions"] / car_minutes if car_minutes else 0.0,
            mean=float(np.mean([row["mean_kph"] for row in rows])) if rows else 0.0,
            **totals
        )
    )
    if arguments.json:
        with open(arguments.json, "w") as handle:
            json.dump(rows, handle, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
