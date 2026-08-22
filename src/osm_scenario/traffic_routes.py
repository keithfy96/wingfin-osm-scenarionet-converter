"""Routes for other cars to drive, worked out once and written to a file.

Stage 8 puts traffic on the road. The cars themselves are MetaDrive's - its vehicle, its
`TrajectoryIDMPolicy`, its physics - and the only thing this project supplies is *where they
drive*. That is this module: a pool of paths through the reviewed lane model, each one a
polyline in the same projected metres the dataset uses.

**It writes a file rather than being imported by the thing that spawns the cars.** The
manager runs inside MetaDrive, which on this machine is Python 3.8; this package is
`>=3.10,<3.11` and cannot be installed there. `tools/signal_control.py` settled the same
question for traffic lights and says so in its own docstring - it reads the numbers
`signal_plan` wrote rather than importing the planner. `tools/traffic.py` reads these.

Nothing here touches the dataset. `traffic.json` sits beside `routes.json` and
`signals.json` in the workspace, and a different seed costs a second rather than a
re-conversion.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from osm_scenario.conversion import (
    _check_stage_5,
    _lane_change_moves,
    _lane_neighbours,
    _read,
    _sha256,
)
from osm_scenario.ego_route import RouteError, plan_route, route_polyline, speed_profile
from osm_scenario.lane_model import PreliminaryLaneModel

TRAFFIC_VERSION = 2
"""2 added `speed_step_m` / `speeds` to every route. A version 1 file has no speed profile,
so a car reading it would drive every corner at `TrajectoryIDMPolicy.NORMAL_SPEED` - which is
the fault the profile exists to fix - and it is refused rather than driven."""

TRAFFIC_LATERAL_ACCEL_MPS2 = 4.0
"""How hard a traffic car is asked to corner, against `ego_route.LATERAL_ACCEL_MPS2`'s 8.5.

**Not a comfort figure, and not the ego's.** 8.5 is pinned to the ego's 30°-per-step gate,
and the ego's drive is a *recording* - the positions are replayed, so nothing has to steer to
them. A traffic car is steered there by MetaDrive's IDM, whose `steering_control` is two PIDs
aimed at a point a fixed **1 m** ahead, and at 8.5 it arrives at the corner too fast to hold
the line. Swept on `junction-1`, three episodes of 25 cars, counting cars that left the
tarmac: flat 40 km/h **26**, profile at 8.5 **19**, at 4.0 **13**, at 2.5 **16** - and 2.5
halves the number of routes completed per episode (25 to 14), which is the throughput live
traffic exists to keep up. 4.0 is the knee."""

SPEED_STEP_M = 2.0
"""Spacing of the stored speed profile, min-pooled from `speed_profile`'s own 0.1 m.

The raw profile is 517,750 samples over `junction-1`'s 60 routes - it would be most of the
file. Pooled at 2 m it is 25,887, and taking the **minimum** of each interval rather than a
mean or a sample keeps every pool conservative: no car is ever told it may go faster than the
finest reading said. A car covers 2 m in under half a second at these speeds."""


DEFAULT_COUNT = 60
"""How many routes to keep. A pool, not a car count - one route may carry several cars."""

POLYLINE_TOLERANCE_M = 0.005
"""How far a dropped vertex may sit off the line drawn through its neighbours.

`route_polyline` samples finely enough for `speed_profile` to read curvature off it, which
is far finer than a car needs to be steered along: 55,842 points over `junction-1`'s 60
routes, a median 0.24 m apart, and every one of them is carried in the file and turned into
a `PointLane` vertex for every car on that route.

