"""Stage 5 - validate the reviewed map.

Stage 4 produces a lane model; this decides whether it is fit to convert. It reads and
never writes `lane-model/reviewed.json`, so a failed validation costs nothing but a
report.

Two rules here are not obvious from the spec, and both were measured against
`junction-1` before being written down.

* **A centreline lies on its own polygon boundary by construction.** The polygon is
  `centerline.buffer(width / 2)` (`generation._lane_surface`), so `Polygon.contains` is
  false for every lane in the map. Even `covers` fails on 11 of 285 by floating-point
  noise - the worst point-to-polygon distance measured was 2.8e-15 m and the worst
  excursion length was exactly zero. `_POLYGON_EPS_M` is the slack that makes the check
  mean what a reader thinks it means.

* **A lane that stops dead is usually the edge of the extract, not a defect.** Every one
  of `junction-1`'s 20 no-exit and 19 no-entry lanes ends at a node that terminates every
  source way containing it: the road does not continue there, because the extract was cut
  there. A lane stopping at a node the road runs *through* is the real defect, and there
  were none. Reporting the first as an error would bury the second under 39 false alarms.
"""

from __future__ import annotations

import json
import math
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import networkx as nx
from shapely.geometry import LineString, Polygon

# Private helpers imported across modules on purpose, for the reason given in
# `apply_review`: these are the exact routines the earlier stages use, and a Stage 5 copy
# would be a second implementation to keep in step. `_sha256` in particular must match
# what Stage 4 wrote into the manifest or every run reports a stale checksum.
from osm_scenario.apply_review import _read_json, _sha256
from osm_scenario.config import ConverterConfig
from osm_scenario.lane_model import PreliminaryLaneModel
from osm_scenario.stage1b_data_audit import _write_text_atomic
from osm_scenario.validation_view import render_validation_html

REPORT_VERSION = 1

# How far outside its polygon a centreline may stray before it counts as outside. The
# polygon is a buffer of the centreline, so the true distance is float noise around zero;
# a nanometre is six orders of magnitude above the worst observed and far below anything
# a person would call a gap.
_POLYGON_EPS_M = 1e-9

# How near a connector's ends must come to the lanes it joins. Coupled to the 0.05 m gap
# threshold in `topology.connector_curve`: below that gap the curve degenerates to a
# three-point stub whose far end stays on the *incoming* lane, leaving it up to 0.05 m
# from the outgoing lane's start. 32 of `junction-1`'s 83 active connectors are stubs, so
# an exact-endpoint assertion would fail every merge in the map. Keep the two in step.
_CONNECTOR_MEET_M = 0.05


class ValidationError(RuntimeError):
    """Raised when the reviewed map cannot be validated."""


def _issue(code: str, osm_id: str | None, reason: str, **extra: Any) -> dict[str, Any]:
    """One problem, in the shape Stage 1 uses.

    Severity is which list this lands in, never a field on it - the same convention as
    `normalization._preflight`, so a reader who knows one report can read the other.
    """
    return {"code": code, "osm_id": osm_id, "reason": reason, **extra}


def _line(points: Any) -> LineString:
    return LineString((point.x, point.y) for point in points)


def _polygon(points: Any) -> Polygon:
    return Polygon((point.x, point.y) for point in points)


def _finite(points: Any) -> bool:
    return all(math.isfinite(point.x) and math.isfinite(point.y) for point in points)


# --- gates -----------------------------------------------------------------------------


def _check_stage_4(workspace: Path, model_path: Path) -> dict[str, Any]:
    """Gate 1 - the model on disk is the one Stage 4 signed, unchanged since.

    Checked before any geometry, because a stale model makes every later answer describe a
    map nobody reviewed.
    """
    manifest = _read_json(workspace / "source" / "manifest.json", "Stage 1 manifest")
    stage_4 = manifest.get("stage_4")
    if not isinstance(stage_4, dict) or stage_4.get("status") != "passed":
        raise ValidationError("Stage 4 has not passed; run apply-review first")
    if not model_path.is_file():
        raise ValidationError(f"reviewed lane model not found: {model_path}")

    recorded = stage_4.get("artifacts", {}).get("reviewed_lane_model", {}).get("sha256")
    actual = _sha256(model_path)
    if recorded != actual:
        raise ValidationError(
            "reviewed lane model checksum does not match the Stage 4 manifest: the model "
            "changed after it was reviewed"
        )
    return manifest


