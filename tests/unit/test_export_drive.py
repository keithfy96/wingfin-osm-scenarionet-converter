"""What `--export-drive` has to keep true, pinned where a drive cannot pin it.

Three separate things went wrong on the way to a working export, and all three are silent
failures rather than errors -- which is why they are here rather than left to a drive to catch.

1. **The file has to open on the other machine.** The whole point of the flag is that the rig
   records and the laptop watches, and those are numpy 2.2 and numpy 1.24. A plain pickle of an
   ndarray written by the first cannot be read by the second.
2. **MetaDrive records nothing at `decision_repeat == 1`**, which is exactly what `--step-hz 100`
   produces -- the rate the openpilot bridge runs at. Measured before the fix: 3516 steps in,
   one frame out.
3. **A float32 timestamp is not 0.1.** MetaDrive stamps an exported scenario's `ts` as float32,
   so a 10 Hz drive reads back as 0.10000000149, and an absolute 1e-9 rate check refused it with
   a message reading "10 Hz against 10 Hz".
"""

import pickle
import sys
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))

import portable_pickle  # noqa: E402


class _Stub:
    """Somewhere to hang the patched `after_step` and the four attributes it reads."""

    def __init__(self, after_step):
        self.after_step = after_step.__get__(self, type(self))


class _Engine:
    record_episode = True
    global_config = {"decision_repeat": 1}


@pytest.fixture
def patched_after_step(monkeypatch):
    """`drive._record_every_step`'s replacement, without importing MetaDrive.

    Same reason as `test_conversion._metadrive_src` and `test_policy_client`: importing
    `metadrive` pulls in panda3d, and this suite runs on a machine that may not have a GL
    context. `test_conversion` already stubs `metadrive.utils.math` into `sys.modules` for the
    whole session, which is what made an `importorskip` here skip silently in the full run and
    pass on its own -- a pin that only holds when nobody else has run is not a pin.
    """
    import types

    record_manager = types.ModuleType("metadrive.manager.record_manager")

    class RecordManager:
        pass

    record_manager.RecordManager = RecordManager
    monkeypatch.setitem(sys.modules, "metadrive", types.ModuleType("metadrive"))
    monkeypatch.setitem(sys.modules, "metadrive.manager", types.ModuleType("metadrive.manager"))
    monkeypatch.setitem(sys.modules, "metadrive.manager.record_manager", record_manager)

    import drive

    drive._record_every_step()
    return RecordManager.after_step


def test_an_array_survives_the_round_trip_with_its_dtype_and_shape(tmp_path):
    """Not a list back. MetaDrive indexes these with tuples (`positions[:, :2]`)."""
    path = tmp_path / "scenario.pkl"
    payload = {"position": np.arange(12, dtype=np.float32).reshape(6, 2)}
    portable_pickle.dump(payload, path)

    with open(path, "rb") as handle:
        back = pickle.load(handle)

    assert isinstance(back["position"], np.ndarray)
    assert back["position"].dtype == np.float32
    assert back["position"].shape == (6, 2)
    assert np.array_equal(back["position"], payload["position"])


def test_the_stream_names_no_numpy_internal_module(tmp_path):
    """The actual failure, and the only property that makes the file portable.

    numpy 2 pickles an array as a reference to `numpy._core`, which numpy 1 does not have, so
    the file fails to *open* there with `ModuleNotFoundError` -- a message about pickle rather
    than about versions. Asserted against the bytes because that is where the name would be.
    """
    path = tmp_path / "scenario.pkl"
    portable_pickle.dump({"position": np.zeros((4, 2))}, path)
    raw = path.read_bytes()

    assert b"numpy._core" not in raw
    assert b"numpy.core" not in raw


def test_nothing_is_written_when_the_payload_cannot_be_pickled(tmp_path):
    """Buffered first, so a half-written dataset is never left behind to be driven."""
    path = tmp_path / "scenario.pkl"
    with pytest.raises((pickle.PicklingError, AttributeError, TypeError)):
        portable_pickle.dump({"handle": lambda: None}, path)
    assert not path.exists()


def test_the_record_manager_appends_a_frame_at_decision_repeat_one(patched_after_step):
    """The patch, against MetaDrive's own guard.

    `RecordManager.after_step` guards its append on `current_frame_count` being truthy, and that
    counter is only advanced from inside the engine's physics loop under `i < step_num - 1`
    (`base_engine.py:443`) -- never true when `step_num` is 1. `step_config` returns
    `decision_repeat` 1 at 100 Hz. So this is the pairing where the recorder silently keeps
    nothing, and the fix is to ask whether there is a batch rather than which tick filled it.
    """
    # On a stand-in rather than a real RecordManager: `engine` is a read-only property on
    # `BaseManager`, and what is under test is the guard, not the class.
    manager = _Stub(patched_after_step)
    manager.engine = _Engine()
    manager.episode_info = {"frame": []}
    manager.current_frames = ["one-tick"]
    manager.current_frame_count = 0
    manager.step = lambda *args, **kwargs: None

    manager.after_step()

    assert manager.episode_info["frame"] == [["one-tick"]]


def test_nothing_is_appended_before_the_first_step(patched_after_step):
    """`current_frames` is None between `after_reset` and the first `before_step`.

    Which is why the guard asks about the batch rather than simply dropping the old one: the
    reset frame is appended by `after_reset` and must not be appended twice.
    """
    manager = _Stub(patched_after_step)
    manager.engine = _Engine()
    manager.episode_info = {"frame": []}
    manager.current_frames = None
    manager.current_frame_count = 0

    manager.after_step()

    assert manager.episode_info["frame"] == []


@pytest.mark.parametrize(
    "sim_dt, data_dt, mismatched",
    [
        # What MetaDrive's own float32 `ts` reads back as for a 10 Hz drive. Not a mismatch.
        (0.1, float(np.float32(0.1)), False),
        (0.01, float(np.float32(0.01)), False),
        # The differences the check exists for: a 100 Hz simulator on a 10 Hz dataset.
        (0.01, 0.1, True),
        (0.1, 0.01, True),
    ],
)
def test_the_rate_check_ignores_float32_and_still_catches_a_real_mismatch(
    sim_dt, data_dt, mismatched
):
    """The tolerance in `drive.py`, stated where it can be argued with.

    Relative 1 ppm: at 0.1 s that is 1e-7, which covers float32's 1.5e-9 error and sits five
    orders of magnitude below the smallest mismatch worth refusing.
    """
    assert (abs(sim_dt - data_dt) > 1e-6 * max(sim_dt, data_dt)) is mismatched
