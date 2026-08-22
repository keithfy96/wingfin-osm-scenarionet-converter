"""Put other cars on the road, driven by MetaDrive's own IDM.

    from traffic import load_plan, traffic_env
    env_class = traffic_env(ScenarioEnv, plan=load_plan(path), count=25, seed=0)

Like `drive.py`, `check_dataset.py` and `signal_control.py` this is not part of the package
and imports nothing from it, because it does not run on the same Python: the repo targets
3.10 and MetaDrive's own venv is 3.8. It reads the numbers `osm_scenario.traffic_routes`
wrote into `traffic/traffic.json` rather than importing the planner, exactly as
`signal_control` reads `metadata.signals` rather than importing `signal_plan`.

**What it does not do.** It does not decide where a road goes, how a junction is shaped, or
what a car is. Every route in the file was built by `ego_route.plan_route` and
`ego_route.route_polyline` - the same two functions that build the recorded car's drive - and
every car here is a MetaDrive vehicle running `TrajectoryIDMPolicy`. The only thing this
module supplies is *placement*: which routes carry cars this episode, where along them, and
what happens when one reaches the end.

**Why traffic is not in the dataset.** A recorded track has to be as long as the episode, and
the episode is as long as the recording - so a slow agent runs off the end of the tape and the
road empties around it while it is still driving. Live cars have no end. It is the same split
already made for `--lights tape` against `--lights live`, and it costs the same thing: a stock
ScenarioNet consumer reading the pickles sees an empty map.

**It fills the lidar.** `Lidar.perceive` scans `physics_world.dynamic_world`, and a scenario
holding one car returns 120 lasers of exactly 1.0. That is not a misconfiguration and this is
what fixes it.
"""

import json
import math
import random

TRAFFIC_VERSION = 1

DEFAULT_COUNT = 25
"""Cars on the road at once. Not the number of routes - the pool is usually larger, and one
route may carry several cars."""

MIN_GAP_M = 15.0
"""Least distance between two cars placed on the same route.

A car is about 4.5 m long, so this is roughly two car lengths of clear road either side. It
is a *placement* rule only: once the episode is running, how close cars get is IDM's business
and it is allowed to close right up in a queue."""

END_MARGIN_M = 20.0
"""No car is placed within this of a route's end.

`TrajectoryIDMPolicy.arrive_destination` fires within `DEST_REGION_RADIUS` (2 m) of the last
point, so a car placed at the end would be cleared on the frame it appeared."""


class TrafficError(RuntimeError):
    """Raised when a traffic plan cannot be used with this dataset."""


def load_plan(path):
    """Read `traffic.json`, refusing a file this build cannot use.

    The identity block is *not* checked here. `drive.py` checks it against the scenario's own
    metadata, where both halves are in hand; this function is also used by the tests, which
    have a plan and no dataset.
    """
    with open(path, encoding="utf-8") as handle:
        raw = json.load(handle)
    if not isinstance(raw, dict):
        raise TrafficError(f"{path} is not a traffic plan")
    version = raw.get("traffic_version")
    if version != TRAFFIC_VERSION:
        raise TrafficError(
            f"unsupported traffic_version {version!r}; this reader understands {TRAFFIC_VERSION}"
        )
    routes = raw.get("routes")
    if not isinstance(routes, list) or not routes:
        raise TrafficError(f"{path} contains no routes")
    for position, route in enumerate(routes):
        points = route.get("polyline")
        if not isinstance(points, list) or len(points) < 2:
            raise TrafficError(
                f"route {position} in {path} has no polyline to drive along"
            )
    return raw


def _cumulative(points):
    """Distance along a polyline at each vertex."""
    out = [0.0]
    # Indexed rather than zipped: ruff asks for `zip(..., strict=)` here and that keyword
    # does not exist on 3.8, which is the interpreter this file actually runs on.
    for index in range(len(points) - 1):
        before, after = points[index], points[index + 1]
        out.append(out[-1] + math.hypot(after[0] - before[0], after[1] - before[1]))
    return out


