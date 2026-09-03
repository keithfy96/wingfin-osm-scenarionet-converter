# `flir_camera_msgs` — the camera driver's per-frame metadata

One file, `ImageMetaData.msg`, recovered from the vehicle's own recording:

    uv run python tools/ros_defs.py bags/074143 \
        --write tools/flir_camera_msgs --package flir_camera_msgs

This is a **public** package (the FLIR/Spinnaker ROS driver), not the vehicle's. It was read out
of the bag anyway, because the bag is what the vehicle actually serialised against and a release
fetched from upstream is a guess about which release that was.

## Nothing publishes it, and that is the decision

The six `/sensing/camera/cam_sync_rig/<camera>/meta` topics are excluded from the coverage
target — `_META_REASON` in `ros_schema.py`. The message is `camera_time`, `brightness`,
`exposure_time`, `max_exposure_time` and `gain`: **facts of a physical image sensor**, and a
rendered frame has none of them. Publishing a plausible exposure would say the simulated rig has
an aperture and a shutter, on exactly the reasoning that already excludes
`/sensing/gnss/imu/temp`.

The definition is kept because the exclusion has to be checkable — a reader asking *why* these
six are absent can see what would have had to be invented.

What the simulated cameras genuinely do and do not model is stated in `camera_info` instead, by
an empty `distortion_model` and an empty `d`, which is ROS's way of saying no distortion is
modelled rather than that one was measured and came out zero.
