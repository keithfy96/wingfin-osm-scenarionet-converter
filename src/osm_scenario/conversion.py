"""Stage 6 - convert the validated map into a map-only ScenarioNet dataset.

Reads `lane-model/reviewed.json` and writes three pickles. It never writes back into the
lane model, so a bad conversion costs nothing but the output directory.

Two things about this stage are worth knowing before reading the code.

* **`entry_lanes` / `exit_lanes` hold two kinds of id.** A continuation - the same road
  carrying on - is recorded as the next lane's id. A junction movement is recorded as a
  *connector* id, and the lane on the other side is inside that connector. ScenarioNet
  wants lane ids and nothing else, so every connector reference is swapped for the lane it
  leads to. In `junction-1` that is 422 lane ids left alone and 166 connector ids
  swapped - exactly twice the 83 active connectors, which is the arithmetic that says the
  swap is complete.

* **A lane change is a way to get somewhere.** `entry_lanes` / `exit_lanes` say where a
  lane physically leads, and a lane change is not that - so those two lists are untouched by
  it. But the *reachability* figures in `metadata.routing` count moving across into a
  side-by-side lane, because OSM says a change is permitted unless `change` / `change:lanes`
  forbids it and `junction-1` carries no such tag. Counting junction movements alone said
  the best lane reached 79 of 285; counting what the source actually permits says 190.

* **The lane lines are that same decision, drawn.** A boundary a lane change crosses is
  written broken and every other boundary stays a road edge, so the road cannot show a solid
  line across a movement `metadata.routing.lane_change_edges` advertises. MetaDrive names the
  line's ghost body after its type, so this decides whether crossing sets
  `on_broken_line` or `on_white_continuous_line` - it is not a question of appearance. See
  `_divider_boundaries`.

* **ScenarioNet is deliberately not a dependency, but the schema is still pinned.** The
  scenario dict is built by hand against MetaDrive's `ScenarioDescription`.
  `test_the_scenario_passes_metadrives_own_sanity_check` runs MetaDrive's real
  `sanity_check` against a converted scenario by loading its schema module directly from a
  checkout - no install, no panda3d - and skips where no checkout exists. So the field
  names below are measured against MetaDrive 0.4.3 rather than assumed, and a schema change
  fails a test rather than surfacing as a load error hours later.
"""

from __future__ import annotations

import io
import json
import math
import os
import pickle
import re
import statistics
import tempfile
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import networkx as nx
import numpy as np
from shapely.geometry import LineString, MultiPolygon

# Private helpers imported across modules on purpose, for the reason `validation` gives:
# these are the exact routines the earlier stages use, and a Stage 6 copy would be a
# second implementation to keep in step. `_sha256` must match what Stage 5 wrote into the
# manifest or every run reports a stale checksum.
from osm_scenario.apply_review import ApplyReviewError, _read_json, _sha256
from osm_scenario.config import ConverterConfig
from osm_scenario.ego_route import (
    TIME_STEP_S,
    Route,
    RouteError,
    SignalTiming,
    ego_track,
    plan_route,
    route_polyline,
    route_summary,
)
from osm_scenario.ids import deterministic_id
from osm_scenario.lane_model import ConnectorFeature, LaneFeature, PreliminaryLaneModel
from osm_scenario.reachability_view import render_reachability_html
from osm_scenario.route_builder_view import render_route_builder_html
from osm_scenario.signal_builder_view import render_signal_builder_html
from osm_scenario.signal_plan import (
    SIGNALS_VERSION,
    SignalPlan,
    SignalPlanError,
    light_states,
    plan_metadata,
    read_signal_plan,
    stop_points,
)
from osm_scenario.stage1b_data_audit import _write_text_atomic
from osm_scenario.topology import connector_curve

REPORT_VERSION = 1

# The version of `routes.json` this converter reads. The route builder writes the same
# constant, so a page and a CLI that have drifted apart say so instead of half working.
ROUTES_VERSION = 1

# Route names reach the scenario filename, which MetaDrive keys the whole dataset on and
# accepts only when it starts `sd_`. Anything a filesystem or a URL would treat specially has
# no business in it.
_ROUTE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,39}")

# The track key, the `object_id` inside it and `metadata.sdc_id` must all agree; MetaDrive
# asserts the first two match and looks the car up by the third.
_EGO_ID = "ego"

SUMMARY_FILE = "dataset_summary.pkl"
MAPPING_FILE = "dataset_mapping.pkl"

# Protocol 4 rather than the interpreter default. The dataset is meant to be handed to a
# separate, lockfile-pinned ScenarioNet environment whose Python is chosen by MetaDrive's
# constraints, not ours; 4 is readable everywhere that matters and costs nothing here.
_PICKLE_PROTOCOL = 4


class _PortablePickler(pickle.Pickler):
    """Pickle arrays so an older numpy can read them.

    The same argument as the protocol above, carried through to the payload. numpy 2 pickles
    an array as a reference to `numpy._core`, a module that does not exist in numpy 1 - so a
    dataset written here fails to *open* in the environment it is written for, with
    `ModuleNotFoundError` rather than anything that names the real problem. Both of the
    MetaDrive checkouts this repo targets run Python 3.8 and numpy 1.24, and 3.8 cannot have
    numpy 2 at all, so this is not a version skew that waits itself out.

    `np.array` and `dtype.str` exist unchanged in both major versions, so rebuilding through
    them makes the stream carry no version-specific name. What comes back is a real
    `ndarray` with the original dtype and shape, not a list - which matters, because
    MetaDrive indexes these with tuples (`positions[:, :2]` in `parse_full_trajectory`) and
    a list would fail there instead, further away.

    The cost is that arrays travel as nested lists rather than raw buffers. For `junction-1`
    that is 1141 arrays holding 50 KB, so the file grows by a few hundred KB - cheap enough
    not to trade against being loadable.
    """

    def reducer_override(self, obj: Any) -> Any:
        if isinstance(obj, np.ndarray):
            return np.array, (obj.tolist(), obj.dtype.str)
        return NotImplemented


def _portable_pickle(payload: Any) -> bytes:
    """`pickle.dumps`, minus the numpy version dependence. See `_PortablePickler`."""
    buffer = io.BytesIO()
    _PortablePickler(buffer, protocol=_PICKLE_PROTOCOL).dump(payload)
    return buffer.getvalue()


_LANE_TYPE = "LANE_SURFACE_STREET"

# The kerb, or the centreline: the edge of what a car may move into sideways. `unknown` is the
# `boundary_type` on every boundary in the lane model, because the source carries no marking
# survey, so the generic road edge stays the strongest honest claim for these.
_BOUNDARY_TYPE = "ROAD_EDGE_BOUNDARY"

