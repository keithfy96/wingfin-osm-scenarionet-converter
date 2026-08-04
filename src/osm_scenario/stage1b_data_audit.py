"""Programmatic Stage 1B audit of normalized OSM source data."""

from __future__ import annotations

import ast
import json
import os
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import networkx as nx

from osm_scenario.config import ConverterConfig
from osm_scenario.osm_source import OsmSnapshot, read_osm_snapshot


class Stage1BDataAuditError(RuntimeError):
    """Raised when the Stage 1B data audit cannot be generated."""


def _graph_osm_ids(value: Any) -> set[str]:
    if isinstance(value, (list, tuple, set)):
        return {str(item) for item in value}
    if value is None:
        return set()
    if isinstance(value, str) and value.startswith("["):
        try:
            parsed = ast.literal_eval(value)
        except (SyntaxError, ValueError):
            pass
        else:
            if isinstance(parsed, (list, tuple, set)):
                return {str(item) for item in parsed}
    return {str(value)}


def _selected_way_ids(graph: nx.MultiDiGraph) -> set[str]:
    selected: set[str] = set()
    for *_, data in graph.edges(keys=True, data=True):
        selected.update(_graph_osm_ids(data.get("osmid")))
    return selected


def _warning_way_ids(acquisition_report: dict[str, Any], code: str) -> set[str]:
    identifiers: set[str] = set()
    for warning in acquisition_report["preflight"]["warnings"]:
        if warning["code"] != code or warning.get("osm_id") is None:
            continue
        identifiers.update(part.strip() for part in str(warning["osm_id"]).split(","))
    return identifiers


def _way_details(snapshot: OsmSnapshot, identifiers: set[str]) -> list[dict[str, Any]]:
    details = []
    for identifier in sorted(identifiers, key=lambda value: (len(value), value)):
        way = snapshot.ways.get(identifier)
        if way is None:
            details.append({"osm_way_id": identifier, "source_way_found": False})
            continue
        details.append(
            {
                "osm_way_id": identifier,
                "source_way_found": True,
                "highway": way.tags.get("highway"),
                "name": way.tags.get("name"),
                "oneway": way.tags.get("oneway"),
                "lanes": way.tags.get("lanes"),
                "lanes_forward": way.tags.get("lanes:forward"),
                "lanes_backward": way.tags.get("lanes:backward"),
                "width": way.tags.get("width"),
            }
        )
    return details


def _component_report(
    graph: nx.MultiDiGraph, snapshot: OsmSnapshot
) -> list[dict[str, Any]]:
    components = sorted(
        nx.weakly_connected_components(graph),
        key=lambda nodes: (-len(nodes), min(str(node) for node in nodes)),
    )
    result = []
    for index, nodes in enumerate(components, start=1):
        subgraph = graph.subgraph(nodes)
        way_ids = _selected_way_ids(subgraph)
        highway_counts = Counter(
            snapshot.ways[way_id].tags.get("highway", "<missing>")
            for way_id in way_ids
            if way_id in snapshot.ways
        )
        result.append(
            {
                "component_id": f"component-{index}",
                "is_primary": index == 1,
                "node_count": len(nodes),
                "directed_edge_count": len(subgraph.edges),
                "unique_osm_way_count": len(way_ids),
                "osm_way_ids": sorted(way_ids, key=lambda value: (len(value), value)),
                "highway_counts": dict(sorted(highway_counts.items())),
            }
        )
    return result


def _traffic_signal_report(
    graph: nx.MultiDiGraph, snapshot: OsmSnapshot, component_by_node: dict[str, str]
) -> dict[str, Any]:
    graph_node_ids = {str(node_id) for node_id in graph.nodes}
    signal_ids = sorted(
        (
            identifier
            for identifier, node in snapshot.nodes.items()
            if node.tags.get("highway") == "traffic_signals"
        ),
        key=lambda value: (len(value), value),
    )
    details = []
    for identifier in signal_ids:
        retained = identifier in graph_node_ids
        details.append(
            {
                "osm_node_id": identifier,
                "retained_in_driving_graph": retained,
                "component_id": component_by_node.get(identifier),
                "source_tags": snapshot.nodes[identifier].tags,
            }
        )
    retained_count = sum(item["retained_in_driving_graph"] for item in details)
    return {
        "source_count": len(details),
        "retained_count": retained_count,
        "excluded_count": len(details) - retained_count,
        "details": details,
        "note": "Graph membership does not prove an unambiguous lanelet association.",
    }


