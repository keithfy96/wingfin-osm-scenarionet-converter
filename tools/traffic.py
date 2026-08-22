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

**The file's coordinates are not the simulator's, and the difference is the width of the
map.** `traffic.json` is written in the same frame as the scenario on disk, but
`ScenarioDataManager` loads every scenario with `centralize=True`
(`scenario_data_manager.py:76`), which moves the whole world so the recorded car starts at
the origin. So a point taken from the file and handed to MetaDrive unshifted lands
`metadata.old_origin_in_current_coordinate` away from the road it was computed for -
measured `[55.725, -75.469]` on `junction-1`, 93.8 m, which is the grass. `_episode_shift`
reads that field every reset, for the same reason `geodesy.py` and `policy_client.py` do.
"""

import json
import math
import random

import numpy as np

TRAFFIC_VERSION = 2

DEFAULT_COUNT = 25
"""Cars on the road at once. Not the number of routes - the pool is usually larger, and one
route may carry several cars."""

MIN_GAP_M = 15.0
"""Least distance between two cars being placed, measured **between the cars**.

A car is about 4.5 m long, so this is roughly two car lengths of clear road either side. It
is a *placement* rule only: once the episode is running, how close cars get is IDM's business
and it is allowed to close right up in a queue.

**Between the cars, not along one route**, and that distinction is the whole of it: the pool
holds many more routes than the map has ways onto it - 60 routes over **10 distinct start
points** on `junction-1`, the busiest carrying 8 - so two routes are usually the same tarmac
for their first hundred metres. Spaced per route, two cars on different routes were measured
**0.97 m** apart at reset, which is one car inside another, and about half of every episode's
collisions were the rear-end that followed. A car cannot see which route another car is
following; it can only see where it is."""

END_MARGIN_M = 20.0
"""No car is placed within this of a route's end.

`TrajectoryIDMPolicy.arrive_destination` fires within `DEST_REGION_RADIUS` (2 m) of the last
point, so a car placed at the end would be cleared on the frame it appeared."""


YIELD_LOOKAHEAD_M = 40.0
"""How far ahead a car looks along its own route for somewhere another car will cross it.

Long enough to see a junction from the approach at the speeds this map is posted at - 40 m
is about 3 s at 50 km/h - and short enough that a car is not held for a crossing two
junctions away."""

YIELD_SAMPLE_M = 2.0
"""Spacing of the look-ahead samples. Half a car length, so a crossing cannot fall between
two samples of one path and be missed."""

CONFLICT_WIDTH_M = 4.0
"""Two paths conflict where they pass within this of each other.

A lane is 3.5 m and a car about 2 m wide, so paths closer than this cannot both be used at
the same moment. It is a *path* separation, not a car separation: the cars are nowhere near
each other yet when the yield is decided, which is the point of deciding it early."""

CROSSING_MIN_DEG = 30.0
CROSSING_MAX_DEG = 150.0
"""The angle band that counts as crossing.

Below `CROSSING_MIN_DEG` the two paths are running together - the same lane, or a merge -
and that is IDM's own business: `FrontBackObjects.get_find_front_back_objs_single_lane`
tests `lane.point_on_lane` on the *other* car's bounding box, so it sees anything sharing
the tarmac ahead whatever route object that car is following. Yielding there instead would
be a follower and its leader each waiting for the other. Above `CROSSING_MAX_DEG` is
head-on, which on a correct one-way lane model does not happen and is not something a
give-way rule can fix."""

YIELD_BRAKE_MPS2 = 4.0
"""The deceleration a full brake command is taken to be worth, when working out how hard to
brake for a conflict this far off. Firm, and short of an emergency stop."""

STOP_MARGIN_M = 3.0
"""Where a yielding car aims to stop, short of the crossing point."""

LOST_LATERAL_M = 5.0
"""How far off its own route a car may be before it is taken off the map.

A car is not steered by anything that knows where the road is - `steering_control` is two
PIDs chasing the polyline - so one that has been carried wide does not necessarily come back,
and what it does instead is drive across whatever is there. Measured over three `junction-1`
episodes with the speed profile in force: the lateral error is 0.11 m at the median and
1.80 m at p90, and there were **7 excursions past 5 m against 11 past 3 m**. 3 m would pick up
cars still going round a junction; 5 m is a lane and a half, which nothing driving its route
needs.

