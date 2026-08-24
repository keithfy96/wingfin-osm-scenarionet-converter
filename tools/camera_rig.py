"""A multi-camera rig, read from a CARLA-shaped sensor spec and mounted on the ego.

    from camera_rig import load_rig

    rig = load_rig("rigs/cams.txt")
    env = make_env(dataset, render="offscreen", sensors=rig.sensors(),
                   vehicle_config=dict(image_source=rig.image_source()))
    env.reset()
    rig.mount(env)                      # after every reset
    frames = rig.read()                 # {name: (H, W, 3) uint8}, one step

Stage 7d. `tools/sensor_survey.py` samples **one** forward camera at MetaDrive's default
mount; a surround rig is what a real vehicle carries, and the spec for it is a file rather
than an argument. This module reads that file, converts it, and puts the cameras on the car.

**Mount the cameras, do not borrow one.** MetaDrive's own six-view example
(`tests/scripts/multiview_generation_with_image_on_cuda.py`) re-aims a single camera per
view through `perceive(..., new_parent_node, position, hpr)`, which calls `taskMgr.step()`
twice each time - six serialised render passes. Six cameras parented to the ego instead are
all filled by the *same* pass. Measured on `junction-1` at 320x180: **20.4 ms/step mounted
against 77.3 ms/step borrowed**, of which mounted spends only 2.2 ms in the read. That
example also shares one `ImageObservation` across its six views, so its six dict entries are
the same array object and its 3-deep "stack" is the last three *views* rather than the last
three *timesteps*; nothing here goes through `ImageObservation` at all.

**The spec is CARLA's and MetaDrive's frame is not.** Measured by parenting a `NodePath` to
`env.agent.origin` and reading its world pose back:

    local +y 1 m  ->  +1.000 m ahead, -0.000 m right     MetaDrive: +y forward,
    local +x 1 m  ->  +0.000 m ahead, +1.000 m right                +x right, +z up
    H = +55       ->  +55.00 deg from the car's heading  H positive turns LEFT
    H = -55       ->  -55.00 deg from the car's heading

CARLA is x forward, y right, z up, with **yaw positive to the right**. So the conversion is
an **x/y swap** and a **sign flip on yaw**, neither of which is a rename:

    position = (carla_y, carla_x, carla_z)
    hpr      = (-carla_yaw, carla_pitch, carla_roll)

`pitch` and `roll` are `0.0` throughout the spec this was written for, so their signs have
never been tested against anything. A non-zero one is **refused** rather than guessed.

**`import yaml` is not available here.** `tools/` runs on MetaDrive's Python 3.8, where
PyYAML is not installed, and installing into the reference checkout is out - the same
constraint that made `geodesy.py` solve the inverse projection directly instead of depending
on `pyproj`. `_parse` therefore reads the restricted shape this file actually has (a list of
flat mappings under `sensors:`) and raises on anything else, rather than silently skipping a
camera the caller believes is mounted.
"""

from __future__ import annotations

import os

# What a camera's `type:` may say. Only RGB is wired: a depth or semantic view is a different
# MetaDrive class with a different channel count, and guessing which the caller meant would
# put the wrong array under a name their model reads.
SUPPORTED_TYPES = ("rgb_camera",)

# Keys `_parse` understands. Anything else in the file is an error rather than a shrug -
# a `tick_rate` of 0.05 would mean the caller expects 20 Hz and would silently get 10.
TRANSFORM_KEYS = ("x", "y", "z", "pitch", "yaw", "roll")

# MetaDrive's own `env.step` interval (`base_env.py:190`), and the rate a rig is read at when
# the caller does not say otherwise. It is no longer the *only* legal `tick_rate`: `--step-hz`
# moves how far a step advances and `--decision-hz` moves how often the cameras are read, so a
# spec declaring 0.05 s is exactly right on a 100 Hz world with 20 Hz decisions. What is still
# refused is a spec whose rate disagrees with the interval it will actually be read at -
# nothing here resamples, and a silently-wrong frame rate is the fault this check exists for.
STEP_S = 0.1

