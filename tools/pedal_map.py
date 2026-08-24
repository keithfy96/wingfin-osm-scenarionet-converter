"""A measured MetaDrive pedal table, and the lookup that inverts it.

    from pedal_map import PedalMap
    table = PedalMap.load("calibration/metadrive-pedal-map.json")
    action = table.pedal_for(accel_mps2=-1.0, speed_mps=14.0)

Stage 9, Phase 0. `tools/openpilot_policy.py --longitudinal table` is the only consumer;
`tools/pedal_sweep.py` is the only producer.

**This is not a controller, and the distinction is worth stating because the flag it sits
behind is spelled `--longitudinal`.** A model decides where to go, the openpilot bridge
decides how hard and which way - `accel_cmd` in m/s^2 and a steering angle in degrees - and
this decides only *how far to press a pedal to get that acceleration on this particular car*.
There is no target here, no error term, no memory and no feedback: `PedalMap` is a pure
`(accel, speed) -> pedal` and `pedal_for(accel_for(p)) == p` exactly.

**The bridge's own egress already contains one of these.** `server.py:788-792` does exactly
two conversions before replying, side by side - a road-wheel angle into a normalised steer,
and `accel_to_carla(self._last_actuators.accel, v_ego)` into a throttle and a brake - and both
are properties of the car rather than of the control law. So this module replaces
`accel_map.py` rather than adding a stage after it. The steering half needed no replacement
because that conversion is geometry and cancelled to nothing; see the module docstring of
`openpilot_policy`.

**Why a table rather than a formula.** The openpilot bridge plans in m/s^2 and hands back
CARLA pedals from `accel_map.py` - two 8x11 tables from a "Town10HD calibration sweep on
Tesla M3 @ 20 Hz sync". Its zero crossing is the **CARLA Tesla's own zero-throttle
deceleration**, -1.582 m/s^2 above 10 m/s, so on `junction-1` **137 of 201** requests to slow
down came back as throttle and the car ran away from 13.9 to 20.5 m/s and left the road. That
crossover belongs to the table, not to the trajectory, so no model fixes it. This is the same
measurement made against the car that is actually being driven.

**What MetaDrive's longitudinal model turns out to be**, read from
`base_vehicle.py:493-520` and then measured rather than assumed:

* `setBrake(2.0)` is applied to all four wheels **even under throttle**, and there is no
  aerodynamic term anywhere. So coasting decelerates at a *constant* rate - measured
  **-0.364 m/s^2** at every speed from 22.45 down to 15.22 m/s on `junction-1`. This is the
  MetaDrive equivalent of the bridge's `coast_accel`, and it is a quarter of the CARLA
  figure the bridge assumes.
* Engine force is cut to **zero** above `max_speed_km_h` (80, so 22.22 m/s). That is a
  ceiling rather than an equilibrium: at full throttle the car alternates between +2.76 and
  -0.364 m/s^2 as the cutoff engages and releases. Averaged over a speed bin it reads as the
  ~0 net acceleration it really is, which is why the table is binned and meaned rather than
  sampled.
* `max_engine_force` and `max_brake_force` are **sampled**, not fixed -
  `BoxSpace(750, 850)` and `BoxSpace(80, 180)` at `pg_space.py:239-240`. Measured
  **759.464 / 89.464** on both extracts at both rates, identical, because the parameter seed
  is the scenario index and each of our datasets holds one scenario. They are recorded in the
  file and checked at load, since a vehicle whose forces differ is a different car and the
  table does not describe it.

**Pure standard library on purpose.** `tools/` runs under MetaDrive's 3.8 interpreter as well
as this repo's 3.10, and this module is imported by `openpilot_policy`, which is imported by
`examples/openpilot_server.py`, which runs on whichever interpreter the model does. The table
is a few hundred floats; numpy would buy nothing and cost portability.
"""

from __future__ import annotations

import json
import os

# The table's own schema version, in the file. Version 1 is the first, and a reader refuses
# anything else by name rather than mis-reading a later layout - the same rule
# `tools/traffic.py` applies to `traffic_version`, and for the same reason: a silently
# mis-read calibration drives a car.
PEDAL_MAP_VERSION = 1

