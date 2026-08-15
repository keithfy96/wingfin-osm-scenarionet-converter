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
from collections.abc import Mapping, MutableMapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import networkx as nx
import numpy as np
from shapely import STRtree, contains_xy, segmentize, union_all
from shapely.geometry import LineString, MultiLineString, MultiPolygon, Point, Polygon
from shapely.ops import linemerge, unary_union

# Private helpers imported across modules on purpose, for the reason `validation` gives:
# these are the exact routines the earlier stages use, and a Stage 6 copy would be a
# second implementation to keep in step. `_sha256` must match what Stage 5 wrote into the
# manifest or every run reports a stale checksum.
from osm_scenario.apply_review import ApplyReviewError, _read_json, _sha256
from osm_scenario.config import ConverterConfig
from osm_scenario.ego_route import (
    COINCIDENT_M,
    TIME_STEP_S,
    Route,
    RouteError,
    SignalTiming,
    ego_track,
    plan_route,
    route_polyline,
    route_summary,
)
from osm_scenario.generation import MIN_TRIMMED_LANE_M
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

# How close to `MIN_TRIMMED_LANE_M` a lane's centreline has to land to be the clamp rather than
# a way that happens to be short. A trim is interpolated along the line, so the length comes back
# a fraction of a millimetre off the constant; nothing else in either extract sits within 7 cm of
# it, so the tolerance is picked to be unmistakably below that gap rather than tuned to a case.
_STUB_LANE_TOLERANCE_M = 0.01

# How much of a line that is already drawn is taken out of the kerb candidate. Only enough that
# the kerb is not laid a second time over paint that exists - two coincident lines are resampled
# out of phase by MetaDrive (`scenario_map.py:61` resamples at 2 m) and draw as something neither
# of them is. It is deliberately *not* a stand-off: an earlier 0.15 m here left a visible gap
# wherever a kerb took over from a lane's edge line, and those gaps were 58 of `mosque`'s 154
# breaks in an otherwise continuous kerb and 85 of `junction-1`'s 186.
_KERB_PAINT_ALLOWANCE_M = 0.02

# How far each end of a kerb line is pushed along its own end tangent, so it runs into the line it
# meets instead of stopping beside it. The end sits on the road edge and a tenth of a metre along
# that edge is still the road edge, so this cannot wander off it; the overlap is what makes one
# physical kerb read as one line rather than a chain of unequal ones.
_KERB_JOIN_OVERLAP_M = 0.10

# How far from an arc's end a drawn line has to be to count as the line that arc meets there. It
# is the paint allowance plus a little, because that subtraction is what put the end where it is.
_KERB_JOIN_REACH_M = 0.25

# How wide a gap between two road surfaces is closed up before the kerb is traced round them. Lane
# and connector surfaces are each buffered from their own centreline, so where they meet they leave
# a notch 0.10-0.30 m wide - narrower than the 0.125 m line drawn on it. Traced literally, the ring
# dives in along one wall of that notch and back out along the other, so **both walls get painted**.
# 238 of `mosque`'s 408 kerb lines and 140 of `junction-1`'s 284 were that: 459 m and 270 m of paint
# lying on open tarmac in pairs about 1.93 m long. A gap that narrow is an artefact of how the
# surfaces are built, not a road edge, so the kerb should run straight past it.
#
# 0.35 m is the smallest closing that reaches zero on both extracts - 0.30 leaves one on each, 0.40
# and 0.45 are also clean, so it is not a knife edge, and 0.50 swallows a real island on `mosque`.
# It settles the islands too: enclosed holes fall from 693 to exactly `mosque`'s 20 real ones and
# from 330 to exactly `junction-1`'s 9, those being the same defect seen from the inside.
#
# Mitred, not rounded: a round join would pull every convex corner of the network out and back by
# the radius. Mitred, nothing moves except where a gap is filled - measured, the kerb sits a median
# 0.004 m from the true road edge and only reaches 0.46 m at the notch mouths it now bridges.
_KERB_GAP_CLOSE_M = 0.35

# How much of a shared edge two surfaces must have before a gap between them may be merged into
# one of them. Numerical only: the wedge comes out of `sealed.difference(road)`, so the parts of
# its ring that lie on a real surface are built from that surface's own coordinates and coincide
# exactly. The tolerance is here for the parts that came through the two buffers, not for slop.
_SEAM_CONTACT_M = 1e-6

# The grid the road surfaces are unioned on - see `_road_union`. A nanometre, which is six orders
# of magnitude below the tolerances anything else here works in and well inside what a double
# holds at these coordinates, so it decides nothing about the geometry and only stops the union
# losing a whole surface.
_UNION_GRID_M = 1e-9