def _dispositioned_osm_ids(
    model: PreliminaryLaneModel, comparison: dict[str, Any]
) -> dict[str, str]:
    """OSM features a reviewer judged not applicable, and the finding that says so.

    Stage 5 re-derives conditions from the model, so it will happily re-detect something a
    human has already looked at and ruled out - `junction-1`'s traffic signal at the
    upstream edge of the extract is exactly that. Re-deriving it as an error would make the
    review pointless: the reviewer would have to answer the same question in a second
    place, and answering it there would still not silence this one.

    A judgement is not a silence. The issue is still reported, still carries its feature
    ids, and still names the finding that dispositioned it - it just stops failing the map.
    """
    decisions = comparison.get("finding_decisions") or {}
    dispositioned: dict[str, str] = {}
    for finding in model.findings:
        entry = decisions.get(finding.identifier) or {}
        if entry.get("status") != "not_applicable":
            continue
        for source_id in finding.source_ids:
            dispositioned.setdefault(source_id, finding.identifier)
    return dispositioned


def _read_comparison(workspace: Path) -> dict[str, Any]:
    """Gate 2 - Stage 4's report is present and new enough to say what was decided.

    `findings_still_open` is read rather than re-derived from severities. A blocker
    surviving into the reviewed model is not evidence of unreviewed work: accepting an
    inference leaves the map unchanged, so the same question comes back on every
    regeneration. Stage 4 already did the join that tells the two apart.
    """
    comparison = _read_json(
        workspace / "reports" / "reviewed-comparison.json", "Stage 4 comparison report"
    )
    if "findings_still_open" not in comparison:
        raise ValidationError(
            "the Stage 4 comparison predates `findings_still_open`; re-run apply-review "
            "so validation can tell an answered blocker from an open one"
        )
    return comparison


# --- checks ----------------------------------------------------------------------------


def _geometry_issues(model: PreliminaryLaneModel) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for lane in model.lanes:
        way = lane.source_way_ids[0] if lane.source_way_ids else None
        if not _finite(lane.centerline) or not _finite(lane.polygon):
            issues.append(
                _issue(
                    "non_finite_geometry",
                    way,
                    "lane geometry has a non-finite coordinate",
                    lane_id=lane.identifier,
                )
            )
            continue
        if len(lane.centerline) < 2:
            issues.append(
                _issue(
                    "empty_lane_geometry",
                    way,
                    f"lane centreline has {len(lane.centerline)} point(s)",
                    lane_id=lane.identifier,
                )
            )
            continue
        centerline = _line(lane.centerline)
        if centerline.is_empty or centerline.length <= 0:
            issues.append(
                _issue(
                    "empty_lane_geometry",
                    way,
                    "lane centreline has no length",
                    lane_id=lane.identifier,
                )
            )
            continue
        if not centerline.is_simple:
            issues.append(
                _issue(
                    "self_intersecting_centerline",
                    way,
                    "lane centreline crosses itself",
                    lane_id=lane.identifier,
                )
            )
        polygon = _polygon(lane.polygon) if len(lane.polygon) >= 4 else None
        if polygon is None or not polygon.is_valid:
            issues.append(
                _issue(
                    "invalid_lane_polygon",
                    way,
                    "lane polygon is not a valid ring",
                    lane_id=lane.identifier,
                )
            )
            continue
        # See `_POLYGON_EPS_M`: the polygon is a buffer of this centreline, so the
        # centreline sits on its boundary and only a tolerance makes the test meaningful.
        if not polygon.buffer(_POLYGON_EPS_M).covers(centerline):
            outside = round(centerline.difference(polygon).length, 6)
            issues.append(
                _issue(
                    "centerline_outside_polygon",
                    way,
                    f"{outside} m of the centreline lies outside its lane polygon",
                    lane_id=lane.identifier,
                )
            )
    return issues


