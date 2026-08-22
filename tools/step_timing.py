"""How much wall-clock time one `env.step` costs, against how much simulated time it buys.

    <metadrive-checkout>/.venv/bin/python tools/step_timing.py <dataset> [<dataset> ...]

A workspace holds one dataset per rate - `scenarionet-10hz` beside `scenarionet-100hz` - and
nothing in either says what it costs to run. This drives each one and reports the ratio that
decides whether a rate is usable: seconds of simulation per second of clock.

Like `drive.py` and `check_dataset.py` this runs on MetaDrive's interpreter (3.8 / numpy
1.24) rather than this repo's, so it imports nothing from the package. It imports plenty from
its neighbours in `tools/`, though - `make_env`, `IdmDriver`, `step_config`, the two clocks,
the GL probe - because a benchmark that configures the map differently from the thing being
benchmarked is measuring its own arrangement.

**The default is two rows that differ only in who drives.** Row 1 replays the recorded track,
which decides nothing; row 2 puts `TrajectoryIDMPolicy` in the seat. Put a model behind
`--policy-url` (row 3) and it takes the same seat.

**`policy_ms` is what answers "is the model the slow part", not the difference between the
rows.** The subtraction was the plan and it does not survive contact with the machine:
measured three times over on one dataset, row 1 came out at 8.90 / 8.99 / 10.07 ms a step and
row 2 at 9.35 / 10.35 / 8.99, so the difference read +0.45, +1.36 and **-1.08** ms while the
driver's own cost sat steady at 0.37-0.43 ms throughout. About a millisecond of run-to-run
spread swamps it - and the two rows do not drive quite the same route anyway, since a replayed
car follows the tape and an IDM car follows its own line. So `policy_ms` is timed directly
around the policy call and is the number to read; the replay row is the reference for whether
the simulator keeps up *at all* with nothing deciding, which is a different question and still
worth having.

Three things about the arithmetic that are not guessable and make the table read backwards if
they are missed:

* **`env.step` is not one physics tick.** It holds one action across `decision_repeat`
  integrations of `physics_world_step_size` (`base_env.py:466` -> `base_engine.py:436-441` ->
  `engine_core.py:385`, where `doPhysics(dt, 1, dt)` takes exactly one fixed tick). MetaDrive's
  default 10 Hz is `(0.02, 5)` - **50 Hz of physics, not 10** - and 100 Hz is `(0.01, 1)`. So
  one step at 100 Hz is *cheaper* than one at 10 Hz, and `ms/step` cannot be compared across
  rates at all. The real-time factor can, which is why it is the headline column.

* **The camera is welded to `env.step`.** `base_engine.py:458` calls `task_manager.step()` once
  after the repeat loop, unconditionally, and that redraws every camera buffer. So the image
  rate *is* the step rate, and a camera costs a full 10x more per simulated second at 100 Hz
  where the physics costs only 2x. The decision rate is the one that separates, and it
  separates in the caller's loop rather than in any config key: hold the action across N steps.

* **Every offscreen row carries a camera, and it is most of what a step costs.** With
  `image_observation=True` - which `--render offscreen` sets, and which is the only way a
  camera exists without a window (`base_env.py:342-347` deletes every `BaseCamera` otherwise)
  - the *observation* **is** the image stack: `ImageStateObservation.observe` calls
  `perceive()` and rolls the three frames as part of building `env.step`'s return value
  (`image_obs.py:85`). So there is no seam to time it in, and no setting that draws it without
  reading it - turning the read off turns the camera off. `sensor_ms` is therefore the
  *numeric* sensors only, and the camera lives in `step_ms`. It is **about three quarters of
  a step** - `--rows 2,6` on `junction-1` at 10 Hz measured 16.69 ms against 4.06 with no
  graphics - so it is what this sweep mostly measures. Row 2 against row 6 prices it, and
  `carried` is what makes the `sensors` column say the camera is there at all.

* **That camera is one this tool invented, until `--camera-rig` names a real one.** Unflagged,
  every offscreen row registers a single 320x180 `RGBCamera` - a number chosen here, not by
  the vehicle. A surround rig is what a car carries, and `tools/camera_rig.py` already reads
  one from a CARLA-shaped spec (`tools/sensor_survey.py --camera-rig` has driven it since
  Stage 7d). The spec this was written against is **7 cameras at 512x288**: 3.10 MB of image
  a step against 0.17, 17.9x the pixels. Since the camera is most of what a step costs, an
  unflagged figure is not what that vehicle costs, and `--camera-rig` is what closes the gap.
  The rig's cameras are **mounted** on the ego, so one render pass fills all seven, and
  `rig_ms` times reading them back out - a plain buffer read with no parent node, which is a
  different thing from the second render pass `read` is barred from causing.

Every run writes its own CSV, named for the moment it started, carrying the machine it was
measured on in every row - so a file from this laptop and a file from a container on another
box concatenate into one spreadsheet with nothing to line up by hand.
"""

from __future__ import annotations

import argparse
import collections
import csv
import os
import socket
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from drive import (  # noqa: E402
    _max_texture_dimension,
    _refuse_mismatch,
    data_step_seconds,
    decides_on,
    decision_stride,
    sim_step_seconds,
    step_config,
)

# How many steps are dropped from the *distribution* before it is summarised. They stay in
# `wall_seconds`, because the honest total includes them: the first steps of a drive carry
# lazily-built buffers and a cold cache, which a training loop also pays once.
WARMUP_STEPS = 20

# Steps of a throwaway drive before anything is measured. See `prime`.
PRIME_STEPS = 40

# The camera every offscreen row registers when no rig is named, so that "with sensors" and
# "without" differ in what is read rather than in what is drawn. `make_env`'s own offscreen
# default is 320x240; named here so both rows are the same size whatever that default does
# later. `--camera-rig` replaces this with the vehicle's own cameras.
CAMERA_SIZE = (320, 180)

# How far off its recorded route a car may get before the episode ends, for every row of a
# sweep, replay included.
#
# MetaDrive's own default is 4 m (`scenario_env.py:84`) and it exists to judge *driving*: an
# agent that has lost the reference line is not doing the task. This tool measures what a step
# costs, and a car 6 m off its line costs the same per step as one on it - so honouring that
# rule here buys nothing and costs the sample. Measured on `mosque`: the IDM rows ended
# `out_of_road` at step 44 with 24 steps measured, against replay's 400. Four of the six
# default rows are IDM, so at 4 m most of the table would be a median over two dozen samples.
#
# Uniform across rows on purpose - no row may be measured under different termination rules
# from the one it is being compared with. It is recorded in the CSV as `max_lateral_m` rather
# than applied silently, and `ended_by` still says `out_of_road` if a row hits it anyway.
# `drive.py` keeps MetaDrive's 4 m: that tool *is* asking whether a drive is drivable.
SWEEP_MAX_LATERAL_M = 20.0