# The furthest apart two points on a sealed polygon's ring may be. Nothing geometric depends on
# it - `segmentize` only adds points to edges that already exist - and it is here for MetaDrive's
# `sanity_check`, which measures where a polygon is by averaging its vertices. See the write-back
# in `_sealed_surfaces`. At 5 m the worst polygon-against-centreline reading on either extract is
# back to what an untouched map already gives.
_RING_STEP_M = 5.0

# How far to either side of a candidate kerb the road is looked for. A kerb separates road from
# not-road; a line with tarmac on both sides of it is the wall of a slot between two surfaces,
# which is the defect `_KERB_GAP_CLOSE_M` exists to remove and this catches where the slot is too
# wide for it to seal.
#
# **The test is every sample, not most of them**, and the arcs themselves chose that. Scored over
# both extracts, a wall of a slot reads 1.000 and is 0.38-1.00 m long - 6 of them on `mosque` and
# 2 on `junction-1` - and the next score down is 0.750. Those are arcs of 2-4 m that run along a
# seam for part of their length and along real road edge for the rest, and the majority rule threw
# one out on `junction-1`: `12b7284b7f`, 3.80 m of kerb round a **118 m² traffic island**, which
# happens to pass within 0.8 m of a lane and a junction bridge that fail to meet. An arc that is
# half seam and half kerb is a kerb passing a seam, and the seam is the surfaces' fault.
#
# **An island cannot be caught by this however narrow it is**, which is why it can be a blunt
# geometric test: an island is a hole in the union, so the probe lands outside the road on the
# island side. `mosque`'s narrowest real island is 0.97 m across and `junction-1`'s 1.55 m.
_KERB_SIDE_PROBE_M = 0.8

# The shortest kerb line worth drawing, and it is numerical dust only. **`_MIN_KERB_M` was 2.0 m,
# and calling that a proxy for "do not paint on road a car drives on" is what shipped the notch
# walls.** It was the needle filter, and a notch wall is 1.93 m. Lowering it was still right - the
# proxy cost 19 of `mosque`'s breaks and 22 of `junction-1`'s, arcs that bridged two painted lines
# and were thrown away for being short - but what replaced it had to be `_KERB_GAP_CLOSE_M`, which
# removes the seam, rather than `_KERB_INSET_M`, which cannot see it.
_MIN_KERB_M = 0.05

# How far inside the carriageway a kerb line has to be before it is refused. Every line MetaDrive
# draws gets a ghost body and a solid one sets `on_white_continuous_line`, so a line the far side of
# this is one a car can be judged to have crossed. **It catches a line that strays into the road and
# nothing else** - a notch wall lies exactly *on* the boundary, so it passes this cleanly, and the
# stray count read 0 while 238 marks sat on the tarmac. Measured against the real surfaces, never
# against the closed ones.
_KERB_INSET_M = 0.25

# How square-on to the paint at both of its ends an arc has to run before it is read as the end of
# a road rather than a length of kerb. A road that simply stops - at the edge of the extract, or a
# dead end - leaves a bar of bare edge straight across the carriageway, and painting it would draw
# a stop line where there is none. `_MAX_ROAD_END_M` bounds it to something a carriageway wide, so
# a genuinely long kerb that happens to meet its neighbours squarely is not mistaken for one. 100
# of these are left bare on `mosque` and 96 on `junction-1`.
_ROAD_END_SQUARENESS = 0.35
_MAX_ROAD_END_M = 6.0

# The smallest enclosed gap in the road that is treated as a real island - a roundabout centre, or
# the block a ring road runs round - and given a kerb on its inward-facing side. A backstop rather
# than the thing doing the work: `_KERB_GAP_CLOSE_M` already seals the needle-thin slivers where two
# lane polygons fail to meet, and what is left as a hole afterwards is exactly the 20 real islands
# on `mosque` and 9 on `junction-1`.
_MIN_ISLAND_M = 0.3

# Where a kerb line is cut because it has turned back on itself. Where two turns cross, the
# outline of their union can run out along a seam and straight back down it - a zero-width needle
# that draws as a mark across the junction rather than round it, and one of them landed on
# drivable road. The threshold is not a taste: over both workspaces the per-vertex turns are 6740
# under 10 degrees, a cluster of 40 at 80-89 where a connector's flat end cap meets its side, then
# nothing at all until 32 sit at 170-179. Anything above this is a seam; the sharpest real corner
# a kerb turns is that 90. The same figure `ego_route.MAX_VERTEX_TURN_DEG` uses, for the same
# reason - a line whose shape is not ours has doubled back rather than bent.
_MAX_KERB_TURN_DEG = 150.0

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


