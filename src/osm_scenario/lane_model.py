"""Versioned, JSON-safe models for generated lane geometry."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Point2D(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x: float
    y: float


class GeoPoint(BaseModel):
    """A WGS84 position.

    Named keys rather than a `[lon, lat]` pair: these are joined against GPS tracks
    from drive footage, where a silent transposition is both easy and expensive.
    Distinct from :class:`Point2D`, which is the projected CRS the geometry uses.
    """

    model_config = ConfigDict(extra="forbid")

    lat: float
    lon: float


class FindingSource(BaseModel):
    """One OSM feature a finding came from, with its geometry copied in.

    Copied rather than referenced by id so a single finding is enough to place it on
    a map, at the cost of repeating a way's nodes across the findings that share it.
    """

    model_config = ConfigDict(extra="forbid")

    ref: str
    coordinates: list[GeoPoint]


class FindingLocation(BaseModel):
    """Where a finding is, for matching against externally recorded positions."""

    model_config = ConfigDict(extra="forbid")

    coordinate_system: Literal["EPSG:4326"] = "EPSG:4326"
    lat: float
    lon: float
    # [min_lon, min_lat, max_lon, max_lat]. A way-scoped finding covers a length of
    # road, so its extent has to be stated rather than implied by the point.
    bbox: list[float]
    sources: list[FindingSource]


class LaneBoundary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    identifier: str
    side: Literal["left", "right"]
    boundary_type: Literal["unknown"] = "unknown"
    points: list[Point2D]


class LaneFeature(BaseModel):
    model_config = ConfigDict(extra="forbid")

    identifier: str
    source_way_ids: list[str]
    source_edge: list[str]
    lane_index: int = Field(ge=0)
    lane_count: int = Field(gt=0)
    direction: Literal["forward", "backward"]
    road_class: str
    width_m: float = Field(gt=0)
    speed_limit_kph: float = Field(gt=0)
    centerline: list[Point2D]
    polygon: list[Point2D]
    boundaries: list[LaneBoundary]
    entry_lanes: list[str] = Field(default_factory=list)
    exit_lanes: list[str] = Field(default_factory=list)
    left_neighbor: str | None = None
    right_neighbor: str | None = None
    turn_permissions: list[str] = Field(default_factory=list)


class ConnectorFeature(BaseModel):
    model_config = ConfigDict(extra="forbid")

    identifier: str
    junction_node_id: str
    from_lane_id: str
    to_lane_id: str
    from_way_id: str
    to_way_id: str
    movement: Literal["reverse", "left", "slight_left", "through", "slight_right", "right"]
    turn_angle_degrees: float
    status: Literal["active", "forbidden", "review_required"]
    centerline: list[Point2D]
    polygon: list[Point2D]


class SignalAssociation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    identifier: str
    source_node_id: str
    lane_ids: list[str]
    status: Literal["mapped", "review_required"]


class StopLine(BaseModel):
    model_config = ConfigDict(extra="forbid")

    identifier: str
    source_node_id: str
    lane_ids: list[str]
    points: list[Point2D]
    source: Literal["explicit", "inferred"]
    status: Literal["mapped", "review_required"]


class RestrictionEffect(BaseModel):
    model_config = ConfigDict(extra="forbid")

    identifier: str
    source_relation_id: str
    restriction: str
    from_way_ids: list[str]
    via_member_ids: list[str]
    to_way_ids: list[str]
    status: Literal["enforced", "already_satisfied", "review_required"]
    forbidden_connector_ids: list[str] = Field(default_factory=list)
    reason: str


class ReviewFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    identifier: str
    rule: str
    severity: Literal["warning", "blocker"]
    source_type: Literal["way", "node", "relation", "edge"]
    source_ids: list[str]
    affected_feature_ids: list[str]
    proposed_value: object
    confidence: Literal["high", "medium", "low"]
    reason: str
    evidence_checksum: str
    # Assigned after `evidence_checksum` is computed, and deliberately absent from it:
    # location is derived from `source_ids`, which the checksum already covers, so a
    # decision made before this field existed stays valid. `None` when `source_type`
    # is `edge`, whose ids name graph edges with no OSM geometry to resolve.
    location: FindingLocation | None = None


class GenerationMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    generator_version: str
    lane_model_schema_version: int
    source_checksum: str
    projected_graph_checksum: str
    configuration_checksum: str
    generation_fingerprint: str
    coordinate_system_wkt: str


class PreliminaryLaneModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metadata: GenerationMetadata
    lanes: list[LaneFeature]
    connectors: list[ConnectorFeature] = Field(default_factory=list)
    signals: list[SignalAssociation] = Field(default_factory=list)
    stop_lines: list[StopLine] = Field(default_factory=list)
    restrictions: list[RestrictionEffect] = Field(default_factory=list)
    findings: list[ReviewFinding] = Field(default_factory=list)