# Each row is one configuration, driven once per dataset. `read` is what this loop reads
# itself each step; a camera is not in it because MetaDrive reads that inside `env.step`
# (see the module docstring). `physics_hz` pins the integrator and lets `decision_repeat`
# fall out of it, which is the only way to say "100 Hz physics, 10 Hz decisions" - CARLA's
# own default shape, and the pairing a camera-driven training loop wants.
ROWS = {
    1: dict(
        render="offscreen", policy="replay", read=("imu", "gps"),
        isolates="the floor: the same step with nothing deciding",
    ),
    2: dict(
        render="offscreen", policy="idm", read=("imu", "gps"),
        isolates="a training-shaped step, with a controller driving",
    ),
    3: dict(
        render="offscreen", policy="remote", read=("imu", "gps"),
        isolates="your model in the same seat, over --policy-url",
    ),
    4: dict(
        render="offscreen", policy="idm", read=(),
        isolates="vision only: a camera and MetaDrive's own state, nothing else read",
    ),
    5: dict(
        render="offscreen", policy="idm", read=("imu", "gps"), physics_hz=100.0,
        isolates="physics pinned at 100 Hz: CARLA-shaped at a 10 Hz dataset",
    ),
    6: dict(
        render=None, policy="idm", read=("imu", "gps"),
        isolates="no graphics at all: what the camera and the render path cost",
    ),
    # There is deliberately no unthrottled twin of this row. `ForceFPS` is built only
    # onscreen and takes its interval from `physics_world_step_size` (`force_fps.py:12-22`),
    # so it looks like the thing to raise -- but measured on this machine it never fires:
    # `force_render_fps=1000` gives 16.59 ms a frame against 16.67 stock at 100 Hz, and
    # 83.34 against 83.50 at 10 Hz. Loading `sync-video #f` into panda3d before the window
    # exists does not move it either. The ceiling is the compositor's 60 Hz, which neither
    # reaches, so a second row would have measured the same thing and claimed not to.
    7: dict(
        render="3D", policy="replay", read=(),
        isolates="what drive.sh gives you, at the display's own 60 Hz ceiling",
    ),
}

# Every row but 7, which opens a window and so cannot be part of an unattended default. Row 3
# is in and skips itself with `needs --policy-url` when no model is listening, which is a
# truer thing for the table to say than the row not being there.
DEFAULT_ROWS = (1, 2, 3, 4, 5, 6)


def carried(row, cameras=1):
    """Everything a row's drive actually has, camera included.

    `row["read"]` is only what this loop reads *itself*, and the camera is deliberately not
    in it: with `image_observation` on, MetaDrive draws a frame and rolls the 3-frame stack
    inside `env.step` while building the observation (`image_obs.py:85`), so reading it here
    as well forces a second render pass (`base_camera.py:188`) and charges the benchmark for
    a frame no training loop draws. Every offscreen row therefore carries a camera whether
    or not `read` mentions one - which is what the `sensors` column has to say, having read
    as "no camera on rows 1 and 2" for as long as it printed `read` alone.

    This is the *declared* list, for `--list-rows`, which has no env to ask. A run reports
    what its own env holds; see `drive`.
    """
    return camera_label(cameras if row["render"] == "offscreen" else 0) + tuple(row["read"])


def camera_label(count):
    """`()`, `("camera",)` or `("camera x7",)` - what a count of cameras is called in a table.

    One entry however many cameras there are, because the `sensors` column lists *kinds*
    beside `imu` and `gps`. The count is on it because a rig and the single invented camera
    are not the same measurement and must not print the same word.
    """
    if not count:
        return ()
    return ("camera" if count == 1 else f"camera x{count}",)


# One line of a `--rate-sets` file: a whole simulator configuration under a name.
#
# `world tick / decision + camera / physics` is how the question is usually asked - CARLA's
# three knobs - so a sweep comparing configurations wants to name them that way rather than
# spell three flags per run and reconcile three CSVs afterwards. `decision_hz` and
# `physics_hz` may be blank, meaning "whatever `step_hz` derives", which is what makes
# `10/10/50` and a bare `10` the same row.
RateSet = collections.namedtuple("RateSet", "name step_hz decision_hz physics_hz")

RATE_SET_COLUMNS = ("name", "step_hz", "decision_hz", "physics_hz")


def rate_set_label(rate_set, native_hz=None):
    """`10/10/50` - the three rates in the order the question is asked in.

    `native_hz` stands in for a set that did not name a step rate, which is what the flags
    produce when `--step-hz` was not passed: the rate is then each dataset's own.
    """
    step = rate_set.step_hz or native_hz
    if step is None:
        return "dataset rate"
    decision = rate_set.decision_hz or step
    physics = rate_set.physics_hz or (1.0 / step_config(step)["physics_world_step_size"])
    return f"{step:g}/{decision:g}/{physics:g}"


def load_rate_sets(path):
    """Read a rate-set CSV: `name,step_hz,decision_hz,physics_hz`, one configuration a row.

    Blank lines and `#` comments are skipped, and a blank `decision_hz` or `physics_hz` means
    "derived from `step_hz`" rather than zero. Every other malformed thing is an error rather
    than a shrug, for `camera_rig._parse`'s reason: a configuration silently dropped from a
    comparison is a hole in it that looks like a result.

    The arithmetic is *not* checked here. `rate_keys` and `decision_stride` refuse an
    impossible pair where they are applied, per row and per dataset, so an unbuildable set
    shows in the table as a skipped row saying why - which is more useful than a file the
    whole sweep refuses to start on.
    """
    if not os.path.exists(path):
        raise ValueError(f"no rate-set file at {path}")
    with open(path, encoding="utf-8") as handle:
        lines = [
            line for line in handle
            if line.strip() and not line.lstrip().startswith("#")
        ]
    reader = csv.DictReader(lines)
    unknown = [name for name in (reader.fieldnames or []) if name not in RATE_SET_COLUMNS]
    if unknown:
        raise ValueError(
            "{}: unknown column(s) {}. Known: {}".format(
                path, ", ".join(unknown), ", ".join(RATE_SET_COLUMNS)
            )
        )
    if not reader.fieldnames or "step_hz" not in reader.fieldnames:
        raise ValueError(f"{path}: no `step_hz` column; the header must name the columns")

    def number(row, column, line):
        text = (row.get(column) or "").strip()
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            raise ValueError(f"{path} line {line}: {column} is {text!r}, not a number") from None

    sets = []
    for offset, row in enumerate(reader, start=2):
        step = number(row, "step_hz", offset)
        if step is None:
            raise ValueError(f"{path} line {offset}: no step_hz. Every set needs a world tick.")
        name = (row.get("name") or "").strip()
        sets.append(
            RateSet(
                name=name or f"{step:g}Hz",
                step_hz=step,
                decision_hz=number(row, "decision_hz", offset),
                physics_hz=number(row, "physics_hz", offset),
            )
        )
    if not sets:
        raise ValueError(f"{path}: no rate sets in it")
    duplicates = {one.name for one in sets if [s.name for s in sets].count(one.name) > 1}
    if duplicates:
        raise ValueError(
            "{}: duplicate set name(s) {}. The name is what tells two sets apart in the "
            "CSV.".format(path, ", ".join(sorted(duplicates)))
        )
    return sets


def rate_keys(step_hz, physics_hz):
    """The two MetaDrive keys for a decision rate, optionally with the integrator pinned.

    With no `physics_hz` this is exactly `drive.step_config`, so an unflagged run's config is
    unchanged key-for-key. With one, the physics tick is what was asked for and
    `decision_repeat` is how many of them fit in a step - refused when that is not a whole
    number, because a decision cannot be finer than a tick and rounding it would silently
    measure a rate nobody asked for.
    """
    if physics_hz is None:
        return step_config(step_hz)
    step_dt = 1.0 / float(step_hz)
    physics_dt = 1.0 / float(physics_hz)
    ticks = step_dt / physics_dt
    repeat = int(round(ticks))
    if repeat < 1 or abs(ticks - repeat) > 1e-6:
        raise ValueError(
            f"{physics_hz:g} Hz physics does not divide a {step_hz:g} Hz step: that is "
            f"{ticks:.4g} ticks per step. A decision cannot be finer than a physics tick, "
            f"and a fraction of one is not a rate."
        )
    return {"physics_world_step_size": physics_dt, "decision_repeat": repeat}


