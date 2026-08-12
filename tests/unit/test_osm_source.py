from __future__ import annotations

from pathlib import Path

import osmnx as ox
import pytest

from osm_scenario.acquisition import _configure_preserved_tags
from osm_scenario.osm_source import (
    read_osm_snapshot,
    select_public_driving_graph,
    single_lane_implies_oneway,
)

SINGLE_LANE = Path(__file__).parents[1] / "fixtures" / "osm" / "single-lane.osm"


@pytest.mark.parametrize(
    "tags",
    [
        {"lanes": "1"},
        {"lanes": "1", "highway": "tertiary", "turn:lanes:forward": "right"},
    ],
)
def test_a_bare_single_lane_way_is_read_as_one_way(tags: dict[str, str]) -> None:
    assert single_lane_implies_oneway(tags)


@pytest.mark.parametrize(
    ("tags", "why"),
    [
        ({"lanes": "1", "oneway": "no"}, "an explicit oneway=no is surveyed evidence"),
        ({"lanes": "1", "oneway": "false"}, "so is any spelling of it"),
        ({"lanes": "1", "lanes:backward": "1"}, "a directional count names the direction"),
        ({"lanes": "1", "lanes:forward": "1"}, "and so does the other one"),
        ({"lanes": "2"}, "two lanes can be one each way"),
        ({"lanes": "1", "oneway": "yes"}, "already one-way, nothing to infer"),
        ({"lanes": "1", "junction": "roundabout"}, "a roundabout is already one-way"),
        ({"highway": "residential"}, "no lane count at all"),
    ],
)
def test_a_surveyed_tag_switches_the_inference_off(tags: dict[str, str], why: str) -> None:
    assert not single_lane_implies_oneway(tags), why


def _selected(path: Path):
    _configure_preserved_tags()
    graph = ox.graph_from_xml(path, simplify=False, retain_all=True)
    snapshot = read_osm_snapshot(path)
    return select_public_driving_graph(graph, snapshot)


def _by_id(entries: list[dict]) -> dict[str, dict]:
    return {entry["osm_id"]: entry for entry in entries}


def test_the_guard_applies_the_reading_only_where_a_way_out_survives() -> None:
    _, audit = _selected(SINGLE_LANE)
    report = audit["single_lane_oneway"]

    # 200 is a second route between two nodes the spine already joins, so nothing needs
    # its reverse. 600 is one of two links to node 20, so the other still gets you back.
    assert sorted(_by_id(report["applied"])) == ["200", "600"]
    # 300 ends at a dead end, and 700 is the last link off node 20 once 600 is one-way.
    assert sorted(_by_id(report["blocked"])) == ["300", "700"]
    assert _by_id(report["blocked"])["300"]["would_strand"] == ["9"]
    # 21 is named too, and has to be: once 600 runs 3 to 20 only, its exit is node 20,
    # which the drop would leave with none. The guard reports what the drop costs the
    # network, not what it costs the way it is looking at.
    assert set(_by_id(report["blocked"])["700"]["would_strand"]) == {"20", "21", "22"}


def test_a_way_the_guard_refused_keeps_both_directions() -> None:
    graph, _ = _selected(SINGLE_LANE)
    directions = {
        (str(u), str(v))
        for u, v, data in graph.edges(data=True)
        if "300" in str(data.get("osmid"))
    }
    assert ("2", "9") in directions
    assert ("9", "2") in directions, "the spur must still be drivable in both directions"


def test_a_way_the_guard_applied_runs_one_way_only() -> None:
    graph, _ = _selected(SINGLE_LANE)
    directions = {
        (str(u), str(v))
        for u, v, data in graph.edges(data=True)
        if "200" in str(data.get("osmid"))
    }
    assert directions == {("1", "4"), ("4", "3")}


def test_the_selection_audit_still_passes_after_the_drop() -> None:
    """`_expected_directions` has to learn the same rule or every applied way is an error."""
    _, audit = _selected(SINGLE_LANE)
    assert audit["errors"] == []
    assert audit["status"] == "passed"
    assert audit["direction_mismatches"] == []


def test_the_guard_leaves_a_map_with_nothing_to_infer_untouched() -> None:
    fixture = Path(__file__).parents[1] / "fixtures" / "osm" / "junction.osm"
    graph, audit = _selected(fixture)
    assert audit["single_lane_oneway"] == {"applied": [], "blocked": []}
    assert audit["status"] == "passed"
