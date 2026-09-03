# `point_cloud_interfaces` — the compressed point cloud

One file, `CompressedPointCloud2.msg`, recovered from the vehicle's own recording:

    uv run python tools/ros_defs.py bags/074143 \
        --write tools/point_cloud_interfaces --package point_cloud_interfaces

Public (`point_cloud_transport`), BSD-3-Clause, read out of the bag for the same reason the FLIR
one was: the bag is what the vehicle serialised against.

## `soa+zstd`, decoded rather than guessed

The type carries a free-text `format` field and the vehicle's value for it is the literal string
`'soa+zstd'`. Nothing documents what that means, so one real message was pulled out of
`bags/074143` and read:

    frame_id     livox_link
    height/width 1 / 45216          point_step 26      is_dense true
    fields       x y z intensity  float32
                 tag line         uint8
                 timestamp        float64
    payload      428,440 B compressed -> 1,175,616 B  (2.7x, and width*point_step exactly)

**The layout is a per-field byte-plane transpose, then one zstd frame over the whole thing.** For
a `w`-byte field, `w` planes of `n` bytes, plane `k` holding byte `k` of every point's value.

It is *not* plain structure-of-arrays, and the difference is not visible from the sizes — both
readings are the same total length. What gives it away is that reading it as either plain SoA or
plain AoS produces garbage floats while the **one-byte** fields come out clean, which is exactly
what a byte-plane shuffle does: a 1-byte field has one plane and is unaffected.

De-shuffled it is unambiguous: `x ∈ [0, 184.9] m` (forward-looking), `intensity ∈ [0, 255]`
exactly, `line ∈ {0..5}` for a six-line Livox, and the per-point timestamps spanning 100 ms —
one sweep at the 10 Hz the ledger records for the topic.

## The frame differs from ours

The vehicle publishes in `livox_link`; this repo's cloud is in `lidar` (`ros_schema.LIDAR_FRAME`,
against `RIG_LIDAR_FRAME`). Recorded rather than silently reconciled — see `NAME_DRIFT`.
