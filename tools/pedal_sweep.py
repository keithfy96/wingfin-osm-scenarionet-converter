"""Measure what MetaDrive's car actually does when you hold a pedal, and write the table.

    <metadrive-checkout>/.venv/bin/python tools/pedal_sweep.py \\
        workspaces/junction-1/scenarionet-10hz --out calibration/metadrive-pedal-map.json

    # or, from inside scripts/
    ./pedal-sweep.sh junction-1

Stage 9, Phase 0. The output is read by `tools/pedal_map.py`, and through it by
`examples/openpilot_server.py --longitudinal table`. Nothing else consumes it and nothing
here is a dependency of anything.

**Why it exists.** The openpilot bridge plans in m/s^2 and converts to pedals with
`accel_map.py` - two 8x11 tables from a "Town10HD calibration sweep on Tesla M3 @ 20 Hz
sync". Their zero crossing is the *CARLA Tesla's* zero-throttle deceleration, -1.582 m/s^2
above 10 m/s, and MetaDrive's car coasts at **-0.364**. So every request to slow down more
gently than the CARLA car's own drag comes back as throttle: measured on `junction-1`,
**137 of 201** decelerations, and the car accelerated from 13.9 to 20.5 m/s and left the road.
This tool builds the same kind of table for the car that is being driven, by the same method
they used - hold a pedal, measure the acceleration, sweep the grid, invert it.

**The sweep visits speeds, not the pedal's own trajectory**, and that is the whole design.
Holding one pedal and letting the car sweep through speeds by itself is the obvious shape and
it does not work here, because `_apply_throttle_brake` has **no aerodynamic term** - the only
resistance is a constant `setBrake(2.0)` on all four wheels. Net acceleration is therefore
almost independent of speed, so a pedal near the one that cancels the coast (about +0.13)
neither speeds the car up nor slows it down: measured, it would take **440 s and 4.9 km** to
cross the speed range, and the pedals either side of it never leave the end they start at. So
the car is trimmed *to* each speed and every pedal is probed there.

**Four things read off MetaDrive before any of this was written:**

* `BulletPlaneShape(Vec3(0, 0, 1), 0)` (`terrain.py:179`) is **infinite**, so a car driven
  straight for kilometres never runs out of ground and the sweep is not bounded by
  `map_region_size`. It also means the road surface is irrelevant to the measurement - with
  `--render none` there is no terrain mesh at all and the car is on that plane wherever it is.
* Engine force is cut to **zero** above `max_speed_km_h` (`base_vehicle.py:499`), so the top
  of the range is a limit cycle rather than an equilibrium - at full throttle the car
  alternates +2.76 and -0.364 m/s^2. Binned and meaned, that reads as the ~0 net acceleration
  it is, which is the honest number for "what can this car sustain at 22 m/s".
* `max_engine_force` and `max_brake_force` are **sampled** from `BoxSpace(750, 850)` and
  `BoxSpace(80, 180)` (`pg_space.py:239-240`), not constants. Measured 759.464 / 89.464 on
  both extracts at both rates - identical, because the parameter seed is the scenario index
  and each dataset holds one scenario. Written into the file and checked at load.
* The car does **not** start from rest. `spawn_velocity` comes off the recorded track:
  13.89 m/s on `junction-1`, 10.85 on `mosque`. Nothing here assumes a standing start.

`max_lateral_dist` is raised for the sweep and **recorded rather than applied silently**, the
same decision `step_timing.py` documents for `SWEEP_MAX_LATERAL_M`: MetaDrive's 4 m ends an
episode when the car strays from its *recorded* route, and it is there to judge driving. This
tool holds zero steering on a curving route on purpose, and a car 200 m off its line has
exactly the drivetrain a car on it has.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from agent_env import make_env, sim_step_seconds  # noqa: E402
from pedal_map import DEFAULT_PEDAL_MAP, PEDAL_MAP_VERSION, PedalMap  # noqa: E402
from step_timing import rate_keys  # noqa: E402

# MetaDrive's own 4 m judges *driving*; this measures a drivetrain. See the module docstring.
SWEEP_MAX_LATERAL_M = 100000.0

# How close to the requested speed the trim has to get before a pedal is probed. 0.15 m/s is
# under a sixth of a speed bin, so a cell is measured at the speed it is filed under.
TRIM_TOLERANCE_MPS = 0.15

# Trimming is proportional, using the full-throttle and full-brake accelerations measured at
# the start of the run rather than a constant - `setBrake` is a torque in units bullet does
# not document, and the arithmetic from `max_brake_force` is out by a factor of twelve.
TRIM_MAX_STEPS = 600

# A braking step that ends below this is thrown away, and that is not tidying - it is the one
# measurement fault this sweep has. A car braking at 11.2 m/s^2 loses 1.12 m/s in a 10 Hz step,
# so from 1 m/s it reaches zero **inside** the step and the average over it reads -3.18 rather
# than -11.19: the step was cut short by running out of speed, not by the pedal. Measured
# before this guard existed, that artefact alone put 60 cells out of order by up to 0.90 m/s^2
# and made the bottom four rows of the table describe a car that cannot brake. A step is kept
# when it ends above the floor, or when it ends *faster* than it started - the latter is a car
# pulling away from rest, which is a real throttle measurement and the only thing worth having
# in the 0 m/s row.
TRUNCATION_FLOOR_MPS = 0.5


def longitudinal_speed(vehicle):
    """Speed along the car's own heading, which is the quantity MetaDrive brakes against.

    `vehicle.speed` is the 2-D norm and cannot go negative, so it reads a car rolling backwards
    as going forwards. `_apply_throttle_brake` uses exactly this projection for its deadzone
    (`base_vehicle.py:511-513`), so the measurement and the thing measured agree by
    construction.
    """
    heading = vehicle.heading
    velocity = vehicle.velocity
    return float(velocity[0] * heading[0] + velocity[1] * heading[1])


def _step(env, pedal):
    """One step at zero steering, returning the speed before and the speed after."""
    before = longitudinal_speed(env.agent)
    env.step([0.0, float(pedal)])
    return before, longitudinal_speed(env.agent)


def _trim_to(env, target_mps, gains, dt):
    """Drive the car to `target_mps` and leave it there. True when it arrived."""
    throttle_gain, brake_gain = gains
    for _ in range(TRIM_MAX_STEPS):
        speed = longitudinal_speed(env.agent)
        error = float(target_mps) - speed
        if abs(error) <= TRIM_TOLERANCE_MPS:
            return True
        # The acceleration that would close the gap in one step, turned into a pedal by
        # whichever of the two measured full-scale accelerations applies in that direction.
        wanted = error / dt
        pedal = wanted / throttle_gain if wanted >= 0.0 else wanted / brake_gain
        _step(env, max(-1.0, min(1.0, pedal)))
    return False


def _calibrate_gains(env, dt):
    """Full-throttle and |full-brake| acceleration, measured once, for the trim controller.

    Only the trim uses these; the table itself is measured cell by cell. They are taken at
    whatever speed the car spawns at, which is enough for a proportional controller with a
    600-step budget and is not enough for anything else.
    """
    throttle = []
    for _ in range(3):
        before, after = _step(env, 1.0)
        throttle.append((after - before) / dt)
    brake = []
    for _ in range(3):
        before, after = _step(env, -1.0)
        brake.append((after - before) / dt)
    # Floors rather than the raw means: at the speed ceiling the throttle probe reads ~0 and a
    # gain of zero divides by zero on the first trim step.
    return max(0.5, sum(throttle) / len(throttle)), max(0.5, abs(sum(brake) / len(brake)))


def _speed_grid(ceiling_mps, step_mps):
    speeds = []
    value = 0.0
    while value < ceiling_mps - 1e-9:
        speeds.append(round(value, 6))
        value += step_mps
    speeds.append(round(ceiling_mps, 6))
    # A ceiling less than half a step above the last grid point would give two rows the trim
    # cannot tell apart, and `PedalMap` refuses a non-increasing axis.
    if len(speeds) > 1 and speeds[-1] - speeds[-2] < step_mps * 0.5:
        speeds.pop(-2)
    return speeds


def _pedal_grid(step):
    pedals = []
    value = -1.0
    while value <= 1.0 + 1e-9:
        pedals.append(round(value, 6))
        value += step
    if abs(pedals[-1] - 1.0) > 1e-9:
        pedals.append(1.0)
    return pedals


def sweep(env, speeds, pedals, hold_steps, dt, progress=None):
    """`(accel[speed][pedal], counts[speed][pedal], drift)` - the measurement itself.

    Every cell is the mean acceleration over `hold_steps` steps of holding that pedal at that
    speed. The car drifts while a pedal is held - 1.2 m/s a step under full braking - so the
    largest distance any cell's samples ended up from the speed they are filed under comes
    back as `drift` and is written into the file. It bounds how wrong the speed axis can be.
    """
    gains = _calibrate_gains(env, dt)
    accel = [[0.0] * len(pedals) for _ in speeds]
    counts = [[0] * len(pedals) for _ in speeds]
    drift = 0.0
    unreachable = []

    for row, target in enumerate(speeds):
        if not _trim_to(env, target, gains, dt):
            unreachable.append(target)
        for column, pedal in enumerate(pedals):
            _trim_to(env, target, gains, dt)
            values = []
            for _ in range(hold_steps):
                before, after = _step(env, pedal)
                if after <= TRUNCATION_FLOOR_MPS:
                    # See TRUNCATION_FLOOR_MPS. Below the floor the two cases part: a car
                    # that ended slower has run out of speed inside the step and every step
                    # after it is a stationary car, so stop; a car that ended faster is
                    # pulling away from rest and has simply not reached the floor yet, so
                    # skip this step and keep holding.
                    if after <= before:
                        break
                    continue
                values.append((after - before) / dt)
                drift = max(drift, abs(before - target))
            if values:
                accel[row][column] = sum(values) / len(values)
                counts[row][column] = len(values)
        if progress is not None:
            progress(row, target, accel[row])

    return accel, counts, drift, unreachable


def fill_gaps(speeds, accel, counts):
    """Give every unmeasured cell the value from the nearest speed that *was* measured.

    Only the bottom of the table has any: braking cannot be measured at 0 m/s, because a
    stationary car does not decelerate however hard the brake is held. Taking the nearest
    measured speed is right rather than merely convenient - brake torque does not vanish as
    the car slows, and the measured column is flat to within 6% from 4 m/s to 22 m/s.

    Returns the cells filled, as `(speed, pedal, taken_from_speed)`, so the run can say how
    much of the table it did not measure instead of the file quietly implying it did. The
    counts stay at zero, which is what `sample_counts` in the file means.
    """
    filled = []
    for column in range(len(accel[0])):
        measured = [row for row in range(len(speeds)) if counts[row][column] > 0]
        if not measured:
            raise RuntimeError(
                f"pedal column {column} was never measured at any speed, which means the "
                "sweep did not run - not that the car cannot do it"
            )
        for row in range(len(speeds)):
            if counts[row][column] > 0:
                continue
            nearest = min(measured, key=lambda other: abs(speeds[other] - speeds[row]))
            accel[row][column] = accel[nearest][column]
            filled.append((speeds[row], column, speeds[nearest]))
    return filled


def _enforce_monotonic(accel):
    """Make each row non-decreasing in pedal, reporting every place it had to.

    Acceleration rises with pedal by construction - more engine force, or less brake force -
    so a fall is one of two things, and neither is a reason to throw a sweep away:

    * **at the speed ceiling, the engine cutoff.** Above `max_speed_km_h` the engine is off
      (`base_vehicle.py:499`), so a cell there is the mean of engaged and cut steps and its
      value depends on where in that limit cycle the three held steps landed. Measured on
      `junction-1`, every fall of any size was in the top row.
    * **elsewhere, measurement noise**, which is small: the same row read -11.19 and -11.32
      m/s^2 at full brake on different passes.

    Flattening to the cell before is the conservative direction - it never claims more
    acceleration than was measured. `PedalMap` refuses a table it cannot invert, so this runs
    first and says where.
    """
    repairs = []
    for row_index, row in enumerate(accel):
        for column in range(1, len(row)):
            if row[column] < row[column - 1]:
                repairs.append((row_index, column, row[column - 1] - row[column]))
                row[column] = row[column - 1]
    return repairs


def _vehicle_fields(vehicle):
    return {
        "class": type(vehicle).__name__,
        "mass_kg": float(vehicle.MASS),
        "max_engine_force": float(vehicle.config["max_engine_force"]),
        "max_brake_force": float(vehicle.config["max_brake_force"]),
        "max_speed_km_h": float(vehicle.config["max_speed_km_h"]),
        "wheel_friction": float(vehicle.config["wheel_friction"]),
        "max_steering_deg": float(vehicle.max_steering),
    }


def _print_table(speeds, pedals, accel, every):
    """The measurement, thinned to fit a terminal. Every column is a real measured pedal."""
    columns = [index for index in range(len(pedals)) if index % every == 0]
    if columns[-1] != len(pedals) - 1:
        columns.append(len(pedals) - 1)
    header = "  m/s  " + "".join(f"{pedals[index]:>8.2f}" for index in columns)
    print(header)
    print("  " + "-" * (len(header) - 2))
    for row, speed in enumerate(speeds):
        line = f"  {speed:5.1f}" + "".join(f"{accel[row][index]:>8.2f}" for index in columns)
        print(line)
    print("\n  cells are m/s^2 held at that speed; columns are throttle_brake\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("dataset", help="Directory holding dataset_summary.pkl")
    parser.add_argument(
        "--out",
        default=DEFAULT_PEDAL_MAP,
        help=f"Where to write the table (default: {DEFAULT_PEDAL_MAP})",
    )
    parser.add_argument("--scenario", type=int, default=0, help="Which scenario to drive")
    parser.add_argument(
        "--step-hz",
        type=float,
        default=None,
        help="How many times a second the simulator advances. MetaDrive's own 10 when unset. "
        "The forces are the same at any rate, so a table measured at one is usable at "
        "another; the rate is recorded so a reader can see which it was.",
    )
    parser.add_argument(
        "--physics-hz",
        type=float,
        default=None,
        help="Physics ticks a second. Derived from --step-hz when unset, exactly as drive.py.",
    )
    parser.add_argument(
        "--speed-step",
        type=float,
        default=1.0,
        help="Spacing of the speed axis in m/s (default 1.0). The axis runs 0 to the car's "
        "own max_speed_km_h.",
    )
    parser.add_argument(
        "--pedal-step",
        type=float,
        default=0.05,
        help="Spacing of the pedal axis (default 0.05, so 41 pedals over [-1, 1]).",
    )
    parser.add_argument(
        "--hold-steps",
        type=int,
        default=3,
        help="Steps each pedal is held at each speed before the mean is taken (default 3). "
        "More is not better: the car drifts away from the speed the cell is filed under.",
    )
    parser.add_argument(
        "--column-every",
        type=int,
        default=4,
        help="Print every Nth pedal column (default 4). Only affects what is printed.",
    )
    parser.add_argument(
        "--no-write", action="store_true", help="Measure and print, write nothing"
    )
    arguments = parser.parse_args()

    started = time.time()
    # No flags at all means MetaDrive's own pair, left untouched. With either flag, 10 Hz is
    # the step rate to derive from, because `step_config(10)` returns exactly (0.02, 5) - so
    # `--physics-hz 100` alone is the default step rate with the integrator pinned, and not a
    # second, differently-derived version of the unflagged run. There is no dataset-rate
    # refusal here as there is in `drive.py`: the sweep never replays the tape, it drives its
    # own pedals and reads the car, so the rate the dataset was written at does not enter.
    if arguments.step_hz is None and arguments.physics_hz is None:
        config = {}
    else:
        config = dict(rate_keys(arguments.step_hz or 10.0, arguments.physics_hz))
    env = make_env(
        arguments.dataset,
        render=None,
        max_lateral_dist=SWEEP_MAX_LATERAL_M,
        **config,
    )
    try:
        env.reset(seed=arguments.scenario)
        dt = sim_step_seconds(env)
        vehicle = _vehicle_fields(env.agent)
        ceiling = vehicle["max_speed_km_h"] / 3.6
        speeds = _speed_grid(ceiling, arguments.speed_step)
        pedals = _pedal_grid(arguments.pedal_step)

        print(f"dataset      {arguments.dataset}  scenario {arguments.scenario}")
        print(
            f"vehicle      {vehicle['class']}  {vehicle['mass_kg']:.0f} kg  "
            f"engine {vehicle['max_engine_force']:.3f}  brake {vehicle['max_brake_force']:.3f}"
        )
        print(
            f"rate         {1.0 / dt:.0f} Hz step, "
            f"{1.0 / env.config['physics_world_step_size']:.0f} Hz physics"
        )
        print(
            f"grid         {len(speeds)} speeds x {len(pedals)} pedals, "
            f"{arguments.hold_steps} steps a cell "
            f"= {len(speeds) * len(pedals) * arguments.hold_steps} measured steps"
        )
        print(f"max_lateral  {SWEEP_MAX_LATERAL_M:.0f} m (raised for the sweep; see --help)\n")

        def progress(row, target, _values):
            print(f"  {row + 1:3d}/{len(speeds)}  {target:5.1f} m/s", flush=True)

        accel, counts, drift, unreachable = sweep(
            env, speeds, pedals, arguments.hold_steps, dt, progress
        )
    finally:
        env.close()

    filled = fill_gaps(speeds, accel, counts)
    repairs = _enforce_monotonic(accel)
    table = PedalMap(
        speeds,
        pedals,
        accel,
        measured={
            "dataset": os.path.abspath(arguments.dataset),
            "scenario": arguments.scenario,
            "step_seconds": dt,
            "step_hz": round(1.0 / dt, 6),
            "hold_steps": arguments.hold_steps,
            "max_lateral_m": SWEEP_MAX_LATERAL_M,
            "speed_drift_mps": round(drift, 4),
            "vehicle": vehicle,
            "tool": "tools/pedal_sweep.py",
        },
    )

    print()
    _print_table(speeds, pedals, accel, max(1, arguments.column_every))
    print(f"  {table.summary()}")
    print(f"  speed drift  {drift:.2f} m/s worst, over {arguments.hold_steps} held steps")
    if filled:
        lowest = max(speed for speed, _, _ in filled)
        print(
            f"  filled       {len(filled)} of {len(speeds) * len(pedals)} cells from the "
            f"nearest measured speed, none above {lowest:.1f} m/s - a stationary car cannot "
            "be measured braking"
        )
    if repairs:
        row, column, worst = max(repairs, key=lambda repair: repair[2])
        print(
            f"  flattened    {len(repairs)} cells where accel fell as the pedal rose, worst "
            f"{worst:.4f} m/s^2 at {speeds[row]:.1f} m/s / pedal {pedals[column]:+.2f} - "
            "the engine cutoff or noise, see _enforce_monotonic"
        )
    if unreachable:
        print(
            f"  unreachable  {len(unreachable)} speeds the trim could not hold: "
            + ", ".join(f"{value:.1f}" for value in unreachable)
        )
    print(f"  elapsed      {time.time() - started:.1f} s")

    if arguments.no_write:
        print("\n  --no-write: nothing written")
        return 0

    directory = os.path.dirname(os.path.abspath(arguments.out))
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(arguments.out, "w", encoding="utf-8") as handle:
        json.dump(table.to_payload(counts), handle, indent=1)
        handle.write("\n")
    size = os.path.getsize(arguments.out)
    print(f"\n  wrote        {arguments.out}  ({size / 1024:.1f} KB, version {PEDAL_MAP_VERSION})")
    print("  drive with   --longitudinal table")
    return 0


if __name__ == "__main__":
    sys.exit(main())
