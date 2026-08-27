"""Turn a chosen start and end lane into the ego track MetaDrive drives.

MetaDrive has no route format. `ScenarioEnv` is wired to `TrajectoryNavigation`, whose whole
input is `tracks[sdc_id]["state"]["position"]` - an array of positions it walks. So "give the
map a route" means "give the map a car that was recorded driving it", and this module is
what invents that car.

Four things about the geometry are worth knowing before reading the code.

* **A junction turn is built here, not read from the connector.** `ConnectorFeature.centerline`
  looks like the path across a junction and is not one: `topology.connector_curve` builds it
  as a *marker* for the inspection map, and says so. Where the two lanes already meet - 44 of
  `junction-1`'s 83 active connectors, because OSM splits a way whenever a tag changes - the
  marker is a 3 m stub that retraces the approach, so splicing it made the car drive three
  metres, jump back, and drive them again. Where the lanes are genuinely apart, the marker is
  a quadratic Bezier bent around the OSM node and tangent to neither lane, so a 90° turn came
  out as two 82° corners with 28° of curve between them, over 2.81 m of path - an implied
  radius of 1.8 m. `_turn` builds the join from what the two lanes actually do instead: it
  cuts back into both and lays a curve between the cut points whose end tangents *are* the two
  lane directions, so the drive leaves along the road it is on and arrives along the road it
  is joining.

* **A lane change is not a teleport.** Concatenating a lane's centreline with its
  neighbour's would step sideways by a lane width in zero distance. `_lane_change` cuts both
  lanes either side of their midpoints and curves between the cuts, over as much road as
  taking the move at speed needs - crammed into a few metres it is a swerve, and the speed
  profile then has to crawl through it. `_lane_change_moves` has already refused any
  neighbour that is not the same stretch of road running the same way, so the two centrelines
  are parallel and comparable in length.

* **Speed follows the geometry.** A car does not take a 90° junction at the speed limit, and
  a track that says it did teaches an agent that it can. `speed_profile` caps the speed at
  every vertex by the curvature there, then bounds how fast it may change, so the recorded car
  slows before a turn and picks up after it. How hard it is allowed to corner is set by
  `LATERAL_ACCEL_MPS2`, and that constant is pinned to the drivability gate in
  `tools/check_dataset.py` rather than to a comfort figure - see the comment on it.

* **The route is generated, and says so.** Nothing in the OSM says a car drove here. What
  the source does supply is every metre of the geometry, so the invention is confined to
  *which* way to go and *how fast* - both recorded in `metadata.sdc_route` rather than left
  to be inferred from the numbers.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import networkx as nx
import numpy as np

from osm_scenario.generation import MIN_TRIMMED_LANE_M
from osm_scenario.lane_model import LaneFeature, Point2D, PreliminaryLaneModel
from osm_scenario.signal_plan import LIGHT_RED, colour_at, seconds_until_green
from osm_scenario.topology import BEND_FILLET_MIN_DEGREES

# MetaDrive's *default* step: `physics_world_step_size` 0.02 x `decision_repeat` 5. It is a
# default rather than a constant of the simulator - both keys are configurable, and
# `tools/drive.py`'s `step_config` derives them from a requested rate. What is not
# configurable is `parse_object_state`, which hard-codes 0.1 s when it differentiates
# positions into an angular velocity, so a track written at any other spacing must be driven
# at that spacing too rather than replayed at MetaDrive's default.
TIME_STEP_S = 0.1

# A default vehicle's box. MetaDrive warns when width exceeds length, and `ScenarioEnv`
# spawns the ego at whatever size the track claims, so this is the one place a plausible
# figure is needed. Not surveyed - nothing about the car is.
EGO_LENGTH_M = 4.6
EGO_WIDTH_M = 1.85
EGO_HEIGHT_M = 1.5

# What a lane change costs in the path search, in metres of equivalent travel. It is not free
# - a route that changes lanes for no reason is worse than one that does not - but it must
# stay far below the length of a detour, or the search would rather drive a mile than move
# across. Nothing in the source sets this; it is a tie-break, and it is only ever a tie-break
# because every real alternative differs by much more.
LANE_CHANGE_COST_M = 5.0

# How far apart two consecutive pieces of a route may be where they join, and how far a
# junction may be across.
#
# Two lanes of the same road meet exactly - a continuation starts where the last one ended -
# so a real join measures 0, and anything more than `MAX_JOIN_M` is a hole the car would
# drive straight across. A junction movement is different: the two lane lines belong to
# different roads and are each offset sideways off their own, so they stop short of the
# shared node on different sides. On `junction-1` those gaps run from 1.7 m to 5.4 m, and a
# larger crossroads is wider still.
#
# Neither survives as a visible jump once the track is resampled - it becomes a smooth line
# over open ground with nothing downstream to complain - so the join is the only place
# either can be caught. Checked there rather than on the finished polyline: a single lane
# can legitimately be one straight 155 m segment between two vertices, so step length alone
# cannot tell a long road from a gap. Lane changes are excluded because their whole point is
# a deliberate sideways offset.
MAX_JOIN_M = 5.0
MAX_CROSSING_M = 20.0

# Below this, `ScenarioEnv._is_arrive_destination` returns true on the first frame, because
# `reference_trajectory.length < 2` is its "vehicle is static" case. An episode that succeeds
# before it starts is worse than one that fails.
MIN_ROUTE_M = 2.0

# The most of a lane one change may use, as a fraction of its length either side of the
# midpoint. Cutting both lanes at the *same* point would leave the car stepping a full lane
# width sideways at constant longitude - a teleport, not a lane change - and cramming the
# move into a few metres is a swerve, which the speed profile then has to slow to a crawl
# for. The manoeuvre takes as long as `SMOOTHING_RADIUS_M` asks for and no more than this.
_CHANGE_MAX_FRACTION = 0.45

# The radius a junction turn is built to, in metres. A 90° turn at 9 m takes about 14 m of
# path, which is what a car does - it starts turning before the junction and finishes after
# it. The connector marker spans 2.81 m at the median, an implied radius of 1.8 m, tighter
# than a car can physically turn. Nothing in OSM sets this: the source says which movements
# are permitted, never how one is driven, so this is presentation of a permitted move rather
# than a claim about how anyone drove it.
TURN_RADIUS_M = 9.0

# How much of a lane one turn may eat: this fraction of what that lane still has, or all
# but `MIN_TANGENT_M` of it, whichever is more.
#
# The fraction alone starves the short ones. `junction-1` has lanes of 5.8 m and 6.0 m
# between two junctions, and chains of them - a turn taking 40% of one leaves 60% for the
# next, which takes 40% of that, and by the third the arc is built over a metre and comes
# out at 1.6 m of radius. A lane that short *is* junction; it should be nearly all curve.
# What has to survive is enough of it to read a direction off, which is what the metre is.
MAX_TURN_TRIM_FRACTION = 0.4
MIN_TANGENT_M = 1.0

# The cubic Bezier's control handles, as a fraction of the chord, for a turn of θ.
#
# `(4/3)·tan(θ/4)·R` is the exact handle for a cubic approximating a circular arc of turn θ
# and radius R. Written against the chord rather than R, using `chord = 2·R·sin(θ/2)`, it
# becomes the expression below - 0.39 of the chord at 90°, and a third of it as θ goes to
# zero, where the join is a plain sideways shift rather than a corner.
#
# Both wrong alternatives were tried and both are visible in the geometry. A flat 0.5523 of
# the chord - the usual circle rule of thumb - only approximates a circle when the two ends
# sit symmetrically on one, which a junction's do not: the lane lines are offset sideways as
# well as turned, and the curve pinched to 2.7 m of radius where the geometry called for 24.
# Sizing the handle off the *trim* instead fixed that, and then failed the other way: on two
# short lanes the trim shrinks to a fraction of the chord, the handles with it, and the curve
# becomes a straight line with a 176° hook at each end. Against the chord it cannot degenerate
# either way, because it scales with the thing it has to span.
def _handle_fraction(turn: float) -> float:
    angle = abs(turn)
    if angle < 1e-6:
        return 1.0 / 3.0
    return (2.0 / 3.0) * math.tan(angle / 4.0) / math.sin(angle / 2.0)

# How finely a turn is sampled. The track is resampled at up to 1.4 m per step, so a
# coarsely drawn arc is thrown away before MetaDrive ever sees it - which is what happened
# to the connector's five points, leaving a median of two recorded positions in a whole turn.
TURN_SAMPLE_M = 0.25

# Below this the two pieces already point the same way and no turn is built.
_STRAIGHT_DEG = 1.0

# Up to this much of a turn, a join is treated as a lane-offset artefact to be smoothed away
# at road speed rather than as a corner to be taken at corner speed. Consecutive lanes of the
# same road do not meet exactly - each is pushed sideways off the way it came from, so where
# the bearing changes at a node the two offsets are 0.26 m to 0.75 m apart on `junction-1`'s
# own routes. That step is not a manoeuvre and the car should not slow for it.
_SMOOTHING_MAX_DEG = 20.0

# The radius a shallow join is smoothed to, in metres. It was chosen so that shifting sideways
# at 50 km/h stayed inside the old 1.8 m/s² lateral cap: v² / a = 13.89² / 1.8 = 107 m, rounded
# up. Against the present 8.5 the same sum asks for only 22.7 m, so 110 is now generous rather
# than exact - and it stays that way deliberately. A larger radius asks for a *longer*, gentler
# crossing; shrinking it to match the cap would tighten every lane change, which is already the
# sharpest thing on a drive line. The arithmetic is here rather than computed because this is a
# property of the road, not of whichever route happens to be driven over it.
SMOOTHING_RADIUS_M = 110.0

# How finely the speed profile is worked out. The line it runs on is mostly two-point lanes
# with 0.25 m arcs at the junctions, so a 150 m step sits next to a 0.08 m one; resampling
# first is what lets the acceleration passes and the curvature estimate mean anything.
#
# It has to be at least as fine as the track it is deciding the speed for, or it under-reads
# its own geometry: curvature measured over 0.25 m spreads a bend the recorded car meets over
# 0.1 m, and the drive then exceeds the very lateral limit the profile was computed to keep.
# `MIN_SPEED_MPS` × `TIME_STEP_S` is the shortest step the track can contain, so it is the
# right spacing here.
PROFILE_SAMPLE_M = 0.1

# No vertex of a finished route may turn more than this. A road never does; a marker spliced
# in backwards turns 180°, and that is exactly what shipped. The mirror of `MAX_JOIN_M`:
# that catches a hole at a join, this catches a reversal. Well above a tight U-turn, which
# is drawn as a curve and so turns a few degrees per vertex however sharp it is.
MAX_VERTEX_TURN_DEG = 150.0

# Lateral acceleration through a bend, in m/s², and what decides the speed through a turn:
# 9 m of radius comes out at about 31 km/h.
#
# This was 1.8 - about 0.18 g, the side friction urban junction design assumes - and at that
# figure the recorded car crawled. Over 120 real `junction-1` routes it averaged 25.0 km/h on
# roads posted at 50, took a 9 m junction turn at 15 km/h, and its worst route sat at 3.6 km/h.
#
# 8.5 is not a comfort figure and is not chosen as one. It is the edge of the drivability gate
# `tools/check_dataset.py` already enforces - no more than 30° of heading change in one 0.1 s
# step - which is what really caps the pace, because degrees per step scale with speed while
# the geometry underneath does not. Measured over those same 120 routes, cruising at the posted
# limit: 7.0 gives 39.5 km/h with a worst step of 26.9°; 8.5 gives 41.5 km/h at 29.6°, still
# nothing over the gate; 9.0 gives 41.8 km/h and puts one step over it; 12.0 gives 44.7 km/h
# and puts 22 over. Past 8.5 the gain is a percent and the cost is a failing dataset.
LATERAL_ACCEL_MPS2 = 8.5

# How fast the recorded car may gain and lose speed, in m/s². Braking is allowed to be
# firmer than acceleration, as it is in a car. Without these the speed would step from the
# limit to the corner speed between two samples, which is not a drive and would differentiate
# into an impossible acceleration.
#
# These were 1.2 and 2.0, and they were the other half of the crawl: at 1.2 m/s² the car needed
# 11.6 s and 80 m to reach 50 km/h, and at 2.0 it began braking 37 m before a corner, so on a
# map whose lanes are tens of metres long it was accelerating or braking nearly all the time.
# 5.0 and 6.0 are brisk rather than sedate - about 0.5 g and 0.6 g - and buy the pace the
# lateral figure above is there to allow. Raising them further to 6.0 and 7.0 was measured at
# one percent more speed, which is not worth another tenth of a g.
ACCEL_MPS2 = 5.0
BRAKE_MPS2 = 6.0

# The slowest the recorded car moves while it is still moving. A floor is needed at all
# because a sample much shorter than a centimetre has no reliable direction in it; at 1 m/s
# the samples are 0.1 m, which is well clear of that and clear of `COINCIDENT_M`.
#
# It was 2.0, on the reasoning that nothing `_turn` builds is tight enough to demand less.
# That is not true of a *lane change*: crossing 3.5 m sideways inside a 7.11 m lane - which
# `junction-1` has, and which the route search will happily use - is an S-curve of about 2 m
# of radius, and 2 m/s through 2 m of radius is 2.0 m/s² of lateral acceleration, which broke
# the 1.8 cap of the day. A floor that quietly overrides the lateral limit is a track that says
# a car took a swerve faster than it could have.
#
# `LATERAL_ACCEL_MPS2` is 8.5 now, so that particular sum no longer bites - but the floor stays
# at 1.0, because the sharpest kinks a lane change leaves measure 0.36 m of radius, where the
# cap on its own allows 1.75 m/s. A floor of 2.0 would override it there instead. Stopping at a
# red already put a legitimate zero in the profile.
MIN_SPEED_MPS = 1.0

# Two points closer than this are the same point. A millimetre is far below anything a map
# built from OSM means and far above the residue two lane endpoints leave: trimming a lane at
# a length computed from its own vertices lands 0.000078 m and 0.000157 m off the endpoint on
# `junction-1`, and at the old 1e-6 m those survived as segments of their own. A segment that
# short has no usable direction - `atan2` over it returns noise, which read as an exact 90°
# turn - and it was the worst vertex in 390 of 813 swept routes.
COINCIDENT_M = 1e-3

# A curve this module builds itself must never turn more than this at one of its own samples.
# Sampled at `TURN_SAMPLE_M`, a well-formed turn changes heading by a few degrees per sample:
# the worst across all 83 of `junction-1`'s active connectors, built off untouched lanes, is
# 4.1°. Anything near this is a cubic that has doubled back on itself, which is a different
# fault from a sharp road and is caught separately from `MAX_VERTEX_TURN_DEG` for that reason.
MAX_CURVE_TURN_DEG = 20.0

# How close to `MIN_TRIMMED_LANE_M` a lane's centreline has to land to be the junction-box
# clamp rather than a way that happens to be short. The same figure, for the same reason, as
# `conversion._STUB_LANE_TOLERANCE_M`: a trim is interpolated along the line, so the length
# comes back a fraction of a millimetre off the constant, and nothing in either extract sits
# within 7 cm of it. See `_is_stub_lane`.
STUB_LANE_TOLERANCE_M = 0.01

# Above this much turn across a junction box, the crossing is guided by the stub lanes'
# midpoints; below it, one cubic spans the whole box. Swept on both extracts against two
# things at once - the share of routes carrying a bend tighter than a car can turn, and how
# much of the drive line ends up off the drivable surface. One cubic everywhere is the
# smoothest line there is, but a box turning more than this is a loop of short ways driven
# *round*, and cutting across it put the line a median 18.7 m and up to 40.8 m from the road.
# 60 and 120 both hold the line inside the box, and 120 is taken: it leaves the routes
# carrying an undrivable bend at 36.4% against 60's 43.1% on `junction-1`, and it is what
# lets the worked case - `fold-demo`, a 96.2 deg crossing - come out at 9.60 m of radius
# rather than 1.71 m. The cost is `mosque`'s off-road distance at 0.23% against 0.14%, with
# today's code at 0.11%; on `junction-1` the two are level at 1.16% and 1.12%.
BOX_GUIDE_MIN_DEG = 120.0

# How much road a crossing that doubles back may use, as a multiple of `_box_path`. Swept on
# both extracts against the two things that trade off against each other here - the radius the
# crossing comes out at, and how much of the line ends up off the drivable surface. Radius
# rises monotonically with it (`junction-1`'s three guided boxes read 2.67/2.45/4.89 m at 0.4
# and 3.60/3.11/6.17 m at 1.2) and so does the road lost, so this is picked at the largest
# value that still costs nothing. See `_junction_box`.
BOX_TRIM_SPANS = 1.0

# How far short of a light's stop line the recorded car comes to rest. MetaDrive builds a
# 0.25 m invisible wall across the lane at the stop point and flips its collision mask with
# the colour, so a car recorded *on* the line is recorded inside the wall - harmless under
# replay, which sets position directly, and a collision under any policy that does not.
# `TrajectoryIDMPolicy`'s own gap on this map measures 5.7 m.
STOP_LINE_SETBACK_M = 5.0

# Two stop lines closer together than this are one stop. Without it a pair of them inside one
# profile sample would leave two zero speeds side by side, and the time to cross between them
# is a distance divided by their mean speed of zero.
SEPARATE_STOPS_M = 1.0


class RouteError(RuntimeError):
    """Raised when a chosen start and end cannot become a route."""


@dataclass(frozen=True)
class SignalTiming:
    """A light the route may pass: where it stops the car, and the plan that governs it.

    Passed in rather than read, because `ego_route` must not depend on a signal plan being
    present - most datasets have none - and because the timing is a `convert`-time file that
    deliberately sits outside the generation fingerprint.
    """

    lane_id: str
    stop_point: tuple[float, float]
    cycle_seconds: float
    green_seconds: float
    yellow_seconds: float
    offset_seconds: float


@dataclass(frozen=True)
class Wait:
    """A red light the recorded car actually stopped for."""

    lane_id: str
    at_m: float
    """How far along the drive the car comes to rest - already set back from the stop line."""
    arrived_s: float
    waited_s: float


@dataclass(frozen=True)
class Route:
    """A path through the lane model, and what it cost to take it."""

    name: str
    start_lane: str
    end_lane: str
    lanes: tuple[str, ...]
    lane_changes: tuple[int, ...]
    """Indices into `lanes` where the step *into* that lane was a lane change."""
    distance_m: float
    speed_mps: float
    """Cruising speed - the lowest limit along the route. Turns are taken below it."""
    duration_s: float
    """The whole drive, waits at red lights included. Not distance / speed."""
    slowest_mps: float
    """The speed at the tightest turn on the route."""
    driving_duration_s: float = 0.0
    """The same drive with the standing still taken out. Not the drive with every light green:
    the car still brakes for a red and pulls away from it, and that time is in here."""
    waits: tuple[Wait, ...] = ()
    """Resolved once, here, and carried rather than re-derived: the track and the summary must
    stop in the same places, and two implementations of the same clock would not have to."""


def _xy(points: list[Point2D]) -> np.ndarray:
    return np.array([[point.x, point.y] for point in points], dtype=np.float64)


def _length_of(points: np.ndarray) -> float:
    if len(points) < 2:
        return 0.0
    return float(np.linalg.norm(np.diff(points, axis=0), axis=1).sum())


def _cut(points: np.ndarray, *, keep_head: bool, at: float) -> np.ndarray:
    """A centreline split at `at`, a fraction of its arc length.

    `keep_head` keeps the run-up to that point, otherwise the run-out from it.
    """
    if len(points) < 2:
        return points
    steps = np.linalg.norm(np.diff(points, axis=0), axis=1)
    travelled = np.concatenate([[0.0], np.cumsum(steps)])
    target = travelled[-1] * at
    index = int(np.searchsorted(travelled, target))
    index = max(1, min(index, len(points) - 1))
    # Interpolated rather than snapped to the nearest vertex, so a two-point lane still
    # splits where it was asked to instead of collapsing onto one of its ends.
    span = travelled[index] - travelled[index - 1]
    ratio = 0.0 if span == 0 else (target - travelled[index - 1]) / span
    split = points[index - 1] + (points[index] - points[index - 1]) * ratio
    if keep_head:
        return np.vstack([points[:index], split])
    return np.vstack([split, points[index:]])


def _trim_end(points: np.ndarray, distance: float) -> np.ndarray:
    """The line with `distance` metres taken off its far end."""
    total = _length_of(points)
    if distance <= 0.0 or total <= distance:
        return points
    return _cut(points, keep_head=True, at=(total - distance) / total)


def _trim_start(points: np.ndarray, distance: float) -> np.ndarray:
    """The line with `distance` metres taken off its near end."""
    total = _length_of(points)
    if distance <= 0.0 or total <= distance:
        return points
    return _cut(points, keep_head=False, at=distance / total)


def _unit(vector: np.ndarray, *, what: str) -> np.ndarray:
    length = float(np.linalg.norm(vector))
    if length < 1e-9:
        raise RouteError(f"{what} has no direction: two of its points are the same")
    return vector / length


def _turn_between(before: np.ndarray, after: np.ndarray) -> float:
    """Signed angle from one direction to another, in radians, CCW-positive."""
    return math.atan2(
        float(before[0] * after[1] - before[1] * after[0]),
        float(before[0] * after[0] + before[1] * after[1]),
    )


def _advance_past(
    points: np.ndarray, *, origin: np.ndarray, direction: np.ndarray, what: str
) -> np.ndarray:
    """`points` with any head that lies at or behind `origin` along `direction` removed.

    Lane centrelines are offset sideways from the way they came from, so where the bearing
    changes at a node the two offsets do not meet exactly and the next lane can start a
    fraction of a metre *behind* the last one ended - 0.26 m to 0.75 m on `junction-1`'s own
    routes. Concatenated, that is one sample driven backwards and a heading reversed by 180°,
    which `ReplayEgoCarPolicy` then plays back exactly as recorded.

    Resumed at the foot of the perpendicular rather than at the first surviving vertex, so
    the overlap costs no length and the line stays continuous.
    """
    reach = (points - origin) @ direction
    if float(reach[-1]) <= 0.0:
        raise RouteError(
            f"{what} lies entirely behind the piece before it, so the route would have to "
            "drive backwards to reach it"
        )
    if float(reach[0]) > 0.0:
        return points
    index = int(np.argmax(reach > 0.0))
    before, after = float(reach[index - 1]), float(reach[index])
    ratio = (0.0 - before) / (after - before)
    foot = points[index - 1] + (points[index] - points[index - 1]) * ratio
    return np.vstack([foot, points[index:]])


def _turn_curve(
    start: np.ndarray,
    start_direction: np.ndarray,
    end: np.ndarray,
    end_direction: np.ndarray,
    *,
    turn: float,
) -> np.ndarray | None:
    """A cubic Bezier leaving `start` along one direction and reaching `end` along another.

    Tangent to both, which is the whole point: the corner the connector left at each end of a
    junction was 82° at the median, and a curve that meets the lane at an angle is a corner
    however smooth it is in the middle.
    """
    chord = float(np.linalg.norm(end - start))
    if chord < COINCIDENT_M:
        return None
    wanted = _handle_fraction(turn) * chord
    # Shortening the handle straightens the curve towards the chord, so the search always
    # terminates: at zero it *is* the chord, which has no turn in it at all. What it costs is
    # tangency at the ends, and that is the right thing to trade - a curve that meets the lane
    # at a small angle is a small corner, while one that doubles back is not drivable at all.
    handle = wanted
    for _ in range(8):
        curve = _bezier(start, start + start_direction * handle, end - end_direction * handle, end)
        if _worst_turn_deg(curve) <= MAX_CURVE_TURN_DEG:
            return curve
        handle /= 2.0
    return _bezier(start, start, end, end)


def _bezier(
    start: np.ndarray, control_in: np.ndarray, control_out: np.ndarray, end: np.ndarray
) -> np.ndarray:
    """A cubic through four control points, sampled at `TURN_SAMPLE_M`."""
    # The control polygon bounds the curve, so its length bounds the arc - generous, and it
    # only decides how many samples to take.
    bound = sum(
        float(np.linalg.norm(second - first))
        for first, second in ((start, control_in), (control_in, control_out), (control_out, end))
    )
    count = max(8, int(math.ceil(bound / TURN_SAMPLE_M)))
    step = np.linspace(0.0, 1.0, count + 1).reshape(-1, 1)
    return (
        (1 - step) ** 3 * start
        + 3 * (1 - step) ** 2 * step * control_in
        + 3 * (1 - step) * step**2 * control_out
        + step**3 * end
    )


def _worst_turn_deg(line: np.ndarray) -> float:
    """The sharpest change of heading at any vertex, in degrees."""
    line = _drop_repeats(line)
    if len(line) < 3:
        return 0.0
    direction = np.diff(line, axis=0)
    heading = np.arctan2(direction[:, 1], direction[:, 0])
    return float(np.abs(np.degrees((np.diff(heading) + np.pi) % (2 * np.pi) - np.pi)).max())


def _turn(
    arriving: np.ndarray,
    leaving: np.ndarray,
    *,
    what: str,
    crossing: bool,
    reserve: float = 0.0,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Join two lanes: the trimmed approach, the turn between them, the trimmed exit.

    Returned in three pieces rather than one because the exit is trimmed at its start here
    and may be trimmed again at its end by the *next* junction, and the second trim has to be
    measured against what is left rather than against the untouched lane.

    `reserve` is how much of the exit lane to leave at its far end for whatever comes next.
    Two junctions on one lane is common in `junction-1` - a 14.58 m lane between two of them
    had 9.7 m taken by the first turn, and the second was left a 1 m approach it had to swing
    a 90° turn out of. See `_turn_reserve`.
    """
    allowed = MAX_CROSSING_M if crossing else MAX_JOIN_M
    gap = float(np.linalg.norm(leaving[0] - arriving[-1]))
    if gap > allowed:
        where = "junction" if crossing else "join"
        raise RouteError(
            f"the route leaves a {gap:.0f} m gap before {what}, more than a {where} spans. "
            "The lanes do not meet there, and a car would drive straight across it"
        )

    direction_in = _unit(arriving[-1] - arriving[-2], what="the lane before " + what)
    direction_out = _unit(leaving[1] - leaving[0], what=what)
    angle = _turn_between(direction_in, direction_out)

    if abs(math.degrees(angle)) < _SMOOTHING_MAX_DEG:
        # Only a shallow join can have an overlap worth trimming: two lanes of the same road
        # that do not quite meet. On a real turn the exit lane legitimately begins behind
        # where the approach ended - that is what a sharp left or a U-turn looks like - and
        # trimming there would refuse movements the map permits.
        leaving = _advance_past(leaving, origin=arriving[-1], direction=direction_in, what=what)
        if len(leaving) < 2:
            raise RouteError(f"{what} has nothing left of it once the overlap is removed")
        direction_out = _unit(leaving[1] - leaving[0], what=what)
        angle = _turn_between(direction_in, direction_out)
    # How far the next lane's line sits to the side of the one arriving, measured across the
    # direction of travel. Distinct from `gap`, which is along it as well.
    across = leaving[0] - arriving[-1]
    sideways = abs(float(across[0] * -direction_in[1] + across[1] * direction_in[0]))
    if abs(math.degrees(angle)) < _STRAIGHT_DEG and sideways < 0.05:
        # Already pointing the same way and already in line: the road carries on, and there
        # is nothing to build. This is the common case - most nodes in OSM are a way ending
        # and the next beginning, not a junction.
        return arriving, np.empty((0, 2), dtype=np.float64), leaving

    # Cut independently rather than by the smaller of the two. The Bezier is tangent to both
    # lanes whatever the cut lengths are, so making them equal only throws away room on the
    # side that has it - and it is the side that has none that produces the fold.
    wanted = _wanted_trim(angle=angle, sideways=sideways)
    leaving_length = _length_of(leaving)
    trim_in = min(wanted, _spare(_length_of(arriving)))
    trim_out = min(wanted, _spare(leaving_length), max(0.0, leaving_length - reserve))
    head = _trim_end(arriving, trim_in)
    tail = _trim_start(leaving, trim_out)
    curve = _turn_curve(head[-1], direction_in, tail[0], direction_out, turn=angle)
    if curve is None:
        return head, np.empty((0, 2), dtype=np.float64), tail
    # Both endpoints are already the last point of `head` and the first of `tail`.
    return head, curve[1:-1], tail