def percentiles(samples):
    """mean / median / p95 / p99 / max in milliseconds, or NaNs when nothing was measured."""
    import numpy

    if not samples:
        nan = float("nan")
        return dict(mean=nan, median=nan, p95=nan, p99=nan, max=nan)
    values = numpy.asarray(samples, dtype=float) * 1000.0
    return dict(
        mean=float(values.mean()),
        median=float(numpy.percentile(values, 50)),
        p95=float(numpy.percentile(values, 95)),
        p99=float(numpy.percentile(values, 99)),
        max=float(values.max()),
    )


def machine():
    """Who ran this, in enough detail that two files can be told apart a month later.

    A container's hostname is a random id, which is why `--label` exists and defaults to this
    rather than replacing it.
    """
    import numpy
    from metadrive.constants import EDITION

    cpu = ""
    try:
        with open("/proc/cpuinfo", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("model name"):
                    cpu = line.split(":", 1)[1].strip()
                    break
    except OSError:
        pass

    in_docker = os.path.exists("/.dockerenv")
    if not in_docker:
        try:
            with open("/proc/1/cgroup", encoding="utf-8") as handle:
                blob = handle.read()
            in_docker = "docker" in blob or "containerd" in blob or "kubepods" in blob
        except OSError:
            pass

    gpu = ""
    try:
        finished = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=20,
        )
        gpu = finished.stdout.strip().splitlines()[0] if finished.stdout.strip() else ""
    except (OSError, subprocess.SubprocessError, IndexError):
        pass

    return dict(
        host=socket.gethostname(),
        in_docker="yes" if in_docker else "no",
        cpu_model=cpu,
        cpu_threads=os.cpu_count() or 0,
        gpu_name=gpu,
        os=" ".join(os.uname()[:1] + os.uname()[2:3]),
        python=sys.version.split()[0],
        numpy=numpy.__version__,
        metadrive=EDITION,
    )


def build_env(dataset, row, step_hz, physics_hz, rig=None, max_lateral_m=SWEEP_MAX_LATERAL_M):
    """A `ScenarioEnv` configured for one row, through the same `make_env` a drive uses."""
    from agent_env import make_env
    from metadrive.policy.replay_policy import ReplayEgoCarPolicy
    from policy_client import sensor_config

    overrides = dict(rate_keys(step_hz, physics_hz))
    # Uniform across every row of the sweep, replay included - see `SWEEP_MAX_LATERAL_M`.
    overrides["max_lateral_dist"] = max_lateral_m
    if row["policy"] == "replay":
        # `make_env` deliberately leaves `agent_policy` alone so the action reaches the car;
        # replay is the one row that wants it set, and it goes through `**overrides` like any
        # other MetaDrive key.
        overrides["agent_policy"] = ReplayEgoCarPolicy
    if row["render"] == "offscreen":
        # Registered on every offscreen row, including the ones that read nothing, so that
        # "with sensors" and "without" differ in what is read rather than in what is drawn.
        if rig is None:
            overrides["sensors"] = sensor_config(("camera",), camera_size=CAMERA_SIZE)
        else:
            overrides["sensors"] = rig.sensors()
            # `image_observation` builds the observation out of `config["sensors"][image_source]`
            # (`image_obs.py:68`) and that name defaults to `rgb_camera`, which a rig does not
            # have. Naming a rig camera is what stops `make_env` registering a dead 320x240
            # buffer beside the rig and rendering it every step. It lives in `vehicle_config`;
            # at the top level MetaDrive dies at construction.
            overrides["vehicle_config"] = dict(image_source=rig.image_source())
    return make_env(dataset, render=row["render"], **overrides)


