# Camera rigs

A camera-rig spec describes the cameras a vehicle really carries — where each one sits, which
way it looks, how big its frame is. `--camera-rig` mounts them, so `sensor-survey.sh` records
what they see and `step-timing.sh` prices what they cost. Without one, both tools fall back to a
single camera the tool invented, which is not what any car has.

`cams.txt` is the seven-camera spec this project is built around: six 512×288 views and one
1280×720 forward wide. Its own numbers aim two of its four side cameras against their names —
`docs/scenario-datapoints.md` has the measurement; which pair is wrong is not ours to decide.

`cams6.txt` is that same spec with the seventh camera removed, and it exists for one reason:
**seven cameras cannot be recorded beside a point cloud.** `--ros-lidar` adds an eighth image
buffer and above seven the depth buffer comes back at its far plane — 0.1–0.3% of rays in range
against 48.5–57.7% — with every shape, stamp and header still correct and nothing raising.
`drive.py` refuses the combination rather than writing a blind cloud. The dropped camera is the
right one to lose: `cam_front_wide` has no counterpart on the vehicle and therefore no rig topic
(`ros_schema.RIG_CAMERA_NAMES`), so a bag recorded with `cams6.txt` carries exactly the same six
`cam_sync_rig` channels as one recorded with `cams.txt`.

`av3.txt` is the AV3 model's own rig, generated from the checkpoint's `camera_order`. It is
already six cameras under the vehicle's own names, so it needs no row in `RIG_CAMERA_NAMES` and
also fits beside a cloud. `--model-checkpoint` implies it.

| | cameras | frame | `tick_rate` | with `--ros-lidar` |
|---|---|---|---|---|
| `cams.txt` | 7 | 6× 512×288 + 1× 1280×720 | 0.1 s → `--decision-hz 10` | **refused** — 8 buffers |
| `cams6.txt` | 6 | 512×288 | 0.1 s → `--decision-hz 10` | 7 buffers, the ceiling exactly |
| `av3.txt` | 6 | 512×384 | 0.05 s → `--decision-hz 20` | 7 buffers, the ceiling exactly |

**A spec's `tick_rate` has to match the interval the cameras are actually read at**, which is
`--decision-hz`, and the loader refuses a mismatch rather than resampling. That is why `av3.txt`
cannot be driven at the default 10 Hz: it needs `--step-hz 100 --decision-hz 20`, which is also
the rig's own camera rate.

**`cams.txt` is CRLF and the other two are LF.** Nothing reads it that cares, and it is left as it
is rather than reformatted, because every step-timing figure in the repo was measured against that
file.

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