def _stub_lanes(model: PreliminaryLaneModel) -> set[str]:
    """Lanes that survive only because the junction trim was clamped, and so sit in a junction.

    Every lane is cut back from the junctions at its ends so a connector has room to bridge it.
    A way shorter than its two setbacks together would trim to nothing, so `_trimmed_edge`
    scales both setbacks down and stops at `MIN_TRIMMED_LANE_M` - which keeps the road, and is
    right, but leaves that lane reaching further into the junction than any other. Its own
    comment in `generation.py` says so, beside the `trim_clamped_edges` count.

    The paint is what goes wrong there, not the lane. `junction-1`'s node 1927184814 is four
    such ways in a loop around one open junction - 9 lanes, 18 boundaries, all of them inside
    it - drawn as 2 m marks pointing four different ways across a box a car turns through. And
    they are not only drawn: `ScenarioBlock` gives every line a ghost body, which is what sets
    `on_white_continuous_line`, so a policy reading line contacts sees a violation mid-turn.

    Length is the whole test, and it is exact rather than approximate. A lane comes out at
    `MIN_TRIMMED_LANE_M` only when the clamp bound, and the clamp binding is what makes it
    intrude; the next lanes up - 2.07 m, 2.37 m, 3.65 m - kept their full setbacks, so they end
    outside both junctions and their markings are on open road. 28 of `junction-1`'s 285 lanes
    and 43 of `mosque`'s 405 are clamped.
    """
    stubs: set[str] = set()
    for lane in model.lanes:
        points = _polyline(lane.centerline)
        if len(points) < 2:
            continue
        length = float(np.linalg.norm(np.diff(points, axis=0), axis=1).sum())
        if length <= MIN_TRIMMED_LANE_M + _STUB_LANE_TOLERANCE_M:
            stubs.add(lane.identifier)
    return stubs


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


def _surface(points: Any) -> Polygon | None:
    """A road polygon from a ring, whether it is held as `Point2D`s or as an array.

    Both shapes turn up: `LaneFeature.polygon` and `ConnectorFeature.polygon` are model objects,
    while `_bridge_feature` has already written its ring out as the array the file carries. A
    ring under four points is not an area, and a self-touching one is repaired rather than
    dropped - `buffer(0)` is shapely's own idiom for it - because a discarded surface would leave
    a kerb line drawn across a junction that is in fact road.
    """
    ring = points if isinstance(points, np.ndarray) else _polyline(points)
    if len(ring) < 4:
        return None
    shape = Polygon(ring)
    if not shape.is_valid:
        shape = shape.buffer(0)
    if shape.is_empty:
        return None
    if isinstance(shape, MultiPolygon):
        shape = max(shape.geoms, key=lambda part: part.area)
    return shape


def _uncreased(arc: LineString) -> list[np.ndarray]:
    """`arc` cut at every vertex where it turns back on itself - see `_MAX_KERB_TURN_DEG`.

    Cut rather than discarded: a needle is usually a metre of seam on the end of an arc that is
    otherwise the kerb, and throwing the whole line away to be rid of it would leave the corner
    it was drawn for unpainted. The pieces either side are returned and the caller drops whichever
    are too short to be worth drawing, which is what removes a needle: both of its halves are.
    """
    points = np.array(arc.coords, dtype=np.float64)
    steps = np.diff(points, axis=0)
    spans = np.hypot(steps[:, 0], steps[:, 1])
    # `> 0` is not enough, for the reason `ego_route.COINCIDENT_M` is 1e-3 and not 1e-6: a vertex
    # that shapely repeated lands a fraction of a micrometre away rather than exactly on itself,
    # and `atan2` over that returns a bearing made of noise. Read as a direction it hides the
    # reversal on either side of it, which is how the one needle that reached drivable road got
    # there - it survived a cut that was looking straight at it.
    keep = spans > COINCIDENT_M
    steps, spans = steps[keep], spans[keep]
    if len(steps) < 2:
        return [points]
    # Rebuilt from the surviving steps, so a repeated vertex cannot read as a reversal: two
    # identical points have no direction, and `atan2` over nothing returns a bearing anyway.
    walked = np.vstack([points[0], points[0] + np.cumsum(steps, axis=0)])
    unit = steps / spans[:, None]
    cross = unit[:-1, 0] * unit[1:, 1] - unit[:-1, 1] * unit[1:, 0]
    dot = unit[:-1, 0] * unit[1:, 0] + unit[:-1, 1] * unit[1:, 1]
    creases = np.flatnonzero(
        np.abs(np.degrees(np.arctan2(cross, dot))) > _MAX_KERB_TURN_DEG
    )
    if not len(creases):
        return [walked]
    cuts = [0, *(int(index) + 1 for index in creases), len(walked) - 1]
    return [
        walked[start : stop + 1]
        for start, stop in zip(cuts, cuts[1:], strict=False)
        if stop > start
    ]


