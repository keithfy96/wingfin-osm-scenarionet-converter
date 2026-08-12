# ruff: noqa: E501
"""Stage 2 automatic lane-geometry generation."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Any

import osmnx as ox
from pyproj import Transformer
from shapely.geometry import LineString, MultiLineString, MultiPolygon, Polygon
from shapely.ops import linemerge, substring

from osm_scenario.config import ConverterConfig
from osm_scenario.ids import deterministic_id
from osm_scenario.lane_model import (
    ConnectorFeature,
    FindingLocation,
    FindingSource,
    GenerationMetadata,
    GeoPoint,
    LaneBoundary,
    LaneFeature,
    Point2D,
    PreliminaryLaneModel,
    RestrictionEffect,
    ReviewFinding,
    SignalAssociation,
    StopLine,
)
from osm_scenario.osm_source import (
    ONEWAY_VALUES,
    OsmRelation,
    OsmSnapshot,
    read_osm_snapshot,
    single_lane_implies_oneway,
    way_terminus_nodes,
)
from osm_scenario.topology import (
    BEND_FILLET_MIN_DEGREES,
    MovementCandidate,
    classify_movement,
    connector_curve,
    forbidden_by_node_restriction,
    movement_family,
    movement_matches,
    movement_side,
    restriction_roles,
    side_lane_index,
    signed_turn_angle,
    tagged_movement_side,
    uturn_evidence_status,
    via_way_resolution,
    way_adjacency,
)

GENERATOR_VERSION = "direct-osm-stage2-v19"
LANE_MODEL_SCHEMA_VERSION = 3

# How much clear road to leave beyond the crossing carriageway when a lane is cut back at a
# junction. Without it a lane stops exactly on the far kerb line of the road it crosses, which
# leaves the turn no room to round its corner. Not surveyed and deliberately not on
# `ConverterConfig`: `configuration_checksum` feeds `generation_fingerprint`, so a field there
# would invalidate the Stage 3 review every time this were touched.
JUNCTION_CORNER_ALLOWANCE_M = 1.5

# What a lane must keep after both ends are cut. Short link ways between two close junctions can
# be shorter than the two setbacks together; trimming those to nothing would delete a road. When
# the clamp binds, both setbacks shrink in proportion and the lane is reported.
MIN_TRIMMED_LANE_M = 2.0

# `BEND_FILLET_MIN_DEGREES` is imported from `topology`: `ego_route` needs the same number to
# tell a deliberately parted join from a hole, and one definition is the only way they agree.
#
# The radius the fillet curve is fitted to. The setback each side is `R * tan(bend / 2)`, which is the
# tangent length of a circular arc of this radius - 1.0 m at 10 degrees, 6.0 m at 53. Capped so
# a hairpin between two long ways cannot eat the road either side of it.
BEND_FILLET_RADIUS_M = 12.0
BEND_FILLET_MAX_SETBACK_M = 8.0

# One outgoing carriageway at a node: its OSM way and the directed edge it leaves on.
GroupKey = tuple[str, tuple[str, ...]]

OPPOSITE_DIRECTION = {"forward": "backward", "backward": "forward"}


class GenerationError(RuntimeError):
    """Raised when Stage 2 inputs are unsafe or generation cannot complete."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_checksum(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _positive_int(value: object) -> int | None:
    try:
        result = int(str(value))
    except (TypeError, ValueError):
        return None
    return result if result > 0 else None


def _positive_float(value: object) -> float | None:
    if value is None:
        return None
    text = str(value).strip().lower().replace("meters", "").replace("meter", "").strip()
    try:
        result = float(text)
    except ValueError:
        return None
    return result if math.isfinite(result) and result > 0 else None


def _speed_kph(value: object) -> float | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    factor = 1.609344 if "mph" in text else 1.0
    number = text.replace("km/h", "").replace("kph", "").replace("mph", "").strip()
    try:
        result = float(number) * factor
    except ValueError:
        return None
    return result if math.isfinite(result) and result > 0 else None


def _way_ids(data: dict[str, Any]) -> list[str]:
    value = data.get("osmid")
    if isinstance(value, (list, tuple, set)):
        return sorted(str(item) for item in value)
    return [str(value)] if value is not None else []


def _edge_geometry(graph: Any, u: Any, v: Any, data: dict[str, Any]) -> LineString:
    geometry = data.get("geometry")
    if geometry is None:
        geometry = LineString(
            [
                (float(graph.nodes[u]["x"]), float(graph.nodes[u]["y"])),
                (float(graph.nodes[v]["x"]), float(graph.nodes[v]["y"])),
            ]
        )
    if not isinstance(geometry, LineString) or geometry.is_empty or geometry.length <= 0:
        raise GenerationError(f"edge {u}->{v} has invalid line geometry")
    start = (float(graph.nodes[u]["x"]), float(graph.nodes[u]["y"]))
    if math.dist(geometry.coords[0], start) > math.dist(geometry.coords[-1], start):
        geometry = LineString(reversed(geometry.coords))
    return geometry


def _edge_direction(way: Any, u: str, v: str) -> str:
    pairs = list(zip(way.node_ids, way.node_ids[1:], strict=False))
    return "forward" if (u, v) in pairs else "backward"


def _carries_whole_carriageway(
    tags: dict[str, str], *, one_way_in_graph: bool = False
) -> bool:
    """True when one directed edge holds every lane the way has.

    A two-way way splits its lanes between two directed edges that offset to
    opposite sides of the OSM centreline. A one-way way has only one edge, so
    its centreline is the centre of the carriageway itself.

    `one_way_in_graph` is Stage 1's answer for a `lanes=1` way that carried no `oneway`
    tag (`osm_source.single_lane_implies_oneway`, applied only where it costs nothing).
    It has to be asked separately because the tags are not what changed — Stage 1 edits
    the graph and never the source OSM, which is acquisition evidence. Without it the
    surviving lane would be offset half a lane off the road's centre, balancing against
    an oncoming block that is no longer there, and its count would still be read as an
    inference rather than as the whole carriageway.
    """
    return (
        one_way_in_graph
        or tags.get("oneway") in ONEWAY_VALUES
        or tags.get("junction") == "roundabout"
    )


def _single_direction_ways(graph: Any, snapshot: OsmSnapshot) -> frozenset[str]:
    """Ways that `single_lane_implies_oneway` matches and the graph runs one way.

    Read back off the graph rather than out of the manifest, so generation cannot
    disagree with the stage that made the decision: where the guard refused, both
    directions are still here and this returns nothing for that way, which is exactly
    the no-op the refusal asked for.
    """
    directions: dict[str, set[tuple[str, str]]] = {}
    for u, v, data in graph.edges(data=True):
        for way_id in _way_ids(data):
            directions.setdefault(way_id, set()).add((str(u), str(v)))
    return frozenset(
        way_id
        for way_id, seen in directions.items()
        if way_id in snapshot.ways
        and single_lane_implies_oneway(snapshot.ways[way_id].tags)
        and not any((v, u) in seen for u, v in seen)
    )


def _lane_offset(
    lane_index: int, *, lane_count: int, width: float, side_sign: float, centred: bool
) -> float:
    """Lateral offset of a generated lane from the OSM way centreline.

    A two-way way puts each direction's block wholly on its own side, so the two
    blocks straddle the centreline. A one-way carriageway has no opposite block to
    balance against and is centred on the line instead. `side_sign` keeps lane index 0
    offside and index `lane_count - 1` kerbside in both cases.
    """
    return side_sign * (lane_index + 0.5 - (0.5 * lane_count if centred else 0.0)) * width


def _as_line(offset: Any, fallback: LineString) -> LineString:
    """One `LineString` from whatever `offset_curve` returned.

    Shapely can hand back a `MultiLineString` for an offset that is geometrically a single
    unbroken line — a trimmed centreline whose interior vertex is collinear is enough to
    trigger it. `linemerge` stitches those pieces back together; anything it cannot join is a
    genuinely broken offset, and the longest piece is the lane's own side of it.
    """
    if isinstance(offset, LineString):
        return offset if not offset.is_empty else fallback
    if isinstance(offset, MultiLineString):
        if offset.is_empty:
            return fallback
        merged = linemerge(offset)
        if isinstance(merged, LineString) and not merged.is_empty:
            return merged
        return max(offset.geoms, key=lambda part: part.length)
    return fallback


def _lane_surface(center: LineString, width: float) -> tuple[Polygon, LineString, LineString]:
    """Derive a lane's drawn extent from its centreline."""
    return (
        center.buffer(width / 2, cap_style="flat", join_style="mitre"),
        _as_line(center.offset_curve(width / 2, join_style="mitre"), center),
        _as_line(center.offset_curve(-width / 2, join_style="mitre"), center),
    )


def _wrap(radians: float) -> float:
    """An angle difference folded into (-pi, pi]."""
    return math.atan2(math.sin(radians), math.cos(radians))


def _node_setbacks(
    graph: Any,
    snapshot: Any,
    config: ConverterConfig,
    single_direction: frozenset[str] = frozenset(),
) -> dict[str, float]:
    """How far short of each junction node an arriving lane should stop.

    OSM puts one node at the centre of an intersection and every way runs to it, so a lane
    generated over the whole edge ends in the middle of the junction — and so does every other
    lane at that node. That leaves the connector nothing to span but the lateral offset between
    two overlapping lane ends, which is why turns came out 1.7 m long pointing sideways.

    The setback is half the widest carriageway meeting at the node, which is where that road's
    far kerb is, plus a corner allowance. One value per node rather than one per approach: the
    junction is a single region, and a square box keeps a wide road and a narrow one agreeing
    about where it ends.

    Only nodes with more than two distinct neighbours are junctions. A node that merely splits
    one road into two ways has exactly two, and trimming there would tear a straight road in
    half — which is why the test is on neighbours rather than on edge count, since a two-way
    road already puts four directed edges on every node along it.
    """
    widths: dict[str, float] = {}
    adjacency: dict[str, set[str]] = {}
    arriving: dict[str, list[float]] = {}
    leaving: dict[str, list[float]] = {}
    for u, v, _key, data in graph.edges(keys=True, data=True):
        way_ids = _way_ids(data)
        if not way_ids:
            continue
        way = snapshot.ways.get(way_ids[0])
        if way is None:
            continue
        try:
            line = _edge_geometry(graph, u, v, data)
        except GenerationError:
            line = None
        if line is not None and len(line.coords) >= 2:
            head = line.coords[:2]
            tail = line.coords[-2:]
            leaving.setdefault(str(u), []).append(
                math.atan2(head[1][1] - head[0][1], head[1][0] - head[0][0])
            )
            arriving.setdefault(str(v), []).append(
                math.atan2(tail[1][1] - tail[0][1], tail[1][0] - tail[0][0])
            )
        one_way = way.identifier in single_direction
        total_lanes = _positive_int(way.tags.get("lanes"))
        if total_lanes is None:
            forward, _, _ = _directional_lane_count(
                way.tags, "forward", one_way_in_graph=one_way
            )
            if _carries_whole_carriageway(way.tags, one_way_in_graph=one_way):
                total_lanes = forward
            else:
                backward, _, _ = _directional_lane_count(
                    way.tags, "backward", one_way_in_graph=one_way
                )
                total_lanes = forward + backward
        width_total = _positive_float(way.tags.get("width")) or (
            config.lane_width_defaults.vehicle * max(total_lanes, 1)
        )
        for node in (str(u), str(v)):
            widths[node] = max(widths.get(node, 0.0), width_total)
            adjacency.setdefault(node, set())
        adjacency[str(u)].add(str(v))
        adjacency[str(v)].add(str(u))
    setbacks: dict[str, float] = {}
    for node, neighbours in adjacency.items():
        if len(neighbours) > 2:
            setbacks[node] = widths.get(node, 0.0) / 2.0 + JUNCTION_CORNER_ALLOWANCE_M
            continue
        if len(neighbours) != 2:
            continue
        # A through node. The sharpest turn any car takes here is the bend to fillet: with a
        # two-way road there are two arriving and two leaving headings, and the reverse pairing
        # is a U-turn nobody drives, so pair each arrival with the leaving heading closest to it.
        bend = 0.0
        for incoming in arriving.get(node, ()):
            outgoing = leaving.get(node, ())
            if not outgoing:
                continue
            bend = max(
                bend,
                min(abs(math.degrees(_wrap(angle - incoming))) for angle in outgoing),
            )
        if bend < BEND_FILLET_MIN_DEGREES or bend >= 180.0:
            continue
        setbacks[node] = min(
            BEND_FILLET_RADIUS_M * math.tan(math.radians(bend) / 2.0),
            BEND_FILLET_MAX_SETBACK_M,
        )
    return setbacks


def _trimmed_edge(line: LineString, start_m: float, end_m: float) -> tuple[LineString, bool]:
    """`line` cut back by `start_m` at its head and `end_m` at its tail.

    Returns the trimmed line and whether the clamp bound. Both cuts are interpolated rather
    than snapped to a vertex, because a generated lane usually has exactly two points and
    snapping would collapse it onto one of its ends.
    """
    total = line.length
    wanted = max(start_m, 0.0) + max(end_m, 0.0)
    if wanted <= 0:
        return line, False
    room = total - MIN_TRIMMED_LANE_M
    clamped = wanted > room
    if clamped:
        scale = max(room, 0.0) / wanted
        start_m *= scale
        end_m *= scale
    start_m = max(start_m, 0.0)
    end_m = max(end_m, 0.0)
    if start_m + end_m <= 0:
        return line, clamped
    trimmed = substring(line, start_m, total - end_m)
    if not isinstance(trimmed, LineString) or trimmed.is_empty or trimmed.length <= 0:
        return line, True
    return trimmed, clamped


