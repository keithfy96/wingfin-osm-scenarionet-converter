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


class LaneSelectionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Smallest turn angle that makes a movement a side movement rather than a straight
    # continuation. Must stay below the 35 degree `through` band in `classify_movement`,
    # or no movement classified `through` could ever be treated as a turn.
    side_movement_min_degrees: float = Field(default=10.0, gt=0, lt=35)


class ConverterConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    config_version: Literal[1]
    driving_side: Literal["left", "right"] | None = None
    coordinate_origin: CoordinateOrigin | None = None
    coordinate_round_trip_tolerance_degrees: float = Field(default=1e-9, gt=0)
    lane_width_defaults: LaneWidthDefaults = Field(default_factory=LaneWidthDefaults)
    default_speed_kph: float = Field(default=50.0, gt=0)
    speed_defaults_kph: dict[str, float] = Field(
        default_factory=lambda: {
            "living_street": 20.0,
            "motorway": 110.0,
            "motorway_link": 60.0,
            "residential": 50.0,
            "service": 30.0,
        }
    )
    tag_inference: TagInferenceConfig = Field(default_factory=TagInferenceConfig)
    lane_selection: LaneSelectionConfig = Field(default_factory=LaneSelectionConfig)


def load_config(path: Path) -> ConverterConfig:
    """Load and validate a versioned YAML configuration."""
    with path.open(encoding="utf-8") as config_file:
        return ConverterConfig.model_validate(yaml.safe_load(config_file))
