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

    # Turn angle at which a non-reverse movement stops being self-evident. Beyond this
    # the movement sends a driver back the way they came, so it needs the same positive
    # `turn:lanes` evidence a U-turn does. Must stay below the 145 degree `reverse` band,
    # which the U-turn policy already governs.
    sharp_movement_review_degrees: float = Field(default=130.0, gt=70, lt=145)


class LaneGeometryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # A merging or diverging lane is drawn reaching the lane it joins rather than
    # stopping on the other road's centreline, where OSM ends its way. This is the
    # distance over which that lateral correction is blended in, so the lane bends
    # instead of kinking. It is clamped to the lane's own length.
    merge_taper_length_m: float = Field(default=30.0, gt=0)

    # Gaps below this are the ordinary half-lane offset between two blocks and are
    # left to the connector; only a real merge is worth bending a lane for.
    merge_taper_min_gap_m: float = Field(default=0.5, gt=0)


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
    lane_geometry: LaneGeometryConfig = Field(default_factory=LaneGeometryConfig)


def load_config(path: Path) -> ConverterConfig:
    """Load and validate a versioned YAML configuration."""
    with path.open(encoding="utf-8") as config_file:
        return ConverterConfig.model_validate(yaml.safe_load(config_file))