def drive(env, row, arguments, url=None, rig=None, decision_hz=None):
    """Drive one scenario under one row's configuration and return what it cost.

    Returns a dict of measurements, or one carrying `skip_reason` when the dataset cannot be
    driven this way at all.
    """
    from agent_env import IdmDriver
    from policy_client import RemotePolicy, SensorPack

    started = time.perf_counter()
    observation, _ = env.reset(seed=arguments.scenario_index)
    reset_seconds = time.perf_counter() - started

    # After the reset and not before: `mount` parents each camera to `env.agent.origin`, and
    # the ego does not exist until the scenario is loaded. Only where cameras were built -
    # row 6 registers none, so there is nothing on the car to mount.
    mounted = rig if rig is not None and row["render"] == "offscreen" else None
    if mounted is not None:
        mounted.mount(env)

    scenario = env.engine.data_manager.current_scenario
    length = env.engine.data_manager.current_scenario_length
    sim_dt = sim_step_seconds(env)
    data_dt = data_step_seconds(scenario)

    if abs(sim_dt - data_dt) > 1e-9:
        # The same refusal `drive.py` makes, for the same reason: three things consume the
        # recording one frame per `env.step` with no interpolation, so at another rate they
        # drive something other than what the dataset says. It returns "" when harmless,
        # which is what lets a pinned-physics row run against either dataset.
        refusal = _refuse_mismatch(
            scenario, policy=row["policy"], lights="tape", sim_dt=sim_dt, data_dt=data_dt
        )
        if refusal:
            return dict(skip_reason=refusal.splitlines()[0].replace("REFUSED: ", ""))

    read = tuple(row["read"])
    if row["policy"] == "remote" and arguments.policy_sensors is not None:
        # What a hosted model is sent is the model's business, not the row's: openpilot's
        # bridge wants `route` and cannot be driven without it. Overridden rather than
        # written into ROWS so row 3's own definition - and every CSV taken under it - keeps
        # meaning one thing. The `sensors` column reports whatever really went.
        read = tuple(name for name in arguments.policy_sensors.split(",") if name)

    # What this run *carries*, asked of the live env rather than taken from the row
    # definition. A camera is only real when `image_observation` is on and one is registered,
    # and the frame's own shape is the honest camera size - so if anything ever stops
    # building one, the table says so instead of repeating what it was meant to do.
    #
    # Counted by class rather than by the name `rgb_camera`, which is what the single invented
    # camera happens to be called: a rig's cameras are named by the spec (`cam_front`, ...),
    # so a name test reports a seven-camera run as having no camera at all - the mislabelling
    # this probe exists to prevent, returning by a different door.
    from metadrive.component.sensors.base_camera import BaseCamera

    cameras = 0
    if env.config.get("image_observation"):
        for entry in (env.config.get("sensors") or {}).values():
            if isinstance(entry, (tuple, list)) and entry and isinstance(entry[0], type):
                cameras += issubclass(entry[0], BaseCamera)
    camera_size = ""
    stack_size = ""
    if isinstance(observation, dict) and "image" in observation:
        shape = observation["image"].shape
        camera_size = f"{int(shape[1])}x{int(shape[0])}"
        stack_size = int(shape[3]) if len(shape) > 3 else ""
    sensors = ",".join(camera_label(cameras) + read)

    pack = SensorPack(env, read) if read else None
    policy = None
    if row["policy"] == "idm":
        policy = IdmDriver(env)
    elif row["policy"] == "remote":
        # The pack goes to the policy rather than to this loop: a hosted model is sent its
        # sensors, so their cost is part of what the call takes and double-reading them here
        # would charge the drive twice for one frame.
        policy = RemotePolicy(url, pack=SensorPack(env, read), step_seconds=sim_dt)
        policy.spec({"dataset": env.config["data_directory"], "row": row["isolates"]})
        # `drive.py` does this per scenario and this did not, which was invisible only
        # because `SensorPack` re-reads the projection lazily. A server holding anything per
        # episode - openpilot's bridge holds a whole connection - gets nothing without it.
        policy.start_episode(scenario["id"])
        pack = None
    if pack is not None:
        pack.reset()

    budget = int(round(length * data_dt / sim_dt))
    if arguments.max_steps:
        budget = min(budget, arguments.max_steps)

    # The middle rate. `decision_stride` is `drive.py`'s, so this tool and that one cannot
    # disagree about what "20 Hz decisions on a 100 Hz world" means.
    #
    # Passed in rather than read off `arguments`, and that is not tidiness: under
    # `--rate-sets` the rate lives on the set and `arguments.decision_hz` is None, so reading
    # it here drove every set at stride 1 while the table printed the rate the set asked for.
    # A benchmark that misreports its own configuration is worse than one that cannot express
    # it, so there is now one source and the caller passes it.
    stride = decision_stride(1.0 / sim_dt, decision_hz)

    step_samples = []
    policy_samples = []
    sensor_samples = []
    rig_samples = []
    info = {}
    steps = 0
    gl_renderer = ""
    window = env.engine.win
    if window is not None:
        gl_renderer = window.getGsg().getDriverRenderer()
    # Recorded rather than assumed: ForceFPS is built only onscreen, and even there its
    # sleep never fires on a display slower than its own interval. `UnlimitedFPS` means
    # nothing was throttling this row at all.
    force_fps = getattr(env.engine, "force_fps", None)
    force_fps_state = getattr(force_fps, "state", "") if force_fps is not None else ""

    # The clock starts when the distribution does. Counting warm-up into `wall_seconds`
    # was tried and is wrong for the one comparison this tool exists to make: it charges
    # the *first* row of a process for the driver's lazily-built state, and the pair of
    # rows that differ only in who drives then reports the floor as the dearer of the two.
    # `reset_seconds` still carries the terrain build, separately, where it can be seen.
    loop_started = None
    measured = 0
    action = [0.0, 0.0]
    while steps < budget:
        if steps == arguments.warmup:
            loop_started = time.perf_counter()
        deciding = decides_on(steps, stride)
        mark = time.perf_counter()
        if pack is not None and deciding:
            pack()
        read_done = time.perf_counter()
        if mounted is not None and deciding:
            # Not a second render pass, which is why this is allowed where `read` is not:
            # `CameraRig.read` calls `perceive` with no parent node, so it copies the buffer
            # the frame pass has already filled rather than re-aiming the camera and stepping
            # the task manager again (`base_camera.py:188`). Only the `image_source` camera
            # reaches the observation; the other six are read here or not at all, and a
            # training loop reads all of them.
            mounted.read()
        rig_done = time.perf_counter()
        # Held, never reset to `[0, 0]`, on a step between two decisions: zeroing it would
        # lift the throttle four steps in five and a lower decision rate would read as a
        # slower car rather than as a cheaper one. Ignored outright on the replay row, where
        # MetaDrive drives the ego in-engine from the tape.
        if policy is not None and deciding:
            action = policy(observation)
        decided = time.perf_counter()
        observation, _, terminated, truncated, info = env.step(action)
        stepped = time.perf_counter()

        if steps >= arguments.warmup:
            # `step_samples` every step, because `ms/step` is per step by definition. The other
            # three only on the steps they happened, because they answer "what does one of
            # these cost" - and charging them to skipped steps too makes the median a skipped
            # step as soon as the stride is 2 or more. Measured: at `--decision-hz 10` on a
            # 100 Hz world, `policy` printed **0.00 ms** against 0.20 at full rate, which is
            # not the model getting faster. How often they happen is the `decide` column; what
            # one costs is here, and a reader multiplies.
            if deciding:
                sensor_samples.append(read_done - mark)
                rig_samples.append(rig_done - read_done)
                policy_samples.append(decided - rig_done)
            step_samples.append(stepped - decided)
            measured += 1
        steps += 1
        if terminated or truncated:
            break
    wall_seconds = time.perf_counter() - loop_started if loop_started else float("nan")

    named = ("arrive_dest", "out_of_road", "crash", "crash_object", "crash_vehicle", "max_step")
    ended_by = ", ".join(name for name in named if info.get(name)) or "ran out of budget"
    # Over the measured window, so the ratio and the distribution describe the same steps.
    sim_seconds = measured * sim_dt

    return dict(
        gl_renderer=gl_renderer,
        force_fps=force_fps_state,
        sensors=sensors,
        camera_count=cameras,
        camera_size=camera_size,
        camera_mb_per_step=round(mounted.megabytes, 3) if mounted is not None else "",
        stack_size=stack_size,
        # Named the same way the declared fallback names it, so a skipped row and a row that
        # ran describe the same thing: `LidarStateObservation`'s 161 floats are what
        # `--render none` leaves, and `image_observation` replaces them with a dict whose
        # `state` half has no lidar block at all (`image_obs.py:40`).
        observation_kind=(
            "image+state{}".format(len(observation["state"]))
            if isinstance(observation, dict) and "state" in observation
            else f"lidarstate{len(observation)}"
        ),
        scenario_id=scenario["id"],
        sim_dt_s=sim_dt,
        physics_dt_s=float(env.config["physics_world_step_size"]),
        decision_repeat=int(env.config["decision_repeat"]),
        steps=steps,
        measured_steps=measured,
        warmup_steps=min(arguments.warmup, steps),
        sim_seconds=sim_seconds,
        wall_seconds=wall_seconds,
        realtime_factor=sim_seconds / wall_seconds if wall_seconds else float("nan"),
        reset_seconds=reset_seconds,
        step=percentiles(step_samples),
        policy=percentiles(policy_samples),
        sensor=percentiles(sensor_samples),
        rig=percentiles(rig_samples),
        arrive_dest="yes" if info.get("arrive_dest") else "no",
        ended_by=ended_by,
    )


def dataset_step_hz(dataset, index):
    """The rate a dataset was written at, read off the file rather than off its directory name.

    `metadata.ts` spacing *is* the rate, so this is authoritative for a legacy `scenarionet`
    directory from before the name carried it.
    """
    from metadrive.scenario.utils import read_dataset_summary, read_scenario_data

    _, lookup, mapping = read_dataset_summary(dataset)
    names = list(lookup)
    if not names:
        raise ValueError(f"{dataset} holds no scenarios")
    name = names[min(index, len(names) - 1)]
    scenario = read_scenario_data(os.path.join(dataset, mapping[name], name))
    return 1.0 / data_step_seconds(scenario)


FIELDS = [
    "label", "host", "in_docker", "cpu_model", "cpu_threads", "gpu_name", "gl_max_texture",
    "gl_renderer", "os", "python", "numpy", "metadrive", "timestamp",
    "dataset", "scenario_id", "step_hz", "sim_dt_s", "physics_hz", "physics_dt_s",
    "decision_repeat", "physics_ticks_per_sim_second",
    # The middle column of world tick / decision + camera / physics. `decision_hz` is the rate
    # the policy is consulted and the sensors are read at, `steps_per_decision` the stride it
    # was reached by. Every CSV written before these existed had `decision_hz == step_hz`,
    # so the two files still concatenate.
    "rate_set", "decision_hz", "steps_per_decision",
    "row", "render", "policy", "sensors", "camera_read_mode", "camera_rig", "camera_count",
    "camera_size", "camera_mb_per_step", "camera_hz", "camera_draw_hz", "stack_size",
    "norm_pixel", "observation_kind", "force_fps", "max_lateral_m",
    "status", "skip_reason", "steps", "measured_steps", "warmup_steps",
    "sim_seconds", "wall_seconds",
    "realtime_factor", "reset_seconds",
    "step_ms_mean", "step_ms_median", "step_ms_p95", "step_ms_p99", "step_ms_max",
    "policy_ms_median", "sensor_ms_median", "rig_ms_median", "arrive_dest", "ended_by",
]

