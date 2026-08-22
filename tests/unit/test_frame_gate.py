"""`tools/frame_gate.py` - the schedule and the hold, without an engine.

Like `test_step_timing.py`, this reaches into `tools/` from this repo's 3.10 while the module
itself runs on MetaDrive's 3.8. What a real engine does with a gated render pass is measured by
running the sweep; what is reachable here is that the gate lets exactly the right frames
through, that a held step gives back the stack it already had, and that the proxy is a proxy.
"""

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))

import frame_gate  # noqa: E402
from drive import decides_on  # noqa: E402
from frame_gate import FrameGate, FrameGateError, _GatedTaskManager, install  # noqa: E402


class FakeTaskManager:
    """Panda3D's task manager, as far as this module is concerned: something with `step`."""

    def __init__(self):
        self.steps = 0
        self.globalClock = "the clock"  # noqa: N815 - panda3d's own spelling

    def step(self):
        self.steps += 1
        return "stepped"

    def add(self, task, name):
        return (task, name)

    def hasTaskNamed(self, name):  # noqa: N802 - panda3d's own spelling
        return name == "force_fps"


class FakeImageObservation:
    """`ImageObservation`: a stack, and an `observe` that pulls a frame and rolls it in."""

    def __init__(self):
        self.state = ["empty"]
        self.perceives = 0

    def observe(self, *arguments, **keywords):
        self.perceives += 1
        self.state = [f"frame {self.perceives}"]
        return self.state


class FakeEnv:
    def __init__(self, render_mode="offscreen", cuda=False, engine=True):
        self.config = {"_render_mode": render_mode, "image_on_cuda": cuda}
        self.image = FakeImageObservation()
        holder = type("Observation", (), {"img_obs": self.image})()
        self.agent_manager = type("Manager", (), {"observations": {"default": holder}})()
        self.observations = {"default": holder}
        self.engine = _FakeEngine() if engine else None
        self.resets = 0

    def reset(self, *arguments, **keywords):
        self.resets += 1
        return "reset"


class _FakeEngine:
    def __init__(self):
        self.task_manager = FakeTaskManager()


def _drive(env, gate, steps, stride):
    """What both timing loops do: gate, then step. `env.step` is MetaDrive's, so here the
    render call and the observation are exercised directly in the order the engine uses."""
    for step in range(steps):
        gate.before_step(decides_on(step, stride))
        env.engine.task_manager.step()
        env.image.observe()


def test_a_stride_of_five_draws_four_frames_in_twenty_steps():
    env = FakeEnv()
    gate = install(env)
    inner = env.engine.task_manager._inner
    _drive(env, gate, 20, 5)
    assert gate.draws == 4
    assert gate.held == 16
    assert inner.steps == 4
    # The camera is read exactly as often as it is drawn: a held step must not pull pixels.
    assert env.image.perceives == 4


def test_at_stride_one_every_step_draws():
    env = FakeEnv()
    gate = install(env)
    _drive(env, gate, 20, 1)
    assert (gate.draws, gate.held) == (20, 0)
    assert env.image.perceives == 20


def test_a_held_step_hands_back_the_stack_it_already_had():
    env = FakeEnv()
    gate = install(env)

    gate.before_step(True)
    drawn = env.image.observe()
    gate.before_step(False)
    held = env.image.observe()

    assert held is drawn
    assert held == ["frame 1"]
    assert env.image.perceives == 1


def test_the_draw_count_is_what_a_rate_is_worked_out_from():
    gate = FrameGate()
    gate.draws = 40
    assert gate.drawn_hz(2.0) == 20.0


def test_counting_starts_again_where_a_warm_up_ends():
    env = FakeEnv()
    gate = install(env)
    _drive(env, gate, 10, 1)
    gate.reset_counts()
    _drive(env, gate, 20, 5)
    assert (gate.draws, gate.held) == (4, 16)


def test_the_proxy_forwards_everything_that_is_not_the_render_call():
    inner = FakeTaskManager()
    proxy = _GatedTaskManager(inner, FrameGate())
    # `force_fps.py:33` reads this name, `main_camera.py` calls these two.
    assert proxy.globalClock == "the clock"
    assert proxy.add("task", "name") == ("task", "name")
    assert proxy.hasTaskNamed("force_fps") is True


def test_a_gated_call_returns_without_reaching_the_task_manager():
    inner = FakeTaskManager()
    gate = FrameGate()
    proxy = _GatedTaskManager(inner, gate)
    gate.before_step(False)
    assert proxy.step() is None
    assert inner.steps == 0
    gate.before_step(True)
    assert proxy.step() == "stepped"
    assert inner.steps == 1


@pytest.mark.parametrize("mode", ["none", "onscreen"])
def test_nothing_is_gated_where_there_is_no_offscreen_camera(mode):
    env = FakeEnv(render_mode=mode)
    assert install(env) is None
    # And the task manager is left exactly as it was.
    assert isinstance(env.engine.task_manager, FakeTaskManager)


def test_a_reset_puts_the_gate_back_to_drawing():
    env = FakeEnv()
    gate = install(env)
    gate.before_step(False)
    env.reset()
    assert gate.drawing is True
    assert env.resets == 1


def test_cuda_is_refused_by_name():
    env = FakeEnv(cuda=True)
    with pytest.raises(FrameGateError, match="image_on_cuda"):
        install(env)


def test_installing_before_the_engine_exists_is_refused():
    env = FakeEnv(engine=False)
    with pytest.raises(FrameGateError, match="env.reset"):
        install(env)


def test_the_offscreen_name_is_metadrives_own_where_there_is_one():
    assert frame_gate.offscreen_mode() == "offscreen"
