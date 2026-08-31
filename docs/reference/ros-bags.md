# ROS 2 bags out of a drive - what runs, and what it measured

What Stage 10 does today, with the numbers. The plan and the reasoning are in
`docs/implementation-plan/stage-10-ros-bags-out-of-a-drive.md`; this file is the measurements,
so nothing here has to be re-derived to answer "what did it actually do".

Everything below was produced by running it on 2026-08-31, against
`workspaces/junction-1/scenarionet-10hz` and `bag_audit.html`'s audit of the rig's own
`ros2_mig_phase_5_p1`.

## Running it

```bash
./scripts/ros-bag.sh junction-1 -- --out bags/junction-1-001
./scripts/ros-bag.sh --audit bags/junction-1-001
uv run python tools/ros_probe.py bags/junction-1-001 --workspace workspaces/junction-1
```

Or straight at `drive.py`, which is what the script wraps:

```bash
uv run python tools/drive.py workspaces/junction-1/scenarionet-10hz \
    --render none --ros-bag bags/junction-1-001
```

The four modules and what each may import:

| | imports MetaDrive | imports rosbags | unit tested |
|---|---|---|---|
| `tools/ros_schema.py` | no | no | yes - 32 tests |
| `tools/ros_frame.py` | yes | no | the pure parts |
| `tools/ros_bag.py` | no | yes | via the probe |
| `tools/ros_audit.py` | no | **no** - reads the bytes | via the probe |

## The bag it wrote

A default replay drive of junction-1, 364 frames:

```
0.97 MB on disk · 32 chunks · compression=['zstd'] · payload 34.98 MB · 39.44x
3641 messages across 11 channels
```

Every channel at **10.00 Hz, 100.00 ms median interval**. 11 rather than 12 because `/tf_static`
is only written when a `--camera-rig` is mounted.