# `tick` and `decide` are two columns because they are two rates: the world tick is how far
# `env.step` advances, and `decide` is how often the policy is consulted and the sensors read.
# They are equal unless `--decision-hz` was passed, which is exactly why both are shown - a
# single column would make a 100/20/100 run indistinguishable from a 100/100/100 one.
HEADER = (
    "  #  render     policy  sensors              tick  decide  physics  rpt   steps"
    "   sim s  wall s  x real  ms/step  policy    p95"
)


def table_line(record):
    if record["status"] != "ok":
        return "  {:<2} {:<10} {:<7} {:<18} {:>6} Hz   skipped: {}".format(
            record["row"], record["render"], record["policy"], record["sensors"] or "-",
            _hz(record["step_hz"]), record["skip_reason"],
        )
    return (
        "  {:<2} {:<10} {:<7} {:<18} {:>4} Hz {:>4} Hz {:>5} Hz  x{:<3} {:>5}  {:>6.1f}"
        "  {:>6.1f}  {:>6.2f}x {:>7.2f} {:>7.2f} {:>6.2f}".format(
            record["row"], record["render"], record["policy"], record["sensors"] or "-",
            _hz(record["step_hz"]), _hz(record["decision_hz"] or record["step_hz"]),
            _hz(record["physics_hz"]), record["decision_repeat"],
            record["measured_steps"], record["sim_seconds"], record["wall_seconds"],
            record["realtime_factor"], record["step_ms_median"],
            record["policy_ms_median"], record["step_ms_p95"],
        )
    )


def _hz(value):
    return f"{float(value):g}"


# Where the fuller explanation lives. Named in the listing and in the table's footer,
# because the question "what is row 5" is asked at the terminal rather than in a browser.
ROWS_DOC = "docs/step-timing-rows.md"