def _road_union(surfaces: Sequence[Polygon]) -> Polygon | MultiPolygon:
    """Every road surface as one shape - and `unary_union` is not reliable enough to do it.

    On both extracts, plain `unary_union` over the lane polygons **silently drops one of them**:
    `3cc5be39c0caf1c2` on `mosque`, 141.17 m² of lane, and `2fe4cf2f93...` on `junction-1`. The
    union comes back valid, one piece, and simply does not cover that lane - union it a second
    time and it appears. Unioning the same shapes on a grid of `_UNION_GRID_M` covers all 610 and
    all 434, and the area rises by exactly the missing lane's.

    That matters here beyond tidiness: a surface missing from the union is a hole in the road, and
    a hole in the road is where this module draws kerb lines.
    """
    return union_all(list(surfaces), grid_size=_UNION_GRID_M)


def _closed(road: Polygon | MultiPolygon) -> Polygon | MultiPolygon:
    """`road` with every gap under `_KERB_GAP_CLOSE_M` sealed up.

    Mitred, not rounded: a round join would pull every convex corner of the network out and back
    by the radius, moving the whole outline. Mitred, nothing moves except where a gap is filled.
    """
    return road.buffer(
        _KERB_GAP_CLOSE_M, join_style="mitre", mitre_limit=5.0
    ).buffer(-_KERB_GAP_CLOSE_M, join_style="mitre", mitre_limit=5.0)


def _sealed_surfaces(features: MutableMapping[str, dict[str, Any]]) -> int:
    """Close the holes between road surfaces, by growing one of the surfaces that borders each.

    **A hole in the tarmac draws as a white line, and nothing paints it.** A lane surface is built
    by offsetting its own centreline, so where one edge of a road hands over to the next the two
    rectangles meet end-to-end with square caps a few degrees apart, and a wedge of nothing is left
    between them. `terrain.frag.glsl:115` paints everything in `5 < value < 16` white and the
    semantic texture is filtered, so the blend from road surface (20) across a gap to ground (0)
    passes straight through that band. Keith: *"lines that go into the lanes where the tarmac does
    not fully cover it."* 80 of these on `mosque` were wider than a texel - 168.7 m², 69 of them
    enclosed by road on every side, the widest 0.687 m - and 13 sat within 3 m of the driven line.

    It is not the kerb rule and it is not adjacent lanes: three pairs of side-by-side lanes on
    `mosque` way `859423754` share their facing edges to 0.0000 m. It is only the joins along a
    road, and it is the same mitre gap the v25 change measured at a median 0.213 m.

    Each wedge is added to the **one** surface it shares the most boundary with, because the union
    is what MetaDrive paints and a wedge only needs covering once. That is what holds this to ~80
    polygons on `mosque` rather than the 336 that border one. Surface is only ever added, no
    feature is created or destroyed, and no painted line moves - the lines come from `left`/`right`
    in `generation.py` and are not touched here, so a lane's kerb stays where it was and the tarmac
    now reaches it.

    Returns how many polygons grew, for `metadata.lane_markings`.
    """
    identifiers: list[str] = []
    shapes: list[Polygon] = []
    for identifier, feature in features.items():
        if feature["type"] != _LANE_TYPE:
            continue
        shape = _surface(np.asarray(feature["polygon"], dtype=np.float64))
        if shape is not None:
            identifiers.append(identifier)
            shapes.append(shape)
    if len(shapes) < 2:
        return 0

    road = _road_union(shapes)
    holes = _closed(road).difference(road)
    if holes.is_empty:
        return 0
    tree = STRtree(shapes)

    grown: dict[int, Polygon] = {}
    for wedge in holes.geoms if hasattr(holes, "geoms") else [holes]:
        if not isinstance(wedge, Polygon) or wedge.is_empty:
            continue
        reach = wedge.buffer(_SEAM_CONTACT_M)
        contacts = sorted(
            (
                (shapes[index].exterior.intersection(reach).length, int(index))
                for index in tree.query(reach)
            ),
            reverse=True,
        )
        contacts = [(length, index) for length, index in contacts if length > 0.0]
        # A gap bounded by one surface alone is that surface's own concavity, not a seam between
        # two of them, and closing it would change the shape of a road on nobody's evidence.
        if len(contacts) < 2:
            continue
        # **A wedge is shared out among the surfaces along it, not given to one of them.** Given
        # whole to its longest neighbour, `mosque`'s largest snakes 190 m through a junction and
        # takes a 40 m lane's polygon with it - which MetaDrive's own `sanity_check` rejects,
        # because the centroid of that polygon then stands more than 100 m from the centroid of
        # the centreline it belongs to. Each surface takes only the part of the wedge within
        # `_KERB_GAP_CLOSE_M` of itself, longest shared edge first, so what a polygon gains is
        # always beside it.
        remaining: Any = wedge
        for _, index in contacts:
            if remaining.is_empty:
                break
            share = remaining.intersection(shapes[index].buffer(_KERB_GAP_CLOSE_M))
            for part in share.geoms if hasattr(share, "geoms") else [share]:
                if not isinstance(part, Polygon) or part.is_empty:
                    continue
                # A union has to come out as one simple ring, because `ScenarioLane` reads the
                # polygon straight through (`edge_road_network.py:121`). A part meeting its
                # surface across a sliver a millionth of a metre wide unions to a `MultiPolygon`,
                # which is what the buffered copy is for, and one that would close a ring round
                # an island leaves an interior - there the answer is the next surface along.
                for piece in (part, part.buffer(_SEAM_CONTACT_M)):
                    merged = unary_union([grown.get(index, shapes[index]), piece])
                    if isinstance(merged, Polygon) and not merged.interiors:
                        grown[index] = merged
                        remaining = remaining.difference(piece)
                        break

    for index, shape in grown.items():
        # Spaced out before it is written, and MetaDrive's own gate is the reason.
        # `ScenarioDescription.sanity_check` compares the **mean of the vertices** of the polygon
        # against the mean of the vertices of the centreline (`scenario_description.py:270`) and
        # refuses the map at 100 m. A ring is 5 points, so a 400 m lane that gains a hundred of
        # them at one end has its vertex mean dragged there - measured 14.9 m -> 136.8 m on
        # `mosque` way `ee2c3f00ec3383e0`, and the dataset stopped loading. Segmentizing inserts
        # points along the edges the ring already has, so the polygon is **the same shape to the
        # last decimal** and its vertex mean becomes a perimeter centre again.
        features[identifiers[index]]["polygon"] = np.asarray(
            segmentize(shape, _RING_STEP_M).exterior.coords, dtype=np.float64
        )
    return len(grown)