def _tapered_line(
    line: LineString, *, at_end: bool, target: tuple[float, float], taper_length: float
) -> LineString:
    """Blend one end of a centreline onto `target` without kinking the rest of it.

    Displacement grows linearly from nothing at the hinge to the full offset at the
    end being moved, so the lane bends across the taper instead of stepping sideways.
    """
    coords = [(x, y) for x, y in line.coords]
    stations = [0.0]
    for before, after in zip(coords, coords[1:], strict=False):
        stations.append(stations[-1] + math.dist(before, after))
    length = stations[-1]
    if length <= 0:
        return line
    span = min(taper_length, length)
    anchor = length - span if at_end else span
    # A straight lane has only two vertices, so without a hinge the blend would spread
    # over its whole length instead of the taper distance.
    if 0.0 < anchor < length and all(abs(station - anchor) > 1e-9 for station in stations):
        index = next(i for i, station in enumerate(stations) if station > anchor)
        before, after = coords[index - 1], coords[index]
        ratio = (anchor - stations[index - 1]) / (stations[index] - stations[index - 1])
        coords.insert(
            index,
            (
                before[0] + (after[0] - before[0]) * ratio,
                before[1] + (after[1] - before[1]) * ratio,
            ),
        )
        stations.insert(index, anchor)
    origin = coords[-1] if at_end else coords[0]
    shift_x, shift_y = target[0] - origin[0], target[1] - origin[1]
    moved = []
    for (x, y), station in zip(coords, stations, strict=True):
        weight = (station - anchor) / span if at_end else (anchor - station) / span
        weight = min(1.0, max(0.0, weight))
        moved.append((x + shift_x * weight, y + shift_y * weight))
    return LineString(moved)


def _merge_taper_plan(
    connectors: list[ConnectorFeature],
    lane_lookup: dict[str, LaneFeature],
    *,
    min_gap: float,
) -> dict[tuple[str, str], tuple[float, float]]:
    """Decide which lane ends should be pulled onto the lane they merge into.

    A lane must not stop on another road's centreline, where OSM ends its way; it has
    to reach the lane it enters or it points at through traffic. Only a shallow
    `through` movement is a merge or diverge — a real turn ends at a stop line and its
    connector curve is already the right shape. The side with fewer lanes yields, so
    the through carriageway is never bent, and an even split is left alone because
    there is nothing to choose between the two.

    A connector's status answers whether the movement is right; where a lane's free end
    sits is a different question, and the junction node is the one answer that is never
    right. So review only withholds a decision, it does not park a lane on a centreline
    until someone makes one. A forbidden movement is the exception: it does not exist,
    so it may not drag geometry.
    """
    candidates: list[tuple[tuple[str, str], tuple[str, str], tuple[float, float], int]] = []
    for connector in sorted(connectors, key=lambda item: item.identifier):
        if connector.status == "forbidden" or connector.movement != "through":
            continue
        source = lane_lookup[connector.from_lane_id]
        target = lane_lookup[connector.to_lane_id]
        leaves, enters = source.centerline[-1], target.centerline[0]
        if math.dist((leaves.x, leaves.y), (enters.x, enters.y)) <= min_gap:
            continue
        rank = 0 if connector.status == "active" else 1
        if source.lane_count < target.lane_count:
            candidates.append(
                (
                    (source.identifier, "end"),
                    (target.identifier, "start"),
                    (enters.x, enters.y),
                    rank,
                )
            )
        elif target.lane_count < source.lane_count:
            candidates.append(
                (
                    (target.identifier, "start"),
                    (source.identifier, "end"),
                    (leaves.x, leaves.y),
                    rank,
                )
            )
    # A decided movement outranks one still awaiting review, and where the best rank an
    # endpoint has still names two places it cannot be in both: leave it where OSM put
    # it rather than settle a real disagreement on whichever connector ID sorted first.
    best_rank: dict[tuple[str, str], int] = {}
    for subject, _, _, rank in candidates:
        best_rank[subject] = min(rank, best_rank.get(subject, rank))
    contested = {
        subject
        for subject in best_rank
        if len(
            {
                destination
                for other, _, destination, rank in candidates
                if other == subject and rank == best_rank[subject]
            }
        )
        > 1
    }
    moving = {subject for subject, _, _, _ in candidates} - contested
    plan: dict[tuple[str, str], tuple[float, float]] = {}
    for subject, anchor, destination, rank in candidates:
        # Chasing an endpoint that is itself moving would reopen the gap it closed.
        if subject not in contested and anchor not in moving and rank == best_rank[subject]:
            plan.setdefault(subject, destination)
    return plan


