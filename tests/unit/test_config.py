from pathlib import Path

from osm_scenario.config import load_config


def test_default_configuration_loads() -> None:
    config = load_config(Path("config/default.yaml"))

    assert config.config_version == 1
    assert config.coordinate_round_trip_tolerance_degrees == 1e-9
    assert config.lane_width_defaults.vehicle == 3.5