def _road_on_both_sides(line: LineString, road: Polygon | MultiPolygon) -> bool:
    """Whether `line` has tarmac on both sides of it, which means it is not a kerb.

    See `_KERB_SIDE_PROBE_M`. Sampled rather than reasoned about, because the arcs this rejects
    are the walls of a slot two surfaces left between them and a slot has no other description.
    """
    points = np.asarray(line.coords, dtype=np.float64)
    if len(points) < 2 or line.length <= COINCIDENT_M:
        return False
    steps = np.linspace(0.0, line.length, max(3, int(line.length / 0.5) + 1))
    heads = np.asarray(
        [line.interpolate(min(line.length, at + 0.01)).coords[0] for at in steps]
    )
    tails = np.asarray([line.interpolate(at).coords[0] for at in steps])
    reach = heads - tails
    span = np.hypot(reach[:, 0], reach[:, 1])
    usable = span > COINCIDENT_M
    if not usable.any():
        return False
    normal = np.column_stack([-reach[:, 1], reach[:, 0]])[usable] / span[usable, None]
    base = tails[usable]
    both = np.ones(len(base), dtype=bool)
    for side in (1.0, -1.0):
        probe = base + normal * side * _KERB_SIDE_PROBE_M
        both &= contains_xy(road, probe[:, 0], probe[:, 1])
    return bool(both.all())


def _kerb_rings(road: Polygon | MultiPolygon) -> list[LineString]:
    """Every edge of the road network a kerb could run along.

    The *exterior* of each piece, plus the ring of any enclosed gap wide enough to be a real
    island - a roundabout centre, or the block a ring road runs round, whose inward-facing kerb is
    as real as the outward one and was getting nothing.

    **Called on the road with its seams closed**, never on the raw union: a sliver between two
    surfaces that fail to meet is a defect in the surfaces, and drawing a kerb down both sides of
    one draws the defect rather than a road edge. `_KERB_GAP_CLOSE_M` removes them, and
    `_MIN_ISLAND_M` here is the backstop for anything wider that survives.

    Nothing here can reach the inside of a junction: the interior of the box is covered road, so
    it is not on any ring.
    """
    parts = list(road.geoms) if isinstance(road, MultiPolygon) else [road]
    rings: list[LineString] = []
    for part in parts:
        rings.append(LineString(part.exterior))
        for hole in part.interiors:
            if not Polygon(hole).buffer(-_MIN_ISLAND_M).is_empty:
                rings.append(LineString(hole))
    return rings