def _restriction_report(snapshot: OsmSnapshot, selected_way_ids: set[str]) -> dict[str, Any]:
    details = []
    for relation in sorted(
        snapshot.relations.values(), key=lambda item: (len(item.identifier), item.identifier)
    ):
        if relation.tags.get("type") != "restriction":
            continue
        way_members = [member for member in relation.members if member.member_type == "way"]
        missing_way_ids = sorted(
            {
                member.reference
                for member in way_members
                if member.reference not in selected_way_ids
            },
            key=lambda value: (len(value), value),
        )
        fully_retained = bool(way_members) and not missing_way_ids
        details.append(
            {
                "osm_relation_id": relation.identifier,
                "restriction": relation.tags.get("restriction"),
                "fully_retained_way_members": fully_retained,
                "missing_way_member_ids": missing_way_ids,
                "members": [
                    {
                        "type": member.member_type,
                        "ref": member.reference,
                        "role": member.role,
                    }
                    for member in relation.members
                ],
            }
        )
    retained_count = sum(item["fully_retained_way_members"] for item in details)
    return {
        "source_count": len(details),
        "fully_retained_way_member_count": retained_count,
        "partially_or_not_retained_count": len(details) - retained_count,
        "details": details,
        "note": "Retained way members do not replace downstream from/via/to validation.",
    }


def _is_stop_line_candidate(tags: dict[str, str]) -> bool:
    normalized = {
        str(value).lower().replace("-", "_").replace(" ", "_")
        for pair in tags.items()
        for value in pair
    }
    return "stop_line" in normalized


def _markdown_report(report: dict[str, Any]) -> str:
    lanes = report["lane_count_coverage"]
    widths = report["width_coverage"]
    signals = report["traffic_signals"]
    restrictions = report["turn_restrictions"]
    stop_lines = report["stop_line_geometry"]
    lines = [
        "# Stage 1B Data Audit",
        "",
        f"- Stage 1B status: **{report['stage_1b_status']}**",
        f"- Downstream readiness: **{report['downstream_readiness']}**",
        f"- Selected OSM ways: {report['selected_network']['unique_osm_way_count']}",
        f"- Graph nodes: {report['selected_network']['node_count']}",
        f"- Directed graph edges: {report['selected_network']['directed_edge_count']}",
        "",
        "## Lane Counts",
        "",
        f"- Ways missing lane counts: {lanes['missing_way_count']}",
        f"- Missing counts by highway: `{json.dumps(lanes['missing_by_highway'], sort_keys=True)}`",
        f"- Missing-count inference enabled: {str(lanes['inference_enabled']).lower()}",
        "- See `stage-1b-data-audit.json` for every affected OSM way ID.",
        "",
        "## Connectivity",
        "",
        f"- Weakly connected components: {report['connectivity']['component_count']}",
        "- Component node counts: "
        + ", ".join(str(item["node_count"]) for item in report["connectivity"]["components"]),
        "- Components are observations only; no missing connections are invented or discarded.",
        "",
        "## Widths",
        "",
        f"- Ways with an explicit `width`: {widths['tagged_way_count']}",
        f"- Ways missing `width`: {widths['missing_way_count']}",
        f"- Configured default lane width: {widths['configured_default_lane_width_metres']} m",
        "",
        "## Traffic Signals",
        "",
        f"- Source signal nodes: {signals['source_count']}",
        f"- Signals on retained graph nodes: {signals['retained_count']}",
        f"- Signals outside retained graph nodes: {signals['excluded_count']}",
        "- Retention does not by itself prove an unambiguous lanelet association.",
        "",
        "## Turn Restrictions",
        "",
        f"- Source restriction relations: {restrictions['source_count']}",
        "- Relations whose way members are all retained: "
        f"{restrictions['fully_retained_way_member_count']}",
        "- Partially or non-retained relations: "
        f"{restrictions['partially_or_not_retained_count']}",
        "- A later conversion stage must still validate `from`, `via`, and `to` roles.",
        "",
        "## Stop-Line Geometry",
        "",
        f"- Candidate source ways: {stop_lines['candidate_way_count']}",
        "- Detection matches normalized tag keys or values exactly equal to `stop_line`; "
        "bus stops and transit relation roles are excluded.",
        "",
        "## Readiness Risks",
        "",
        "- Missing lane counts require an explicit inference or source correction "
        "before lanes are generated.",
        "- Disconnected components prevent routes from crossing between them.",
        "- This report audits source evidence; it does not generate lane geometry.",
        "",
    ]
    return "\n".join(lines)


