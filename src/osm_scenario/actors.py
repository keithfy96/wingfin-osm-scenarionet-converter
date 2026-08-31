"""Stage 6 - pedestrians, cyclists and static objects, baked into the dataset as tracks.

    from osm_scenario.actors import read_actor_plan, actor_tracks, crosswalk_features

**Nothing here needs a drive-time counterpart.** `PEDESTRIAN` and `CYCLIST` are first-class
ScenarioNet track types, and `ScenarioTrafficManager.after_reset` branches on `track["type"]`
to call `spawn_pedestrian` / `spawn_cyclist` / `spawn_static_object`, attaching
`ReplayTrafficParticipantPolicy` - which wants no lane, no route and no map feature, only the
track's own state arrays. That manager is registered in every drive already: `no_traffic`
defaults False and `tools/drive.py` never sets it. It has simply had nothing but the ego to
spawn. So an actor in the pickle is an actor in the simulator, and `tools/` is untouched.

**The paths cannot come from the source.** Stage 1 drops footways (`road_exclusion_reason`),
and the extracts are bare regardless - across `junction-1` and `mosque` together the source OSM
holds four `highway=footway` ways, one `steps`, two `path`, and **not one `highway=crossing`
node or `crossing=*` tag**. There is no surveyed pedestrian network to convert and no surveyed
crossing to place a zebra on. So a path is drawn by a person, in the Stage 6 actor builder, and
arrives here as `actors.json` - the same arrangement, and for the same reason, as `routes.json`
and `signals.json`.

**Coordinates, not lane ids.** Routes and signal plans name lanes, which are content addressed;
an actor path cannot, because it runs where no lane is. It is therefore geometry - and the file
carries it as `[lat, lon]`, the order every Stage 6 page already speaks, because the page that
draws it is a Leaflet map and the browser has no projection. Projecting into the model's own
metric CRS happens **here**, with the WKT the lane model carries, so the exchange file and the
pickle can never disagree about which of the pair is which.

Two consequences. `metadata.old_origin_in_current_coordinate` must **not** be applied: that
shift is the drive-time correction `tools/` makes to files read *beside* a pickle, and these go
*inside* one, through the same path as the ego track. And a swapped pair cannot be caught by
range alone - `junction-1` sits at lat 3.18, lon 101.6, and 3.18 is a perfectly good longitude -
so every projected point is checked against the map's own extent instead.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from pyproj import Transformer
from shapely.geometry import LineString, Polygon
from shapely.ops import unary_union

from osm_scenario.ego_route import TIME_STEP_S, _densify, _headings, _with_dwells
from osm_scenario.lane_model import PreliminaryLaneModel

# The version of `actors.json` this converter reads. The actor builder writes the same
# constant, so a page and a CLI that have drifted apart say so instead of half working.
ACTORS_VERSION = 1

# Matches `_ROUTE_NAME` and `_GROUP_NAME` so a person does not have to remember three rules
# for three files in the same stage. An actor name is a track key, and a track key reaches
# MetaDrive's logs.
_ACTOR_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,39}")

# `MetaDriveType`. Spelled out rather than imported, the arrangement `signal_plan` and
# `conversion` already use: MetaDrive is deliberately not a dependency of this package, and
# `test_actors` pins these against the real enum wherever a checkout exists.
PEDESTRIAN = "PEDESTRIAN"
CYCLIST = "CYCLIST"
TRAFFIC_CONE = "TRAFFIC_CONE"
TRAFFIC_BARRIER = "TRAFFIC_BARRIER"

CROSSWALK = "CROSSWALK"


@dataclass(frozen=True)
class _Body:
    """What MetaDrive will build, so the track can describe the same thing.

    Read off the classes rather than chosen: `Pedestrian.RADIUS` is 0.35 and `HEIGHT` 1.75;
    `Cyclist.DEFAULT_LENGTH/WIDTH/HEIGHT` are 1.75, 0.40 and 1.75. The numbers matter because
    `spawn_pedestrian` and `spawn_cyclist` read `state["width"]` **unconditionally**
    (`scenario_traffic_manager.py:253` and `:280`), so they are not optional here however
    optional `parse_object_state` says they are - an omitted array is a bare `KeyError` at
    reset rather than a warning.
    """

    type_name: str
    length_m: float
    width_m: float
    height_m: float
    moving: bool


BODIES: Mapping[str, _Body] = {
    "pedestrian": _Body(PEDESTRIAN, 0.70, 0.70, 1.75, moving=True),
    "cyclist": _Body(CYCLIST, 1.75, 0.40, 1.75, moving=True),
    "cone": _Body(TRAFFIC_CONE, 0.0, 0.0, 0.0, moving=False),
    "barrier": _Body(TRAFFIC_BARRIER, 0.0, 0.0, 0.0, moving=False),
}

# Below this a crossing is a clipped corner rather than a way across, and painting it puts a
# stub of zebra in the mouth of a junction. Junction turn polygons overlap the road heavily,
# so a path that merely clips one produces exactly that.
MIN_CROSSING_M = 1.5

# A path shorter than this cannot be walked in any meaningful sense, and `_densify` has
# nothing to interpolate between.
MIN_PATH_M = 0.5

# How far outside the mapped lanes an actor may be placed before it is refused. A pedestrian
# on the pavement is outside every lane by a few metres and must be allowed; one 200 m clear
# of the whole network is a mistake - most likely a `[lat, lon]` written `[lon, lat]`, which
# no range check can see at these latitudes.
EXTENT_MARGIN_M = 200.0


class ActorPlanError(RuntimeError):
    """Raised when an actor plan cannot be read or does not fit this map."""


@dataclass(frozen=True)
class ActorWait:
    """A pause on the way across - at a kerb, or in a refuge.

    Field names match `ego_route.Wait`'s two load-bearing ones so `_with_dwells` takes this
    straight, rather than there being a second implementation of writing a wait in as a
    repeated vertex.
    """

    at_m: float
    waited_s: float


@dataclass(frozen=True)
class Actor:
    """One drawn actor. `path` for the moving kinds, `position` for the static ones.

    Both hold **projected metres in the model's own CRS**, not the `[lat, lon]` the file
    carries: `read_actor_plan` projects on the way in, so nothing downstream of it has to
    know which frame it is looking at.
    """

    name: str
    kind: str
    path: tuple[tuple[float, float], ...] = ()
    speed_mps: float = 0.0
    start_delay_s: float = 0.0
    waits: tuple[ActorWait, ...] = ()
    crossing_width_m: float | None = None
    position: tuple[float, float] | None = None
    heading_rad: float = 0.0

    @property
    def body(self) -> _Body:
        return BODIES[self.kind]


@dataclass(frozen=True)
class ActorPlan:
    """Everything drawn on one map."""

    actors: tuple[Actor, ...]

    @property
    def crossings(self) -> tuple[Actor, ...]:
        return tuple(actor for actor in self.actors if actor.crossing_width_m is not None)


def _number(value: Any, *, label: str, minimum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ActorPlanError(f"{label} must be a number, not {value!r}")
    number = float(value)
    if not math.isfinite(number):
        raise ActorPlanError(f"{label} must be finite, not {number!r}")
    if minimum is not None and number < minimum:
        raise ActorPlanError(f"{label} must be at least {minimum:g}, not {number:g}")
    return number


@dataclass(frozen=True)
class _Frame:
    """WGS84 to the model's own metric CRS, and where the map is in it.

    One object rather than a transformer and a bounding box passed separately, because the
    two are only meaningful together: the extent is in projected metres and checking a point
    against it is the step straight after projecting it.
    """

    transformer: Transformer
    min_x: float
    min_y: float
    max_x: float
    max_y: float

    def project(self, latitude: float, longitude: float, *, label: str) -> tuple[float, float]:
        x, y = self.transformer.transform(longitude, latitude)
        if not (math.isfinite(x) and math.isfinite(y)):
            raise ActorPlanError(f"{label} does not project into this map's coordinate system")
        if not (
            self.min_x - EXTENT_MARGIN_M <= x <= self.max_x + EXTENT_MARGIN_M
            and self.min_y - EXTENT_MARGIN_M <= y <= self.max_y + EXTENT_MARGIN_M
        ):
            raise ActorPlanError(
                f"{label} at {latitude:.6f}, {longitude:.6f} lands more than "
                f"{EXTENT_MARGIN_M:g} m outside this map. A point is [lat, lon]; written the "
                "other way round it still reads as a valid pair at these latitudes, so check "
                "the order before anything else"
            )
        return float(x), float(y)


def _from_model(model: PreliminaryLaneModel) -> _Frame:
    points = [point for lane in model.lanes for point in lane.centerline]
    if not points:
        raise ActorPlanError("this lane model has no lanes, so there is nowhere to place an actor")
    xs = [point.x for point in points]
    ys = [point.y for point in points]
    return _Frame(
        transformer=Transformer.from_crs(
            "EPSG:4326", model.metadata.coordinate_system_wkt, always_xy=True
        ),
        min_x=min(xs),
        min_y=min(ys),
        max_x=max(xs),
        max_y=max(ys),
    )


def _point(value: Any, *, label: str, frame: _Frame) -> tuple[float, float]:
    """One `[lat, lon]` from the file, as metres in the model's frame."""
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ActorPlanError(f"{label} must be a two-element [lat, lon], not {value!r}")
    latitude = _number(value[0], label=f"{label} latitude")
    longitude = _number(value[1], label=f"{label} longitude")
    if not -90.0 <= latitude <= 90.0:
        raise ActorPlanError(f"{label} latitude {latitude:g} is not a latitude")
    if not -180.0 <= longitude <= 180.0:
        raise ActorPlanError(f"{label} longitude {longitude:g} is not a longitude")
    return frame.project(latitude, longitude, label=label)


