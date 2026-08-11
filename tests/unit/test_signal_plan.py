"""Reading a signal plan, and expanding it into the tape MetaDrive replays.

The cases in `colour_at` are the same numbers `web/test/signal/phase.test.ts` asserts on the
browser side. That duplication is deliberate: the page is where a plan is judged and this is
what writes it, so a disagreement about the sign of the offset would draw a green light on
screen and put a red one in the pickle.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest

from osm_scenario.lane_model import LaneFeature, Point2D, PreliminaryLaneModel
from osm_scenario.signal_plan import (
    LIGHT_GREEN,
    LIGHT_RED,
    LIGHT_YELLOW,
    SIGNALS_VERSION,
    PhaseGroup,
    SignalPlan,
    SignalPlanError,
    colour_at,
    light_states,
    plan_metadata,
    read_signal_plan,
)

WIDTH = 4.0

IDENTITY = {
    "generation_fingerprint": "fingerprint",
    "reviewed_lane_model_sha256": "model-sha",
}

_METADATA = {
    "generator_version": "test",
    "lane_model_schema_version": 1,
    "source_checksum": "source",
    "projected_graph_checksum": "graph",
    "configuration_checksum": "config",
    "generation_fingerprint": "fingerprint",
    "coordinate_system_wkt": "EPSG:4326",
}


def _lane(identifier: str, *, x0: float = 0.0, x1: float = 50.0) -> LaneFeature:
    half = WIDTH / 2
    return LaneFeature(
        identifier=identifier,
        source_way_ids=["200"],
        source_edge=["1", "2", "0"],
        lane_index=0,
        lane_count=1,
        direction="forward",
        road_class="residential",
        width_m=WIDTH,
        speed_limit_kph=50.0,
        centerline=[Point2D(x=x0, y=0.0), Point2D(x=x1, y=0.0)],
        polygon=[
            Point2D(x=x0, y=-half),
            Point2D(x=x1, y=-half),
            Point2D(x=x1, y=half),
            Point2D(x=x0, y=half),
            Point2D(x=x0, y=-half),
        ],
        boundaries=[],
    )


def _model() -> PreliminaryLaneModel:
    return PreliminaryLaneModel.model_validate(
        {
            "metadata": _METADATA,
            "lanes": [
                _lane("a", x0=0.0, x1=50.0).model_dump(),
                _lane("b", x0=60.0, x1=110.0).model_dump(),
            ],
            "connectors": [],
        }
    )


def _raw(**update: Any) -> dict[str, Any]:
    plan: dict[str, Any] = {
        "signals_version": SIGNALS_VERSION,
        "identity": dict(IDENTITY),
        "cycle_seconds": 60,
        "groups": [
            {
                "name": "phase-a",
                "lanes": ["a"],
                "green_seconds": 27,
                "yellow_seconds": 3,
                "offset_seconds": 0,
            }
        ],
    }
    plan.update(update)
    return plan


def _read(**update: Any) -> SignalPlan:
    return read_signal_plan(
        _raw(**update), model=_model(), model_sha256="model-sha", source="signals.json"
    )


# --- the clock ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (0.0, LIGHT_GREEN),
        (26.9, LIGHT_GREEN),
        (27.0, LIGHT_YELLOW),
        (29.9, LIGHT_YELLOW),
        (30.0, LIGHT_RED),
        (59.9, LIGHT_RED),
        # Wraps, so an episode longer than one cycle keeps cycling.
        (60.0, LIGHT_GREEN),
        (87.0, LIGHT_YELLOW),
    ],
)
def test_colour_at_runs_green_then_yellow_then_red(seconds: float, expected: str) -> None:
    assert (
        colour_at(
            seconds=seconds,
            cycle_seconds=60.0,
            green_seconds=27.0,
            yellow_seconds=3.0,
            offset_seconds=0.0,
        )
        == expected
    )


def test_the_offset_is_when_green_starts() -> None:
    """The one error that would still look plausible on screen, so it is pinned both sides.

    `web/src/signal/conflicts.ts` measures how long two groups are green together by treating
    the offset as the start of the green window. Reading it the other way here would put a
    red light in the pickle where the page drew a green one.
    """
    kwargs = {"cycle_seconds": 60.0, "green_seconds": 27.0, "yellow_seconds": 3.0}
    assert colour_at(seconds=29.9, offset_seconds=30.0, **kwargs) == LIGHT_RED
    assert colour_at(seconds=30.0, offset_seconds=30.0, **kwargs) == LIGHT_GREEN
    assert colour_at(seconds=57.0, offset_seconds=30.0, **kwargs) == LIGHT_YELLOW


def test_a_green_filling_the_cycle_never_goes_red() -> None:
    for seconds in (0.0, 15.0, 30.0, 59.9):
        assert (
            colour_at(
                seconds=seconds,
                cycle_seconds=60.0,
                green_seconds=60.0,
                yellow_seconds=0.0,
                offset_seconds=0.0,
            )
            == LIGHT_GREEN
        )


def test_no_green_at_all_is_a_permanent_red_rather_than_an_error() -> None:
    """A stop line with no phase is a legitimate thing to ask for, and red is the safe
    reading of it. Refusing would make the only way to say it a 0.1 s green."""
    assert (
        colour_at(
            seconds=30.0,
            cycle_seconds=60.0,
            green_seconds=0.0,
            yellow_seconds=0.0,
            offset_seconds=0.0,
        )
        == LIGHT_RED
    )


# --- reading a plan -------------------------------------------------------------------------


def test_a_well_formed_plan_reads_back() -> None:
    plan = _read()
    assert plan.cycle_seconds == 60.0
    assert plan.groups == (
        PhaseGroup(
            name="phase-a",
            lanes=("a",),
            green_seconds=27.0,
            yellow_seconds=3.0,
            offset_seconds=0.0,
        ),
    )
    assert plan.lanes == ("a",)


def test_an_offset_past_the_cycle_is_normalised_rather_than_refused() -> None:
    """So two plans that differ only by a whole cycle produce the same tape, and so the live
    controller can add its own offset without compounding."""
    groups = _raw()["groups"]
    groups[0]["offset_seconds"] = 90
    assert _read(groups=groups).groups[0].offset_seconds == 30.0


def test_a_plan_drawn_on_a_different_generation_is_refused() -> None:
    identity = {**IDENTITY, "generation_fingerprint": "somewhere-else"}
    with pytest.raises(SignalPlanError, match="different lane model"):
        _read(identity=identity)


def test_a_plan_drawn_before_the_model_was_re_reviewed_is_refused() -> None:
    identity = {**IDENTITY, "reviewed_lane_model_sha256": "other"}
    with pytest.raises(SignalPlanError, match="different lane model"):
        _read(identity=identity)


def test_a_version_this_converter_does_not_read_is_refused() -> None:
    with pytest.raises(SignalPlanError, match="signals_version"):
        _read(signals_version=SIGNALS_VERSION + 1)


def test_a_plan_with_no_identity_block_is_refused() -> None:
    raw = _raw()
    del raw["identity"]
    with pytest.raises(SignalPlanError, match="identity"):
        read_signal_plan(raw, model=_model(), model_sha256="model-sha", source="signals.json")


def test_a_lane_that_is_not_on_this_map_is_refused() -> None:
    """The failure this prevents is silent: MetaDrive's `skip_missing_light` defaults to
    True, so a light keyed on a lane that does not exist is dropped with a log line."""
    groups = _raw()["groups"]
    groups[0]["lanes"] = ["nowhere"]
    with pytest.raises(SignalPlanError, match="not a lane in this model"):
        _read(groups=groups)


def test_one_lane_in_two_groups_is_refused() -> None:
    """Two lights on one key: the second would silently win, and no controller could carry
    out a lane that is green and red at once."""
    groups = _raw()["groups"] + [
        {
            "name": "phase-b",
            "lanes": ["a"],
            "green_seconds": 27,
            "yellow_seconds": 3,
            "offset_seconds": 30,
        }
    ]
    with pytest.raises(SignalPlanError, match="only show one colour"):
        _read(groups=groups)


def test_two_groups_with_the_same_name_are_refused() -> None:
    second = dict(_raw()["groups"][0], lanes=["b"])
    with pytest.raises(SignalPlanError, match="twice"):
        _read(groups=[_raw()["groups"][0], second])


def test_green_plus_yellow_overrunning_the_cycle_is_refused() -> None:
    groups = _raw()["groups"]
    groups[0]["green_seconds"] = 58
    with pytest.raises(SignalPlanError, match="longer than"):
        _read(groups=groups)


def test_a_cycle_of_zero_is_refused_before_anything_divides_by_it() -> None:
    with pytest.raises(SignalPlanError, match="cycle_seconds"):
        _read(cycle_seconds=0)


def test_a_group_with_no_lanes_is_refused() -> None:
    groups = _raw()["groups"]
    groups[0]["lanes"] = []
    with pytest.raises(SignalPlanError, match="no lanes"):
        _read(groups=groups)


def test_a_plan_with_no_groups_is_refused() -> None:
    with pytest.raises(SignalPlanError, match="no phase groups"):
        _read(groups=[])


# --- the tape -------------------------------------------------------------------------------


def test_the_tape_is_one_colour_per_tenth_of_a_second() -> None:
    """MetaDrive steps at 0.1 s and `after_step` indexes by `episode_step`, so the tape has
    to be sampled at exactly that interval or the plan runs at the wrong speed."""
    states = light_states(_read(), model=_model(), steps=601)
    tape = states["a"]["state"]["object_state"]
    assert len(tape) == 601
    assert tape[0] == LIGHT_GREEN
    assert tape[269] == LIGHT_GREEN
    assert tape[270] == LIGHT_YELLOW
    assert tape[300] == LIGHT_RED
    assert tape[600] == LIGHT_GREEN


def test_each_lane_gets_its_own_tape_rather_than_a_shared_one() -> None:
    groups = _raw()["groups"]
    groups[0]["lanes"] = ["a", "b"]
    states = light_states(_read(groups=groups), model=_model(), steps=10)
    assert states["a"]["state"]["object_state"] == states["b"]["state"]["object_state"]
    assert states["a"]["state"]["object_state"] is not states["b"]["state"]["object_state"]


def test_the_stop_point_is_a_float32_triple_outside_state() -> None:
    light = light_states(_read(), model=_model(), steps=10)["a"]
    assert "stop_point" not in light["state"]
    assert isinstance(light["stop_point"], np.ndarray)
    assert light["stop_point"].dtype == np.float32
    assert light["stop_point"].tolist() == pytest.approx([50.0, 0.0, 0.0])


def test_the_metadata_names_the_group_a_light_belongs_to() -> None:
    light = light_states(_read(), model=_model(), steps=10)["a"]
    assert light["metadata"]["phase_group"] == "phase-a"
    assert light["metadata"]["track_length"] == 10


# --- what a controller reads ------------------------------------------------------------------


def test_plan_metadata_carries_every_number_needed_to_rebuild_the_tape() -> None:
    """`tools/signal_control.py` re-derives the colours per episode from this and nothing
    else, so anything missing here is a light it cannot drive."""
    metadata = plan_metadata(_read(), model=_model())
    assert metadata["source"] == "synthesised"
    assert metadata["cycle_seconds"] == 60.0
    assert metadata["time_step_s"] == 0.1
    group = metadata["groups"][0]
    assert group == {
        "name": "phase-a",
        "green_seconds": 27.0,
        "yellow_seconds": 3.0,
        "offset_seconds": 0.0,
        "red_seconds": 30.0,
        "lanes": [{"lane_id": "a", "stop_point": [50.0, 0.0, 0.0]}],
    }


def test_plan_metadata_holds_no_arrays() -> None:
    """It travels in `dataset_summary.pkl` and into the JSON conversion report, and it is
    read under MetaDrive's numpy 1 where an array written by numpy 2 would not open."""
    metadata = plan_metadata(_read(), model=_model())

    def walk(value: Any) -> None:
        assert not isinstance(value, np.ndarray)
        if isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(metadata)