# How many image buffers one env may hold before panda3d stops being reliable.
#
# Past this, `env.reset` fails *intermittently* inside `graphicsEngine.renderFrame()` with
# `AssertionError: _formats_by_animation.empty() at line 350 of panda/src/gobj/geomMunger.cxx`
# or `MutexPosixImpl::~MutexPosixImpl(): Assertion 'result == 0' failed`, and the process then
# aborts or segfaults. Measured over 5 runs at each size on `junction-1`, counting the buffers
# the engine really holds (`env.engine.sensors` less the three ray detectors):
#
#     7 RGB  5/5      10 RGB  3/5
#     8 RGB  5/5      11 RGB  1/5
#     9 RGB  5/5      12 RGB  1/5
#
# **Mixing camera types costs more than the count suggests.** Adding *one* non-RGB camera to a
# 7-camera rig is free - a `DepthCamera`, a `SemanticCamera` or a `PointCloudLidar` each give
# 5/5 at 8 buffers - but *two* of them give **1/5 at 9**, where nine RGB cameras give 5/5. So
# this ceiling is honest for an all-RGB rig, which is what `SUPPORTED_TYPES` allows, and a
# caller adding several other kinds alongside one should re-measure rather than trust it.
#
# Four readings that each looked like the cause and are not:
#
#   * not the GPU: 1/5 on the RTX 4050 through `__NV_PRIME_RENDER_OFFLOAD`, 2/5 on the Intel
#     iGPU, with the same eleven buffers.
#   * not `multi_thread_render` (default True, `threading-model Cull`): False gave 0/5.
#   * not panda3d's threading generally: `loadPrcFileData("", "threading-model")` before
#     MetaDrive is imported gave 2/5 against 3/5 with it left alone.
#   * not `stm-max-views`, which panda3d complains about past ~6 cameras. Raising it changed
#     nothing byte for byte, and every camera renders pixel-identically alone and in the full
#     rig anyway.
#
# This was 7 until `agent_env.make_env` stopped injecting an `rgb_camera` no rig reads. That
# dead buffer was inside every count above, so the old figures were describing a rig one
# camera larger than the caller had asked for.
#
# The intermittency is why this is a refusal rather than a warning: a rig one camera over the
# line looks like it works, and then fails on a run somebody is relying on.
MAX_IMAGE_BUFFERS = 9


class RigError(Exception):
    """The spec could not be turned into a rig. Always names the camera and the reason."""


class Camera:
    """One camera, already converted into MetaDrive's frame."""

    def __init__(self, name, position, hpr, width, height, fov, carla_yaw):
        self.name = name
        self.position = position  # (x right, y forward, z up), metres, ego frame
        self.hpr = hpr  # (heading, pitch, roll), degrees, + heading is LEFT
        self.width = width
        self.height = height
        self.fov = fov  # horizontal; the vertical angle follows the aspect ratio
        self.carla_yaw = carla_yaw

    @property
    def aim(self):
        """Where this camera actually points, in words, under the CARLA reading.

        The spec this was written for disagrees with itself about the sign of `yaw` - its
        front pair reads `+` as right and its back pair reads `+` as left - so exactly two of
        its four side cameras are named backwards whichever convention is chosen. Printing
        the resolved aim on every run is what keeps that visible instead of baked in.
        """
        yaw = self.carla_yaw
        turn = ((yaw + 180.0) % 360.0) - 180.0
        if abs(turn) < 1.0:
            return "straight ahead"
        if abs(abs(turn) - 180.0) < 1.0:
            return "straight behind"
        side = "right" if turn > 0 else "left"
        quarter = "rear-" if abs(turn) > 90.0 else ("front-" if abs(turn) < 90.0 else "")
        return f"{abs(turn):.0f} deg to the {side}, i.e. {quarter}{side}"

    @property
    def megabytes(self):
        return self.width * self.height * 3 / 1e6

    def __repr__(self):
        return f"Camera({self.name}, {self.width}x{self.height}, fov {self.fov})"