def _end_squareness(arc: LineString, paint: STRtree, lines: Sequence[LineString]) -> float:
    """How square-on `arc` runs to the paint it meets, at whichever of its ends is squarer.

    A length of kerb leaves an existing line pointing the way that line was going; a bar across
    the end of a road leaves both of them sideways. Returned as the larger of the two |cos|, so a
    piece is only read as a road end when *both* ends are square - one square end is a kerb
    turning a corner into a line that carries straight on, which is ordinary.

    1.0 where there is no paint within reach to compare against, which reads as "not a road end":
    an arc with nothing at its ends is not closing a gap between two lines.
    """
    points = np.asarray(arc.coords, dtype=np.float64)
    if len(points) < 2:
        return 1.0
    # Each end's heading is taken over a few vertices rather than the last segment alone: a ring
    # from `unary_union` carries vertices a few millimetres apart, and one of those on its own has
    # no reliable direction.
    ends = (
        (points[0], points[0] - points[min(3, len(points) - 1)]),
        (points[-1], points[-1] - points[max(-4, -len(points))]),
    )
    best = 0.0
    for tip, step in ends:
        span = float(np.hypot(*step))
        if span <= COINCIDENT_M:
            return 1.0
        heading = step / span
        node = Point(tip)
        nearby = paint.query_nearest(node, max_distance=_KERB_JOIN_REACH_M)
        if not len(nearby):
            return 1.0
        line = lines[int(nearby[0])]
        along = line.project(node)
        back = np.asarray(line.interpolate(max(0.0, along - 1.0)).coords[0])
        ahead = np.asarray(line.interpolate(min(line.length, along + 1.0)).coords[0])
        reach = ahead - back
        length = float(np.hypot(*reach))
        if length <= COINCIDENT_M:
            return 1.0
        best = max(best, abs(float(np.dot(heading, reach / length))))
    return best


def _extended(points: np.ndarray) -> np.ndarray:
    """`points` pushed `_KERB_JOIN_OVERLAP_M` further along its own end tangent at both ends."""

    def push(tip: np.ndarray, inward: np.ndarray) -> np.ndarray:
        step = tip - inward
        span = float(np.hypot(*step))
        if span <= COINCIDENT_M:
            return tip
        return tip + step / span * _KERB_JOIN_OVERLAP_M

    return np.vstack([push(points[0], points[1]), points, push(points[-1], points[-2])])