def _read_actor(entry: Any, *, position: int, frame: _Frame) -> Actor:
    """One entry, refused rather than defaulted when it does not fit its own kind."""
    where = f"actor {position}"
    if not isinstance(entry, dict):
        raise ActorPlanError(f"{where} is not an object")

    name = entry.get("name")
    if not isinstance(name, str) or not _ACTOR_NAME.fullmatch(name):
        raise ActorPlanError(
            f"{where} has the name {name!r}; a name is 1-40 characters of letters, digits "
            "and hyphens, starting with a letter or digit"
        )
    # The ego track's key. Two tracks under one key is one track, and the one that survives
    # would be whichever was written second.
    if name == "ego":
        raise ActorPlanError(
            "'ego' is the recorded car's own track key; name the actor something else"
        )

    kind = entry.get("kind")
    if kind not in BODIES:
        raise ActorPlanError(
            f"{where} ({name}) has kind {kind!r}; expected one of {', '.join(sorted(BODIES))}"
        )
    body = BODIES[kind]

    crossing = entry.get("crossing_width_m")
    crossing_width = (
        None
        if crossing is None
        else _number(crossing, label=f"{where} crossing_width_m", minimum=0.5)
    )

    if not body.moving:
        for unwanted in ("path", "speed_mps", "waits", "crossing_width_m"):
            if entry.get(unwanted) is not None:
                raise ActorPlanError(
                    f"{where} ({name}) is a {kind}, which does not move, so it has no "
                    f"{unwanted}. Give it a position instead"
                )
        if entry.get("position") is None:
            raise ActorPlanError(f"{where} ({name}) is a {kind} and needs a position")
        return Actor(
            name=name,
            kind=kind,
            position=_point(entry["position"], label=f"{where} position", frame=frame),
            heading_rad=_number(entry.get("heading_rad", 0.0), label=f"{where} heading_rad"),
        )

    if entry.get("position") is not None:
        raise ActorPlanError(
            f"{where} ({name}) is a {kind}, which moves, so it takes a path rather than a position"
        )
    raw_path = entry.get("path")
    if not isinstance(raw_path, list) or len(raw_path) < 2:
        raise ActorPlanError(f"{where} ({name}) needs a path of at least two points")
    path = tuple(
        _point(point, label=f"{where} path[{index}]", frame=frame)
        for index, point in enumerate(raw_path)
    )
    length = float(np.linalg.norm(np.diff(np.asarray(path), axis=0), axis=1).sum())
    if length < MIN_PATH_M:
        raise ActorPlanError(
            f"{where} ({name}) has a path {length:.2f} m long, below the {MIN_PATH_M:g} m "
            "minimum; it would stand still rather than walk"
        )

    speed = _number(entry.get("speed_mps"), label=f"{where} speed_mps", minimum=0.05)
    delay = _number(entry.get("start_delay_s", 0.0), label=f"{where} start_delay_s", minimum=0.0)

    raw_waits = entry.get("waits") or []
    if not isinstance(raw_waits, list):
        raise ActorPlanError(f"{where} ({name}) has a waits that is not a list")
    waits: list[ActorWait] = []
    for index, raw_wait in enumerate(raw_waits):
        if not isinstance(raw_wait, dict):
            raise ActorPlanError(f"{where} ({name}) wait {index} is not an object")
        at_m = _number(raw_wait.get("at_m"), label=f"{where} wait {index} at_m", minimum=0.0)
        if at_m > length:
            raise ActorPlanError(
                f"{where} ({name}) waits at {at_m:.2f} m, past the end of its "
                f"{length:.2f} m path"
            )
        waits.append(
            ActorWait(
                at_m=at_m,
                waited_s=_number(
                    raw_wait.get("seconds"), label=f"{where} wait {index} seconds", minimum=0.0
                ),
            )
        )

    return Actor(
        name=name,
        kind=kind,
        path=path,
        speed_mps=speed,
        start_delay_s=delay,
        waits=tuple(waits),
        crossing_width_m=crossing_width,
    )