It is a retirement, not a rescue: the car is cleared and a replacement enters at a route
start, the same path an arrival takes. Counted separately from `cars_retired`, because a car
picked up off the grass did not complete a route and must not be reported as one."""


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
        # Named, not just numbered. A version 1 file is the common case and it is one
        # rebuild away, so the message says what is missing rather than leaving the reader to
        # work out what a version number means.
        missing = (
            " - it carries no speed profile, so every car would take every corner at"
            " MetaDrive's flat 40 km/h"
            if version == 1
            else ""
        )
        raise TrafficError(
            f"{path} is traffic_version {version!r} and this reader understands "
            f"{TRAFFIC_VERSION}{missing}. Rebuild it with: "
            f"uv run osm-scenario traffic -w <workspace>"
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


def _look_ahead(points, lengths, distance):
    """Where a car will be over the next `YIELD_LOOKAHEAD_M`, and which way it faces.

    Sampled along the route rather than integrated forward in time: the shape of what is
    coming is fixed and known, and only *when* the car gets there depends on its speed.
    Returns the sample positions, the distance along the route of each, and the heading of
    each, so a conflict can be tested for angle as well as position.

    **Vectorised, and that is not a micro-optimisation.** This runs for every car every step,
    and a route carries about 900 vertices - `_pose_at` walks them from the start, so the
    first version cost **6.9 ms a step** at 25 cars, two thirds again on top of the 10.8 ms
    the same drive costs without it. `np.searchsorted` over the cumulative lengths makes it
    **0.5 ms**. The arrays are built once a reset by `_localised_routes`, so `asarray` here
    is free on the real path and only copies for a caller passing lists.
    """
    points = np.asarray(points, dtype=float)
    lengths = np.asarray(lengths, dtype=float)
    stops = distance + np.arange(0.0, YIELD_LOOKAHEAD_M + YIELD_SAMPLE_M, YIELD_SAMPLE_M)
    stops = stops[stops <= lengths[-1]]
    if len(stops) < 2:
        return None
    index = np.clip(np.searchsorted(lengths, stops, side="right") - 1, 0, len(points) - 2)
    first, second = points[index], points[index + 1]
    span = lengths[index + 1] - lengths[index]
    # A repeated vertex leaves a zero-length segment - `ego_route.COINCIDENT_M`'s subject -
    # and dividing by it would put NaN into a comparison that silently answers False.
    fraction = np.where(span > 0.0, (stops - lengths[index]) / np.where(span > 0.0, span, 1.0), 0.0)
    step = second - first
    here = first + step * fraction[:, None]
    heading = np.arctan2(step[:, 1], step[:, 0])
    return np.column_stack((here[:, 0], here[:, 1], stops, heading))


def _conflict(mine, theirs):
    """Where two look-aheads cross, as (my distance, their distance), or None.

    The *first* crossing along my own path, not the closest one: a car has to give way at the
    first place its path is taken, and a nearer conflict behind a further one is not something
    it will ever reach.

    Two prunings, in this order, because this runs for every pair of cars every step. The
    bounding boxes settle most pairs on a road network - two cars on different streets share no
    ground - and the angle is then worked out only for the samples that are actually close,
    rather than over the whole grid: the trig was two thirds of the cost when it was not.
    """
    if (
        mine[:, 0].max() + CONFLICT_WIDTH_M < theirs[:, 0].min()
        or theirs[:, 0].max() + CONFLICT_WIDTH_M < mine[:, 0].min()
        or mine[:, 1].max() + CONFLICT_WIDTH_M < theirs[:, 1].min()
        or theirs[:, 1].max() + CONFLICT_WIDTH_M < mine[:, 1].min()
    ):
        return None
    gap = np.hypot(
        mine[:, 0][:, None] - theirs[None, :, 0],
        mine[:, 1][:, None] - theirs[None, :, 1],
    )
    rows, columns = np.nonzero(gap < CONFLICT_WIDTH_M)
    if not len(rows):
        return None
    delta = mine[rows, 3] - theirs[columns, 3]
    angle = np.abs(np.degrees(np.arctan2(np.sin(delta), np.cos(delta))))
    keep = (angle >= CROSSING_MIN_DEG) & (angle <= CROSSING_MAX_DEG)
    if not keep.any():
        return None
    rows, columns = rows[keep], columns[keep]
    first = rows == rows.min()
    rows, columns = rows[first], columns[first]
    nearest = int(np.argmin(gap[rows, columns]))
    return float(mine[rows[nearest], 2]), float(theirs[columns[nearest], 2])


def _yield_brake(speed, distance_to_conflict):
    """The brake command that stops this car short of a crossing it must give way at.

    Worked out from the room left rather than applied flat, so a car that sees a
    conflict 40 m off lifts off and one that sees it at 5 m stands on the brakes. It
    is combined with IDM's own acceleration by taking the smaller of the two, so
    giving way can only ever slow a car down - everything else about how it drives is
    still MetaDrive's.
    """
    room = distance_to_conflict - STOP_MARGIN_M
    if room <= 0.0:
        return -1.0
    
    return -min(1.0, (speed * speed) / (2.0 * room) / YIELD_BRAKE_MPS2)


def build_manager(plan, count=DEFAULT_COUNT, seed=0, give_way=True, follow_speed_profile=True):
    """A `BaseManager` subclass that keeps `count` cars on the roads in `plan`.

    Imports live in here rather than at module scope so this file can be read - and its
    placement arithmetic tested - without panda3d, exactly as `signal_control` does.
    """
    from metadrive.component.vehicle.vehicle_type import LVehicle, MVehicle, SVehicle, XLVehicle
    from metadrive.constants import DEFAULT_AGENT
    from metadrive.manager.base_manager import BaseManager
    from metadrive.manager.scenario_traffic_manager import ScenarioTrafficManager
    from metadrive.policy.idm_policy import TrajectoryIDMPolicy

    # In the file's frame. The simulator's frame is this one moved by the ego's start
    # position, and `after_reset` is where that is applied - see `_episode_shift`.
    routes = []
    for entry in plan["routes"]:
        points = [(float(x), float(y)) for x, y in entry["polyline"]]
        routes.append(
            {
                "name": entry["name"],
                "points": points,
                "lengths": _cumulative(points),
                "speed_mps": float(entry.get("speed_mps", 0.0)),
                "speed_step_m": float(entry["speed_step_m"]),
                "speeds": np.asarray(entry["speeds"], dtype=float),
            }
        )

    class LiveTrafficManager(BaseManager):
        """Cars generated from the reviewed lane graph, decided every reset."""

        IDM_ACT_BATCH_SIZE = ScenarioTrafficManager.IDM_ACT_BATCH_SIZE
        SIZES = (SVehicle, MVehicle, LVehicle, XLVehicle)

        def __init__(self):
            super().__init__()
            self.plan_routes = routes
            # The same routes moved into this episode's world. Rebuilt every reset, because
            # the shift belongs to the scenario and a dataset may hold more than one.
            self.routes = routes
            self.car_count = count
            self.episode_seed = seed
            self._placed = []
            self._policy_index = 0
            self._to_clear = []
            self._episode = 0
            self.cars_spawned = 0
            self.cars_retired = 0
            self.collisions = 0
            self.on_road_low = 0
            self.gave_way = 0
            self.conflicts_seen = 0
            self.give_way = give_way
            self.follow_speed_profile = follow_speed_profile
            self.cars_lost = 0
            self._lost = set()
            self._crashed = set()
            self._route_of = {}
            self._order = {}
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

        def _episode_shift(self):
            """Where the file's coordinates land in this episode's world.

            `centralize=True` moves the loaded scenario so the recorded car starts at the
            origin and records the move as `metadata.old_origin_in_current_coordinate`.
            Everything MetaDrive shows - the map, the ego, the lights - is in the moved
            frame; `traffic.json` is not, because it was written beside the pickle rather
            than by it. Read per reset rather than once, since the shift is the *scenario's*
            and a dataset may hold several.
            """
            manager = getattr(self.engine, "data_manager", None)
            if manager is None:
                return 0.0, 0.0
            shift = manager.current_scenario_summary.get("old_origin_in_current_coordinate")
            if shift is None:
                return 0.0, 0.0
            return float(shift[0]), float(shift[1])

        def _localised_routes(self):
            """`plan_routes` moved into this episode's frame.

            Lengths are not recomputed: a translation does not change a distance along a
            line, and recomputing them would let the two frames disagree by rounding.
            """
            offset_x, offset_y = self._episode_shift()
            out = []
            for route in self.plan_routes:
                points = [(x + offset_x, y + offset_y) for x, y in route["points"]]
                out.append(
                    {
                        "name": route["name"],
                        "points": points,
                        "lengths": route["lengths"],
                        # Built once here rather than per car per step: `_look_ahead` runs for
                        # every car on every step and is the only hot path in this module.
                        "xy": np.asarray(points, dtype=float),
                        "cumulative": np.asarray(route["lengths"], dtype=float),
                        "speed_mps": route["speed_mps"],
                        # A translation does not change how fast a corner may be taken, so
                        # the profile is carried across unchanged rather than recomputed.
                        "speed_step_m": route["speed_step_m"],
                        "speeds": route["speeds"],
                    }
                )
            return out

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

        def _free_at(self, position):
            """Whether a car may be put here: far enough from every car already placed.

            Every car, not every car on this route - see `MIN_GAP_M`. The ego is checked
            separately by `_clear_of_ego`, with MetaDrive's own constants.
            """
            return all(
                math.hypot(position[0] - taken[0], position[1] - taken[1]) >= MIN_GAP_M
                for taken in self._placed
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
                route = self.routes[index]
                usable = route["lengths"][-1] - END_MARGIN_M
                if usable <= 0.0:
                    continue
                distance = rng.uniform(0.0, usable)
                position, heading = _pose_at(route["points"], route["lengths"], distance)
                if not self._free_at(position) or not self._clear_of_ego(position):
                    continue
                self._placed.append(position)
                placements.append((index, distance, position, heading))
            return placements

        def _spawn(self, index, position, heading, rng):
            from metadrive.component.lane.point_lane import PointLane

            route = self.routes[index]
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
            # A stable name for this car. **Not `vehicle.name`**, which is
            # `str(uuid.uuid4())` (`nameable.py:12`) - a fresh random id every process - so a
            # give-way tie broken on it is decided differently on every run. That was measured
            # before it was fixed: with the rule off the same five episodes gave 26 collisions
            # four times over, and with it on they gave 13, 19, 20 and 22, because a different
            # car of each tied pair went first and the physics amplified it from there.
            self._order[vehicle.name] = self._policy_index
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
            self._placed = []
            self._policy_index = 0
            self._to_clear = []
            self._episode += 1
            self.cars_spawned = 0
            self.cars_retired = 0
            self.collisions = 0
            self.gave_way = 0
            self.conflicts_seen = 0
            self.cars_lost = 0
            self._lost = set()
            self.on_road_low = self.car_count
            self._crashed = set()
            self._route_of = {}
            self._order = {}
            # Before any placement: every position, heading and `PointLane` below comes off
            # these points, and in the file's frame all of them are off the map.
            self.routes = self._localised_routes()
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
            brakes = self._yielders() if self.give_way else {}
            # Counted over the episode rather than held as a snapshot: a rule that fires
            # twice and a rule that never fires look the same on the last step of a drive.
            self.gave_way += len(brakes)
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
                if policy.arrive_destination or self._past_the_end(vehicle, policy):
                    self._to_clear.append(vehicle.name)
                    continue
                index, along, lateral = self._on_route(vehicle, policy)
                if abs(lateral) > LOST_LATERAL_M:
                    self._to_clear.append(vehicle.name)
                    self._lost.add(vehicle.name)
                    continue
                if self.follow_speed_profile and index is not None:
                    # IDM's own lever, read every step it computes an acceleration:
                    # `acceleration()` uses `self.target_speed` and `TrajectoryIDMPolicy.act`
                    # never resets it, because it does not call `lane_change_policy`.
                    # `NORMAL_SPEED` is a flat 40 km/h and 29.5% of `junction-1`'s route
                    # distance allows less than that on curvature alone.
                    policy.target_speed = 3.6 * self._allowed_mps(index, along)
                speed_control = self.episode_step % self.IDM_ACT_BATCH_SIZE == policy.policy_index
                action = policy.act(speed_control)
                brake = brakes.get(vehicle.name)
                if brake is not None:
                    # Taking the smaller of the two, so giving way only ever slows a car:
                    # steering, following distance and everything else stays IDM's.
                    action = [action[0], min(action[1], brake)]
                vehicle.before_step(action)

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
                self._order.pop(name, None)
            replacements = len(self._to_clear)
            # A car picked up off the grass did not complete a route, so it is counted apart
            # from the arrivals. Both are replaced: the road must not empty either way.
            lost = len(self._lost & set(self._to_clear))
            self.cars_lost += lost
            self._lost -= set(self._to_clear)
            self.cars_retired += replacements - lost
            self._to_clear = []
            if not replacements:
                return {}
            # Where the cars actually are, rather than where their routes say they should be:
            # a replacement must not land on a car, and a car's position is the only thing
            # that decides whether it has.
            self._placed = [tuple(v.position[:2]) for v in self.spawned_objects.values()]
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
                route = self.routes[index]
                position, heading = _pose_at(route["points"], route["lengths"], 0.0)
                if not self._free_at(position) or not self._clear_of_ego(position):
                    continue
                self._placed.append(position)
                out.append((index, 0.0, position, heading))
            return out

        # --- giving way --------------------------------------------------------------------

        def _yielders(self):
            """Which cars must give way this step, and how hard each must brake.

            **IDM cannot see a car crossing its path.** `get_find_front_back_objs_single_lane`
            keeps only objects whose bounding box is on the follower's *own* lane, so a car
            entering the junction from the side is not an obstacle to it at any distance -
            which is why a junction full of IDM cars collides, and why MetaDrive's own traffic
            manager never meets the problem: it replays recorded tracks that were driven by
            people who did give way.

            **The nearer car goes.** Priority is the distance each has left to run to the
            crossing, not the time - a car that has stopped has an infinite time to arrive and
            would give way to everything for ever, including to the car waiting for it. Ties
            break on the **spawn ordinal**, so the answer cannot depend on iteration order, two
            cars never both decide they are the one to go, and the same seed gives the same
            drive - see `_order` in `_spawn` for why it is not the vehicle's name.
            """
            ahead, brakes = {}, {}
            found_any = 0
            ego = self._ego_look_ahead()
            for vehicle in self.spawned_objects.values():
                index, distance = self._where(vehicle)
                if index is None:
                    continue
                route = self.routes[index]
                samples = _look_ahead(route["xy"], route["cumulative"], distance)
                if samples is not None and len(samples) > 1:
                    ahead[vehicle.name] = (vehicle, distance, samples)
            names = sorted(ahead, key=self._order.get)
            for position, name in enumerate(names):
                vehicle, distance, samples = ahead[name]
                # The ego first, and it is never the one told to brake: nothing here drives
                # it. Under `--agent-policy replay` it is a tape and cannot yield at all, and
                # under any other policy it has its own reason to brake and does not need
                # ours. Before this, traffic could not see the ego at all - it is not in the
                # plan - and 5 of 16 collisions measured over four `junction-1` episodes were
                # with it.
                if ego is not None:
                    against_ego = _conflict(samples, ego)
                    if against_ego is not None:
                        found_any += 1
                        room = against_ego[0] - distance
                        if room > 0.0:
                            brakes[name] = min(
                                brakes.get(name, 0.0),
                                _yield_brake(float(vehicle.speed), room),
                            )
                for other in names[position + 1 :]:
                    their_vehicle, their_distance, their_samples = ahead[other]
                    separation = math.hypot(
                        vehicle.position[0] - their_vehicle.position[0],
                        vehicle.position[1] - their_vehicle.position[1],
                    )
                    if separation > 2 * YIELD_LOOKAHEAD_M:
                        continue
                    found = _conflict(samples, their_samples)
                    if found is None:
                        continue
                    found_any += 1
                    my_room = found[0] - distance
                    their_room = found[1] - their_distance
                    if my_room <= 0.0 or their_room <= 0.0:
                        continue
                    # The spawn ordinal is the tie-break, and it is why `names` is sorted by
                    # it: this car's ordinal is always the smaller here, so an exact tie is
                    # always resolved the same way round - and unlike `vehicle.name` it is the
                    # same on every run of the same seed.
                    if (my_room, self._order[name]) > (their_room, self._order[other]):
                        loser, room = name, my_room
                    else:
                        loser, room = other, their_room
                    brake = _yield_brake(float(ahead[loser][0].speed), room)
                    brakes[loser] = min(brakes.get(loser, 0.0), brake)
            self.conflicts_seen += found_any
            return brakes

        def _ego_look_ahead(self):
            """Where the ego will be over the next `YIELD_LOOKAHEAD_M`, as look-ahead samples.

            **Extrapolated from where it is going, not read off its recorded track**, and that
            is deliberate: the tape is the ego's future only while it is being replayed, and
            `--agent-policy idm`, `manual` and `remote` all drive it somewhere else. A
            straight line at the current heading is right enough for all four over the second
            or two that decides a give-way, and wrong in the same way for all four.

            A stationary ego yields no samples: it is not going anywhere to be given way to,
            and IDM already brakes for a car standing on its own lane.
            """
            try:
                ego = self.ego_vehicle
            except (KeyError, AttributeError):
                return None
            if float(ego.speed) < 0.5:
                return None
            heading = float(ego.heading_theta)
            reach = np.arange(0.0, YIELD_LOOKAHEAD_M + YIELD_SAMPLE_M, YIELD_SAMPLE_M)
            return np.column_stack(
                (
                    ego.position[0] + np.cos(heading) * reach,
                    ego.position[1] + np.sin(heading) * reach,
                    reach,
                    np.full(len(reach), heading),
                )
            )

        def _on_route(self, vehicle, policy):
            """Which route the car is on, how far along it is, and how far off it is.

            The route index is **remembered from the spawn**, not recovered from the policy's
            destination: several routes in a pool end on the same exit lane and therefore at
            the same point - 60 routes over 18 exit lanes on `junction-1` - so matching on the
            endpoint would silently name the wrong one, and every measurement made from it
            would then be against a road the car is not on. Along and lateral are measured,
            because they are the parts that move.

            One projection for all three callers - the speed profile, the lost-car test and
            the give-way spacing. `local_coordinates` walks the line and is the second most
            expensive thing in this module after `_look_ahead`.
            """
            index = self._route_of.get(vehicle.name)
            if index is None:
                return None, 0.0, 0.0
            along, lateral = policy.traj_to_follow.local_coordinates(vehicle.position)
            return index, float(along), float(lateral)

        def _allowed_mps(self, index, along):
            """How fast the route says a car may be at `along` metres.

            The profile is min-pooled onto an even grid by `traffic_routes._pooled_speeds`, so
            this is an index rather than a search, and the pool a car is *in* is the one that
            applies - never interpolated toward the next one, which on the approach to a
            corner would hand back a speed the corner does not allow.
            """
            route = self.routes[index]
            speeds = route["speeds"]
            if not len(speeds):
                return float(route["speed_mps"])
            step = int(max(along, 0.0) // route["speed_step_m"])
            return float(speeds[min(step, len(speeds) - 1)])

        def _past_the_end(self, vehicle, policy):
            """Whether the car has driven off the end of its route.

            `TrajectoryIDMPolicy.arrive_destination` is a `DEST_REGION_RADIUS` circle around
            the trajectory's **last point**, so a car that arrives even slightly wide never
            enters it - and nothing else stops it, because `steering_control` asks
            `heading_theta_at(long + 1)`, which clamps to the final segment: the car then
            drives dead straight for ever, through whatever is in front of it. Measured on
            `junction-1` before this test existed, three episodes of 25 cars: **36 cars ran
            past their last point and stayed**, against 27 retired by the circle, two of them
            reaching 245 m and 131 m clear of any road.

            The same margin, measured **along the route** rather than across the plane, so a
            car that gets to the end wide is still a car that got to the end. No new
            constant: arriving is still `DEST_REGION_RADIUS` from the end, and this only
            stops asking the car to arrive laterally as well.
            """
            line = policy.traj_to_follow
            along, _lateral = line.local_coordinates(vehicle.position)
            return along >= line.length - TrajectoryIDMPolicy.DEST_REGION_RADIUS

        def _where(self, vehicle):
            """`_on_route` for a caller that has the vehicle but not its policy."""
            if not self.engine.has_policy(vehicle.id, TrajectoryIDMPolicy):
                return None, 0.0
            index, along, _lateral = self._on_route(
                vehicle, self.engine.get_policy(vehicle.name)
            )
            return index, along

    return LiveTrafficManager


def traffic_env(base_class, *, plan, count=DEFAULT_COUNT, seed=0, give_way=True,
                follow_speed_profile=True):
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
    manager_class = build_manager(
        plan, count=count, seed=seed, give_way=give_way,
        follow_speed_profile=follow_speed_profile,
    )

    class TrafficScenarioEnv(base_class):
        def setup_engine(self):
            super().setup_engine()
            # A name of its own. `register_manager` asserts the name is free, and
            # `traffic_manager` is taken by the stock one that replays recorded tracks - which
            # our datasets have none of, so it stays registered and does nothing.
            self.engine.register_manager("live_traffic_manager", manager_class())

    return TrafficScenarioEnv
