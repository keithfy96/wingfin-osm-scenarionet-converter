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


# --- stopping a drive early ------------------------------------------------------------------
#
# `--export-drive` writes the recording once, after the drive loop. Everything before that lives
# in MetaDrive's `RecordManager`, so a Ctrl-C used to run the `finally` that closes the engine and
# take the whole recording with it. That matters because **a car that stalls never terminates**:
# `terminated` and `truncated` stay false, the loop steps to its budget, and on an AV3 drive the
# budget is fifty minutes. Ctrl-C was the only way out, and it was also the way to lose the run.
#
# Three things have to hold, and none of them can be checked by a drive:
#   1. The stop is a *flag*, not an exception, so the loop leaves at a frame boundary.
#   2. The second Ctrl-C still ends the process, by *exiting* rather than raising. It used to
#      put Python's own handler back, so the next signal was a `KeyboardInterrupt` from
#      wherever the process stood -- under `--render 3D` that is inside panda3d's C++ render
#      call, and unwinding a half-drawn frame segfaulted, taking the nvidia driver's VA space
#      with it (`NVRM: ... GPU_IN_FULLCHIP_RESET`, card gone until a reboot). 2026-08-28.
#   3. A re-export replaces the previous one rather than merging into it.


def _sigint(handler_owner):
    """Deliver a SIGINT to whatever handler is currently installed, without a real signal.

    A real `os.kill` would work here and is worse: pytest runs this suite in-process, and a
    handler that failed to be restored would then take the runner down rather than this test.
    """
    import signal as signal_module

    handler = signal_module.getsignal(signal_module.SIGINT)
    handler(signal_module.SIGINT, None)
    return handler_owner


def test_the_first_ctrl_c_sets_the_flag_and_does_not_raise():
    """The whole point: the loop has to reach its own next iteration to leave cleanly."""
    drive = pytest.importorskip("drive")

    said = []
    with drive._EarlyClose(lambda: said.append("stopping")) as closing:
        assert not closing.asked
        _sigint(closing)
        assert closing.asked

    assert said == ["stopping"], "the handler is what reports; a slow step would look dead"


def test_the_second_ctrl_c_exits_and_does_not_raise():
    """The escape hatch. A graceful stop that cannot itself be interrupted is a worse bargain.

    It has to *exit*, though, not raise. `os._exit` is stubbed here for the obvious reason --
    the real one would end pytest -- and the assertion is that it was reached at all, with no
    exception in flight. An exception is what the previous version did, and see the banner.
    """
    drive = pytest.importorskip("drive")

    codes, said = [], []
    with drive._EarlyClose(on_kill=lambda: said.append("exiting")) as closing:
        closing._exit = codes.append
        _sigint(closing)
        assert closing.asked
        _sigint(closing)  # raises nothing: reaching the next line is half the assertion

    assert codes == [130], "128 + SIGINT, what a shell reports for a process ended by Ctrl-C"
    assert said == ["exiting"], "the user pressed it twice; silence is why they press a third"


def test_the_first_ctrl_c_does_not_put_the_raising_handler_back():
    """The mechanism, separately from its effect.

    Restoring the previous handler is what made the second Ctrl-C a `KeyboardInterrupt`. If
    this ever goes back to `restore()`, the exit test above still passes -- it calls the
    handler that is installed, whatever it is -- and the segfault comes back. So assert on
    which handler is armed, not only on what it does.
    """
    import signal as signal_module

    drive = pytest.importorskip("drive")

    before = signal_module.getsignal(signal_module.SIGINT)
    with drive._EarlyClose() as closing:
        _sigint(closing)
        armed = signal_module.getsignal(signal_module.SIGINT)
        assert armed is not before, "the raising handler is back; a 2nd Ctrl-C would throw"
        assert armed == closing._kill

    # And the original is still what is left behind, so Ctrl-C outside the loop -- during
    # `env.close()`, or at a prompt -- keeps meaning exactly what it always meant.
    assert signal_module.getsignal(signal_module.SIGINT) is before


def test_the_previous_handler_is_restored_even_when_the_drive_raises():
    """A handler is process-global; a drive that dies must not leave Ctrl-C meaning nothing."""
    import signal as signal_module

    drive = pytest.importorskip("drive")

    before = signal_module.getsignal(signal_module.SIGINT)
    with pytest.raises(RuntimeError), drive._EarlyClose():
        assert signal_module.getsignal(signal_module.SIGINT) is not before
        raise RuntimeError("the drive fell over")
    assert signal_module.getsignal(signal_module.SIGINT) is before

    # And `restore` twice is not an error: the drive loop's `finally` calls it, and so does the
    # context manager the tests use.
    closing = drive._EarlyClose().install()
    closing.restore()
    closing.restore()
    assert signal_module.getsignal(signal_module.SIGINT) is before


def test_arming_the_exit_covers_the_teardown_that_the_loop_does_not():
    """The dangerous window is not the loop -- it is `env.close()`, after the loop.

    Measured on 2026-08-28: hammering SIGINT at 50 ms for a whole `--render none` drive ended
    **6 of 6** runs in a `KeyboardInterrupt` out of
    `base_object.detach_from_physics_world -> bullet_world.remove(node)`, reached through
    `env.close() -> close_engine -> clear_stored_maps`. Under `--render 3D` that same unwind
    also has panda3d's GL context in it, which is why it segfaulted rather than printing.

    The loop's own flag cannot help there: the loop is already over. So the drive arms the
    exit handler itself on the way into the teardown, whether or not anyone pressed anything,
    and only restores the original once `env.close()` has returned.
    """
    import signal as signal_module

    drive = pytest.importorskip("drive")

    before = signal_module.getsignal(signal_module.SIGINT)
    closing = drive._EarlyClose().install()
    try:
        assert not closing.asked, "no signal has arrived; this is the drive arming it, not a press"
        closing.arm_exit()
        assert signal_module.getsignal(signal_module.SIGINT) == closing._kill

        codes = []
        closing._exit = codes.append
        _sigint(closing)
        assert codes == [130], "a press during env.close() must exit, not raise into bullet"
    finally:
        closing.restore()
    assert signal_module.getsignal(signal_module.SIGINT) is before