def _junction_kerb_boundaries(
    features: Mapping[str, dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], int]:
    """The kerb, wherever the road has an edge and nothing already paints it.

    `_map_features` writes boundaries for `model.lanes` and nothing else, so a `ConnectorFeature`
    is a road polygon with no lines at all, and `_stub_lanes` drops 56 more on `junction-1`. That
    is right for the *inside* of a junction - real ones are bare, and 82 turns' worth of edge line
    would cross each other through the middle of every box - but the *edge* of a junction is a
    kerb, and a kerb is painted everywhere else on the map. What a reader sees in its place is
    `terrain.frag.glsl` colouring anything in `5 < value < 16` white, so the filtered blend from
    road surface (20) to ground (0) draws a hairline about one texel wide - 3 cm against the
    0.156 m a real marking draws at. It traces the road convincingly and is not a line.

    **The rule is continuity, and that is the whole of the second attempt at this.** The first
    version took the outline of the *junction* surfaces alone and then stood it off 0.15 m from
    every line it met, dropping anything under 2 m. Both of those break a kerb that is physically
    one thing into a chain of unequal lines, which is what Keith reported: 154 breaks over 276 m
    on `mosque` and 186 over 292 m on `junction-1`, half of them those two constants and half of
    them gaps on a *lane's* own edge that were never candidates at all.

    So the candidate is every ring of the road network (`_kerb_rings`), less only what is already
    painted, and each surviving arc is pushed into the line it meets rather than held off it.
    Nothing is rejected for being short; the two things that must not be drawn are rejected for
    what they are - an arc lying inside the carriageway, and a bar across the end of a road.

    **And the ring is taken round the road with its seams closed**, which is the third attempt and
    the reason for `_KERB_GAP_CLOSE_M`. Traced round the raw union, the ring dives into the notch
    between two surfaces that fail to meet and comes back out along its other wall, so both walls
    get painted: 238 of `mosque`'s 408 lines and 140 of `junction-1`'s 284 were marks lying on open
    tarmac. Keith: *"it's adding the edges between the lanes as well… I just need it on either
    side."*

    Additive by construction: existing features are read for their geometry and none is changed.
    Every line MetaDrive draws also gets a ghost body, and a solid one sets
    `on_white_continuous_line`, which is why nothing here may land on drivable road.
    """
    surfaces: list[Polygon] = []
    lines: list[LineString] = []
    for feature in features.values():
        if feature["type"] == _LANE_TYPE:
            shape = _surface(np.asarray(feature["polygon"], dtype=np.float64))
            if shape is not None:
                surfaces.append(shape)
        elif len(feature.get("polyline", ())) >= 2:
            lines.append(LineString(feature["polyline"]))
    if not surfaces:
        return {}, 0

    road = _road_union(surfaces)
    # Traced round the road with its seams closed up, but judged against the real surfaces: what
    # `_KERB_INSET_M` must not let a line onto is tarmac a car drives on, and sealing a notch does
    # not make one. See `_KERB_GAP_CLOSE_M` for why the seams have to go before the ring is taken.
    candidate = unary_union(_kerb_rings(_closed(road)))
    if lines:
        candidate = candidate.difference(
            unary_union(lines).buffer(_KERB_PAINT_ALLOWANCE_M)
        )
    if candidate.is_empty:
        return {}, 0

    # Only a `MultiLineString` may be merged - `linemerge` refuses a lone closed ring, which is
    # what a road nobody has painted yet comes out as. Anything else is already one piece.
    if isinstance(candidate, MultiLineString):
        candidate = linemerge(candidate)
    arcs = [
        part
        for part in (candidate.geoms if hasattr(candidate, "geoms") else [candidate])
        if isinstance(part, LineString) and not part.is_empty
    ]
    paint = STRtree(lines) if lines else None
    inside = road.buffer(-_KERB_INSET_M)

    kerbs: dict[str, dict[str, Any]] = {}
    road_ends = 0
    for arc in arcs:
        if arc.is_empty or arc.length < _MIN_KERB_M:
            continue
        # Before the crease cut, because a road end is a straight bar and cutting it in half would
        # leave two pieces that are each too short to recognise as one.
        if (
            paint is not None
            and arc.length < _MAX_ROAD_END_M
            and _end_squareness(arc, paint, lines) < _ROAD_END_SQUARENESS
        ):
            road_ends += 1
            continue
        for points in _uncreased(arc):
            if len(points) < 2 or LineString(points).length < _MIN_KERB_M:
                continue
            points = _extended(points)
            line = LineString(points)
            if line.interpolate(0.5, normalized=True).within(inside):
                continue
            # A kerb has road on one side of it and not the other. Where a slot between two
            # surfaces is too wide for `_KERB_GAP_CLOSE_M` to seal, its two walls are both on the
            # ring and both look like road edge, and this is what says they are not.
            if _road_on_both_sides(line, road):
                continue
            # Keyed on the whole line, because where it is is the only thing about it that is
            # stable: it is merged from however many turns happen to meet there and belongs to
            # no one of them. Its ends and its length are not enough - two arcs on either side
            # of the same seam run between the same two points and are the same length, and at
            # a shorter `_MIN_KERB_M` a pair of them collided. Reversed if it runs backwards, so
            # the id does not follow the direction `linemerge` happened to pick.
            shape = points if tuple(points[0]) <= tuple(points[-1]) else points[::-1]
            identifier = deterministic_id(
                "junction_kerb", *(f"{value:.3f}" for value in shape.flatten())
            )
            # Same id means same coordinates, which means the same line twice - the two halves a
            # needle is cut into are one segment traversed each way. Dropped rather than raised
            # for the reason `_divider_boundaries` drops a duplicated divider: two copies of one
            # line resample out of phase and draw as something neither of them is.
            kerbs.setdefault(identifier, {"type": _BOUNDARY_TYPE, "polyline": points})
    return kerbs, road_ends