class CameraRig:
    """The cameras of one spec, registerable on an env and readable each step."""

    def __init__(self, cameras, path=None, tick_rate_s=None):
        if not cameras:
            raise RigError("the spec defines no cameras")
        self.cameras = cameras
        self.path = path
        # What the spec itself declared, or None if it declared nothing. Kept so a caller that
        # could not know the read interval at load time - `step_timing`, which sweeps every
        # rate a workspace holds - can compare against the spec's own number per dataset
        # instead of assuming the 0.1 s that used to be the only value `_parse` allowed.
        self.tick_rate_s = tick_rate_s
        self._mounted = []

    def __len__(self):
        return len(self.cameras)

    @property
    def names(self):
        return [camera.name for camera in self.cameras]

    @property
    def megabytes(self):
        """MB of uint8 image produced per step, across the whole rig."""
        return sum(camera.megabytes for camera in self.cameras)

    def sensors(self):
        """The `sensors=` dict for `agent_env.make_env`.

        The FOV is *not* set here: `sensors` carries only the constructor arguments, and
        `camera_fov` (`base_env.py:102`) is one global number for every camera. Per-camera FOV
        is applied in `mount`, through the lens - which `PointCloudLidar.get_rgb_array_cpu`
        notes can be changed on the fly.
        """
        from metadrive.component.sensors.rgb_camera import RGBCamera

        return {
            camera.name: (RGBCamera, camera.width, camera.height) for camera in self.cameras
        }

    def image_source(self):
        """A rig camera to point `vehicle_config["image_source"]` at.

        `make_env(render="offscreen")` turns on `image_observation`, and
        `ImageObservation.observation_space` reads `config["sensors"][image_source]`
        (`image_obs.py:68`), defaulting the name to `rgb_camera`. Left alone, that registers a
        320x240 camera nothing in the rig reads - an extra render pass every step. Naming a
        rig camera instead drops it.
        """
        return self.cameras[0].name

    def mount(self, env):
        """Parent every camera to the ego and set its lens. Call after each `reset()`.

        `env.agent.origin` is measured to be the *same* NodePath across a reset here, so a
        mount does survive one - but re-mounting costs nothing and does not rest on that
        staying true for a scenario whose ego is a different vehicle class.
        """
        self._mounted = []
        for camera in self.cameras:
            sensor = env.engine.get_sensor(camera.name)
            sensor.lens.setFov(camera.fov)
            sensor.track(env.agent.origin, camera.position, camera.hpr)
            self._mounted.append((camera.name, sensor))
        return self

    def read(self, to_float=False):
        """One step from every camera: `{name: (H, W, 3)}`, uint8 unless `to_float`.

        `perceive` is called with no parent node, so it reads the buffer the frame pass has
        already filled rather than re-aiming and re-rendering - which is the whole reason the
        cameras are mounted.

        **`numpy.asarray` here would raise under `image_on_cuda`** - CuPy refuses the implicit
        copy by design - and it is left as it is because nothing can reach it: only `drive.py`
        sets that key and it does not use a rig, while the rig's callers (`sensor_survey.py`,
        `step_timing.py`) never set it. Wiring the flag into either means wrapping this in
        `gpu_frames.to_host`, which is where that copy is written out.
        """
        import numpy

        if not self._mounted:
            raise RigError("read() before mount(env) - the cameras are on nothing")
        return {
            name: numpy.asarray(sensor.perceive(to_float)) for name, sensor in self._mounted
        }

    def describe(self):
        """The resolved rig, in lines, for a report or a log."""
        lines = [
            "{} camera(s) from {}".format(len(self.cameras), self.path or "<spec>"),
            "  CARLA spec (x fwd, y right, z up, +yaw right) -> MetaDrive "
            "(x right, y fwd, z up, +heading LEFT)",
        ]
        for camera in self.cameras:
            x, y, z = camera.position
            size = f"{camera.width}x{camera.height}"
            lines.append(
                f"  {camera.name:<16} {size:>9}  fov {camera.fov:>3.0f}  "
                f"mount x{x:+.2f} y{y:+.2f} z{z:+.2f}  "
                f"H{camera.hpr[0]:+7.1f}  aims {camera.aim}"
            )
        lines.append(f"  {self.megabytes:.2f} MB of uint8 image per step")
        return lines


def _scalar(text, camera, key):
    """A YAML scalar. Numbers only - every value this spec carries is one."""
    text = text.strip()
    try:
        return float(text)
    except ValueError:
        raise RigError(f"{camera}: {key} is {text!r}, which is not a number") from None