def test_the_loop_leaves_before_its_next_step_so_the_last_frame_is_whole():
    """The invariant behind `assert frames[-1].episode_step == episode_len - 1`.

    `convert_recorded_scenario_exported` refuses an episode whose last frame is half appended
    (`metadrive/scenario/utils.py:143`), and MetaDrive appends in `after_step`. So the flag has
    to be read at the *top* of the loop. Modelled here rather than asserted about, because the
    ordering is the whole of the fix: a check after the step would record frame 4 and export 3.
    """
    drive = pytest.importorskip("drive")

    frames = []
    with drive._EarlyClose() as closing:
        for step in range(10):
            if closing.asked:
                break
            frames.append(step)          # stands in for env.step -> after_step -> append
            if step == 3:
                _sigint(closing)

    assert frames == [0, 1, 2, 3], "the step that was running when Ctrl-C landed is kept, whole"


def test_only_the_files_a_drive_export_wrote_are_ever_owned(tmp_path):
    """`--export-drive` takes a *directory*, which is the argument a person mistypes."""
    drive = pytest.importorskip("drive")

    for name in ("dataset_summary.pkl", "dataset_mapping.pkl", "sd_wingfin_drive_Map-0.pkl"):
        (tmp_path / name).write_bytes(b"x")
    (tmp_path / "routes.json").write_text("{}")
    (tmp_path / "notes.md").write_text("mine")

    owned, foreign = drive._export_files(tmp_path)

    assert owned == ["dataset_mapping.pkl", "dataset_summary.pkl", "sd_wingfin_drive_Map-0.pkl"]
    assert foreign == ["notes.md", "routes.json"]


def test_a_missing_directory_is_neither_owned_nor_in_the_way(tmp_path):
    """`workspaces/<ws>/drives/` does not exist the first time, and that is not a refusal."""
    drive = pytest.importorskip("drive")

    assert drive._export_files(tmp_path / "never-driven") == ([], [])


def test_a_re_export_leaves_no_scenario_the_summary_does_not_list(tmp_path):
    """The fault the old "refuses a non-empty directory" rule existed to prevent.

    `dataset_summary.pkl` names only what the run that wrote it exported. A second, *shorter*
    drive into the same directory - which stopping early makes the ordinary case - would leave
    the first drive's `sd_*.pkl` beside a summary that does not list them: a dataset that reads
    as smaller than it is, and drives as one.
    """
    drive = pytest.importorskip("drive")

    # A long first drive.
    for name in ("sd_wingfin_drive_Map-0.pkl", "sd_wingfin_drive_Map-1.pkl"):
        (tmp_path / name).write_bytes(b"first")
    (tmp_path / "dataset_summary.pkl").write_bytes(b"first")
    (tmp_path / "dataset_mapping.pkl").write_bytes(b"first")
    keep = tmp_path / "README.md"          # nothing else in there is touched
    keep.write_text("hand-written")

    replaced = drive._clear_export(tmp_path)
    # A short second drive, Ctrl-C'd out of its first scenario.
    (tmp_path / "sd_wingfin_drive_Map-0.pkl").write_bytes(b"second")
    (tmp_path / "dataset_summary.pkl").write_bytes(b"second")
    (tmp_path / "dataset_mapping.pkl").write_bytes(b"second")

    assert replaced == 4
    owned, foreign = drive._export_files(tmp_path)
    assert owned == ["dataset_mapping.pkl", "dataset_summary.pkl", "sd_wingfin_drive_Map-0.pkl"]
    assert foreign == ["README.md"] and keep.read_text() == "hand-written"


def test_the_owned_names_are_metadrives_own():
    """Two constants are copied into `drive.py`, and a copy needs the original to be pinned to.

    They are written out rather than imported so the precheck can run before MetaDrive is
    imported at all -- importing it pulls in panda3d, and the refusal it feeds is about a
    directory. Read here out of the **checkout's source text** rather than by importing it:
    MetaDrive is deliberately not a dependency of this repo, so an `importorskip` would skip
    every time and pin nothing. `test_conversion._metadrive_src` is the same reasoning applied
    to the schema, and its `skipif` is why this asserts the file was found rather than skipping
    when it was not - a moved checkout must fail here, not go quiet.
    """
    drive = pytest.importorskip("drive")
    from test_conversion import METADRIVE_SRC

    source = (METADRIVE_SRC / "scenario" / "scenario_description.py").read_text()
    for constant, value in (
        ("SUMMARY_FILE", drive._EXPORT_SUMMARY),
        ("MAPPING_FILE", drive._EXPORT_MAPPING),
    ):
        assert f'{constant} = "{value}"' in source, f"MetaDrive renamed {constant}"

    # And the third shape, which is a *pattern* rather than a constant: every scenario file
    # `extract_dataset_summary_and_mapping` writes goes through `SD.get_export_file_name`,
    # which builds `sd_<dataset>_<version>_<id>.pkl`. That prefix and suffix are what
    # `_export_files` matches on.
    assert '"sd_{}_{}_{}.pkl"' in source