def _pose_at(points, lengths, distance):
    """(x, y), heading in radians at `distance` along the line.

    The heading is the direction of the segment the point falls on, never a difference across
    the point: at a vertex those are two different numbers and only one of them is where the
    car is pointing.
    """
    distance = min(max(distance, 0.0), lengths[-1])
    index = 0
    for index in range(len(lengths) - 1):
        if lengths[index + 1] >= distance:
            break
    first, second = points[index], points[index + 1]
    span = lengths[index + 1] - lengths[index]
    fraction = 0.0 if span <= 0.0 else (distance - lengths[index]) / span
    position = (
        first[0] + (second[0] - first[0]) * fraction,
        first[1] + (second[1] - first[1]) * fraction,
    )
    return position, math.atan2(second[1] - first[1], second[0] - first[0])


def build_manager(plan, count=DEFAULT_COUNT, seed=0):
    """A `BaseManager` subclass that keeps `count` cars on the roads in `plan`.

    Imports live in here rather than at module scope so this file can be read - and its
    placement arithmetic tested - without panda3d, exactly as `signal_control` does.
    """
    from metadrive.component.vehicle.vehicle_type import LVehicle, MVehicle, SVehicle, XLVehicle
    from metadrive.constants import DEFAULT_AGENT
    from metadrive.manager.base_manager import BaseManager
    from metadrive.manager.scenario_traffic_manager import ScenarioTrafficManager
    from metadrive.policy.idm_policy import TrajectoryIDMPolicy

    routes = []
    for entry in plan["routes"]:
        points = [(float(x), float(y)) for x, y in entry["polyline"]]
        routes.append(
            {
                "name": entry["name"],
                "points": points,
                "lengths": _cumulative(points),
                "speed_mps": float(entry.get("speed_mps", 0.0)),
            }
        )

    class LiveTrafficManager(BaseManager):
        """Cars generated from the reviewed lane graph, decided every reset."""

        IDM_ACT_BATCH_SIZE = ScenarioTrafficManager.IDM_ACT_BATCH_SIZE
        SIZES = (SVehicle, MVehicle, LVehicle, XLVehicle)

        def __init__(self):
            super().__init__()
            self.plan_routes = routes
            self.car_count = count
            self.episode_seed = seed
            self._placed = {}
            self._policy_index = 0
            self._to_clear = []
            self._episode = 0
            self.cars_spawned = 0
            self.cars_retired = 0
            self.collisions = 0
            self.on_road_low = 0
            self._crashed = set()
            self._route_of = {}
            # This manager's own generator, seeded once and **not** reseeded per episode.
            # `BaseEngine.seed` reseeds every manager from the scenario index at each reset,
            # so a manager drawing from `self.np_random` would place the traffic identically
            # every time a given scenario came round - and a one-scenario dataset would then
            # have the same cars in the same places for a whole training run, which is the
            # fixed layout live traffic exists to avoid. Measured: `junction-1` holds exactly
            # one scenario, so `global_random_seed` is 0 on every reset. The same trap
            # `signal_control.LiveSignalManager` documents, for the same reason.
            self._rng = random.Random(seed)

        # --- where the cars go ------------------------------------------------------------

        @property
        def ego_vehicle(self):
            return self.engine.agents[DEFAULT_AGENT]

        def _clear_of_ego(self, position):
            """MetaDrive's own rule, and its own constants.

            `filter_overlapping_car` exists because a car created on top of the ego is a
            collision on frame one that nothing caused.
            """
            try:
                ego = self.ego_vehicle
            except (KeyError, AttributeError):
                return True
            ahead, sideways = ego.convert_to_local_coordinates(list(position), ego.position)
            return not (
                abs(ahead) < ScenarioTrafficManager.GENERATION_FORWARD_CONSTRAINT
                and abs(sideways) < ScenarioTrafficManager.GENERATION_SIDE_CONSTRAINT
            )

        def _free_at(self, index, distance):
            return all(
                abs(taken - distance) >= MIN_GAP_M for taken in self._placed.get(index, ())
            )

        def _choose_placements(self, rng):
            """`count` (route, distance) pairs, spread over the pool.

            Routes are taken in a shuffled order and cycled, so cars are spread across the map
            rather than queued nose to tail on whichever route came first. A route that cannot
            take another car is simply passed over; the loop gives up rather than spinning,
            because a small pool on a small map genuinely cannot hold an arbitrary number.
            """
            order = list(range(len(self.plan_routes)))
            rng.shuffle(order)
            placements = []
            attempts = 0
            budget = self.car_count * 20 + 100
            while len(placements) < self.car_count and attempts < budget:
                index = order[len(placements) % len(order)] if order else 0
                attempts += 1
                route = self.plan_routes[index]
                usable = route["lengths"][-1] - END_MARGIN_M
                if usable <= 0.0:
                    continue
                distance = rng.uniform(0.0, usable)
                if not self._free_at(index, distance):
                    continue
                position, heading = _pose_at(route["points"], route["lengths"], distance)
                if not self._clear_of_ego(position):
                    continue
                self._placed.setdefault(index, []).append(distance)
                placements.append((index, distance, position, heading))
            return placements

        def _spawn(self, index, position, heading, rng):
            from metadrive.component.lane.point_lane import PointLane

            route = self.plan_routes[index]
            vehicle_class = self.SIZES[rng.randrange(len(self.SIZES))]
            config = ScenarioTrafficManager.get_traffic_v_config()
            vehicle = self.spawn_object(
                vehicle_class,
                position=list(position),
                heading=heading,
                vehicle_config=config,
            )
            # The whole polyline, not the part ahead: `TrajectoryIDMPolicy` finds the car on
            # it by projection, and `arrive_destination` measures to `traj.end`, so trimming
            # the start would move the finish line as well.
            self.add_policy(
                vehicle.name,
                TrajectoryIDMPolicy,
                vehicle,
                self.generate_seed(),
                PointLane(route["points"], 3.5),
                self._policy_index % self.IDM_ACT_BATCH_SIZE,
            )
            self._policy_index += 1
            self.cars_spawned += 1
            self._route_of[vehicle.name] = index
            return vehicle

        @property
        def episode_index(self):
            """Which episode this is, counting from 1.

            Reported rather than hidden: the layout is drawn from a generator advanced once
            per episode, so `(seed, episode_index)` is the whole of what decides it and an
            episode nobody can identify afterwards is not reproducible in any useful sense.
            """
            return self._episode

        # --- the episode ------------------------------------------------------------------

        def after_reset(self):
            self._placed = {}
            self._policy_index = 0
            self._to_clear = []
            self._episode += 1
            self.cars_spawned = 0
            self.cars_retired = 0
            self.collisions = 0
            self.on_road_low = self.car_count
            self._crashed = set()
            self._route_of = {}
            for index, _distance, position, heading in self._choose_placements(self._rng):
                self._spawn(index, position, heading, self._rng)

        def before_step(self, *args, **kwargs):
            """Act every car, and note the ones that have arrived.

            The `IDM_ACT_BATCH_SIZE` stagger is copied from
            `ScenarioTrafficManager.before_step` rather than invented: the speed half of IDM
            is the expensive part, and it runs for a fifth of the cars each step. Steering
            still runs for all of them every step.
            """
            self._to_clear = []
            self.on_road_low = min(self.on_road_low, len(self.spawned_objects))
            for vehicle in list(self.spawned_objects.values()):
                # Counted once per car per episode, not once per step: a crash flag stays up
                # while the two cars are still touching, so a per-step count would report one
                # collision as thirty and the number would say more about the frame rate than
                # about the driving.
                if getattr(vehicle, "crash_vehicle", False) and vehicle.name not in self._crashed:
                    self._crashed.add(vehicle.name)
                    self.collisions += 1
                if not self.engine.has_policy(vehicle.id, TrajectoryIDMPolicy):
                    continue
                policy = self.engine.get_policy(vehicle.name)
                if policy.arrive_destination:
                    self._to_clear.append(vehicle.name)
                    continue
                speed_control = self.episode_step % self.IDM_ACT_BATCH_SIZE == policy.policy_index
                vehicle.before_step(policy.act(speed_control))

        def after_step(self, *args, **kwargs):
            """Retire the arrived cars and put the same number back on the road.

            Without the replacement the map drains: every car that reaches the edge of the
            extract is gone for good, and a long episode ends on empty roads. A replacement is
            placed by the same rule as an opening car, so it cannot appear inside another one.
            """
            for name in self._to_clear:
                self.clear_objects([name])
                self._crashed.discard(name)
                self._route_of.pop(name, None)
            replacements = len(self._to_clear)
            self.cars_retired += replacements
            self._to_clear = []
            if not replacements:
                return {}
            self._placed = {}
            for vehicle in self.spawned_objects.values():
                index, distance = self._where(vehicle)
                if index is not None:
                    self._placed.setdefault(index, []).append(distance)
            for _index, _distance, position, heading in self._choose_replacements(replacements):
                self._spawn(_index, position, heading, self._rng)
            return {}

        def _choose_replacements(self, wanted):
            """New cars enter at the *start* of a route, which is where a road begins.

            A replacement dropped anywhere along a route would be a car appearing in the middle
            of a road other cars are already on. Every route in the pool starts at a lane the
            extract cut, so the start is the one place a car may arrive from off the map.
            """
            order = list(range(len(self.plan_routes)))
            self._rng.shuffle(order)
            out = []
            for index in order:
                if len(out) >= wanted:
                    break
                route = self.plan_routes[index]
                if not self._free_at(index, 0.0):
                    continue
                position, heading = _pose_at(route["points"], route["lengths"], 0.0)
                if not self._clear_of_ego(position):
                    continue
                self._placed.setdefault(index, []).append(0.0)
                out.append((index, 0.0, position, heading))
            return out

        def _where(self, vehicle):
            """Which route a car is on and how far along it is.

            The route index is **remembered from the spawn**, not recovered from the policy's
            destination: several routes in a pool end on the same exit lane and therefore at
            the same point - 60 routes over 18 exit lanes on `junction-1` - so matching on the
            endpoint would silently name the wrong one, and the spacing check would then be
            made against a road the car is not on. How far along is measured, because that is
            the part that moves.
            """
            index = self._route_of.get(vehicle.name)
            if index is None or not self.engine.has_policy(vehicle.id, TrajectoryIDMPolicy):
                return None, 0.0
            policy = self.engine.get_policy(vehicle.name)
            along, _lateral = policy.traj_to_follow.local_coordinates(vehicle.position)
            return index, float(along)

    return LiveTrafficManager


def traffic_env(base_class, *, plan, count=DEFAULT_COUNT, seed=0):
    """`base_class` with a live traffic manager registered beside whatever it already has.

    **It takes a class rather than returning one from scratch**, which
    `signal_control.live_signal_env` does not, and the difference matters: that function
    returns a whole `ScenarioEnv` subclass, so composing the two by assignment would leave
    only the last one standing. `--traffic live --lights live` would then silently drop the
    lights - and a red light is the only thing separating conflicting movements at a junction,
    because IDM has no give-way rule.

    `setup_engine` is the seam for this, and the only place where the timing is right: the
    engine exists and no episode has begun. Registering after a `reset()` would leave that
    episode's cars unmanaged, `before_reset` having already run for every manager the engine
    knew about.
    """
    manager_class = build_manager(plan, count=count, seed=seed)

    class TrafficScenarioEnv(base_class):
        def setup_engine(self):
            super().setup_engine()
            # A name of its own. `register_manager` asserts the name is free, and
            # `traffic_manager` is taken by the stock one that replays recorded tracks - which
            # our datasets have none of, so it stays registered and does nothing.
            self.engine.register_manager("live_traffic_manager", manager_class())

    return TrafficScenarioEnv