def _reference_issues(model: PreliminaryLaneModel) -> list[dict[str, Any]]:
    """Dangling and non-reciprocal links.

    `entry_lanes` and `exit_lanes` hold two kinds of id - a lane id for a continuation, a
    connector id for a junction movement - and nothing in the id says which. Membership of
    the two id sets is the only way to tell, and a check that assumes one kind passes
    silently over the other.
    """
    lanes = {lane.identifier: lane for lane in model.lanes}
    connectors = {item.identifier: item for item in model.connectors}
    issues: list[dict[str, Any]] = []

    for lane in model.lanes:
        way = lane.source_way_ids[0] if lane.source_way_ids else None
        for field, other_field, own_end, far_end in (
            ("exit_lanes", "entry_lanes", "from_lane_id", "to_lane_id"),
            ("entry_lanes", "exit_lanes", "to_lane_id", "from_lane_id"),
        ):
            for reference in getattr(lane, field):
                if reference in lanes:
                    if lane.identifier not in getattr(lanes[reference], other_field):
                        issues.append(
                            _issue(
                                "non_reciprocal_link",
                                way,
                                f"lane names {reference} in {field}, but that lane does "
                                f"not name it back in {other_field}",
                                lane_id=lane.identifier,
                            )
                        )
                    continue
                connector = connectors.get(reference)
                if connector is None:
                    issues.append(
                        _issue(
                            "dangling_reference",
                            way,
                            f"{field} names {reference}, which is neither a lane nor a "
                            "connector in this model",
                            lane_id=lane.identifier,
                        )
                    )
                    continue
                if getattr(connector, own_end) != lane.identifier:
                    issues.append(
                        _issue(
                            "non_reciprocal_link",
                            way,
                            f"lane names connector {reference} in {field}, but the "
                            f"connector's {own_end} is {getattr(connector, own_end)}",
                            lane_id=lane.identifier,
                            connector_id=reference,
                        )
                    )
                far_lane = lanes.get(getattr(connector, far_end))
                if far_lane is not None and reference not in getattr(far_lane, other_field):
                    issues.append(
                        _issue(
                            "non_reciprocal_link",
                            way,
                            f"connector {reference} is listed here but not in "
                            f"{far_lane.identifier}'s {other_field}",
                            lane_id=lane.identifier,
                            connector_id=reference,
                        )
                    )

        for side, opposite in (
            ("left_neighbor", "right_neighbor"),
            ("right_neighbor", "left_neighbor"),
        ):
            neighbour_id = getattr(lane, side)
            if neighbour_id is None:
                continue
            neighbour = lanes.get(neighbour_id)
            if neighbour is None:
                issues.append(
                    _issue(
                        "dangling_reference",
                        way,
                        f"{side} names {neighbour_id}, which is not a lane in this model",
                        lane_id=lane.identifier,
                    )
                )
                continue
            if getattr(neighbour, opposite) != lane.identifier:
                issues.append(
                    _issue(
                        "non_reciprocal_neighbor",
                        way,
                        f"{side} is {neighbour_id}, but its {opposite} is "
                        f"{getattr(neighbour, opposite)}",
                        lane_id=lane.identifier,
                    )
                )
            elif neighbour.source_edge != lane.source_edge:
                issues.append(
                    _issue(
                        "non_reciprocal_neighbor",
                        way,
                        f"{side} {neighbour_id} belongs to a different graph edge; "
                        "neighbours are lanes of one carriageway in one direction",
                        lane_id=lane.identifier,
                    )
                )
    return issues


