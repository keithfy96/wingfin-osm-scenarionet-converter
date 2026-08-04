"""Stage 4 structural, geometric, and routing validation for Lanelet2 maps."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import networkx as nx
import yaml
from lanelet2 import io, projection, routing, traffic_rules
from shapely.geometry import LineString, Polygon


class LaneletValidationError(RuntimeError):
    """Raised when Stage 4 cannot run or finds blocking results."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _coords(line: Any) -> list[tuple[float, float]]:
    return [(point.x, point.y) for point in line]


def _finding(code: str, severity: str, message: str, **identifiers: Any) -> dict[str, Any]:
    return {"code": code, "severity": severity, "message": message, **identifiers}


def _load_waivers(path: Path) -> tuple[list[dict[str, Any]], str | None]:
    if not path.is_file():
        return [], "reports/stage-3b-review.yaml is missing"
    content = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    waivers = content.get("validation_waivers", [])
    if not isinstance(waivers, list):
        raise LaneletValidationError("validation_waivers must be a list")
    valid = [
        waiver
        for waiver in waivers
        if isinstance(waiver, dict)
        and waiver.get("code")
        and waiver.get("feature_id") is not None
        and waiver.get("operator")
        and waiver.get("reason")
    ]
    return valid, None


def _is_waived(finding: dict[str, Any], waivers: list[dict[str, Any]]) -> bool:
    feature_id = str(finding.get("lanelet_id", finding.get("feature_id", "")))
    return any(
        waiver["code"] == finding["code"] and str(waiver["feature_id"]) == feature_id
        for waiver in waivers
    )


def _markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Stage 4 Lanelet2 Validation",
        "",
        f"- Status: **{report['status']}**",
        f"- Map: `{report['input']['path']}`",
        f"- SHA-256: `{report['input']['sha256']}`",
        f"- Parser errors: {report['counts']['parser_errors']}",
        f"- Blocking errors: {report['counts']['errors']}",
        f"- Unwaived warnings: {report['counts']['unwaived_warnings']}",
        f"- Drivable components: {report['routing']['component_count']}",
        f"- Components with a route: {report['routing']['components_with_route']}",
        "",
        "## Native validator",
        "",
        f"- Status: `{report['native_validator']['status']}`",
        f"- Command: `{report['native_validator']['command']}`",
        "",
        "## Findings",
        "",
    ]
    if not report["findings"]:
        lines.append("No findings.")
    for finding in report["findings"]:
        identifier = finding.get("lanelet_id", finding.get("feature_id", "map"))
        waiver = " (waived)" if finding.get("waived") else ""
        lines.append(
            f"- **{finding['severity']}** `{finding['code']}` `{identifier}`{waiver}: "
            f"{finding['message']}"
        )
    return "\n".join(lines) + "\n"


