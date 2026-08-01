"""Stage 1A OSM acquisition and durable artifact generation."""

from __future__ import annotations

import hashlib
import json
import platform
import shutil
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Literal

import geopandas as gpd
import osmnx as ox

SourceType = Literal["local_file", "place", "bounding_box"]

PRESERVED_WAY_TAGS = {
    "access",
    "area",
    "bridge",
    "crossing",
    "cycleway",
    "driving_side",
    "est_width",
    "highway",
    "junction",
    "lanes",
    "lanes:backward",
    "lanes:both_ways",
    "lanes:forward",
    "maxspeed",
    "name",
    "oneway",
    "ref",
    "service",
    "sidewalk",
    "surface",
    "traffic_signals",
    "turn:lanes",
    "turn:lanes:backward",
    "turn:lanes:forward",
    "width",
}
PRESERVED_NODE_TAGS = {"crossing", "highway", "traffic_signals"}


class AcquisitionError(RuntimeError):
    """Raised when Stage 1A cannot produce a valid source network."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _package_version(name: str) -> str:
    try:
        return version(name)
    except PackageNotFoundError:
        return "unknown"


def _workspace_path(path: Path, workspace: Path) -> str:
    return path.resolve().relative_to(workspace.resolve()).as_posix()


def _prepare_local_source(osm_file: Path, source_dir: Path) -> tuple[Path, str]:
    original = osm_file.resolve()
    source_dir_resolved = source_dir.resolve()
    before = _sha256(original)

    if original.parent == source_dir_resolved:
        return original, before

    destination = source_dir / "map.osm"
    if destination.exists():
        raise AcquisitionError(
            f"refusing to replace existing workspace source: {destination}"
        )
    shutil.copy2(original, destination)
    if _sha256(destination) != before:
        raise AcquisitionError("copied OSM source checksum does not match the original")
    return destination, before


def _configure_preserved_tags() -> None:
    ox.settings.useful_tags_way = sorted(
        set(ox.settings.useful_tags_way) | PRESERVED_WAY_TAGS
    )
    ox.settings.useful_tags_node = sorted(
        set(ox.settings.useful_tags_node) | PRESERVED_NODE_TAGS
    )


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


def acquire_osm(
    *,
    workspace: Path,
    driving_side: Literal["left", "right"],
    osm_file: Path | None = None,
    place: str | None = None,
    bbox: tuple[float, float, float, float] | None = None,
) -> Path:
    """Acquire one source and write the complete Stage 1A workspace."""
    workspace = workspace.resolve()
    source_dir = workspace / "source"
    normalized_dir = workspace / "normalized"
    source_dir.mkdir(parents=True, exist_ok=True)
    normalized_dir.mkdir(parents=True, exist_ok=True)
    _configure_preserved_tags()

    source_details: dict[str, Any]
    original_checksum: str | None = None
    if osm_file is not None:
        source_type: SourceType = "local_file"
        source_path, original_checksum = _prepare_local_source(osm_file, source_dir)
        graph = ox.graph_from_xml(source_path, simplify=False, retain_all=True)
        source_details = {
            "input_path": str(osm_file.resolve()),
            "osm_xml_kind": "original_local_file",
        }
    elif place is not None:
        source_type = "place"
        graph = ox.graph_from_place(place, network_type="drive", simplify=False, retain_all=True)
        source_path = source_dir / "map.osm"
        ox.save_graph_xml(graph, filepath=source_path)
        source_details = {"place_query": place, "osm_xml_kind": "osmnx_graph_export"}
    elif bbox is not None:
        source_type = "bounding_box"
        west, south, east, north = bbox
        if west >= east or south >= north:
            raise AcquisitionError("bbox must satisfy WEST < EAST and SOUTH < NORTH")
        graph = ox.graph_from_bbox(
            (west, south, east, north),
            network_type="drive",
            simplify=False,
            retain_all=True,
        )
        source_path = source_dir / "map.osm"
        ox.save_graph_xml(graph, filepath=source_path)
        source_details = {
            "requested_bounds": {"west": west, "south": south, "east": east, "north": north},
            "osm_xml_kind": "osmnx_graph_export",
        }
    else:
        raise AcquisitionError("exactly one OSM source is required")

    if len(graph.nodes) == 0 or len(graph.edges) == 0:
        raise AcquisitionError("OSM source produced an empty driving graph")

    graphml_path = normalized_dir / "road-network.graphml"
    gpkg_path = normalized_dir / "road-network.gpkg"
    if gpkg_path.exists():
        gpkg_path.unlink()
    ox.save_graphml(graph, filepath=graphml_path)
    _save_geopackage(graph, gpkg_path)

    xs = [float(data["x"]) for _, data in graph.nodes(data=True)]
    ys = [float(data["y"]) for _, data in graph.nodes(data=True)]
    artifacts = {}
    for name, path in (
        ("osm_xml", source_path),
        ("graphml", graphml_path),
        ("geopackage", gpkg_path),
    ):
        artifacts[name] = {
            "path": _workspace_path(path, workspace),
            "sha256": _sha256(path),
            "size_bytes": path.stat().st_size,
        }

    manifest = {
        "manifest_version": 1,
        "acquired_at": datetime.now(timezone.utc).isoformat(),
        "attribution": "OpenStreetMap contributors",
        "source": {
            "type": source_type,
            "path": _workspace_path(source_path, workspace),
            "sha256": artifacts["osm_xml"]["sha256"],
            **source_details,
        },
        "driving_side": driving_side,
        "driving_side_source": "explicit_cli",
        "graph": {
            "directed": bool(graph.is_directed()),
            "simplified": bool(graph.graph.get("simplified", False)),
            "node_count": len(graph.nodes),
            "edge_count": len(graph.edges),
            "bounds_wgs84": {
                "west": min(xs),
                "south": min(ys),
                "east": max(xs),
                "north": max(ys),
            },
        },
        "artifacts": artifacts,
        "tool_versions": {
            "converter": _package_version("wingfin-osm-scenarionet-converter"),
            "python": platform.python_version(),
            "osmnx": _package_version("osmnx"),
            "geopandas": _package_version("geopandas"),
            "pyproj": _package_version("pyproj"),
            "shapely": _package_version("shapely"),
        },
    }
    if original_checksum is not None:
        manifest["source"]["original_input_sha256"] = original_checksum

    manifest_path = source_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest_path
