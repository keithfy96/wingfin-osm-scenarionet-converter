"""Versioned, JSON-safe models for generated lane geometry."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Point2D(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x: float
    y: float


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


class SignalAssociation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    identifier: str
    source_node_id: str
    lane_ids: list[str]
    status: Literal["mapped", "review_required"]


class RestrictionEffect(BaseModel):
    model_config = ConfigDict(extra="forbid")

    identifier: str
    source_relation_id: str
    restriction: str
    from_way_ids: list[str]
    via_member_ids: list[str]
    to_way_ids: list[str]
    status: Literal["review_required"] = "review_required"


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
    connectors: list[LaneFeature] = Field(default_factory=list)
    signals: list[SignalAssociation] = Field(default_factory=list)
    restrictions: list[RestrictionEffect] = Field(default_factory=list)
    findings: list[ReviewFinding] = Field(default_factory=list)