def _parse(text, path=None, read_interval_s=STEP_S):
    """A list of flat mappings under `sensors:`, and nothing else.

    Deliberately not a YAML parser. It accepts exactly the shape the spec has and raises on
    anything it does not recognise, because a camera silently dropped from a rig is a hole in
    the model's input that looks like a blind spot in the map.

    `read_interval_s` is how long passes between two reads of these cameras - `1 / step_hz`
    times the `--decision-hz` stride - and a declared `tick_rate` that disagrees with it is
    refused. `None` skips the check, for a caller that does not know the interval yet because
    it is about to drive several rates; `CameraRig.tick_rate_s` carries the declared value on
    for it to check per dataset.
    """
    entries = []
    current = None
    section = None
    seen_root = False
    tick_rate_s = None

    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        stripped = line.strip()

        if indent == 0:
            if stripped != "sensors:":
                raise RigError(
                    f"line {number}: expected `sensors:` at the top level, found {stripped!r}"
                )
            seen_root = True
            continue
        if not seen_root:
            raise RigError(f"line {number}: content before `sensors:`")

        if stripped.startswith("- "):
            current = {"transform": {}}
            entries.append(current)
            section = None
            stripped = stripped[2:].strip()
            indent += 2
        if current is None:
            raise RigError(f"line {number}: {stripped!r} is not inside a `- ` list item")

        key, separator, value = stripped.partition(":")
        if not separator:
            raise RigError(f"line {number}: {stripped!r} is not `key: value`")
        key = key.strip()
        value = value.strip()

        if key == "transform":
            if value:
                raise RigError(f"line {number}: inline `transform:` is not supported")
            section = "transform"
            continue
        # A transform's keys are indented under it; anything at the item's own indent ends it.
        if section == "transform" and key in TRANSFORM_KEYS:
            current["transform"][key] = value
        else:
            section = None
            current[key] = value

    if not seen_root:
        raise RigError("no `sensors:` key - this does not look like a camera spec")

    cameras = []
    for index, entry in enumerate(entries):
        name = entry.get("name") or f"camera {index}"
        kind = entry.get("type")
        if kind not in SUPPORTED_TYPES:
            raise RigError(
                "{}: type is {!r}; only {} is wired. A depth or semantic view is a different "
                "MetaDrive class with a different channel count.".format(
                    name, kind, " / ".join(SUPPORTED_TYPES)
                )
            )
        for required in ("width", "height", "fov"):
            if required not in entry:
                raise RigError(f"{name}: no {required}")
        transform = entry["transform"]
        missing = [key for key in TRANSFORM_KEYS if key not in transform]
        if missing:
            raise RigError("{}: transform has no {}".format(name, ", ".join(missing)))

        rate = entry.get("tick_rate")
        if rate is not None:
            declared = _scalar(rate, name, "tick_rate")
            if tick_rate_s is None:
                tick_rate_s = declared
            elif abs(declared - tick_rate_s) > 1e-9:
                raise RigError(
                    f"{name}: tick_rate {declared} s, but another camera in this spec "
                    f"declares {tick_rate_s} s. The rate has one source - the flags - so a "
                    "spec cannot ask for two."
                )
            if read_interval_s is not None and abs(declared - read_interval_s) > 1e-9:
                raise RigError(
                    f"{name}: tick_rate {declared} s ({1.0 / declared:g} Hz), but these "
                    f"cameras are read every {read_interval_s:g} s "
                    f"({1.0 / read_interval_s:g} Hz). Nothing here resamples. "
                    "--step-hz sets how far a step advances and --decision-hz how often the "
                    "cameras are read; together they are what matches the two."
                )

        pitch = _scalar(transform["pitch"], name, "pitch")
        roll = _scalar(transform["roll"], name, "roll")
        if pitch or roll:
            raise RigError(
                f"{name}: pitch {pitch} / roll {roll}. Only yaw is converted: every camera "
                "in the spec this was built for has pitch and roll of 0, so neither sign has been "
                "measured against MetaDrive, and a guessed sign tilts the view the wrong "
                "way silently."
            )

        forward = _scalar(transform["x"], name, "x")
        right = _scalar(transform["y"], name, "y")
        up = _scalar(transform["z"], name, "z")
        yaw = _scalar(transform["yaw"], name, "yaw")

        cameras.append(
            Camera(
                name=name,
                # The swap: CARLA's forward is MetaDrive's y, CARLA's right is its x.
                position=(right, forward, up),
                # The flip: CARLA's +yaw is right, MetaDrive's +heading is left.
                # `+ 0.0` so a yaw of 0.0 does not come back as -0.0.
                hpr=(-yaw + 0.0, pitch, roll),
                width=int(_scalar(entry["width"], name, "width")),
                height=int(_scalar(entry["height"], name, "height")),
                fov=_scalar(entry["fov"], name, "fov"),
                carla_yaw=yaw,
            )
        )

    duplicates = {n for n in (c.name for c in cameras) if [c.name for c in cameras].count(n) > 1}
    if duplicates:
        raise RigError("duplicate camera name(s): {}".format(", ".join(sorted(duplicates))))
    return CameraRig(cameras, path=path, tick_rate_s=tick_rate_s)


