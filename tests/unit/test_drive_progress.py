"""The heartbeat `drive.py` prints while a drive is running, and the two shapes it has.

A drive used to print nothing between `ego starts at ...` and its scenario summary. On a
replayed `junction-1` that silence is four seconds; under the AV3 model on the rig it is hours,
because a model drive has **no step budget** -- `--agent-policy remote` sets `budget = None`
deliberately, the tape's length being no bound on a car that is not following the tape, so the
drive ends only when the episode does or at MetaDrive's `horizon` of 100000. A route that
completes is about 758 decisions at `--decision-hz 20`; one that runs to the horizon is 20000,
which is five and a half hours. **A finished drive, a slow drive and a hung socket all look
identical from a terminal**, which is the thing this line exists to fix.

What is pinned here is the formatting, because that is the part with judgement in it:

1. **No ETA when there is no budget.** The route fraction is the only progress a self-driving
   car has, and extrapolating it would put a confident figure on a car that may be circling.
2. **Precision follows magnitude.** 1.0 ms/step at replay speed and 205 at model speed are the
   same field; a fixed precision is unreadable at one end. A local stub answering in 0.4 ms
   rendered by a bare integer reads as "0 ms", which reads as broken.
3. **The units span four orders of magnitude**, so the duration is seconds, `14m20s` or
   `5h30m` by size rather than fixed.

The loop wiring itself -- the interval, the flush, the counters -- is not testable without
MetaDrive and is verified by running a drive; see `docs/running-a-test.md`.
"""

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))


@pytest.fixture
def drive():
    return pytest.importorskip("drive")


# --- the two shapes --------------------------------------------------------------------------


def test_a_bounded_drive_reports_its_share_and_what_is_left(drive):
    """replay and idm: the recording's length bounds the drive, so a percentage means something."""
    line = drive._progress_line(
        steps=1800,
        budget=3695,
        sim_seconds=18.0,
        completion=0.462,
        speed_ms=13.0,
        ms_per_step=1.1,
        policy_ms=None,
        elapsed_seconds=2.0,
    )

    assert line == (
        "progress     step 1800 of 3695 (49%), 18.0 s driven, completion 0.462, 47 km/h, "
        "1.1 ms/step, 2s elapsed, ~2s left"
    )


def test_an_unbounded_drive_gives_no_eta_at_all(drive):
    """remote and manual: `budget` is None, and completion is the only progress there is.

    The absence is the point. A car circling a roundabout has a step count climbing against a
    completion that is not, and an ETA computed from either would hide exactly that.
    """
    line = drive._progress_line(
        steps=4200,
        budget=None,
        sim_seconds=42.0,
        completion=0.081,
        speed_ms=3.33,
        ms_per_step=205.0,
        policy_ms=12.0,
        elapsed_seconds=860.0,
    )

    assert line == (
        "progress     step 4200, 42.0 s driven, completion 0.081, 12 km/h, "
        "205 ms/step (12 ms of it the policy round trip), 14m20s elapsed"
    )
    assert "left" not in line
    assert "%" not in line


def test_the_policy_share_is_named_only_when_something_is_hosted(drive):
    """A replayed drive has no socket, and a line claiming a 0 ms round trip would invent one."""
    kwargs = dict(
        steps=100,
        budget=None,
        sim_seconds=1.0,
        completion=0.5,
        speed_ms=10.0,
        ms_per_step=1.0,
        elapsed_seconds=1.0,
    )

    assert "round trip" not in drive._progress_line(policy_ms=None, **kwargs)
    assert "round trip" in drive._progress_line(policy_ms=4.0, **kwargs)


def test_a_sub_millisecond_round_trip_does_not_render_as_zero(drive):
    """0.4 ms is what the stub bridge answers in, and "0 ms" reads as a broken measurement."""
    line = drive._progress_line(
        steps=100,
        budget=None,
        sim_seconds=1.0,
        completion=0.5,
        speed_ms=10.0,
        ms_per_step=1.3,
        policy_ms=0.42,
        elapsed_seconds=1.0,
    )

    assert "(0.4 ms of it the policy round trip)" in line


def test_completion_is_left_out_rather_than_printed_as_nan(drive):
    """`info` carries no route fraction until the environment has reported one."""
    line = drive._progress_line(
        steps=10,
        budget=None,
        sim_seconds=0.1,
        completion=float("nan"),
        speed_ms=10.0,
        ms_per_step=1.0,
        policy_ms=None,
        elapsed_seconds=1.0,
    )

    assert "nan" not in line
    assert "completion" not in line


# --- durations -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "seconds,expected",
    [
        (0.4, "0s"),
        (2.0, "2s"),
        (59.6, "1m00s"),  # rounds up across the boundary rather than printing 60s
        (860.0, "14m20s"),
        (3599.0, "59m59s"),
        (19800.0, "5h30m"),
    ],
)
def test_a_duration_uses_the_units_that_say_something(drive, seconds, expected):
    """A drive here spans 40 s to five hours, so one fixed unit is unreadable at one end."""
    assert drive._duration(seconds) == expected
