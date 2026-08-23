"""`tools/pedal_map.py` and the pure half of `tools/pedal_sweep.py` - Stage 9, Phase 0.

Like `test_openpilot_policy.py`, this reaches into `tools/` from this repo's 3.10 and needs
no MetaDrive: the table is JSON, the lookup is arithmetic, and `pedal_sweep`'s grid, gap-fill
and monotonicity helpers take plain lists.

**Every assertion here is about a sign or a magnitude**, because those are what fail silently.
A pedal map that answers "slow down" with throttle drives a car off a road and reads as a
badly tuned controller; a lookup that returns the right sign and the wrong scale reads as a
timid one. MetaDrive clips whatever it is given and says nothing about either.

The last group drives the **shipped** `calibration/metadrive-pedal-map.json` rather than a
fixture, because the fault Phase 0 exists to fix is a property of that file's numbers and not
of the code that reads them.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))

from openpilot_policy import BridgeError, to_metadrive_action  # noqa: E402
from pedal_map import (  # noqa: E402
    DEFAULT_PEDAL_MAP,
    PEDAL_MAP_VERSION,
    PedalMap,
    PedalMapError,
)
from pedal_sweep import _enforce_monotonic, _pedal_grid, _speed_grid, fill_gaps  # noqa: E402

SHIPPED = REPO / DEFAULT_PEDAL_MAP


def increases(values):
    """Indexed rather than `zip(v, v[1:])`, which ruff wants a `strict=` on (B905)."""
    return all(values[index] > values[index - 1] for index in range(1, len(values)))


def a_table(**overrides):
    """A small table with MetaDrive's real shape: flat in speed, sloped in pedal, cut at the top.

    Two speeds and three pedals is the smallest thing that exercises both interpolations.
    """
    fields = dict(
        speeds_mps=[0.0, 10.0, 20.0],
        pedals=[-1.0, 0.0, 1.0],
        accel_mps2=[
            [-12.0, -0.4, 2.8],
            [-11.0, -0.4, 2.8],
            # The speed ceiling: the engine is cut, so full throttle is worth almost nothing.
            [-11.0, -0.4, 0.6],
        ],
        measured={"vehicle": {"max_engine_force": 759.464, "max_brake_force": 89.464}},
    )
    fields.update(overrides)
    return PedalMap(**fields)


# ---------------------------------------------------------------------------------------
# The lookup, both directions
# ---------------------------------------------------------------------------------------


def test_a_pedal_survives_the_round_trip_through_acceleration_and_back():
    """`pedal_for(accel_for(p)) == p`. If it does not, the table is not being inverted."""
    table = a_table()
    for pedal in (-1.0, -0.6, -0.25, 0.0, 0.3, 0.75, 1.0):
        accel = table.accel_for(pedal, 10.0)
        assert table.pedal_for(accel, 10.0) == pytest.approx(pedal, abs=1e-9)


def test_acceleration_is_interpolated_between_the_two_nearest_speeds():
    """5 m/s lies between the rows, so full brake is the mean of -12.0 and -11.0."""
    table = a_table()
    assert table.accel_for(-1.0, 5.0) == pytest.approx(-11.5)
    assert table.accel_for(-1.0, 2.5) == pytest.approx(-11.75)


def test_a_request_harder_than_the_car_can_manage_clamps_rather_than_refusing():
    """Both ends are ordinary driving: full lock on the brake, and the speed limiter."""
    table = a_table()
    assert table.pedal_for(-40.0, 10.0) == pytest.approx(-1.0)
    assert table.pedal_for(+40.0, 10.0) == pytest.approx(1.0)
    # At the ceiling the engine is cut, so even +1.0 m/s^2 is more than the car has.
    assert table.pedal_for(1.0, 20.0) == pytest.approx(1.0)
    # ...and the same request well below the ceiling is a part-throttle.
    assert 0.0 < table.pedal_for(1.0, 10.0) < 1.0


def test_the_speed_the_engine_is_cut_at_changes_what_a_throttle_is_worth():
    """The one place the speed axis matters. A table read at one speed would miss it."""
    table = a_table()
    assert table.accel_for(1.0, 10.0) == pytest.approx(2.8)
    assert table.accel_for(1.0, 20.0) == pytest.approx(0.6)


def test_speed_outside_the_measured_range_clamps_to_the_nearest_row():
    table = a_table()
    assert table.accel_for(-1.0, -5.0) == pytest.approx(-12.0)
    assert table.accel_for(1.0, 999.0) == pytest.approx(0.6)


# ---------------------------------------------------------------------------------------
# What the file has to be for the lookup to mean anything
# ---------------------------------------------------------------------------------------


def test_a_table_whose_acceleration_falls_as_the_pedal_rises_is_refused():
    """It cannot be inverted: two pedals answer to one acceleration and neither is wrong."""
    with pytest.raises(PedalMapError, match="falls as the pedal rises"):
        a_table(accel_mps2=[[-12.0, 1.0, 0.5], [-11.0, -0.4, 2.8], [-11.0, -0.4, 0.6]])


def test_an_axis_that_does_not_increase_is_refused():
    with pytest.raises(PedalMapError, match="must increase"):
        a_table(speeds_mps=[0.0, 20.0, 10.0])
    with pytest.raises(PedalMapError, match="must increase"):
        a_table(pedals=[-1.0, 1.0, 0.0])


def test_a_ragged_table_is_refused_rather_than_read_short():
    with pytest.raises(PedalMapError, match="one column per pedal"):
        a_table(accel_mps2=[[-12.0, -0.4], [-11.0, -0.4, 2.8], [-11.0, -0.4, 0.6]])
    with pytest.raises(PedalMapError, match="one row per speed"):
        a_table(accel_mps2=[[-12.0, -0.4, 2.8]])


def test_a_missing_file_names_the_command_that_makes_one(tmp_path):
    with pytest.raises(PedalMapError, match="pedal-sweep.sh"):
        PedalMap.load(tmp_path / "nothing-here.json")


def test_a_table_written_by_a_later_version_is_refused_by_name(tmp_path):
    """A layout this reader does not know must not be read as if it did."""
    path = tmp_path / "future.json"
    payload = a_table().to_payload()
    payload["pedal_map_version"] = PEDAL_MAP_VERSION + 1
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(PedalMapError, match="pedal_map_version"):
        PedalMap.load(path)


def test_a_table_round_trips_through_the_file(tmp_path):
    path = tmp_path / "map.json"
    original = a_table()
    path.write_text(json.dumps(original.to_payload()), encoding="utf-8")
    loaded = PedalMap.load(path)
    assert loaded.speeds_mps == original.speeds_mps
    assert loaded.pedals == original.pedals
    assert loaded.accel_mps2 == original.accel_mps2
    assert loaded.pedal_for(-1.0, 10.0) == original.pedal_for(-1.0, 10.0)


# ---------------------------------------------------------------------------------------
# A table describes one car
# ---------------------------------------------------------------------------------------


def test_a_car_with_different_forces_is_reported_and_the_same_car_is_not():
    """`max_engine_force` is sampled from a BoxSpace, so this is not a hypothetical."""
    table = a_table()
    assert table.vehicle_notes({"max_engine_force": 759.464, "max_brake_force": 89.464}) == []
    notes = table.vehicle_notes({"max_engine_force": 840.0, "max_brake_force": 89.464})
    assert len(notes) == 1
    assert "max_engine_force" in notes[0]


def test_a_field_the_live_car_does_not_report_is_not_treated_as_a_difference():
    assert a_table().vehicle_notes({}) == []
    assert a_table().vehicle_notes(None) == []


# ---------------------------------------------------------------------------------------
# The third longitudinal mode
# ---------------------------------------------------------------------------------------

REPLY = {"steer": 0.25, "throttle": 0.274, "brake": 0.0, "accel_cmd": -1.0}


def test_the_table_mode_brakes_where_the_bridges_own_pedals_accelerate():
    """The whole of Phase 0, in one assertion.

    `accel_cmd` -1.0 m/s^2 is a request to slow down. `pedal` hands back the CARLA table's
    answer, which on this car is throttle, because that table's zero crossing is the CARLA
    Tesla's -1.582 m/s^2 of drag rather than MetaDrive's -0.364.
    """
    table = a_table()
    _, from_bridge = to_metadrive_action(REPLY, "pedal")
    _, from_table = to_metadrive_action(REPLY, "table", speed_mps=10.0, table=table)
    assert from_bridge > 0.0
    assert from_table < 0.0
    assert table.accel_for(from_table, 10.0) == pytest.approx(-1.0, abs=1e-6)


def test_the_table_mode_reads_the_speed_it_is_given():
    """A throttle request at the speed ceiling has to ask for more pedal than below it."""
    table = a_table()
    reply = dict(REPLY, accel_cmd=0.5)
    _, low = to_metadrive_action(reply, "table", speed_mps=10.0, table=table)
    _, high = to_metadrive_action(reply, "table", speed_mps=20.0, table=table)
    assert high > low


def test_the_table_mode_without_a_table_is_refused_by_name():
    with pytest.raises(BridgeError, match="pedal map"):
        to_metadrive_action(REPLY, "table", speed_mps=10.0, table=None)


def test_the_table_mode_refuses_a_reply_the_stub_would_send():
    """The stub answers in pedals and carries no `accel_cmd`; that has to say so."""
    with pytest.raises(BridgeError, match="accel_cmd"):
        to_metadrive_action({"steer": 0.0, "throttle": 0.1}, "table", 10.0, a_table())


def test_the_table_mode_refuses_a_nan_before_it_reaches_the_lookup():
    """`min(1.0, nan)` is 1.0, so a NaN that survives arrives as full throttle."""
    with pytest.raises(BridgeError, match="accel_cmd"):
        to_metadrive_action(dict(REPLY, accel_cmd=float("nan")), "table", 10.0, a_table())


def test_the_other_two_modes_are_unchanged_by_the_third_ones_arguments():
    """`pedal` and `accel` must keep producing exactly what they did before the table."""
    assert to_metadrive_action(REPLY, "pedal") == to_metadrive_action(
        REPLY, "pedal", speed_mps=20.0, table=a_table()
    )
    assert to_metadrive_action(REPLY, "accel") == to_metadrive_action(
        REPLY, "accel", speed_mps=20.0, table=a_table()
    )


# ---------------------------------------------------------------------------------------
# The sweep's own arithmetic
# ---------------------------------------------------------------------------------------


def test_the_speed_grid_reaches_the_cars_own_ceiling_and_stays_increasing():
    speeds = _speed_grid(22.222, 1.0)
    assert speeds[0] == 0.0
    assert speeds[-1] == pytest.approx(22.222)
    assert increases(speeds)


def test_a_ceiling_that_lands_on_the_grid_does_not_produce_two_rows_the_trim_cannot_separate():
    """`PedalMap` refuses a non-increasing axis, so a near-duplicate top row is a refusal."""
    speeds = _speed_grid(20.05, 1.0)
    assert speeds[-1] == pytest.approx(20.05)
    assert increases(speeds)
    assert speeds[-1] - speeds[-2] >= 0.5


def test_the_pedal_grid_covers_the_whole_action_range_and_lands_on_both_ends():
    pedals = _pedal_grid(0.05)
    assert pedals[0] == -1.0
    assert pedals[-1] == 1.0
    assert 0.0 in pedals
    assert len(pedals) == 41


def test_an_unmeasured_cell_takes_the_nearest_measured_speed_and_keeps_a_zero_count():
    """Only the bottom rows are ever empty - a stationary car cannot be measured braking."""
    speeds = [0.0, 1.0, 2.0]
    accel = [[0.0, 1.0], [0.0, 1.0], [-11.0, 2.0]]
    counts = [[0, 3], [0, 3], [3, 3]]
    filled = fill_gaps(speeds, accel, counts)
    assert [row[0] for row in accel] == [-11.0, -11.0, -11.0]
    assert [entry[0] for entry in filled] == [0.0, 1.0]
    assert counts[0][0] == 0


def test_a_column_that_was_never_measured_at_all_is_a_broken_run_not_a_car_limit():
    with pytest.raises(RuntimeError, match="never measured"):
        fill_gaps([0.0, 1.0], [[0.0], [0.0]], [[0], [0]])


def test_a_row_is_flattened_forward_so_it_never_claims_more_than_was_measured():
    accel = [[-1.0, -2.0, 3.0]]
    repairs = _enforce_monotonic(accel)
    assert accel == [[-1.0, -1.0, 3.0]]
    assert repairs == [(0, 1, pytest.approx(1.0))]


# ---------------------------------------------------------------------------------------
# The shipped table itself
# ---------------------------------------------------------------------------------------


@pytest.mark.skipif(not SHIPPED.exists(), reason=f"no {DEFAULT_PEDAL_MAP}")
def test_the_shipped_table_answers_every_deceleration_request_with_a_brake():
    """The measurement that found the fault, run as an assertion.

    Measured against the real bridge before this existed: 137 of 201 requests to decelerate
    on `junction-1` came back as throttle. Any request below MetaDrive's own coast must now
    be a negative pedal at every speed the car reaches.
    """
    table = PedalMap.load(SHIPPED)
    for speed in table.speeds_mps:
        for accel in (-3.48, -2.0, -1.5, -1.0, -0.5):
            pedal = table.pedal_for(accel, speed)
            assert pedal < 0.0, f"{accel} m/s2 at {speed} m/s came back as pedal {pedal}"


@pytest.mark.skipif(not SHIPPED.exists(), reason=f"no {DEFAULT_PEDAL_MAP}")
def test_the_shipped_tables_zero_crossing_is_metadrives_coast_and_not_carlas():
    """The crossover is the whole difference between the two tables.

    MetaDrive coasts at -0.364 m/s^2 and the CARLA Tesla at -1.582, so everything between the
    two is where `pedal` mode has the sign wrong. A tolerance rather than an equality because
    it is a measurement.
    """
    table = PedalMap.load(SHIPPED)
    assert table.accel_for(0.0, 14.0) == pytest.approx(-0.364, abs=0.05)
    # And a request in the disputed band is a light brake here, where CARLA's table throttles.
    assert -0.2 < table.pedal_for(-1.0, 14.0) < 0.0


@pytest.mark.skipif(not SHIPPED.exists(), reason=f"no {DEFAULT_PEDAL_MAP}")
def test_the_shipped_table_covers_the_whole_envelope_the_bridge_plans_within():
    """The bridge plans in [-3.48, +2.0]. A table that stops short would clamp real requests."""
    table = PedalMap.load(SHIPPED)
    assert table.accel_for(-1.0, 14.0) <= -3.48
    assert table.accel_for(1.0, 14.0) >= 2.0
    assert table.speeds_mps[0] == 0.0
    assert table.speeds_mps[-1] >= 22.0


@pytest.mark.skipif(not SHIPPED.exists(), reason=f"no {DEFAULT_PEDAL_MAP}")
def test_the_shipped_table_says_which_car_it_was_measured_on():
    """Without this the check at the start of an episode has nothing to compare against."""
    vehicle = PedalMap.load(SHIPPED).measured.get("vehicle") or {}
    for key in ("max_engine_force", "max_brake_force", "max_speed_km_h", "mass_kg"):
        assert key in vehicle, key