def _connector_issues(model: PreliminaryLaneModel) -> list[dict[str, Any]]:
    """Endpoints meet their lanes, and only live movements are wired into the lane graph."""
    lanes = {lane.identifier: lane for lane in model.lanes}
    listed: set[str] = set()
    for lane in model.lanes:
        listed.update(lane.entry_lanes)
        listed.update(lane.exit_lanes)
    issues: list[dict[str, Any]] = []

    for connector in model.connectors:
        node = connector.junction_node_id
        if connector.status != "active":
            if connector.identifier in listed:
                issues.append(
                    _issue(
                        "inactive_connector_is_drivable",
                        node,
                        f"connector is {connector.status} but is still listed by a lane, "
                        "so the movement is still drivable",
                        connector_id=connector.identifier,
                    )
                )
            continue
        if connector.identifier not in listed:
            issues.append(
                _issue(
                    "active_connector_is_unreachable",
                    node,
                    "connector is active but no lane lists it, so nothing can use the "
                    "movement",
                    connector_id=connector.identifier,
                )
            )
        from_lane = lanes.get(connector.from_lane_id)
        to_lane = lanes.get(connector.to_lane_id)
        if from_lane is None or to_lane is None:
            issues.append(
                _issue(
                    "dangling_reference",
                    node,
                    "connector names a lane that is not in this model",
                    connector_id=connector.identifier,
                )
            )
            continue
        if not (_finite(connector.centerline) and len(connector.centerline) >= 2):
            issues.append(
                _issue(
                    "invalid_connector_geometry",
                    node,
                    "connector centreline is empty or has a non-finite coordinate",
                    connector_id=connector.identifier,
                )
            )
            continue
        ends = (connector.centerline[0], connector.centerline[-1])
        for label, target in (
            ("its incoming lane", from_lane.centerline[-1]),
            ("its outgoing lane", to_lane.centerline[0]),
        ):
            gap = min(math.dist((end.x, end.y), (target.x, target.y)) for end in ends)
            if gap > _CONNECTOR_MEET_M:
                issues.append(
                    _issue(
                        "connector_endpoint_gap",
                        node,
                        f"connector comes no closer than {gap:.3f} m to {label}",
                        connector_id=connector.identifier,
                    )
                )
    return issues


def _restriction_issues(model: PreliminaryLaneModel) -> list[dict[str, Any]]:
    """A movement a reviewed restriction forbids must be dead, not merely labelled.

    Deliberately narrow. Turn permissions are *not* checked against movement classes here:
    reverse movements bypass permissions, `_stranded_permission_fallback` restores a
    rejected movement on purpose and files a finding for a human, and continuations never
    consult permissions at all. A raw tag-versus-geometry rule would re-raise judgements
    the review has already made.
    """
    connectors = {item.identifier: item for item in model.connectors}
    listed: set[str] = set()
    for lane in model.lanes:
        listed.update(lane.entry_lanes)
        listed.update(lane.exit_lanes)
    issues: list[dict[str, Any]] = []

    for restriction in model.restrictions:
        for connector_id in restriction.forbidden_connector_ids:
            connector = connectors.get(connector_id)
            if connector is None:
                issues.append(
                    _issue(
                        "dangling_reference",
                        restriction.source_relation_id,
                        f"restriction forbids {connector_id}, which is not a connector in "
                        "this model",
                        restriction_id=restriction.identifier,
                    )
                )
                continue
            if connector.status != "forbidden":
                issues.append(
                    _issue(
                        "forbidden_movement_live",
                        restriction.source_relation_id,
                        f"restriction forbids this movement but the connector is "
                        f"{connector.status}",
                        restriction_id=restriction.identifier,
                        connector_id=connector_id,
                    )
                )
            if connector_id in listed:
                issues.append(
                    _issue(
                        "forbidden_movement_live",
                        restriction.source_relation_id,
                        "restriction forbids this movement but a lane still lists it, so "
                        "it is still drivable",
                        restriction_id=restriction.identifier,
                        connector_id=connector_id,
                    )
                )
    return issues