# The line between two side-by-side lanes a car may move between. Not a cosmetic choice:
# `BaseBlock._construct_lane_line_segment` names the ghost body after the line type, so on
# contact `base_vehicle` sets `on_white_continuous_line` for a solid line and `on_broken_line`
# for a broken one - and `ScenarioEnv._is_out_of_road` reads the first. Writing every divider
# solid told the simulator that each of the lane changes `metadata.routing.lane_change_edges`
# advertises is a violation. See `_divider_boundaries` for how one is picked out.
_DIVIDER_TYPE = "ROAD_LINE_BROKEN_SINGLE_WHITE"

# Two lanes either side of a divider each carry their own copy of it. This is the distance
# below which those two copies are one line rather than two - the same threshold the rest of
# the pipeline uses for "these two pieces of geometry are the same place".
_SAME_LINE_M = 0.05

# A junction turn carries no surveyed speed limit - it is not a way and has no `maxspeed` - and
# the lanes either side of it may disagree. 30 km/h is the figure MetaDrive's own IDM would end
# up at through a turn of this radius anyway, and `ScenarioLane` only reads it to cap speed.
_CONNECTOR_SPEED_KPH = 30.0

# How far apart two consecutive features may be and still count as meeting. Waymo's own data
# joins lane to lane at exactly 0.000 m, and MetaDrive builds the road surface per feature, so
# anything above this is a hole in the road rather than rounding.
_JOIN_TOLERANCE_M = 0.05

# The smallest gap worth spanning with a junction lane of its own. Below this there is no room
# for a curve that can also match the tangents at both ends - the handles are a third of the
# chord, so a 0.1 m gap between lanes pointing different ways produces a cusp rather than a
# join. A seam this size is invisible to MetaDrive anyway: it builds each lane's surface
# separately and they are metres wide, so the polygons still abut.
_BRIDGE_MIN_GAP_M = 0.25

# How much chord a bridge needs per radian of turn. A curve asked to swing 140 degrees across
# 0.25 m cannot meet both tangents and comes out as a cusp - a spike in the road surface, which
# is worse than the kink it was meant to remove. Below this the movement is left as a direct
# join rather than papered over with geometry that is wrong in a new way.
_BRIDGE_CHORD_PER_RADIAN_M = 0.5

_FORMAT_VERSION = "1.0"

_DATASET_NAME = "osm-scenario"
_DATASET_VERSION = "v1"

# MetaDrive's coordinate frame: right-handed, metres. Nothing in MetaDrive 0.4.3 branches on
# this value - the only assignment is in its own `scenario/utils.py` - so it is a label, not
# a transform. `metadrive_processed` stays False because this dataset came from a converter,
# which is what every ScenarioNet converter records.
_COORDINATE = "metadrive"


def scenario_file_name(scenario_id: str) -> str:
    """The one filename the dataset is keyed on, in the form MetaDrive insists on.

    `ScenarioDescription.is_scenario_file` accepts a name only if it starts with `sd_` or is
    entirely digits, and `read_dataset_summary` asserts it for every entry in the summary.
    So a friendly name like `scenario.pkl` loads nowhere. Built the way MetaDrive's own
    `get_export_file_name` builds it, and derived rather than constant so the summary and
    the mapping cannot key on different strings.
    """
    return f"sd_{_DATASET_NAME}_{_DATASET_VERSION}_{scenario_id}.pkl"


class ConversionError(RuntimeError):
    """Raised when the validated map cannot be converted."""


def _read(path: Path, label: str) -> Any:
    """`_read_json`, with its failures wearing this stage's name.

    The reader is shared on purpose - one implementation of "missing or malformed JSON" -
    but it raises `ApplyReviewError`, which the Stage 6 CLI has no reason to catch. Without
    this, pointing `convert` at a workspace with no manifest prints a traceback instead of
    a sentence.
    """
    try:
        return _read_json(path, label)
    except ApplyReviewError as error:
        raise ConversionError(str(error)) from error