**5 mm, and the 30°-per-step gate is what pins it**, not the file size. Measured over those
60 routes, worst turn at any vertex: 18.3° undecimated, **18.5° at 5 mm** for 23.4% of the
points — the geometry is the same line. At 2 cm it is 11.9% of the points and **34.3°**,
which is over the gate, and at 5 cm it is 47.9°. So the next tolerance up is not a cheaper
version of this one; it is a different road.
"""

MIN_ROUTE_M = 30.0
"""A route shorter than this is not worth a car: it would despawn almost at once.
`TrajectoryIDMPolicy.arrive_destination` fires within `DEST_REGION_RADIUS` (2 m) of the
end, so a 10 m route is a car that appears and vanishes."""


class TrafficError(RuntimeError):
    """Raised when a traffic pool cannot be built from this lane model."""


def _pooled_speeds(polyline: np.ndarray, *, cruise_mps: float) -> np.ndarray:
    """The route's speed profile, min-pooled onto an even `SPEED_STEP_M` grid from zero.

    `ego_route.speed_profile` is called rather than reimplemented, and that is the whole point
    of computing this here: it is the one place that knows curvature is turn per metre and not
    a circumradius, that the braking pass has to run backwards so the car slows *before* the
    corner, and what `MIN_SPEED_MPS` is for. `tools/traffic.py` runs on MetaDrive's 3.8 and
    cannot import it, so it gets the numbers - the same split `signal_control` makes against
    `signal_plan`, and for the same reason.

    Min-pooled, never sampled: a sample can land either side of the one tight vertex in a
    junction and report the speed of the straight beside it.
    """
    _dense, travelled, speed = speed_profile(
        polyline,
        cruise_mps=cruise_mps,
        lateral_accel_mps2=TRAFFIC_LATERAL_ACCEL_MPS2,
    )
    edges = np.arange(0.0, float(travelled[-1]) + SPEED_STEP_M, SPEED_STEP_M)
    # `searchsorted` puts every fine sample in exactly one pool; `minimum.at` then takes the
    # slowest of each. Pools past the last sample keep the final speed rather than `inf`.
    pools = np.full(len(edges), float(speed[-1]))
    where = np.clip(np.searchsorted(edges, travelled, side="right") - 1, 0, len(edges) - 1)
    np.minimum.at(pools, where, speed)
    return pools


@dataclass(frozen=True)
class TrafficRoute:
    """One path a car may drive, and the line it drives along."""

    name: str
    start_lane: str
    end_lane: str
    distance_m: float
    speed_mps: float
    polyline: np.ndarray
    speeds: np.ndarray
    """How fast a car may be at each `SPEED_STEP_M` along the line, from zero."""


@dataclass(frozen=True)
class TrafficPlan:
    """The pool, and an account of which lanes it was allowed to start from."""

    routes: tuple[TrafficRoute, ...]
    entries: tuple[str, ...]
    """Lanes a car may appear on - the ones the pool was drawn from."""
    entries_rejected: tuple[tuple[str, str], ...]
    """(lane id, why) for every unfed lane that is *not* an entry. Reported rather than
    counted, because the two kinds of unfed lane are not the same fact."""
    exits: tuple[str, ...]
    pairs_tried: int
    pairs_with_no_drive: int


def _simplify(points: np.ndarray, tolerance_m: float = POLYLINE_TOLERANCE_M) -> np.ndarray:
    """Drop vertices that lie within `tolerance_m` of the line through their neighbours.

    Ramer-Douglas-Peucker, iterative rather than recursive because a 3,000-point route would
    otherwise be 3,000 frames deep in the worst case. It only ever removes points, so the
    ends and every genuine corner survive exactly as `route_polyline` placed them.
    """
    if len(points) < 3:
        return points
    keep = np.zeros(len(points), dtype=bool)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        first, last = stack.pop()
        if last <= first + 1:
            continue
        span = points[last] - points[first]
        length = float(np.hypot(*span))
        interior = points[first + 1 : last]
        if length < 1e-12:
            # The two ends coincide, so "distance from the chord" is distance from the point.
            offsets = np.hypot(*(interior - points[first]).T)
        else:
            normal = np.array([-span[1], span[0]]) / length
            offsets = np.abs((interior - points[first]) @ normal)
        worst = int(np.argmax(offsets))
        if offsets[worst] > tolerance_m:
            split = first + 1 + worst
            keep[split] = True
            stack.append((first, split))
            stack.append((split, last))
    return points[keep]


def _edge_nodes(lane: Any) -> tuple[str, str]:
    """The OSM nodes a lane runs from and to, in the direction it is driven.

    `source_edge` is `[u, v, key]` in the *way's* node order, and `direction` says whether
    the lane runs with it or against it. A backward lane driven from `u` would be driven
    backwards.
    """
    upstream, downstream = lane.source_edge[0], lane.source_edge[1]
    if lane.direction == "backward":
        upstream, downstream = downstream, upstream
    return upstream, downstream


def entry_lanes(
    model: PreliminaryLaneModel,
    neighbours: dict[str, tuple[list[str], list[str]]],
) -> tuple[list[str], list[tuple[str, str]]]:
    """Lanes a car may appear on, and every lane rejected for the job with the reason.

    A car may appear where a road *starts* - where the extract cut it, so its traffic
    genuinely comes from off the map. Two lanes have no feeder and only one of them is that:

    - nothing arrives at its upstream node either, so the road begins there. Verified on
      `junction-1`: **all 11** such nodes lie outside `source/map.osm`'s own `<bounds>`,
      which is what a way truncated by the extract looks like.
    - a road *does* end at that node and simply does not feed this lane. That is a starved
      lane in the middle of a junction, and a car appearing on one appears inside an
      intersection other traffic is crossing. 8 of `junction-1`'s 19 are these.

    The test is on the node, never on a count. `CLAUDE.md` records 21 unfed lanes on
    `junction-1`; the measured figure is 19, and it mixes the two kinds - which is exactly
    why both lists come back rather than a total.
    """
    arrivals: dict[str, list[str]] = {}
    for lane in model.lanes:
        arrivals.setdefault(_edge_nodes(lane)[1], []).append(lane.identifier)

    kept: list[str] = []
    rejected: list[tuple[str, str]] = []
    for lane in model.lanes:
        if neighbours[lane.identifier][0]:
            continue
        upstream = _edge_nodes(lane)[0]
        # A lane arriving at its own upstream node would be a U-turn onto itself; it is not
        # a road ending there in any sense that matters, so it does not disqualify.
        if [other for other in arrivals.get(upstream, ()) if other != lane.identifier]:
            rejected.append((lane.identifier, f"a road already ends at node {upstream}"))
            continue
        kept.append(lane.identifier)
    return kept, rejected


def exit_lanes(
    model: PreliminaryLaneModel,
    neighbours: dict[str, tuple[list[str], list[str]]],
) -> list[str]:
    """Lanes a car may leave on - the ones with nowhere further to go.

    No node test here, and deliberately: a lane that leads nowhere is a fine place to
    *stop*, whether the road was cut by the extract or the allocation left it with no exit.
    Nothing appears there, so nothing appears in the wrong place.
    """
    return [
        lane.identifier for lane in model.lanes if not neighbours[lane.identifier][1]
    ]


def plan_traffic(
    *,
    model: PreliminaryLaneModel,
    count: int = DEFAULT_COUNT,
    seed: int = 0,
) -> TrafficPlan:
    """A pool of routes across the map, drawn from entry lanes to exit lanes.

    Every route is built by `ego_route.plan_route` and `ego_route.route_polyline`, unchanged
    and uncopied. So traffic drives the **same** junction geometry the recorded car does -
    cubics laid between the two lanes' own tangents rather than the connector marker, which
    is a band on an inspection map and not a driving line - and inherits the 30°-per-step
    gate that geometry was tuned against.

    `seed` chooses which source-sink pairs are tried and in what order, so two seeds give
    two different pools over the same map. Most pairs have no drive at all: the map is
    one-way in most places, and `plan_route` documents that as a normal answer.
    """
    if count < 1:
        raise TrafficError(f"--count must be at least 1, got {count}")

    neighbours = _lane_neighbours(model)
    moves = _lane_change_moves(model)
    entries, rejected = entry_lanes(model, neighbours)
    exits = exit_lanes(model, neighbours)
    if not entries:
        raise TrafficError(
            "no lane in this model is a place a car may appear: every unfed lane has a road "
            "ending at its upstream node, so a car there would appear inside a junction"
        )
    if not exits:
        raise TrafficError("no lane in this model leads nowhere, so no route can end")

    pairs = [(start, end) for start in entries for end in exits if start != end]
    random.Random(seed).shuffle(pairs)

    routes: list[TrafficRoute] = []
    tried = 0
    refused = 0
    for start, end in pairs:
        if len(routes) >= count:
            break
        tried += 1
        name = f"traffic-{len(routes):03d}"
        try:
            route = plan_route(
                model=model,
                neighbours=neighbours,
                moves=moves,
                name=name,
                start_lane=start,
                end_lane=end,
            )
            polyline = _simplify(
                route_polyline(
                    model=model, route_lanes=route.lanes, lane_changes=route.lane_changes
                )
            )
        except RouteError:
            refused += 1
            continue
        if route.distance_m < MIN_ROUTE_M:
            refused += 1
            continue
        routes.append(
            TrafficRoute(
                name=name,
                start_lane=start,
                end_lane=end,
                distance_m=route.distance_m,
                speed_mps=route.speed_mps,
                polyline=polyline,
                speeds=_pooled_speeds(polyline, cruise_mps=route.speed_mps),
            )
        )

    if not routes:
        raise TrafficError(
            f"no drive exists between any of the {len(entries)} entry lanes and "
            f"{len(exits)} exit lanes on this map"
        )
    return TrafficPlan(
        routes=tuple(routes),
        entries=tuple(entries),
        entries_rejected=tuple(rejected),
        exits=tuple(exits),
        pairs_tried=tried,
        pairs_with_no_drive=refused,
    )


def payload(
    plan: TrafficPlan,
    *,
    model: PreliminaryLaneModel,
    model_sha256: str,
    seed: int,
) -> dict[str, Any]:
    """`traffic.json`, with the identity block that binds it to this lane model.

    Lane ids are content addressed, so a pool built on one generation and used on another
    would not fail loudly - it would name lanes that exist somewhere else. The same guard
    `routes.json` carries, for the same reason.
    """
    return {
        "traffic_version": TRAFFIC_VERSION,
        "identity": {
            "generation_fingerprint": model.metadata.generation_fingerprint,
            "reviewed_lane_model_sha256": model_sha256,
        },
        "generated": {
            "seed": seed,
            "entry_lanes": list(plan.entries),
            "entry_lanes_rejected": [
                {"lane_id": lane_id, "reason": reason}
                for lane_id, reason in plan.entries_rejected
            ],
            "exit_lanes": list(plan.exits),
            "pairs_tried": plan.pairs_tried,
            "pairs_with_no_drive": plan.pairs_with_no_drive,
        },
        "routes": [
            {
                "name": route.name,
                "start_lane": route.start_lane,
                "end_lane": route.end_lane,
                "distance_m": round(route.distance_m, 3),
                "speed_mps": round(route.speed_mps, 3),
                "speed_step_m": SPEED_STEP_M,
                "speeds": [round(float(v), 3) for v in route.speeds],
                # Rounded to the millimetre. The polyline is the bulk of this file and a
                # micrometre of a lane position is not a fact about anything.
                "polyline": [[round(float(x), 3), round(float(y), 3)] for x, y in route.polyline],
            }
            for route in plan.routes
        ],
    }


def build_traffic(
    *,
    workspace: Path,
    count: int = DEFAULT_COUNT,
    seed: int = 0,
) -> tuple[Path, TrafficPlan]:
    """Read a workspace's reviewed lane model and write `traffic/traffic.json`.

    Gated on Stage 5 exactly as `convert` is: the model on disk must be the one validation
    passed. A pool built from an unvalidated model would name lanes the dataset does not
    have, and the identity block would then bind it to the wrong map rather than refuse it.
    """
    workspace = workspace.resolve()
    model_path = workspace / "lane-model" / "reviewed.json"
    _check_stage_5(workspace, model_path)
    model_sha256 = _sha256(model_path)
    model = PreliminaryLaneModel.model_validate(_read(model_path, "reviewed lane model"))

    plan = plan_traffic(model=model, count=count, seed=seed)

    out = workspace / "traffic" / "traffic.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(payload(plan, model=model, model_sha256=model_sha256, seed=seed), indent=2)
        + "\n",
        encoding="utf-8",
    )
    return out, plan
