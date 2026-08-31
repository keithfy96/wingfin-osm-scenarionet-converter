"""Write a drive out as a ROS 2 bag, in the container the reference rig records.

    from ros_bag import BagWriter
    with BagWriter("bags/run-1", topics=None, notes={...}) as bag:
        bag.start_episode(scenario_id, route=[...], mounts={...})
        bag.write(frame)          # one Frame per env.step

**No ROS is installed or needed.** `rosbags` is a pure-Python implementation of the format;
`Stores.ROS2_HUMBLE` pins the message definitions to the distro `bag_audit.html` was recorded
under, so a simulated bag and a rig bag carry byte-identical schemas. `vision_msgs` and
`ffmpeg_image_transport_msgs` are not in that store and are registered at runtime from the
`.msg` text in `ros_schema.EXTRA_DEFINITIONS` - no package, no colcon, no build step.

**The container is MCAP with per-chunk zstd, and the mode matters.** Measured on a 16 MB bag:

    NONE      12 chunks, compression=<none>
    STORAGE   12 chunks, compression=zstd     <- what the rig's bag is
    FILE      one bag.mcap.zstd, index unreadable without inflating the whole file
    MESSAGE   larger than no compression at all

`FILE` is the one to never use. The rig's own audit reads 1,466,940 timestamps out of 7.41 GB
in 0.3 s by parsing the summary and the `MessageIndex` records **without decompressing a byte of
payload**; behind a file-level zstd that becomes a 7 GB inflate. The published `rosbags` docs
list only `file` and `message`, which is why this is written down - `STORAGE` exists in the
installed release and is the correct answer.

**Nothing here decides content.** Every message is built by `ros_schema`, which imports neither
this module nor MetaDrive, so the rules that are easy to get silently wrong stay testable
without a bag on disk.
"""

from __future__ import annotations

import json
from pathlib import Path

import ros_schema

#: What `bag_audit.html` measured, so a reader of our bag can compare like for like without
#: going back to the page. Not used in any calculation - it is provenance for a person.
REFERENCE_BAG = {
    "name": "ros2_mig_phase_5_p1",
    "bytes_per_camera_frame": 7159,
    "writer_latency_p99_ms": 2.24,
    "topics": 55,
}


class BagError(RuntimeError):
    """Refusals that must not be warnings: a hole in a bag is invisible afterwards."""


def _typestore():
    """The humble store, plus the two packages it has never heard of."""
    from rosbags.typesys import Stores, get_types_from_msg, get_typestore

    store = get_typestore(Stores.ROS2_HUMBLE)
    extra = {}
    for name, text in ros_schema.EXTRA_DEFINITIONS.items():
        extra.update(get_types_from_msg(text, name))
    store.register(extra)
    return store


def _coerce(store, spec, value):
    """One field of a message, from a plain Python value.

    Walks `typestore.fielddefs`' parse tree rather than the dataclass annotations, because the
    tree distinguishes a sequence of messages from a sequence of numbers and the annotations do
    not. Compared on `.name` rather than by importing `Nodetype`, which lives at an internal
    path that has moved between releases.
    """
    kind, detail = spec
    if kind.name == "BASE":
        typename, _ = detail
        if typename == "string":
            return str(value)
        if typename == "bool":
            return bool(value)
        if typename.startswith(("float",)):
            return float(value)
        return int(value)
    if kind.name == "NAME":
        return _message(store, detail, value)
    if kind.name in ("ARRAY", "SEQUENCE"):
        inner, _length = detail
        if inner[0].name == "NAME":
            return [_message(store, inner[1], item) for item in value]
        if inner[0].name == "BASE" and inner[1][0] == "string":
            return [str(item) for item in value]
        import numpy

        return numpy.asarray(value, dtype=numpy.dtype(inner[1][0]))
    raise BagError(f"unhandled field kind {kind!r}")


def _message(store, msgtype, content):
    cls = store.types[msgtype]
    _constants, fields = store.fielddefs[msgtype]
    try:
        return cls(**{name: _coerce(store, spec, content[name]) for name, spec in fields})
    except KeyError as missing:
        raise BagError(f"{msgtype} is missing field {missing}") from None


