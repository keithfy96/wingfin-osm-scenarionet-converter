"""H.264 for the six `image_raw/ffmpeg` camera topics.

The one module in stage 11 that can be wrong in a way no header check sees. Every other topic
in the bag is a number a reader can look at; this one is a compressed byte string, and a bag
full of well-formed packets carrying the wrong pixels opens, plays, and renders. So the
decoder is here too, beside the encoder, and both `ros_probe.py` and `tests/unit/` use it to
read back what was written rather than to trust it.

    ros_schema.py   Frame -> messages        no MetaDrive, no rosbags   (unit tested)
    ros_encode.py   images -> CameraPacket   no MetaDrive, no rosbags   (unit tested, this file)
    ros_frame.py    env   -> Frame           needs a live engine
    ros_bag.py      Frame -> bag on disk     no MetaDrive

**Raw is not an option, and the ratio is why.** The rig writes 7,159 bytes for a camera frame.
An uncompressed `sensor_msgs/Image` at `rigs/cams.txt`'s 512x288 is 442,368 - **62x** - and six
of those at 20 Hz over the rig's own 780 s drive is ~41 GB against its measured 0.67 GB. The
encode costs about a millisecond a frame per camera, and that is the trade.

**The pictures are BGR, whatever the method is called.** `BaseCamera.get_rgb_array_cpu` returns
panda3d's RAM image with the rows flipped and the channels untouched, and MetaDrive's own
`get_image(mode="bgr")` returns it unchanged while `mode="rgb"` is the one that reverses
(`base_camera.py:110-113`). So `CameraRig.read()` hands over BGR, `from_ndarray(..., "bgr24")`
is the correct declaration, and getting it wrong swaps the red and blue channels of every frame
in the bag - a picture that is obviously wrong to a person and perfectly plausible to a loss
function. `tests/unit/test_ros_encode.py` encodes a pure blue frame and checks it comes back
blue.

**`tune=zerolatency` is load-bearing, not a performance setting.** It turns off B-frames and the
lookahead, so libx264 emits exactly one packet per input frame, in order, with `pts == dts`.
That is what lets a packet share the stamp of the `env.step` it was drawn in. With lookahead on,
the encoder holds frames back and the packet coming out of `encode()` belongs to an earlier
step than the frame going in - and nothing raises, because a delayed packet is a perfectly
valid packet. Measured over three presets, 20 frames each: one packet per call, every call, and
nothing left to flush at close.
"""

from __future__ import annotations

import ros_schema

#: libav's own name for the encoder, and the string written into `FFMPEGPacket.encoding`. See
#: `ros_schema.CameraPacket` for why it is this and not `h264` or the newer 4-token form.
CODEC = "libx264"

#: What libx264 is fed. `yuv420p` is the format every H.264 decoder in existence handles; the
#: 4:2:0 chroma subsampling is where most of the 62x comes from, and it is why the width and
#: height have to be even.
PIXEL_FORMAT = "yuv420p"

#: What `CameraRig.read()` hands over. See the module docstring - the name of MetaDrive's method
#: says RGB and the array is BGR.
SOURCE_FORMAT = "bgr24"

#: Measured on 512x288 over three presets: `veryfast` came out both smaller (1,277 vs 1,412
#: bytes a frame) and quicker (0.96 vs 1.59 ms) than `ultrafast`, and `medium` was worse than
#: either on both counts. `ultrafast` is the reflex choice for a real-time encode and is the
#: wrong one here.
PRESET = "veryfast"

#: x264's default, and a deliberate one: the bag is training data, not a preview. Measured 42.0
#: dB mean PSNR against the source frames on the synthetic pattern the unit tests use.
DEFAULT_CRF = 23

#: One keyframe a second. A bag is seeked into and played from the middle, and a reader that
#: joins between keyframes decodes nothing until the next one - x264's own default GOP of 250
#: would be 25 seconds of that at the rig's 10 Hz.
KEYFRAME_SECONDS = 1.0


class RosEncodeError(RuntimeError):
    """Refusals that must not be warnings, for the same reason `BagError`'s are."""