# Where the sweep writes and the server reads, repo-relative so the string is the same on the
# host and in the container - `rigs/cams.txt`'s reason. Not `config/`: `configuration_checksum`
# feeds `generation_fingerprint` (`generation.py:2212`), so nothing that is not a Stage 2 input
# belongs there, and a neighbouring file invites the question every time.
DEFAULT_PEDAL_MAP = "calibration/metadrive-pedal-map.json"

# How far the live car's forces may differ from the measured ones before the table is refused.
# 1% rather than exact equality because the values are floats off a `BoxSpace` sample and are
# written to the file through JSON; 1% of 759 N is 7.6 N, far under the 100 N spread of the
# space itself, so a genuinely different sample cannot slip through.
FORCE_TOLERANCE = 0.01


class PedalMapError(RuntimeError):
    """The table is missing, malformed, or describes a different car."""


def _interpolate(xs, ys, x):
    """`y` at `x` by linear interpolation, clamped to the ends. `xs` must be increasing."""
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    low = 0
    high = len(xs) - 1
    while high - low > 1:
        middle = (low + high) // 2
        if xs[middle] <= x:
            low = middle
        else:
            high = middle
    span = xs[high] - xs[low]
    if span <= 0.0:
        return ys[low]
    fraction = (x - xs[low]) / span
    return ys[low] + fraction * (ys[high] - ys[low])