def read_actor_plan(
    raw: Any, *, model: PreliminaryLaneModel, model_sha256: str, source: Path | str
) -> ActorPlan:
    """The actors drawn in the builder, refused unless they were drawn on this map.

    The identity block does the job it does for `routes.json` and `signals.json`, and here it
    is the *only* check available: an actor path is metres rather than lane ids, so a plan
    drawn on one generation and applied to another does not name anything that could be found
    missing. It simply puts a pedestrian somewhere else - most likely in a building, possibly
    in a live carriageway. Nothing downstream would notice.
    """
    if not isinstance(raw, dict):
        raise ActorPlanError(f"{source} is not an actor plan")
    version = raw.get("actors_version")
    if version != ACTORS_VERSION:
        raise ActorPlanError(
            f"unsupported actors_version {version!r}; this converter writes and reads "
            f"{ACTORS_VERSION}"
        )

    identity = raw.get("identity")
    if not isinstance(identity, dict):
        raise ActorPlanError(f"{source} has no identity block, so it cannot be checked")
    expected = {
        "generation_fingerprint": model.metadata.generation_fingerprint,
        "reviewed_lane_model_sha256": model_sha256,
    }
    for key, value in expected.items():
        if identity.get(key) != value:
            raise ActorPlanError(
                f"{source} was drawn on a different lane model ({key} does not match). "
                "Re-open the actor builder from this workspace and place the actors again"
            )

    entries = raw.get("actors")
    if not isinstance(entries, list) or not entries:
        raise ActorPlanError(f"{source} contains no actors")

    frame = _from_model(model)

    actors: list[Actor] = []
    names: set[str] = set()
    for position, entry in enumerate(entries):
        actor = _read_actor(entry, position=position, frame=frame)
        if actor.name in names:
            raise ActorPlanError(f"{source} uses the actor name {actor.name!r} twice")
        names.add(actor.name)
        actors.append(actor)

    return ActorPlan(actors=tuple(actors))


