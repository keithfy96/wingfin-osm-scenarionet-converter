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
