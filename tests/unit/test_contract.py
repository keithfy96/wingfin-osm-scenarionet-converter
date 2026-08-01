from osm_scenario.contract import REQUIRED_SCENARIO_KEYS, ScenarioDescriptionV1


def test_contract_requires_all_top_level_sections() -> None:
    scenario = ScenarioDescriptionV1(
        id="fixture",
        version="1.0",
        length=1,
        metadata={},
        tracks={},
        dynamic_map_states={},
        map_features={},
    )

    assert set(scenario.model_dump()) == REQUIRED_SCENARIO_KEYS
