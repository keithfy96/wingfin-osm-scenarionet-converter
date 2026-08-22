"""Draw the cameras at the decision rate instead of once per world tick.

    from frame_gate import install

    gate = install(env)                      # None unless this env renders offscreen
    ...
    for step in range(budget):
        deciding = decides_on(step, stride)
        if gate is not None:
            gate.before_step(deciding)
        observation, *_ = env.step(action)

`--decision-hz` already gates the policy call, the numeric sensor read and `CameraRig.read`.
It did **not** gate the draw, so at `100/20/100` the cameras were still redrawn a hundred
times a simulated second and merely *looked at* twenty - a 100 Hz camera read at 20 Hz, not a
20 Hz camera. This module makes the middle column mean what it says.

**The seam is the name, not the object.** `base_engine.py:65` is
`self.task_manager = self.taskMgr`, a plain instance attribute onto panda3d's global task
manager, and `task_manager.step()` is called in exactly two places - `base_engine.py:455`
(inside the substep loop, onscreen `ForceFPS` only) and `:458`, the one commented
`#  Do rendering`. Every other render in MetaDrive reaches the same object through `taskMgr`:
`base_engine.py:761`, `base_env.py:439` (the pause loop), `base_env.py:534,569` and
`base_engine.py:394` (reset's own frames), `main_camera.py:504`, and `base_camera.py:188,193`,
which is `perceive(new_parent_node=...)`'s second pass. So rebinding `engine.task_manager` to a
forwarding proxy gates the per-step render **and nothing else**, and a frame is still drawn
everywhere one has to be. One `task_manager.step()` is one `graphicsEngine.renderFrame()`
(`ShowBase.__igLoop`), so a call that does not happen is a frame that is not drawn - no camera
buffer, no tonemap quad, no shadow split.

**Why this and not `buffer.set_active(False)`, which MetaDrive uses itself at
`dashboard.py:130` and `engine_core.py:215`.** That was built here first and measured at 1% of
a 26 ms step with `is_active()` confirmed `False` on all seven cameras, and the reason is that
**an `RGBCamera` owns two GraphicsOutputs and `self.buffer` is the cheap one**:
`rgb_camera.py:38-52` builds a `FilterManager(self.buffer, self.cam)` and calls
`render_scene_into(...)` with `set_multisamples(16)`, which creates a second buffer
(`direct/filter/FilterManager.py:325-328`) hosted by the first. The scene - terrain, PBR, that
16x MSAA on top of the global 8x at `engine_core.py:96-103` - is drawn into *that* one, and
`self.buffer` only draws a fullscreen quad over the result. Deactivating `self.buffer` switches
off the quad and leaves the scene rendering. Reaching the real buffer would mean touching a
private `FilterManager` per camera class; gating the render pass costs nothing to know.

**The read-back is held too, and that is not an optimisation but the same fact.**
`ImageObservation.observe` (`image_obs.py:80-88`) calls `perceive` - which with no parent node
renders nothing and only pulls pixels, `image_buffer.py:105`,
`buffer.getDisplayRegion(1).getScreenshot()`, a synchronous GPU->CPU read - then `np.roll`s the
whole stack and writes the frame into its last slot. On a step where nothing was drawn that
pulls the same pixels again and rolls a duplicate into the stack. A 20 Hz camera produces a
frame every 50 ms and the observation carries the last one in between, which is exactly
`return self.state` unchanged.

**Only the image half is held.** `ImageStateObservation.observe` (`image_obs.py:39-40`)
composes `{"image": ..., "state": ...}` and the 41-number state stays fresh every step: a
vehicle state is not a camera.

Nothing in the MetaDrive checkout is edited, and nothing is patched onto a MetaDrive class -
every binding here is on one env's own instances, so two envs in one process cannot interfere.
The same shape as `drive._set_line_width` and `drive._keep_line_ends`.
"""

import math


def offscreen_mode():
    """MetaDrive's own name for the offscreen render mode.

    Read out of the package where there is one and fall back to the literal otherwise, so this
    module is importable - and testable - on an interpreter that has no MetaDrive. `tools/`
    runs on MetaDrive's 3.8; this repo's tests run on 3.10, where the `sim` dependency group is
    opt-in. The same reason `test_conversion._metadrive_src` falls back to the installed
    package rather than the checkout.
    """
    try:
        from metadrive.constants import RENDER_MODE_OFFSCREEN
    except ImportError:
        return "offscreen"
    return RENDER_MODE_OFFSCREEN


