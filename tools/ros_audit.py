"""Audit a bag the way `bag_audit.html` audits the rig's: from the index, touching no payload.

    ./scripts/ros-bag.sh --audit bags/junction-1-sim-001
    uv run python tools/ros_audit.py bags/junction-1-sim-001

This is a deliberate re-implementation of the method that page describes, against our own bags:

    Parse the MCAP summary section for channels, chunk indexes and statistics, then read
    every MessageIndex record - this yields all 1,466,940 log timestamps without
    decompressing a byte of payload (0.3 s for the whole 7.41 GB file).

**Re-implemented rather than reused, and that is the point.** If this runs on a simulated bag
and produces the same shape of report, the container is provably the same as the rig's - not
merely readable by the library that happened to write it. It is the one check here that does not
depend on `rosbags` being correct, and it is the reason `ros_bag.py` must use
`CompressionMode.STORAGE`: behind a file-level zstd there is no index to read and this tool has
nothing to say.

Like the host that produced `bag_audit.html`, this imports **no** mcap library and no ROS. It
reads the bytes.

MCAP opcodes, for the record layout below: 0x01 Header, 0x02 Footer, 0x03 Schema, 0x04 Channel,
0x05 Message, 0x06 Chunk, 0x07 MessageIndex, 0x08 ChunkIndex, 0x0B Statistics, 0x0F DataEnd.
"""

from __future__ import annotations

import argparse
import struct
import sys
from collections import defaultdict
from pathlib import Path

MAGIC = b"\x89MCAP0\r\n"

OP_SCHEMA = 0x03
OP_CHANNEL = 0x04
OP_CHUNK = 0x06
OP_MESSAGE_INDEX = 0x07
OP_CHUNK_INDEX = 0x08
OP_STATISTICS = 0x0B
OP_DATA_END = 0x0F


class _Cursor:
    """Just enough of MCAP's primitive types to walk a record without decoding a message."""

    def __init__(self, buffer, offset=0):
        self.buffer = buffer
        self.offset = offset

    def u16(self):
        value = struct.unpack_from("<H", self.buffer, self.offset)[0]
        self.offset += 2
        return value

    def u32(self):
        value = struct.unpack_from("<I", self.buffer, self.offset)[0]
        self.offset += 4
        return value

    def u64(self):
        value = struct.unpack_from("<Q", self.buffer, self.offset)[0]
        self.offset += 8
        return value

    def text(self):
        length = self.u32()
        value = self.buffer[self.offset : self.offset + length].decode("utf-8", "replace")
        self.offset += length
        return value

    def skip_bytes(self):
        length = self.u32()
        self.offset += length


def records(blob):
    """Every top-level record as `(opcode, body)`, skipping over chunk payloads entirely."""
    if blob[: len(MAGIC)] != MAGIC:
        raise ValueError("not an MCAP file: bad magic")
    offset = len(MAGIC)
    end = len(blob) - len(MAGIC)
    while offset + 9 <= end:
        opcode = blob[offset]
        length = struct.unpack_from("<Q", blob, offset + 1)[0]
        body = blob[offset + 9 : offset + 9 + length]
        yield opcode, body
        offset += 9 + length


def read(path):
    """Channels, per-channel timestamps, chunk compression, all without inflating a payload."""
    path = Path(path)
    if path.is_dir():
        found = sorted(path.glob("*.mcap"))
        if not found:
            zstd = sorted(path.glob("*.mcap.zstd"))
            if zstd:
                raise ValueError(
                    f"{zstd[0].name} is a whole-file zstd bag - written with "
                    "CompressionMode.FILE, which has no readable index. Re-record with "
                    "CompressionMode.STORAGE."
                )
            raise ValueError(f"no .mcap file in {path}")
        path = found[0]

    blob = path.read_bytes()
    topics: dict[int, str] = {}
    schemas: dict[int, str] = {}
    channel_schema: dict[int, int] = {}
    stamps: dict[int, list[int]] = defaultdict(list)
    chunks = 0
    compressions: set[str] = set()
    compressed = uncompressed = 0

    for opcode, body in records(blob):
        cursor = _Cursor(body)
        if opcode == OP_SCHEMA:
            schema_id = cursor.u16()
            schemas[schema_id] = cursor.text()
        elif opcode == OP_CHANNEL:
            channel_id = cursor.u16()
            channel_schema[channel_id] = cursor.u16()
            topics[channel_id] = cursor.text()
        elif opcode == OP_CHUNK:
            chunks += 1
            cursor.u64()  # message_start_time
            cursor.u64()  # message_end_time
            uncompressed += cursor.u64()
            cursor.u32()  # uncompressed_crc
            compressions.add(cursor.text() or "<none>")
            compressed += cursor.u64()
        elif opcode == OP_MESSAGE_INDEX:
            channel_id = cursor.u16()
            length = cursor.u32()
            stop = cursor.offset + length
            while cursor.offset < stop:
                stamps[channel_id].append(cursor.u64())
                cursor.u64()  # offset into the chunk - not needed, and not followed
    return {
        "file": path,
        "bytes": len(blob),
        "chunks": chunks,
        "compression": compressions,
        "compressed_bytes": compressed,
        "uncompressed_bytes": uncompressed,
        "topics": {topics[cid]: sorted(values) for cid, values in stamps.items() if cid in topics},
        "types": {topics[cid]: schemas.get(sid, "?") for cid, sid in channel_schema.items()},
    }


def rates(timestamps):
    """Message count, span and mean rate. Empty and single-message channels are latched."""
    if len(timestamps) < 2:
        return len(timestamps), 0.0, None
    span = (timestamps[-1] - timestamps[0]) / 1e9
    return len(timestamps), span, (len(timestamps) - 1) / span if span > 0 else None


def gaps(timestamps):
    """Intervals in milliseconds, for the histogram `bag_audit.html` draws."""
    return [(b - a) / 1e6 for a, b in zip(timestamps, timestamps[1:], strict=False)]


def report(path, out=sys.stdout):
    found = read(path)
    ratio = (
        found["uncompressed_bytes"] / found["compressed_bytes"]
        if found["compressed_bytes"]
        else 1.0
    )
    print(f"{found['file']}", file=out)
    print(
        f"  {found['bytes'] / 1e6:.2f} MB on disk · {found['chunks']} chunks · "
        f"compression={sorted(found['compression']) or ['<none>']} · "
        f"payload {found['uncompressed_bytes'] / 1e6:.2f} MB · {ratio:.2f}x",
        file=out,
    )
    total = sum(len(v) for v in found["topics"].values())
    print(f"  {total} messages across {len(found['topics'])} channels\n", file=out)
    print(f"  {'channel':<40} {'msgs':>7} {'rate Hz':>9} {'median ms':>10}  type", file=out)
    for topic in sorted(found["topics"]):
        stamps = found["topics"][topic]
        count, _span, rate = rates(stamps)
        intervals = sorted(gaps(stamps))
        median = intervals[len(intervals) // 2] if intervals else None
        print(
            f"  {topic:<40} {count:>7} "
            f"{'latched' if rate is None else f'{rate:9.2f}':>9} "
            f"{'-' if median is None else f'{median:10.2f}':>10}  {found['types'].get(topic, '?')}",
            file=out,
        )
    return found


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("bag", help="a rosbag2 directory, or the .mcap inside one")
    arguments = parser.parse_args(argv)
    found = report(arguments.bag)
    if found["compression"] and found["compression"] != {"zstd"}:
        print(
            f"\n  NOTE: chunks are {sorted(found['compression'])}, not zstd - the reference "
            "bag is zstd-compressed per chunk.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