def _walk(actor: Actor, *, time_step_s: float) -> tuple[np.ndarray, np.ndarray]:
    """Where a moving actor is every `time_step_s`, and which way it faces.

    Constant speed, deliberately - **not** `ego_route._sample_in_time`. That routes through
    `speed_profile`, which caps speed by curvature against `LATERAL_ACCEL_MPS2`, a figure
    pinned to the recorded car's own 30-degree-per-step gate. A pedestrian rounding a kerb has
    no such limit and slowing one by car physics would be a fabrication. The waits are shared,
    though: `_with_dwells` writes a pause in as a repeated vertex, which is the same thing a
    person standing at a kerb does, and a second implementation of it would be a second clock.
    """
    dense, travelled = _densify(np.asarray(actor.path, dtype=np.float64), spacing=0.1)
    speed = np.full(len(dense), actor.speed_mps, dtype=np.float64)
    times = travelled / actor.speed_mps
    if actor.waits:
        dense, speed, times = _with_dwells(
            dense, speed, times, travelled=travelled, waits=actor.waits
        )
    duration = float(times[-1])
    wanted = np.arange(int(math.floor(duration / time_step_s)) + 1) * time_step_s
    samples = np.stack(
        [np.interp(wanted, times, dense[:, 0]), np.interp(wanted, times, dense[:, 1])], axis=1
    )
    return samples, _headings(samples)