def _write_bytes_atomic(path: Path, payload: bytes) -> None:
    """Write a pickle in one step, or not at all.

    The text twin of this lives in `stage1b_data_audit`. A half-written pickle is worse
    than a missing one: it can still unpickle far enough to look like a scenario.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _check_stage_5(workspace: Path, model_path: Path) -> dict[str, Any]:
    """The model on disk is the one Stage 5 passed, unchanged since.

    Checked before anything is read out of the model, because converting a map that was
    edited after validation ships geometry nobody checked.
    """
    manifest = _read(workspace / "source" / "manifest.json", "Stage 1 manifest")
    stage_5 = manifest.get("stage_5")
    if not isinstance(stage_5, dict) or stage_5.get("status") != "passed":
        raise ConversionError("Stage 5 has not passed; run validate-map first")
    if not model_path.is_file():
        raise ConversionError(f"reviewed lane model not found: {model_path}")

    recorded = stage_5.get("validated_lane_model", {}).get("sha256")
    actual = _sha256(model_path)
    if recorded != actual:
        raise ConversionError(
            "reviewed lane model checksum does not match the Stage 5 manifest: the model "
            "changed after it was validated"
        )
    return manifest


def _lane_neighbours(model: PreliminaryLaneModel) -> dict[str, tuple[list[str], list[str]]]:
    """Each lane's entries and exits, as lane ids only.

    A connector reference is replaced by the lane on the far side of it. A connector that
    is not `active` is dropped rather than followed: a movement the review forbade must
    not reappear as a drivable edge just because something still names it. `junction-1`
    references none today, which is exactly when a guard is cheap to add.
    """
    lanes = {lane.identifier: lane for lane in model.lanes}
    connectors = {item.identifier: item for item in model.connectors}
    resolved: dict[str, tuple[list[str], list[str]]] = {}

    for lane in model.lanes:
        sides: list[list[str]] = []
        for references, entering in ((lane.entry_lanes, True), (lane.exit_lanes, False)):
            out: list[str] = []
            for reference in references:
                if reference in lanes:
                    out.append(reference)
                    continue
                connector = connectors.get(reference)
                if connector is None:
                    raise ConversionError(
                        f"lane {lane.identifier} names {reference} as "
                        f"{'an entry' if entering else 'an exit'}, but it is neither a "
                        "lane nor a connector in this model"
                    )
                if connector.status != "active":
                    continue
                near = connector.to_lane_id if entering else connector.from_lane_id
                far = connector.from_lane_id if entering else connector.to_lane_id
                if near != lane.identifier:
                    raise ConversionError(
                        f"connector {connector.identifier} is listed on lane "
                        f"{lane.identifier} but joins {connector.from_lane_id} to "
                        f"{connector.to_lane_id}"
                    )
                if far not in lanes:
                    raise ConversionError(
                        f"connector {connector.identifier} leads to {far}, which is not a "
                        "lane in this model"
                    )
                out.append(far)
            # Duplicates are possible where a continuation and a connector name the same
            # lane. Deduplicated in first-seen order so the output is stable without
            # sorting hex ids into an order that means nothing.
            sides.append(list(dict.fromkeys(out)))
        resolved[lane.identifier] = (sides[0], sides[1])

    return resolved


def _lane_change_moves(model: PreliminaryLaneModel) -> dict[str, list[str]]:
    """Each lane's side-by-side neighbours - the lanes a car can move across into.

    OSM spells a lane-change ban with `change` / `change:lanes`, and absence means the
    change is permitted; `junction-1`'s source carries no `change` tag of any kind. So
    treating every side-by-side lane as unreachable is *stricter than the survey*, which
    inverts this project's rule that a surveyed tag outranks an inferred angle.

    Each recorded link becomes a one-way move from the lane that records it. Where the
    model is symmetric - all 178 links in `junction-1` are - that yields both directions
    on its own, and where it is not, the half that is recorded still works rather than
    failing the conversion over bookkeeping.
    """
    lanes = {lane.identifier: lane for lane in model.lanes}
    moves: dict[str, list[str]] = {}

    for lane in model.lanes:
        out: list[str] = []
        for side, neighbour in (("left", lane.left_neighbor), ("right", lane.right_neighbor)):
            if not neighbour:
                continue
            other = lanes.get(neighbour)
            if other is None:
                raise ConversionError(
                    f"lane {lane.identifier} names {neighbour} as its {side} neighbour, "
                    "but it is not a lane in this model"
                )
            # A "neighbour" facing the other way, or on another stretch of road, would put
            # a drivable edge into oncoming traffic or teleport a car down the street. The
            # check costs nothing and the failure it prevents is silent.
            if (
                other.direction != lane.direction
                or other.source_edge != lane.source_edge
                or other.source_way_ids != lane.source_way_ids
            ):
                raise ConversionError(
                    f"lane {lane.identifier} names {neighbour} as its {side} neighbour, "
                    "but they are not the same stretch of road running the same way"
                )
            out.append(neighbour)
        moves[lane.identifier] = list(dict.fromkeys(out))

    return moves


def _reach_facts(graph: nx.DiGraph) -> dict[str, Any]:
    """The six numbers that describe how far a car gets in one graph."""
    reach = {lane_id: len(nx.descendants(graph, lane_id)) for lane_id in graph}
    best = max(reach.items(), key=lambda item: (item[1], item[0]))
    strong = sorted((len(part) for part in nx.strongly_connected_components(graph)), reverse=True)
    return {
        "best_start_lane_id": best[0],
        "best_start_reaches": best[1],
        "median_reach": statistics.median(reach.values()) if reach else 0,
        "lanes_reaching_nothing": sum(1 for count in reach.values() if count == 0),
        "reachable_lane_pairs": sum(reach.values()),
        "possible_lane_pairs": len(graph) * (len(graph) - 1),
        # Summarised, not listed. `junction-1` has 185 of these and most are a single lane,
        # so the full list would be 185 numbers carrying two facts.
        "components_respecting_direction": {
            "count": len(strong),
            "largest": strong[0] if strong else 0,
        },
    }


def _reachability(
    neighbours: Mapping[str, tuple[list[str], list[str]]],
    moves: Mapping[str, list[str]],
) -> dict[str, Any]:
    """Where a car can actually get to, respecting one-way direction.

    Two facts sit behind the numbers, and reporting either one alone misleads.

    Stage 5 reports `routing_components`, which uses *weakly* connected components - it
    ignores direction, so two one-way lanes pointing away from each other still count as
    one piece. That is the right measure for "is this map internally sound", and the wrong
    one for "can a route be driven here". `junction-1` is 6 pieces weakly and 185 strongly.

    And a car can change lanes. Counting only junction movements says `junction-1`'s best
    lane reaches 79 of 285; counting the lane changes the source permits says 190. So the
    headline numbers allow lane changes - that is what a person planning a route needs -
    and `without_lane_changes` keeps the junction-only figures beside them.
    """
    graph = nx.DiGraph()
    graph.add_nodes_from(neighbours)
    for lane_id, (_, exits) in neighbours.items():
        for target in exits:
            graph.add_edge(lane_id, target)

    junction_only = _reach_facts(graph)

    edges = 0
    for lane_id, sideways in moves.items():
        for target in sideways:
            if not graph.has_edge(lane_id, target):
                edges += 1
            graph.add_edge(lane_id, target)

    return {
        **_reach_facts(graph),
        "lane_changes_allowed": True,
        "lane_change_edges": edges,
        "without_lane_changes": junction_only,
    }


def _polyline(points: Any) -> np.ndarray:
    return np.array([[point.x, point.y] for point in points], dtype=np.float64)


def _lane_feature(
    lane: LaneFeature, entries: list[str], exits: list[str]
) -> dict[str, Any]:
    return {
        "type": _LANE_TYPE,
        "polyline": _polyline(lane.centerline),
        "polygon": _polyline(lane.polygon),
        "speed_limit_kmh": lane.speed_limit_kph,
        # MetaDrive's `ScenarioLane` ignores this and uses its own `VIS_LANE_WIDTH`. Kept
        # because it is the surveyed width and the field is the format's own name for it.
        "width": lane.width_m,
        "entry_lanes": entries,
        "exit_lanes": exits,
        # Lists, not bare ids: that is the shape every ScenarioNet converter writes, and an
        # empty list is how "no neighbour" is spelled. MetaDrive 0.4.3 stores these and
        # reads them nowhere - `ScenarioLane.get_lane_width` returns before it reaches the
        # only code that would - so this is convention rather than a load requirement.
        "left_neighbor": [lane.left_neighbor] if lane.left_neighbor else [],
        "right_neighbor": [lane.right_neighbor] if lane.right_neighbor else [],
    }


def _same_line(left: Any, right: Any) -> bool:
    """Whether two boundaries are the same line, drawn from either end."""
    if len(left) != len(right) or not len(left):
        return False
    first = _polyline(left)
    second = _polyline(right)
    apart = min(np.abs(first - second).max(), np.abs(first - second[::-1]).max())
    return bool(apart <= _SAME_LINE_M)


def _divider_boundaries(
    model: PreliminaryLaneModel, moves: Mapping[str, list[str]]
) -> tuple[set[str], set[str]]:
    """Which boundaries a lane change crosses, and which of those are a second copy.

    The line style is not a second opinion about where a lane change is allowed - it is
    `_lane_change_moves` drawn. A boundary is broken exactly where that function records a
    move across it, so a `change` ban honoured there would take the dashes with it rather
    than leave the road contradicting `metadata.routing.lane_change_edges`. Nothing here
    reads geometry to decide: a neighbour is assigned positionally within one direction's
    lane list for one way (`generation.py`), which is why the centreline and the kerb cannot
    come out dashed - opposing directions are never each other's neighbours, and there is no
    lane beyond a kerb to be a neighbour at all.

    Both lanes either side of a divider carry their own copy of it, and in `junction-1` 85 of
    the 89 pairs are the same line to within a centimetre. Left as two features they dash out
    of phase - each is resampled from its own first point - and fill each other's gaps, which
    renders as a solid line and looks exactly like this change having done nothing. The
    remaining 4 pairs stand up to 0.31 m apart, are genuinely two lines, and both are kept.
    """
    lanes = {lane.identifier: lane for lane in model.lanes}
    sides = {
        lane.identifier: {boundary.side: boundary for boundary in lane.boundaries}
        for lane in model.lanes
    }

    dividers: set[str] = set()
    superseded: set[str] = set()
    for lane in model.lanes:
        crossable = set(moves.get(lane.identifier, ()))
        for side, neighbour in (("left", lane.left_neighbor), ("right", lane.right_neighbor)):
            boundary = sides[lane.identifier].get(side)
            if boundary is None or neighbour is None or neighbour not in crossable:
                continue
            dividers.add(boundary.identifier)

            # Which of the neighbour's two boundaries faces back at this lane. Read from the
            # neighbour rather than assumed to be the opposite side, because
            # `_lane_change_moves` tolerates a one-way link and this must not invent the
            # other half of one.
            other = lanes[neighbour]
            facing = next(
                (
                    name
                    for name, back in (
                        ("left", other.left_neighbor),
                        ("right", other.right_neighbor),
                    )
                    if back == lane.identifier
                ),
                None,
            )
            twin = sides[neighbour].get(facing) if facing is not None else None
            if twin is not None and _same_line(boundary.points, twin.points):
                # Drop the later id rather than this lane's, so which copy survives does not
                # depend on the order `model.lanes` happens to be in.
                superseded.add(max(boundary.identifier, twin.identifier))
    return dividers, superseded


def _connector_feature(connector: ConnectorFeature, width: float) -> dict[str, Any]:
    """A junction turn, written as a lane because that is what it is.

    MetaDrive builds its road network from lane features and nothing else:
    `ScenarioBlock._sample_topology` makes a `ScenarioLane` per `is_lane` feature and puts it in
    the graph. A turn that is not a feature is not road - there is no surface over the junction
    to localise on or to paint, and the ego drives across open ground. Waymo's own data does it
    this way; in `metadrive/assets/waymo/` an intersection turn is an ordinary
    `LANE_SURFACE_STREET` whose polyline curves across the box, and one of them turns 181.6
    degrees over 26.6 m with 55 points.

    No `left_neighbor` or `right_neighbor`: two turns crossing the same junction are not lanes of
    one road, and a car may not change between them.
    """
    return {
        "type": _LANE_TYPE,
        "polyline": _polyline(connector.centerline),
        "polygon": _polyline(connector.polygon),
        "speed_limit_kmh": _CONNECTOR_SPEED_KPH,
        "width": width,
        "entry_lanes": [connector.from_lane_id],
        "exit_lanes": [connector.to_lane_id],
        "left_neighbor": [],
        "right_neighbor": [],
    }


def _already_meet(lane: LaneFeature, other: LaneFeature | None, leaving: bool) -> bool:
    """Whether the two lanes touch at the end the movement uses, so nothing spans them."""
    if other is None:
        return False
    end = lane.centerline[-1] if leaving else lane.centerline[0]
    start = other.centerline[0] if leaving else other.centerline[-1]
    return math.hypot(end.x - start.x, end.y - start.y) <= _JOIN_TOLERANCE_M


def _gap_ahead(source: LaneFeature, target: LaneFeature) -> tuple[float, float]:
    """How far ahead the next lane starts, and how far away it is.

    The first is the gap measured *along* the direction the approach is travelling, so an
    overlap comes back negative. The second is the straight-line distance, which cannot.
    """
    a0, a1 = source.centerline[-2], source.centerline[-1]
    span = math.hypot(a1.x - a0.x, a1.y - a0.y)
    if span <= 0:
        return 0.0, 0.0
    heading = ((a1.x - a0.x) / span, (a1.y - a0.y) / span)
    start = target.centerline[0]
    delta = (start.x - a1.x, start.y - a1.y)
    return (
        delta[0] * heading[0] + delta[1] * heading[1],
        math.hypot(delta[0], delta[1]),
    )


def _bend_between(source: LaneFeature, target: LaneFeature) -> float:
    """Radians a car must swing through between the end of one lane and the start of the next."""
    a0, a1 = source.centerline[-2], source.centerline[-1]
    b0, b1 = target.centerline[0], target.centerline[1]
    entry = math.atan2(a1.y - a0.y, a1.x - a0.x)
    exit_ = math.atan2(b1.y - b0.y, b1.x - b0.x)
    return abs(math.atan2(math.sin(exit_ - entry), math.cos(exit_ - entry)))


def _bridge_feature(source: LaneFeature, target: LaneFeature) -> dict[str, Any]:
    """A junction lane for a movement the model records as a plain continuation.

    Not every movement across a junction is a connector. A road running straight through one is
    written as a continuation - lane names lane, no connector between them - because
    topologically nothing happens. Geometrically something does: both lanes are now cut back to
    the edge of the junction, so the two ends no longer meet and MetaDrive would be handed a hole
    exactly where the road goes straight on.

    Built with the same `connector_curve` the turns use, so a straight-through movement and a
    turning one produce the same kind of feature. This lives here rather than in `generation`
    deliberately: making these into model connectors would mint new connector ids, new findings
    and a new review.
    """
    line = connector_curve(
        LineString([(point.x, point.y) for point in source.centerline]),
        LineString([(point.x, point.y) for point in target.centerline]),
        (
            (source.centerline[-1].x + target.centerline[0].x) / 2,
            (source.centerline[-1].y + target.centerline[0].y) / 2,
        ),
    )
    width = min(source.width_m, target.width_m)
    surface = line.buffer(width / 2, cap_style="flat", join_style="round")
    if isinstance(surface, MultiPolygon):
        surface = max(surface.geoms, key=lambda part: part.area)
    return {
        "type": _LANE_TYPE,
        "polyline": np.array(line.coords, dtype=np.float64),
        "polygon": np.array(surface.exterior.coords, dtype=np.float64),
        "speed_limit_kmh": min(source.speed_limit_kph, target.speed_limit_kph),
        "width": width,
        "entry_lanes": [source.identifier],
        "exit_lanes": [target.identifier],
        "left_neighbor": [],
        "right_neighbor": [],
    }


def _exported_links(
    model: PreliminaryLaneModel,
) -> tuple[dict[str, tuple[list[str], list[str]]], dict[str, dict[str, Any]]]:
    """What `entry_lanes` and `exit_lanes` say in the written file, for every feature.

    Deliberately not `_lane_neighbours`, which replaces a connector reference with the lane
    beyond it. That resolution is right for our own reachability and routing, which answer
    questions about roads; it is wrong for the dataset, because it deletes the junction turn on
    the way out and leaves MetaDrive two lanes that do not meet. Here the chain is written in
    full - approach, then the turn, then the exit - which is the shape every ScenarioNet
    converter produces and the shape `ScenarioBlock` expects.

    `_lane_neighbours` is left alone, so the reachability figures, the route search and the
    Stage 6 pages keep reasoning about roads rather than about turns.
    """
    lanes = {lane.identifier for lane in model.lanes}
    by_identifier = {lane.identifier: lane for lane in model.lanes}
    connectors = {item.identifier: item for item in model.connectors}
    links: dict[str, tuple[list[str], list[str]]] = {}
    used: dict[str, ConnectorFeature] = {}
    for lane in model.lanes:
        sides: list[list[str]] = []
        for references in (lane.entry_lanes, lane.exit_lanes):
            out: list[str] = []
            for reference in references:
                if reference in lanes:
                    out.append(reference)
                    continue
                connector = connectors.get(reference)
                if connector is None:
                    raise ConversionError(
                        f"lane {lane.identifier} names {reference}, which is neither a lane "
                        "nor a connector in this model"
                    )
                if connector.status != "active":
                    # A movement the review forbade. Dropped rather than followed, for the same
                    # reason `_lane_neighbours` drops it.
                    continue
                far = (
                    connector.to_lane_id
                    if references is lane.exit_lanes
                    else connector.from_lane_id
                )
                if _already_meet(lane, by_identifier.get(far), references is lane.exit_lanes):
                    # The two lanes touch, so there is no junction to cross and the connector is
                    # only a marker - `connector_curve` returns a stub measured *backwards* along
                    # the approach for exactly this case. Writing that as a feature would open a
                    # 3 m hole where the road is in fact continuous, so the chain names the far
                    # lane directly and no junction lane is emitted.
                    out.append(far)
                    continue
                out.append(reference)
                used[reference] = connector
            sides.append(list(dict.fromkeys(out)))
        links[lane.identifier] = (sides[0], sides[1])

    # Only the connectors a lane actually names. Reached this way rather than by walking
    # `model.connectors`, so this stays exactly as strict as `_lane_neighbours`: a connector
    # nothing references is not part of the network and must not become road.
    for identifier, connector in used.items():
        for role, target_id in (
            ("from", connector.from_lane_id),
            ("to", connector.to_lane_id),
        ):
            if target_id not in lanes:
                raise ConversionError(
                    f"connector {identifier} names {target_id} as its {role} lane, but it is "
                    "not a lane in this model"
                )
        links[identifier] = ([connector.from_lane_id], [connector.to_lane_id])

    # Continuations whose two ends stopped meeting once both were cut back at a junction. Each
    # gets a junction lane of its own, spliced into the chain so the surface is unbroken.
    bridges: dict[str, dict[str, Any]] = {}
    for lane in model.lanes:
        entries, exits = links[lane.identifier]
        rewritten: list[str] = []
        for target_id in exits:
            target = by_identifier.get(target_id)
            if target is None:
                rewritten.append(target_id)
                continue
            forward, chord = _gap_ahead(lane, target)
            # `forward`, not `chord`: trimming the two ends independently can leave the next lane
            # starting slightly *behind* where this one stopped, and the straight-line distance
            # cannot tell that from a real gap. Bridging an overlap asks the curve to go forwards,
            # turn round and come back, which is the cusp this guard exists to prevent.
            if forward < _BRIDGE_MIN_GAP_M or chord < _BRIDGE_CHORD_PER_RADIAN_M * _bend_between(
                lane, target
            ):
                rewritten.append(target_id)
                continue
            bridge_id = deterministic_id("junction-lane", lane.identifier, target_id)
            if bridge_id in links or bridge_id in bridges:
                raise ConversionError(
                    f"the junction lane bridging {lane.identifier} to {target_id} collides "
                    f"with an existing feature id {bridge_id}"
                )
            bridges[bridge_id] = _bridge_feature(lane, target)
            links[bridge_id] = ([lane.identifier], [target_id])
            rewritten.append(bridge_id)
        links[lane.identifier] = (entries, rewritten)

    # The bridged lanes' `entry_lanes` still name the approach directly. Rewrite them to name the
    # bridge, so the chain reads the same in both directions.
    incoming: dict[str, list[str]] = {}
    for feature_id, (_, exits) in links.items():
        for target_id in exits:
            incoming.setdefault(target_id, []).append(feature_id)
    for lane in model.lanes:
        entries, exits = links[lane.identifier]
        arrivals = [item for item in incoming.get(lane.identifier, []) if item in links]
        links[lane.identifier] = (arrivals or entries, exits)
    return links, bridges


def _map_features(
    model: PreliminaryLaneModel,
    neighbours: Mapping[str, tuple[list[str], list[str]]],
    moves: Mapping[str, list[str]],
) -> dict[str, dict[str, Any]]:
    features: dict[str, dict[str, Any]] = {}
    # `neighbours` is still accepted, and still what the rest of Stage 6 reasons about, but the
    # file is written from `_exported_links` so the junction turns survive into it.
    links, bridges = _exported_links(model)
    lane_widths = {lane.identifier: lane.width_m for lane in model.lanes}
    for lane in model.lanes:
        entries, exits = links[lane.identifier]
        features[lane.identifier] = _lane_feature(lane, entries, exits)

    for connector in model.connectors:
        if connector.identifier not in links:
            continue
        if connector.identifier in features:
            raise ConversionError(
                f"connector {connector.identifier} shares an id with another map feature"
            )
        features[connector.identifier] = _connector_feature(
            connector,
            min(lane_widths[connector.from_lane_id], lane_widths[connector.to_lane_id]),
        )

    for bridge_id, feature in bridges.items():
        if bridge_id in features:
            raise ConversionError(
                f"junction lane {bridge_id} shares an id with another map feature"
            )
        features[bridge_id] = feature

    dividers, superseded = _divider_boundaries(model, moves)
    for lane in model.lanes:
        for boundary in lane.boundaries:
            if boundary.identifier in features:
                raise ConversionError(
                    f"boundary {boundary.identifier} on lane {lane.identifier} shares an "
                    "id with another map feature"
                )
            if boundary.identifier in superseded:
                continue
            features[boundary.identifier] = {
                "type": (
                    _DIVIDER_TYPE if boundary.identifier in dividers else _BOUNDARY_TYPE
                ),
                "polyline": _polyline(boundary.points),
                "side": boundary.side,
                "lane_id": lane.identifier,
            }
    return features


def _scenario(
    *,
    model: PreliminaryLaneModel,
    workspace_name: str,
    manifest: dict[str, Any],
    model_sha256: str,
    plan: SignalPlan | None,
) -> tuple[
    dict[str, Any],
    dict[str, Any],
    dict[str, tuple[list[str], list[str]]],
    dict[str, list[str]],
]:
    """The scenario dict, the reachability facts, and the two graphs both were built from.

    The graphs come back out so the Stage 6 page can be drawn from the same objects rather
    than a second resolution of the same references. A page that disagreed with the
    dataset about which lanes join would be worse than no page.
    """
    neighbours = _lane_neighbours(model)
    moves = _lane_change_moves(model)
    features = _map_features(model, neighbours, moves)

    # Checked over what the file actually says rather than over the resolved lane graph: the
    # written chain runs through the junction lanes, so it is that chain which must have no
    # references to features nobody wrote.
    dangling = {
        target
        for feature in features.values()
        for target in (*feature.get("entry_lanes", ()), *feature.get("exit_lanes", ()))
        if target not in features
    }
    if dangling:
        raise ConversionError(
            f"{len(dangling)} lane reference(s) survived resolution without a map "
            f"feature: {', '.join(sorted(dangling)[:5])}"
        )

    routing = _reachability(neighbours, moves)
    # A 16-hex-digit prefix, which is how the fingerprint is written everywhere a person
    # reads it. The id ends up in the filename and in MetaDrive's logs, and the full 64
    # characters are one field away in `metadata.provenance`, so nothing is lost by not
    # spending 64 of them here.
    scenario_id = f"{workspace_name}-{model.metadata.generation_fingerprint[:16]}"
    scenario = {
        "id": scenario_id,
        "version": _FORMAT_VERSION,
        # One step, no motion. A map-only scenario still needs an envelope with a length
        # for the format to be well formed; it must not imply that anything moves.
        "length": 1,
        "tracks": {},
        # One step of tape, for the same reason `length` is 1: a map-only scenario still
        # needs a well-formed envelope. The lights are rebuilt at the real length in
        # `_with_route`, because `_check_object_state_dict` requires every state array to be
        # exactly as long as the scenario.
        "dynamic_map_states": (
            light_states(plan, model=model, steps=1, time_step_s=TIME_STEP_S) if plan else {}
        ),
        "map_features": features,
        "metadata": {
            "scenario_id": scenario_id,
            "dataset": _DATASET_NAME,
            # The three keys `ScenarioDescription.METADATA_KEYS` requires. `ts` must be an
            # array whose shape equals `length` - `sanity_check` reads `.shape` on it, so a
            # plain list fails there rather than at load.
            "coordinate": _COORDINATE,
            "metadrive_processed": False,
            "ts": np.zeros(1, dtype=np.float64),
            "sdc_id": None,
            "map_only": True,
            "coordinate_system_wkt": model.metadata.coordinate_system_wkt,
            "counts": {
                "lanes": len(model.lanes),
                # Counted by type rather than subtracted from the total, which stopped being a
                # safe way to count once the junction turns became features in their own right.
                "lane_boundaries": sum(
                    1
                    for item in features.values()
                    if item["type"] in (_BOUNDARY_TYPE, _DIVIDER_TYPE)
                ),
                # How many of the `LANE_SURFACE_STREET` features are junction turns rather than
                # road. MetaDrive makes no distinction - that is the point - so this is the only
                # place the split is recorded.
                "junction_lanes": sum(
                    1 for item in features.values() if item["type"] == _LANE_TYPE
                )
                - len(model.lanes),
                "connectors_total": len(model.connectors),
                "connectors_active": sum(1 for item in model.connectors if item.status == "active"),
                "signals": len(model.signals),
                "stop_lines": len(model.stop_lines),
                "restrictions": len(model.restrictions),
                # What OSM supplies and what a person chose are different counts, and the
                # gap is the point: `signals` is how many signal nodes the survey has,
                # `signalled_lanes` is how many lanes carry a light in this dataset. In
                # `junction-1` the first is 1 and it is at the edge of the extract.
                "signalled_lanes": len(plan.lanes) if plan else 0,
                "phase_groups": len(plan.groups) if plan else 0,
            },
            # Which lines were drawn broken, and on what authority. The same reason
            # `signals` carries `source` - a reader has to be able to tell a surveyed
            # marking from one this converter worked out. OSM supplies neither: the extract
            # behind `junction-1` carries no `lane_markings`, `change`, `overtaking` or
            # `divider` tag of any kind, so `_divider_boundaries` derives the style from
            # where a lane change is permitted and says so here. `merged` counts the second
            # copies of a shared divider that were dropped rather than drawn twice.
            "lane_markings": {
                "source": "derived-from-lane-change-permissions",
                "dividers": sum(
                    1 for item in features.values() if item["type"] == _DIVIDER_TYPE
                ),
                "edges": sum(
                    1 for item in features.values() if item["type"] == _BOUNDARY_TYPE
                ),
                # Boundaries the model holds, less the boundaries actually written. Counted by
                # type rather than as `len(features) - len(lanes)`, which quietly stopped
                # meaning "boundaries" once the junction turns became features too.
                "merged": sum(len(lane.boundaries) for lane in model.lanes)
                - sum(
                    1
                    for item in features.values()
                    if item["type"] in (_BOUNDARY_TYPE, _DIVIDER_TYPE)
                ),
            },
            # Present only when `--signals` was given, and always marked `synthesised`.
            # OSM records that a signal exists and carries no cycle, split or offset, so a
            # phase plan that could not be told apart from a surveyed one is the thing this
            # field exists to prevent. `signal_plan.plan_metadata` writes it.
            **(
                {"signals": plan_metadata(plan, model=model, time_step_s=TIME_STEP_S)}
                if plan
                else {}
            ),
            "routing": routing,
            "provenance": {
                "generator_version": model.metadata.generator_version,
                "generation_fingerprint": model.metadata.generation_fingerprint,
                "source_osm_sha256": manifest["source"]["sha256"],
                "reviewed_lane_model_sha256": model_sha256,
                "stage_5_status": manifest["stage_5"]["status"],
            },
        },
    }
    return scenario, routing, neighbours, moves


def _signal_timings(plan: SignalPlan, *, model: PreliminaryLaneModel) -> tuple[SignalTiming, ...]:
    """The plan flattened to one entry per signalled lane, for the route builder.

    The same `stop_points` the tape keys its walls on, so a baked stop and the wall it stops
    at cannot end up in different places.
    """
    points = stop_points(plan, model)
    return tuple(
        SignalTiming(
            lane_id=lane_id,
            stop_point=(points[lane_id][0], points[lane_id][1]),
            cycle_seconds=plan.cycle_seconds,
            green_seconds=group.green_seconds,
            yellow_seconds=group.yellow_seconds,
            offset_seconds=group.offset_seconds,
        )
        for group in plan.groups
        for lane_id in group.lanes
    )


def _read_routes(
    path: Path, *, model: PreliminaryLaneModel, model_sha256: str
) -> list[dict[str, str]]:
    """The routes drawn in the route builder, refused unless they were drawn on this map.

    A `routes.json` names two lane ids per route and nothing else. Lane ids are content
    addressed, so a route drawn on one generation and applied to another does not fail
    loudly - it either names lanes that no longer exist, or worse, names lanes that do exist
    somewhere else entirely. The identity block is what makes that a refusal rather than a
    silently different drive, and it is the same guard `apply-review` puts on a submission.
    """
    raw = _read(path, "route selection")
    if not isinstance(raw, dict):
        raise ConversionError(f"{path} is not a route selection")
    version = raw.get("routes_version")
    if version != ROUTES_VERSION:
        raise ConversionError(
            f"unsupported routes_version {version!r}; this converter writes and reads "
            f"{ROUTES_VERSION}"
        )

    identity = raw.get("identity")
    if not isinstance(identity, dict):
        raise ConversionError(f"{path} has no identity block, so it cannot be checked")
    expected = {
        "generation_fingerprint": model.metadata.generation_fingerprint,
        "reviewed_lane_model_sha256": model_sha256,
    }
    for key, value in expected.items():
        if identity.get(key) != value:
            raise ConversionError(
                f"{path} was drawn on a different lane model ({key} does not match). "
                "Re-open the route builder from this workspace and pick the routes again"
            )

    routes = raw.get("routes")
    if not isinstance(routes, list) or not routes:
        raise ConversionError(f"{path} contains no routes")

    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for position, entry in enumerate(routes):
        if not isinstance(entry, dict):
            raise ConversionError(f"route {position} in {path} is not an object")
        name = entry.get("name")
        if not isinstance(name, str) or not _ROUTE_NAME.fullmatch(name):
            raise ConversionError(
                f"route {position} in {path} has name {name!r}; a name must be 1-40 "
                "characters of letters, digits or hyphens, because it goes into the "
                "scenario filename MetaDrive keys the dataset on"
            )
        if name in seen:
            raise ConversionError(
                f"{path} uses the route name {name!r} twice, so the two would write to "
                "the same scenario file"
            )
        seen.add(name)
        for key in ("start_lane", "end_lane"):
            if not isinstance(entry.get(key), str):
                raise ConversionError(f"route {name!r} in {path} has no {key}")
        out.append(
            {
                "name": name,
                "start_lane": entry["start_lane"],
                "end_lane": entry["end_lane"],
            }
        )
    return out


def _with_route(
    base: dict[str, Any],
    *,
    route: Route,
    track: dict[str, Any],
    model: PreliminaryLaneModel,
    plan: SignalPlan | None,
) -> dict[str, Any]:
    """The map-only scenario, plus the car that turns it into a drive.

    A shallow copy sharing `map_features`: every scenario in the dataset is the same map, and
    each is pickled separately, so sharing the features costs nothing and guarantees the
    routes cannot disagree about the road they are drawn on. `metadata` is rebuilt rather
    than shared, because it is the part that differs.

    The lights are rebuilt rather than shared for a harder reason: every array in a light's
    `state` is length-checked against the scenario length, so the one-step tape the map-only
    envelope carries is wrong for every route.
    """
    steps = len(track["state"]["position"])
    scenario_id = f"{base['id']}-{route.name}"
    metadata = {
        **base["metadata"],
        "scenario_id": scenario_id,
        # `sanity_check` reads `.shape` on this and asserts it matches `length`.
        "ts": np.arange(steps, dtype=np.float64) * TIME_STEP_S,
        "sdc_id": _EGO_ID,
        # No longer true, and the field exists so a reader does not have to infer it.
        "map_only": False,
        "sdc_route": route_summary(route),
    }
    return {
        **base,
        "id": scenario_id,
        "length": steps,
        "tracks": {_EGO_ID: track},
        "dynamic_map_states": (
            light_states(plan, model=model, steps=steps, time_step_s=TIME_STEP_S) if plan else {}
        ),
        "metadata": metadata,
    }


def _read_signal_plan(
    path: Path, *, model: PreliminaryLaneModel, model_sha256: str
) -> SignalPlan:
    """`signal_plan.read_signal_plan`, with its failures wearing this stage's name.

    Split out for the reason `_read` is: the CLI catches `ConversionError` and nothing else,
    so a malformed plan would otherwise print a traceback instead of a sentence.
    """
    try:
        return read_signal_plan(
            _read(path, "signal plan"),
            model=model,
            model_sha256=model_sha256,
            source=path,
        )
    except SignalPlanError as error:
        raise ConversionError(str(error)) from error


def convert_scenario(
    *,
    workspace: Path,
    config: ConverterConfig,
    routes: Path | None = None,
    signals: Path | None = None,
) -> tuple[list[Path], Path, Path, Path, tuple[Path, Path, Path]]:
    """Convert WORKSPACE's validated lane model into a ScenarioNet dataset.

    Without `routes` the result is map-only: every road, and nothing that moves. MetaDrive
    can load and check that, but it cannot *run* it - `ScenarioEnv` builds its route from a
    recorded ego car, and refuses to reset without one.

    With `routes` - a `routes.json` from the Stage 6 route builder - the dataset holds one
    scenario per route, all sharing the same map. That is the shape every ScenarioNet
    dataset has: variety comes from having many scenarios, not from freedom within one, so
    `num_scenarios=N` is what gives a policy different drives to learn across.

    With `signals` - a `signals.json` from the Stage 6 signal builder - every lane in the plan
    gets a traffic light, and `metadata.signals` carries the phase structure that produced it.
    Both are needed: MetaDrive replays the tape and nothing else, while anything that wants to
    *drive* the lights - `tools/signal_control.py`, which re-draws the phase per episode so an
    agent cannot learn the clock - needs the numbers rather than the colours.

    `config` is accepted for symmetry with the other stage entry points; conversion is a
    faithful restatement of the reviewed model and has nothing left to configure. Signal
    timing deliberately does **not** live there: `configuration_checksum` is an input to the
    generation fingerprint, so a phase plan in the config would invalidate the lane model
    review the next time the map was generated.
    """
    workspace = workspace.resolve()
    model_path = workspace / "lane-model" / "reviewed.json"
    manifest = _check_stage_5(workspace, model_path)
    model_sha256 = _sha256(model_path)

    model = PreliminaryLaneModel.model_validate(_read(model_path, "reviewed lane model"))
    plan = (
        _read_signal_plan(signals, model=model, model_sha256=model_sha256)
        if signals is not None
        else None
    )
    scenario, routing, neighbours, moves = _scenario(
        model=model,
        workspace_name=workspace.name,
        manifest=manifest,
        model_sha256=model_sha256,
        plan=plan,
    )

    selections = (
        _read_routes(routes, model=model, model_sha256=model_sha256)
        if routes is not None
        else []
    )
    # The lights the route builder has to obey. Read once here rather than inside `ego_route`,
    # which must not depend on a plan being present - most datasets have none.
    signal_timings = _signal_timings(plan, model=model) if plan else ()
    planned: list[Route] = []
    scenarios: list[dict[str, Any]] = []
    try:
        for selection in selections:
            route = plan_route(
                model=model,
                neighbours=neighbours,
                moves=moves,
                name=selection["name"],
                start_lane=selection["start_lane"],
                end_lane=selection["end_lane"],
                signals=signal_timings,
            )
            polyline = route_polyline(
                model=model, route_lanes=route.lanes, lane_changes=route.lane_changes
            )
            planned.append(route)
            scenarios.append(
                _with_route(
                    scenario,
                    route=route,
                    track=ego_track(route=route, polyline=polyline),
                    model=model,
                    plan=plan,
                )
            )
    except RouteError as error:
        raise ConversionError(str(error)) from error
    if not scenarios:
        scenarios = [scenario]

    dataset_dir = workspace / "scenarionet"
    summary_path = dataset_dir / SUMMARY_FILE
    mapping_path = dataset_dir / MAPPING_FILE

    scenario_paths: list[Path] = []
    summary: dict[str, Any] = {}
    mapping: dict[str, str] = {}
    for item in scenarios:
        file_name = scenario_file_name(item["id"])
        path = dataset_dir / file_name
        _write_bytes_atomic(path, _portable_pickle(item))
        scenario_paths.append(path)
        summary[file_name] = item["metadata"]
        # An empty relative path means "beside the summary". Both index files key on the
        # same computed filename, so the two cannot drift apart.
        mapping[file_name] = ""

    # Scenario files from an earlier run with different route names would otherwise stay in
    # the directory. `read_dataset_summary` reads the summary rather than the listing, so
    # they would not be loaded - but they would still be a dataset directory holding files
    # that are not in the dataset, which is exactly the state that makes a stale pickle look
    # current.
    for stale in sorted(dataset_dir.glob("sd_*.pkl")):
        if stale not in scenario_paths:
            stale.unlink()

    _write_bytes_atomic(summary_path, _portable_pickle(summary))
    _write_bytes_atomic(mapping_path, _portable_pickle(mapping))

    # Written after the pickles and from the same `neighbours`, so the page can only ever
    # describe a dataset that exists. It goes in `inspection/` beside the other stages'
    # pages rather than in the dataset directory, which MetaDrive reads and which must
    # hold nothing but the dataset.
    html_path = workspace / "inspection" / "stage-6-reachability.html"
    _write_text_atomic(
        html_path,
        render_reachability_html(
            model=model, neighbours=neighbours, moves=moves, routing=routing
        ),
    )

    # The route builder is written on every convert, including a map-only one - it is how a
    # map-only dataset stops being map-only, so it has to exist before there are any routes.
    builder_path = workspace / "inspection" / "stage-6-route-builder.html"
    _write_text_atomic(
        builder_path,
        render_route_builder_html(
            model=model,
            neighbours=neighbours,
            moves=moves,
            workspace_name=workspace.name,
            model_sha256=model_sha256,
            routes_version=ROUTES_VERSION,
        ),
    )

    # Written on every convert for the same reason the route builder is: it is how a dataset
    # with no lights stops having none, so it has to exist before there is a plan to draw.
    signal_path = workspace / "inspection" / "stage-6-signal-builder.html"
    _write_text_atomic(
        signal_path,
        render_signal_builder_html(
            model=model,
            neighbours=neighbours,
            moves=moves,
            workspace_name=workspace.name,
            model_sha256=model_sha256,
            signals_version=SIGNALS_VERSION,
        ),
    )

    artifacts = {}
    for name, path in (
        ("dataset_summary", summary_path),
        ("dataset_mapping", mapping_path),
        ("reachability_html", html_path),
        ("route_builder_html", builder_path),
        ("signal_builder_html", signal_path),
        *(
            (f"scenario:{item['id']}", path)
            for item, path in zip(scenarios, scenario_paths, strict=True)
        ),
    ):
        artifacts[name] = {
            "path": path.relative_to(workspace).as_posix(),
            "sha256": _sha256(path),
            "size_bytes": path.stat().st_size,
        }

    report = {
        "report_version": REPORT_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "converted",
        "scenario_id": scenario["id"],
        "inputs": {
            "reviewed_lane_model": "lane-model/reviewed.json",
            "reviewed_lane_model_sha256": model_sha256,
            "routes": routes.name if routes is not None else None,
            "signals": signals.name if signals is not None else None,
        },
        "converted": scenario["metadata"]["counts"],
        "map_features": len(scenario["map_features"]),
        "routing": routing,
        # Named outside the pickle because these are the strings every MetaDrive entry point
        # keys on, and the first thing to check when a load fails.
        "scenario_files": [path.name for path in scenario_paths],
        # Empty for a map-only dataset, which is the difference between one MetaDrive can
        # check and one it can drive.
        "routes": [route_summary(route) for route in planned],
        # None when no plan was given. Reported outside the pickle because the phase numbers
        # are the first thing to check when a light does not do what was expected, and
        # reading them back out of a pickle needs MetaDrive's interpreter.
        "signals": scenario["metadata"].get("signals"),
        "artifacts": artifacts,
    }

    report_path = workspace / "reports" / "scenario-conversion.json"
    _write_text_atomic(report_path, json.dumps(report, indent=2, sort_keys=True) + "\n")

    manifest["stage_6"] = {
        "status": report["status"],
        "scenario_id": scenario["id"],
        "converted": report["converted"],
        "map_features": report["map_features"],
        "routing": routing,
        "routes": report["routes"],
        "signals": report["signals"],
        "scenario_files": report["scenario_files"],
        "source_lane_model": {"path": "lane-model/reviewed.json", "sha256": model_sha256},
        "artifacts": artifacts,
    }
    (workspace / "source" / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    return (
        scenario_paths,
        summary_path,
        mapping_path,
        report_path,
        (html_path, builder_path, signal_path),
    )
