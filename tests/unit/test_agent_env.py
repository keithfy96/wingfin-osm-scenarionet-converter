"""`agent_env.ActionRecorder`, and the two shapes an observation arrives in.

With no graphics the observation is a flat 161-number vector. Under `--render offscreen`
MetaDrive swaps it for `ImageStateObservation`, which returns `{"image", "state"}` -- a
`(H, W, C, stack)` camera stack and a **41**-number state with no lidar block
(`image_obs.py:40`). Ravelling that dict is what made `--record --render offscreen` die with
`TypeError: float() argument must be a string or a real number, not 'dict'`, from well before
Phase B and right through it -- which is why the `to_host` in `record` sat on a path nothing
could reach, guarded by a test that could not fail.

These are the tests that could not exist while that was true. The env itself is out of reach
from pytest, for `test_policy_client.py`'s reason; the recorder is not, because it takes an
observation and a vehicle and asks nothing of an engine.
"""

import sys
from pathlib import Path

import numpy
import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))

from agent_env import ActionRecorder  # noqa: E402


class _Vehicle:
    """Only `current_action` is read, and only ever as a pair of floats."""

    def __init__(self, action=(0.3, -0.2)):
        self.current_action = action


class _FakeDeviceArray:
    """A CuPy stand-in: the interface, a `.get()`, and no host buffer.

    `numpy.asarray` on the real thing **raises** -- CuPy defines `__array__` to refuse the
    implicit PCIe round trip -- so this one raises the same way. Without that it is quietly
    accepted as a 0-d object array, measured, and the guard below would then be passing on a
    shape mismatch rather than on the fault it is written for.
    """

    def __init__(self, host):
        self._host = host
        self.__cuda_array_interface__ = {
            "shape": host.shape,
            "typestr": host.dtype.str,
            "data": (0x7F0000000000, False),
            "version": 3,
        }

    def __array__(self, dtype=None, copy=None):
        raise TypeError(
            "Implicit conversion to a NumPy array is not allowed. Please use `.get()` to "
            "construct a NumPy array explicitly."
        )

    def get(self):
        return self._host


def _stack(value=0.5, shape=(4, 6, 3, 3)):
    return numpy.full(shape, value, dtype=numpy.float32)


# ---------------------------------------------------------------------------------------
# The two observation shapes
# ---------------------------------------------------------------------------------------


def test_a_flat_observation_is_recorded_as_it_always_was():
    recorder = ActionRecorder()
    recorder.record(numpy.arange(161, dtype=numpy.float32), _Vehicle())
    written = recorder.save_arrays()
    assert written["observations"].shape == (1, 161)
    assert "images" not in written


def test_an_offscreen_observation_is_split_rather_than_ravelled():
    recorder = ActionRecorder()
    recorder.record(
        {"image": _stack(), "state": numpy.arange(41, dtype=numpy.float32)}, _Vehicle()
    )
    written = recorder.save_arrays()
    assert written["observations"].shape == (1, 41), "the state half, not the whole dict"
    assert written["images"].shape == (1, 4, 6, 3, 3), "the camera stack, kept"


def test_ravelling_the_dict_is_what_used_to_happen_and_would_still_raise():
    """The bug this file exists for, asserted directly so it cannot come back quietly."""
    observation = {"image": _stack(), "state": numpy.arange(41, dtype=numpy.float32)}
    with pytest.raises(TypeError):
        numpy.asarray(observation, dtype=numpy.float32).ravel()


# ---------------------------------------------------------------------------------------
# The image half: on the card, and 8-bit
# ---------------------------------------------------------------------------------------


def test_an_image_on_the_card_is_copied_to_the_host_rather_than_raising():
    """The assertion that could not be made before: this call site is live now.

    Without `to_host` in `record` this raises, which is what it did against every version of
    the recorder up to 2026-08-24 -- unobservably, because the dict never got that far.
    """
    recorder = ActionRecorder()
    recorder.record(
        {"image": _FakeDeviceArray(_stack()), "state": numpy.zeros(41, numpy.float32)},
        _Vehicle(),
    )
    assert recorder.save_arrays()["images"].shape == (1, 4, 6, 3, 3)


def test_a_normalised_image_survives_the_round_trip_through_uint8_exactly():
    """All 256 values, because the float was made by dividing a uint8 by 255 to begin with.

    `BaseCamera._format`'s `ret / 255` (`base_camera.py:208-214`) is what created it, so
    `round(x * 255)` is not a quantisation -- it is the inverse. Phase A measured the same
    thing one bus further along.
    """
    original = numpy.arange(256, dtype=numpy.uint8).reshape(16, 16)
    normalised = (original / 255.0).astype(numpy.float32)

    recorder = ActionRecorder()
    recorder.record({"image": normalised, "state": numpy.zeros(41, numpy.float32)}, _Vehicle())
    written = recorder.save_arrays()

    assert written["images"].dtype == numpy.uint8
    assert numpy.array_equal(written["images"][0], original)
    assert written["image_scale"] == pytest.approx(255.0)