def refuse_if_unsupported():
    """Refuse before the drive starts, rather than three hundred frames in.

    Two separate things can be missing and they need different fixes. `av` is a dependency of the
    `ros` group; libx264 is compiled into the wheel's own ffmpeg, so its absence means a build of
    PyAV that this repo has not seen rather than a missing package.
    """
    try:
        import av
    except ImportError:
        import os

        in_container = os.path.exists("/.dockerenv")
        if in_container:
            raise RosEncodeError(
                "--ros-camera needs `av`, and this image does not carry it. A `uv sync` in "
                "here would not survive the container: rebuild the image on the host with "
                "`docker compose build`, after checking docker/Dockerfile syncs --group ros."
            ) from None
        raise RosEncodeError(
            "--ros-camera needs `av`, in the `ros` dependency group: uv sync --group sim "
            "--group ros (name every group you want - syncing one alone removes the others)."
        ) from None
    if CODEC not in av.codecs_available:
        raise RosEncodeError(
            f"this build of PyAV ({av.__version__}) has no {CODEC} encoder, so there is nothing "
            "to write `image_raw/ffmpeg` with. The wheels on PyPI carry it; a build from source "
            "against a system ffmpeg without --enable-libx264 does not."
        )


class CameraEncoder:
    """One camera's H.264 stream. Stateful across frames, which is the whole point of a codec.

    Created once per episode rather than once per drive: a new episode is a new scene, and
    restarting the stream is what makes its first frame a keyframe. Carrying one encoder across
    a reset would code the first frame of the new scene as a difference against the last frame
    of the old one - valid, tiny, and a picture of nothing that happened.
    """

    def __init__(self, camera, rate_hz, crf=DEFAULT_CRF, preset=PRESET):
        from fractions import Fraction

        import av

        if camera.width % 2 or camera.height % 2:
            raise RosEncodeError(
                f"{camera.name} is {camera.width}x{camera.height}, and {PIXEL_FORMAT} needs both "
                "even - its chroma planes are half size in each direction. Give the camera an "
                "even width and height in the rig spec."
            )
        if rate_hz <= 0:
            raise RosEncodeError(f"{camera.name}: a camera rate of {rate_hz} Hz is not a rate")
        self.camera = camera
        self.rate_hz = float(rate_hz)
        self.crf = int(crf)
        self.preset = preset
        self.packets = 0
        self.bytes = 0
        self._index = 0
        self._context = av.CodecContext.create(CODEC, "w")
        self._context.width = int(camera.width)
        self._context.height = int(camera.height)
        self._context.pix_fmt = PIXEL_FORMAT
        # The time base is the frame interval and `pts` is the frame counter, which is what
        # libx264 wants and what the upstream publisher writes. It is **not** the drive's clock:
        # `header.stamp` is, and it is the one every other topic in the same frame shares.
        rate = Fraction(self.rate_hz).limit_denominator(1000)
        self._context.time_base = 1 / rate
        self._context.framerate = rate
        self._context.gop_size = max(1, round(KEYFRAME_SECONDS * self.rate_hz))
        self._context.options = {
            "preset": self.preset,
            # See the module docstring. Without this the packet coming out belongs to an
            # earlier frame than the one going in, and the stamp beside it is a lie.
            "tune": "zerolatency",
            "crf": str(self.crf),
        }

    @property
    def gop_size(self):
        return self._context.gop_size

    def encode(self, image):
        """One drawn frame in, zero or more packets out. Normally exactly one.

        Returns a tuple rather than a single packet because the codec's contract allows it not
        to be one, and pretending otherwise would mean silently dropping a picture the day it
        stops being. `drive.py` writes whatever comes back and counts it.
        """
        import av
        import numpy

        array = numpy.asarray(image)
        want = (int(self.camera.height), int(self.camera.width), 3)
        if array.shape != want:
            raise RosEncodeError(
                f"{self.camera.name}: the rig drew {array.shape} and the spec says {want}. A "
                "camera cannot change size mid-drive, so this is the wrong camera's buffer."
            )
        if array.dtype != numpy.uint8:
            raise RosEncodeError(
                f"{self.camera.name}: the picture is {array.dtype}, not uint8 - `CameraRig.read()` "
                "was called with `to_float=True`, and 0..1 floats encode as a black frame."
            )
        frame = av.VideoFrame.from_ndarray(numpy.ascontiguousarray(array), format=SOURCE_FORMAT)
        frame.pts = self._index
        frame.time_base = self._context.time_base
        out = self._wrap(self._context.encode(frame))
        self._index += 1
        return out

    def close(self):
        """Flush. Measured empty for this preset and tune, and returned rather than assumed."""
        if self._context is None:
            return ()
        out = self._wrap(self._context.encode(None))
        self._context = None
        return out

    def _wrap(self, packets):
        out = []
        for packet in packets:
            data = bytes(packet)
            self.packets += 1
            self.bytes += len(data)
            out.append(
                ros_schema.CameraPacket(
                    name=self.camera.name,
                    frame_id=self.camera.frame_id,
                    width=int(self.camera.width),
                    height=int(self.camera.height),
                    encoding=CODEC,
                    pts=int(packet.pts if packet.pts is not None else self._index),
                    keyframe=bool(packet.is_keyframe),
                    data=data,
                )
            )
        return tuple(out)