def validate_lanelet2_workspace(workspace: Path) -> Path:
    """Validate the reviewed edited map and write checksum-bound Stage 4 reports."""
    edited = workspace / "lanelet2" / "edited.osm"
    if not edited.is_file():
        raise LaneletValidationError(f"reviewed map not found: {edited}")

    manifest_path = workspace / "source" / "manifest.json"
    if not manifest_path.is_file():
        raise LaneletValidationError(f"source manifest not found: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    try:
        origin = manifest["stage_1b"]["projection"]["origin"]
        projector = projection.LocalCartesianProjector(
            io.Origin(origin["latitude"], origin["longitude"])
        )
    except (KeyError, TypeError, ValueError) as error:
        raise LaneletValidationError("recorded Stage 1B projection origin is invalid") from error

    lanelet_map, parser_errors = io.loadRobust(str(edited), projector)
    findings = [
        _finding("parser_error", "error", str(error), feature_id="map") for error in parser_errors
    ]

    lanelets = list(lanelet_map.laneletLayer)
    for lanelet in lanelets:
        left = _coords(lanelet.leftBound)
        right = _coords(lanelet.rightBound)
        if len(left) < 2 or len(right) < 2:
            findings.append(
                _finding(
                    "boundary_too_short",
                    "error",
                    "Both boundaries need at least two points.",
                    lanelet_id=str(lanelet.id),
                )
            )
            continue
        polygon = Polygon(left + list(reversed(right)))
        if not polygon.is_valid or polygon.area <= 0:
            findings.append(
                _finding(
                    "invalid_lanelet_polygon",
                    "error",
                    "Lanelet polygon is empty or self-intersecting.",
                    lanelet_id=str(lanelet.id),
                )
            )
        sample_count = max(2, min(10, max(len(left), len(right))))
        left_line, right_line = LineString(left), LineString(right)
        widths = []
        for index in range(sample_count):
            fraction = index / (sample_count - 1)
            a = left_line.interpolate(fraction, normalized=True)
            b = right_line.interpolate(fraction, normalized=True)
            widths.append(a.distance(b))
        minimum, maximum = min(widths), max(widths)
        if minimum < 1.5 or maximum > 8.0:
            findings.append(
                _finding(
                    "lane_width_out_of_bounds",
                    "warning",
                    f"Sampled width range is {minimum:.2f} m to {maximum:.2f} m.",
                    lanelet_id=str(lanelet.id),
                )
            )
        left_vector = (left[-1][0] - left[0][0], left[-1][1] - left[0][1])
        right_vector = (right[-1][0] - right[0][0], right[-1][1] - right[0][1])
        if left_vector[0] * right_vector[0] + left_vector[1] * right_vector[1] <= 0:
            findings.append(
                _finding(
                    "boundary_orientation",
                    "error",
                    "Left and right boundaries run in opposite directions.",
                    lanelet_id=str(lanelet.id),
                )
            )

    rules = traffic_rules.create(
        traffic_rules.Locations.Germany, traffic_rules.Participants.Vehicle
    )
    routing_graph = routing.RoutingGraph(lanelet_map, rules)
    graph = nx.DiGraph()
    relation_counts: Counter[str] = Counter()
    for lanelet in lanelets:
        lanelet_id = str(lanelet.id)
        graph.add_node(lanelet_id)
        relations = {
            "successor": routing_graph.following(lanelet),
            "predecessor": routing_graph.previous(lanelet),
            "left": routing_graph.lefts(lanelet),
            "right": routing_graph.rights(lanelet),
        }
        for relation, targets in relations.items():
            relation_counts[relation] += len(targets)
            for target in targets:
                graph.add_edge(lanelet_id, str(target.id), relation=relation)

    components = list(nx.weakly_connected_components(graph))
    components_with_route = sum(
        1 for component in components if graph.subgraph(component).number_of_edges() > 0
    )
    if components_with_route != len(components):
        findings.append(
            _finding(
                "component_without_route",
                "error",
                f"{len(components) - components_with_route} drivable component(s) have no route.",
                feature_id="routing_graph",
            )
        )

    waivers, waiver_problem = _load_waivers(workspace / "reports" / "stage-3b-review.yaml")
    if waiver_problem:
        findings.append(
            _finding("review_record_missing", "warning", waiver_problem, feature_id="map")
        )
    for finding in findings:
        finding["waived"] = finding["severity"] == "warning" and _is_waived(finding, waivers)

    native_command = (
        "lanelet2_validate --map "
        + str(edited)
        + " --origin-lat "
        + str(origin["latitude"])
        + " --origin-lon "
        + str(origin["longitude"])
    )
    findings.append(
        _finding(
            "native_validator_not_run",
            "error",
            "The required pinned Lanelet2 1.2.2 native validator was not available.",
            feature_id="map",
        )
    )
    findings[-1]["waived"] = False
    errors = sum(item["severity"] == "error" for item in findings)
    warnings = sum(item["severity"] == "warning" for item in findings)
    unwaived_warnings = sum(
        item["severity"] == "warning" and not item["waived"] for item in findings
    )
    report = {
        "report_version": 1,
        "stage": 4,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "passed" if errors == 0 and unwaived_warnings == 0 else "failed",
        "input": {"checkpoint": "edited", "path": "lanelet2/edited.osm", "sha256": _sha256(edited)},
        "projection_origin": origin,
        "counts": {
            "lanelets": len(lanelets),
            "line_strings": len(lanelet_map.lineStringLayer),
            "regulatory_elements": len(lanelet_map.regulatoryElementLayer),
            "parser_errors": len(parser_errors),
            "errors": errors,
            "warnings": warnings,
            "unwaived_warnings": unwaived_warnings,
        },
        "routing": {
            "component_count": len(components),
            "components_with_route": components_with_route,
            "relations": dict(sorted(relation_counts.items())),
        },
        "native_validator": {
            "status": "command_recorded_not_run",
            "command": native_command,
            "note": (
                "lanelet2_validate is not installed on this host; run the recorded "
                "command in the pinned Lanelet2 1.2.2 tool image."
            ),
        },
        "waiver_count": len(waivers),
        "findings": findings,
    }
    reports = workspace / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    json_path = reports / "lanelet2-validation.json"
    markdown_path = reports / "lanelet2-validation.md"
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    markdown_path.write_text(_markdown(report), encoding="utf-8")
    if report["status"] != "passed":
        raise LaneletValidationError(
            f"validation found {errors} error(s) and {unwaived_warnings} "
            f"unwaived warning(s); see {json_path}"
        )
    return json_path