def _directional_lane_count(
    tags: dict[str, str], direction: str, *, one_way_in_graph: bool = False
) -> tuple[int, str, str]:
    explicit = _positive_int(tags.get(f"lanes:{direction}"))
    if explicit is not None:
        return explicit, "explicit_directional", "high"
    total = _positive_int(tags.get("lanes"))
    oneway = _carries_whole_carriageway(tags, one_way_in_graph=one_way_in_graph)
    if oneway and total is not None:
        return total, "explicit_total_oneway", "high"
    opposite = _positive_int(tags.get(f"lanes:{OPPOSITE_DIRECTION[direction]}"))
    if total is not None and opposite is not None:
        if total > opposite:
            return total - opposite, "complementary_directional", "high"
        return 1, "contradictory_directional_total", "low"
    if total is not None:
        count = max(1, total // 2)
        confidence = "medium" if total % 2 == 0 else "low"
        return count, "inferred_from_total", confidence
    return 1, "default_single_lane", "low"


def _signal_association(
    *, approaching: list[str], released: list[str], is_terminus: bool
) -> tuple[list[str], str | None, str, str]:
    """Which lanes a signal governs, and what the reviewer must be told about it.

    Returns `(lane_ids, finding severity or None, confidence, reason)`.

    A signal governs the traffic coming *up* to it, so a lane ending at the node is the
    association whenever one exists, and nothing needs reviewing.

    Where none exists and the node terminates every way through it, the extract was cut at
    the signal: the approach is outside the map, and no amount of review can produce one.
    What the map does hold is the lanes the signal *releases*, and naming those is both
    true and useful - it is where a vehicle entering this scenario is held. It is still an
    inference, so it is still raised, as a warning.

    Where none exists and a source way runs *through* the node, the road is there and the
    lane that should end at it is not. That is a defect, not an edge, and it stays a
    blocker with no association: `apply_review._decision_is_satisfied` treats "mapped" as
    the reviewer's answer having been met, so associating a guess here would let accepting
    close a question the generator cannot answer.
    """
    if approaching:
        return approaching, None, "high", ""
    if released and is_terminus:
        return (
            released,
            "warning",
            "medium",
            "signal is at the edge of the extract - no lane in this map approaches it, "
            "so it is associated with the lanes it releases",
        )
    if released:
        return (
            [],
            "blocker",
            "low",
            "no lane ends at this signal although a source way runs through the node",
        )
    return [], "blocker", "low", "signal has no generated approaching lane"


def _turn_permissions(
    tags: dict[str, str],
    direction: str,
    lane_index: int,
    lane_count: int,
    driving_side: str,
) -> list[str]:
    value = tags.get(f"turn:lanes:{direction}") or tags.get("turn:lanes")
    if not value:
        return []
    lanes = value.split("|")
    tag_index = lane_count - 1 - lane_index if driving_side == "left" else lane_index
    if tag_index >= len(lanes):
        return []
    return sorted(item.strip() for item in lanes[tag_index].split(";") if item.strip())


def _is_exact_reverse(source: LaneFeature, target: LaneFeature) -> bool:
    return (
        source.source_edge[0] == target.source_edge[1]
        and source.source_edge[1] == target.source_edge[0]
    )


def _tagged_side_block(
    approach: list[LaneFeature], side: str, *, driving_side: str
) -> list[LaneFeature]:
    """The lanes of an approach that an explicit `turn:lanes` puts on `side`.

    Only tagged lanes form a block. Where the approach carries no `turn:lanes`,
    `_side_filtered_candidates` already leaves the side-most lane alone with the
    movement, so treating its neighbours as part of a block would deal them into lanes
    they never reach.
    """
    return [
        lane
        for lane in approach
        if tagged_movement_side(lane.turn_permissions, driving_side) == side
    ]


def _side_block_offset(source: LaneFeature, side: str, block: list[LaneFeature]) -> int:
    """How far inside the block's leading lane this lane sits, in lane widths.

    A block is dealt from its side inward, so the ordering has to start at that side:
    kerbside-first for a nearside block, centreline-first for an offside one.
    """
    ordered = sorted(block, key=lambda item: item.lane_index, reverse=side == "nearside")
    for offset, lane in enumerate(ordered):
        if lane.identifier == source.identifier:
            return offset
    return 0


def _mapped_lane_index(
    source: LaneFeature,
    target_count: int,
    side: str | None = None,
    side_block: list[LaneFeature] | None = None,
) -> int:
    """Choose which lane of the outgoing group a movement lands in.

    Indices run centre-out, so index 0 is the lane against the centreline (the offside
    lane) and `target_count - 1` is the kerbside one. A movement that leaves toward the
    kerb therefore enters the last index and one toward the centre enters index 0;
    without a side, lane order is preserved proportionally.

    A side says where a block of lanes *starts*, not where every lane in it goes. Two
    lanes both tagged `turn:lanes=right` are both offside-bound, and answering `0` for
    each puts two streams of traffic in one lane and starves the one beside it. So the
    side fixes the leading index and the rest of the block follows it inward, keeping
    lane order. Where the destination has no room the clamp collapses them as before,
    and `lane_transition_count_mismatch` reports the sharing rather than hiding it.
    """
    if side in {"nearside", "offside"}:
        start = side_lane_index(side, target_count)
        offset = _side_block_offset(source, side, side_block) if side_block else 0
        step = -1 if side == "nearside" else 1
        return max(0, min(target_count - 1, start + step * offset))
    if source.lane_count > 1 and target_count > 1:
        return round(source.lane_index * (target_count - 1) / (source.lane_count - 1))
    return min(source.lane_index, target_count - 1)


def _approach_blocks(
    incoming: list[str], lane_lookup: dict[str, LaneFeature]
) -> list[list[LaneFeature]]:
    """Group the lanes arriving at a node by the directed edge they arrive on."""
    blocks: dict[tuple[str, ...], list[LaneFeature]] = {}
    for lane_id in incoming:
        lane = lane_lookup[lane_id]
        blocks.setdefault(tuple(lane.source_edge), []).append(lane)
    for block in blocks.values():
        block.sort(key=lambda item: item.lane_index)
    return [blocks[key] for key in sorted(blocks)]


def _kerb_first_key(source: LaneFeature, target: LaneFeature, driving_side: str) -> float:
    """Rank a movement by how far it turns toward the kerb, most kerbward first."""
    angle = signed_turn_angle(
        LineString((point.x, point.y) for point in source.centerline),
        LineString((point.x, point.y) for point in target.centerline),
    )
    # Positive angles turn left, which is the kerb side only where traffic drives on
    # the left, so the ordering follows the country rather than the screen.
    return -angle if driving_side == "left" else angle


def _balanced_approach_assignment(
    approach: list[LaneFeature],
    outgoing_groups: dict[GroupKey, list[LaneFeature]],
    *,
    driving_side: str,
) -> dict[GroupKey, dict[str, str]] | None:
    """Deal an approach's lanes across its destinations when the arithmetic closes.

    A lane that peels off cannot also be the straight-on lane. Where the destinations
    of an approach hold exactly as many lanes as the approach brings, every lane has
    one destination and there is nothing left to infer, so deal them kerb first: the
    link leaving toward the kerb is fed by the kerbside lane and the rest carry on in
    order. Returns None when the counts do not close, because a lane then really does
    serve more than one movement and the proportional mapping still decides.
    """
    source = approach[0]
    groups = {
        key: targets
        for key, targets in outgoing_groups.items()
        if not _is_exact_reverse(source, targets[0])
    }
    if not groups or len(approach) != source.lane_count:
        return None
    if sum(len(targets) for targets in groups.values()) != source.lane_count:
        return None
    def kerb_first(item: tuple[GroupKey, list[LaneFeature]]) -> tuple[float, GroupKey]:
        key, targets = item
        return (_kerb_first_key(source, targets[0], driving_side), key)

    # Lane indices run centre-out, so the highest index is the kerbside lane.
    remaining = sorted(approach, key=lambda item: -item.lane_index)
    assignment: dict[GroupKey, dict[str, str]] = {}
    for key, targets in sorted(groups.items(), key=kerb_first):
        taken, remaining = remaining[: len(targets)], remaining[len(targets) :]
        assignment[key] = {
            lane.identifier: target.identifier
            for lane, target in zip(
                taken, sorted(targets, key=lambda item: -item.lane_index), strict=True
            )
        }
    return assignment


def _balanced_merge_assignment(
    approaches: list[list[LaneFeature]],
    outgoing_groups: dict[GroupKey, list[LaneFeature]],
    *,
    driving_side: str,
) -> dict[tuple[str, ...], dict[GroupKey, dict[str, str]]]:
    """Deal several approaches into the one carriageway they all join.

    The mirror of `_balanced_approach_assignment`. There, one approach apportions its
    lanes across several destinations; here several approaches apportion themselves
    across one. Both say the same thing: no lane may vanish and none may be shared.
    Requiring every approach to have this destination and only this one is what keeps
    the allocation unambiguous — at an ordinary crossroads each approach has several
    destinations, so nothing fires. Returns an empty mapping unless the approaches'
    lanes fill the destination exactly, leaving the proportional mapping to decide.
    """
    if len(approaches) < 2:
        return {}
    destination: GroupKey | None = None
    for approach in approaches:
        live = [
            key
            for key, targets in outgoing_groups.items()
            if not _is_exact_reverse(approach[0], targets[0])
        ]
        if len(live) != 1 or len(approach) != approach[0].lane_count:
            return {}
        if destination is not None and live[0] != destination:
            return {}
        destination = live[0]
    if destination is None:
        return {}
    targets = outgoing_groups[destination]
    if sum(len(approach) for approach in approaches) != len(targets):
        return {}
    ordered = sorted(
        approaches,
        key=lambda approach: (
            _kerb_first_key(approach[0], targets[0], driving_side),
            tuple(approach[0].source_edge),
        ),
    )
    # The approach arriving from the kerb side takes the kerbside lanes; the rest keep
    # their order behind it, so a merging link never lands on top of a running lane.
    remaining = sorted(targets, key=lambda item: -item.lane_index)
    assignment: dict[tuple[str, ...], dict[GroupKey, dict[str, str]]] = {}
    for approach in ordered:
        taken, remaining = remaining[: len(approach)], remaining[len(approach) :]
        assignment[tuple(approach[0].source_edge)] = {
            destination: {
                lane.identifier: target.identifier
                for lane, target in zip(
                    sorted(approach, key=lambda item: -item.lane_index), taken, strict=True
                )
            }
        }
    return assignment


def _side_filtered_candidates(
    candidates: list[MovementCandidate],
    *,
    source: LaneFeature,
    driving_side: str,
    min_degrees: float,
    node_restrictions: list[OsmRelation],
    has_continuation: bool,
) -> list[MovementCandidate]:
    """Keep only the movements this source lane is on the correct side of the road for.

    A reverse candidate is left to the U-turn policy, a lane with an explicit
    `turn:lanes` direction keeps what its tags allow, and a candidate a node-via
    restriction forbids is kept so the restriction still has something to act on and
    stays visible for audit. If the filter would leave the lane with nowhere to go,
    the straightest movement is kept instead of stranding it — but a lane that
    carries straight on is not stranded, so a continuation disables that fallback.
    Without this every lane of an approach feeds the exit, which is what the
    side rule exists to prevent.
    """
    tagged = any(permission in {"left", "right"} for permission in source.turn_permissions)
    kept: list[MovementCandidate] = []
    removed: list[MovementCandidate] = []
    for candidate in candidates:
        side = movement_side(
            movement=candidate.movement,
            angle=candidate.angle_degrees,
            driving_side=driving_side,
            turn_permissions=source.turn_permissions,
            min_degrees=min_degrees,
        )
        wrong_side = (
            side is not None
            and not tagged
            and candidate.movement != "reverse"
            and source.lane_index != side_lane_index(side, source.lane_count)
        )
        if wrong_side and not any(
            forbidden_by_node_restriction(candidate, relation) for relation in node_restrictions
        ):
            removed.append(candidate)
        else:
            kept.append(candidate)
    if kept or not removed or has_continuation:
        return kept
    return [min(removed, key=lambda item: (abs(item.angle_degrees), item.to_lane_id))]


def _stranded_permission_fallback(
    candidates: list[MovementCandidate],
    removed: list[MovementCandidate],
    *,
    has_continuation: bool,
) -> MovementCandidate | None:
    """Return the movement to restore when `turn:lanes` rejected every candidate.

    `turn:lanes` is surveyed evidence for which movements are *allowed*; the movement
    class is inferred by binning a turn angle. A tag that matches nothing on offer is a
    disagreement between the two, and resolving it by dropping every candidate cuts the
    drivable network on the strength of a threshold constant. The straightest rejected
    movement is restored instead, on the same no-stranding rule the side filter uses.
    A lane that carries straight on has somewhere to go already, so it is not stranded.
    """
    if candidates or not removed or has_continuation:
        return None
    return min(removed, key=lambda item: (abs(item.angle_degrees), item.to_lane_id))


def _unproven_sharp_movement(
    candidate: MovementCandidate, *, source: LaneFeature, min_degrees: float
) -> bool:
    """True when a movement doubles back far enough to need the evidence a U-turn needs.

    `classify_movement` only calls a movement `reverse` past 145 degrees, so a turn a
    few degrees short of that escapes the U-turn policy and is asserted outright. The
    ramp nose at Kenanga is the case in point: the on-ramp doubles straight back down
    the off-ramp at 138 degrees. An explicit `turn:lanes` permission for the movement
    is positive evidence and settles it.
    """
    if candidate.movement == "reverse" or abs(candidate.angle_degrees) < min_degrees:
        return False
    return not any(
        movement_matches(permission, candidate.movement)
        for permission in source.turn_permissions
    )


def _source_refs(finding: ReviewFinding, snapshot: OsmSnapshot) -> tuple[set[str], set[str]]:
    """The OSM ways and nodes a finding points at.

    A relation carries no geometry of its own, so it contributes its member ways and
    nodes instead. Shared by the review payload and the finding location, so the two
    can never disagree about what a finding refers to.
    """
    ways: set[str] = set()
    nodes: set[str] = set()
    for identifier in finding.source_ids:
        if finding.source_type == "way":
            ways.add(identifier)
        elif finding.source_type == "node":
            nodes.add(identifier)
        elif finding.source_type == "relation":
            relation = snapshot.relations.get(identifier)
            for member in relation.members if relation else ():
                if member.member_type == "way":
                    ways.add(member.reference)
                elif member.member_type == "node":
                    nodes.add(member.reference)
    return ways, nodes


def _finding_location(finding: ReviewFinding, snapshot: OsmSnapshot) -> FindingLocation | None:
    """Where a finding is, in WGS84, with the referenced geometry copied in.

    OSM node positions are already WGS84, so this is a lookup and not a projection —
    the projected CRS the lane geometry uses never enters into it.

    The representative point is the middle node of the longest source polyline. It
    therefore always lies on real geometry, where the centre of the bounding box can
    fall off the road entirely on an L-shaped or curved way.

    Returns `None` when nothing resolves — a `source_type` of `edge` names graph
    edges, which have no OSM geometry, and a fabricated point would be worse than
    none for anything matching these against recorded positions.
    """
    ways, nodes = _source_refs(finding, snapshot)
    sources: list[FindingSource] = []
    for way_id in sorted(ways):
        way = snapshot.ways.get(way_id)
        if way is None:
            continue
        points = [
            GeoPoint(lat=node.latitude, lon=node.longitude)
            for node in (snapshot.nodes.get(reference) for reference in way.node_ids)
            if node is not None
        ]
        if points:
            sources.append(FindingSource(ref=f"way:{way_id}", coordinates=points))
    for node_id in sorted(nodes):
        node = snapshot.nodes.get(node_id)
        if node is None:
            continue
        sources.append(
            FindingSource(
                ref=f"node:{node_id}",
                coordinates=[GeoPoint(lat=node.latitude, lon=node.longitude)],
            )
        )
    if not sources:
        return None

    every = [point for source in sources for point in source.coordinates]
    anchor = max(sources, key=lambda source: len(source.coordinates)).coordinates
    middle = anchor[len(anchor) // 2]
    return FindingLocation(
        lat=middle.lat,
        lon=middle.lon,
        bbox=[
            min(point.lon for point in every),
            min(point.lat for point in every),
            max(point.lon for point in every),
            max(point.lat for point in every),
        ],
        sources=sources,
    )


# The angle band where `classify_movement` is choosing between through and a turn.
# A movement landing inside it is reported rather than asserted.
_BORDERLINE_TURN_BAND = (30.0, 40.0)

# How each ambiguity trigger reads when it is not the headline reason.
_CAUSE_LABELS = {
    "restriction_not_expressible": "a turn restriction that cannot be applied to one movement",
    "uturn_without_evidence": "U-turn without evidence",
    "unproven_sharp_movement": "sharp doubling back without a turn:lanes permission",
    "competing_movements": "competing movements in the same turn family",
    "borderline_angle": "borderline turn angle",
}


def _ambiguity_causes(
    candidate: MovementCandidate,
    *,
    source: LaneFeature,
    uturn_status: str,
    family_count: int,
    sharp_movement_min_degrees: float,
) -> tuple[str, ...]:
    """Which ambiguity triggers a movement fired, most decision-relevant first.

    These are exactly the conditions that have always set `ambiguous`; naming them is
    what lets a reviewer see why a movement is held. Several can fire on one movement
    and all are reported: a U-turn that also competes with another movement is two
    separate reasons to look at it, not one.
    """
    causes: list[str] = []
    if candidate.movement == "reverse" and uturn_status == "review_required":
        causes.append("uturn_without_evidence")
    if _unproven_sharp_movement(
        candidate, source=source, min_degrees=sharp_movement_min_degrees
    ):
        causes.append("unproven_sharp_movement")
    if family_count > 1:
        causes.append("competing_movements")
    low, high = _BORDERLINE_TURN_BAND
    if low <= abs(candidate.angle_degrees) <= high:
        causes.append("borderline_angle")
    return tuple(causes)


def _ambiguity_reason(candidate: MovementCandidate, *, sharp_movement_min_degrees: float) -> str:
    """One sentence naming why this movement needs review, from its headline cause."""
    causes = candidate.ambiguity_causes
    headline = causes[0] if causes else ""
    low, high = _BORDERLINE_TURN_BAND
    if headline == "restriction_not_expressible":
        text = (
            "a turn restriction forbids a route through this movement, but the movement also "
            "carries traffic the restriction does not name, so removing it is a judgement "
            "rather than a deduction"
        )
    elif headline == "uturn_without_evidence":
        text = (
            f"U-turn at {candidate.angle_degrees:.1f} degrees and nothing in the tags "
            "permits a U-turn here"
        )
    elif headline == "unproven_sharp_movement":
        # Kept word for word, so entries either side of this change stay comparable.
        text = (
            "movement doubles back beyond "
            f"{sharp_movement_min_degrees:g}"
            " degrees without an explicit turn:lanes permission"
        )
    elif headline == "competing_movements":
        text = (
            "another movement from this lane is classed "
            f"{movement_family(candidate.movement)} as well, so which lane serves it "
            "is not settled"
        )
    elif headline == "borderline_angle":
        text = (
            f"turn angle of {candidate.angle_degrees:.1f} degrees sits in the "
            f"{low:g}-{high:g} degree band where through and turn are not separable"
        )
    else:  # pragma: no cover - a status of review_required always carries a cause
        text = "movement has multiple or borderline geometric interpretations"
    if len(causes) > 1:
        text += "; also " + ", ".join(_CAUSE_LABELS[cause] for cause in causes[1:])
    return text


def _is_decision_node(
    *,
    non_reverse_group_count: int,
    adjacent_node_count: int,
    has_control_or_restriction: bool,
    explicit_reverse: bool,
) -> bool:
    return (
        non_reverse_group_count > 1
        or adjacent_node_count > 2
        or has_control_or_restriction
        or explicit_reverse
    )


def _points(line: LineString) -> list[Point2D]:
    return [Point2D(x=float(x), y=float(y)) for x, y in line.coords]


def _polygon_points(polygon: Polygon | MultiPolygon) -> list[Point2D]:
    """The outline of a buffered centreline, as the model stores it.

    Buffering a tight curve can return a `MultiPolygon`: where the turn bends harder than its
    own half-width the offset outline crosses itself, and shapely resolves that into separate
    pieces. The largest is the lane surface and the rest are slivers that self-intersection left
    behind, so take the largest rather than failing — the alternative is a junction turn that
    cannot be represented at all once it is drawn as a real curve.
    """
    if isinstance(polygon, MultiPolygon):
        if polygon.is_empty:
            raise GenerationError("lane surface buffered to an empty polygon")
        polygon = max(polygon.geoms, key=lambda part: part.area)
    return [Point2D(x=float(x), y=float(y)) for x, y in polygon.exterior.coords]


def _finding(
    *,
    rule: str,
    severity: str,
    source_type: str,
    source_ids: list[str],
    affected_feature_ids: list[str],
    proposed_value: object,
    confidence: str,
    reason: str,
) -> ReviewFinding:
    evidence = {
        "rule": rule,
        "source_type": source_type,
        "source_ids": source_ids,
        "affected_feature_ids": affected_feature_ids,
        "proposed_value": proposed_value,
        "reason": reason,
    }
    return ReviewFinding(
        identifier=deterministic_id(
            "finding", rule, source_type, *source_ids, *affected_feature_ids
        ),
        rule=rule,
        severity=severity,
        source_type=source_type,
        source_ids=source_ids,
        affected_feature_ids=affected_feature_ids,
        proposed_value=proposed_value,
        confidence=confidence,
        reason=reason,
        evidence_checksum=_canonical_checksum(evidence),
    )


def _lane_collapse_findings(
    connectors: list[ConnectorFeature],
    continuation_links: list[tuple[str, str, str]],
    lane_lookup: dict[str, LaneFeature],
) -> list[ReviewFinding]:
    """Where the lane mapping put several approach lanes onto one destination lane.

    Read from the links that survived, never from the way's `lanes` tag. Two roads
    meeting at a turn have unrelated widths, and comparing them called a one-lane left
    turn off a two-lane road a "2 to 1 lane change" — a statement about two different
    carriageways rather than about the movement. What can actually go wrong is narrower:
    `_mapped_lane_index` sending two lanes to the same target, so two streams of traffic
    are handed one lane. That is what is counted here.

    A `forbidden` connector is left out because the movement does not exist; the
    reviewer would be shown lanes with nothing between them.
    """
    groups: dict[tuple[str, tuple[str, ...], tuple[str, ...]], tuple[set[str], set[str]]] = {}
    links = [
        (connector.junction_node_id, connector.from_lane_id, connector.to_lane_id)
        for connector in connectors
        if connector.status != "forbidden"
    ]
    links.extend(continuation_links)
    for node_id, from_id, to_id in links:
        source = lane_lookup[from_id]
        target = lane_lookup[to_id]
        key = (node_id, tuple(source.source_edge), tuple(target.source_edge))
        feeders, landed = groups.setdefault(key, (set(), set()))
        feeders.add(from_id)
        landed.add(to_id)
    findings: list[ReviewFinding] = []
    for (node_id, _, target_edge), (feeders, landed) in sorted(groups.items()):
        if len(feeders) <= len(landed):
            continue
        destination_lane_count = sum(
            1 for lane in lane_lookup.values() if tuple(lane.source_edge) == target_edge
        )
        findings.append(
            _finding(
                rule="lane_transition_count_mismatch",
                severity="warning",
                source_type="node",
                source_ids=[node_id],
                affected_feature_ids=sorted(feeders) + sorted(landed),
                proposed_value={
                    "incoming_lane_count": len(feeders),
                    "outgoing_lane_count": len(landed),
                    "destination_lane_count": destination_lane_count,
                },
                confidence="medium",
                reason=(
                    f"proportional lane-order mapping collapses {len(feeders)} approach "
                    f"lanes onto {len(landed)} destination lane"
                    f"{'' if len(landed) == 1 else 's'}"
                ),
            )
        )
    return findings


def _direction_arrow(centerline: list[Point2D], width: float) -> list[Point2D] | None:
    """Chevron at the midpoint of a lane centreline, pointing along direction of travel.

    Built in projected metres so the arrow keeps a physical size, and returned as an
    open three-point polyline the review map can draw like any other geometry.
    """
    spans = [
        math.hypot(end.x - start.x, end.y - start.y)
        for start, end in zip(centerline, centerline[1:], strict=False)
    ]
    total = sum(spans)
    if total <= 0:
        return None

    target = total / 2
    travelled = 0.0
    index = 0
    for position, span in enumerate(spans):
        if span <= 0:
            continue
        index = position
        if travelled + span >= target:
            break
        travelled += span
    start, end = centerline[index], centerline[index + 1]
    span = spans[index]
    ratio = min(max((target - travelled) / span, 0.0), 1.0)

    unit_x, unit_y = (end.x - start.x) / span, (end.y - start.y) / span
    normal_x, normal_y = -unit_y, unit_x
    tip_x = start.x + (end.x - start.x) * ratio
    tip_y = start.y + (end.y - start.y) * ratio
    half = min(width, total) / 2
    return [
        Point2D(
            x=tip_x - unit_x * half + normal_x * half * 0.8,
            y=tip_y - unit_y * half + normal_y * half * 0.8,
        ),
        Point2D(x=tip_x + unit_x * half, y=tip_y + unit_y * half),
        Point2D(
            x=tip_x - unit_x * half - normal_x * half * 0.8,
            y=tip_y - unit_y * half - normal_y * half * 0.8,
        ),
    ]


def _links_by_node(model: PreliminaryLaneModel) -> dict[str, set[tuple[str, str]]]:
    """Every lane-to-lane link the model keeps, indexed by the node it happens at.

    Connectors and continuations both count; a `forbidden` connector does not, because
    the movement does not exist and nothing travels between its two lanes.
    """
    lanes = {lane.identifier for lane in model.lanes}
    links: dict[str, set[tuple[str, str]]] = {}
    for connector in model.connectors:
        if connector.status == "forbidden":
            continue
        links.setdefault(connector.junction_node_id, set()).add(
            (connector.from_lane_id, connector.to_lane_id)
        )
    for lane in model.lanes:
        for exit_id in lane.exit_lanes:
            if exit_id in lanes:
                links.setdefault(lane.source_edge[1], set()).add((lane.identifier, exit_id))
    return links


def _movement_roles(
    finding: ReviewFinding, links_by_node: dict[str, set[tuple[str, str]]]
) -> dict[str, str]:
    """Which lanes a finding names are approached from, and which are arrived at.

    A finding that names a connector already carries its two ends; one that names lanes
    directly does not, and the reviewer is left with a set of identically coloured lanes
    and no way to see which turns into which. The direction is not a guess: it is in the
    links at the node, so read it from there.

    Only node-scoped findings qualify. A finding about a whole way names lanes along it,
    and consecutive edges of one way *are* joined by continuations, so orienting those
    would chain dozens of lanes into a sequence that means nothing. Returns an empty
    mapping when nothing orients, so a connector finding keeps its own path.
    """
    if finding.source_type != "node":
        return {}
    named = set(finding.affected_feature_ids)
    approach: set[str] = set()
    destination: set[str] = set()
    for node_id in finding.source_ids:
        for from_id, to_id in links_by_node.get(node_id, set()):
            if from_id in named and to_id in named:
                approach.add(from_id)
                destination.add(to_id)
    roles = {identifier: "approach" for identifier in approach - destination}
    roles.update({identifier: "destination" for identifier in destination - approach})
    # A lane that is both would be painted a colour that is only half true, so it keeps
    # the plain highlight and says nothing it cannot support.
    return dict(sorted(roles.items()))


def build_review_payload(model: PreliminaryLaneModel, snapshot: OsmSnapshot) -> dict[str, Any]:
    """Projected features, findings and counts shared by the Stage 2 audit and Stage 3 review.

    Both views draw the same map and differ only in whether decisions can be recorded
    on it. One builder means a reviewer cannot be shown different geometry depending
    on which view they opened.
    """
    transformer = Transformer.from_crs(
        model.metadata.coordinate_system_wkt, "EPSG:4326", always_xy=True
    )

    def coordinates(points: list[Point2D]) -> list[list[float]]:
        return [list(transformer.transform(point.x, point.y)) for point in points]

    features: list[dict[str, Any]] = []
    for lane in model.lanes:
        properties = {
            "id": lane.identifier,
            "source_way_ids": lane.source_way_ids,
            "source_edge": lane.source_edge,
            "lane_index": lane.lane_index,
            "lane_count": lane.lane_count,
            "direction": lane.direction,
            "turn_permissions": lane.turn_permissions,
            "entry_lanes": lane.entry_lanes,
            "exit_lanes": lane.exit_lanes,
        }
        features.extend(
            [
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [coordinates(lane.polygon)],
                    },
                    "properties": {**properties, "kind": "lane_polygon"},
                },
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "LineString",
                        "coordinates": coordinates(lane.centerline),
                    },
                    "properties": {**properties, "kind": "lane_centerline"},
                },
            ]
        )
        arrow = _direction_arrow(lane.centerline, lane.width_m)
        if arrow is not None:
            features.append(
                {
                    "type": "Feature",
                    "geometry": {"type": "LineString", "coordinates": coordinates(arrow)},
                    "properties": {**properties, "kind": "lane_direction"},
                }
            )
    for connector in model.connectors:
        properties = {
            "id": connector.identifier,
            "kind": "connector",
            "status": connector.status,
            "movement": connector.movement,
            "source": f"{connector.from_way_id} -> {connector.to_way_id}",
            "from_lane_id": connector.from_lane_id,
            "to_lane_id": connector.to_lane_id,
            "junction_node_id": connector.junction_node_id,
            "turn_angle_degrees": connector.turn_angle_degrees,
        }
        features.extend(
            [
                # The band is what makes a link readable where lane polygons abut; the
                # centreline alone is a hairline lost among them.
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [coordinates(connector.polygon)],
                    },
                    "properties": {**properties, "kind": "connector_polygon"},
                },
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "LineString",
                        "coordinates": coordinates(connector.centerline),
                    },
                    "properties": properties,
                },
            ]
        )
    for stop_line in model.stop_lines:
        features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "LineString",
                    "coordinates": coordinates(stop_line.points),
                },
                "properties": {
                    "id": stop_line.identifier,
                    "kind": "stop_line",
                    "status": stop_line.status,
                    "source": stop_line.source_node_id,
                },
            }
        )

    feature_ids = {feature["properties"]["id"] for feature in features}
    lanes_by_way: dict[str, list[str]] = {}
    for lane in model.lanes:
        for way_id in lane.source_way_ids:
            lanes_by_way.setdefault(way_id, []).append(lane.identifier)
    restrictions = {item.identifier: item for item in model.restrictions}

    # Source OSM geometry, so a finding's `source_ids` can be located on the map and
    # not merely matched by the queue's text search. Relations carry no geometry of
    # their own, so they contribute their member ways and nodes instead.
    def source_refs(finding: ReviewFinding) -> tuple[set[str], set[str]]:
        return _source_refs(finding, snapshot)

    source_way_ids = set(lanes_by_way)
    source_node_ids = {connector.junction_node_id for connector in model.connectors}
    source_node_ids.update(stop_line.source_node_id for stop_line in model.stop_lines)
    source_node_ids.update(signal.source_node_id for signal in model.signals)
    for finding in model.findings:
        ways, nodes = source_refs(finding)
        source_way_ids.update(ways)
        source_node_ids.update(nodes)

    def node_position(node_id: str) -> list[float] | None:
        node = snapshot.nodes.get(node_id)
        return None if node is None else [node.longitude, node.latitude]

    source_ids: set[str] = set()
    for way_id in sorted(source_way_ids):
        way = snapshot.ways.get(way_id)
        if way is None:
            continue
        line = [point for point in map(node_position, way.node_ids) if point is not None]
        if len(line) < 2:
            continue
        key = f"way:{way_id}"
        source_ids.add(key)
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": line},
                "properties": {
                    "id": key,
                    "kind": "source_way",
                    "osm_way_id": way_id,
                    "generated_lane_ids": sorted(lanes_by_way.get(way_id, [])),
                    **way.tags,
                },
            }
        )
    junction_nodes = {connector.junction_node_id for connector in model.connectors}
    for node_id in sorted(source_node_ids):
        position = node_position(node_id)
        if position is None:
            continue
        key = f"node:{node_id}"
        source_ids.add(key)
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": position},
                "properties": {
                    "id": key,
                    "kind": "source_node",
                    "osm_node_id": node_id,
                    "junction_node": node_id in junction_nodes,
                    **snapshot.nodes[node_id].tags,
                },
            }
        )

    links_by_node = _links_by_node(model)

    findings = []
    for finding in model.findings:
        finding_data = finding.model_dump(mode="json")
        geometry_ids = {
            identifier for identifier in finding.affected_feature_ids if identifier in feature_ids
        }
        for identifier in finding.affected_feature_ids:
            restriction = restrictions.get(identifier)
            if restriction is None:
                continue
            geometry_ids.update(restriction.forbidden_connector_ids)
            for way_id in restriction.from_way_ids + restriction.to_way_ids:
                geometry_ids.update(lanes_by_way.get(way_id, []))
        finding_data["geometry_ids"] = sorted(geometry_ids)
        ways, nodes = source_refs(finding)
        finding_data["source_geometry_ids"] = sorted(
            key
            for key in [f"way:{item}" for item in ways] + [f"node:{item}" for item in nodes]
            if key in source_ids
        )
        roles = _movement_roles(finding, links_by_node)
        if roles:
            finding_data["movement_roles"] = roles
        findings.append(finding_data)

    return {
        "features": {"type": "FeatureCollection", "features": features},
        "findings": findings,
        "summary": {
            "lanes": len(model.lanes),
            "connectors": len(model.connectors),
            "signals": len(model.signals),
            "stop_lines": len(model.stop_lines),
            "restrictions": len(model.restrictions),
            "findings": len(model.findings),
        },
    }


