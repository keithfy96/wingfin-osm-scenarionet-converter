"""The step budget `drive.py` gives a drive, and the terms that make it.

A drive is bounded because a car that stalls never terminates: `terminated` and `truncated`
both stay false, the episode is still live, and the loop would step to MetaDrive's `horizon` of
100000. The bound is not a schedule, though - the loop ends on `arrive_dest` - so it only ever
costs a drive that was going to fail anyway, and being too *tight* is the expensive mistake.

It was too tight. The budget carried a named term for the IDM tracking deficit and one for the
longest red, and none at all for queueing behind other cars, which is the one thing
`--traffic live` exists to create. Measured on `junction-1` at 10 Hz under `--agent-policy idm`,
each run driven to `arrive_dest` with the bound raised by hand:

    no traffic                              412 steps      budget 424   arrives
    --traffic live --traffic-count 25       645 steps      budget 424   cut off at 0.415
    --traffic-count 50                      656 steps      budget 424   cut off
    --traffic-count 25 --traffic-seed 3     412 steps      budget 424   arrives

Doubling the cars added 11 steps; changing the seed removed all 233. The delay is
seed-dependent rather than count-dependent, so no factor can be right for every run and
`--extra-seconds` has to exist beside whatever the default is.

What is pinned here is `_step_budget`, because that is the part with judgement in it, plus the
message that reads the result back. The loop wiring is not testable without MetaDrive and is
verified by driving; see `docs/reference/running-the-simulator.md`.
"""

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))


@pytest.fixture
def drive():
    return pytest.importorskip("drive")


# `junction-1`'s route 1 at 10 Hz, the numbers every measurement above was taken on.
JUNCTION_1 = dict(recorded_s=37.9, pace_s=36.04, red_s=0.0, sim_dt=0.1)


# --- the drive term --------------------------------------------------------------------------


def test_a_paced_car_gets_its_own_duration_inflated_by_the_tracking_ratio(drive):
    """424 is the number junction-1 has been driven against all along: the recording is 379
    frames and the pace term wins, which is why calling 424 "the recording" was wrong."""
    budget, parts = drive._step_budget(**JUNCTION_1, extra_s=0.0, traffic=False)
    assert budget == 424
    assert parts == [("the drive itself", 424)]


def test_traffic_doubles_the_drive_term_and_covers_the_measured_645(drive):
    budget, parts = drive._step_budget(**JUNCTION_1, extra_s=0.0, traffic=True)
    assert budget == 848
    assert budget > 656  # the longest drive measured, --traffic-count 50 --traffic-seed 0
    assert parts == [("the drive itself, x2 for --traffic live", 848)]


def test_a_replayed_ego_is_the_recording_even_with_traffic_on(drive):
    """A replayed car's position is set frame by frame, so nothing on the road can delay it.
    Neither the tracking deficit nor the traffic factor may reach it - `pace_s` is None."""
    plain = drive._step_budget(
        recorded_s=37.9, pace_s=None, red_s=0.0, extra_s=0.0, traffic=False, sim_dt=0.1
    )
    with_traffic = drive._step_budget(
        recorded_s=37.9, pace_s=None, red_s=0.0, extra_s=0.0, traffic=True, sim_dt=0.1
    )
    assert plain == with_traffic == (379, [("the recording", 379)])


def test_the_recording_wins_when_it_is_the_longer_of_the_two(drive):
    budget, parts = drive._step_budget(
        recorded_s=96.0, pace_s=36.04, red_s=0.0, extra_s=0.0, traffic=False, sim_dt=0.1
    )
    assert (budget, parts) == (960, [("the recording", 960)])


# --- the terms on top ------------------------------------------------------------------------


def test_the_longest_red_still_adds_on_top(drive):
    budget, parts = drive._step_budget(
        recorded_s=37.9, pace_s=36.04, red_s=22.0, extra_s=0.0, traffic=False, sim_dt=0.1
    )
    assert budget == 424 + 220
    assert parts == [("the drive itself", 424), ("a red", 220)]


def test_extra_seconds_is_counted_on_the_sim_clock_not_the_data_clock(drive):
    """The trap the whole signature exists to avoid. Ten seconds is a hundred steps at 10 Hz
    and a **thousand** at 100 - `_longest_red` records what happened the last time a seconds
    figure was divided by the wrong one of the two clocks."""
    at_10hz, _ = drive._step_budget(**JUNCTION_1, extra_s=10.0, traffic=False)
    assert at_10hz == 424 + 100

    at_100hz, parts = drive._step_budget(
        recorded_s=37.9, pace_s=36.04, red_s=0.0, extra_s=10.0, traffic=False, sim_dt=0.01
    )
    assert at_100hz == 4240 + 1000
    assert parts[-1] == ("--extra-seconds", 1000)