class FrameGateError(Exception):
    """The gate cannot be installed on this env, and the caller must be told rather than
    quietly given a camera running at a rate it did not ask for."""


class _GatedTaskManager:
    """Panda3D's task manager with `step()` gated. Every other attribute forwards.

    Forwarding matters: `force_fps.py:33` reads `.globalClock` off this name and
    `main_camera.py` calls `.add` / `.remove` / `.hasTaskNamed` on it.
    """

    def __init__(self, inner, gate):
        self._inner = inner
        self._gate = gate

    def step(self, *arguments, **keywords):
        gate = self._gate
        if not gate.drawing:
            return None
        gate.draws += 1
        return self._inner.step(*arguments, **keywords)

    def __getattr__(self, name):
        # Only reached for names this proxy does not define, and `_inner` is bound first in
        # `__init__`, so there is no recursion here.
        return getattr(self._inner, name)


class FrameGate:
    """Whether the cameras draw on this `env.step`, and how many times they have.

    `draws` is counted rather than derived. `camera_draw_hz` used to be *declared* equal to
    the step rate, which is the one camera column a CSV never re-read from the live run - so
    the number that says the gate worked has to come from the gate.
    """

    def __init__(self):
        self.drawing = True
        self.draws = 0
        self.held = 0

    def before_step(self, deciding):
        """Call immediately before `env.step`. Truthy draws a frame; falsy holds the last."""
        self.drawing = bool(deciding)
        if not self.drawing:
            self.held += 1

    def reset_counts(self):
        """Start counting again - called where a benchmark's warm-up ends."""
        self.draws = 0
        self.held = 0

    def drawn_hz(self, seconds):
        """The rate the cameras really drew at over `seconds` of simulated time."""
        if not seconds or math.isnan(seconds):
            return float("nan")
        return self.draws / seconds


def install(env):
    """Gate this env's per-step render, or return None where there is nothing to gate.

    Installed only for an offscreen env - `_render_mode` (`base_env.py:382-392`) is
    `offscreen` exactly when a `BaseCamera` was registered and no window is being watched.
    Under `--render none` there are no cameras (`base_env.py:342-349` drops every one), and
    under `--render 3D` the window *is* the point: `ForceFPS.real_time_simulation` steps the
    task manager inside the substep loop as well, and `--agent-policy manual` polls the
    keyboard there.

    Must be called after `env.reset()`, which is when the engine exists.
    """
    if env.config.get("_render_mode") != offscreen_mode():
        return None
    if env.config.get("image_on_cuda"):
        raise FrameGateError(
            "image_on_cuda keeps the frame on the GPU and fills it from a panda3d task "
            "(depth_camera.py:28,124), so holding a frame here would hand back a buffer "
            "something else is still writing. Drive without --decision-hz, or without CUDA."
        )
    engine = getattr(env, "engine", None)
    if engine is None:
        raise FrameGateError("install(env) before env.reset(): there is no engine yet")

    gate = FrameGate()
    engine.task_manager = _GatedTaskManager(engine.task_manager, gate)

    for observation in _image_observations(env):
        _hold_image(observation, gate)

    # Belt and braces. Reset's own frames go through `taskMgr` and `graphicsEngine` directly,
    # so they are drawn whatever this says - but leaving `drawing` False across an episode
    # boundary would mean the first step of the next episode silently held a frame from the
    # last one.
    original_reset = env.reset

    def reset(*arguments, **keywords):
        gate.drawing = True
        return original_reset(*arguments, **keywords)

    env.reset = reset
    return gate


def _image_observations(env):
    """Every `ImageObservation` this env builds its observation from.

    Taken off the agent manager's own dict rather than `env.observations`, which is a property
    that rebuilds a mapping of the same objects per call (`agent_manager.py:210-221`).
    """
    manager = getattr(env, "agent_manager", None)
    holder = getattr(manager, "observations", None) if manager is not None else None
    if not holder:
        holder = env.observations
    found = []
    for observation in holder.values():
        image = getattr(observation, "img_obs", None)
        if image is not None and hasattr(image, "state"):
            found.append(image)
    return found


def _hold_image(image, gate):
    """Return the stack unrolled on a step where nothing was drawn."""
    original = image.observe

    def observe(*arguments, **keywords):
        if gate.drawing:
            return original(*arguments, **keywords)
        return image.state

    image.observe = observe