class BagWriter:
    """One bag, one drive.

    Counts what it wrote per topic rather than assuming the frame count. A builder that returns
    nothing - `gnss_fix_message` on a dataset with no projection - drops its topic for the whole
    drive, and a bag that silently held one fewer channel than expected is the kind of thing
    nobody notices until the training run is already wrong.
    """

    def __init__(self, path, topics=None, notes=None, chunk_bytes=None):
        self.path = Path(path)
        self.topics = topics
        self.notes = dict(notes or {})
        self.chunk_bytes = chunk_bytes
        self.counts: dict[str, int] = {}
        self.frames = 0
        self._writer = None
        self._store = None
        self._connections: dict[str, object] = {}

    # -- lifecycle ------------------------------------------------------------------
    def __enter__(self):
        from rosbags.rosbag2 import CompressionFormat, CompressionMode, StoragePlugin, Writer

        if self.path.exists():
            raise BagError(
                f"{self.path} already exists. A bag is a recording, not an output file to "
                "overwrite - move or delete it deliberately."
            )
        self._store = _typestore()
        writer = Writer(self.path, version=9, storage_plugin=StoragePlugin.MCAP)
        # STORAGE, never FILE. See the module docstring: FILE destroys the index-only read the
        # rig's own audit depends on, and MESSAGE is bigger than no compression at all.
        writer.set_compression(CompressionMode.STORAGE, CompressionFormat.ZSTD)
        writer.open()
        self._writer = writer
        return self

    def __exit__(self, *exc):
        if self._writer is None:
            return False
        self._writer.set_custom_data("wingfin", json.dumps(self.summary(), sort_keys=True))
        self._writer.close()
        self._writer = None
        return False

    # -- writing --------------------------------------------------------------------
    def _connection(self, topic, msgtype):
        found = self._connections.get(topic)
        if found is None:
            found = self._writer.add_connection(topic, msgtype, typestore=self._store)
            self._connections[topic] = found
        return found

    def _put(self, topic, msgtype, content, nanoseconds):
        message = _message(self._store, msgtype, content)
        raw = self._store.serialize_cdr(message, msgtype)
        self._writer.write(self._connection(topic, msgtype), nanoseconds, raw)
        self.counts[topic] = self.counts.get(topic, 0) + 1

    def start_episode(self, frame, route=(), mounts=None):
        """Write the latched topics: the route, and where the cameras are bolted on.

        Written once, at the first frame, because neither changes during an episode. The bag
        still carries a real stamp for them rather than zero, so a reader that sorts by time
        does not find them in 1970.
        """
        nanoseconds = _nanos(frame.sim_time_s)
        if route:
            content = ros_schema.route_message(
                ros_schema.Frame(
                    index=frame.index,
                    sim_time_s=frame.sim_time_s,
                    ego=frame.ego,
                    route=tuple(route),
                )
            )
            self._put(
                ros_schema.ROUTE,
                ros_schema.TOPICS[ros_schema.ROUTE][0],
                content,
                nanoseconds,
            )
        if mounts:
            content = ros_schema.tf_static_message(frame.sim_time_s, mounts)
            self._put(
                ros_schema.TF_STATIC,
                ros_schema.TOPICS[ros_schema.TF_STATIC][0],
                content,
                nanoseconds,
            )

    def write(self, frame):
        """One simulated step. Every topic of this frame shares one stamp, taken once."""
        nanoseconds = _nanos(frame.sim_time_s)
        for topic, msgtype, content in ros_schema.messages(frame, self.topics):
            self._put(topic, msgtype, content, nanoseconds)
        self.frames += 1

    # -- reporting ------------------------------------------------------------------
    def summary(self):
        """What this bag is, written into the bag itself.

        `source: simulated` and `noise_model: none` are the important half. Every GNSS and IMU
        channel here is ground truth with no noise, no lag, no multipath and no dropout, unlike
        the receiver the rig carries - whose own `/localization/odometry` skips 12.2% of its
        cycles. A model trained on both without being told learns to trust GNSS absolutely.
        """
        return {
            "source": "simulated",
            "generator": "wingfin-osm-scenarionet-converter stage 10",
            "noise_model": "none",
            "frames": self.frames,
            "messages_per_topic": dict(sorted(self.counts.items())),
            "reference_bag": REFERENCE_BAG,
            **self.notes,
        }


def _nanos(seconds):
    return round(seconds * 1e9)