def _moving_track(actor: Actor, *, steps: int, time_step_s: float) -> dict[str, Any] | None:
    """The track for one walker or rider, or None when it never appears in this scenario.

    An actor is present for exactly the window it is walking in: invalid before
    `start_delay_s`, invalid again once it has arrived. Holding it at the far kerb for the
    rest of the episode would leave a pedestrian standing in the scene for reasons the plan
    never stated, and MetaDrive despawns it on the first invalid frame anyway.

    Every array is zeroed where `valid` is False. `sanity_check` only enforces that under
    `valid_check=True`, which nothing here passes - but a non-zero position on a frame the
    actor does not exist on is a claim about where it is, and it costs nothing not to make it.
    """
    samples, heading = _walk(actor, time_step_s=time_step_s)
    start = int(round(actor.start_delay_s / time_step_s))
    if start >= steps:
        return None
    written = min(len(samples), steps - start)
    stop = start + written

    position = np.zeros((steps, 3), dtype=np.float64)
    headings = np.zeros(steps, dtype=np.float64)
    velocity = np.zeros((steps, 2), dtype=np.float64)
    valid = np.zeros(steps, dtype=bool)

    position[start:stop, :2] = samples[:written]
    headings[start:stop] = heading[:written]
    valid[start:stop] = True
    # From the recorded positions rather than from `speed_mps`, so a step spent standing in a
    # wait reads as stationary - which is what `ReplayTrafficParticipantPolicy.act` hands to
    # `set_velocity`, and what the animation picks its walk cycle from.
    if written >= 2:
        velocity[start : stop - 1] = np.diff(samples[:written], axis=0) / time_step_s
        velocity[stop - 1] = velocity[stop - 2]

    body = actor.body

    def constant(value: float) -> np.ndarray:
        array = np.zeros(steps, dtype=np.float64)
        array[start:stop] = value
        return array

    return {
        "type": body.type_name,
        "state": {
            "position": position,
            "heading": headings,
            "velocity": velocity,
            "valid": valid,
            "length": constant(body.length_m),
            "width": constant(body.width_m),
            "height": constant(body.height_m),
        },
        "metadata": {
            "type": body.type_name,
            # Must equal the key this track is stored under; `_check_object_state_dict`
            # asserts it.
            "object_id": actor.name,
            "track_length": steps,
        },
    }


def _static_track(actor: Actor, *, steps: int) -> dict[str, Any]:
    """A cone or a barrier: one place, every frame, valid throughout.

    Valid throughout is not tidiness. `spawn_static_object` counts the valid frames and
    discards anything under `MIN_VALID_FRAME_LEN = 20` as sensor noise, silently - a habit
    inherited from the real datasets this format came from. A short-lived static object is
    therefore not a short-lived static object, it is no object at all.

    No `length` / `width` / `height`: `spawn_static_object` reads position and heading and
    nothing else, so writing sizes here would be a claim about a body MetaDrive builds to its
    own dimensions regardless.
    """
    x, y = actor.position or (0.0, 0.0)
    position = np.zeros((steps, 3), dtype=np.float64)
    position[:, 0] = x
    position[:, 1] = y
    return {
        "type": actor.body.type_name,
        "state": {
            "position": position,
            "heading": np.full(steps, actor.heading_rad, dtype=np.float64),
            "velocity": np.zeros((steps, 2), dtype=np.float64),
            "valid": np.ones(steps, dtype=bool),
        },
        "metadata": {
            "type": actor.body.type_name,
            "object_id": actor.name,
            "track_length": steps,
        },
    }