class PedalMap:
    """`accel_mps2 <-> throttle_brake`, at a given speed, both ways.

    The forward direction is what was measured; `pedal_for` inverts it. Inversion is only
    defined because acceleration rises monotonically with pedal at every speed - checked at
    load rather than assumed, since a table with a flat or reversed run in it would silently
    return whichever pedal the search happened to land on.
    """

    def __init__(self, speeds_mps, pedals, accel_mps2, measured=None, path=None):
        if len(speeds_mps) < 2 or len(pedals) < 2:
            raise PedalMapError("a pedal map needs at least two speeds and two pedals")
        if len(accel_mps2) != len(speeds_mps):
            raise PedalMapError("accel_mps2 must have one row per speed")
        for row in accel_mps2:
            if len(row) != len(pedals):
                raise PedalMapError("accel_mps2 must have one column per pedal")
        self.speeds_mps = [float(v) for v in speeds_mps]
        self.pedals = [float(p) for p in pedals]
        self.accel_mps2 = [[float(a) for a in row] for row in accel_mps2]
        self.measured = dict(measured or {})
        self.path = path
        self._check_axes()

    def _check_axes(self):
        # Indexed rather than `zip(axis, axis[1:])` because ruff asks for `strict=` (B905) and
        # that keyword does not exist on Python 3.8, which `tools/` also runs under. Same
        # resolution as `policy_client.py`.
        for name, axis in (("speeds_mps", self.speeds_mps), ("pedals", self.pedals)):
            for index in range(1, len(axis)):
                earlier, later = axis[index - 1], axis[index]
                if later <= earlier:
                    raise PedalMapError(f"{name} must increase; got {earlier} then {later}")
        for index, row in enumerate(self.accel_mps2):
            for column in range(len(row) - 1):
                earlier, later = row[column], row[column + 1]
                if later < earlier:
                    raise PedalMapError(
                        "acceleration falls as the pedal rises at "
                        f"{self.speeds_mps[index]:.2f} m/s, between pedal "
                        f"{self.pedals[column]:+.3f} ({earlier:+.3f} m/s2) and "
                        f"{self.pedals[column + 1]:+.3f} ({later:+.3f} m/s2). "
                        "The table cannot be inverted; re-run tools/pedal_sweep.py."
                    )

    # -- reading and writing -------------------------------------------------------------

    @classmethod
    def load(cls, path):
        try:
            with open(path, encoding="utf-8") as handle:
                payload = json.load(handle)
        except OSError as error:
            raise PedalMapError(
                f"cannot read the pedal map at {path} - {type(error).__name__}: {error}. "
                "Measure one with:\n"
                "    ./scripts/pedal-sweep.sh junction-1"
            ) from error
        except ValueError as error:
            raise PedalMapError(f"{path} is not valid JSON - {error}") from error

        version = payload.get("pedal_map_version")
        if version != PEDAL_MAP_VERSION:
            raise PedalMapError(
                f"{path} is pedal_map_version {version!r}, and this reader is "
                f"{PEDAL_MAP_VERSION}. Re-run tools/pedal_sweep.py rather than editing it."
            )
        try:
            return cls(
                payload["speeds_mps"],
                payload["pedals"],
                payload["accel_mps2"],
                measured=payload.get("measured"),
                path=os.path.abspath(path),
            )
        except KeyError as error:
            raise PedalMapError(f"{path} has no {error.args[0]!r}") from error

    def to_payload(self, counts=None):
        payload = {
            "pedal_map_version": PEDAL_MAP_VERSION,
            "measured": self.measured,
            "speeds_mps": self.speeds_mps,
            "pedals": self.pedals,
            "accel_mps2": self.accel_mps2,
        }
        if counts is not None:
            # How many simulator steps landed in each cell. Zero means the cell was filled in
            # along the speed axis rather than measured, which a reader must be able to see.
            payload["sample_counts"] = counts
        return payload

    # -- the two directions --------------------------------------------------------------

    def row_at(self, speed_mps):
        """`accel` against pedal at one speed, interpolated between the two nearest rows."""
        return [
            _interpolate(self.speeds_mps, [row[column] for row in self.accel_mps2], speed_mps)
            for column in range(len(self.pedals))
        ]

    def accel_for(self, pedal, speed_mps):
        """What holding `pedal` at `speed_mps` produces. The measured direction."""
        return _interpolate(self.pedals, self.row_at(speed_mps), float(pedal))

    def pedal_for(self, accel_mps2, speed_mps):
        """The pedal that produces `accel_mps2` at `speed_mps`, clamped to what the car can do.

        Clamped rather than refused, because both ends are ordinary driving rather than an
        error: above the top the car is at `max_speed_km_h` and the engine is cut, and below
        the bottom the request is harder than full braking. Returning the endpoint asks for
        everything the car has, which is what a driver does.
        """
        accel = float(accel_mps2)
        if accel != accel:
            raise PedalMapError("pedal_for was asked for NaN acceleration")
        row = self.row_at(speed_mps)
        # `row` increases with pedal - `_check_axes` refused the table otherwise - so
        # interpolating pedal against accel is the inverse of `accel_for` on the same row.
        pedal = _interpolate(row, self.pedals, accel)
        return max(-1.0, min(1.0, pedal))

    # -- what it was measured on ---------------------------------------------------------

    def vehicle_notes(self, vehicle):
        """Lines describing how the live car differs from the one the table was measured on.

        Empty when they agree. Called once per episode rather than per step: the forces are
        sampled at construction from `pg_space.py:239-240` and do not change while driving.
        """
        expected = (self.measured or {}).get("vehicle") or {}
        notes = []
        for key, tolerance in (
            ("max_engine_force", FORCE_TOLERANCE),
            ("max_brake_force", FORCE_TOLERANCE),
            ("max_speed_km_h", FORCE_TOLERANCE),
            ("mass_kg", FORCE_TOLERANCE),
        ):
            want = expected.get(key)
            have = (vehicle or {}).get(key)
            if want is None or have is None:
                continue
            if abs(float(have) - float(want)) > tolerance * max(abs(float(want)), 1e-9):
                notes.append(
                    f"the pedal map was measured with {key} {float(want):.3f} and this car "
                    f"has {float(have):.3f}. It is a different car, and the table does not "
                    "describe it - re-run tools/pedal_sweep.py against this dataset."
                )
        return notes

    def summary(self):
        """One line for a log: the envelope the table covers."""
        low = self.accel_for(-1.0, 0.0)
        top = max(self.accel_for(1.0, v) for v in self.speeds_mps)
        coast = self.accel_for(0.0, self.speeds_mps[len(self.speeds_mps) // 2])
        return (
            f"{len(self.speeds_mps)} speeds x {len(self.pedals)} pedals, "
            f"{self.speeds_mps[0]:.1f}-{self.speeds_mps[-1]:.1f} m/s, "
            f"full brake {low:+.2f} m/s2, best throttle {top:+.2f}, coast {coast:+.3f}"
        )