def row_listing(cameras=1):
    """Every row, rendered from `ROWS` itself.

    From the dict rather than from a copy, so a row added later cannot be missing here -
    the failure this exists to prevent is a listing that describes a tool from last month.
    """
    lines = ["  #  render     policy  sensors             physics  isolates"]
    for number, row in sorted(ROWS.items()):
        marks = "  [default]" if number in DEFAULT_ROWS else ""
        if row["policy"] == "remote":
            marks += "  [needs --policy-url]"
        lines.append(
            "  {:<2} {:<10} {:<7} {:<19} {:<8} {}{}".format(
                number,
                row["render"] or "none",
                row["policy"],
                ",".join(carried(row, cameras)) or "-",
                f"{_hz(row['physics_hz'])} Hz" if row.get("physics_hz") else "dataset",
                row["isolates"],
                marks,
            )
        )
    lines.append("")
    lines.append(f"  full reference: {ROWS_DOC}")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Time env.step against the simulated time it buys, at every rate a "
        "workspace holds.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    # `*` rather than `+` so `--list-rows` is answerable without a dataset: it is a question
    # about the tool, not about a workspace. The refusal below says so in those terms, which
    # argparse's own "the following arguments are required" would not.
    parser.add_argument("dataset", nargs="*", help="Converted dataset directories to time.")
    parser.add_argument(
        "--list-rows", action="store_true",
        help=f"Print what every row measures and exit. The same table, longer, is in {ROWS_DOC}.",
    )
    parser.add_argument(
        "--rows", default=",".join(str(number) for number in DEFAULT_ROWS),
        help="Which configurations to run, comma separated - see --list-rows, or "
        f"{ROWS_DOC}. One row on its own is `--rows 5`. The default is every row but 7, "
        "which needs a display; rows 1 and 2 differ only in who drives, so read `policy` "
        "rather than the difference between them.",
    )
    parser.add_argument(
        "--label", default=None,
        help="Names the machine in every CSV row and in the file name. Defaults to the "
        "hostname, which in a container is a random id - so name it there.",
    )
    parser.add_argument(
        "--step-hz", type=float, default=None,
        help="Decision rate. Defaults to each dataset's own, read off `metadata.ts`, because "
        "a dataset can only be replayed at the rate it was written at.",
    )
    parser.add_argument(
        "--physics-hz", type=float, default=None,
        help="Pin the physics tick and let decision_repeat fall out of it. Without this the "
        "pair comes from --step-hz, which at 10 Hz means 50 Hz physics - half of what CARLA "
        "integrates at the same tick rate. 100 with --step-hz 10 is the matched shape.",
    )
    parser.add_argument(
        "--rate-sets", default=None,
        help="A CSV of whole simulator configurations - `name,step_hz,decision_hz,physics_hz`, "
        "one a row - driven one after another in this process, into one CSV. "
        "scripts/rate-sets.csv is the one in the repo, and the path is the same inside the "
        "container and out. It is the way to compare 10/10/50 against 100/20/100 without "
        "three flags per run and three files to reconcile afterwards, and running them in one "
        "process is what keeps them comparable: `prime` is paid once and the machine columns "
        "are identical. A set drives only the dataset written at its own step rate. Cannot be "
        "combined with --step-hz, --decision-hz or --physics-hz - the file is the source.",
    )
    parser.add_argument(
        "--decision-hz", type=float, default=None,
        help="The middle rate of world tick / decision + camera / physics, when it should be "
        "slower than the simulator. Must divide --step-hz. MetaDrive has no clock for it - "
        "env.step is the world tick, the policy call and the camera draw all at once - so it "
        "is a stride counted in this loop. On the replay row it gates the sensor and camera "
        "read alone, MetaDrive calling the replay policy in-engine on every step whatever "
        "this says. --step-hz 100 --decision-hz 20 is 100/20/100, and what openpilot's "
        "bridge is written for.",
    )
    parser.add_argument("--policy-url", default=None, help="Where row 3's model is listening.")
    parser.add_argument(
        "--policy-sensors",
        default=None,
        help="Comma-separated sensors row 3 sends its model, overriding the row's own list. "
        "`imu,route` is what examples/openpilot_server.py needs.",
    )
    parser.add_argument(
        "--camera-rig", default=None,
        help="A CARLA-shaped camera spec (see tools/camera_rig.py), the same file "
        "sensor-survey.sh takes. Its cameras replace the single invented 320x180 one on "
        "every offscreen row, so the sweep prices the vehicle's own vision rather than a "
        "size chosen here. Their read-back is timed separately as rig_ms.",
    )
    parser.add_argument(
        "--max-lateral-m", type=float, default=SWEEP_MAX_LATERAL_M,
        help="How far off its recorded route a car may get before the episode ends. "
        "MetaDrive's own 4 m judges driving and cuts the IDM rows off after a couple of "
        "dozen steps; a benchmark wants the sample. Recorded in the CSV as max_lateral_m.",
    )
    parser.add_argument(
        "--warmup", type=int, default=WARMUP_STEPS,
        help="Steps driven before the clock starts, so no row is charged for the graphics "
        "driver warming up. They are outside wall_seconds as well as outside the "
        "distribution; reset_seconds carries the terrain build separately.",
    )
    parser.add_argument("--max-steps", type=int, default=0, help="Cap the drive. 0 is no cap.")
    parser.add_argument("--scenario-index", type=int, default=0, help="Which scenario to drive.")
    parser.add_argument("--csv-dir", default=None, help="Where the CSV lands.")
    parser.add_argument("--csv", default=None, help="Name the CSV outright. Refuses to overwrite.")
    parser.add_argument("--no-csv", action="store_true", help="Print the table and write nothing.")
    arguments = parser.parse_args()

    # One implicit set when no file was given, so the loop below has one shape rather than two.
    # `step_hz=None` there means "each dataset's own", which is what an unflagged sweep does and
    # what a rate-set file can never say - a set names a world tick by definition.
    if arguments.rate_sets:
        conflicting = [
            name for name, value in (
                ("--step-hz", arguments.step_hz),
                ("--decision-hz", arguments.decision_hz),
                ("--physics-hz", arguments.physics_hz),
            ) if value is not None
        ]
        if conflicting:
            print(
                "result       FAILED: --rate-sets is the source of the rates, so {} cannot be "
                "passed beside it. Put the rate in the file.".format(", ".join(conflicting))
            )
            return 1
        try:
            rate_sets = load_rate_sets(arguments.rate_sets)
        except ValueError as error:
            print(f"result       FAILED: {error}")
            return 1
    else:
        rate_sets = [
            RateSet(
                name="",
                step_hz=arguments.step_hz,
                decision_hz=arguments.decision_hz,
                physics_hz=arguments.physics_hz,
            )
        ]

    rig = None
    rig_tick_s = None
    if arguments.camera_rig:
        from camera_rig import MAX_IMAGE_BUFFERS, STEP_S, RigError, load_rig

        # `None`, so `load_rig` does not judge the rate here. This sweep drives *every* rate a
        # workspace holds, so there is no one read interval to check against until a dataset is
        # in hand - which is what the per-dataset note below does, against the spec's own
        # declared `tick_rate` rather than against an assumed 0.1 s.
        try:
            rig = load_rig(arguments.camera_rig, read_interval_s=None)
        except RigError as error:
            print(f"result       FAILED: camera rig rejected: {error}")
            return 1
        rig_tick_s = rig.tick_rate_s or STEP_S
        if len(rig) > MAX_IMAGE_BUFFERS:
            print(
                f"result       FAILED: camera rig rejected: {len(rig)} cameras is more than "
                f"the {MAX_IMAGE_BUFFERS} image buffers panda3d holds reliably. Past it "
                "MetaDrive's reset fails intermittently, which looks like a working rig "
                f"until it does not. Drop {len(rig) - MAX_IMAGE_BUFFERS} camera(s)."
            )
            return 1

    if arguments.list_rows:
        print(row_listing(len(rig) if rig else 1))
        return 0
    if not arguments.dataset:
        print(
            "result       FAILED: name at least one dataset directory to time, e.g.\n"
            "             tools/step_timing.py workspaces/junction-1/scenarionet-10hz\n"
            "             (--list-rows needs no dataset; it only says what each row measures)"
        )
        return 1

    try:
        wanted = [int(part) for part in arguments.rows.split(",") if part.strip()]
    except ValueError:
        print("result       FAILED: --rows takes numbers, e.g. --rows 1,2")
        return 1
    unknown = [number for number in wanted if number not in ROWS]
    if unknown:
        print(f"result       FAILED: no such row(s): {unknown}")
        return 1

    stamp = time.strftime("%Y-%m-%d-%H:%M:%S")
    facts = machine()
    label = arguments.label or os.environ.get("STEP_TIMING_LABEL") or facts["host"]
    ceiling = _max_texture_dimension()
    has_display = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))

    print("step timing  {}      label {}      {}".format(
        ", ".join(os.path.basename(os.path.normpath(one)) for one in arguments.dataset),
        label, stamp,
    ))
    print("  cpu  {}, {} threads      docker  {}".format(
        facts["cpu_model"] or "unknown", facts["cpu_threads"], facts["in_docker"]))
    gl = f"{ceiling} px" if ceiling else "no context"
    print("  gpu  {}      gl  {}".format(facts["gpu_name"] or "none reported", gl))
    print("  env  python {} / numpy {} / {}".format(
        facts["python"], facts["numpy"], facts["metadrive"]))
    # Said here because it is the largest single thing being measured and was invisible: the
    # camera is drawn and read on every offscreen row, and its cost lands inside ms/step.
    if any(ROWS[number]["render"] == "offscreen" for number in wanted):
        if rig is None:
            print("  cam  {}x{} RGB, 3-frame stack, drawn and read inside env.step on every "
                  "offscreen row".format(*CAMERA_SIZE))
            print("       one camera, invented here rather than by a vehicle - "
                  "--camera-rig prices a real rig")
        else:
            # `describe` resolves each camera's real aim, which is worth printing every run:
            # the spec this was built for disagrees with itself about the sign of `yaw`, so
            # two of its four side cameras are named backwards whichever reading is taken.
            for line in rig.describe():
                print(f"  {line}" if line.startswith(" ") else f"  rig  {line}")
    print("")

    # Before `prime`, not after the sweep. The only way to collide is a hand-named `--csv`
    # (the stamp is per-second otherwise), and spending six minutes of measuring and *then*
    # declining to write it is the wrong order to find that out in.
    writer = RowWriter(csv_path(arguments, label, stamp), enabled=not arguments.no_csv)
    if writer.enabled and os.path.exists(writer.path):
        print(f"result       FAILED: {writer.path} already exists; nothing was measured")
        return 1

    print(HEADER)

    prime(arguments, wanted, rig, rate_set=rate_sets[0])

    records = []
    measured = 0
    # Ctrl-C during a sweep is ordinary - a twelve-row run with a real rig is minutes of
    # GPU time, and a reader who has seen enough should not have to hunt for what it
    # measured. Every row is already flushed; this only names the file and says it is short.
    try:
        for rate_set in rate_sets:
            if rate_set.name:
                # One line naming the configuration, rather than a column on a table that is
                # already at the width of a terminal. The three rates in the order the question is
                # asked in, so a reader matching this against a spreadsheet does not have to
                # reorder anything.
                print(
                    f"  set  {rate_set.name}  {rate_set_label(rate_set)} Hz"
                    "  (world tick / decision + camera / physics)"
                )
            drove_a_dataset = False
            for dataset in arguments.dataset:
                dataset = os.path.abspath(dataset)
                try:
                    native_hz = dataset_step_hz(dataset, arguments.scenario_index)
                except Exception as error:  # noqa: BLE001 - reported per dataset, never fatal
                    print(f"  {dataset} could not be read: {error}")
                    continue

                # A named set drives only the dataset written at its own world tick, and that is
                # the one place `--rate-sets` behaves differently from the flags. A set is a whole
                # simulator configuration, so running it against a tape written at another rate
                # measures a configuration nobody asked for - and every replay row of it would skip
                # anyway. Without a set the sweep still drives every rate a workspace holds, each
                # at its own, which is the comparison it exists to make.
                if rate_set.name and abs(rate_set.step_hz - native_hz) > 1e-9:
                    continue
                drove_a_dataset = True

                # Said rather than refused. `load_rig` rejects a spec whose `tick_rate` is not
                # `camera_rig.STEP_S`, because nothing there resamples - but this sweep drives every
                # rate a workspace holds, and what a rig costs at 100 Hz is one of the most useful
                # numbers in the table. So the cameras draw at whatever the dataset's rate is,
                # and the run says so instead of quietly delivering ten frames where the spec
                # asked for one.
                # Resampling a rig to its own tick is Phase 2 of the adjustable-sample-rate plan.
                if rig is not None:
                    # Against the rate the cameras are really read at, which `--decision-hz` moves
                    # off the step rate. A rig declaring 20 Hz on a 100 Hz dataset is exactly right
                    # under `--decision-hz 20`, and saying otherwise would be the note crying wolf
                    # at the one configuration it was written to bless.
                    try:
                        rig_stride = decision_stride(
                            rate_set.step_hz or native_hz, rate_set.decision_hz
                        )
                    except ValueError:
                        rig_stride = 1
                    drawn_hz = (rate_set.step_hz or native_hz) / rig_stride
                    if abs(drawn_hz - 1.0 / rig_tick_s) > 1e-9:
                        name = os.path.basename(os.path.normpath(dataset))
                        print(
                            f"  rig  {name}: the spec ticks at {rig_tick_s:g} s "
                            f"({1.0 / rig_tick_s:g} Hz) and these cameras are read at "
                            f"{drawn_hz:g} Hz, {drawn_hz * rig_tick_s:g}x that. Nothing resamples; "
                            "--decision-hz is what matches the two."
                        )

                for number in wanted:
                    row = ROWS[number]
                    step_hz = rate_set.step_hz or native_hz
                    # A set's physics outranks row 5's own pin, because a set describes the whole
                    # configuration and a row that overrode part of it would be measuring something
                    # the table does not name. Rows 2 and 5 then coincide, which the `physics`
                    # column shows; the footer says so once rather than per row.
                    physics_hz = rate_set.physics_hz or row.get("physics_hz")
                    offscreen = row["render"] == "offscreen"
                    # Declared here as well as re-derived in `drive`, so a row that skips before it
                    # ever builds an env still records the rate it would have run at. A skip rather
                    # than a fatal error, because this sweep drives *every* rate a workspace holds:
                    # `--decision-hz 20` divides a 100 Hz dataset and not a 10 Hz one, and the row
                    # that cannot have it should say so beside the row that can.
                    stride, stride_refusal = 1, ""
                    try:
                        stride = decision_stride(step_hz, rate_set.decision_hz)
                    except ValueError as error:
                        stride_refusal = str(error)
                    record = dict(
                        (field, "") for field in FIELDS
                    )
                    record.update(facts)
                    record.update(
                        label=label, timestamp=stamp, gl_max_texture=ceiling or "",
                        rate_set=rate_set.name,
                        dataset=os.path.basename(os.path.normpath(dataset)),
                        row=number, render=row["render"] or "none", policy=row["policy"],
                        # Declared, so a row that never gets as far as an env still says what it
                        # would have carried. A row that runs overwrites all four from its own env.
                        sensors=",".join(carried(row, len(rig) if rig else 1)),
                        # MetaDrive draws the camera and reads it inside `env.step` when
                        # `image_observation` is on (`image_obs.py:85`), so the image costs what it
                        # costs there on every offscreen row. No row reads one in the timing
                        # loop as well: that forces a second render pass and would charge the
                        # benchmark for a frame no training loop draws.
                        camera_read_mode="observation" if offscreen else "none",
                        camera_rig=os.path.basename(rig.path) if rig is not None else "",
                        camera_count=(len(rig) if rig else 1) if offscreen else 0,
                        camera_size=(
                            "x".join(str(one) for one in (
                                (rig.cameras[0].width, rig.cameras[0].height)
                                if rig else CAMERA_SIZE))
                            if offscreen else ""),
                        camera_mb_per_step=round(rig.megabytes, 3) if rig and offscreen else "",
                        # Two columns because they are two rates and only one of them is ours to
                        # move. `camera_hz` is what the cameras are *read* at, which `--decision-hz`
                        # decimates; `camera_draw_hz` is what they are *drawn* at, which is the step
                        # rate and nothing else - MetaDrive redraws every buffer once per `env.step`
                        # (`base_engine.py:458`), and deactivating them in between was measured and
                        # saves 1% (see docs/step-timing-rows.md). One column would read as though a
                        # decimated camera were cheap, which it is not.
                        camera_hz=step_hz / stride if offscreen else "",
                        camera_draw_hz=step_hz if offscreen else "",
                        decision_hz=step_hz / stride, steps_per_decision=stride,
                        stack_size=3 if offscreen else "", norm_pixel="True" if offscreen else "",
                        observation_kind="image+state41" if offscreen else "lidarstate161",
                        max_lateral_m=arguments.max_lateral_m,
                        step_hz=step_hz, physics_hz=physics_hz or (1.0 / step_config(step_hz)[
                            "physics_world_step_size"]),
                        status="skipped",
                    )

                    reason = ""
                    if stride_refusal:
                        reason = stride_refusal
                    elif row["render"] in ("offscreen", "3D") and ceiling is None:
                        reason = "no GL context, so no camera can be built here"
                    elif row["render"] == "3D" and not has_display:
                        reason = "no display ($DISPLAY and $WAYLAND_DISPLAY unset)"
                    elif row["policy"] == "remote" and not arguments.policy_url:
                        reason = "needs --policy-url"

                    env = None
                    if not reason:
                        try:
                            env = build_env(
                                dataset, row, step_hz, physics_hz, rig=rig,
                                max_lateral_m=arguments.max_lateral_m,
                            )
                            result = drive(
                                env, row, arguments, url=arguments.policy_url, rig=rig,
                                decision_hz=rate_set.decision_hz,
                            )
                            reason = result.pop("skip_reason", "")
                        except Exception as error:  # noqa: BLE001 - one row failing is not the run
                            reason = f"{type(error).__name__}: {error}"
                        finally:
                            if env is not None:
                                env.close()

                    if reason:
                        record["skip_reason"] = reason.replace("\n", " ")[:200]
                    else:
                        record.update(
                            status="ok", skip_reason="",
                            physics_ticks_per_sim_second=(
                                result["decision_repeat"] / result["sim_dt_s"]),
                            step_ms_mean=result["step"]["mean"],
                            step_ms_median=result["step"]["median"],
                            step_ms_p95=result["step"]["p95"],
                            step_ms_p99=result["step"]["p99"],
                            step_ms_max=result["step"]["max"],
                            policy_ms_median=result["policy"]["median"],
                            sensor_ms_median=result["sensor"]["median"],
                            rig_ms_median=result["rig"]["median"],
                        )
                        for key in (
                            "sensors", "camera_count", "camera_size", "camera_mb_per_step",
                            "stack_size", "observation_kind",
                            "gl_renderer", "force_fps", "scenario_id", "sim_dt_s", "physics_dt_s",
                            "decision_repeat", "steps", "measured_steps", "warmup_steps",
                            "sim_seconds",
                            "wall_seconds", "realtime_factor", "reset_seconds", "arrive_dest",
                            "ended_by",
                        ):
                            record[key] = result[key]
                        record["physics_hz"] = 1.0 / result["physics_dt_s"]
                        measured += 1

                    records.append(record)
                    writer.write(record)
                    print(table_line(record))
            if rate_set.name and not drove_a_dataset:
                # Named rather than left as a silent gap in the table. A set that measured
                # nothing looks identical to one that was never in the file.
                print(
                    f"       no {rate_set.step_hz:g} Hz dataset in this workspace, so this set "
                    "drove nothing. Convert one:\n"
                    f"       uv run osm-scenario convert -w <workspace> --config "
                    f"config/default.yaml --routes <workspace>/routes/routes.json "
                    f"--step-hz {rate_set.step_hz:g}"
                )
    except KeyboardInterrupt:
        print("")
        writer.close()
        if writer.wrote_anything:
            print(f"  csv  {writer.path}")
        print(
            f"result       INTERRUPTED after {len(records)} row(s); "
            "what was measured is in the CSV above"
        )
        # `os._exit`, not `return 130`: panda3d segfaults tearing an engine down out from
        # under a KeyboardInterrupt, so the process died with 139 and the exit status stopped
        # meaning anything. Everything that had to reach disk already has - the CSV was
        # flushed row by row and closed just above - so there is nothing left for a normal
        # shutdown to do except crash. stdout is block-buffered when redirected to a file,
        # hence the explicit flush.
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(130)

    print("")
    left = [number for number in sorted(ROWS) if number not in wanted]
    if left:
        print("  not run: {}  (--rows adds them; --list-rows says what they measure)".format(
            ",".join(str(number) for number in left)))
    print("  `policy` is the driver's own cost, timed around the call - read that rather than")
    print("  subtracting one row from another, which about 1 ms of run-to-run spread swamps.")
    if arguments.decision_hz:
        print("  decide is --decision-hz: how often the policy is asked and the sensors read.")
        print("  On the replay row it gates the read alone - MetaDrive drives the ego from the")
        print("  tape in-engine on every step, so there is no decision there to decimate. The")
        print("  camera still *draws* every step - camera_draw_hz - because MetaDrive redraws")
        print("  every buffer once per env.step and deactivating them in between was measured")
        print("  at 1% of a 26 ms step. What a lower decide rate saves is the read: rig_ms.")
    if arguments.rate_sets:
        print(f"  set is a whole configuration from {arguments.rate_sets}, and each drives")
        print("  only the dataset written at its own world tick.")
        print("  rate_set names it in the CSV.")
        if 5 in wanted and any(one.physics_hz for one in rate_sets):
            print("  a set's physics outranks row 5's own 100 Hz pin, so rows 2 and 5 are the")
            print("  same measurement under any set that names one - the physics column shows it.")
    print("  rpt is decision_repeat: physics ticks per env.step, so ms/step is not comparable")
    print("  across rates - x real is. Every offscreen row draws and reads a camera; MetaDrive")
    print("  builds the observation out of it inside env.step, so that cost is in ms/step and")
    print("  never in the policy or sensor columns. Row 2 against row 6 is what it costs.")
    if rig is None:
        print("  that camera is one 320x180 buffer this tool registers, not a vehicle's rig:")
        print("  --camera-rig <spec> mounts the real cameras and prices them instead.")
    else:
        print("  rig_ms in the CSV is reading the mounted cameras back out - a buffer copy,")
        print("  not a second render; the drawing of them is inside ms/step with everything else.")
    print(f"  what every row and column means: {ROWS_DOC}  (or --list-rows)")

    # Every row is already on disk, flushed as it was measured. This only closes the handle
    # and names the file, so the last line still means "this run finished".
    writer.close()
    if writer.wrote_anything:
        print(f"  csv  {writer.path}")

    print("result       {}".format("OK" if measured else "FAILED: nothing was measured"))
    return 0 if measured else 1


