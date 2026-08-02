"""Stage 1B projection, coordinate traceability, and preflight checks."""

from __future__ import annotations

import hashlib
import json
import math
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import geopandas as gpd
import networkx as nx
import osmnx as ox
import pyproj
from pyproj import CRS, Transformer
from shapely.geometry import LineString
from shapely.ops import transform as transform_geometry

from osm_scenario.config import ConverterConfig
from osm_scenario.stage1b_data_audit import generate_stage1b_data_audit


class NormalizationError(RuntimeError):
    """Raised when Stage 1B cannot produce a safe projected network."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _workspace_path(path: Path, workspace: Path) -> str:
    return path.resolve().relative_to(workspace.resolve()).as_posix()


def _serializable_gdf(frame: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    result = frame.copy()
    for column in result.columns:
        if column == result.geometry.name:
            continue
        if result[column].dtype == object:
            result[column] = result[column].map(
                lambda value: json.dumps(value, sort_keys=True)
                if isinstance(value, (dict, list, tuple, set))
                else value
            )
    return result


def _save_geopackage(graph: Any, path: Path) -> None:
    nodes, edges = ox.graph_to_gdfs(graph, nodes=True, edges=True)
    _serializable_gdf(nodes).to_file(path, layer="nodes", driver="GPKG", index=True)
    _serializable_gdf(edges).to_file(path, layer="edges", driver="GPKG", index=True)


def _osm_identifier(data: dict[str, Any], fallback: str) -> str:
    value = data.get("osmid", fallback)
    if isinstance(value, list):
        return ",".join(str(item) for item in value)
    return str(value)


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _preflight(graph: Any) -> dict[str, list[dict[str, Any]]]:
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    if len(graph.nodes) == 0 or len(graph.edges) == 0:
        errors.append({"code": "empty_network", "osm_id": None, "reason": "no roads remain"})
        return {
            "errors": errors,
            "warnings": warnings,
            "discarded_features": [],
            "inferred_values": [],
        }

    for node_id, data in graph.nodes(data=True):
        coordinates = (data.get("x"), data.get("y"))
        if any(value is None or not math.isfinite(float(value)) for value in coordinates):
            errors.append(
                {
                    "code": "invalid_node_coordinate",
                    "osm_id": str(node_id),
                    "reason": "node has missing or non-finite projected coordinates",
                }
            )

    for u, v, key, data in graph.edges(keys=True, data=True):
        edge_id = _osm_identifier(data, f"{u}:{v}:{key}")
        geometry = data.get("geometry")
        if geometry is None:
            start = graph.nodes[u]
            end = graph.nodes[v]
            geometry = LineString([(start["x"], start["y"]), (end["x"], end["y"])])
        if geometry.is_empty or not geometry.is_valid:
            errors.append(
                {
                    "code": "invalid_edge_geometry",
                    "osm_id": edge_id,
                    "reason": "edge geometry is empty or invalid",
                }
            )

        if _positive_int(data.get("lanes")) is None and not (
            _positive_int(data.get("lanes:forward"))
            or _positive_int(data.get("lanes:backward"))
        ):
            warnings.append(
                {
                    "code": "missing_lane_count",
                    "osm_id": edge_id,
                    "reason": "no usable lanes or directional lane-count tag is present",
                }
            )

        oneway = str(data.get("oneway", "")).lower()
        forward = _positive_int(data.get("lanes:forward"))
        backward = _positive_int(data.get("lanes:backward"))
        conflict = (oneway in {"yes", "true", "1"} and backward is not None) or (
            oneway == "-1" and forward is not None
        )
        if conflict:
            warnings.append(
                {
                    "code": "conflicting_direction_tags",
                    "osm_id": edge_id,
                    "reason": "oneway direction conflicts with directional lane-count tags",
                }
            )

    components = sorted(nx.weakly_connected_components(graph), key=len, reverse=True)
    for component_index, component in enumerate(components[1:], start=2):
        warnings.append(
            {
                "code": "disconnected_component",
                "osm_id": None,
                "reason": f"component {component_index} contains {len(component)} nodes",
                "node_ids": [str(node_id) for node_id in sorted(component, key=str)],
            }
        )

    unique_warnings = []
    seen_warnings = set()
    for warning in warnings:
        identity = (warning["code"], warning.get("osm_id"), warning["reason"])
        if identity not in seen_warnings:
            seen_warnings.add(identity)
            unique_warnings.append(warning)

    return {
        "errors": errors,
        "warnings": unique_warnings,
        "discarded_features": [],
        "inferred_values": [],
    }


def _project_graph(graph: Any, transformer: Transformer, local_crs: CRS) -> Any:
    projected = deepcopy(graph)
    for _, data in projected.nodes(data=True):
        data["x"], data["y"] = transformer.transform(float(data["x"]), float(data["y"]))
    for *_, data in projected.edges(keys=True, data=True):
        if data.get("geometry") is not None:
            data["geometry"] = transform_geometry(transformer.transform, data["geometry"])
    projected.graph["crs"] = local_crs
    projected.graph["projected"] = True
    return projected


def _stage_parity(source: Any, projected: Any) -> dict[str, Any]:
    source_nodes = set(source.nodes)
    projected_nodes = set(projected.nodes)
    source_edges = set(source.edges(keys=True))
    projected_edges = set(projected.edges(keys=True))
    attribute_mismatches = []

    for node_id in sorted(source_nodes & projected_nodes, key=str):
        before = {
            key: value for key, value in source.nodes[node_id].items() if key not in {"x", "y"}
        }
        after = {
            key: value for key, value in projected.nodes[node_id].items() if key not in {"x", "y"}
        }
        if before != after:
            attribute_mismatches.append(f"node:{node_id}")
    for edge_id in sorted(source_edges & projected_edges, key=str):
        before = {
            key: value for key, value in source.edges[edge_id].items() if key != "geometry"
        }
        after = {
            key: value for key, value in projected.edges[edge_id].items() if key != "geometry"
        }
        if before != after:
            attribute_mismatches.append(f"edge:{edge_id[0]}:{edge_id[1]}:{edge_id[2]}")

    result = {
        "status": "passed",
        "missing_node_ids": sorted(str(value) for value in source_nodes - projected_nodes),
        "extra_node_ids": sorted(str(value) for value in projected_nodes - source_nodes),
        "missing_edges": sorted(str(value) for value in source_edges - projected_edges),
        "extra_edges": sorted(str(value) for value in projected_edges - source_edges),
        "attribute_mismatches": attribute_mismatches,
    }
    if any(value for key, value in result.items() if key != "status"):
        result["status"] = "failed"
    return result


def _markdown_report(report: dict[str, Any]) -> str:
    projection = report["projection"]
    preflight = report["preflight"]
    source_audit = report["source_audit"]
    lines = [
        "# Stage 1 Acquisition and Normalization Report",
        "",
        f"- Status: **{report['status']}**",
        f"- Nodes: {report['feature_counts']['nodes']}",
        f"- Directed edges: {report['feature_counts']['edges']}",
        f"- Road-selection policy: `{source_audit['policy_id']}`",
        f"- Source audit: **{source_audit['status']}**",
        f"- Selected source ways: {source_audit['selected_source_ways']}",
        f"- Excluded source highways: {source_audit['excluded_source_ways']}",
        f"- Origin: {projection['origin']['longitude']}, {projection['origin']['latitude']}",
        f"- Origin source: {projection['origin']['source']}",
        f"- Local CRS: `{projection['local_crs_projjson']['name']}`",
        f"- Axes: {projection['axis_order']}",
        f"- Units: {projection['units']}",
        "- Maximum round-trip error: "
        f"{projection['round_trip']['maximum_error_degrees']:.3e} degrees",
        f"- Configured tolerance: {projection['round_trip']['tolerance_degrees']:.3e} degrees",
        "",
        "## Preflight Errors",
        "",
    ]
    errors = preflight["errors"]
    lines.extend(
        [f"- `{item['code']}` OSM `{item['osm_id']}`: {item['reason']}" for item in errors]
        or ["- None"]
    )
    lines.extend(["", "## Preflight Warnings", ""])
    warnings = preflight["warnings"]
    if warnings:
        warning_counts: dict[str, int] = {}
        for item in warnings:
            warning_counts[item["code"]] = warning_counts.get(item["code"], 0) + 1
        lines.extend(
            f"- `{code}`: {count} affected feature(s)"
            for code, count in sorted(warning_counts.items())
        )
        lines.extend(["", "### Warning Sample", ""])
        lines.extend(
            f"- `{item['code']}` OSM `{item['osm_id']}`: {item['reason']}"
            for item in warnings[:20]
        )
        if len(warnings) > 20:
            lines.append(f"- {len(warnings) - 20} more; see `acquisition.json` for the full list.")
    else:
        lines.append("- None")
    lines.extend(
        [
            "",
            "## Discarded Features",
            "",
            "- None" if not preflight["discarded_features"] else "- See the JSON report.",
            "",
            "## Inferred Values",
            "",
            "- None" if not preflight["inferred_values"] else "- See the JSON report.",
            "",
        ]
    )
    return "\n".join(lines)


def normalize_workspace(*, workspace: Path, config: ConverterConfig) -> Path:
    """Project a saved Stage 1A graph and write Stage 1B artifacts and reports."""
    workspace = workspace.resolve()
    manifest_path = workspace / "source" / "manifest.json"
    if not manifest_path.is_file():
        raise NormalizationError(f"Stage 1A manifest not found: {manifest_path}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    graph_artifact = manifest.get("artifacts", {}).get("graphml", {})
    graph_path = workspace / graph_artifact.get("path", "")
    if not graph_path.is_file():
        raise NormalizationError(f"Stage 1A graph not found: {graph_path}")
    if graph_artifact.get("sha256") != _sha256(graph_path):
        raise NormalizationError("Stage 1A GraphML checksum does not match the manifest")

    graph = ox.load_graphml(graph_path)
    xs = [float(data["x"]) for _, data in graph.nodes(data=True)]
    ys = [float(data["y"]) for _, data in graph.nodes(data=True)]
    if not xs or not ys:
        raise NormalizationError("Stage 1A graph is empty")

    if config.coordinate_origin is not None:
        origin_lon = config.coordinate_origin.longitude
        origin_lat = config.coordinate_origin.latitude
        origin_source = "explicit_config"
    else:
        edges = ox.graph_to_gdfs(graph, nodes=False, edges=True, fill_edge_geometry=True)
        centroid = edges.geometry.union_all().centroid
        origin_lon = float(centroid.x)
        origin_lat = float(centroid.y)
        origin_source = "network_geometry_centroid"

    source_crs = CRS.from_epsg(4326)
    local_crs = CRS.from_proj4(
        f"+proj=aeqd +lat_0={origin_lat:.12f} +lon_0={origin_lon:.12f} "
        "+datum=WGS84 +units=m +no_defs +type=crs"
    )
    forward = Transformer.from_crs(source_crs, local_crs, always_xy=True)
    inverse = Transformer.from_crs(local_crs, source_crs, always_xy=True)
    projected = _project_graph(graph, forward, local_crs)

    round_trip_errors = []
    for lon, lat in zip(xs, ys, strict=True):
        local_x, local_y = forward.transform(lon, lat)
        restored_lon, restored_lat = inverse.transform(local_x, local_y)
        round_trip_errors.append(max(abs(restored_lon - lon), abs(restored_lat - lat)))
    maximum_error = max(round_trip_errors, default=0.0)

    preflight = _preflight(projected)
    if maximum_error > config.coordinate_round_trip_tolerance_degrees:
        preflight["errors"].append(
            {
                "code": "coordinate_round_trip_failed",
                "osm_id": None,
                "reason": (
                    f"maximum error {maximum_error:.3e} exceeds tolerance "
                    f"{config.coordinate_round_trip_tolerance_degrees:.3e} degrees"
                ),
            }
        )

    normalized_dir = workspace / "normalized"
    reports_dir = workspace / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    projected_graph_path = normalized_dir / "road-network-local.graphml"
    projected_gpkg_path = normalized_dir / "road-network-local.gpkg"
    report_json_path = reports_dir / "acquisition.json"
    report_markdown_path = reports_dir / "acquisition.md"

    if projected_gpkg_path.exists():
        projected_gpkg_path.unlink()
    ox.save_graphml(projected, filepath=projected_graph_path)
    _save_geopackage(projected, projected_gpkg_path)
    reloaded_projected = ox.load_graphml(projected_graph_path)
    stage_parity = _stage_parity(graph, reloaded_projected)
    if stage_parity["status"] != "passed":
        preflight["errors"].append(
            {
                "code": "stage_1_parity_failed",
                "osm_id": None,
                "reason": "Stage 1B changed topology or non-geometric source attributes",
            }
        )

    local_xs = [float(data["x"]) for _, data in projected.nodes(data=True)]
    local_ys = [float(data["y"]) for _, data in projected.nodes(data=True)]
    source_audit = manifest["road_selection"]
    report = {
        "report_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "failed" if preflight["errors"] or source_audit["errors"] else "passed",
        "source_audit": source_audit,
        "stage_1a_to_1b_parity": stage_parity,
        "feature_counts": {"nodes": len(projected.nodes), "edges": len(projected.edges)},
        "bounds": {
            "wgs84": manifest["graph"]["bounds_wgs84"],
            "local_metres": {
                "west": min(local_xs),
                "south": min(local_ys),
                "east": max(local_xs),
                "north": max(local_ys),
            },
        },
        "projection": {
            "source_crs": "EPSG:4326",
            "local_crs_wkt": local_crs.to_wkt(),
            "local_crs_projjson": local_crs.to_json_dict(),
            "origin": {
                "longitude": origin_lon,
                "latitude": origin_lat,
                "source": origin_source,
            },
            "axis_order": "x=east,y=north",
            "units": "metre",
            "always_xy": True,
            "pyproj_version": pyproj.__version__,
            "forward": "EPSG:4326 to local_crs, always_xy=True",
            "inverse": "local_crs to EPSG:4326, always_xy=True",
            "round_trip": {
                "sample_count": len(round_trip_errors),
                "maximum_error_degrees": maximum_error,
                "tolerance_degrees": config.coordinate_round_trip_tolerance_degrees,
            },
        },
        "preflight": preflight,
    }
    report_json_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    report_markdown_path.write_text(_markdown_report(report), encoding="utf-8")

    audit_json_path, audit_markdown_path = generate_stage1b_data_audit(
        workspace=workspace,
        graph=reloaded_projected,
        acquisition_report=report,
        config=config,
    )

    stage_1b_artifacts = {}
    for name, path in (
        ("projected_graphml", projected_graph_path),
        ("projected_geopackage", projected_gpkg_path),
        ("acquisition_json", report_json_path),
        ("acquisition_markdown", report_markdown_path),
        ("stage_1b_data_audit_json", audit_json_path),
        ("stage_1b_data_audit_markdown", audit_markdown_path),
    ):
        stage_1b_artifacts[name] = {
            "path": _workspace_path(path, workspace),
            "sha256": _sha256(path),
            "size_bytes": path.stat().st_size,
        }
    manifest["stage_1b"] = {
        "status": report["status"],
        "projection": report["projection"],
        "preflight_counts": {
            "errors": len(preflight["errors"]),
            "warnings": len(preflight["warnings"]),
            "discarded_features": len(preflight["discarded_features"]),
            "inferred_values": len(preflight["inferred_values"]),
        },
        "artifacts": stage_1b_artifacts,
    }
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    if preflight["errors"]:
        raise NormalizationError(
            f"preflight found {len(preflight['errors'])} blocking error(s); see {report_json_path}"
        )
    return report_json_path
