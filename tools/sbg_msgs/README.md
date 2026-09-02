# `sbg_driver` message definitions, verbatim

The nine SBG topics in the reference rig's bag are `sbg_driver` types, and that package is not
in `Stores.ROS2_HUMBLE`. These twelve files are the upstream `.msg` text, copied byte for byte
out of the public driver, and `ros_schema.EXTRA_DEFINITIONS` loads them at import.

    repository  https://github.com/SBG-Systems/sbg_ros2_driver
    tag         3.4.0
    commit      3efaf2982a3eacbbdcf6ff7ef40116a36fb3b2cc
    fetched     2026-09-02

Seven are the message types themselves - `SbgEkfNav`, `SbgEkfQuat`, `SbgEkfEuler`, `SbgImuData`,
`SbgGpsPos`, `SbgGpsVel`, `SbgUtcTime` - and five are the nested status submessages they name.

## Why they are files rather than strings in `ros_schema.py`

Verbatim has to be checkable. A file that is byte-identical to `msg/SbgEkfNav.msg` at that commit
can be diffed against upstream by anyone in one command; the same text re-typed into a Python
string literal, rewrapped to 99 columns, cannot - and rewrapping is exactly where a field changes
order.

The comments are kept because they are the only documentation of what each field means, and this
repo has to decide what to put in every one of them. **They do not travel into the bag**, which
was measured rather than assumed: `rosbags` regenerates the definition from its parsed typestore
when it writes a connection, so what `bags/phase2-sbg` carries is the field list alone, comments
stripped. That is enough for a decoder and it round-trips exactly - all seven types and all five
nested ones, checked field for field in `TestTheSbgFamily` - but a consumer wanting the prose
comes back here.

## The trap: these are version-dependent, and CDR does not check

**The field lists changed between releases, and a mismatch is silent.** Measured against 3.1.0:

| type | difference at 3.4.0 |
|---|---|
| `SbgGpsPosStatus` | 7 fields -> 22 (`ifm`, `spoofing`, `osnma`, and twelve more constellation flags) |
| `SbgEkfStatus` | 16 -> 23; `gps1_course_used` and `gps2_course_used` **removed**, nine added |
| `SbgUtcTime` | 11 -> 14 (`clk_bias_std`, `clk_sf_error_std`, `clk_residual_error`) |
| `SbgGpsPos` | 12 -> 13 (`num_sv_tracked`) |
| `SbgImuStatus` | 10 -> 11 (`imu_gyros_use_high_scale`) |

CDR carries no field names, so a subscriber built against 3.1.0 reading a 3.4.0 `SbgEkfStatus`
does not fail - it reads `dvl_bt_used` as `gps1_course_used` and carries on. The protection is
that rosbag2 writes these definitions into every bag we produce, so a self-describing reader
(`ros2 bag`, `rosbags`, Foxglove) decodes ours correctly whatever it has installed. A consumer
that instead uses its own `sbg_driver` package must be on a compatible one.

**Which version the rig recorded is not known.** `bag_audit.html` carries rates, not types, and
no bag off the rig has been read yet. 3.4.0 is the latest release, and the version is written
into every bag we produce as `sbg_driver_version` in its `wingfin` metadata - so the day a rig
bag does arrive, `tools/ros_defs.py` prints its definitions and the two can be compared instead
of assumed. If they differ, replace these files and bump the constant; nothing else changes.