def _write_text_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(content)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def generate_stage1b_data_audit(
    *,
    workspace: Path,
    graph: nx.MultiDiGraph,
    acquisition_report: dict[str, Any],
    config: ConverterConfig,
) -> tuple[Path, Path]:
    """Write Stage 1B source-evidence facts and readiness findings."""
    workspace = workspace.resolve()
    source_path = workspace / "source" / "map.osm"
    if not source_path.is_file():
        raise Stage1BDataAuditError(f"preserved OSM source not found: {source_path}")

    snapshot = read_osm_snapshot(source_path)
    selected_way_ids = _selected_way_ids(graph)
    missing_lane_ids = _warning_way_ids(acquisition_report, "missing_lane_count")
    missing_lane_details = _way_details(snapshot, missing_lane_ids)
    missing_lane_highways = Counter(
        item.get("highway") or "<missing>"
        for item in missing_lane_details
        if item["source_way_found"]
    )
    width_tagged_ids = {
        way_id
        for way_id in selected_way_ids
        if way_id in snapshot.ways and snapshot.ways[way_id].tags.get("width")
    }
    width_missing_ids = selected_way_ids - width_tagged_ids

    components = _component_report(graph, snapshot)
    component_by_node = {
        str(node_id): component["component_id"]
        for component, nodes in zip(
            components,
            sorted(
                nx.weakly_connected_components(graph),
                key=lambda values: (-len(values), min(str(node) for node in values)),
            ),
            strict=True,
        )
        for node_id in nodes
    }
    stop_line_ids = sorted(
        (
            way.identifier
            for way in snapshot.ways.values()
            if _is_stop_line_candidate(way.tags)
        ),
        key=lambda value: (len(value), value),
    )

    blocking_errors = len(acquisition_report["preflight"]["errors"])
    inference_blocked = bool(missing_lane_ids) and not config.tag_inference.infer_missing_lane_count
    review_required = any(
        (
            missing_lane_ids,
            width_missing_ids,
            len(components) > 1,
        )
    )
    downstream_readiness = (
        "blocked"
        if blocking_errors or inference_blocked
        else "review_required"
        if review_required
        else "ready"
    )
    report = {
        "report_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "stage_1b_status": acquisition_report["status"],
        "downstream_readiness": downstream_readiness,
        "inputs": {
            "source_osm": "source/map.osm",
            "normalized_graph": "normalized/road-network-local.graphml",
            "acquisition_report": "reports/acquisition.json",
        },
        "selected_network": {
            "unique_osm_way_count": len(selected_way_ids),
            "node_count": len(graph.nodes),
            "directed_edge_count": len(graph.edges),
        },
        "lane_count_coverage": {
            "missing_way_count": len(missing_lane_ids),
            "missing_by_highway": dict(sorted(missing_lane_highways.items())),
            "inference_enabled": config.tag_inference.infer_missing_lane_count,
            "details": missing_lane_details,
        },
        "connectivity": {
            "component_count": len(components),
            "non_primary_component_count": max(0, len(components) - 1),
            "components": components,
        },
        "width_coverage": {
            "tagged_way_count": len(width_tagged_ids),
            "missing_way_count": len(width_missing_ids),
            "tagged_way_ids": sorted(width_tagged_ids, key=lambda value: (len(value), value)),
            "missing_way_ids": sorted(width_missing_ids, key=lambda value: (len(value), value)),
            "configured_default_lane_width_metres": config.lane_width_defaults.vehicle,
        },
        "traffic_signals": _traffic_signal_report(graph, snapshot, component_by_node),
        "turn_restrictions": _restriction_report(snapshot, selected_way_ids),
        "stop_line_geometry": {
            "candidate_way_count": len(stop_line_ids),
            "candidate_way_ids": stop_line_ids,
            "detection_rule": (
                "normalized source way tag key or value exactly equals stop_line"
            ),
        },
        "stage_1_preflight": {
            "blocking_error_count": blocking_errors,
            "warning_counts": dict(
                sorted(
                    Counter(
                        item["code"] for item in acquisition_report["preflight"]["warnings"]
                    ).items()
                )
            ),
        },
    }

    reports_dir = workspace / "reports"
    json_path = reports_dir / "stage-1b-data-audit.json"
    markdown_path = reports_dir / "stage-1b-data-audit.md"
    _write_text_atomic(json_path, json.dumps(report, indent=2, sort_keys=True) + "\n")
    _write_text_atomic(markdown_path, _markdown_report(report))
    return json_path, markdown_path
