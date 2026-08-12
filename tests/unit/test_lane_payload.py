"""The payload both Stage 6 pages draw from.

There is one assertion here worth more than the rest: that the pages are *handed* which
steps cross a junction rather than working it out. They used to work it out, from whether a
connector existed, and that is not the same question - a road running straight through a
junction has no connector and crosses one anyway. The browser refused those drives while
the converter built them, and nothing failed; the page simply said no.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from osm_scenario.conversion import _lane_change_moves, _lane_neighbours
from osm_scenario.ego_route import junction_crossings
from osm_scenario.lane_model import PreliminaryLaneModel
from osm_scenario.lane_payload import build_lane_payload, embed

_WORKSPACE = Path(__file__).resolve().parents[2] / "workspaces/junction-1/lane-model/reviewed.json"


def _payload() -> tuple[PreliminaryLaneModel, dict]:
    if not _WORKSPACE.exists():
        pytest.skip("workspaces/junction-1 is gitignored and not present")
    model = PreliminaryLaneModel.model_validate(json.loads(_WORKSPACE.read_text()))
    payload = build_lane_payload(
        model=model, neighbours=_lane_neighbours(model), moves=_lane_change_moves(model)
    )
    return model, payload


def test_the_payload_carries_the_crossings_ego_route_decided() -> None:
    """Equal to `junction_crossings`, not merely overlapping it.

    The page judges a step against `MAX_CROSSING_M` or `MAX_JOIN_M` on this answer, and the
    converter judges the same step on `junction_crossings`. If the two ever part, the page
    offers drives the converter refuses, or refuses drives it would build - which is the
    defect this field exists to close.
    """
    model, payload = _payload()
    carried = {(before, after) for before, after in payload["crossings"]}
    assert carried == junction_crossings(model)


def test_the_crossings_are_more_than_the_connectors_the_page_can_see() -> None:
    """Otherwise the field would be redundant and the old behaviour would have been right."""
    _, payload = _payload()
    connectors = {(entry["from"], entry["to"]) for entry in payload["connectors"]}
    carried = {(before, after) for before, after in payload["crossings"]}
    assert connectors <= carried
    assert carried - connectors


def test_every_crossing_names_lanes_the_payload_actually_contains() -> None:
    """A pair keyed on something the page has no lane for is silently ignored by it."""
    _, payload = _payload()
    known = {lane["id"] for lane in payload["lanes"]}
    missing = [
        pair for pair in payload["crossings"] if pair[0] not in known or pair[1] not in known
    ]
    assert not missing, f"{len(missing)} crossings name lanes not in the payload: {missing[:5]}"


def test_the_payload_survives_being_embedded_in_a_script_tag() -> None:
    """`crossings` is a list of lists on purpose: a tuple is not JSON, and a set is not either."""
    _, payload = _payload()
    text = embed(payload)
    assert "</" not in text
    assert json.loads(text.replace("<\\/", "</"))["crossings"] == payload["crossings"]