def _map_features(
    model: PreliminaryLaneModel,
    neighbours: Mapping[str, tuple[list[str], list[str]]],
    moves: Mapping[str, list[str]],
) -> tuple[dict[str, dict[str, Any]], set[str], int, int]:
    """The map features, which are kerbs, how many road ends are bare, how many surfaces grew.

    The second element is not a nicety. `metadata.lane_markings` counts edges and merged copies
    **by feature type**, so a kerb line - a `ROAD_EDGE_BOUNDARY` belonging to no lane - would
    inflate one count and drive the other negative if the caller could not tell the two apart.

    The third is reported for the opposite reason: a bar of bare edge across the end of a road is
    a deliberate blank, and a blank nothing counts reads as a gap in the numbers.

    The fourth is the blast radius of `_sealed_surfaces` - the only step in this file that changes
    a feature rather than adding one, so the number a reader needs is how many it changed.
    """
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

    # `_divider_boundaries` runs over every lane, stubs included, and must: it decides a line's
    # style from its neighbours, so hiding the stubs from it would change a *surviving*
    # neighbour's dashes. The stubs are dropped when the features are written, not before.
    dividers, superseded = _divider_boundaries(model, moves)
    stubs = _stub_lanes(model)
    for lane in model.lanes:
        for boundary in lane.boundaries:
            if boundary.identifier in features:
                raise ConversionError(
                    f"boundary {boundary.identifier} on lane {lane.identifier} shares an "
                    "id with another map feature"
                )
            if boundary.identifier in superseded or lane.identifier in stubs:
                continue
            features[boundary.identifier] = {
                "type": (
                    _DIVIDER_TYPE if boundary.identifier in dividers else _BOUNDARY_TYPE
                ),
                "polyline": _polyline(boundary.points),
                "side": boundary.side,
                "lane_id": lane.identifier,
            }

    # The holes between surfaces are closed before the kerb is traced, and it matters that it is
    # this way round: the kerb runs along the road's edge, and until the tarmac is whole the
    # road's edge includes the walls of every gap in it.
    sealed = _sealed_surfaces(features)

    # Last, and over the features the loops above decided rather than over the model: the kerb is
    # drawn where the road has an edge and no line covers it, so it has to see every surface that
    # was written and every line that was written, which is exactly what `features` now holds.
    kerbs, road_ends = _junction_kerb_boundaries(features)
    for kerb_id, feature in kerbs.items():
        if kerb_id in features:
            raise ConversionError(
                f"junction kerb {kerb_id} shares an id with another map feature"
            )
        features[kerb_id] = feature
    return features, set(kerbs), road_ends, sealed


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
    features, kerbs, road_ends, sealed = _map_features(model, neighbours, moves)
    # Counted here, not inferred from the gap between what the model holds and what was
    # written: `merged` is that gap, and letting the two reasons for a missing boundary share
    # one number would report suppression as deduplication.
    stubs = _stub_lanes(model)
    stub_boundaries = sum(
        len(lane.boundaries) for lane in model.lanes if lane.identifier in stubs
    )

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
                # Both of these count a lane's own markings, so both exclude the kerb lines: a
                # kerb is a `ROAD_EDGE_BOUNDARY` too, and left in it would inflate `edges` and
                # drive `merged` - which is a difference against what the model holds - negative.
                "edges": sum(
                    1
                    for identifier, item in features.items()
                    if item["type"] == _BOUNDARY_TYPE and identifier not in kerbs
                ),
                # Boundaries the model holds, less the boundaries actually written, less the
                # ones `junction_stubs` accounts for. Counted by type rather than as
                # `len(features) - len(lanes)`, which quietly stopped meaning "boundaries" once
                # the junction turns became features too.
                "merged": sum(len(lane.boundaries) for lane in model.lanes)
                - stub_boundaries
                - sum(
                    1
                    for identifier, item in features.items()
                    if item["type"] in (_BOUNDARY_TYPE, _DIVIDER_TYPE)
                    and identifier not in kerbs
                ),
                # Markings left unpainted because their lane is a clamped trim sitting inside a
                # junction - see `_stub_lanes`. Reported rather than folded into `merged`: a
                # dropped duplicate and a deliberately blank junction are different facts, and
                # this one is a choice a reader should be able to see us making.
                "junction_stubs": stub_boundaries,
                # Kerb lines drawn round the junctions themselves - see
                # `_junction_kerb_boundaries`. Its own count for the same reason
                # `junction_stubs` has one: these belong to no lane, and folding them into
                # `edges` would say a lane had markings it does not have.
                "junction_kerbs": len(kerbs),
                # Places where a road simply stops - at the edge of the extract, or a dead end -
                # leaving a bar of bare edge straight across the carriageway. Deliberately not
                # painted, because a line there draws as a stop line where there is none. Counted
                # so the blank is a stated fact rather than a hole in the numbers.
                "road_ends_unpainted": road_ends,
                # Lane surfaces grown to swallow a hole in the tarmac beside them - see
                # `_sealed_surfaces`. Not a marking, but it belongs beside these: every other
                # number here counts a line that was or was not drawn, and this counts the road
                # those lines are drawn on being made whole first.
                "surfaces_sealed": sealed,
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
    speed_kph: float | None = None,
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

    `speed_kph` is the speed the recorded car cruises at, and overrides the road's own posted
    limit, which is what it uses when this is `None`. It exists because the limit is the ceiling
    on how quick a drive can be made to look: the profile can be freed to corner and accelerate
    hard, but a car obeying a 50 km/h road cannot beat 50 km/h over the route however it is
    tuned. Passing a number here is the only way past that, and it is a per-dataset decision
    rather than a property of the map, which is why it is an argument and not a config field.

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
                speed_kph=speed_kph,
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
