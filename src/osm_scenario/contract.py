"""Locally owned scenario compatibility contract."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

SCENARIO_CONTRACT_VERSION = 1
REQUIRED_SCENARIO_KEYS = frozenset(
    {"id", "version", "length", "metadata", "tracks", "dynamic_map_states", "map_features"}
)


class ScenarioDescriptionV1(BaseModel):
    """Top-level structure owned by compatibility contract version 1."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    length: int = Field(gt=0)
    metadata: dict[str, Any]
    tracks: dict[str, dict[str, Any]]
    dynamic_map_states: dict[str, dict[str, Any]]
    map_features: dict[str, dict[str, Any]]


class ScenarioContractManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal[1] = SCENARIO_CONTRACT_VERSION
    format: Literal["metadrive-scenario-description"]
