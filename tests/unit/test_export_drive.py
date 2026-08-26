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
4. **A replayed car never leaves the height it spawned at when `decision_repeat` is 1**, which
   is what `--step-hz 100` produces. Every dataset here carries z = 0, physics is what normally
   lifts the car to its 0.537 m ride height, and the first physics call after a teleport moves
   the body by exactly nothing -- so when every call follows a teleport, the car is drawn half a
   ride height under the road. Also only visible in a window.
5. **The timestamps MetaDrive writes are 0.1 s a frame at every rate.** That one reached a
   person: a 100 Hz export claimed to be 10 Hz, so it replayed at 10 Hz, and the replay policy
   sets the recorded *velocity* along with the recorded position -- the body coasted 1.26 m
   between frames and was teleported back 1.13 m, once a frame, over a recorded line whose own
   step distance is 0.109 m and whose heading barely moves. Keith watched that and reported a
   car spiking back and forth. It is the only one of the four that a numeric check could not
   have caught, because the wrong number made the rate check *agree*.
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


@pytest.mark.parametrize("sim_dt", [0.01, 0.1, 0.02])
def test_the_export_stamps_the_rate_the_drive_actually_ran_at(sim_dt):
    """`drive.py` overwrites MetaDrive's timestep array. This is the arithmetic it uses.

    MetaDrive cannot be asked for this: `convert_recorded_scenario_exported` raises on any
    `scenario_log_interval` but 0.1 and stamps `0.1 * i` regardless. Correcting the output is
    the only route, and the spacing is what `data_step_seconds` reads to decide whether a
    replay's rate matches the file's.

    float32 because that is the dtype MetaDrive wrote and a reader should find no difference
    but the values -- and it is why the rate check next door is a relative tolerance.
    """
    frames = 353
    stamps = np.asarray([sim_dt * index for index in range(frames)], dtype=np.float32)

    assert stamps.dtype == np.float32
    assert len(stamps) == frames
    spacing = float(stamps[1]) - float(stamps[0])
    assert abs(spacing - sim_dt) <= 1e-6 * sim_dt
    # And the round trip a caller actually makes: spacing back to a whole-number rate.
    assert round(1.0 / spacing, 6) == pytest.approx(1.0 / sim_dt, rel=1e-5)


def test_settling_stops_when_the_car_stops_moving():
    """`_settle_on_the_road`'s loop: run physics until z is still, then leave it alone.

    Not a physics test -- a stopping test. The loop exists because Bullet will not integrate a
    body on the first call after it has been teleported, so the settling has to happen at reset
    with no teleports in it, and it has to end on its own rather than on a fixed count.
    """
    drive = pytest.importorskip("drive")

    class Agent:
        def __init__(self):
            self.origin = self
            self.z = 0.0

        def getZ(self):
            return self.z

    class Engine:
        def __init__(self, agent):
            self.agent = agent
            self.ticks = 0

        def step_physics_world(self):
            self.ticks += 1
            # Rises, then stops -- the shape the real suspension has.
            self.agent.z = min(0.539, self.agent.z + 0.02)

    class Env:
        pass

    env = Env()
    env.agent = Agent()
    env.engine = Engine(env.agent)

    was, now, ticks = drive._settle_on_the_road(env)

    assert was == 0.0
    assert now == pytest.approx(0.539)
    # It must stop shortly after the height stops changing, not run to the 2000 bound.
    assert ticks < 100
    assert env.engine.ticks == ticks


def test_settling_is_bounded_when_the_car_never_settles():
    """A car that will not settle must not hang a drive that is otherwise fine."""
    drive = pytest.importorskip("drive")

    class Agent:
        def __init__(self):
            self.origin = self
            self.z = 0.0

        def getZ(self):
            return self.z

    class Engine:
        def __init__(self, agent):
            self.agent = agent

        def step_physics_world(self):
            self.agent.z += 1.0  # never converges

    class Env:
        pass

    env = Env()
    env.agent = Agent()
    env.engine = Engine(env.agent)

    _, _, ticks = drive._settle_on_the_road(env)

    assert ticks == 2000


# --- `/work/...` typed outside the container -----------------------------------------------
#
# The sixth thing, and the one Keith hit: the flag was *documented* with an absolute container
# path, `/work/workspaces/junction-1/drives/rig`. It works, but only in the container, so the
# command could not be carried to the rig and back unchanged -- and typed on a host it fails at
# `os.makedirs("/work")` with a `Permission denied` that names neither cause nor fix. A
# repo-relative path is one string everywhere, which is the property `rigs/cams.txt` already
# has, so the refusal's job is to hand that string over rather than merely to decline.


@pytest.fixture
def not_in_container(monkeypatch):
    import env_hint

    monkeypatch.setattr(env_hint, "in_container", lambda: False)


@pytest.fixture
def inside_container(monkeypatch):
    import env_hint

    monkeypatch.setattr(env_hint, "in_container", lambda: True)


def test_a_container_path_on_a_host_is_refused_with_the_path_to_use(not_in_container):
    drive = pytest.importorskip("drive")

    refusal = drive._container_path_refusal("/work/workspaces/junction-1/drives/rig")

    assert refusal is not None
    # The point of the message is the answer, not the complaint: the corrected string must be
    # in there, ready to be copied, and it must not still carry the /work.
    assert "--export-drive workspaces/junction-1/drives/rig" in refusal
    assert "/work/workspaces" not in refusal.split("Drop the /work/")[1]


def test_the_same_path_is_fine_inside_the_container(inside_container):
    """`/work/...` is not wrong in there -- it is just not the spelling that travels."""
    drive = pytest.importorskip("drive")

    assert drive._container_path_refusal("/work/workspaces/junction-1/drives/rig") is None


def test_an_ordinary_path_is_never_refused(not_in_container):
    """Including an absolute one, and including one that merely starts with the same letters."""
    drive = pytest.importorskip("drive")

    assert drive._container_path_refusal("workspaces/junction-1/drives/rig") is None
    assert drive._container_path_refusal("/home/keith/drives/rig") is None
    assert drive._container_path_refusal("/workspaces/rig") is None
