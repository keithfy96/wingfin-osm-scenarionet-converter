"""`tools/ros_encode.py` - the H.264 encoder behind the six `image_raw/ffmpeg` topics.

**This is the one module in the bag whose output cannot be checked by reading it.** Every other
topic is numbers, and every other test file in this directory compares one number against
another. A camera packet is an opaque byte string: a bag full of well-formed packets carrying
the wrong pixels opens, plays, renders, and passes every header check that could be written for
it. So every test here that matters runs the decoder and looks at the pictures that come back.

The three faults it is here to catch, all silent in the bytes:

* **the channels swapped.** MetaDrive's `get_rgb_array_cpu` returns BGR despite its name, so
  declaring the source `rgb24` mirrors red and blue on every frame in the bag. A person spots it
  instantly and a loss function never does.
* **the picture upside down**, which a flip anywhere in the path would produce and no field in
  `FFMPEGPacket` describes.
* **a delayed packet.** With B-frames or a lookahead the packet coming out of `encode()` belongs
  to an earlier frame than the one going in, so every stamp in the bag is off by a decision -
  and a delayed packet is a perfectly valid packet.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy
import pytest

TOOLS = Path(__file__).resolve().parents[2] / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import ros_encode  # noqa: E402
import ros_schema  # noqa: E402

WIDTH, HEIGHT, RATE = 128, 64, 10.0


def spec(name="front_left", frame_id="cam_left", width=WIDTH, height=HEIGHT):
    return ros_schema.CameraSpec(
        name=name, frame_id=frame_id, width=width, height=height, fov_deg=55.0
    )


def moving_picture(index):
    """A frame that is different from every other frame, and obviously so once decoded.

    A ramp in each of two channels plus a block that moves. Flat colour would decode to flat
    colour however badly the encoder were configured, which is exactly the test that proves
    nothing.
    """
    image = numpy.zeros((HEIGHT, WIDTH, 3), numpy.uint8)
    image[..., 0] = numpy.linspace(0, 255, WIDTH, dtype=numpy.uint8)[None, :]
    image[..., 1] = numpy.linspace(0, 255, HEIGHT, dtype=numpy.uint8)[:, None]
    image[8:24, 4 + index * 3 : 20 + index * 3, 2] = 255
    return image


def encode_frames(images, camera=None, **keywords):
    encoder = ros_encode.CameraEncoder(camera or spec(), RATE, **keywords)
    packets = [packet for image in images for packet in encoder.encode(image)]
    flushed = encoder.close()
    return encoder, packets, flushed


class TestOnePacketPerFrame:
    """The property `tune=zerolatency` buys, and the reason it is not a performance setting."""

    def test_every_frame_produces_exactly_one_packet_and_nothing_is_left_at_close(self):
        """A packet held back is a packet whose stamp belongs to a different step.

        `drive.py` gives each packet the stamp of the `env.step` that drew the picture going
        *in*. That is only true while the encoder is one-in-one-out. If it ever starts
        buffering, every camera stamp in every bag is a decision early and nothing raises -
        which is why this is a test rather than a comment.
        """
        encoder = ros_encode.CameraEncoder(spec(), RATE)
        counts = [len(encoder.encode(moving_picture(i))) for i in range(12)]
        assert counts == [1] * 12
        assert encoder.close() == ()

    def test_the_stream_opens_on_a_keyframe_and_keeps_one_every_second(self):
        """A reader that joins a bag mid-playback decodes nothing until the next keyframe.

        x264's own default GOP is 250, which at the rig's 10 Hz is 25 seconds of blank.
        """
        _, packets, _ = encode_frames([moving_picture(i) for i in range(25)])
        keyframes = [i for i, packet in enumerate(packets) if packet.keyframe]
        assert keyframes[0] == 0
        assert keyframes == [0, 10, 20]
        assert round(ros_encode.KEYFRAME_SECONDS * RATE) == 10

    def test_pts_counts_frames_from_zero(self):
        _, packets, _ = encode_frames([moving_picture(i) for i in range(6)])
        assert [packet.pts for packet in packets] == [0, 1, 2, 3, 4, 5]


class TestThePicturesSurviveTheRoundTrip:
    """Decode what was encoded and look at it. Nothing else here can see these faults."""

    def test_a_decoded_frame_is_the_frame_that_went_in(self):
        sources = [moving_picture(i) for i in range(12)]
        _, packets, _ = encode_frames(sources)
        decoded = ros_encode.decode([packet.data for packet in packets])
        assert len(decoded) == len(sources)
        worst = min(ros_encode.psnr(a, b) for a, b in zip(sources, decoded, strict=False))
        # A correct lossy encode at crf 23 sits in the forties. A swapped channel, a flip or a
        # stale buffer all collapse this into the teens, which is why one threshold covers
        # three faults.
        assert worst > 30.0, f"worst PSNR {worst:.1f} dB"

    def test_blue_stays_blue_rather_than_becoming_red(self):
        """`CameraRig.read()` hands over **BGR**, whatever `get_rgb_array_cpu` is called.

        Declaring the source `rgb24` is a one-word change that mirrors red and blue in every
        frame of every bag, and there is no field in `FFMPEGPacket` that would contradict it.
        """
        blue = numpy.zeros((HEIGHT, WIDTH, 3), numpy.uint8)
        blue[..., 0] = 255  # channel 0 is blue in BGR
        _, packets, _ = encode_frames([blue])
        decoded = ros_encode.decode([packet.data for packet in packets])[0]
        b, g, r = decoded.reshape(-1, 3).mean(axis=0)
        assert b > 200 and g < 30 and r < 30, f"blue went in, {b:.0f}/{g:.0f}/{r:.0f} came out"
        assert ros_encode.SOURCE_FORMAT == "bgr24"

    def test_the_top_of_the_picture_stays_on_top(self):
        """A vertical flip is invisible in every header and in the PSNR of a symmetric frame."""
        image = numpy.zeros((HEIGHT, WIDTH, 3), numpy.uint8)
        image[: HEIGHT // 2] = 255
        _, packets, _ = encode_frames([image])
        decoded = ros_encode.decode([packet.data for packet in packets])[0]
        assert decoded[: HEIGHT // 2].mean() > 200
        assert decoded[HEIGHT // 2 :].mean() < 55

    def test_a_held_buffer_re_encoded_does_not_come_back_bit_identical(self):
        """The held-frame fault, and **why `ros_probe` cannot test it with `==`**.

        If `drive.py` ever encoded the picture `frame_gate` held rather than a freshly drawn
        one, the packets would all still differ - inter-frame coding makes each repeat tiny and
        perfectly valid - so only the decoded pictures could show it. The obvious check is
        whether two consecutive pictures are equal, and **it does not work**: a keyframe and the
        P-frames after it quantise differently, so ten encodes of one identical source frame
        decode to ten slightly different pictures. Measured here, and it is why the probe uses a
        PSNR ceiling on the median instead.
        """
        moving = [moving_picture(i) for i in range(10)]
        held = [moving[0]] * 10

        def consecutive(sources):
            _, packets, _ = encode_frames(sources)
            decoded = ros_encode.decode([packet.data for packet in packets])
            repeats = sum(
                1 for a, b in zip(decoded, decoded[1:], strict=False) if numpy.array_equal(a, b)
            )
            gaps = sorted(
                ros_encode.psnr(a, b) for a, b in zip(decoded, decoded[1:], strict=False)
            )
            return repeats, gaps[len(gaps) // 2]

        moving_repeats, moving_median = consecutive(moving)
        held_repeats, held_median = consecutive(held)
        # Neither stream repeats a picture exactly, including the one that was literally the
        # same frame ten times. That is the finding.
        assert moving_repeats == 0 and held_repeats == 0
        # And the two are still cleanly separable, well either side of the probe's 40 dB.
        assert held_median > 45.0, f"held stream only reached {held_median:.1f} dB"
        assert moving_median < 35.0, f"moving stream reached {moving_median:.1f} dB"


class TestTheCompression:
    def test_a_packet_is_far_smaller_than_the_picture_it_carries(self):
        """The whole argument for `image_raw/ffmpeg` over `sensor_msgs/Image`."""
        encoder, packets, _ = encode_frames([moving_picture(i) for i in range(20)])
        raw = WIDTH * HEIGHT * 3
        assert encoder.bytes / encoder.packets < raw / 10
        assert all(packet.data for packet in packets), "an empty packet is not a picture"

    def test_a_lower_crf_costs_more_bytes(self):
        """`--ros-camera-crf` is x264's quality dial, and lower is bigger. A flag that is read
        but never applied would leave these equal."""
        sources = [moving_picture(i) for i in range(10)]
        coarse, _, _ = encode_frames(sources, crf=40)
        fine, _, _ = encode_frames(sources, crf=10)
        assert fine.bytes > coarse.bytes


class TestWhatItRefuses:
    """Each of these is a fault that would otherwise be discovered at the first decision frame
    of a drive, which is a recording already begun."""

    def test_an_odd_sized_camera_is_refused_by_name(self):
        with pytest.raises(ros_encode.RosEncodeError, match="front_left is 127x64"):
            ros_encode.CameraEncoder(spec(width=127), RATE)
        with pytest.raises(ros_encode.RosEncodeError, match="128x63"):
            ros_encode.CameraEncoder(spec(height=63), RATE)

    def test_a_picture_of_the_wrong_size_is_refused_rather_than_rescaled(self):
        encoder = ros_encode.CameraEncoder(spec(), RATE)
        with pytest.raises(ros_encode.RosEncodeError, match="wrong camera's buffer"):
            encoder.encode(numpy.zeros((HEIGHT + 2, WIDTH, 3), numpy.uint8))

    def test_a_float_picture_is_refused_because_it_would_encode_as_black(self):
        """`CameraRig.read(to_float=True)` returns 0..1 floats, which cast to uint8 are all
        zero. A bag of black frames is a bag that decodes, plays and teaches nothing."""
        encoder = ros_encode.CameraEncoder(spec(), RATE)
        with pytest.raises(ros_encode.RosEncodeError, match="to_float=True"):
            encoder.encode(numpy.zeros((HEIGHT, WIDTH, 3), numpy.float32))

    def test_a_rate_of_zero_is_refused(self):
        with pytest.raises(ros_encode.RosEncodeError, match="not a rate"):
            ros_encode.CameraEncoder(spec(), 0.0)

    def test_a_rig_with_no_vehicle_cameras_is_refused_rather_than_writing_nothing(self):
        with pytest.raises(ros_encode.RosEncodeError, match="RIG_CAMERA_NAMES"):
            ros_encode.RigEncoder((), RATE)

    def test_a_camera_that_stops_being_drawn_is_a_hole_rather_than_a_skip(self):
        rig = ros_encode.RigEncoder((spec(), spec("front_middle", "cam_front")), RATE)
        with pytest.raises(ros_encode.RosEncodeError, match="drew no cam_front"):
            rig.encode({"cam_left": moving_picture(0)})


class TestTheRigEncoder:
    def test_a_packet_is_labelled_by_the_rig_and_looked_up_by_the_spec(self):
        """The two names a camera has, and the one place they meet.

        `CameraRig.read()` is keyed by the **spec's** name (`cam_left`) and the topic is the
        **rig's** (`front_left`). Swapping the two would look up a picture that is not there or
        publish one under the wrong lens, and on `rigs/cams.txt` - where the labels and the
        geometry already disagree - a wrong lookup is not obvious from the pictures either.
        """
        rig = ros_encode.RigEncoder((spec(), spec("front_middle", "cam_front")), RATE)
        packets = rig.encode(
            {"cam_left": moving_picture(0), "cam_front": moving_picture(4)}
        )
        assert [p.name for p in packets] == ["front_left", "front_middle"]
        assert [p.frame_id for p in packets] == ["cam_left", "cam_front"]
        assert all(p.encoding == ros_encode.CODEC for p in packets)
        rig.close()

    def test_each_camera_keeps_its_own_stream(self):
        """One decoder holds the reference frames of exactly one camera. If the encoders shared
        a context, every packet would be coded against whichever camera went last."""
        left, front = spec(), spec("front_middle", "cam_front")
        rig = ros_encode.RigEncoder((left, front), RATE)
        by_name = {"front_left": [], "front_middle": []}
        sources = [moving_picture(i) for i in range(8)]
        for image in sources:
            for packet in rig.encode(
                {"cam_left": image, "cam_front": numpy.flip(image, axis=1).copy()}
            ):
                by_name[packet.name].append(packet)
        rig.close()
        decoded = ros_encode.decode([p.data for p in by_name["front_left"]])
        worst = min(ros_encode.psnr(a, b) for a, b in zip(sources, decoded, strict=False))
        assert worst > 30.0, f"worst PSNR {worst:.1f} dB - the streams are crossed"

    def test_the_bytes_and_packets_add_up_across_the_rig(self):
        rig = ros_encode.RigEncoder((spec(), spec("front_middle", "cam_front")), RATE)
        for index in range(5):
            rig.encode({"cam_left": moving_picture(index), "cam_front": moving_picture(index)})
        assert rig.packets == 10
        assert rig.bytes == sum(encoder.bytes for encoder in rig.encoders)
        assert rig.close() == ()
        assert rig.flushed == 0


class TestTheCodecItDeclares:
    def test_the_encoding_string_is_one_a_humble_decoder_resolves(self):
        """`ffmpeg_image_transport`'s decoder resolves `encoding` as an encoder *or* a codec
        name, and the humble-era one carried an explicit `libx264 -> h264` map. The newer
        four-token form (`codec;av_fmt;cv_fmt;ros_fmt`) is a master-branch feature that the
        humble decoder would take whole as a codec name and find none of."""
        assert ros_encode.CODEC == "libx264"
        assert ";" not in ros_encode.CODEC

    def test_the_encoder_this_repo_needs_is_checked_for_by_name(self):
        """The import succeeding is not the same as libx264 being present: a PyAV built from
        source against a distro ffmpeg without `--enable-libx264` imports perfectly."""
        ros_encode.refuse_if_unsupported()
        import av

        assert ros_encode.CODEC in av.codecs_available