def _junction_box(
    arriving: np.ndarray,
    stubs: Sequence[np.ndarray],
    leaving: np.ndarray,
    *,
    what: str,
    reserve: float = 0.0,
    guide_from: int = 0,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Cross a run of clamped stub lanes as ONE manoeuvre, not one turn per stub.

    A big intersection is often mapped as several nodes joined by short ways rather than one
    node, and `_is_stub_lane` names the 2.00 m fragments that leaves behind. Built stub by
    stub, `_turn` ran once per fragment - and **the turn is not spread along the fragments,
    it is concentrated in the gaps between them.** Measured on `junction-1`'s `fold-demo`
    route, whose crossing turns -96.2 deg in total:

        lane 6   49.62 m   heading  +15.45 deg
        stub 7    2.00 m   heading  +17.64 deg     (still aimed along the approach)
                gap 2.65 m   <-- -88.97 of the -96.2 deg happens across this gap
        stub 8    2.00 m   heading  -71.33 deg     (already aimed at the exit)
        lane 9   26.83 m   heading  -80.78 deg

    So `_turn` had to swing 89 deg across 2.65 m, with `_spare(2.0)` = 1.00 m to cut back into
    on either side, and the cubic it laid between those cuts had about a metre of radius. Each
    such cubic passed `MAX_CURVE_TURN_DEG` on its own; the fold was the concatenation. The
    room the manoeuvre needs is in the **real** lanes either side, which is why the whole run
    is crossed at once and only those two are trimmed.

    The turn itself is ordinary and that is the point: at all 34 mid-route folds on
    `junction-1` a single arc through the junction needs a median 15.3 m of radius, while the
    line drawn through it came out at a median 1.40 m - against a DefaultVehicle turning
    circle of 2.94 m, which is geometry (`wheelbase / tan(max_steer)`) and so is not helped by
    slowing down. Over 300 seeded routes per extract this takes the share wasting more than
    90 deg of steering over a 12 m window from 53.1% to 13.8% on `junction-1` and 58.3% to
    24.0% on `mosque`, and `fold-demo`'s own tightest radius from 0.95 m to 9.60 m.

    **The stubs do not shape the curve below `BOX_GUIDE_MIN_DEG`, and that was measured rather
    than assumed.** Interpolating through them - their endpoints, or one midpoint each - puts
    a 19 m span beside a 2 m span at a shared waypoint, so the two handles differ tenfold and
    the curvature jumps exactly at the joint: routes carrying a bend tighter than the car can
    turn went from 64.9% to 80.3%, worse than doing nothing.

    **What this does not fix, said here so it is not rediscovered as a regression.** The drive
    line now leaves the mapped drivable surface more often - 0.23% of route distance to 1.16%
    on `junction-1`, worst continuous run 6.09 m to 25.81 m - because the surface through a
    box is a set of narrow ribbons following the *folded* path: through `fold-demo`'s box the
    corner spans 1275 m2 of ground and only 954 m2 of it is mapped as drivable. A line a car
    can steer and a line inside those ribbons are not the same line, and closing that gap is a
    change to what `conversion.py` exports, not to this module.
    """
    pieces = [arriving, *stubs, leaving]
    for before, after in zip(pieces, pieces[1:], strict=False):
        # Per span, never across the run. Every individual gap on either extract measures
        # under `MAX_CROSSING_M` (worst 14.43 m), while the straight line from the approach to
        # the exit reaches 41 m and would refuse a third of the routes the map permits.
        gap = float(np.linalg.norm(after[0] - before[-1]))
        if gap > MAX_CROSSING_M:
            raise RouteError(
                f"the route leaves a {gap:.0f} m gap inside the junction before {what}, more "
                "than a junction spans. The lanes do not meet there, and a car would drive "
                "straight across it"
            )

    direction_in = _unit(arriving[-1] - arriving[-2], what="the lane before " + what)
    direction_out = _unit(leaving[1] - leaving[0], what=what)
    angle = _turn_between(direction_in, direction_out)
    across = leaving[0] - arriving[-1]
    sideways = abs(float(across[0] * -direction_in[1] + across[1] * direction_in[0]))

    # The same trims `_turn` would take, sized by the whole crossing rather than by one stub,
    # and bounded by what each real lane can spare and by what the manoeuvre after this one
    # needs left over.
    wanted = _wanted_trim(angle=angle, sideways=sideways)
    guided = abs(math.degrees(angle)) >= BOX_GUIDE_MIN_DEG
    if guided:
        # **`_wanted_trim` diverges at a box that doubles back, and must not be believed
        # there.** It returns `TURN_RADIUS_M * tan(angle/2)`, the tangent length of an arc
        # meeting two straight lines - which runs away as the two become anti-parallel and is
        # clamped only at 170 deg, so a 180 deg crossing asks for **102.87 m**. `_spare` then
        # hands over every metre the two lanes have, and the crossing swallows the road either
        # side of it: measured on `junction-1`, a 178.9 deg box turned 166.5 m of a 167.5 m
        # straight approach into one curve, putting 43.1 m of the drive line up to 4.21 m off
        # the tarmac. **A U-turn is not two tangent lines meeting; it is a semicircle**, and
        # the room it needs is set by the box rather than by `tan`. `_box_path` is how far the
        # road itself travels through the box, and capping at it leaves **zero** metres off the
        # drivable surface on all three of `junction-1`'s guided boxes, against 43.1, 3.4 and
        # 26.9 uncapped. Larger multiples buy radius and start losing the road again - 1.5x
        # puts 20.5 m back off it - which is the trade this branch exists to hold.
        wanted = min(wanted, BOX_TRIM_SPANS * _box_path(arriving, stubs, leaving))
    leaving_length = _length_of(leaving)
    trim_in = min(wanted, _spare(_length_of(arriving)))
    trim_out = min(wanted, _spare(leaving_length), max(0.0, leaving_length - reserve))
    head = _trim_end(arriving, trim_in)
    tail = _trim_start(leaving, trim_out)

    guides: list[np.ndarray | None] = []
    if guided:
        # A box this sharp is a loop of short ways driven round rather than a corner cut
        # across, and one cubic tangent to both ends leaves the road: measured on
        # `junction-1`, the line strays a median 18.7 m and up to 40.8 m from the road the
        # stubs trace, against 0.45-0.91 m below 120 deg. One waypoint per stub - its
        # midpoint, never its two endpoints - keeps the line inside the box.
        middles: list[np.ndarray] = []
        headings: list[np.ndarray | None] = []
        # `guide_from` drops the stubs of a lane the route changes off part-way through the
        # box. They are still crossed - they are still this junction, and both the gap check
        # and `_box_path` above still count them - but a line steered through a lane it has
        # left and then through the one it joined doubles back. See `route_polyline`.
        for stub in stubs[guide_from:]:
            points = np.asarray(stub, dtype=np.float64)
            middles.append(points.mean(axis=0).reshape(1, 2))
            step = points[-1] - points[0]
            reach = float(np.linalg.norm(step))
            headings.append(step / reach if reach >= COINCIDENT_M else None)
        stacked = np.vstack([head[-1:], *middles, tail[:1]])
        # The waypoints are deduplicated exactly as `_drop_repeats` would, but the stub
        # headings have to fall away with them or the two lists stop lining up.
        keep = [0]
        for index in range(1, len(stacked)):
            if float(np.linalg.norm(stacked[index] - stacked[keep[-1]])) > COINCIDENT_M:
                keep.append(index)
        waypoints = stacked[keep]
        along: list[np.ndarray | None] = [None, *headings, None]
        guides = [along[index] for index in keep]
    else:
        waypoints = _drop_repeats(np.vstack([head[-1:], tail[:1]]))
    if len(waypoints) < 2:
        return head, np.empty((0, 2), dtype=np.float64), tail
    tangents = _catmull_rom_tangents(waypoints, direction_in, direction_out)
    # **At an interior waypoint of a box that doubles back, Catmull-Rom is degenerate and the
    # stub's own heading is the answer.** `P[i+1] - P[i-1]` across a U-turn is the *net*
    # displacement, which points along the exit rather than through the turn: measured on
    # `junction-1`'s 178.85 deg crossing, the middle waypoint came out at +100.98 deg against
    # the approach's +94.65 deg, so the first span took 6.33 deg and the second was left to
    # render 172.52 deg on one cubic - which no cubic can do, and it doubled back at 0.16 m of
    # radius. A stub is a real piece of road pointing the way traffic goes through the box, so
    # steering by it splits a 180 deg crossing into two ordinary ~90 deg spans. Interior only:
    # the ends stay pinned to the lanes either side, because tangency to a 2 m fragment at both
    # ends of a manoeuvre is what starved this in the first place. All three of `junction-1`'s
    # guided boxes improve - 0.16 -> 5.64 m, 4.26 -> 5.75 m and 6.90 -> 7.31 m.
    for index, guide in enumerate(guides):
        if 0 < index < len(tangents) - 1 and guide is not None:
            tangents[index] = guide

    spans: list[np.ndarray] = []
    for index in range(len(waypoints) - 1):
        turn = _turn_between(tangents[index], tangents[index + 1])
        span = _turn_curve(
            waypoints[index],
            tangents[index],
            waypoints[index + 1],
            tangents[index + 1],
            turn=turn,
        )
        if span is None:
            continue
        # The joint is already the last point of the span before it.
        spans.append(span if not spans else span[1:])
    if not spans:
        return head, np.empty((0, 2), dtype=np.float64), tail
    curve = np.vstack(spans)
    # Both ends are already `head[-1]` and `tail[0]`.
    return head, curve[1:-1], tail


def _box_path(
    arriving: np.ndarray, stubs: Sequence[np.ndarray], leaving: np.ndarray
) -> float:
    """How far the road itself travels through a junction box, end of lane to start of lane.

    Measured on the **untrimmed** lanes deliberately: it is what bounds the trim, so it must
    not depend on it. Each stub contributes its midpoint, which is where `_junction_box`
    steers the crossing through.
    """
    points = [
        arriving[-1],
        *(np.asarray(stub, dtype=np.float64).mean(axis=0) for stub in stubs),
        leaving[0],
    ]
    steps = np.diff(np.asarray(points, dtype=np.float64), axis=0)
    return float(np.linalg.norm(steps, axis=1).sum())


def _spare(length: float) -> float:
    """How much of a lane of this length one turn may use. See `MAX_TURN_TRIM_FRACTION`."""
    return max(MAX_TURN_TRIM_FRACTION * length, length - MIN_TANGENT_M)


def _is_stub_lane(points: np.ndarray) -> bool:
    """Is this lane the interior of a junction box rather than a piece of road?

    A big intersection is often mapped as several nodes joined by short ways rather than one
    node - `junction-1`'s node 1927184814 is four one-way ways in a loop round the box. Those
    ways are shorter than the two setbacks that cut every lane back from its junctions, so
    `generation._trimmed_edge` scales both setbacks down and stops at `MIN_TRIMMED_LANE_M`.
    The length is therefore the whole test, and it is exact rather than approximate: a lane
    measures `MIN_TRIMMED_LANE_M` only when the clamp bound. The next lengths up - 2.07 m,
    2.37 m, 3.65 m - kept their setbacks and are ordinary road that must not be caught.

    The same test as `conversion._stub_lanes`, which suppresses the *paint* on these lanes for
    a related reason. Written here rather than imported because `conversion` imports this
    module; the tolerance is `STUB_LANE_TOLERANCE_M` in both, and nothing in either extract
    sits within 7 cm of the constant, so the two cannot drift apart in practice.
    """
    return _length_of(points) <= MIN_TRIMMED_LANE_M + STUB_LANE_TOLERANCE_M


def _catmull_rom_tangents(
    waypoints: np.ndarray, first: np.ndarray, last: np.ndarray
) -> np.ndarray:
    """A direction at every waypoint: pinned at the ends, `P[i+1] - P[i-1]` in between.

    Used only where `_junction_box` guides a crossing by the stubs' positions. The interior
    directions come from the neighbouring waypoints rather than from each stub's own heading -
    not because that heading is meaningless (it is not: the stubs of a box interpolate its turn
    perfectly well) but because the drive line must not be tangent to a 2 m fragment at both
    ends. That tangency is what starved the manoeuvre; see `_junction_box`.
    """
    tangents = np.empty_like(waypoints)
    tangents[0] = first
    tangents[-1] = last
    for index in range(1, len(waypoints) - 1):
        span = waypoints[index + 1] - waypoints[index - 1]
        length = float(np.linalg.norm(span))
        # Falls back to the chord out of this point rather than raising: three waypoints in a
        # row that double back exactly is not a shape any real junction has, and a route is
        # not worth refusing over it.
        if length < COINCIDENT_M:
            span = waypoints[index + 1] - waypoints[index]
            length = float(np.linalg.norm(span))
        tangents[index] = span / length if length >= COINCIDENT_M else tangents[index - 1]
    return tangents


def _wanted_trim(*, angle: float, sideways: float) -> float:
    """How far back into each lane a turn of `angle` wants to cut, before either can spare it.

    `TURN_RADIUS_M · tan(θ/2)` is the tangent length of an arc of that radius through that
    turn. A shallow join is not a corner but a sideways step between two offset lane lines,
    and wants spreading over `_smoothing_span` instead - taking the larger, because a join can
    be both slightly turned and noticeably offset.
    """
    trim = TURN_RADIUS_M * math.tan(min(abs(angle), math.radians(170.0)) / 2.0)
    if abs(math.degrees(angle)) < _SMOOTHING_MAX_DEG:
        return max(trim, _smoothing_span(sideways))
    return trim


def _turn_reserve(joining: np.ndarray, following: np.ndarray, *, what: str) -> float:
    """How much of `joining` must be left at its far end for the junction turn after it.

    A lane change positions itself in the window the two lanes share and, unasked, will take
    all of it. When a junction turn comes next that leaves the turn nothing to cut back into,
    and the cubic it builds between a 0.2 m trim and a 4.2 m chord doubles back on itself - an
    82° cusp measured on `junction-1`, from three changes in a row onto a 7.11 m lane.

    Estimated from the two untouched centrelines rather than from the trimmed line the change
    will actually hand over, which is not built yet. That is exact for the direction, because
    a change ends running parallel to the lane it joined, and generous for the length, which
    is the safe way round.
    """
    direction_in = _unit(joining[-1] - joining[-2], what=what)
    direction_out = _unit(following[1] - following[0], what=what)
    across = following[0] - joining[-1]
    sideways = abs(float(across[0] * -direction_in[1] + across[1] * direction_in[0]))
    trim = _wanted_trim(angle=_turn_between(direction_in, direction_out), sideways=sideways)
    # Bounded by what the lane *after* the turn can match, since `_turn` cuts both sides by
    # the same amount, and by `MIN_TANGENT_M` of straight to read a direction off.
    return min(trim, _spare(_length_of(following))) + MIN_TANGENT_M


def _smoothing_span(sideways: float) -> float:
    """Half the distance a sideways step of `sideways` metres needs to be spread over.

    A cubic S that shifts `sideways` over a span L peaks at about 6·sideways/L² of curvature,
    so the span that keeps it at `SMOOTHING_RADIUS_M` is sqrt(6·sideways·R).
    """
    return math.sqrt(6.0 * max(sideways, 0.0) * SMOOTHING_RADIUS_M) / 2.0


def _project_along(line: np.ndarray, point: np.ndarray) -> float:
    """How far along `line` the nearest point to `point` lies."""
    steps = np.linalg.norm(np.diff(line, axis=0), axis=1)
    travelled = np.concatenate([[0.0], np.cumsum(steps)])
    best, best_at = math.inf, 0.0
    for index in range(len(line) - 1):
        segment = line[index + 1] - line[index]
        length = float(np.linalg.norm(segment))
        if length < 1e-9:
            continue
        along = float(np.dot(point - line[index], segment) / length)
        along = min(max(along, 0.0), length)
        distance = float(np.linalg.norm(line[index] + segment / length * along - point))
        if distance < best:
            best, best_at = distance, float(travelled[index]) + along
    return best_at


def _lane_change(
    leaving: np.ndarray, joining: np.ndarray, *, what: str, reserve: float = 0.0
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Cross into a lane alongside: the run-up, the crossing, the run-out.

    `_lane_change_moves` has already refused any neighbour that is not the same stretch of
    road running the same way, so the two centrelines are parallel and comparable in length.

    Where the crossing sits is found by *projection*, not by taking both midpoints. The two
    are the same only when the car arrives at the start of the lane it is leaving, and it
    usually does not: a junction turn trims the front off, and a change immediately before
    this one leaves only the far end. Taking midpoints then put the far side of the crossing
    behind the near side, and the curve doubled back on itself - measured on `junction-1` at
    118° of turn in a single 0.1 s step, on a route with two changes in a row.

    `reserve` is how much of the joining lane must be left past the end of the crossing, for
    whatever manoeuvre comes next. Without it a change will use the whole window and starve
    the junction turn after it; see `_turn_reserve`.
    """
    entry = _project_along(joining, leaving[0])
    exit_ = _project_along(joining, leaving[-1])
    if exit_ - entry < 1e-6:
        return leaving, np.empty((0, 2), dtype=np.float64), joining
    middle = (entry + exit_) / 2.0
    abreast = _cut(joining, keep_head=True, at=middle / _length_of(joining))[-1]
    sideways = float(np.linalg.norm(abreast - leaving[-1]))
    reach = min(_smoothing_span(sideways), _CHANGE_MAX_FRACTION * (exit_ - entry))

    # The crossing occupies `middle ± reach`, and it has to fit between where the two lanes
    # first run alongside each other and whatever the next manoeuvre wants left over. Moved
    # earlier before it is made shorter: a change taken sooner is still a change, one taken
    # over less road is a swerve the speed profile then has to brake for.
    #
    # The reserve yields first, and can yield to nothing. A short lane with a change and a
    # junction on it is a road the map genuinely has, and refusing to drive one because the
    # turn after it would be tight cuts the drivable network on the strength of a constant.
    # What is left is bounded by `_spare` inside `_turn` and checked by `_turn_curve`,
    # whose halving loop refuses any cubic turning more than `MAX_CURVE_TURN_DEG` at a sample.
    window = _length_of(joining) - entry
    reserve = min(reserve, max(0.0, window - 2.0 * MIN_TANGENT_M))
    latest = _length_of(joining) - reserve
    reach = min(reach, (latest - entry) / 2.0)
    middle = min(max(middle, entry + reach), latest - reach)

    # Mapped back onto the lane being left in proportion, which is exact while the two run
    # parallel and is what `_lane_change_moves` guarantees.
    at = (middle - reach - entry) / (exit_ - entry)
    head = _cut(leaving, keep_head=True, at=min(max(at, 1e-6), 1.0))
    tail = _trim_start(joining, middle + reach)
    if len(head) < 2 or len(tail) < 2:
        return head, np.empty((0, 2), dtype=np.float64), tail
    direction_in = _unit(head[-1] - head[-2], what=f"the lane changed out of before {what}")
    direction_out = _unit(tail[1] - tail[0], what=what)
    curve = _turn_curve(head[-1], direction_in, tail[0], direction_out, turn=0.0)
    if curve is None:
        return head, np.empty((0, 2), dtype=np.float64), tail
    return head, curve[1:-1], tail


def _refuse_reversals(line: np.ndarray) -> None:
    """Refuse a drive that snaps round, however it got that way.

    The mirror of `MAX_JOIN_M`. A hole at a join was checked from the day this module was
    written; a *reversal* never was, and 55 of `junction-1`'s 83 connectors produced one. It
    survives resampling as a 180° heading flip, and `ReplayEgoCarPolicy` sets the car's
    heading from that array without complaint, so this is the only place it can be caught.
    """
    if len(line) < 3:
        return
    direction = np.diff(line, axis=0)
    heading = np.arctan2(direction[:, 1], direction[:, 0])
    turns = np.abs(np.degrees((np.diff(heading) + np.pi) % (2 * np.pi) - np.pi))
    worst = int(turns.argmax())
    if turns[worst] > MAX_VERTEX_TURN_DEG:
        travelled = float(np.linalg.norm(direction[: worst + 1], axis=1).sum())
        raise RouteError(
            f"the route turns {turns[worst]:.0f}° in one step, {travelled:.0f} m in at "
            f"({line[worst + 1][0]:.1f}, {line[worst + 1][1]:.1f}). A car cannot do that, "
            "and replaying it spins the recorded car on the spot"
        )


def _drop_repeats(points: np.ndarray, *, tolerance: float = COINCIDENT_M) -> np.ndarray:
    """Collapse coincident consecutive points.

    Lanes meet where one ends and the next begins, and a connector starts where its
    approach stops, so joins routinely produce a duplicate. A zero-length step has no
    direction, which would make the heading at that sample undefined.
    """
    if len(points) < 2:
        return points
    keep = [0]
    for index in range(1, len(points)):
        if float(np.linalg.norm(points[index] - points[keep[-1]])) > tolerance:
            keep.append(index)
    return points[keep]


def _graph(
    lanes: dict[str, LaneFeature],
    neighbours: dict[str, tuple[list[str], list[str]]],
    moves: dict[str, list[str]],
) -> nx.DiGraph:
    """The drivable graph, weighted by how far taking each step actually travels.

    Built from the same two relations `_reachability` reports on, so a route can only ever
    use a move the Stage 6 page also shows.
    """
    graph = nx.DiGraph()
    graph.add_nodes_from(lanes)
    lengths = {
        lane_id: _length_of(_xy(lane.centerline)) for lane_id, lane in lanes.items()
    }
    for lane_id, (_, exits) in neighbours.items():
        for target in exits:
            graph.add_edge(lane_id, target, weight=lengths[target], change=False)
    for lane_id, sideways in moves.items():
        for target in sideways:
            if graph.has_edge(lane_id, target):
                continue
            # Half the lane, because a change lands mid-way along the neighbour rather than
            # at its start - which is also how the geometry below splices it.
            graph.add_edge(
                lane_id,
                target,
                weight=lengths[target] / 2.0 + LANE_CHANGE_COST_M,
                change=True,
            )
    return graph


def plan_route(
    *,
    model: PreliminaryLaneModel,
    neighbours: dict[str, tuple[list[str], list[str]]],
    moves: dict[str, list[str]],
    name: str,
    start_lane: str,
    end_lane: str,
    speed_kph: float | None = None,
    signals: Sequence[SignalTiming] = (),
) -> Route:
    """The shortest drive from `start_lane` to `end_lane`, or why there isn't one.

    `signals` are the lights on the map. Any of them the route passes are read here, once, and
    the waits are carried on the `Route` rather than worked out again when the track is built -
    the summary and the recorded car have to stop in the same places, and two derivations of
    the same clock are two chances for them not to.
    """
    lanes = {lane.identifier: lane for lane in model.lanes}
    for role, lane_id in (("start", start_lane), ("end", end_lane)):
        if lane_id not in lanes:
            raise RouteError(f"route {name!r} names {lane_id} as its {role}, which is not a lane")
    if start_lane == end_lane:
        raise RouteError(f"route {name!r} starts and ends on the same lane, {start_lane}")

    graph = _graph(lanes, neighbours, moves)
    try:
        chain = nx.shortest_path(graph, start_lane, end_lane, weight="weight")
    except nx.NetworkXNoPath as error:
        raise RouteError(
            f"route {name!r}: no drive exists from {start_lane} to {end_lane}. Most lane "
            "pairs have none - the map is one-way in most places - so this is a normal "
            "answer rather than a fault"
        ) from error

    changes = tuple(
        position
        for position, (before, after) in enumerate(zip(chain, chain[1:], strict=False), start=1)
        if graph.edges[before, after]["change"]
    )
    limits = [lanes[lane_id].speed_limit_kph for lane_id in chain]
    speed_kph = speed_kph if speed_kph is not None else min(limits)
    cruise_mps = speed_kph / 3.6

    # Measured off the geometry rather than off the graph weights, which carry the
    # lane-change penalty and so are a search cost, not a distance.
    polyline = route_polyline(model=model, route_lanes=chain, lane_changes=changes)
    waits = resolve_waits(
        polyline, cruise_mps=cruise_mps, route_lanes=chain, signals=signals
    )
    _, travelled, speed = speed_profile(
        polyline, cruise_mps=cruise_mps, stops_at=[wait.at_m for wait in waits]
    )
    driving = float(_arrival_times(travelled, speed)[-1])
    return Route(
        name=name,
        start_lane=start_lane,
        end_lane=end_lane,
        lanes=tuple(chain),
        lane_changes=changes,
        distance_m=_length_of(polyline),
        speed_mps=cruise_mps,
        # Not distance / speed: the car slows for every turn on the way and stands still at
        # every red, and a summary that said otherwise would disagree with the track built
        # from the same profile.
        duration_s=driving + sum(wait.waited_s for wait in waits),
        slowest_mps=float(speed.min()),
        driving_duration_s=driving,
        waits=waits,
    )


def _junction_nodes(model: PreliminaryLaneModel) -> set[str]:
    """Nodes where roads meet, as opposed to where one road is merely split in two.

    The same test `generation._node_setbacks` uses, re-derived here from `source_edge`
    rather than passed in, because putting it on the lane model would move the schema
    version and with it the generation fingerprint - which would invalidate a live review
    to carry a fact both sides can work out for themselves.

    More than two distinct neighbours, not more than two edges: a two-way road already
    puts four directed edges on every node along it.
    """
    adjacency: dict[str, set[str]] = {}
    for lane in model.lanes:
        start, end = lane.source_edge[0], lane.source_edge[1]
        adjacency.setdefault(start, set()).add(end)
        adjacency.setdefault(end, set()).add(start)
    return {node for node, neighbours in adjacency.items() if len(neighbours) > 2}


def _bend_deg(before: LaneFeature, after: LaneFeature) -> float:
    """How far the road turns between the end of one lane and the start of the next."""
    a0, a1 = before.centerline[-2], before.centerline[-1]
    b0, b1 = after.centerline[0], after.centerline[1]
    entry = math.atan2(a1.y - a0.y, a1.x - a0.x)
    leave = math.atan2(b1.y - b0.y, b1.x - b0.x)
    return abs(math.degrees(math.atan2(math.sin(leave - entry), math.cos(leave - entry))))


def junction_crossings(model: PreliminaryLaneModel) -> set[tuple[str, str]]:
    """Every lane-to-lane step that crosses a junction, as `(from_lane_id, to_lane_id)`.

    Which side of `MAX_JOIN_M` / `MAX_CROSSING_M` a step is judged against, and nothing
    else. Lifted out of `route_polyline` so the Stage 6 pages can be handed the answer
    instead of working it out again: the browser previews the drive and Python re-derives
    it, and the two disagreeing is how a route the converter builds came to be refused on
    the page. The browser cannot reproduce this test anyway - 21 of `junction-1`'s 26
    straight-through cases turn on `source_edge`, which the payload does not carry - and a
    second implementation would be a second thing to drift.
    """
    # The connectors say *which* steps cross a junction, which is all they are asked for
    # here. Their geometry is not used: `topology.connector_curve` builds a marker for the
    # inspection map, and splicing it in as a drive line is what put a 180° flip at 55 of
    # `junction-1`'s 83 movements. See the module docstring.
    crossings = {
        (connector.from_lane_id, connector.to_lane_id)
        for connector in model.connectors
        if connector.status == "active"
    }
    # A connector is not the only way to cross a junction. A road running *straight through*
    # one is recorded as a plain continuation - lane names lane, no connector, because
    # topologically nothing happens there - and it used to be indistinguishable from a
    # continuation within a single way, because both met exactly at the shared node. They no
    # longer do: `generation._node_setbacks` cuts every lane back to the edge of the junction,
    # so a straight-through movement now has the whole junction to span, and on `junction-1`
    # 26 of 211 continuations open past `MAX_JOIN_M`, the widest at 17.3 m.
    #
    # Told apart by what generation did, not by the way and not by the gap. Not by the way,
    # because OSM keeps one way id across a junction it runs through, so both sides read the
    # same. Not by the gap, because a threshold that promoted anything wide enough would
    # quietly swallow the hole this guard exists to catch.
    #
    # Two things part a join, and both are deliberate: the junction setback, which applies where
    # more than two roads meet, and the bend fillet, which applies at a through node the road
    # visibly turns at. `BEND_FILLET_MIN_DEGREES` is imported rather than restated so the two
    # modules cannot drift.
    lanes = {lane.identifier: lane for lane in model.lanes}
    junctions = _junction_nodes(model)
    crossings |= {
        (before.identifier, after.identifier)
        for before in model.lanes
        for after in (lanes.get(item) for item in before.exit_lanes)
        if after is not None
        and (
            before.source_edge[1] in junctions
            or _bend_deg(before, after) >= BEND_FILLET_MIN_DEGREES
        )
    }
    return crossings


def route_polyline(
    *,
    model: PreliminaryLaneModel,
    route_lanes: tuple[str, ...] | list[str],
    lane_changes: tuple[int, ...],
) -> np.ndarray:
    """The path a car actually drives along a chain of lanes.

    Junction movements follow the connector; lane changes cross diagonally over the second
    half of one lane and the first half of the next; everything else is the lane centreline.
    """
    lanes = {lane.identifier: lane for lane in model.lanes}
    changing = set(lane_changes)
    crossings = junction_crossings(model)
    centrelines = [_xy(lanes[lane_id].centerline) for lane_id in route_lanes]
    is_stub = [_is_stub_lane(points) for points in centrelines]

    finished: list[np.ndarray] = []
    current: np.ndarray | None = None

    position = 0
    while position < len(route_lanes):
        lane_id = route_lanes[position]
        centre = centrelines[position]
        if current is None:
            current = centre
            position += 1
            continue
        if is_stub[position]:
            # A run of stubs is the inside of one junction box and is crossed once. Gathered
            # the way a run of lane changes is below, and for the same reason: built stub by
            # stub, the second manoeuvre gets only what the first left over - and on a 2 m
            # lane the first leaves 1 m. See `_junction_box`.
            last = position
            while last + 1 < len(route_lanes) and is_stub[last + 1]:
                last += 1
            # **A change inside the run drops every stub before it as a guide.** The stubs
            # steer the crossing by their positions, and a change is a step sideways onto a
            # lane that starts further *back* through the box - guided through both, the line
            # runs out to the stub it is leaving and doubles back to the one it joined, which
            # measured 76.37 deg at a vertex on `junction-1` where `MAX_VERTEX_TURN_DEG`'s
            # sweep allows 30. The run is still crossed whole, because the road either side of
            # the change is the same junction; only the lane whose stubs are followed changes.
            guiding = position
            for step in range(position, last + 1):
                if step in changing:
                    guiding = step
            exit_at = last + 1
            # **Neither of the two awkward cases may go back to the per-lane path**, which is
            # the starved construction this branch exists to replace. Measured over 300 seeded
            # routes, the runs left to it came out at a median radius of 0.49 m on
            # `junction-1` and 1.12 m on `mosque` - the worst manoeuvres on either map, and
            # 56.7% and 64.6% of them tighter than a car can turn.
            #
            # The guard that used to stand here also excluded a run whose *exit* is reached by
            # changing lane. **That case cannot arise**, and the reason is worth keeping: a
            # neighbour is only a lane-change destination if it shares the `source_edge`
            # (`conversion._lane_change_moves`), and a stub's side-neighbour is therefore
            # another lane of the same clamped edge - another stub, which the gather above has
            # already swallowed. Counted over 300 seeded routes: 0 of 487 runs on `junction-1`
            # and 0 of 428 on `mosque`, against 101 and 149 that carry a change *inside* the
            # run, which is the real case and is handled by `guiding` above. The condition is
            # gone rather than kept, because a guard against something impossible reads as
            # evidence that it happens.
            if exit_at < len(route_lanes):
                exit_lane = route_lanes[exit_at]
                exit_centre = centrelines[exit_at]
                after = exit_at + 1
                reserve = 0.0
                if after < len(route_lanes) and after not in changing:
                    reserve = _turn_reserve(
                        exit_centre, centrelines[after], what=f"lane {exit_lane}"
                    )
                head, curve, tail = _junction_box(
                    current,
                    centrelines[position : last + 1],
                    exit_centre,
                    what=f"lane {exit_lane}",
                    reserve=reserve,
                    guide_from=guiding - position,
                )
                finished.append(head)
                if len(curve):
                    finished.append(curve)
                current = tail
                position = exit_at + 1
                continue
            # A run that *ends the route* has no lane beyond it to aim at, so the last stub
            # becomes the one it is aimed at and the rest of the run guides the way there. It
            # is still one manoeuvre; there is simply no road on the far side of it.
            head, curve, tail = _junction_box(
                current,
                centrelines[position:last],
                centrelines[last],
                what=f"lane {route_lanes[last]}",
                guide_from=min(guiding - position, max(0, last - position - 1)),
            )
            finished.append(head)
            if len(curve):
                finished.append(curve)
            current = tail
            position = last + 1
            continue
        if position in changing:
            # A run of changes is one manoeuvre, not several. A car crossing three lanes
            # sweeps across them once; building a separate crossing per lane gives the
            # second one only what the first left over, and on a 20 m pair that is a metre.
            # The lanes in between are passed through diagonally, which is what happens.
            last = position
            while last + 1 < len(route_lanes) and (last + 1) in changing:
                last += 1
            lane_id = route_lanes[last]
            centre = centrelines[last]
            position = last
            # What comes after the crossing decides how much of this lane it may use. A run
            # of changes is always followed by a turn or by the end of the route, never by
            # another change, because the run above already swallowed those.
            reserve = 0.0
            if last + 1 < len(route_lanes):
                following = centrelines[last + 1]
                reserve = _turn_reserve(centre, following, what=f"lane {lane_id}")
            # A lane arrived at by changing across is meant to start away from where the
            # last piece ended - that offset is the manoeuvre, not a fault - so it is
            # crossed into rather than turned into.
            head, curve, tail = _lane_change(
                current, centre, what=f"lane {lane_id}", reserve=reserve
            )
        else:
            # A second junction on the same lane needs room left for it, or the first turn
            # takes everything and the second has nothing to swing out of. A lane change
            # after this one does not: it places itself in the window the two lanes share,
            # not at the far end.
            reserve = 0.0
            following = position + 1
            if following < len(route_lanes) and following not in changing:
                reserve = _turn_reserve(
                    centre, centrelines[following], what=f"lane {lane_id}"
                )
            head, curve, tail = _turn(
                current,
                centre,
                what=f"lane {lane_id}",
                crossing=(route_lanes[position - 1], lane_id) in crossings,
                reserve=reserve,
            )
        finished.append(head)
        if len(curve):
            finished.append(curve)
        current = tail
        position += 1

    if current is None:
        raise RouteError("a route has to name at least one lane")
    finished.append(current)

    line = _drop_repeats(np.vstack(finished))
    _refuse_reversals(line)
    return line


def _densify(polyline: np.ndarray, *, spacing: float) -> tuple[np.ndarray, np.ndarray]:
    """The same line with a vertex every `spacing` metres, and how far along each one is."""
    steps = np.linalg.norm(np.diff(polyline, axis=0), axis=1)
    along = np.concatenate([[0.0], np.cumsum(steps)])
    total = float(along[-1])
    if total <= spacing:
        return polyline, along
    wanted = np.linspace(0.0, total, int(math.ceil(total / spacing)) + 1)
    dense = np.stack(
        [
            np.interp(wanted, along, polyline[:, 0]),
            np.interp(wanted, along, polyline[:, 1]),
        ],
        axis=1,
    )
    return dense, wanted


def speed_profile(
    polyline: np.ndarray,
    *,
    cruise_mps: float,
    stops_at: Sequence[float] = (),
    lateral_accel_mps2: float = LATERAL_ACCEL_MPS2,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """The drive resampled evenly, how far along each point is, and how fast the car is there.

    Three passes, in the order a driver does them. The curvature at a point caps the speed
    there, because a 90° junction taken at the speed limit is a track that teaches an agent
    it can do the same. Then a forward and a backward pass bound how fast the speed may
    change, which is what puts the braking *before* the turn instead of at it.

    `stops_at` are distances along the drive where the car is brought to rest - a red light.
    They are pinned *after* the `MIN_SPEED_MPS` floor, which exists to stop the geometry
    producing a crawl and has nothing to say about a car that is deliberately stationary. The
    same two passes then brake into the stop and pull away from it, so a baked stop is a real
    approach rather than a speed that steps to zero between two samples.

    `lateral_accel_mps2` defaults to the module constant, so the ego's drive is unchanged.
    It is a parameter because **`LATERAL_ACCEL_MPS2` is not a comfort figure** - it is pinned
    to the ego's own 30°-per-step gate, and a caller driving something other than the recorded
    car may need a gentler one. `traffic_routes` passes 4.0: MetaDrive's IDM tracks the line
    with a PID that has a fixed 1 m preview, and at 8.5 it runs wide on the corners this same
    geometry asks the ego to slow for.
    """
    dense, travelled = _densify(polyline, spacing=PROFILE_SAMPLE_M)
    steps = np.diff(travelled)
    limit = np.full(len(dense), float(cruise_mps), dtype=np.float64)

    if len(dense) >= 3:
        # Curvature as turn per metre, which is what the steering wheel does. The obvious
        # alternative - the circumradius of each triple - reads a polyline's *concentrated*
        # bend as if it were spread over the whole window, and on `junction-1` reported 5.4 m
        # where the path really turned through 2.7 m.
        direction = np.diff(dense, axis=0)
        heading = np.arctan2(direction[:, 1], direction[:, 0])
        turn = np.abs((np.diff(heading) + np.pi) % (2 * np.pi) - np.pi)
        span = (steps[:-1] + steps[1:]) / 2.0
        bends = turn > 1e-9
        radius = np.full(len(turn), np.inf)
        radius[bends] = span[bends] / turn[bends]
        limit[1:-1] = np.minimum(limit[1:-1], np.sqrt(lateral_accel_mps2 * radius))

    limit = np.clip(limit, min(MIN_SPEED_MPS, cruise_mps), cruise_mps)
    for stop in stops_at:
        limit[int(np.abs(travelled - stop).argmin())] = 0.0

    speed = limit.copy()
    for index in range(1, len(speed)):
        reachable = math.sqrt(speed[index - 1] ** 2 + 2.0 * ACCEL_MPS2 * steps[index - 1])
        speed[index] = min(speed[index], reachable)
    for index in range(len(speed) - 2, -1, -1):
        stoppable = math.sqrt(speed[index + 1] ** 2 + 2.0 * BRAKE_MPS2 * steps[index])
        speed[index] = min(speed[index], stoppable)
    return dense, travelled, speed


def _arrival_times(travelled: np.ndarray, speed: np.ndarray) -> np.ndarray:
    """When the car reaches each vertex, from the speed at either end of each step."""
    steps = np.diff(travelled)
    mean = (speed[:-1] + speed[1:]) / 2.0
    return np.concatenate([[0.0], np.cumsum(steps / mean)])


def resolve_waits(
    polyline: np.ndarray,
    *,
    cruise_mps: float,
    route_lanes: Sequence[str],
    signals: Sequence[SignalTiming],
) -> tuple[Wait, ...]:
    """Which lights on this route are red when the car gets there, and for how long.

    Resolved front to back, because each wait moves every arrival after it: a car held 14 s at
    the first light meets the second one 14 s later into the cycle, which is a different
    colour. One pass, and each light is decided once.

    A light is read twice. First on the approach, from the arrival the car would have if it
    did not stop - that is what a driver sees, and it is what decides whether to brake. Then,
    if it was red, again from the arrival with the braking included, because slowing down
    takes time and the light may have changed during it; if it has, the car does not stop
    after all. The order matters and cannot be collapsed into one reading: deciding from the
    braked arrival alone oscillates - the stop delays the car into a green, the green removes
    the stop, and the arrival moves back into the red.
    """
    on_route = set(route_lanes)
    placed = sorted(
        (
            (
                max(
                    0.0,
                    _project_along(polyline, np.array(signal.stop_point)) - STOP_LINE_SETBACK_M,
                ),
                signal,
            )
            for signal in signals
            if signal.lane_id in on_route
        ),
        key=lambda item: item[0],
    )

    waits: list[Wait] = []
    pinned: list[float] = []
    held = 0.0
    for at, signal in placed:
        # Two lights within a metre of each other are one stop line. Left in, they would put
        # two zero speeds inside one profile sample, and the time to cross between them is a
        # distance divided by a mean speed of zero.
        if pinned and at - pinned[-1] < SEPARATE_STOPS_M:
            continue
        colour = colour_at(
            seconds=held + _time_at(polyline, cruise_mps=cruise_mps, stops_at=pinned, at=at),
            cycle_seconds=signal.cycle_seconds,
            green_seconds=signal.green_seconds,
            yellow_seconds=signal.yellow_seconds,
            offset_seconds=signal.offset_seconds,
        )
        if colour != LIGHT_RED:
            continue
        pinned.append(at)
        arrived = held + _time_at(polyline, cruise_mps=cruise_mps, stops_at=pinned, at=at)
        waited = seconds_until_green(
            seconds=arrived,
            cycle_seconds=signal.cycle_seconds,
            green_seconds=signal.green_seconds,
            offset_seconds=signal.offset_seconds,
        )
        if waited <= 0.0:
            # It went green while the car was slowing for it, so there is nothing to stop for.
            pinned.pop()
            continue
        waits.append(Wait(lane_id=signal.lane_id, at_m=at, arrived_s=arrived, waited_s=waited))
        held += waited
    return tuple(waits)


def _time_at(
    polyline: np.ndarray, *, cruise_mps: float, stops_at: Sequence[float], at: float
) -> float:
    """How long the car takes to reach `at` metres along, given the stops it makes before it."""
    _, travelled, speed = speed_profile(polyline, cruise_mps=cruise_mps, stops_at=stops_at)
    return float(np.interp(at, travelled, _arrival_times(travelled, speed)))


def ego_track(
    *, route: Route, polyline: np.ndarray, time_step_s: float = TIME_STEP_S
) -> dict[str, Any]:
    """The recorded car, resampled at the step the simulator will be run at.

    Shape read off `ScenarioDescription._check_object_state_dict`: every state array is the
    scenario's length, 2-D arrays may not be empty in their second axis, and the metadata's
    `object_id` has to equal the key the track is stored under.

    `time_step_s` changes only how densely the drive is written down. The route, the speed
    profile and the waits are all decided before this point and do not move with it.
    """
    if route.distance_m < MIN_ROUTE_M:
        raise RouteError(
            f"route {route.name!r} is {route.distance_m:.1f} m long. Below "
            f"{MIN_ROUTE_M:.0f} m MetaDrive treats the car as static and the episode "
            "succeeds on its first frame"
        )
    samples, sampled_speed = _sample_in_time(
        polyline, cruise_mps=route.speed_mps, waits=route.waits, time_step_s=time_step_s
    )
    if len(samples) < 2:
        raise RouteError(f"route {route.name!r} is too short to drive: {route.distance_m:.1f} m")

    count = len(samples)
    heading = _headings(samples)

    position = np.zeros((count, 3), dtype=np.float64)
    position[:, :2] = samples
    velocity = np.stack(
        [np.cos(heading) * sampled_speed, np.sin(heading) * sampled_speed], axis=1
    )

    def constant(value: float) -> np.ndarray:
        return np.full(count, value, dtype=np.float64)

    return {
        "type": "VEHICLE",
        "state": {
            "position": position,
            "heading": heading,
            "velocity": velocity,
            "valid": np.ones(count, dtype=bool),
            "length": constant(EGO_LENGTH_M),
            "width": constant(EGO_WIDTH_M),
            "height": constant(EGO_HEIGHT_M),
        },
        "metadata": {
            "type": "VEHICLE",
            # Must equal the key this track is stored under; `_check_object_state_dict`
            # asserts it, and the traffic manager uses it to know which car not to spawn.
            "object_id": "ego",
            "track_length": count,
        },
    }


def _headings(samples: np.ndarray) -> np.ndarray:
    """Which way the car faces at each sample.

    A stationary car still faces the way it was going. `atan2` over two identical samples
    returns 0 - due east - so a car waiting at a red would swing east, sit there, and swing
    back, which `ReplayEgoCarPolicy` would play back exactly as written. The heading is
    therefore carried across every step short enough to have no direction of its own, which
    also covers the coincident points a join leaves behind.
    """
    direction = np.diff(samples, axis=0)
    heading = np.arctan2(direction[:, 1], direction[:, 0])
    moving = np.linalg.norm(direction, axis=1) > COINCIDENT_M
    if not moving.any():
        return np.zeros(len(samples), dtype=np.float64)
    # Carried forward from the last step that had a direction, and backward for a car that
    # starts at rest and has nothing behind it to inherit from.
    source = np.where(moving, np.arange(len(moving)), -1)
    source = np.maximum.accumulate(source)
    first = int(np.argmax(moving))
    source[source < 0] = first
    heading = heading[source]
    # The last sample has nothing after it to point at, so it keeps the heading it arrived
    # with rather than an invented one.
    return np.concatenate([heading, heading[-1:]])


def _sample_in_time(
    polyline: np.ndarray,
    *,
    cruise_mps: float,
    waits: Sequence[Wait] = (),
    time_step_s: float = TIME_STEP_S,
) -> tuple[np.ndarray, np.ndarray]:
    """Where the car is every `time_step_s`, and how fast it is going, along the whole drive.

    Sampled in *time* rather than at a fixed spacing, which is what makes the speed profile
    visible: a slower stretch simply gets more samples per metre. The last step is dropped
    rather than stretched - the simulator assumes an even interval between samples when it
    differentiates positions, so the alternative is a final step of the wrong length - which
    leaves the recorded car up to one step short of the end of the line.

    This is the only seconds-to-steps conversion in `src/`; everything else that needs a rate
    is handed one.

    A wait is a vertex written twice, at the moment the car arrives and at the moment it
    leaves. Adding the wait to every later time instead would spread the standing still over
    the next quarter-metre of road, which is a crawl rather than a stop.
    """
    dense, travelled, speed = speed_profile(
        polyline, cruise_mps=cruise_mps, stops_at=[wait.at_m for wait in waits]
    )
    times = _arrival_times(travelled, speed)
    if waits:
        dense, speed, times = _with_dwells(dense, speed, times, travelled=travelled, waits=waits)
    duration = float(times[-1])
    if duration <= 0.0:
        return polyline, np.full(len(polyline), cruise_mps, dtype=np.float64)
    wanted = np.arange(int(math.floor(duration / time_step_s)) + 1) * time_step_s
    samples = np.stack(
        [np.interp(wanted, times, dense[:, 0]), np.interp(wanted, times, dense[:, 1])], axis=1
    )
    return samples, np.interp(wanted, times, speed)


def _with_dwells(
    dense: np.ndarray,
    speed: np.ndarray,
    times: np.ndarray,
    *,
    travelled: np.ndarray,
    waits: Sequence[Wait],
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """The same profile with each wait written in as a repeated vertex."""
    held = {int(np.abs(travelled - wait.at_m).argmin()): wait.waited_s for wait in waits}
    positions: list[np.ndarray] = []
    speeds: list[float] = []
    stamps: list[float] = []
    shift = 0.0
    for index in range(len(dense)):
        positions.append(dense[index])
        speeds.append(float(speed[index]))
        stamps.append(float(times[index]) + shift)
        waited = held.get(index)
        if waited:
            shift += waited
            positions.append(dense[index])
            speeds.append(float(speed[index]))
            stamps.append(float(times[index]) + shift)
    return np.array(positions), np.array(speeds), np.array(stamps)


def route_summary(route: Route) -> dict[str, Any]:
    """What `metadata.sdc_route` records: that this was generated, and from what.

    A reader who finds a car in a scenario built from OpenStreetMap should be able to tell
    immediately that nobody drove it.
    """
    return {
        "source": "generated",
        "name": route.name,
        "start_lane": route.start_lane,
        "end_lane": route.end_lane,
        "lanes": list(route.lanes),
        "lane_changes": len(route.lane_changes),
        "junction_movements": len(route.lanes) - 1 - len(route.lane_changes),
        "distance_m": round(route.distance_m, 2),
        "speed_kph": round(route.speed_mps * 3.6, 2),
        # The turns are taken below the cruising speed, so one figure would not describe the
        # drive. A reader comparing `distance_m / speed_kph` against `duration_s` and finding
        # they disagree should be able to see why from the summary itself.
        "slowest_kph": round(route.slowest_mps * 3.6, 2),
        "duration_s": round(route.duration_s, 2),
        # `duration_s` minus the standing still. The route builder page reports neither, since
        # it never sees `signals.json`: what it predicts is the drive with no lights at all,
        # which is shorter than both because the car does not brake for one.
        "driving_duration_s": round(route.driving_duration_s, 2),
        "waiting_s": round(sum(wait.waited_s for wait in route.waits), 2),
        # Every red the recorded car actually stopped for. `offset_seconds` is recorded with
        # it because that is the assumption the wait was computed against: a baked stop
        # matches the tape, and `--lights live` redraws the offset per episode.
        "stops": [
            {
                "lane_id": wait.lane_id,
                "at_m": round(wait.at_m, 2),
                "arrived_s": round(wait.arrived_s, 2),
                "waited_s": round(wait.waited_s, 2),
            }
            for wait in route.waits
        ],
    }
