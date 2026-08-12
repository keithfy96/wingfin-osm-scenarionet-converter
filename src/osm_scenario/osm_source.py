"""OSM source indexing, public-road selection, and graph parity checks."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import networkx as nx

ROAD_SELECTION_POLICY_ID = "public-driving-v1"

PUBLIC_DRIVING_HIGHWAYS = {
    "living_street",
    "motorway",
    "motorway_link",
    "primary",
    "primary_link",
    "residential",
    "road",
    "secondary",
    "secondary_link",
    "tertiary",
    "tertiary_link",
    "trunk",
    "trunk_link",
    "unclassified",
}
PROHIBITED_ACCESS = {"no", "private"}
ONEWAY_VALUES = {"yes", "true", "1", "-1", "reverse", "T", "F"}
REVERSED_ONEWAY_VALUES = {"-1", "reverse", "T"}
NOT_ONEWAY_VALUES = {"no", "false", "0"}


@dataclass(frozen=True)
class OsmNode:
    identifier: str
    latitude: float
    longitude: float
    tags: dict[str, str]


@dataclass(frozen=True)
class OsmWay:
    identifier: str
    node_ids: tuple[str, ...]
    tags: dict[str, str]


@dataclass(frozen=True)
class OsmRelationMember:
    member_type: str
    reference: str
    role: str


@dataclass(frozen=True)
class OsmRelation:
    identifier: str
    members: tuple[OsmRelationMember, ...]
    tags: dict[str, str]


@dataclass(frozen=True)
class OsmSnapshot:
    nodes: dict[str, OsmNode]
    ways: dict[str, OsmWay]
    relations: dict[str, OsmRelation]
    deleted_elements: dict[str, tuple[str, ...]]


class SourceAuditError(RuntimeError):
    """Raised when the source and selected graph do not agree."""


def _is_deleted(element: ET.Element) -> bool:
    """Report whether an editor has flagged this element as deleted in the source XML."""
    # A file saved from an editor is an edit journal, not a snapshot: a deleted element
    # stays in the document carrying action='delete' (and its <nd> refs stripped), and
    # an element read back from the API with its history carries visible='false'. Both
    # mean the element is gone. OSMnx already skips them, so reading them here would
    # make the snapshot disagree with the very graph it is audited against.
    return element.attrib.get("action") == "delete" or element.attrib.get("visible") == "false"


def read_osm_snapshot(path: Path) -> OsmSnapshot:
    """Read source nodes, ways, and their exact string-valued tags."""
    root = ET.parse(path).getroot()
    nodes: dict[str, OsmNode] = {}
    ways: dict[str, OsmWay] = {}
    relations: dict[str, OsmRelation] = {}
    deleted: dict[str, list[str]] = {}

    for element in root:
        identifier = element.attrib.get("id")
        if identifier is None:
            continue
        if _is_deleted(element):
            deleted.setdefault(element.tag, []).append(identifier)
            continue
        tags = {
            tag.attrib["k"]: tag.attrib["v"]
            for tag in element.findall("tag")
            if "k" in tag.attrib and "v" in tag.attrib
        }
        if element.tag == "node":
            nodes[identifier] = OsmNode(
                identifier=identifier,
                latitude=float(element.attrib["lat"]),
                longitude=float(element.attrib["lon"]),
                tags=tags,
            )
        elif element.tag == "way":
            ways[identifier] = OsmWay(
                identifier=identifier,
                node_ids=tuple(
                    child.attrib["ref"] for child in element.findall("nd") if "ref" in child.attrib
                ),
                tags=tags,
            )
        elif element.tag == "relation":
            relations[identifier] = OsmRelation(
                identifier=identifier,
                members=tuple(
                    OsmRelationMember(
                        member_type=member.attrib["type"],
                        reference=member.attrib["ref"],
                        role=member.attrib.get("role", ""),
                    )
                    for member in element.findall("member")
                    if "type" in member.attrib and "ref" in member.attrib
                ),
                tags=tags,
            )
    return OsmSnapshot(
        nodes=nodes,
        ways=ways,
        relations=relations,
        deleted_elements={kind: tuple(sorted(ids)) for kind, ids in sorted(deleted.items())},
    )


def way_terminus_nodes(snapshot: OsmSnapshot) -> set[str]:
    """OSM nodes where no source way continues through.

    A node is a terminus when it is the first or last node of *every* way that contains
    it. A feature sitting on one of those is at the edge of the extract: the road does
    not continue there because the file was cut there. A feature at any other node sits
    somewhere a road runs through, and a gap at one of those is a defect.

    Lives here rather than in either caller because both stages need the same verdict from
    different inputs. Stage 2 has the snapshot in hand and must stay pure, and Stage 5
    reads the source OSM from disk; giving each its own parse would let the two drift into
    disagreeing about where the map ends.

    Deliberately blind to whether a way is drivable. `public-driving-v1` can exclude the
    only way through a node, which leaves lanes stopping at a node the *source* still runs
    through - a selection consequence, not an edge of the extract, and one that should be
    reported rather than absorbed.
    """
    positions: dict[str, list[tuple[int, int]]] = {}
    for way in snapshot.ways.values():
        for index, node_id in enumerate(way.node_ids):
            positions.setdefault(node_id, []).append((index, len(way.node_ids)))
    return {
        node_id
        for node_id, seen in positions.items()
        if all(index in (0, length - 1) for index, length in seen)
    }


def road_exclusion_reason(tags: dict[str, str]) -> str | None:
    """Return why a way is outside the versioned public-driving policy."""
    highway = tags.get("highway")
    if highway is None:
        return "not_a_highway"
    if highway not in PUBLIC_DRIVING_HIGHWAYS:
        return f"highway={highway}"
    if tags.get("area") == "yes":
        return "area=yes"
    for key in ("access", "vehicle", "motor_vehicle", "motorcar"):
        value = tags.get(key)
        if value in PROHIBITED_ACCESS:
            return f"{key}={value}"
    return None


def select_public_driving_graph(
    graph: nx.MultiDiGraph, snapshot: OsmSnapshot
) -> tuple[nx.MultiDiGraph, dict[str, Any]]:
    """Filter an OSMnx graph and verify selected ways against the source XML."""
    selected_way_ids = {
        way_id for way_id, way in snapshot.ways.items() if road_exclusion_reason(way.tags) is None
    }
    excluded_counts: dict[str, int] = {}
    ignored_non_highway_ways = 0
    for way in snapshot.ways.values():
        reason = road_exclusion_reason(way.tags)
        if reason == "not_a_highway":
            ignored_non_highway_ways += 1
        elif reason is not None:
            excluded_counts[reason] = excluded_counts.get(reason, 0) + 1

    retained_edges: list[tuple[Any, Any, Any]] = []
    unexpected_way_ids: set[str] = set()
    for u, v, key, data in graph.edges(keys=True, data=True):
        osmids = _osmid_values(data.get("osmid"))
        matching = osmids & selected_way_ids
        if matching:
            retained_edges.append((u, v, key))
        else:
            unexpected_way_ids.update(osmids)

    selected = graph.edge_subgraph(retained_edges).copy()
    for _, data in selected.nodes(data=True):
        data.pop("osm_tags_json", None)
    for node_id, data in selected.nodes(data=True):
        source = snapshot.nodes.get(str(node_id))
        if source is not None:
            data["osm_tags_json"] = json.dumps(source.tags, sort_keys=True, separators=(",", ":"))
    for *_, data in selected.edges(keys=True, data=True):
        osmids = sorted(_osmid_values(data.get("osmid")))
        source_tags = {
            way_id: snapshot.ways[way_id].tags
            for way_id in osmids
            if way_id in snapshot.ways
        }
        data["osm_tags_json"] = json.dumps(source_tags, sort_keys=True, separators=(",", ":"))

    single_lane_oneway, single_lane_report = _apply_single_lane_oneway(
        selected, snapshot, selected_way_ids
    )

    audit = _audit_selected_graph(
        selected, snapshot, selected_way_ids, single_lane_oneway=single_lane_oneway
    )
    audit.update(
        {
            "policy_id": ROAD_SELECTION_POLICY_ID,
            # `lanes=1` with no `oneway` read as one-way, and the ones where reading it
            # that way would have cut a road off. Reported for the same reason
            # `deleted_source_elements` is: an inference nobody can see is one nobody
            # agreed to, and the blocked ones are what Stage 3 has to judge.
            "single_lane_oneway": single_lane_report,
            "source_way_count": len(snapshot.ways),
            "selected_source_ways": len(selected_way_ids),
            "excluded_source_ways": sum(excluded_counts.values()),
            "ignored_non_highway_ways": ignored_non_highway_ways,
            "excluded_by_reason": dict(sorted(excluded_counts.items())),
            # Reported rather than dropped in silence: a way removed before selection
            # never reaches excluded_by_reason, so this is the only place a reviewer
            # can see that the source once described a road here.
            "deleted_source_elements": {
                kind: list(ids) for kind, ids in snapshot.deleted_elements.items()
            },
            "filtered_graph_way_count": len(unexpected_way_ids - selected_way_ids),
        }
    )
    audit["status"] = "failed" if audit["errors"] else "passed"
    return selected, audit


def _osmid_values(value: Any) -> set[str]:
    if isinstance(value, (list, tuple, set)):
        return {str(item) for item in value}
    return {str(value)} if value is not None else set()


def _against_the_grain(
    graph: nx.MultiDiGraph, way: OsmWay
) -> list[tuple[Any, Any, Any]]:
    """This way's directed edges that run against its own node order.

    An edge is only claimed when the way names it *and* the way does not also run that
    way round: a way that doubles back on itself contributes both directions to the same
    node pair from its own node list, and neither of them is a reverse.
    """
    pairs = set(zip(way.node_ids, way.node_ids[1:], strict=False))
    return [
        (u, v, key)
        for u, v, key, data in graph.edges(keys=True, data=True)
        if way.identifier in _osmid_values(data.get("osmid"))
        and (str(v), str(u)) in pairs
        and (str(u), str(v)) not in pairs
    ]


def _flood(graph: nx.MultiDiGraph, sources: set[Any], *, upstream: bool) -> set[Any]:
    """Multi-source flood fill, against the arrows when `upstream`."""
    step = graph.pred if upstream else graph.succ
    seen = {node for node in sources if node in graph}
    queue = list(seen)
    while queue:
        for neighbour in step[queue.pop()]:
            if neighbour not in seen:
                seen.add(neighbour)
                queue.append(neighbour)
    return seen


def _apply_single_lane_oneway(
    graph: nx.MultiDiGraph, snapshot: OsmSnapshot, selected_way_ids: set[str]
) -> tuple[frozenset[str], dict[str, list[dict[str, Any]]]]:
    """Make `lanes=1` ways one-way in the graph, but never at the cost of a way out.

    `single_lane_implies_oneway` reads the tags; this decides whether the network can
    afford the reading. Dropping the reverse direction of a road that is the only route
    off a spur does not merely simplify the model, it strands every driver on it — so the
    test is what the drop costs, measured against the network, not what the road looks
    like. Where it costs something the way is left exactly as it was, and the
    `lane_count_inference` blocker Stage 2 already raises on it is what carries the
    question to the reviewer.

    Having a way out means reaching the main network **or driving off the edge of the
    map**, and the second half is not optional: an extract ends somewhere, and a merge
    slip whose far end continues west out of the file is not a trap. Both are pinned
    before anything is dropped — the main network as one node of the largest strongly
    connected component, so "the main network" cannot shrink under its own answer, and
    the edges of the map as the nodes that already had no way on from them at all. A
    cul-de-sac tip on a two-way road is not one of those: its way out is the reverse
    direction, which is exactly what is in question, so removing it strands the tip and
    the guard says so.

    Candidates are decided **one at a time against the graph the previous ones left**,
    because two ways can each be redundant alone and be the only way out together — a
    spur of two single-lane ways loses its exit on the second, not the first.
    """
    candidates = sorted(
        (
            way_id
            for way_id in selected_way_ids
            if way_id in snapshot.ways
            and single_lane_implies_oneway(snapshot.ways[way_id].tags)
        ),
        key=lambda way_id: (len(way_id), way_id),
    )
    applied: set[str] = set()
    report: dict[str, list[dict[str, Any]]] = {"applied": [], "blocked": []}
    if not candidates or graph.number_of_nodes() == 0:
        return frozenset(), report

    components = sorted(nx.strongly_connected_components(graph), key=len, reverse=True)
    anchor = min(components[0], key=str)
    off_the_map = {node for node, degree in graph.out_degree() if degree == 0}
    onto_the_map = {node for node, degree in graph.in_degree() if degree == 0}
    can_leave = {anchor} | off_the_map
    can_arrive = {anchor} | onto_the_map

    for way_id in candidates:
        way = snapshot.ways[way_id]
        entry: dict[str, Any] = {
            "osm_id": way_id,
            "highway": way.tags.get("highway"),
            "name": way.tags.get("name"),
        }
        reverse_edges = _against_the_grain(graph, way)
        if not reverse_edges:
            # Already one-way in the graph — nothing to drop, nothing to report.
            applied.add(way_id)
            continue
        before_out = _flood(graph, can_leave, upstream=True)
        before_in = _flood(graph, can_arrive, upstream=False)
        removed = [(u, v, key, dict(graph.edges[u, v, key])) for u, v, key in reverse_edges]
        graph.remove_edges_from(reverse_edges)
        after_out = _flood(graph, can_leave, upstream=True)
        after_in = _flood(graph, can_arrive, upstream=False)
        stranded = sorted((before_out - after_out) | (before_in - after_in), key=str)
        if stranded:
            for u, v, key, data in removed:
                graph.add_edge(u, v, key=key, **data)
            entry["would_strand"] = [str(node) for node in stranded]
            report["blocked"].append(entry)
            continue
        applied.add(way_id)
        entry["dropped_edges"] = len(reverse_edges)
        report["applied"].append(entry)
    return frozenset(applied), report


def single_lane_implies_oneway(tags: dict[str, str]) -> bool:
    """Whether `lanes=1` on this way should be read as one lane in one direction.

    A carriageway with one lane and traffic in both directions is a single-track road,
    and OSM does describe those — but it describes far more one-way slips whose mapper
    gave the lane count and left `oneway` off. Left alone, `_directional_lane_count`
    falls to `max(1, total // 2)` and builds a lane *each way*, so a road the source says
    is one lane wide comes out two lanes and 7 m across, with a U-turn at each end.

    Every surveyed statement outranks this inference and switches it off: an explicit
    `oneway=no` says two-way in so many words, a `lanes:forward`/`lanes:backward` names a
    direction's own count, and a roundabout is already one-way. It is a reading of one
    tag in the absence of others, never a reason to overrule one that is present.

    Whether the reading is *safe* is a separate question, answered by
    `_single_lane_oneway_plan` against the network rather than against the tags.
    """
    if tags.get("lanes") != "1":
        return False
    if "lanes:forward" in tags or "lanes:backward" in tags:
        return False
    if tags.get("oneway") in ONEWAY_VALUES or tags.get("oneway") in NOT_ONEWAY_VALUES:
        return False
    return tags.get("junction") != "roundabout"


def _expected_directions(way: OsmWay, single_lane_oneway: frozenset[str] = frozenset()) -> set[
    tuple[str, str]
]:
    pairs = set(zip(way.node_ids, way.node_ids[1:], strict=False))
    if way.identifier in single_lane_oneway:
        return pairs
    oneway = way.tags.get("oneway")
    is_oneway = oneway in ONEWAY_VALUES or (
        way.tags.get("junction") == "roundabout" and oneway != "no"
    )
    if not is_oneway:
        return pairs | {(v, u) for u, v in pairs}
    if oneway in REVERSED_ONEWAY_VALUES:
        return {(v, u) for u, v in pairs}
    return pairs


def _audit_selected_graph(
    graph: nx.MultiDiGraph,
    snapshot: OsmSnapshot,
    selected_way_ids: set[str],
    *,
    single_lane_oneway: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    represented: set[str] = set()
    actual_directions: dict[str, set[tuple[str, str]]] = {}
    tag_mismatches: list[str] = []
    coordinate_mismatches: list[str] = []

    for u, v, _, data in graph.edges(keys=True, data=True):
        osmids = _osmid_values(data.get("osmid"))
        represented.update(osmids)
        serialized = json.loads(data.get("osm_tags_json", "{}"))
        for way_id in osmids:
            actual_directions.setdefault(way_id, set()).add((str(u), str(v)))
            source = snapshot.ways.get(way_id)
            if source is not None and serialized.get(way_id) != source.tags:
                tag_mismatches.append(way_id)

    for node_id, data in graph.nodes(data=True):
        source = snapshot.nodes.get(str(node_id))
        if source is None:
            coordinate_mismatches.append(str(node_id))
            continue
        if abs(float(data["x"]) - source.longitude) > 1e-12 or abs(
            float(data["y"]) - source.latitude
        ) > 1e-12:
            coordinate_mismatches.append(str(node_id))

    direction_mismatches = []
    for way_id in sorted(selected_way_ids & represented):
        expected = _expected_directions(snapshot.ways[way_id], single_lane_oneway)
        actual = actual_directions.get(way_id, set())
        if actual != expected:
            direction_mismatches.append(
                {
                    "osm_id": way_id,
                    "missing": sorted(expected - actual),
                    "unexpected": sorted(actual - expected),
                }
            )

    missing_way_ids = sorted(selected_way_ids - represented)
    extra_way_ids = sorted(represented - selected_way_ids)
    errors = []
    for code, identifiers in (
        ("missing_selected_way", missing_way_ids),
        ("unexpected_way", extra_way_ids),
        ("source_tag_mismatch", sorted(set(tag_mismatches))),
        ("source_coordinate_mismatch", sorted(set(coordinate_mismatches))),
    ):
        if identifiers:
            errors.append({"code": code, "osm_ids": identifiers})
    if direction_mismatches:
        errors.append({"code": "direction_mismatch", "ways": direction_mismatches})

    return {
        "output_nodes": len(graph.nodes),
        "output_directed_edges": len(graph.edges),
        "represented_way_ids": len(represented),
        "missing_way_ids": missing_way_ids,
        "extra_way_ids": extra_way_ids,
        "tag_mismatch_way_ids": sorted(set(tag_mismatches)),
        "coordinate_mismatch_node_ids": sorted(set(coordinate_mismatches)),
        "direction_mismatches": direction_mismatches,
        "errors": errors,
    }