def _search_index(snapshot: OsmSnapshot) -> dict[str, Any]:
    """Every way and node in the source OSM, so any id a reviewer types can be shown.

    The audit draws source geometry only where the model reached it — the ways lanes
    came from, and the nodes movements were built at. A reviewer checking why a road
    produced nothing types exactly the id that is missing, and got silence back: no
    highlight, no message, indistinguishable from a typo. This index carries the raw
    coordinates for the rest, drawn only when searched, so "not generated" reads as a
    highlighted way with an explanation rather than as nothing happening.
    """
    ways: dict[str, Any] = {}
    for way_id, way in snapshot.ways.items():
        line = [
            [node.longitude, node.latitude]
            for node in (snapshot.nodes.get(reference) for reference in way.node_ids)
            if node is not None
        ]
        if len(line) < 2:
            continue
        ways[way_id] = {"line": line, "tags": way.tags}
    nodes = {
        node_id: {"point": [node.longitude, node.latitude], "tags": node.tags}
        for node_id, node in snapshot.nodes.items()
    }
    return {"ways": ways, "nodes": nodes}


def _render_review_html(model: PreliminaryLaneModel, snapshot: OsmSnapshot) -> str:
    data = build_review_payload(model, snapshot)
    data["search_index"] = _search_index(snapshot)
    payload = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
    template = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Stage 2 Review Audit</title><link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<style>
