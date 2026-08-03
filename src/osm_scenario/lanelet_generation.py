"""Stage 2 preliminary Lanelet2 geometry generation."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import networkx as nx
import osmnx as ox
from lanelet2 import io, projection
from lanelet2.core import (
    AttributeMap,
    GPSPoint,
    Lanelet,
    LaneletMap,
    LineString3d,
    Point3d,
    TrafficLight,
)
from pyproj import CRS, Transformer
from shapely.geometry import LineString, Point

from osm_scenario.config import ConverterConfig
from osm_scenario.osm_source import OsmRelation, OsmSnapshot, OsmWay, read_osm_snapshot


class LaneletGenerationError(RuntimeError):
    """Raised when Stage 2 cannot safely generate a preliminary map."""


@dataclass
class GeneratedLane:
    lanelet: Lanelet
    centerline: LineString
    source_way_id: str
    source_u: str
    source_v: str
    lane_index: int
    lane_count: int
    turn_tokens: frozenset[str]


class StableIds:
    """Allocate deterministic positive 63-bit IDs and reject collisions."""

    def __init__(self) -> None:
        self._used: dict[int, str] = {}

    def get(self, namespace: str, *parts: object) -> int:
        identity = "\x1f".join((namespace, *(str(part) for part in parts)))
        value = int.from_bytes(hashlib.sha256(identity.encode()).digest()[:8], "big")
        value &= (1 << 63) - 1
        value = value or 1
        previous = self._used.setdefault(value, identity)
        if previous != identity:
            raise LaneletGenerationError(
                f"stable ID collision between {previous!r} and {identity!r}"
            )
        return value


class PrimitiveFactory:
    def __init__(
        self,
        *,
        identifiers: StableIds,
        inverse_local: Transformer,
        projector: projection.Projector,
    ) -> None:
        self.identifiers = identifiers
        self.inverse_local = inverse_local
        self.projector = projector
        self.boundaries: dict[tuple[tuple[float, float], ...], LineString3d] = {}

    @staticmethod
    def _coordinate_key(coordinates: list[tuple[float, float]]) -> tuple[tuple[float, float], ...]:
        return tuple((round(x, 4), round(y, 4)) for x, y in coordinates)

    def _point(self, namespace: str, index: int, x: float, y: float) -> Point3d:
        lon, lat = self.inverse_local.transform(x, y)
        projected = self.projector.forward(GPSPoint(lat, lon, 0.0))
        return Point3d(
            self.identifiers.get("point", namespace, index, f"{x:.4f}", f"{y:.4f}"),
            projected.x,
            projected.y,
            projected.z,
        )

    def linestring(
        self,
        namespace: str,
        coordinates: list[tuple[float, float]],
        attributes: dict[str, str],
        *,
        reuse: bool = False,
    ) -> LineString3d:
        coordinates = _deduplicate_coordinates(coordinates)
        if len(coordinates) < 2:
            raise LaneletGenerationError(f"{namespace} has fewer than two distinct points")
        key = self._coordinate_key(coordinates)
        reverse_key = tuple(reversed(key))
        if reuse and key in self.boundaries:
            return self.boundaries[key]
        if reuse and reverse_key in self.boundaries:
            return self.boundaries[reverse_key].invert()
        line = LineString3d(
            self.identifiers.get("linestring", namespace),
            [self._point(namespace, index, x, y) for index, (x, y) in enumerate(coordinates)],
            AttributeMap(attributes),
        )
        if reuse:
            self.boundaries[key] = line
        return line


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _artifact(path: Path, workspace: Path) -> dict[str, Any]:
    return {
        "path": path.resolve().relative_to(workspace.resolve()).as_posix(),
        "sha256": _sha256(path),
        "size_bytes": path.stat().st_size,
    }


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _positive_float(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip().lower().replace("metres", "").replace("meters", "").strip()
    try:
        parsed = float(text)
    except ValueError:
        return None
    return parsed if parsed > 0 else None


def _bool_oneway(tags: dict[str, str]) -> bool:
    return tags.get("oneway") in {"yes", "true", "1", "-1", "reverse"} or (
        tags.get("junction") == "roundabout" and tags.get("oneway") != "no"
    )


def _edge_way(data: dict[str, Any], snapshot: OsmSnapshot) -> tuple[str, OsmWay]:
    value = data.get("osmid")
    values = value if isinstance(value, list) else [value]
    matches = [
        (str(item), snapshot.ways[str(item)]) for item in values if str(item) in snapshot.ways
    ]
    if len(matches) != 1:
        raise LaneletGenerationError(f"edge has {len(matches)} resolvable source ways: {value!r}")
    return matches[0]


def _forward_along_way(way: OsmWay, u: object, v: object) -> bool:
    source_u, source_v = str(u), str(v)
    for first, second in zip(way.node_ids, way.node_ids[1:], strict=False):
        if (first, second) == (source_u, source_v):
            return True
        if (first, second) == (source_v, source_u):
            return False
    raise LaneletGenerationError(
        f"edge {source_u}->{source_v} is not a consecutive pair in OSM way {way.identifier}"
    )


def _lane_count(tags: dict[str, str], *, forward: bool, infer: bool) -> tuple[int, str, str | None]:
    directional_key = "lanes:forward" if forward else "lanes:backward"
    directional = _positive_int(tags.get(directional_key))
    if directional is not None:
        return directional, directional_key, None
    total = _positive_int(tags.get("lanes"))
    if total is not None and _bool_oneway(tags):
        return total, "lanes", None
    if total is not None:
        forward_count = math.ceil(total / 2)
        count = forward_count if forward else max(1, total - forward_count)
        reason = None if total % 2 == 0 else "odd total lane count lacks directional tags"
        return count, "lanes_split", reason
    if not infer:
        raise LaneletGenerationError("lane count is missing and inference is disabled")
    return 1, "inferred_default", "no usable lane-count tag; inferred one lane"


def _lane_width(tags: dict[str, str], lane_count_total: int, default: float) -> tuple[float, str]:
    width = _positive_float(tags.get("width"))
    if width is not None and lane_count_total > 0:
        return width / lane_count_total, "width"
    return default, "configured_default"


def _edge_geometry(
    graph: nx.MultiDiGraph, u: object, v: object, data: dict[str, Any]
) -> LineString:
    geometry = data.get("geometry")
    if not isinstance(geometry, LineString):
        geometry = LineString(
            [
                (float(graph.nodes[u]["x"]), float(graph.nodes[u]["y"])),
                (float(graph.nodes[v]["x"]), float(graph.nodes[v]["y"])),
            ]
        )
    start = (float(graph.nodes[u]["x"]), float(graph.nodes[u]["y"]))
    coords = list(geometry.coords)
    if math.dist(coords[-1][:2], start) < math.dist(coords[0][:2], start):
        coords.reverse()
    return LineString([(float(x), float(y)) for x, y, *_ in coords])


def _offset(line: LineString, distance: float) -> LineString:
    if abs(distance) < 1e-9:
        return line
    result = line.offset_curve(distance, join_style="mitre", mitre_limit=2.0)
    if not isinstance(result, LineString) or result.is_empty or len(result.coords) < 2:
        start, end = line.coords[0], line.coords[-1]
        heading = (end[0] - start[0], end[1] - start[1])
        norm = math.hypot(*heading)
        if norm <= 1e-9:
            raise LaneletGenerationError(f"could not offset road geometry by {distance:.3f} m")
        normal = (-heading[1] / norm * distance, heading[0] / norm * distance)
        result = LineString([(x + normal[0], y + normal[1]) for x, y in line.coords])
    if math.dist(result.coords[0], line.coords[0]) > math.dist(result.coords[-1], line.coords[0]):
        result = LineString(list(result.coords)[::-1])
    return result


def _deduplicate_coordinates(
    coordinates: list[tuple[float, float]],
) -> list[tuple[float, float]]:
    result: list[tuple[float, float]] = []
    for x, y in coordinates:
        point = (float(x), float(y))
        if not result or math.dist(result[-1], point) > 1e-6:
            result.append(point)
    return result


def _turn_tokens(tags: dict[str, str], *, forward: bool, lane_index: int) -> frozenset[str]:
    value = tags.get("turn:lanes:forward" if forward else "turn:lanes:backward")
    value = value or tags.get("turn:lanes")
    if not value:
        return frozenset()
    lanes = value.split("|")
    if lane_index >= len(lanes):
        return frozenset()
    return frozenset(token.strip() for token in lanes[lane_index].split(";") if token.strip())


def _movement(incoming: LineString, outgoing: LineString) -> tuple[str, float]:
    a0, a1 = incoming.coords[-2], incoming.coords[-1]
    b0, b1 = outgoing.coords[0], outgoing.coords[1]
    first = (a1[0] - a0[0], a1[1] - a0[1])
    second = (b1[0] - b0[0], b1[1] - b0[1])
    angle = math.degrees(
        math.atan2(
            first[0] * second[1] - first[1] * second[0], first[0] * second[0] + first[1] * second[1]
        )
    )
    absolute = abs(angle)
    if absolute >= 145:
        return "reverse", angle
    if absolute <= 35:
        return "through", angle
    if angle > 0:
        return ("slight_left" if absolute < 70 else "left"), angle
    return ("slight_right" if absolute < 70 else "right"), angle


def _turn_allowed(tokens: frozenset[str], movement: str) -> bool:
    if not tokens:
        return True
    aliases = {
        "left": {"left", "slight_left", "sharp_left"},
        "slight_left": {"left", "slight_left", "sharp_left"},
        "right": {"right", "slight_right", "sharp_right"},
        "slight_right": {"right", "slight_right", "sharp_right"},
        "through": {"through"},
        "reverse": {"reverse"},
    }
    return bool(tokens & aliases[movement])


def _connector_is_ambiguous(candidate_count: int, angle: float) -> bool:
    return candidate_count > 1 or 30 <= abs(angle) <= 40


def _node_restrictions(snapshot: OsmSnapshot) -> dict[str, list[OsmRelation]]:
    result: dict[str, list[OsmRelation]] = {}
    for relation in snapshot.relations.values():
        if relation.tags.get("type") != "restriction":
            continue
        via_nodes = [
            m.reference for m in relation.members if m.member_type == "node" and m.role == "via"
        ]
        for node_id in via_nodes:
            result.setdefault(node_id, []).append(relation)
    return result


def _restriction_allows(
    relations: list[OsmRelation], from_way: str, to_way: str
) -> tuple[bool, list[str]]:
    applied: list[str] = []
    for relation in relations:
        from_ids = {
            m.reference for m in relation.members if m.member_type == "way" and m.role == "from"
        }
        to_ids = {
            m.reference for m in relation.members if m.member_type == "way" and m.role == "to"
        }
        if from_way not in from_ids:
            continue
        restriction = relation.tags.get("restriction", "")
        if restriction.startswith("no_") and to_way in to_ids:
            return False, [relation.identifier]
        if restriction.startswith("only_") and to_way not in to_ids:
            return False, [relation.identifier]
        applied.append(relation.identifier)
    return True, applied


def _connector_curve(
    incoming: LineString, outgoing: LineString, node_xy: tuple[float, float]
) -> LineString:
    start = incoming.coords[-1]
    end = outgoing.coords[0]
    if math.dist(start, end) < 0.05:
        direction = incoming.coords[-2]
        midpoint = ((start[0] + direction[0]) / 2, (start[1] + direction[1]) / 2)
        return LineString([direction, midpoint, start])
    points = []
    for step in range(5):
        t = step / 4
        x = (1 - t) ** 2 * start[0] + 2 * (1 - t) * t * node_xy[0] + t**2 * end[0]
        y = (1 - t) ** 2 * start[1] + 2 * (1 - t) * t * node_xy[1] + t**2 * end[1]
        points.append((x, y))
    return LineString(_deduplicate_coordinates(points))


def _lanelet_attributes(
    *, source_way_id: str, source_u: str, source_v: str, lane_index: int, tags: dict[str, str]
) -> dict[str, str]:
    attributes = {
        "type": "lanelet",
        "subtype": "road",
        "location": "urban",
        "participant:vehicle": "yes",
        "one_way": "yes",
        "source_osm_way_id": source_way_id,
        "source_osm_from_node": source_u,
        "source_osm_to_node": source_v,
        "source_lane_index": str(lane_index),
    }
    if tags.get("highway"):
        attributes["source_highway"] = tags["highway"]
    if tags.get("maxspeed"):
        attributes["speed_limit"] = tags["maxspeed"]
    return attributes


def _report_markdown(report: dict[str, Any]) -> str:
    counts = report["counts"]
    inference_counts: dict[str, int] = {}
    for item in report["inferences"]:
        inference_counts[item["code"]] = inference_counts.get(item["code"], 0) + 1
    correction_counts: dict[str, int] = {}
    for item in report["correction_queue"]:
        correction_counts[item["priority"]] = correction_counts.get(item["priority"], 0) + 1
    lines = [
        "# Stage 2 Preliminary Lanelet2 Generation",
        "",
        f"- Status: **{report['status']}**",
        f"- Road lanelets: {counts['road_lanelets']}",
        f"- Intersection connectors: {counts['connector_lanelets']}",
        f"- Traffic-light associations: {counts['traffic_light_associations']}",
        f"- Mapped stop lines used: {counts['mapped_stop_lines']}",
        f"- Inferred stop lines: {counts['inferred_stop_lines']}",
        f"- Parser errors: {len(report['parser_errors'])}",
        "",
        "## Inference Summary",
        "",
    ]
    lines.extend(
        [f"- `{code}`: {count}" for code, count in sorted(inference_counts.items())] or ["- None"]
    )
    lines.extend(["", "## Correction Queue", ""])
    lines.extend(
        [f"- `{priority}`: {count}" for priority, count in sorted(correction_counts.items())]
        or ["- None"]
    )
    lines.extend(
        [
            "",
            (
                "See `lanelet2-generation.json` for source IDs, reasons, confidence, "
                "and all generated IDs."
            ),
            "",
        ]
    )
    return "\n".join(lines)


def generate_preliminary_lanelet2(*, workspace: Path, config: ConverterConfig) -> Path:
    """Generate preliminary Lanelet2 geometry from checked Stage 1 artifacts."""
    workspace = workspace.resolve()
    manifest_path = workspace / "source" / "manifest.json"
    acquisition_path = workspace / "reports" / "acquisition.json"
    audit_path = workspace / "reports" / "stage-1b-data-audit.json"
    for required in (manifest_path, acquisition_path, audit_path):
        if not required.is_file():
            raise LaneletGenerationError(f"required Stage 1 artifact not found: {required}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    acquisition = json.loads(acquisition_path.read_text(encoding="utf-8"))
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    if acquisition.get("status") != "passed" or audit.get("stage_1b_status") != "passed":
        raise LaneletGenerationError("Stage 1 checks have not passed")
    if audit.get("downstream_readiness") == "blocked":
        raise LaneletGenerationError("Stage 1B audit blocks generation; review its report")

    source_path = workspace / manifest["source"]["path"]
    local_graph_info = (
        manifest.get("stage_1b", {}).get("artifacts", {}).get("projected_graphml", {})
    )
    graph_path = workspace / local_graph_info.get("path", "")
    if not source_path.is_file() or not graph_path.is_file():
        raise LaneletGenerationError("Stage 1 source or local graph is missing")
    if local_graph_info.get("sha256") != _sha256(graph_path):
        raise LaneletGenerationError("Stage 1B local GraphML checksum does not match the manifest")
    if manifest["source"].get("sha256") != _sha256(source_path):
        raise LaneletGenerationError("source OSM checksum does not match the manifest")

    snapshot = read_osm_snapshot(source_path)
    graph = ox.load_graphml(graph_path)
    origin_data = acquisition["projection"]["origin"]
    origin = io.Origin(float(origin_data["latitude"]), float(origin_data["longitude"]))
    lanelet_projector = projection.LocalCartesianProjector(origin)
    inverse_local = Transformer.from_crs(
        CRS.from_user_input(graph.graph["crs"]), 4326, always_xy=True
    )
    forward_local = Transformer.from_crs(
        4326, CRS.from_user_input(graph.graph["crs"]), always_xy=True
    )
    identifiers = StableIds()
    primitives = PrimitiveFactory(
        identifiers=identifiers, inverse_local=inverse_local, projector=lanelet_projector
    )
    lanelet_map = LaneletMap()
    driving_side = config.driving_side or manifest["driving_side"]
    if config.driving_side is not None and config.driving_side != manifest["driving_side"]:
        raise LaneletGenerationError("configured driving side conflicts with the source manifest")

    inferences: list[dict[str, Any]] = []
    corrections: list[dict[str, Any]] = []
    generated_lanes: list[GeneratedLane] = []
    incoming: dict[str, list[GeneratedLane]] = {}
    outgoing: dict[str, list[GeneratedLane]] = {}
    lane_records: list[dict[str, Any]] = []

    for u, v, key, data in sorted(
        graph.edges(keys=True, data=True), key=lambda item: tuple(map(str, item[:3]))
    ):
        way_id, way = _edge_way(data, snapshot)
        tags = way.tags
        forward = _forward_along_way(way, u, v)
        count, count_source, count_reason = _lane_count(
            tags, forward=forward, infer=config.tag_inference.infer_missing_lane_count
        )
        total = _positive_int(tags.get("lanes"))
        if total is None:
            directional_total = sum(
                value
                for value in (
                    _positive_int(tags.get("lanes:forward")),
                    _positive_int(tags.get("lanes:backward")),
                )
                if value is not None
            )
            total = directional_total or count * (1 if _bool_oneway(tags) else 2)
        width, width_source = _lane_width(tags, total, config.lane_width_defaults.vehicle)
        center = _edge_geometry(graph, u, v, data)
        if _bool_oneway(tags):
            offsets = [((count - 1) / 2 - index) * width for index in range(count)]
        elif driving_side == "left":
            offsets = [(count - index - 0.5) * width for index in range(count)]
        else:
            offsets = [-(index + 0.5) * width for index in range(count)]

        if count_reason:
            item = {
                "code": "lane_count_inferred"
                if count_source == "inferred_default"
                else "lane_count_ambiguous",
                "source_osm_way_id": way_id,
                "source_edge": [str(u), str(v), str(key)],
                "value": count,
                "reason": count_reason,
                "confidence": "medium" if count_source == "inferred_default" else "low",
            }
            inferences.append(item)
            corrections.append(
                {**item, "priority": "high" if item["confidence"] == "low" else "medium"}
            )
        if width_source == "configured_default":
            item = {
                "code": "lane_width_inferred",
                "source_osm_way_id": way_id,
                "source_edge": [str(u), str(v), str(key)],
                "value_metres": width,
                "reason": "no usable source width; used configured vehicle lane width",
                "confidence": "medium",
            }
            inferences.append(item)
            corrections.append({**item, "priority": "medium"})

        for lane_index, offset in enumerate(offsets):
            centerline = _offset(center, offset)
            left = _offset(center, offset + width / 2)
            right = _offset(center, offset - width / 2)
            namespace = f"road:{way_id}:{u}:{v}:{key}:{lane_index}"
            boundary_attributes = AttributeMap({"type": "line_thin", "subtype": "solid"})
            left_bound = primitives.linestring(
                f"{namespace}:left", list(left.coords), dict(boundary_attributes), reuse=True
            )
            right_bound = primitives.linestring(
                f"{namespace}:right", list(right.coords), dict(boundary_attributes), reuse=True
            )
            lanelet_id = identifiers.get("lanelet", namespace)
            lanelet = Lanelet(
                lanelet_id,
                left_bound,
                right_bound,
                AttributeMap(
                    _lanelet_attributes(
                        source_way_id=way_id,
                        source_u=str(u),
                        source_v=str(v),
                        lane_index=lane_index,
                        tags=tags,
                    )
                ),
            )
            lanelet.centerline = primitives.linestring(
                f"{namespace}:center", list(centerline.coords), {"type": "virtual"}
            )
            lanelet_map.add(lanelet)
            generated = GeneratedLane(
                lanelet=lanelet,
                centerline=centerline,
                source_way_id=way_id,
                source_u=str(u),
                source_v=str(v),
                lane_index=lane_index,
                lane_count=count,
                turn_tokens=_turn_tokens(tags, forward=forward, lane_index=lane_index),
            )
            generated_lanes.append(generated)
            incoming.setdefault(str(v), []).append(generated)
            outgoing.setdefault(str(u), []).append(generated)
            lane_records.append(
                {
                    "lanelet_id": lanelet_id,
                    "kind": "road",
                    "source_osm_way_id": way_id,
                    "source_edge": [str(u), str(v), str(key)],
                    "lane_index": lane_index,
                    "lane_count": count,
                    "lane_count_source": count_source,
                    "lane_width_metres": width,
                    "lane_width_source": width_source,
                    "turn_tokens": sorted(generated.turn_tokens),
                }
            )

    node_restrictions = _node_restrictions(snapshot)
    connector_count = 0
    restricted_movements = 0
    ambiguous_connectors = 0
    for node_id in sorted(set(incoming) & set(outgoing)):
        node_xy = (float(graph.nodes[int(node_id)]["x"]), float(graph.nodes[int(node_id)]["y"]))
        for source_lane in incoming[node_id]:
            candidates: list[tuple[GeneratedLane, str, float]] = []
            for target_lane in outgoing[node_id]:
                movement, angle = _movement(source_lane.centerline, target_lane.centerline)
                if movement == "reverse" and source_lane.source_way_id == target_lane.source_way_id:
                    continue
                if not _turn_allowed(source_lane.turn_tokens, movement):
                    continue
                allowed, _ = _restriction_allows(
                    node_restrictions.get(node_id, []),
                    source_lane.source_way_id,
                    target_lane.source_way_id,
                )
                if not allowed:
                    restricted_movements += 1
                    continue
                candidates.append((target_lane, movement, angle))

            same_way = [
                candidate
                for candidate in candidates
                if candidate[0].source_way_id == source_lane.source_way_id
            ]
            if same_way:
                same_index = [
                    candidate
                    for candidate in same_way
                    if candidate[0].lane_index == source_lane.lane_index
                ]
                candidates = same_index or same_way[:1]
            for target_lane, movement, angle in candidates:
                curve = _connector_curve(source_lane.centerline, target_lane.centerline, node_xy)
                width = config.lane_width_defaults.vehicle
                left = _offset(curve, width / 2)
                right = _offset(curve, -width / 2)
                namespace = f"connector:{node_id}:{source_lane.lanelet.id}:{target_lane.lanelet.id}"
                connector = Lanelet(
                    identifiers.get("lanelet", namespace),
                    primitives.linestring(
                        f"{namespace}:left",
                        list(left.coords),
                        {"type": "line_thin", "subtype": "solid"},
                        reuse=True,
                    ),
                    primitives.linestring(
                        f"{namespace}:right",
                        list(right.coords),
                        {"type": "line_thin", "subtype": "solid"},
                        reuse=True,
                    ),
                    AttributeMap(
                        {
                            "type": "lanelet",
                            "subtype": "road",
                            "location": "urban",
                            "participant:vehicle": "yes",
                            "one_way": "yes",
                            "source_osm_node_id": node_id,
                            "source_from_way_id": source_lane.source_way_id,
                            "source_to_way_id": target_lane.source_way_id,
                            "turn_direction": movement,
                        }
                    ),
                )
                connector.centerline = primitives.linestring(
                    f"{namespace}:center", list(curve.coords), {"type": "virtual"}
                )
                lanelet_map.add(connector)
                connector_count += 1
                is_ambiguous = _connector_is_ambiguous(len(candidates), angle)
                lane_records.append(
                    {
                        "lanelet_id": connector.id,
                        "kind": "connector",
                        "source_osm_node_id": node_id,
                        "from_lanelet_id": source_lane.lanelet.id,
                        "to_lanelet_id": target_lane.lanelet.id,
                        "movement": movement,
                        "turn_angle_degrees": round(angle, 3),
                        "confidence": "low" if is_ambiguous else "medium",
                    }
                )
                if is_ambiguous:
                    ambiguous_connectors += 1
                    corrections.append(
                        {
                            "code": "ambiguous_connector",
                            "priority": "high",
                            "confidence": "low",
                            "generated_lanelet_id": connector.id,
                            "source_osm_node_id": node_id,
                            "reason": (
                                "multiple permitted outgoing movements or borderline turn angle"
                            ),
                        }
                    )

    unsupported_restrictions = []
    for relation in snapshot.relations.values():
        if relation.tags.get("type") != "restriction":
            continue
        if any(member.member_type == "way" and member.role == "via" for member in relation.members):
            unsupported_restrictions.append(relation.identifier)
            corrections.append(
                {
                    "code": "via_way_restriction_review",
                    "priority": "high",
                    "confidence": "low",
                    "source_osm_relation_id": relation.identifier,
                    "reason": "via-way restrictions require route-context validation",
                }
            )

    signal_associations = 0
    inferred_stop_lines = 0
    mapped_stop_lines = 0
    stop_line_geometries: list[tuple[str, LineString]] = []
    for way_id in audit["stop_line_geometry"]["candidate_way_ids"]:
        way = snapshot.ways.get(way_id)
        if way is None:
            continue
        coordinates = [
            forward_local.transform(
                snapshot.nodes[node_id].longitude, snapshot.nodes[node_id].latitude
            )
            for node_id in way.node_ids
            if node_id in snapshot.nodes
        ]
        if len(coordinates) >= 2:
            stop_line_geometries.append((way_id, LineString(coordinates)))
    retained_signal_ids = {
        item["osm_node_id"]
        for item in audit["traffic_signals"]["details"]
        if item["retained_in_driving_graph"]
    }
    for node_id in sorted(retained_signal_ids):
        signal_xy = (
            float(graph.nodes[int(node_id)]["x"]),
            float(graph.nodes[int(node_id)]["y"]),
        )
        nearby_stop_lines = sorted(
            (
                (geometry.distance(Point(signal_xy)), way_id, geometry)
                for way_id, geometry in stop_line_geometries
                if geometry.distance(Point(signal_xy)) <= 15.0
            ),
            key=lambda item: (item[0], item[1]),
        )
        for lane in incoming.get(node_id, []):
            end = lane.centerline.coords[-1]
            before = lane.centerline.interpolate(max(0.0, lane.centerline.length - 1.5))
            heading = (end[0] - before.x, end[1] - before.y)
            norm = math.hypot(*heading) or 1.0
            normal = (-heading[1] / norm, heading[0] / norm)
            half_width = config.lane_width_defaults.vehicle / 2
            if nearby_stop_lines:
                stop_way_id = nearby_stop_lines[0][1]
                stop_coords = list(nearby_stop_lines[0][2].coords)
                stop_source = "mapped"
            else:
                stop_way_id = None
                stop_coords = [
                    (before.x + normal[0] * half_width, before.y + normal[1] * half_width),
                    (before.x - normal[0] * half_width, before.y - normal[1] * half_width),
                ]
                stop_source = "inferred"
            light_coords = [
                (end[0] + normal[0] * 0.5, end[1] + normal[1] * 0.5),
                (end[0] - normal[0] * 0.5, end[1] - normal[1] * 0.5),
            ]
            namespace = f"signal:{node_id}:{lane.lanelet.id}"
            stop_line = primitives.linestring(
                f"{namespace}:stop", stop_coords, {"type": "stop_line", "subtype": "solid"}
            )
            light = primitives.linestring(
                f"{namespace}:light",
                light_coords,
                {"type": "traffic_light", "subtype": "red_yellow_green"},
            )
            regulation = TrafficLight(
                identifiers.get("regulatory_element", namespace),
                AttributeMap({"source_osm_node_id": node_id}),
                [light],
                stop_line,
            )
            lane.lanelet.addRegulatoryElement(regulation)
            lanelet_map.add(regulation)
            signal_associations += 1
            association = {
                "code": "traffic_signal_association_review",
                "priority": "high",
                "confidence": "low",
                "source_osm_node_id": node_id,
                "generated_lanelet_id": lane.lanelet.id,
                "source_osm_stop_line_way_id": stop_way_id,
                "reason": "proximity-based signal-to-incoming-lanelet association requires review",
            }
            corrections.append(association)
            if stop_source == "mapped":
                mapped_stop_lines += 1
            else:
                inferred_stop_lines += 1
                item = {
                    "code": "stop_line_inferred",
                    "source_osm_node_id": node_id,
                    "generated_lanelet_id": lane.lanelet.id,
                    "value_metres_before_signal": 1.5,
                    "reason": "no mapped stop-line geometry was found within 15 metres",
                    "confidence": "low",
                }
                inferences.append(item)
                corrections.append({**item, "priority": "high"})

    for node_id in sorted(retained_signal_ids - set(incoming)):
        corrections.append(
            {
                "code": "unassociated_traffic_signal",
                "priority": "high",
                "confidence": "low",
                "source_osm_node_id": node_id,
                "reason": "retained signal has no generated incoming lanelet",
            }
        )

    lanelet_dir = workspace / "lanelet2"
    reports_dir = workspace / "reports"
    lanelet_dir.mkdir(parents=True, exist_ok=True)
    reports_dir.mkdir(parents=True, exist_ok=True)
    output_path = lanelet_dir / "preliminary.osm"
    io.write(str(output_path), lanelet_map, lanelet_projector)
    loaded, parser_errors = io.loadRobust(str(output_path), lanelet_projector)
    parser_errors = [str(error) for error in parser_errors]
    if parser_errors or len(loaded.laneletLayer) != len(lanelet_map.laneletLayer):
        raise LaneletGenerationError(
            f"generated Lanelet2 failed parser reload: {parser_errors or 'lanelet count changed'}"
        )

    report = {
        "report_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "review_required" if corrections else "passed",
        "source": {
            "osm_path": manifest["source"]["path"],
            "osm_sha256": _sha256(source_path),
            "local_graph_path": local_graph_info["path"],
            "local_graph_sha256": _sha256(graph_path),
        },
        "configuration": {
            "config_version": config.config_version,
            "driving_side": driving_side,
            "vehicle_lane_width_metres": config.lane_width_defaults.vehicle,
            "infer_missing_lane_count": config.tag_inference.infer_missing_lane_count,
            "origin": {"latitude": origin_data["latitude"], "longitude": origin_data["longitude"]},
        },
        "counts": {
            "road_lanelets": len(generated_lanes),
            "connector_lanelets": connector_count,
            "boundaries": len(lanelet_map.lineStringLayer),
            "traffic_light_associations": signal_associations,
            "inferred_stop_lines": inferred_stop_lines,
            "mapped_stop_lines": mapped_stop_lines,
            "restricted_movements_omitted": restricted_movements,
            "unsupported_via_way_restrictions": len(unsupported_restrictions),
            "ambiguous_connectors": ambiguous_connectors,
        },
        "parser_errors": parser_errors,
        "lanelets": lane_records,
        "inferences": inferences,
        "correction_queue": sorted(
            corrections,
            key=lambda item: (
                {"high": 0, "medium": 1, "low": 2}[item["priority"]],
                item["code"],
                str(item),
            ),
        ),
    }
    report_json = reports_dir / "lanelet2-generation.json"
    report_markdown = reports_dir / "lanelet2-generation.md"
    report_json.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    report_markdown.write_text(_report_markdown(report), encoding="utf-8")

    manifest["stage_2"] = {
        "status": report["status"],
        "artifacts": {
            "preliminary_lanelet2": _artifact(output_path, workspace),
            "generation_json": _artifact(report_json, workspace),
            "generation_markdown": _artifact(report_markdown, workspace),
        },
        "counts": report["counts"],
    }
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return output_path