def actor_tracks(
    plan: ActorPlan, *, steps: int, time_step_s: float = TIME_STEP_S
) -> dict[str, dict[str, Any]]:
    """The tracks to write beside the ego's, keyed by actor name.

    `steps` is this scenario's length, and every scenario in the dataset is a different route
    with a different one - so the same plan yields different tracks per scenario, and an actor
    whose walk starts after a short route has ended is left out of that route entirely rather
    than written with no valid frame.
    """
    tracks: dict[str, dict[str, Any]] = {}
    for actor in plan.actors:
        track = (
            _moving_track(actor, steps=steps, time_step_s=time_step_s)
            if actor.body.moving
            else _static_track(actor, steps=steps)
        )
        if track is not None:
            tracks[actor.name] = track
    return tracks


def _rectangle(start: np.ndarray, end: np.ndarray, width_m: float) -> np.ndarray:
    """The four corners of a `width_m` band laid along the chord from `start` to `end`.

    A chord rather than a buffer of the path itself. `LineString.buffer` of a path that bends
    inside the carriageway returns a rounded or many-sided polygon, and MetaDrive takes the
    stripe angle from `find_longest_edge` of whatever it is given - so a polygon with more than
    four edges paints at an angle nobody chose. A crossing is straight; drawn as a chord it is
    a quadrilateral by construction.
    """
    direction = end - start
    length = float(math.hypot(direction[0], direction[1]))
    along = direction / length
    across = np.array([-along[1], along[0]]) * (width_m / 2.0)
    return np.array(
        [start - across, end - across, end + across, start + across], dtype=np.float64
    )


def crosswalk_features(
    plan: ActorPlan, *, lane_polygons: Mapping[str, Sequence[Sequence[float]]]
) -> dict[str, dict[str, Any]]:
    """`CROSSWALK` map features for the runs of a drawn path that cross drivable road.

    Painted only where an actor asked for it with `crossing_width_m`, and only for the part of
    its path actually on the carriageway - so the pavement approach is bare and a path drawn
    without the field paints nothing at all. Given that the sources carry no surveyed crossing,
    inventing a zebra for every walker would be inventing infrastructure; asking for one per
    actor keeps it a decision somebody made.

    A crosswalk is paint and a semantic label and nothing else. `base_block._construct_crosswalk`
    builds a **ghost** body and drops the visual node, `collision_callback` skips CROSSWALK
    explicitly, and no policy in `metadrive/policy/` yields at one. It is deliberately not a
    breach of the "nothing painted on drivable road" rule: that rule is about lines whose ghost
    bodies set `on_white_continuous_line` and so read to an agent as a road boundary.
    """
    crossings = plan.crossings
    if not crossings:
        return {}
    surfaces = [
        Polygon(np.asarray(polygon, dtype=np.float64)[:, :2]).buffer(0)
        for polygon in lane_polygons.values()
        if len(polygon) >= 4
    ]
    if not surfaces:
        return {}
    carriageway = unary_union(surfaces)

    features: dict[str, dict[str, Any]] = {}
    for actor in crossings:
        width = float(actor.crossing_width_m or 0.0)
        inside = LineString(actor.path).intersection(carriageway)
        parts = list(getattr(inside, "geoms", [inside]))
        index = 0
        for part in parts:
            coords = list(getattr(part, "coords", []))
            if len(coords) < 2:
                continue
            start = np.asarray(coords[0][:2], dtype=np.float64)
            end = np.asarray(coords[-1][:2], dtype=np.float64)
            if float(np.linalg.norm(end - start)) < MIN_CROSSING_M:
                continue
            features[f"crosswalk-{actor.name}-{index}"] = {
                "type": CROSSWALK,
                "polygon": _rectangle(start, end, width),
            }
            index += 1
    return features