*{box-sizing:border-box}html,body{height:100%;margin:0;font:14px system-ui,sans-serif;color:#202428}body{display:grid;grid-template-columns:minmax(330px,420px) 1fr;background:#f4f5f6}aside{padding:14px;overflow:auto;border-right:1px solid #c8cdd1;background:#fff}h1{font-size:20px;margin:0 0 5px}h2{font-size:14px;margin:14px 0 7px}.muted{color:#687078;font-size:12px}.summary{display:grid;grid-template-columns:repeat(3,1fr);gap:5px;margin:10px 0}.metric{padding:7px;background:#f1f3f5;border-radius:5px;text-align:center}.metric b{display:block;font-size:16px}.filters{display:grid;gap:7px}.filters input,.filters select{width:100%;padding:7px;border:1px solid #adb5bd;border-radius:4px;background:#fff}.queue{display:grid;gap:6px;margin-top:8px}.finding{border:1px solid #d6dadd;border-left:5px solid #e67700;border-radius:5px;padding:8px;background:#fff;cursor:pointer;text-align:left}.finding.blocker{border-left-color:#c92a2a}.finding:hover,.finding.active{background:#fff3bf}.finding strong{display:block}.finding small{display:block;color:#687078;margin-top:3px}.detail{padding:9px;background:#f8f9fa;border:1px solid #dee2e6;border-radius:5px;overflow-wrap:anywhere}.detail table,.popup-table{border-collapse:collapse;width:100%}.detail td,.popup-table td{border-bottom:1px solid #e5e7e9;padding:4px;vertical-align:top;font-size:12px}.legend{display:grid;grid-template-columns:18px 1fr;gap:6px 8px;align-items:center}.swatch{height:5px}.lane{background:#277da1}.lane-direction{background:#0b4f7a}.connector-band{background:#8ce99a;height:9px}.active-connector{background:#2b8a3e}.review-connector{background:#f08c00}.forbidden-connector{background:#c92a2a}.stop-line{background:#7048e8}.source-geometry{background:#868e96}.highlight{background:#ffd43b}.chip{display:inline-block;font:inherit;font-size:12px;padding:2px 7px;margin:1px 0;border:1px solid #b38600;border-radius:10px;background:#fff9db;color:#7a5c00;cursor:pointer}.chip:hover{background:#ffd43b;color:#202428}.muted-chip{border-color:#ced4da;background:#f1f3f5;color:#687078;cursor:default}.link-table{border-collapse:collapse}.link-table td{border:0;padding:1px 7px 1px 0;font-size:12px;vertical-align:middle;white-space:nowrap}.pill{font-size:11px;padding:1px 7px;border-radius:9px;border:1px solid}.pill.active{border-color:#2b8a3e;color:#2b8a3e;background:#ebfbee}.pill.review_required{border-color:#f08c00;color:#a35c00;background:#fff4e6}.pill.forbidden{border-color:#c92a2a;color:#c92a2a;background:#fff5f5}.queue-note{font-size:12px;color:#687078;margin:7px 0}.search-result{font-size:12px;line-height:1.4;margin:8px 0 0;min-height:17px}.search-result.miss{color:#a5390f}#map{height:100%;min-height:520px}.leaflet-popup-content{max-height:300px;overflow:auto}@media(max-width:780px){body{grid-template-columns:1fr;grid-template-rows:minmax(360px,45vh) 1fr}aside{border-right:0;border-bottom:1px solid #c8cdd1}#map{min-height:55vh}}
</style></head><body><aside><h1>Stage 2 Review Audit</h1><div class="muted">Read-only visual explanation of preliminary generation findings. Decisions are recorded later in Stage 3.</div><div class="summary" id="summary"></div><h2>Review filters</h2><div class="filters"><input id="search" placeholder="Search rule or reason; paste an OSM way/node or feature ID to highlight it"><select id="rule"><option value="">All rules</option></select><select id="severity"><option value="">All severities</option><option value="blocker">Blocker</option><option value="warning">Warning</option></select></div><div class="search-result" id="search-result" role="status" aria-live="polite"></div><div class="queue-note" id="queue-note"></div><div class="queue" id="queue"></div><h2>Selected finding</h2><div class="detail" id="detail">Select a review item to focus its affected geometry.</div><h2>Legend</h2><div class="legend"><span class="swatch lane"></span><span>Lane centreline</span><span class="swatch lane-direction"></span><span>Direction of travel (arrow points downstream)</span><span class="swatch connector-band"></span><span>Connector band (the lane-width path a movement takes)</span><span class="swatch active-connector"></span><span>Active connector</span><span class="swatch review-connector"></span><span>Review-required connector</span><span class="swatch forbidden-connector"></span><span>Forbidden connector</span><span class="swatch stop-line"></span><span>Inferred stop line</span><span class="swatch source-geometry"></span><span>Source OSM way or node (dashed)</span><span class="swatch highlight"></span><span>Selected or searched geometry</span></div></aside><main id="map"></main>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script><script>
const payload=__PAYLOAD__;const reviewPriority={turn_permission_geometry_conflict:0,ambiguous_connector:1,restriction_effect_review:2,signal_lane_association:3,lane_transition_count_mismatch:4,inferred_stop_line:5,lane_count_inference:6,lane_width_default:7,speed_default:8};payload.findings.sort((a,b)=>(reviewPriority[a.rule]??99)-(reviewPriority[b.rule]??99)||a.rule.localeCompare(b.rule)||a.identifier.localeCompare(b.identifier));const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const map=L.map('map',{preferCanvas:true});L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:20,attribution:'&copy; OpenStreetMap contributors'}).addTo(map);
// Highlights are drawn into their own pane, above every layer group. Restyling the
// drawn layer in place kept its z-order, and source ways sit at the bottom of it —
// a searched way turned yellow underneath the lane and connector geometry covering
// it, which is indistinguishable from nothing having happened.
map.createPane('focus');map.getPane('focus').style.zIndex=650;const focusLayer=L.layerGroup().addTo(map);
const groups={source_way:L.layerGroup(),source_node:L.layerGroup(),lane_polygon:L.layerGroup(),lane_centerline:L.layerGroup(),lane_direction:L.layerGroup(),connector_polygon:L.layerGroup(),active:L.layerGroup(),review_required:L.layerGroup(),forbidden:L.layerGroup(),stop_line:L.layerGroup()};const byId=new Map(),propsById=new Map(),featuresById=new Map(),allLayers=[];let selected=[],lastFocused=null;
// A connector is drawn twice: a hairline centreline, filed under its status, and a
// lane-width band, filed under 'Connector bands'. A Leaflet overlay is one checkbox to
// one group, so the band cannot sit in both — and filing it under its status alone
// would cost the master switch that clears every band at once. Instead the band stays
// in connector_polygon and is added to or removed from it as its status is toggled,
// which makes a band visible only when both boxes are ticked. Without this, unchecking
// a category hid its centrelines and left the bands lying over the map, still opaque
// and still swallowing the clicks meant for the connectors left showing.
const bandsByStatus={active:[],review_required:[],forbidden:[]};
const statusColor=s=>s==='forbidden'?'#c92a2a':s==='review_required'?'#f08c00':'#2b8a3e';
function styleFor(p){if(p.kind==='source_way')return{color:'#868e96',weight:1.5,opacity:.55,dashArray:'2 4'};if(p.kind==='source_node')return{color:'#868e96',weight:1,radius:4,fillColor:'#adb5bd',fillOpacity:.8,opacity:.8};if(p.kind==='lane_polygon')return{color:'#74c0fc',weight:1,fillColor:'#74c0fc',fillOpacity:.08};if(p.kind==='lane_centerline')return{color:'#277da1',weight:2,opacity:.75};if(p.kind==='lane_direction')return{color:'#0b4f7a',weight:3,opacity:.95,lineCap:'butt',lineJoin:'miter'};if(p.kind==='connector_polygon')return{color:statusColor(p.status),weight:1,fillColor:statusColor(p.status),fillOpacity:.22,opacity:.5};if(p.kind==='stop_line')return{color:'#7048e8',weight:6};return{color:statusColor(p.status),weight:p.status==='review_required'?5:3,dashArray:p.status==='review_required'?'7 5':null,opacity:.9}}
// A lane, connector or source id, described the way a reviewer reads it off the map.
// The generated lane id is what a reviewer searches by, so it leads every reference.
function laneLabel(id){const p=propsById.get(id);return p&&p.lane_count?`${id} · lane ${p.lane_index+1}/${p.lane_count}`:String(id)}
function laneChip(id){return byId.has(id)?`<button class="chip" onclick="focusSource('${esc(id)}')">${esc(laneLabel(id))}</button>`:`<span class="chip muted-chip">${esc(laneLabel(id))}</span>`}
function idChip(id){return byId.has(id)?`<button class="chip" onclick="focusSource('${esc(id)}')">${esc(id)}</button>`:`<span class="chip muted-chip">${esc(id)}</span>`}
// Every link this lane has, in one row each. Status matters as much as the id: a
// review-required U-turn candidate must not read like an asserted connection.
function laneLinks(p,incoming){
  const own=incoming?'to_lane_id':'from_lane_id',far=incoming?'from_lane_id':'to_lane_id';
  const rows=(payload.features.features||[]).filter(f=>f.properties.kind==='connector'&&f.properties[own]===p.id)
    .map(f=>({id:f.properties[far],movement:f.properties.movement,status:f.properties.status}));
  // A direct continuation has no connector, so it is named in entry_lanes/exit_lanes.
  for(const id of ((incoming?p.entry_lanes:p.exit_lanes)||[]).filter(x=>(propsById.get(x)||{}).lane_count))
    rows.push({id,movement:'continuation',status:'active'});
  const seen=new Set();
  return rows.filter(r=>!seen.has(r.id)&&seen.add(r.id))
    .sort((a,b)=>a.movement.localeCompare(b.movement)||a.id.localeCompare(b.id))}
function linkTable(rows){
  if(!rows.length)return '<span class="muted">none</span>';
  return '<table class="link-table">'+rows.map(r=>`<tr><td>${idChip(r.id)}</td>`
    +`<td class="muted">${esc(r.movement)}</td>`
    +`<td><span class="pill ${esc(r.status)}">${esc(r.status==='review_required'?'review':r.status)}</span></td></tr>`).join('')+'</table>'}
function popup(p){
  let head='';
  if(p.kind==='connector'||p.kind==='connector_polygon'){
    // The whole point of the popup: which lane this movement leaves and which it enters.
    head=`<table class="popup-table"><tr><td><strong>Incoming lane</strong></td><td>${laneChip(p.from_lane_id)}</td></tr>`
        +`<tr><td><strong>Outgoing lane</strong></td><td>${laneChip(p.to_lane_id)}</td></tr></table>`;
  }else if(p.kind&&p.kind.startsWith('lane_')){
    head=`<p class="muted">${esc(laneLabel(p.id))}</p><table class="popup-table">`
        +`<tr><td><strong>Entered from</strong></td><td>${linkTable(laneLinks(p,true))}</td></tr>`
        +`<tr><td><strong>Leaves to</strong></td><td>${linkTable(laneLinks(p,false))}</td></tr></table>`;
  }
  return `<strong>${esc(p.kind)}</strong>${head}<table class="popup-table">${Object.entries(p).map(([k,v])=>`<tr><td>${esc(k)}</td><td>${esc(Array.isArray(v)?v.join(', '):v)}</td></tr>`).join('')}</table>`}
for(const feature of payload.features.features){const p=feature.properties;const layer=L.geoJSON(feature,{style:()=>styleFor(p),pointToLayer:(_f,latlng)=>L.circleMarker(latlng,styleFor(p)),onEachFeature:(_f,l)=>l.bindPopup(()=>popup(p))});propsById.set(p.id,p);if(!featuresById.has(p.id))featuresById.set(p.id,[]);featuresById.get(p.id).push(feature);layer.eachLayer(l=>{l._baseStyle=styleFor(p);allLayers.push(l);if(!byId.has(p.id))byId.set(p.id,[]);byId.get(p.id).push(l)});const key=p.kind==='connector'?p.status:p.kind;groups[key].addLayer(layer);if(p.kind==='connector_polygon'&&bandsByStatus[p.status])bandsByStatus[p.status].push(layer)}
groups.source_way.addTo(map);groups.source_node.addTo(map);groups.lane_centerline.addTo(map);groups.lane_direction.addTo(map);groups.connector_polygon.addTo(map);groups.active.addTo(map);groups.review_required.addTo(map);groups.forbidden.addTo(map);groups.stop_line.addTo(map);L.control.layers(null,{'Source OSM ways':groups.source_way,'Source OSM nodes':groups.source_node,'Lane polygons':groups.lane_polygon,'Lane centrelines':groups.lane_centerline,'Lane direction arrows':groups.lane_direction,'Connector bands':groups.connector_polygon,'Active connectors':groups.active,'Review-required connectors':groups.review_required,'Forbidden connectors':groups.forbidden,'Stop lines':groups.stop_line},{collapsed:false}).addTo(map);
// Only the control fires these — the addTo calls above do not — so the opening state,
// with every box ticked, needs no special case.
const bandOwner=new Map([[groups.active,'active'],[groups.review_required,'review_required'],[groups.forbidden,'forbidden']]);
map.on('overlayadd overlayremove',e=>{const status=bandOwner.get(e.layer);if(!status)return;for(const band of bandsByStatus[status]){if(e.type==='overlayadd')groups.connector_polygon.addLayer(band);else groups.connector_polygon.removeLayer(band)}});
const allGeometry=L.featureGroup(allLayers);if(allGeometry.getBounds().isValid())map.fitBounds(allGeometry.getBounds().pad(.04));
document.getElementById('summary').innerHTML=Object.entries(payload.summary).map(([k,v])=>`<div class="metric"><b>${v}</b>${esc(k.replaceAll('_',' '))}</div>`).join('');const rules=[...new Set(payload.findings.map(f=>f.rule))].sort();document.getElementById('rule').innerHTML+=[...rules].map(r=>`<option value="${esc(r)}">${esc(r)}</option>`).join('');
const EMPTY_DETAIL='Select a review item to focus its affected geometry.';
function clearSelection(){for(const l of selected)if(l.setStyle)l.setStyle(l._baseStyle);selected=[];focusLayer.clearLayers();lastFocused=null;document.querySelectorAll('.finding.active').forEach(x=>x.classList.remove('active'))}
const GENERATED_HIGHLIGHT={color:'#ffd43b',weight:8,fillOpacity:.4,opacity:1},SOURCE_HIGHLIGHT={color:'#ffd43b',weight:7,fillColor:'#ffd43b',fillOpacity:.9,opacity:1,radius:9};
function highlight(ids,style){const layers=ids.flatMap(x=>byId.get(x)||[]);for(const l of layers){if(l.setStyle)l.setStyle(style);selected.push(l)}return layers}
function fitTo(layers){const bounds=L.featureGroup(layers).getBounds();if(bounds.isValid())map.fitBounds(bounds.pad(.35),{maxZoom:19});return layers.length}
// Resolve a raw ID the reviewer typed or clicked. A bare OSM ID is ambiguous between
// a way and a node, so try both namespaces before falling back to a generated ID; an
// explicit `way:`/`node:` prefix is honoured as typed and settles the ambiguity.
function resolveId(raw){const q=String(raw).trim();if(!q)return null;for(const key of [q,'way:'+q,'node:'+q])if(featuresFor(key).length)return key;return null}
// Source geometry the audit did not draw, rebuilt on demand from the whole snapshot.
// Only the ways lanes came from and the nodes movements were built at are drawn, so
// searching any other id used to highlight nothing and say nothing — the one case a
// reviewer is most likely to be checking, because the id produced no geometry.
function indexedFeature(key){
  const index=payload.search_index||{};
  if(key.slice(0,4)==='way:'){const id=key.slice(4),way=(index.ways||{})[id];return way?{type:'Feature',geometry:{type:'LineString',coordinates:way.line},properties:Object.assign({id:key,kind:'source_way',osm_way_id:id},way.tags)}:null}
  if(key.slice(0,5)==='node:'){const id=key.slice(5),node=(index.nodes||{})[id];return node?{type:'Feature',geometry:{type:'Point',coordinates:node.point},properties:Object.assign({id:key,kind:'source_node',osm_node_id:id},node.tags)}:null}
  return null}
// A lane draws a polygon, a centreline and a direction arrow under one ID, so all
// three are returned: highlighting a lane must light the lane, not one hairline of it.
function featuresFor(key){const drawn=featuresById.get(key);if(drawn&&drawn.length)return drawn;const one=indexedFeature(key);return one?[one]:[]}
function propertiesFor(key){const p=propsById.get(key);if(p)return p;const feature=indexedFeature(key);return feature?feature.properties:{}}
function kindLabel(p){const kind=String(p.kind||'feature');return kind.startsWith('lane_')?'lane':kind.replaceAll('_',' ')}
function describeMatch(key){const p=propertiesFor(key);if(p.kind==='source_way')return`OSM way ${p.osm_way_id}`;if(p.kind==='source_node')return`OSM node ${p.osm_node_id}`;return`Generated ${kindLabel(p)} ${p.id}`}
function describeId(key){
  const p=propertiesFor(key);
  return `<p class="muted">Highlighted ${esc(describeMatch(key))}</p>`
    +(propsById.has(key)?'':`<p class="muted">Stage 2 drew no geometry for this ${p.kind==='source_node'?'node':'way'} — nothing was generated from it and no finding names it. Shown from the source OSM.</p>`)
    +`<table class="popup-table">${Object.entries(p).filter(([k])=>k!=='id'&&k!=='kind').map(([k,v])=>`<tr><td>${esc(k)}</td><td>${esc(Array.isArray(v)?v.join(', '):v)}</td></tr>`).join('')}</table>`}
const FOCUS_CASING={color:'#111318',weight:13,opacity:.95,fill:false},FOCUS_LINE={color:'#ffd43b',weight:7,opacity:1,fill:false},FOCUS_AREA={color:'#ffd43b',weight:4,opacity:1,fillColor:'#ffd43b',fillOpacity:.45};
// Drawn as its own geometry in the focus pane rather than as a restyle of the layer
// already on the map, so the highlight sits above everything and stays visible when
// the group it belongs to is switched off in the layer control.
function drawFocus(key,features){
  focusLayer.clearLayers();const drawn=[];
  const add=(feature,options)=>{const layer=L.geoJSON(feature,Object.assign({pane:'focus'},options));layer.bindPopup(()=>describeId(key));focusLayer.addLayer(layer);drawn.push(layer)};
  for(const feature of features){
    const type=String(feature.geometry.type);
    if(type.indexOf('Point')>=0)add(feature,{pointToLayer:(_f,ll)=>L.circleMarker(ll,{pane:'focus',radius:11,color:'#111318',weight:4,fillColor:'#ffd43b',fillOpacity:1})});
    else if(type.indexOf('Polygon')>=0)add(feature,{style:()=>FOCUS_AREA});
    // A line gets a dark casing under the yellow so it reads over both the pale
    // basemap and the lane geometry it runs through.
    else{add(feature,{style:()=>FOCUS_CASING});add(feature,{style:()=>FOCUS_LINE})}}
  return drawn}