def test_an_unnormalised_image_is_left_alone_because_it_is_not_a_picture():
    """A `PointCloudLidar` image source is float32 and unbounded -- metres, not pixels.

    `image_obs.py:73-74` gives it `Box(-inf, inf)` whatever `norm_pixel` says. Measured on a
    real drive it runs -18476.9 to +11030.2 m, so `round(x * 255).astype(uint8)` would not
    quantise it, it would destroy it.
    """
    cloud = numpy.array([[-18476.9, 11030.2, 0.0]], dtype=numpy.float32)
    recorder = ActionRecorder(normalised_images=False)
    recorder.record({"image": cloud, "state": numpy.zeros(41, numpy.float32)}, _Vehicle())
    written = recorder.save_arrays()

    assert written["images"].dtype == numpy.float32
    assert numpy.array_equal(written["images"][0], cloud)
    assert written["image_scale"] == pytest.approx(1.0)


def test_an_image_already_in_8_bit_is_not_scaled_again_and_says_so():
    """And `image_scale` reports 1.0, because nothing was divided to make this frame.

    `normalised_images=True` is the caller's belief about the env; the frame is the fact. A
    scale of 255 here would tell a reader to divide a picture that was never multiplied.
    """
    frame = numpy.arange(48, dtype=numpy.uint8).reshape(4, 4, 3)
    recorder = ActionRecorder()
    recorder.record({"image": frame, "state": numpy.zeros(41, numpy.float32)}, _Vehicle())
    written = recorder.save_arrays()
    assert numpy.array_equal(written["images"][0], frame)
    assert written["image_scale"] == pytest.approx(1.0)


def test_store_images_false_keeps_the_state_and_drops_the_frames():
    recorder = ActionRecorder(store_images=False)
    recorder.record({"image": _stack(), "state": numpy.zeros(41, numpy.float32)}, _Vehicle())
    written = recorder.save_arrays()
    assert written["observations"].shape == (1, 41)
    assert "images" not in written


# ---------------------------------------------------------------------------------------
# What lands on disk
# ---------------------------------------------------------------------------------------


def test_the_file_carries_the_scale_a_reader_needs_to_get_the_float_back(tmp_path):
    recorder = ActionRecorder()
    recorder.start_episode("junction-1-test")
    for _ in range(3):
        recorder.record({"image": _stack(), "state": numpy.zeros(41, numpy.float32)}, _Vehicle())

    path = tmp_path / "drive.npz"
    written = recorder.save(str(path))
    assert written["images"] == (3, 4, 6, 3, 3)
    assert written["observations"] == (3, 41)

    with numpy.load(str(path)) as loaded:
        assert set(loaded) >= {"observations", "actions", "images", "image_scale"}
        assert float(loaded["image_scale"]) == pytest.approx(255.0)
        recovered = loaded["images"][0] / float(loaded["image_scale"])
    assert recovered == pytest.approx(_stack(), abs=1 / 255)


def test_the_actions_key_keeps_its_name_because_something_reads_it(tmp_path):
    """`examples/policy_server.py:134` is `numpy.load(path)["actions"]` and is the only reader.

    The schema may grow -- it just did -- but that key may not move.
    """
    recorder = ActionRecorder()
    recorder.record(numpy.zeros(161, numpy.float32), _Vehicle((0.1, 0.2)))
    path = tmp_path / "drive.npz"
    recorder.save(str(path))
    with numpy.load(str(path)) as loaded:
        assert loaded["actions"].shape == (1, 2)


def test_nothing_recorded_writes_nothing(tmp_path):
    path = tmp_path / "drive.npz"
    assert ActionRecorder().save(str(path)) is None
    assert not path.exists()


def test_the_frames_are_released_once_they_are_stacked(tmp_path):
    """The list goes before anything compresses it, which is worth 16 MB of 364 -- measured.

    Not the halving it looks like: the frames are held twice whatever happens, because the
    destination array is allocated whole before the first frame can be freed. What this saves
    is holding them a third time while `savez_compressed` runs. See `_stacked_images`.
    """
    recorder = ActionRecorder()
    for _ in range(4):
        recorder.record({"image": _stack(), "state": numpy.zeros(41, numpy.float32)}, _Vehicle())
    assert len(recorder.images) == 4
    recorder.save_arrays()
    assert recorder.images == []