def test_a_term_worth_nothing_is_left_out_rather_than_printed_as_zero(drive):
    _budget, parts = drive._step_budget(**JUNCTION_1, extra_s=0.0, traffic=False)
    assert [name for name, _ in parts] == ["the drive itself"]


def test_the_terms_are_ordered_drive_then_red_then_flag(drive):
    _budget, parts = drive._step_budget(
        recorded_s=37.9, pace_s=36.04, red_s=22.0, extra_s=10.0, traffic=True, sim_dt=0.1
    )
    assert [name for name, _ in parts] == [
        "the drive itself, x2 for --traffic live",
        "a red",
        "--extra-seconds",
    ]


# --- reading the result back ------------------------------------------------------------------


def test_the_failure_names_the_flag_that_raises_the_budget(drive):
    budget, parts = drive._step_budget(**JUNCTION_1, extra_s=0.0, traffic=True)
    reason = drive._budget_reason(budget, parts)
    assert "848 for the drive itself, x2 for --traffic live" in reason
    assert "--extra-seconds" in reason


def test_several_terms_are_shown_summing_to_the_budget(drive):
    budget, parts = drive._step_budget(
        recorded_s=37.9, pace_s=36.04, red_s=22.0, extra_s=10.0, traffic=False, sim_dt=0.1
    )
    reason = drive._budget_reason(budget, parts)
    assert "744 = 424 for the drive itself + 220 for a red + 100 for --extra-seconds" in reason


def test_the_message_no_longer_blames_a_recording_that_did_not_end_it(drive):
    """The old branch printed "ran out of recorded steps" whenever the red allowance was zero,
    so a junction-1 drive cut off at 424 was told it had exhausted a 379-frame recording."""
    budget, parts = drive._step_budget(**JUNCTION_1, extra_s=0.0, traffic=False)
    assert "recorded" not in drive._budget_reason(budget, parts)


# --- what ends when the drive outlives the tape -----------------------------------------------


def scenario(walkers=0, lights=False):
    tracks = {"ego": {"type": "VEHICLE"}}
    for index in range(walkers):
        tracks[f"walk-{index}"] = {"type": "PEDESTRIAN" if index % 2 else "CYCLIST"}
    tracks["cone-1"] = {"type": "TRAFFIC_CONE"}
    return {
        "tracks": tracks,
        "dynamic_map_states": {"light-1": {}} if lights else {},
        "metadata": {"sdc_id": "ego"},
    }


def test_a_drive_inside_the_recording_says_nothing(drive):
    assert (
        drive._tape_ran_out(
            scenario(walkers=4), steps=364, recorded_steps=379, length=379, lights="tape"
        )
        is None
    )


def test_the_few_steps_every_paced_drive_outruns_the_tape_by_are_not_worth_a_line(drive):
    """junction-1 free-flow: 412 steps against a 379-frame tape, 8% over, all of it after the
    car has arrived. A note here would appear on every `--agent-policy idm` drive ever run."""
    assert (
        drive._tape_ran_out(
            scenario(walkers=4), steps=412, recorded_steps=379, length=379, lights="tape"
        )
        is None
    )


def test_walkers_removed_past_the_tape_are_named_and_statics_are_not(drive):
    note = drive._tape_ran_out(
        scenario(walkers=4), steps=645, recorded_steps=379, length=379, lights="live"
    )
    assert "645 steps against a 379-frame recording" in note
    assert "4 recorded pedestrian(s) and cyclist(s) were removed" in note
    assert "cones and barriers stay" in note
    assert "--lights tape" not in note


def test_a_frozen_tape_light_is_named_only_under_lights_tape(drive):
    on_tape = drive._tape_ran_out(
        scenario(lights=True), steps=645, recorded_steps=379, length=379, lights="tape"
    )
    assert "froze on its last colour" in on_tape
    assert (
        drive._tape_ran_out(
            scenario(lights=True), steps=645, recorded_steps=379, length=379, lights="live"
        )
        is None
    )


def test_a_scenario_with_nothing_on_the_tape_says_nothing_however_long_the_drive(drive):
    assert (
        drive._tape_ran_out(scenario(), steps=9000, recorded_steps=379, length=379, lights="tape")
        is None
    )