function focusSource(key){
  clearSelection();const features=featuresFor(key);
  if(!features.length){document.getElementById('detail').innerHTML=`<p class="muted">Nothing on this map or in the source OSM carries the ID ${esc(key)}.</p>`;return}
  lastFocused=key;const bounds=L.featureGroup(drawFocus(key,features)).getBounds();
  if(bounds.isValid())map.fitBounds(bounds.pad(.35),{maxZoom:19});
  document.getElementById('detail').innerHTML=describeId(key)}
// An ID that matches nothing has to say so. Silence is indistinguishable from a typo,
// and from the map having quietly failed to highlight what was found.
function reportSearch(raw,hit){
  const q=String(raw).trim(),box=document.getElementById('search-result');box.className='search-result';
  if(!q){box.innerHTML='';return}
  if(hit){
    const ambiguous=hit.slice(0,4)==='way:'&&featuresFor('node:'+q).length?` <span class="muted">${esc(q)} is also a node — type <code>node:${esc(q)}</code> for that one.</span>`:'';
    box.innerHTML=`<strong>${esc(describeMatch(hit))}</strong> highlighted on the map.`+(propsById.has(hit)?'':' <span class="muted">Stage 2 generated nothing from it; drawn from the source OSM.</span>')+ambiguous;
    return}
  if(/^(way:|node:)?\\d+$/.test(q)){box.className='search-result miss';box.innerHTML=`No way or node <strong>${esc(q)}</strong> in source/map.osm.`;return}
  box.innerHTML=''}