def prime(arguments, wanted, rig=None, rate_set=None):
    """Build and throw away one env before anything is measured.

    Measured and not guessed: the **first** env of a process is systematically dearer than
    the ones after it - a graphics driver compiles shaders and fills caches on first use, and
    that cost lands on whichever row happens to be first. Since the headline here is one row
    subtracted from another, a first-row penalty is not noise but a bias in the answer. One
    short throwaway drive moves it out of the measurements.

    Never fatal: if this cannot run, the row that follows will say why properly.

    `rate_set` is the first configuration that will be measured, so the warm-up is built the
    way the first real row is. It matters under `--rate-sets`, where the rates live on the set
    and `arguments.step_hz` is None - without it the warm-up happened to be at the first
    dataset's own rate, which is right only when the first set asks for that rate.
    """
    # The first row that drives itself, not simply the first: a remote row needs a server, so
    # keying off `wanted[0]` alone would skip the warm-up whenever row 3 came first and leave
    # the first-use cost on whichever row followed it.
    number = next((one for one in wanted if ROWS[one]["policy"] != "remote"), None)
    if number is None:
        return
    row = ROWS[number]
    env = None
    try:
        native_hz = dataset_step_hz(arguments.dataset[0], arguments.scenario_index)
        env = build_env(
            arguments.dataset[0], row,
            (rate_set.step_hz if rate_set else None) or native_hz,
            (rate_set.physics_hz if rate_set else None) or row.get("physics_hz"), rig=rig,
            max_lateral_m=arguments.max_lateral_m,
        )
        env.reset(seed=arguments.scenario_index)
        for _ in range(PRIME_STEPS):
            _, _, terminated, truncated, _ = env.step([0.0, 0.0])
            if terminated or truncated:
                break
    except Exception:  # noqa: BLE001 - a warm-up that fails is not a result
        pass
    finally:
        if env is not None:
            env.close()


