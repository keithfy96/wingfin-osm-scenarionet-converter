from osm_scenario.ids import deterministic_id


def test_deterministic_id_is_stable_and_namespaced() -> None:
    assert deterministic_id("lane", 1, 2) == deterministic_id("lane", 1, 2)
    assert deterministic_id("lane", 1, 2) != deterministic_id("signal", 1, 2)