def load_rig(path, read_interval_s=STEP_S):
    """Read a rig spec from disk.

    `read_interval_s` is the interval the cameras will really be read at; `None` defers the
    check to the caller (see `_parse`).
    """
    path = os.path.abspath(path)
    if not os.path.exists(path):
        raise RigError(f"no rig spec at {path}")
    with open(path) as handle:
        return _parse(handle.read(), path=path, read_interval_s=read_interval_s)


def check_frame(dataset_dir):
    """Re-measure MetaDrive's vehicle frame, which the conversion above is built on.

    The two facts the whole module rests on - that local +y is forward and local +x is right,
    and that a positive heading turns *left* - are properties of MetaDrive rather than of this
    repo, and `tests/unit/test_camera_rig.py` cannot reach them: it runs on 3.10 and MetaDrive
    is on 3.8. So they are checked here, where an engine exists.

    A `NodePath` is parented to the ego, given a local position or heading, and read back in
    world coordinates against the car's own heading. Returns a list of (label, ok, detail).
    """
    import math
    import sys

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from agent_env import make_env
    from panda3d.core import NodePath

    env = make_env(os.path.abspath(dataset_dir), render=None)
    try:
        env.reset(seed=0)
        env.step([0.0, 0.3])
        agent = env.agent
        heading = agent.heading_theta
        render = env.engine.render

        def probe(position, hpr):
            node = NodePath("probe")
            node.reparentTo(agent.origin)
            node.setPos(*position)
            node.setHpr(*hpr)
            where = node.getPos(render)
            forward = node.getQuat(render).getForward()
            node.removeNode()
            return where, math.degrees(math.atan2(forward[1], forward[0]))

        base, _ = probe((0, 0, 0), (0, 0, 0))

        def offset(position):
            where, _ = probe(position, (0, 0, 0))
            dx, dy = where[0] - base[0], where[1] - base[1]
            return (
                dx * math.cos(heading) + dy * math.sin(heading),
                dx * math.sin(heading) - dy * math.cos(heading),
            )

        results = []
        ahead, right = offset((0, 1, 0))
        results.append(
            ("local +y is 1 m forward", abs(ahead - 1.0) < 1e-3 and abs(right) < 1e-3,
             f"ahead {ahead:+.3f} m, right {right:+.3f} m")
        )
        ahead, right = offset((1, 0, 0))
        results.append(
            ("local +x is 1 m right", abs(right - 1.0) < 1e-3 and abs(ahead) < 1e-3,
             f"ahead {ahead:+.3f} m, right {right:+.3f} m")
        )
        for degrees, label in ((55.0, "left"), (-55.0, "right")):
            _, facing = probe((0, 0, 0), (degrees, 0, 0))
            relative = ((facing - math.degrees(heading) + 180.0) % 360.0) - 180.0
            results.append(
                (f"H={degrees:+.0f} turns {label}",
                 abs(relative - degrees) < 1e-2,
                 f"{relative:+.2f} deg from the car's heading")
            )
        return results
    finally:
        env.close()


if __name__ == "__main__":
    import sys

    arguments = sys.argv[1:]
    if len(arguments) == 2 and arguments[0] == "--check-frame":
        failed = 0
        for label, ok, detail in check_frame(arguments[1]):
            print("  {}  {:<26} {}".format("ok  " if ok else "FAIL", label, detail))
            failed += not ok
        if failed:
            print(
                "\nMetaDrive's vehicle frame is not what the conversion assumes. Every rig "
                "mount and aim is wrong until this passes.",
                file=sys.stderr,
            )
        raise SystemExit(1 if failed else 0)
    if len(arguments) != 1:
        print(
            "usage: camera_rig.py <spec>\n"
            "       camera_rig.py --check-frame <dataset>   (needs MetaDrive's 3.8)",
            file=sys.stderr,
        )
        raise SystemExit(2)
    try:
        for line in load_rig(arguments[0]).describe():
            print(line)
    except RigError as error:
        print(f"rig spec rejected: {error}", file=sys.stderr)
        raise SystemExit(1) from None