class RigEncoder:
    """One encoder per rig camera that has a topic on the vehicle.

    Built from `ros_frame.cameras_from_rig`, which has already dropped the spec cameras with no
    counterpart on the rig - `rigs/cams.txt`'s `cam_front_wide`. That camera is still drawn and
    still in `/tf_static`; what it is not is one of `bag_audit.html`'s six, and encoding it would
    put a seventh `cam_sync_rig` channel in our bag that the rig's cannot have.
    """

    def __init__(self, cameras, rate_hz, crf=DEFAULT_CRF, preset=PRESET):
        if not cameras:
            raise RosEncodeError(
                "--ros-camera needs a rig with at least one camera the vehicle also has. Every "
                "camera in this spec is unmapped, so there is no `cam_sync_rig` channel to "
                "write - see ros_schema.RIG_CAMERA_NAMES for the six names it recognises."
            )
        self.encoders = tuple(
            CameraEncoder(camera, rate_hz, crf=crf, preset=preset) for camera in cameras
        )
        self.rate_hz = float(rate_hz)
        self.flushed = 0

    @property
    def packets(self):
        return sum(encoder.packets for encoder in self.encoders)

    @property
    def bytes(self):
        return sum(encoder.bytes for encoder in self.encoders)

    @property
    def gop_size(self):
        return self.encoders[0].gop_size

    def encode(self, images):
        """Every camera's packet for one decision frame, keyed by the spec's own camera name.

        `images` is `CameraRig.read()`'s dict, whose keys are the **spec** names, while the topic
        is decided by the rig name. `CameraSpec` carries both and this is the one place the two
        meet, which is why it looks the picture up by `frame_id` and labels it by `name`.
        """
        out = []
        for encoder in self.encoders:
            picture = images.get(encoder.camera.frame_id)
            if picture is None:
                raise RosEncodeError(
                    f"the rig drew no {encoder.camera.frame_id}; it has "
                    f"{sorted(images)}. A camera that stops being read mid-drive is a hole in "
                    "the bag, not a frame to skip."
                )
            out.extend(encoder.encode(picture))
        return tuple(out)

    def close(self):
        out = []
        for encoder in self.encoders:
            out.extend(encoder.close())
        self.flushed = len(out)
        return tuple(out)


def decode(packets, width=None, height=None):
    """An ordered stream of packet payloads back to BGR arrays, for a check rather than for use.

    Used by `ros_probe.py` on a written bag and by the unit tests on the encoder's own output.
    It is the only thing that can catch the fault this module exists to avoid, so it is
    deliberately not a debugging aid: nothing else here has to be right for it to say the
    pictures are wrong.

    `packets` is an iterable of `bytes` in the order they were written - one camera's stream,
    never six interleaved, because a decoder holds the reference frames of exactly one.
    """
    import av
    import numpy

    context = av.CodecContext.create("h264", "r")
    if width and height:
        context.width, context.height = int(width), int(height)
    out = []
    for index, payload in enumerate(packets):
        packet = av.Packet(payload)
        packet.pts = index
        for frame in context.decode(packet):
            out.append(frame.to_ndarray(format=SOURCE_FORMAT))
    for frame in context.decode(None):
        out.append(frame.to_ndarray(format=SOURCE_FORMAT))
    return [numpy.asarray(frame) for frame in out]


def psnr(original, decoded):
    """Peak signal-to-noise ratio in dB, or `inf` for a lossless match.

    The one number that says whether a decoded frame is the frame that went in. A swapped colour
    channel, a vertical flip and a stale buffer all show up here as a collapse to the low
    teens, where a correct lossy encode sits in the forties.
    """
    import numpy

    a = numpy.asarray(original, dtype=numpy.float64)
    b = numpy.asarray(decoded, dtype=numpy.float64)
    if a.shape != b.shape:
        raise RosEncodeError(f"cannot compare {a.shape} against {b.shape}")
    error = float(numpy.mean((a - b) ** 2))
    if error == 0.0:
        return float("inf")
    return float(10.0 * numpy.log10(255.0 * 255.0 / error))
