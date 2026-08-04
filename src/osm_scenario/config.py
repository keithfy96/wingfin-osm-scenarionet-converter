"""Versioned converter configuration models."""

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field


class CoordinateOrigin(BaseModel):
    model_config = ConfigDict(extra="forbid")

    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class LaneWidthDefaults(BaseModel):
    model_config = ConfigDict(extra="forbid")

    vehicle: float = Field(default=3.5, gt=0)


class TagInferenceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    infer_missing_lane_count: bool = True


class ConverterConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    config_version: Literal[1]
    driving_side: Literal["left", "right"] | None = None
    coordinate_origin: CoordinateOrigin | None = None
    coordinate_round_trip_tolerance_degrees: float = Field(default=1e-9, gt=0)
    lane_width_defaults: LaneWidthDefaults = Field(default_factory=LaneWidthDefaults)
    tag_inference: TagInferenceConfig = Field(default_factory=TagInferenceConfig)


def load_config(path: Path) -> ConverterConfig:
    """Load and validate a versioned YAML configuration."""
    with path.open(encoding="utf-8") as config_file:
        return ConverterConfig.model_validate(yaml.safe_load(config_file))