| | this bag | the rig's `ros2_mig_phase_5_p1` |
|---|---|---|
| container | MCAP · zstd · 32 chunks | MCAP · zstd · 2,165 chunks |
| chunk size | ~1.15 MB (the writer's default) | ~3.4 MB |
| messages | 3,641 | 1,466,940 |
| channels | 11 | 55 |
| compression | 39.4x | 1.42x |
| ground-truth objects | 300 ids, 51-257 boxes a frame | none |

**The compression ratios are not comparable and the difference is not a win.** The rig's payload
is 87.3% lidar; ours is small structured messages with a great deal of repetition, which zstd
eats. Once cameras land, the bulk becomes already-compressed ffmpeg packets and the ratio will
fall toward the rig's.

## What the probe measured

All 10 checks, on the bag above:

| check | result |
|---|---|
| every per-frame topic holds the same number of messages | 364 each, 10 topics |
| time strictly increasing, no repeated frame | 364 messages, 364 distinct stamps |
| every frame's topics share one identical stamp | **364 of 364** |
| `/tf` and `/localization/odometry` agree | worst **0.00e+00** |
| heading points along the direction of travel | worst **0.0 deg** over 363 moving frames |
| twist is in the car's frame and matches its motion | worst 0.30 m/s |
| objects were labelled | 300 ids; `CYCLIST`, `PEDESTRIAN`, `TRAFFIC_BARRIER` |
| every box has a real size | pass |
| the fix moves with the car | lat 3.184159..3.186394, lon 101.611005..101.612379 |
| every fix lands on the map | **364 of 364** within 50 m of the OSM extent |

Each is a *relationship between two independently produced quantities*, not a value against a
constant, because every fault in this area produces a bag that opens and renders and is wrong.

## The tape bound

`--agent-policy idm --extra-seconds 40` on junction-1:

```
824 of 824 steps (379 recorded frames at 0.1 s)
note: 824 steps against a 379-frame recording. 251 recorded pedestrian(s) and
      cyclist(s) were removed there (cones and barriers stay).
ros bag      379 frames ... stopped at step 379 of 824
```

**445 of 824 frames - 54% - would have shown a junction emptier than the scenario has.** They
would not have been mislabelled; the boxes still match the pixels. They are unrepresentative,
and training on them teaches a model this junction has no people in it.
`--ros-bag-past-tape` keeps them.

## Compression modes, measured on a 16 MB bag

| mode | chunks | chunk compression | file |
|---|---|---|---|
| `NONE` | 12 | `<none>` | `bag.mcap` |
| **`STORAGE`** | 12 | **`zstd`** | `bag.mcap` |
| `FILE` | - | - | `bag.mcap.zstd`, index unreadable without inflating |
| `MESSAGE` | 12 | per message | **larger than no compression** |

`STORAGE` is not in the published `rosbags` docs, which list only `file` and `message`. It is in
the installed release and it is the only one that matches the rig.

## What the datasets actually hold

Read out of the pickles, not assumed:

| | `mosque/scenarionet-10hz` | `junction-1/scenarionet-10hz` |
|---|---|---|
| pedestrians / cyclists / static | 2 / 1 / 1 cone | 202 / 49 / 49 barriers |
| traffic lights (`dynamic_map_states`) | **0** | **0** |
| `coordinate_system_wkt` | yes | yes |

junction-1 has a `signals/signals.json` that was never converted in. Until it is,
`--lights` is refused by `scripts/ros-bag.sh` rather than recording an empty channel:

```bash
uv run osm-scenario convert -w workspaces/junction-1 --config config/default.yaml \
    --routes workspaces/junction-1/routes/routes.json \
    --signals workspaces/junction-1/signals/signals.json \
    --actors workspaces/junction-1/actors/actors.json
```

Convert-time arguments are deliberately not `ConverterConfig` fields, so that run does not move
`generation_fingerprint` and the Stage 3 review keeps applying.

## The rig's 55 topics against what MetaDrive can produce

**24 direct, 21 synthesisable, 10 impossible without fabricating.** The full table is in the
plan doc. The fifteen omitted for want of a `.msg` definition are listed in code, in
`ros_schema.MISSING_DEFINITIONS`, with what each needs - they are omitted rather than published
with a substitute type, because a subscriber expecting `wingfin_msgs/VehicleState` fails on a
`geometry_msgs/TwistStamped` wearing that topic name, which is worse than an absent topic.

## Traps

- **`CompressionMode.FILE` destroys the index-only read** the rig's own audit depends on.
- **`metadata.get("old_origin_in_current_coordinate") or (0.0, 0.0)` raises.** It is a numpy
  array and a two-element array has no truth value. Skipping the shift instead is worse: 93.8 m
  on junction-1, on a road that really exists.
- **`Camera.position` is in MetaDrive's ego frame, not the CARLA spec** the rig file is parsed
  from (`camera_rig.py:130`). x is RIGHT, y is FORWARD, `+heading` is LEFT. ROS `base_link` is x
  forward, y left, `+yaw` left - so forward and lateral swap, the lateral one negates, and the
  yaw sign is already correct and must be left alone.
- **Two attributes name a kind:** `TYPE_NAME` on traffic participants, `CLASS_NAME` on static
  objects. Reading only the first gives `TrafficBarrier` where the dataset says
  `TRAFFIC_BARRIER`.
- **`map.osm` may use single quotes.** junction-1's was hand-edited in JOSM, so a `<bounds>`
  regex written for `"` silently skips the check rather than failing it.
- **A GNSS fix can legitimately land outside the OSM extent** - 10 m past `minlat` on junction-1,
  because osmnx clips ways while lane geometry is offset outward. The containment check pads by
  50 m: wide enough for the overhang, far under the 93.8 m error it exists to catch.
- **`rosbags` needs Python 3.10** and `drive.py` runs on `METADRIVE_PYTHON`, which is 3.8 on the
  host. The container is one 3.10 interpreter and it works there;
  `ros_frame.refuse_if_unsupported()` says so before the terrain is built.
