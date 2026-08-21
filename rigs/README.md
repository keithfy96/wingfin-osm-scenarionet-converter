# Camera rigs

A camera-rig spec describes the cameras a vehicle really carries — where each one sits, which
way it looks, how big its frame is. `--camera-rig` mounts them, so `sensor-survey.sh` records
what they see and `step-timing.sh` prices what they cost. Without one, both tools fall back to a
single camera the tool invented, which is not what any car has.

`cams.txt` is the seven-camera spec this project is built around: six 512×288 views and one
1280×720 forward wide. Its own numbers aim two of its four side cameras against their names —
`docs/scenario-datapoints.md` has the measurement; which pair is wrong is not ours to decide.

From inside `scripts/`:

```bash
./sensor-survey.sh mosque -- --camera-rig rigs/cams.txt
./step-timing.sh   mosque -- --camera-rig rigs/cams.txt
./container-check.sh mosque -- --camera-rig rigs/cams.txt
```

**One path, everywhere.** `scripts/_common.sh` cds to the repo root before a script does
anything, and the container bind-mounts the repo at `/work` and works from there, so
`rigs/cams.txt` is the same string whether it is typed in `scripts/`, at the root, or inside the
container. A spec kept *outside* the repo is the other case: set `RIG_DIR` in `.env` to the
directory holding it and it is `/rig/<name>.txt` in the container — see `docker/rig/README.md`.

**The format is CARLA's, and the conversion into MetaDrive's frame is not a rename** — an x/y
swap and a sign flip on yaw, with a non-zero `pitch` or `roll` refused rather than guessed.
`tools/camera_rig.py`'s module docstring is the reference; it carries the measurements the
conversion was derived from.

**`tick_rate` is read and not honoured, and a run says so.** Buffers redraw once per `env.step`
whatever the spec asks for, so a rig declaring 0.1 s draws every 0.01 s on a 100 Hz dataset.
Resampling is Phase 2 of `docs/implementation-plan/adjustable-simulation-sample-rate.md`.