def _signal_issues(model: PreliminaryLaneModel) -> list[dict[str, Any]]:
    lanes = {lane.identifier for lane in model.lanes}
    issues: list[dict[str, Any]] = []
    for signal in model.signals:
        if signal.status != "mapped" or not signal.lane_ids:
            issues.append(
                _issue(
                    "unassociated_signal",
                    signal.source_node_id,
                    "traffic signal governs no generated lane",
                    signal_id=signal.identifier,
                )
            )
            continue
        missing = [item for item in signal.lane_ids if item not in lanes]
        if missing:
            issues.append(
                _issue(
                    "dangling_reference",
                    signal.source_node_id,
                    f"signal names {len(missing)} lane(s) that are not in this model",
                    signal_id=signal.identifier,
                )
            )
    for stop_line in model.stop_lines:
        if not stop_line.lane_ids or any(item not in lanes for item in stop_line.lane_ids):
            issues.append(
                _issue(
                    "invalid_stop_line",
                    stop_line.source_node_id,
                    "stop line names no lane, or a lane that is not in this model",
                    stop_line_id=stop_line.identifier,
                )
            )
    return issues


# --- the extract boundary --------------------------------------------------------------


def _way_terminus_nodes(source_osm: Path) -> set[str]:
    """OSM nodes where no source way continues through.

    A node is a terminus when it is the first or last node of *every* way that contains
    it. A lane stopping at one of those has run off the edge of the extract; a lane
    stopping anywhere else has lost a link the source road still has.
    """
    if not source_osm.is_file():
        raise ValidationError(f"source OSM not found: {source_osm}")
    try:
        root = ET.parse(source_osm).getroot()
    except ET.ParseError as error:
        raise ValidationError(f"source OSM is not valid XML: {error}") from error

    positions: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for way in root.findall("way"):
        refs = [node.get("ref") for node in way.findall("nd")]
        for index, ref in enumerate(refs):
            if ref is not None:
                positions[ref].append((index, len(refs)))
    return {
        node
        for node, seen in positions.items()
        if all(index == 0 or index == length - 1 for index, length in seen)
    }