def csv_path(arguments, label, stamp):
    """Where this run's file goes. Stamped, so a second run never takes the first one's.

    Settled before anything is measured, which is what lets `RowWriter` write as it goes: the
    path depends on the *arguments*, never on a result.
    """
    if arguments.csv:
        return arguments.csv
    name = f"step-timing-{label}-{stamp}.csv"
    if arguments.csv_dir:
        return os.path.join(arguments.csv_dir, name)
    # Beside the workspace's other reports, found from the dataset rather than assumed.
    workspace = os.path.dirname(os.path.abspath(arguments.dataset[0]))
    return os.path.join(workspace, "reports", name)


class RowWriter:
    """The CSV, written a row at a time as each one is measured.

    It used to be written once at the end, out of a list every row had been appended to - so a
    sweep that did not reach its last line left **nothing**, however much it had measured. A
    full twelve-row run with a seven-camera rig is several minutes of GPU time, and one
    interrupt discarded eleven finished rows of it.

    Opened lazily, on the first row rather than up front, because a run that measures nothing
    must still leave no file behind - the same thing `--no-csv` asks for, and the reason
    `main` can hold a writer it may never use. Flushed every row, which is what makes the file
    readable *during* a run: a flush is microseconds against rows that are tens of seconds, so
    it costs nothing measurable and it is the whole point.

    Refusing an existing path is the caller's job, before any of this - see `main`.
    """

    def __init__(self, path, enabled=True):
        self.path = path
        self.enabled = enabled
        # Separate from `_handle`, which `close` nulls: whether a file was ever opened has to
        # survive closing it, because the line naming the file is printed after the close.
        self.wrote_anything = False
        self._handle = None
        self._writer = None

    def write(self, record):
        if not self.enabled:
            return
        if self._handle is None:
            directory = os.path.dirname(os.path.abspath(self.path))
            if directory:
                os.makedirs(directory, exist_ok=True)
            # Not a context manager, which is the whole point: the handle has to outlive
            # this call so the next row can be appended to it. `close` is the other half.
            self._handle = open(  # noqa: SIM115
                self.path, "w", newline="", encoding="utf-8"
            )
            self._writer = csv.DictWriter(self._handle, fieldnames=FIELDS)
            self._writer.writeheader()
            self.wrote_anything = True
        self._writer.writerow(record)
        self._handle.flush()

    def close(self):
        if self._handle is not None:
            self._handle.close()
            self._handle = None


if __name__ == "__main__":
    raise SystemExit(main())