function sourceChips(f){return f.source_ids.map(s=>{const key=resolveId(s);return key?`<button class="chip" onclick="focusSource('${esc(key)}')">${esc(s)}</button>`:`<span class="chip muted-chip">${esc(s)}</span>`}).join(' ')}
function showFinding(id,button){clearSelection();const f=payload.findings.find(x=>x.identifier===id);button?.classList.add('active');const layers=highlight(f.geometry_ids,GENERATED_HIGHLIGHT);const sources=highlight(f.source_geometry_ids,SOURCE_HIGHLIGHT);fitTo([...layers,...sources]);const rows=[['Rule',f.rule],['Severity',f.severity],['Confidence',f.confidence],['Reason',f.reason],['Source',`${esc(f.source_type)}: ${sourceChips(f)}`,true],['Affected IDs',f.affected_feature_ids.join(', ')||'none'],['Mapped geometry',`${f.geometry_ids.length} generated, ${f.source_geometry_ids.length} source`],['Proposed value',JSON.stringify(f.proposed_value)],['Finding ID',f.identifier]];document.getElementById('detail').innerHTML=`<table>${rows.map(([k,v,isHtml])=>`<tr><td><strong>${esc(k)}</strong></td><td>${isHtml?v:esc(v)}</td></tr>`).join('')}</table>`+(layers.length?'':'<p class="muted">No generated geometry could be mapped for this finding.</p>')}
function renderQueue(){const raw=document.getElementById('search').value,q=raw.trim().toLowerCase(),rule=document.getElementById('rule').value,severity=document.getElementById('severity').value;const matches=payload.findings.filter(f=>(!rule||f.rule===rule)&&(!severity||f.severity===severity)&&(!q||JSON.stringify(f).toLowerCase().includes(q)));const shown=matches.slice(0,250);
// A pasted ID is a request to look at that thing, not merely to filter by its text:
// both happen, the queue narrows and the map highlights what was named. Emptying the
// box takes the highlight away with it; refining the text leaves it alone.
const hit=resolveId(raw);if(hit&&hit!==lastFocused)focusSource(hit);else if(!hit&&lastFocused&&!raw.trim()){clearSelection();document.getElementById('detail').innerHTML=EMPTY_DETAIL}reportSearch(raw,hit);const note=document.getElementById('queue-note');note.innerHTML=`${matches.length} matching findings${matches.length>shown.length?`; showing first ${shown.length}`:''}`;const queue=document.getElementById('queue');queue.innerHTML='';for(const f of shown){const b=document.createElement('button');b.className=`finding ${f.severity}`;b.innerHTML=`<strong>${esc(f.rule)}</strong><span>${esc(f.reason)}</span><small>${esc(f.source_type)} ${esc(f.source_ids.join(', '))} · ${f.geometry_ids.length} mapped feature(s)</small>`;b.onclick=()=>showFinding(f.identifier,b);queue.appendChild(b)}}
for(const id of ['search','rule','severity'])document.getElementById(id).addEventListener(id==='search'?'input':'change',renderQueue);renderQueue();
</script></body></html>"""
    return template.replace("__PAYLOAD__", payload)


@dataclass(frozen=True)
class ReviewOverrides:
    """Stage 4 decisions that change generation without changing the OSM.

    A movement the reviewer judged is no longer ambiguous, but nothing in the source
    records that: `turn:lanes` says what is permitted, not what a human concluded about
    one connector. So the verdict rides alongside the snapshot rather than being written
    into it, and Stage 2 keeps producing exactly what it produced before.

    `signal_lane_associations` is keyed on the **OSM node id**, not on the finding that
    asked the question. A finding's identifier covers its `affected_feature_ids`, so the
    moment the association changes the question is renamed and a verdict keyed on it stops
    matching the thing it answers. The node is what the reviewer actually pointed at.
    """

    forbidden_connector_ids: frozenset[str] = frozenset()
    active_connector_ids: frozenset[str] = frozenset()
    signal_lane_associations: Mapping[str, tuple[str, ...]] = MappingProxyType({})


# Module-level rather than a default argument: a mutable-looking default in the signature
# trips ruff B008, and every no-override caller should share one object anyway.
NO_OVERRIDES = ReviewOverrides()


def build_lane_model(
    *,
    snapshot: OsmSnapshot,
    graph: Any,
    config: ConverterConfig,
    driving_side: str,
    source_checksum: str,
    graph_checksum: str,
    config_checksum: str,
    fingerprint: str,
    coordinate_system_wkt: str,
    overrides: ReviewOverrides = NO_OVERRIDES,
) -> tuple[PreliminaryLaneModel, dict[str, int]]:
    """Build the lane model from an OSM snapshot and its projected graph.

    Pure with respect to the workspace: it reads no files and writes none. Stage 2 calls
    it with `NO_OVERRIDES` over the acquired source; Stage 4 calls it with the reviewer's
    verdicts over `review/reviewed.osm`. Everything either stage needs to differ about —
    which checksums identify the inputs, which movements were judged — arrives as an
    argument, so there is one generator rather than two that must be kept in step.
    """
    lanes: list[LaneFeature] = []
    findings: list[ReviewFinding] = []
    lanes_by_start: dict[str, list[str]] = {}
    lanes_by_end: dict[str, list[str]] = {}
    # Lane count, width and speed are properties of a *way*, and a decision on one
    # writes a tag onto that way. The loop below runs once per graph edge, so asking
    # per edge put the same question to the reviewer once for every segment a road
    # happens to be split into. Accumulate here and emit one finding per answer.
    #
    # The proposed value is part of the key, so two edges of a way that genuinely
    # disagreed still produce two findings. Only identical questions are merged.
    lane_count_findings: dict[tuple[tuple[str, ...], str, int, str, str], list[str]] = {}
    width_findings: dict[tuple[tuple[str, ...], float], list[str]] = {}
    speed_findings: dict[tuple[tuple[str, ...], float], list[str]] = {}
    # Where each junction begins, so lanes stop at its edge instead of piling up on the node at
    # its centre. Computed over the whole graph first because the setback at one end of an edge
    # depends on the other roads at that node, which this loop has not reached yet.
    # Which `lanes=1` ways Stage 1 was able to read as one-way. Derived here, once, so
    # every question the loops below ask about a way's carriageway gets the same answer.
    single_direction = _single_direction_ways(graph, snapshot)
    setbacks = _node_setbacks(graph, snapshot, config, single_direction)
    trim_clamped: list[str] = []
    for u, v, key, data in sorted(
        graph.edges(keys=True, data=True), key=lambda item: tuple(map(str, item[:3]))
    ):
        way_ids = _way_ids(data)
        if not way_ids:
            continue
        way = snapshot.ways.get(way_ids[0])
        if way is None:
            raise GenerationError(f"projected edge references missing OSM way {way_ids[0]}")
        direction = _edge_direction(way, str(u), str(v))
        one_way = way.identifier in single_direction
        count, count_reason, count_confidence = _directional_lane_count(
            way.tags, direction, one_way_in_graph=one_way
        )
        width_total = _positive_float(way.tags.get("width"))
        width = (
            width_total / max(_positive_int(way.tags.get("lanes")) or count, 1)
            if width_total
            else config.lane_width_defaults.vehicle
        )
        speed = _speed_kph(way.tags.get("maxspeed")) or config.speed_defaults_kph.get(
            way.tags.get("highway", ""), config.default_speed_kph
        )
        base = _edge_geometry(graph, u, v, data)
        # Cut the shared edge geometry rather than each offset lane: every lane of this edge
        # then stops at the same station, and `_lane_surface` derives the boundaries and the
        # polygon from the trimmed centreline, so all four stay consistent for free.
        base, clamped = _trimmed_edge(base, setbacks.get(str(u), 0.0), setbacks.get(str(v), 0.0))
        if clamped:
            trim_clamped.append(f"{u}->{v}")
        created: list[str] = []
        side_sign = 1.0 if driving_side == "left" else -1.0
        centred = _carries_whole_carriageway(way.tags, one_way_in_graph=one_way)
        for lane_index in range(count):
            offset = _lane_offset(
                lane_index, lane_count=count, width=width, side_sign=side_sign, centred=centred
            )
            center = base.offset_curve(offset, join_style="mitre")
            if not isinstance(center, LineString) or center.is_empty:
                center = base
            polygon, left, right = _lane_surface(center, width)
            lane_id = deterministic_id("lane", *way_ids, str(u), str(v), str(key), str(lane_index))
            lane = LaneFeature(
                identifier=lane_id,
                source_way_ids=way_ids,
                source_edge=[str(u), str(v), str(key)],
                lane_index=lane_index,
                lane_count=count,
                direction=direction,
                road_class=way.tags.get("highway", "unknown"),
                width_m=width,
                speed_limit_kph=speed,
                centerline=_points(center),
                polygon=_polygon_points(polygon),
                boundaries=[
                    LaneBoundary(
                        identifier=deterministic_id("boundary", lane_id, "left"),
                        side="left",
                        points=_points(left),
                    ),
                    LaneBoundary(
                        identifier=deterministic_id("boundary", lane_id, "right"),
                        side="right",
                        points=_points(right),
                    ),
                ],
                turn_permissions=_turn_permissions(
                    way.tags,
                    direction,
                    lane_index,
                    count,
                    driving_side,
                ),
            )
            lanes.append(lane)
            created.append(lane_id)
            lanes_by_start.setdefault(str(u), []).append(lane_id)
            lanes_by_end.setdefault(str(v), []).append(lane_id)
        for lane_index, _lane_id in enumerate(created):
            if side_sign > 0:
                lane_lookup_left = created[lane_index + 1] if lane_index + 1 < count else None
                lane_lookup_right = created[lane_index - 1] if lane_index > 0 else None
            else:
                lane_lookup_left = created[lane_index - 1] if lane_index > 0 else None
                lane_lookup_right = created[lane_index + 1] if lane_index + 1 < count else None
            lanes[-count + lane_index].left_neighbor = lane_lookup_left
            lanes[-count + lane_index].right_neighbor = lane_lookup_right
        if count_reason not in {
            "explicit_directional",
            "explicit_total_oneway",
            "complementary_directional",
        }:
            lane_count_findings.setdefault(
                (tuple(way_ids), direction, count, count_reason, count_confidence), []
            ).extend(created)
        if width_total is None:
            width_findings.setdefault((tuple(way_ids), width), []).extend(created)
        if _speed_kph(way.tags.get("maxspeed")) is None:
            speed_findings.setdefault((tuple(way_ids), speed), []).extend(created)

    # `deterministic_id` folds affected ids in positionally, so merge order must not
    # decide the identifier: sort.
    for (
        count_way_ids,
        direction,
        count,
        count_reason,
        count_confidence,
    ), affected in lane_count_findings.items():
        findings.append(
            _finding(
                rule="lane_count_inference",
                severity="blocker" if count_confidence == "low" else "warning",
                source_type="way",
                source_ids=list(count_way_ids),
                affected_feature_ids=sorted(set(affected)),
                proposed_value={"direction": direction, "lane_count": count},
                confidence=count_confidence,
                reason=count_reason,
            )
        )
    for (width_way_ids, width_value), affected in width_findings.items():
        findings.append(
            _finding(
                rule="lane_width_default",
                severity="warning",
                source_type="way",
                source_ids=list(width_way_ids),
                affected_feature_ids=sorted(set(affected)),
                proposed_value=width_value,
                confidence="medium",
                reason="no usable explicit OSM width",
            )
        )
    for (speed_way_ids, speed_value), affected in speed_findings.items():
        findings.append(
            _finding(
                rule="speed_default",
                severity="warning",
                source_type="way",
                source_ids=list(speed_way_ids),
                affected_feature_ids=sorted(set(affected)),
                proposed_value=speed_value,
                confidence="medium",
                reason="no usable explicit OSM maxspeed",
            )
        )

    lane_lookup = {lane.identifier: lane for lane in lanes}
    movement_candidates: list[MovementCandidate] = []
    direct_continuations = 0
    # Every link a lane keeps at a node, whichever kind. A continuation never becomes a
    # movement candidate, so the two have to be gathered separately to see a transition
    # whole; the lane-count check below reads both.
    continuation_links: list[tuple[str, str, str]] = []
    restriction_nodes = {
        value
        for relation in snapshot.relations.values()
        if relation.tags.get("type") == "restriction"
        for kind, value in restriction_roles(relation)["via"]
        if kind == "node"
    }
    node_restrictions = [
        relation
        for relation in sorted(snapshot.relations.values(), key=lambda item: item.identifier)
        if relation.tags.get("type") == "restriction"
        and not any(kind == "way" for kind, _ in restriction_roles(relation)["via"])
    ]
    for node_id in sorted(set(lanes_by_start) | set(lanes_by_end)):
        incoming = sorted(lanes_by_end.get(node_id, []))
        outgoing = sorted(lanes_by_start.get(node_id, []))
        graph_node_id = next(key for key in graph.nodes if str(key) == node_id)
        adjacent_nodes = {str(neighbor) for neighbor in graph.predecessors(graph_node_id)} | {
            str(neighbor) for neighbor in graph.successors(graph_node_id)
        }
        node = snapshot.nodes.get(node_id)
        controlled_node = bool(
            node
            and (
                node.tags.get("highway") in {"traffic_signals", "stop", "give_way"}
                or node.tags.get("junction") is not None
            )
        )
        outgoing_groups: dict[GroupKey, list[LaneFeature]] = {}
        for to_id in outgoing:
            target = lane_lookup[to_id]
            group_key = (target.source_way_ids[0], tuple(target.source_edge))
            outgoing_groups.setdefault(group_key, []).append(target)
        for targets in outgoing_groups.values():
            targets.sort(key=lambda item: (item.lane_index, item.identifier))
        # Every lane of an approach shares the same destinations, so which lane peels
        # off is a question about the approach as a whole and cannot be answered one
        # lane at a time: asked separately, two destinations both claim the kerb.
        blocks = _approach_blocks(incoming, lane_lookup)
        blocks_by_edge = {tuple(block[0].source_edge): block for block in blocks}
        approach_assignments: dict[tuple[str, ...], dict[GroupKey, dict[str, str]]] = {}
        for block in blocks:
            assignment = _balanced_approach_assignment(
                block, outgoing_groups, driving_side=driving_side
            )
            if assignment is not None:
                approach_assignments[tuple(block[0].source_edge)] = assignment
        # A merge cannot also be a diverge: every approach of a clean merge brings
        # strictly fewer lanes than the one destination holds, so the rule above has
        # already declined each of them and there is nothing to overwrite.
        approach_assignments.update(
            _balanced_merge_assignment(
                blocks, outgoing_groups, driving_side=driving_side
            )
        )
        for from_id in incoming:
            source = lane_lookup[from_id]
            source_line = LineString((point.x, point.y) for point in source.centerline)
            candidates_for_lane: list[MovementCandidate] = []
            permission_removed: list[MovementCandidate] = []
            carries_straight_on = False
            allocated = approach_assignments.get(tuple(source.source_edge), {})
            non_reverse_groups = [
                targets
                for targets in outgoing_groups.values()
                if not _is_exact_reverse(source, targets[0])
            ]
            uturn_status = uturn_evidence_status(source.turn_permissions)
            explicit_reverse = uturn_status == "active"
            decision_node = _is_decision_node(
                non_reverse_group_count=len(non_reverse_groups),
                adjacent_node_count=len(adjacent_nodes),
                has_control_or_restriction=node_id in restriction_nodes or controlled_node,
                explicit_reverse=explicit_reverse,
            )
            for group_key, targets in outgoing_groups.items():
                exact_reverse = _is_exact_reverse(source, targets[0])
                if exact_reverse and not decision_node:
                    continue
                allocation = allocated.get(group_key)
                if allocation is not None and source.identifier not in allocation:
                    # The approach balances and this lane's one destination is elsewhere.
                    continue
                # Every lane in a group is a parallel offset of one edge, so the group's
                # first lane answers both questions below for the whole group.
                is_continuation = not exact_reverse and (
                    source.source_way_ids[0] == targets[0].source_way_ids[0]
                    or not decision_node
                )
                # The side has to be known before the target is picked, so classify the
                # movement against the group. A continuation carries no side: a
                # carriageway that merely bends past the side threshold is not a turn,
                # and snapping its lanes to the kerb would shuffle the whole block.
                side = None
                side_block: list[LaneFeature] | None = None
                if not is_continuation and allocation is None:
                    group_angle = signed_turn_angle(
                        source_line,
                        LineString((point.x, point.y) for point in targets[0].centerline),
                    )
                    side = movement_side(
                        movement=classify_movement(group_angle),
                        angle=group_angle,
                        driving_side=driving_side,
                        turn_permissions=source.turn_permissions,
                        min_degrees=config.lane_selection.side_movement_min_degrees,
                    )
                    if side is not None:
                        # `turn:lanes=right|right` puts both lanes offside. The side says
                        # where the block starts; without the block behind it every lane
                        # of the block would be answered the same index.
                        side_block = _tagged_side_block(
                            blocks_by_edge[tuple(source.source_edge)],
                            side,
                            driving_side=driving_side,
                        )
                target = (
                    lane_lookup[allocation[source.identifier]]
                    if allocation is not None
                    else targets[_mapped_lane_index(source, len(targets), side, side_block)]
                )
                if is_continuation:
                    source.exit_lanes.append(target.identifier)
                    target.entry_lanes.append(source.identifier)
                    continuation_links.append((node_id, source.identifier, target.identifier))
                    direct_continuations += 1
                    carries_straight_on = True
                    continue
                target_line = LineString((point.x, point.y) for point in target.centerline)
                angle = signed_turn_angle(source_line, target_line)
                movement = classify_movement(angle)
                if movement == "reverse":
                    if uturn_status == "excluded":
                        continue
                    if source.lane_index != 0 and not explicit_reverse:
                        continue
                    permitted = True
                else:
                    # A `turn:lanes` value is surveyed evidence; the movement class is
                    # inferred from an angle threshold. Where the two disagree the tag
                    # must not be the reason a lane loses its only exit, so park the
                    # mismatch here and restore it below if nothing else survives.
                    permitted = not source.turn_permissions or any(
                        movement_matches(permission, movement)
                        for permission in source.turn_permissions
                    )
                curve = connector_curve(
                    source_line,
                    target_line,
                    (
                        float(graph.nodes[graph_node_id]["x"]),
                        float(graph.nodes[graph_node_id]["y"]),
                    ),
                )
                candidate = MovementCandidate(
                    junction_node_id=node_id,
                    from_lane_id=from_id,
                    to_lane_id=target.identifier,
                    from_way_id=source.source_way_ids[0],
                    to_way_id=target.source_way_ids[0],
                    movement=movement,
                    angle_degrees=angle,
                    centerline=curve,
                    ambiguous=False,
                )
                if permitted:
                    candidates_for_lane.append(candidate)
                else:
                    permission_removed.append(candidate)
            # `turn:lanes` says which movements are allowed, not which movements exist,
            # so it must never be the reason a lane loses its only exit. Restore the
            # straightest rejected movement and record the disagreement for review.
            restored = _stranded_permission_fallback(
                candidates_for_lane,
                permission_removed,
                has_continuation=carries_straight_on,
            )
            if restored is not None:
                candidates_for_lane = [restored]
                findings.append(
                    _finding(
                        rule="turn_permission_geometry_conflict",
                        severity="blocker",
                        source_type="node",
                        source_ids=[node_id],
                        affected_feature_ids=[source.identifier, restored.to_lane_id],
                        proposed_value={
                            "turn_permissions": sorted(source.turn_permissions),
                            "restored_movement": restored.movement,
                            "restored_angle_degrees": round(restored.angle_degrees, 2),
                            "rejected_movements": sorted(
                                {item.movement for item in permission_removed}
                            ),
                        },
                        confidence="low",
                        reason=(
                            "turn:lanes permits no movement the geometry offers; "
                            "kept the straightest rather than stranding the lane"
                        ),
                    )
                )
            # A movement that leaves toward one side of the road belongs to that side's
            # lane, so drop it from any other lane of the approach. This runs before the
            # ambiguity pass below: with the duplicate gone the surviving candidate is
            # alone in its movement family and no longer needs review.
            candidates_for_lane = _side_filtered_candidates(
                candidates_for_lane,
                source=source,
                driving_side=driving_side,
                min_degrees=config.lane_selection.side_movement_min_degrees,
                node_restrictions=node_restrictions,
                has_continuation=carries_straight_on,
            )
            family_counts: dict[str, int] = {}
            for candidate in candidates_for_lane:
                family = movement_family(candidate.movement)
                family_counts[family] = family_counts.get(family, 0) + 1
            for candidate in candidates_for_lane:
                causes = _ambiguity_causes(
                    candidate,
                    source=source,
                    uturn_status=uturn_status,
                    family_count=family_counts[movement_family(candidate.movement)],
                    sharp_movement_min_degrees=(
                        config.lane_selection.sharp_movement_review_degrees
                    ),
                )
                movement_candidates.append(
                    MovementCandidate(
                        **{
                            **candidate.__dict__,
                            "ambiguous": bool(causes),
                            "ambiguity_causes": causes,
                        }
                    )
                )

    # Which ways feed which, so a via-way restriction can be told whether removing one step
    # of its route would also remove traffic it does not name. The reading is at way level
    # and so cannot tell a way's two directions apart: it can over-count what reaches a way,
    # never under-count it, and over-counting sends the restriction to review rather than
    # removing the wrong movement. That asymmetry is the whole safety of the test.
    #
    # Continuations are merged in for the same reason, not because a case is known: a
    # continuation only exists where a node joins exactly two ways, and the chain's own
    # movements are connectors, so the two cannot meet at the same node. Neither workspace
    # has one that changes an answer. It is here so the count stays an upper bound if that
    # ever stops being true.
    way_entries, way_exits = way_adjacency(movement_candidates)
    for _node_id, source_lane_id, target_lane_id in continuation_links:
        source_way = lane_lookup[source_lane_id].source_way_ids[0]
        target_way = lane_lookup[target_lane_id].source_way_ids[0]
        if source_way == target_way:
            continue
        way_entries.setdefault(target_way, set()).add(source_way)
        way_exits.setdefault(source_way, set()).add(target_way)

    relation_status: dict[str, tuple[str, set[int], str]] = {}
    forbidden_indexes: set[int] = set()
    # Movements a via-way restriction covers but cannot remove on its own, held for the
    # reviewer. `review_required` keeps them out of the lane graph exactly as `forbidden`
    # does, so nothing prohibited becomes drivable while the question is open.
    review_indexes: set[int] = set()
    restriction_of_index: dict[int, list[str]] = {}
    for relation in sorted(snapshot.relations.values(), key=lambda item: item.identifier):
        if relation.tags.get("type") != "restriction":
            continue
        roles = restriction_roles(relation)
        via_way_ids = [value for kind, value in roles["via"] if kind == "way"]
        if via_way_ids:
            status, removed, reason = via_way_resolution(
                relation, movement_candidates, way_entries, way_exits
            )
            if status == "review_required" and removed:
                review_indexes.update(removed)
                for index in removed:
                    restriction_of_index.setdefault(index, []).append(relation.identifier)
                relation_status[relation.identifier] = (status, removed, reason)
                continue
        else:
            removed = {
                index
                for index, candidate in enumerate(movement_candidates)
                if forbidden_by_node_restriction(candidate, relation)
            }
            status = "enforced" if removed else "already_satisfied"
            reason = (
                "node-via restriction removed matching movement"
                if removed
                else "prohibited node-via movement was already absent"
            )
        relation_status[relation.identifier] = (status, removed, reason)
        forbidden_indexes.update(removed)

    # A movement a restriction covers but cannot claim on its own is ambiguous in the same
    # sense a geometric one is: something real says it may be wrong and only a person can
    # settle it. Marked on the candidate rather than handled beside it, so the status rule
    # and the finding below need no second path.
    for index in sorted(review_indexes):
        held = movement_candidates[index]
        movement_candidates[index] = MovementCandidate(
            **{
                **held.__dict__,
                "ambiguous": True,
                "ambiguity_causes": ("restriction_not_expressible", *held.ambiguity_causes),
            }
        )

    connectors: list[ConnectorFeature] = []
    for index, candidate in enumerate(movement_candidates):
        connector_id = deterministic_id(
            "connector",
            candidate.junction_node_id,
            candidate.from_lane_id,
            candidate.to_lane_id,
        )
        width = min(
            lane_lookup[candidate.from_lane_id].width_m,
            lane_lookup[candidate.to_lane_id].width_m,
        )
        status = (
            "forbidden"
            if index in forbidden_indexes
            else "review_required"
            if candidate.ambiguous
            else "active"
        )
        # A reviewed movement stops being a question. Forbidding drops it from the lane
        # graph below; promoting wires it in and, because only `review_required` raises
        # the finding, stops it being asked again — no second code path either way.
        if connector_id in overrides.forbidden_connector_ids:
            status = "forbidden"
        elif connector_id in overrides.active_connector_ids and status == "review_required":
            status = "active"
        connectors.append(
            ConnectorFeature(
                identifier=connector_id,
                junction_node_id=candidate.junction_node_id,
                from_lane_id=candidate.from_lane_id,
                to_lane_id=candidate.to_lane_id,
                from_way_id=candidate.from_way_id,
                to_way_id=candidate.to_way_id,
                movement=candidate.movement,
                turn_angle_degrees=round(candidate.angle_degrees, 3),
                status=status,
                centerline=_points(candidate.centerline),
                # Round joins, not mitre: a mitre on a turn this tight throws long spikes off
                # the outside of the bend, and the polygon is what MetaDrive paints as road.
                polygon=_polygon_points(
                    candidate.centerline.buffer(width / 2, cap_style="flat", join_style="round")
                ),
            )
        )
        if status == "active":
            lane_lookup[candidate.from_lane_id].exit_lanes.append(connector_id)
            lane_lookup[candidate.to_lane_id].entry_lanes.append(connector_id)
        elif status == "review_required":
            findings.append(
                _finding(
                    rule="ambiguous_connector",
                    severity="blocker",
                    source_type="node",
                    source_ids=[candidate.junction_node_id],
                    affected_feature_ids=[connector_id],
                    proposed_value={
                        "movement": candidate.movement,
                        "to_lane_id": candidate.to_lane_id,
                        "ambiguity_causes": list(candidate.ambiguity_causes),
                        "turn_angle_degrees": round(candidate.angle_degrees, 3),
                        **(
                            {"restriction_relation_ids": sorted(restriction_of_index[index])}
                            if index in restriction_of_index
                            else {}
                        ),
                    },
                    confidence="low",
                    reason=_ambiguity_reason(
                        candidate,
                        sharp_movement_min_degrees=(
                            config.lane_selection.sharp_movement_review_degrees
                        ),
                    ),
                )
            )

    # Only once every movement has been filtered, restored, side-resolved and either
    # kept or forbidden is it known which lanes a transition really joins. Asked any
    # earlier, the question is put about candidate pairs that the passes above go on to
    # discard, and the reviewer is shown a turn that no vehicle can make.
    findings.extend(_lane_collapse_findings(connectors, continuation_links, lane_lookup))

    # Geometry only. Every movement, angle and status above was decided from the
    # untapered OSM geometry and stays as it is: letting a taper change an angle would
    # let it change the classification that selected it.
    taper_plan = _merge_taper_plan(
        connectors, lane_lookup, min_gap=config.lane_geometry.merge_taper_min_gap_m
    )
    for (lane_id, moved_end), destination in sorted(taper_plan.items()):
        lane = lane_lookup[lane_id]
        tapered = _tapered_line(
            LineString((point.x, point.y) for point in lane.centerline),
            at_end=moved_end == "end",
            target=destination,
            taper_length=config.lane_geometry.merge_taper_length_m,
        )
        polygon, left, right = _lane_surface(tapered, lane.width_m)
        lane.centerline = _points(tapered)
        lane.polygon = _polygon_points(polygon)
        lane.boundaries = [
            LaneBoundary(
                identifier=deterministic_id("boundary", lane_id, "left"),
                side="left",
                points=_points(left),
            ),
            LaneBoundary(
                identifier=deterministic_id("boundary", lane_id, "right"),
                side="right",
                points=_points(right),
            ),
        ]
    if taper_plan:
        tapered_lanes = {lane_id for lane_id, _ in taper_plan}
        node_xy = {
            str(node): (float(data["x"]), float(data["y"]))
            for node, data in graph.nodes(data=True)
        }
        for connector in connectors:
            if not tapered_lanes & {connector.from_lane_id, connector.to_lane_id}:
                continue
            curve = connector_curve(
                LineString(
                    (point.x, point.y)
                    for point in lane_lookup[connector.from_lane_id].centerline
                ),
                LineString(
                    (point.x, point.y) for point in lane_lookup[connector.to_lane_id].centerline
                ),
                node_xy[connector.junction_node_id],
            )
            width = min(
                lane_lookup[connector.from_lane_id].width_m,
                lane_lookup[connector.to_lane_id].width_m,
            )
            connector.centerline = _points(curve)
            connector.polygon = _polygon_points(
                curve.buffer(width / 2, cap_style="flat", join_style="mitre")
            )

    # Where the source road simply stops, so a signal there can be told apart from one
    # that lost its lanes to a defect. Derived from the snapshot rather than read from
    # disk, which is what keeps this function pure; `osm_source.way_terminus_nodes` is the
    # same verdict Stage 5 reaches about the same map.
    terminus_nodes = way_terminus_nodes(snapshot)
    signals: list[SignalAssociation] = []
    for node in sorted(snapshot.nodes.values(), key=lambda item: item.identifier):
        if node.tags.get("highway") != "traffic_signals" and "traffic_signals" not in node.tags:
            continue
        associated, severity, confidence, reason = _signal_association(
            approaching=sorted(set(lanes_by_end.get(node.identifier, []))),
            released=sorted(set(lanes_by_start.get(node.identifier, []))),
            is_terminus=node.identifier in terminus_nodes,
        )
        # The reviewer's own answer outranks both. Keyed on the node for the reason given
        # in `ReviewOverrides`; unknown lane ids are refused rather than dropped, because a
        # silently ignored association is a review that did not happen.
        override = overrides.signal_lane_associations.get(node.identifier)
        if override is not None:
            unknown = [lane_id for lane_id in override if lane_id not in lane_lookup]
            if unknown:
                raise GenerationError(
                    f"the review associates signal node {node.identifier} with lanes that "
                    f"are not in the model: {', '.join(sorted(unknown)[:5])}"
                )
            associated, severity, confidence, reason = sorted(set(override)), None, "high", ""
        status = "mapped" if associated else "review_required"
        association_id = deterministic_id("signal-association", node.identifier, *associated)
        signals.append(
            SignalAssociation(
                identifier=association_id,
                source_node_id=node.identifier,
                lane_ids=associated,
                status=status,
            )
        )
        # Raised whenever the association was inferred rather than measured, whatever its
        # severity. An association the reviewer never sees is one nobody agreed to.
        if severity is not None:
            findings.append(
                _finding(
                    rule="signal_lane_association",
                    severity=severity,
                    source_type="node",
                    source_ids=[node.identifier],
                    affected_feature_ids=associated,
                    proposed_value=associated,
                    confidence=confidence,
                    reason=reason,
                )
            )

    stop_lines: list[StopLine] = []
    for signal in signals:
        # A stop line is where traffic waits *before* a signal, and the placement below
        # measures back from the lane's downstream end - which is the signal only for a
        # lane that ends there. A lane the signal releases starts at it, so measuring from
        # its far end would put the stop line at the *other* end of the lane, tens of
        # metres past the junction and facing the wrong way. Nothing waits on such a lane,
        # so it gets no stop line at all.
        ends_here = set(lanes_by_end.get(signal.source_node_id, []))
        for lane_id in signal.lane_ids:
            if lane_id not in ends_here:
                continue
            lane = lane_lookup[lane_id]
            line = LineString((point.x, point.y) for point in lane.centerline)
            distance = max(0.0, line.length - 2.0)
            point = line.interpolate(distance)
            before = line.interpolate(max(0.0, distance - 0.25))
            dx, dy = point.x - before.x, point.y - before.y
            norm = math.hypot(dx, dy) or 1.0
            half = lane.width_m / 2
            endpoints = [
                Point2D(x=point.x - dy / norm * half, y=point.y + dx / norm * half),
                Point2D(x=point.x + dy / norm * half, y=point.y - dx / norm * half),
            ]
            stop_line_id = deterministic_id("stop-line", signal.source_node_id, lane_id)
            stop_lines.append(
                StopLine(
                    identifier=stop_line_id,
                    source_node_id=signal.source_node_id,
                    lane_ids=[lane_id],
                    points=endpoints,
                    source="inferred",
                    status="review_required",
                )
            )
            findings.append(
                _finding(
                    rule="inferred_stop_line",
                    severity="warning",
                    source_type="node",
                    source_ids=[signal.source_node_id],
                    affected_feature_ids=[stop_line_id, lane_id],
                    proposed_value={"distance_upstream_m": 2.0},
                    confidence="medium",
                    reason="no explicit stop-line geometry was available",
                )
            )

    restrictions: list[RestrictionEffect] = []
    for relation in sorted(snapshot.relations.values(), key=lambda item: item.identifier):
        if relation.tags.get("type") != "restriction":
            continue
        typed_roles = restriction_roles(relation)
        roles = {
            role: [reference for _kind, reference in members]
            for role, members in typed_roles.items()
        }
        status, removed, reason = relation_status[relation.identifier]
        covered_ids = [connectors[index].identifier for index in sorted(removed)]
        # Only movements this actually forbade. A restriction held for review covers
        # movements it has not removed, and they are carried on the findings instead:
        # naming them here would tell Stage 4 and `validation` they were forbidden.
        forbidden_ids = [] if status == "review_required" else covered_ids
        restriction = RestrictionEffect(
            identifier=deterministic_id("restriction-effect", relation.identifier),
            source_relation_id=relation.identifier,
            restriction=relation.tags.get("restriction", "unknown"),
            from_way_ids=roles["from"],
            via_member_ids=roles["via"],
            to_way_ids=roles["to"],
            status=status,
            forbidden_connector_ids=forbidden_ids,
            reason=reason,
        )
        restrictions.append(restriction)
        if status == "review_required":
            findings.append(
                _finding(
                    rule="restriction_effect_review",
                    severity="blocker",
                    source_type="relation",
                    source_ids=[relation.identifier],
                    # The movements it covers, whether or not it managed to remove them, so
                    # the page can put the relation and the held turns on the same map.
                    affected_feature_ids=covered_ids,
                    # Only when there is something to hold. `evidence_checksum` covers the
                    # proposed value, so an always-present empty key would re-identify the
                    # findings raised on chains that resolve to nothing and cost their
                    # review decisions for no change in what is being asked.
                    proposed_value={
                        **restriction.model_dump(mode="json"),
                        **({"held_connector_ids": covered_ids} if covered_ids else {}),
                    },
                    confidence="low",
                    reason=reason,
                )
            )
        elif status == "enforced" and any(kind == "way" for kind, _ in typed_roles["via"]):
            # Which step of the route was removed, and why the other one was not. A
            # via-way prohibition can be enforced two ways and the generator picks; this
            # is the record of the pick, so the choice is not made out of sight.
            findings.append(
                _finding(
                    rule="restriction_enforced_leg",
                    severity="warning",
                    source_type="relation",
                    source_ids=[relation.identifier],
                    affected_feature_ids=forbidden_ids,
                    proposed_value={
                        "restriction": restriction.restriction,
                        "chain": roles["from"] + roles["via"] + roles["to"],
                        "removed_connector_ids": forbidden_ids,
                    },
                    confidence="high",
                    reason=reason,
                )
            )

    metadata = GenerationMetadata(
        generator_version=GENERATOR_VERSION,
        lane_model_schema_version=LANE_MODEL_SCHEMA_VERSION,
        source_checksum=source_checksum,
        projected_graph_checksum=graph_checksum,
        configuration_checksum=config_checksum,
        generation_fingerprint=fingerprint,
        coordinate_system_wkt=coordinate_system_wkt,
    )
    # After every finding exists and, critically, after each one's evidence checksum
    # was computed: location is derived from `source_ids`, which the checksum already
    # covers, so keeping it out leaves decisions made before this field still valid.
    for finding in findings:
        finding.location = _finding_location(finding, snapshot)

    model = PreliminaryLaneModel(
        metadata=metadata,
        lanes=lanes,
        connectors=connectors,
        signals=signals,
        stop_lines=stop_lines,
        restrictions=restrictions,
        findings=sorted(findings, key=lambda item: item.identifier),
    )

    feature_counts = {
        "lanes": len(lanes),
        "connectors": len(connectors),
        "direct_continuations": direct_continuations,
        "merge_tapers": len(taper_plan),
        "signals": len(signals),
        "stop_lines": len(stop_lines),
        "restrictions": len(restrictions),
        "active_connectors": sum(item.status == "active" for item in connectors),
        "forbidden_connectors": sum(item.status == "forbidden" for item in connectors),
        "review_required_connectors": sum(
            item.status == "review_required" for item in connectors
        ),
        # Edges too short to carry both their junction setbacks, so the setbacks were scaled
        # down to leave `MIN_TRIMMED_LANE_M`. Their lanes still reach further into the junction
        # than the rest, so the number is worth reading rather than assuming it is zero.
        "trim_clamped_edges": len(trim_clamped),
        "findings": len(findings),
    }
    return model, feature_counts


def generate_lane_model(*, workspace: Path, config: ConverterConfig) -> Path:
    """Generate deterministic preliminary lane geometry from Stage 1 artifacts."""
    workspace = workspace.resolve()
    manifest_path = workspace / "source" / "manifest.json"
    if not manifest_path.is_file():
        raise GenerationError("Stage 1 manifest is missing; run fetch first")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("stage_1b", {}).get("status") != "passed":
        raise GenerationError("Stage 1B has not passed")

    source_path = workspace / manifest["source"]["path"]
    graph_artifact = manifest["stage_1b"]["artifacts"]["projected_graphml"]
    graph_path = workspace / graph_artifact["path"]
    for label, path, expected in (
        ("source OSM", source_path, manifest["source"]["sha256"]),
        ("projected GraphML", graph_path, graph_artifact["sha256"]),
    ):
        if not path.is_file() or _sha256(path) != expected:
            raise GenerationError(f"{label} checksum does not match the Stage 1 manifest")

    graph = ox.load_graphml(graph_path)
    snapshot = read_osm_snapshot(source_path)
    config_payload = config.model_dump(mode="json")
    config_checksum = _canonical_checksum(config_payload)
    fingerprint = _canonical_checksum(
        {
            "generator_version": GENERATOR_VERSION,
            "schema_version": LANE_MODEL_SCHEMA_VERSION,
            "source_checksum": manifest["source"]["sha256"],
            "graph_checksum": graph_artifact["sha256"],
            "configuration_checksum": config_checksum,
        }
    )

    model, feature_counts = build_lane_model(
        snapshot=snapshot,
        graph=graph,
        config=config,
        driving_side=manifest["driving_side"],
        source_checksum=manifest["source"]["sha256"],
        graph_checksum=graph_artifact["sha256"],
        config_checksum=config_checksum,
        fingerprint=fingerprint,
        coordinate_system_wkt=manifest["stage_1b"]["projection"]["local_crs_wkt"],
    )

    lane_model_dir = workspace / "lane-model"
    reports_dir = workspace / "reports"
    inspection_dir = workspace / "inspection"
    for directory in (lane_model_dir, reports_dir, inspection_dir):
        directory.mkdir(parents=True, exist_ok=True)
    model_path = lane_model_dir / "preliminary.json"
    report_path = reports_dir / "lane-model-generation.json"
    review_audit_path = inspection_dir / "stage-2-review-audit.html"
    model_path.write_text(
        json.dumps(model.model_dump(mode="json"), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    review_audit_path.write_text(_render_review_html(model, snapshot), encoding="utf-8")
    report = {
        "report_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "passed",
        "generator_version": GENERATOR_VERSION,
        "lane_model_schema_version": LANE_MODEL_SCHEMA_VERSION,
        "generation_fingerprint": fingerprint,
        "input_checksums": {
            "source_osm": manifest["source"]["sha256"],
            "projected_graphml": graph_artifact["sha256"],
            "configuration": config_checksum,
        },
        "feature_counts": feature_counts,
    }
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    artifacts = {}
    for name, path in (
        ("preliminary_lane_model", model_path),
        ("generation_report", report_path),
        ("review_audit_html", review_audit_path),
    ):
        artifacts[name] = {
            "path": path.relative_to(workspace).as_posix(),
            "sha256": _sha256(path),
            "size_bytes": path.stat().st_size,
        }
    manifest["stage_2"] = {
        "status": "passed",
        "generator_version": GENERATOR_VERSION,
        "lane_model_schema_version": LANE_MODEL_SCHEMA_VERSION,
        "generation_fingerprint": fingerprint,
        "input_checksums": report["input_checksums"],
        "artifacts": artifacts,
    }
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return report_path