def _boundary_report(
    model: PreliminaryLaneModel, terminus_nodes: set[str]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Dead ends that the extract explains, and dead ends that it does not."""
    lanes = {lane.identifier: lane for lane in model.lanes}
    connectors = {item.identifier: item for item in model.connectors}
    issues: list[dict[str, Any]] = []

    def node_of(lane: Any, *, end: bool) -> str | None:
        edge = lane.source_edge
        if len(edge) < 2:
            return None
        return edge[1] if end else edge[0]

    without_exit = [lane for lane in model.lanes if not lane.exit_lanes]
    without_entry = [lane for lane in model.lanes if not lane.entry_lanes]
    boundary_lanes: set[str] = set()
    for lanes_list, end, direction in (
        (without_exit, True, "leaves"),
        (without_entry, False, "enters"),
    ):
        for lane in lanes_list:
            node = node_of(lane, end=end)
            if node is not None and node in terminus_nodes:
                boundary_lanes.add(lane.identifier)
                continue
            issues.append(
                _issue(
                    "interior_dead_end",
                    lane.source_way_ids[0] if lane.source_way_ids else None,
                    f"nothing {direction} this lane at node {node}, and the source road "
                    "runs through that node rather than ending there",
                    lane_id=lane.identifier,
                )
            )

    graph = nx.DiGraph()
    graph.add_nodes_from(lanes)
    for lane in model.lanes:
        for reference in lane.exit_lanes:
            if reference in lanes:
                graph.add_edge(lane.identifier, reference)
            elif reference in connectors:
                connector = connectors[reference]
                if connector.from_lane_id in lanes and connector.to_lane_id in lanes:
                    graph.add_edge(connector.from_lane_id, connector.to_lane_id)

    # A component whose lanes are already named one by one does not also get named as a
    # whole: two codes for one defect sends a reader looking for a second problem.
    already_named = {item["lane_id"] for item in issues if "lane_id" in item}
    components = [set(item) for item in nx.weakly_connected_components(graph)]
    for component in components:
        if component & boundary_lanes or component & already_named:
            continue
        # An island with no dead end the extract explains is unreachable from the rest of
        # the map and goes nowhere: a routing component that should not exist.
        sample = sorted(component)[0]
        issues.append(
            _issue(
                "isolated_component",
                lanes[sample].source_way_ids[0] if lanes[sample].source_way_ids else None,
                f"{len(component)} lane(s) form a component that touches neither the rest "
                "of the network nor the edge of the extract",
                lane_id=sample,
            )
        )

    facts = {
        "lanes_without_exit": len(without_exit),
        "lanes_without_entry": len(without_entry),
        "lanes_at_the_extract_edge": len(boundary_lanes),
        # Listed, not just counted. This is the one verdict Stage 5 reaches by judgement
        # rather than measurement, so the ids have to be checkable against the map by
        # someone who does not trust the rule.
        "lane_ids_at_the_extract_edge": sorted(boundary_lanes),
        "routing_components": sorted((len(item) for item in components), reverse=True),
    }
    return issues, facts


# --- report ------------------------------------------------------------------------------


def _markdown_report(report: dict[str, Any]) -> str:
    boundary = report["boundary"]
    checked = report["checked"]
    errors = report["errors"]
    warnings = report["warnings"]

    def rows(items: list[dict[str, Any]], source: str) -> list[str]:
        if not items:
            return ["- None"]
        lines = [
            f"- `{item['code']}` OSM `{item['osm_id']}`: {item['reason']}"
            for item in items[:20]
        ]
        if len(items) > 20:
            lines.append(f"- {len(items) - 20} more; see `{source}` for the full list.")
        return lines

    lines = [
        "# Stage 5 Map Validation",
        "",
        f"- Status: **{report['status']}**",
        f"- Validated lane model: `{report['validated_lane_model']['path']}`",
        f"- Model checksum: `{report['validated_lane_model']['sha256']}`",
        f"- Generation fingerprint: `{report['validated_lane_model']['generation_fingerprint']}`",
        f"- Errors: {len(errors)}",
        f"- Warnings: {len(warnings)}",
        f"- Checked: {checked['lanes']} lanes, {checked['connectors']} connectors, "
        f"{checked['restrictions']} restrictions, {checked['signals']} signals, "
        f"{checked['stop_lines']} stop lines, across {len(checked['checks'])} checks "
        f"(`{'`, `'.join(checked['checks'])}`)",
        "",
        "## Errors",
        "",
        *rows(errors, "map-validation.json"),
        "",
        "- An error means the map is not fit to convert; Stage 6 stays blocked until it is gone.",
        "",
        "## Warnings",
        "",
        *rows(warnings, "map-validation.json"),
        "",
        "- A warning is a condition a reviewer judged not applicable. It is still true and "
        "still listed; it does not fail the map because someone said why it need not.",
        "",
        "## Network Edges",
        "",
        f"- Lanes nothing leaves: {boundary['lanes_without_exit']}",
        f"- Lanes nothing enters: {boundary['lanes_without_entry']}",
        f"- Of those, lanes at the edge of the extract: {boundary['lanes_at_the_extract_edge']}",
        f"- Routing components: {len(boundary['routing_components'])} "
        f"(sizes `{json.dumps(boundary['routing_components'])}`)",
        "",
        "- A lane that stops where the source road also stops is the edge of the extract, "
        "not a missing link; only a lane stopping mid-road is reported as an error above.",
        "",
    ]
    return "\n".join(lines)


def validate_map(*, workspace: Path, config: ConverterConfig) -> tuple[Path, Path, Path]:
    """Validate WORKSPACE's reviewed lane model and write the Stage 5 reports."""
    workspace = workspace.resolve()
    model_path = workspace / "lane-model" / "reviewed.json"
    manifest = _check_stage_4(workspace, model_path)
    comparison = _read_comparison(workspace)
    still_open = list(comparison["findings_still_open"])

    model = PreliminaryLaneModel.model_validate(_read_json(model_path, "reviewed lane model"))
    terminus_nodes = _way_terminus_nodes(workspace / manifest["source"]["path"])
    boundary_issues, boundary_facts = _boundary_report(model, terminus_nodes)

    # Named so the report can say which checks ran. "0 errors" is not a claim until it
    # comes with a denominator: a validator that silently skipped its lane loop would
    # otherwise write a report byte-identical to one that cleared all 285 lanes.
    checks = {
        "geometry": _geometry_issues,
        "references": _reference_issues,
        "connectors": _connector_issues,
        "restrictions": _restriction_issues,
        "signals": _signal_issues,
    }
    found: list[dict[str, Any]] = []
    for check in checks.values():
        found.extend(check(model))
    found.extend(boundary_issues)
    checked = {
        "lanes": len(model.lanes),
        "connectors": len(model.connectors),
        "restrictions": len(model.restrictions),
        "signals": len(model.signals),
        "stop_lines": len(model.stop_lines),
        # Built from the same mapping the loop above iterates, plus the boundary pass that
        # runs separately because it needs the source OSM. The two cannot drift.
        "checks": [*checks, "boundary"],
    }
    if still_open:
        found.append(
            _issue(
                "open_blocking_finding",
                None,
                f"{len(still_open)} blocking finding(s) are still open in the review; "
                "answer them in Stage 3 and re-run apply-review",
                finding_ids=sorted(still_open),
            )
        )

    dispositioned = _dispositioned_osm_ids(model, comparison)
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    for item in found:
        finding_id = dispositioned.get(item["osm_id"] or "")
        if finding_id is None:
            errors.append(item)
        else:
            warnings.append({**item, "dispositioned_by": finding_id})
    for bucket in (errors, warnings):
        bucket.sort(key=lambda item: (item["code"], item["osm_id"] or "", item["reason"]))

    report = {
        "report_version": REPORT_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "failed" if errors else "passed",
        "inputs": {
            "reviewed_lane_model": "lane-model/reviewed.json",
            "source_osm": manifest["source"]["path"],
            "comparison_report": "reports/reviewed-comparison.json",
        },
        "validated_lane_model": {
            "path": "lane-model/reviewed.json",
            "sha256": _sha256(model_path),
            "generation_fingerprint": model.metadata.generation_fingerprint,
        },
        "checked": checked,
        "errors": errors,
        "warnings": warnings,
        "boundary": boundary_facts,
    }

    reports_dir = workspace / "reports"
    inspection_dir = workspace / "inspection"
    json_path = reports_dir / "map-validation.json"
    markdown_path = reports_dir / "map-validation.md"
    html_path = inspection_dir / "stage-5-validation.html"
    _write_text_atomic(json_path, json.dumps(report, indent=2, sort_keys=True) + "\n")
    _write_text_atomic(markdown_path, _markdown_report(report))
    _write_text_atomic(
        html_path,
        render_validation_html(
            model=model,
            report=report,
            boundary_lane_ids=set(boundary_facts["lane_ids_at_the_extract_edge"]),
        ),
    )

    artifacts = {}
    for name, path in (
        ("validation_json", json_path),
        ("validation_markdown", markdown_path),
        ("validation_html", html_path),
    ):
        artifacts[name] = {
            "path": path.relative_to(workspace).as_posix(),
            "sha256": _sha256(path),
            "size_bytes": path.stat().st_size,
        }
    manifest["stage_5"] = {
        "status": report["status"],
        "checked": checked,
        "validated_lane_model": report["validated_lane_model"],
        "issue_counts": {"errors": len(errors), "warnings": len(warnings)},
        "boundary": boundary_facts,
        "artifacts": artifacts,
    }
    (workspace / "source" / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return json_path, markdown_path, html_path
