# calibration

Measurements of the **simulated car**, not of a map and not of a workspace. One file so far.

## `metadrive-pedal-map.json`

What acceleration MetaDrive's `DefaultVehicle` produces for every pedal at every speed, and
the inverse of that, which is what a controller planning in m/s² actually needs.

```bash
# from inside scripts/
./pedal-sweep.sh junction-1

# then, for the openpilot bridge
uv run python examples/openpilot_server.py --backend bridge --longitudinal table --port 8642
```

Written by `tools/pedal_sweep.py`; read by `tools/pedal_map.py`, and through it by
`examples/openpilot_server.py --longitudinal table`. Nothing else reads it.

## It is not a controller, and the bridge already had one of these

Nothing here decides anything. The chain is four stages and this is the third:

```
model      → waypoints                     where to go
controller → accel_cmd m/s², steer deg     how hard, which way   (the openpilot bridge)
pedal map  → throttle_brake in [-1, 1]     how far to press, on THIS car
MetaDrive  → force on four wheels
```

`PedalMap` has no target, no error term, no memory and no feedback: it is a pure
`(accel, speed) -> pedal`, and `pedal_for(accel_for(p)) == p` exactly. That is the test for
whether something is a controller, and it fails it in every direction.

**The bridge's own last stage is this same component**, which is the clearest evidence of
what it is. `server.py:801` reports openpilot's decision as
`"accel_cmd": float(self._last_actuators.accel)` — an acceleration in m/s² — and
`server.py:788-792` then does exactly two conversions before replying, side by side: a
road-wheel angle into a normalised steer, and `accel_to_carla(self._last_actuators.accel,
v_ego)` into a throttle and a brake. Both are properties of the *car*. `accel_map.py` is a
speed-indexed measured table with an interpolation, a coast crossover and a low-speed special
case — the same shape as this file — and `coast_accel`'s own docstring says what kind of thing
it is: *"Realized accel at zero control (engine braking), from the measured col 0."*

So this did not add a control stage. It **re-measured the bridge's output adapter for a
different car**: theirs is a Tesla Model 3 in CARLA Town10HD, ours is MetaDrive's
`DefaultVehicle`.

**Steering needed no equivalent** because that conversion is geometry and came out free:
`action[0] × max_steering` *is* the road-wheel angle in degrees (`base_vehicle.py:478`), and
the branch `carla_steer_curvature_gain: 0.0` selects emits `-road_wheel_deg / max_steer_angle`
with both sides at 40°, so it cancels. Pedal to acceleration is not geometry — it depends on
mass, engine force, brake force and drag — so it has to be measured.

**Why it exists.** The openpilot bridge plans in m/s² and converts to pedals with
`accel_map.py`, two 8×11 tables from a *"Town10HD calibration sweep on Tesla M3 @ 20 Hz
sync"*. Their zero crossing is the CARLA Tesla's own zero-throttle deceleration — **−1.582
m/s² above 10 m/s** — and MetaDrive's car coasts at **−0.364**. So every request to slow down
more gently than the CARLA car's own drag came back as *throttle*: 137 of 201 on `junction-1`,
and the car accelerated from 13.9 to 20.5 m/s and left the road.

## Why here, and not somewhere else

**Not `config/`.** `configuration_checksum` feeds `generation_fingerprint`
(`generation.py:2212`), so a file there invites the question of whether it moves the Stage 3
review even when it does not — the same reason signal timing and the render flags are not
config fields either.

**Not `workspaces/<ws>/`.** The table describes the *vehicle*, and MetaDrive spawns the same
`DefaultVehicle` on every map. Measured on both extracts at both rates, `max_engine_force` and
`max_brake_force` came out **identical** — 759.464 and 89.464 — because they are sampled from
`BoxSpace(750, 850)` / `BoxSpace(80, 180)` (`pg_space.py:239-240`) with the scenario index as
the seed, and each of our datasets holds one scenario. A workspace-shaped file would imply a
difference that is not there.

**Not `rigs/`.** That is camera specs. Repo-relative like `rigs/cams.txt`, though, and for
the same reason: the path is then the same string on the host and inside the container.

## Reading the file

`pedal_map_version` gates it — a reader refuses any other version by name rather than
mis-reading a later layout. `measured` records what it was taken on: the dataset, the step
rate, the vehicle's own mass and forces, and `speed_drift_mps`, which is how far the car moved
away from a row's own speed while a cell was being held. `sample_counts` is how many simulator
steps landed in each cell, and **zero means the cell was filled from the nearest measured
speed rather than measured** — which happens only at the bottom of the table, because a
stationary car cannot be measured braking.

The vehicle block is checked at the start of every episode. A car whose forces differ is a
different car, and the run says so rather than driving on a table that does not describe it.
