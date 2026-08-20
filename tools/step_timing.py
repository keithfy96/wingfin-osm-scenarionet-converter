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

Every run writes its own CSV, named for the moment it started, carrying the machine it was
measured on in every row - so a file from this laptop and a file from a container on another
box concatenate into one spreadsheet with nothing to line up by hand.
"""

from __future__ import annotations

import argparse
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
    sim_step_seconds,
    step_config,
)

# How many steps are dropped from the *distribution* before it is summarised. They stay in
# `wall_seconds`, because the honest total includes them: the first steps of a drive carry
# lazily-built buffers and a cold cache, which a training loop also pays once.
WARMUP_STEPS = 20

# Steps of a throwaway drive before anything is measured. See `prime`.
PRIME_STEPS = 40

# The camera every offscreen row registers, so that "with sensors" and "without" differ in
# what is read rather than in what is drawn. `make_env`'s own offscreen default is 320x240;
# named here so both rows are the same size whatever that default does later.
CAMERA_SIZE = (320, 180)


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

DEFAULT_ROWS = (1, 2)


def carried(row):
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
    camera = ("camera",) if row["render"] == "offscreen" else ()
    return camera + tuple(row["read"])


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


def build_env(dataset, row, step_hz, physics_hz):
    """A `ScenarioEnv` configured for one row, through the same `make_env` a drive uses."""
    from agent_env import make_env
    from metadrive.policy.replay_policy import ReplayEgoCarPolicy
    from policy_client import sensor_config

    overrides = dict(rate_keys(step_hz, physics_hz))
    if row["policy"] == "replay":
        # `make_env` deliberately leaves `agent_policy` alone so the action reaches the car;
        # replay is the one row that wants it set, and it goes through `**overrides` like any
        # other MetaDrive key.
        overrides["agent_policy"] = ReplayEgoCarPolicy
    if row["render"] == "offscreen":
        # Registered on every offscreen row, including the ones that read nothing, so that
        # "with sensors" and "without" differ in what is read rather than in what is drawn.
        overrides["sensors"] = sensor_config(("camera",), camera_size=CAMERA_SIZE)
    return make_env(dataset, render=row["render"], **overrides)


def drive(env, row, arguments, url=None):
    """Drive one scenario under one row's configuration and return what it cost.

    Returns a dict of measurements, or one carrying `skip_reason` when the dataset cannot be
    driven this way at all.
    """
    from agent_env import IdmDriver
    from policy_client import RemotePolicy, SensorPack

    started = time.perf_counter()
    observation, _ = env.reset(seed=arguments.scenario_index)
    reset_seconds = time.perf_counter() - started

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

    # What this run *carries*, asked of the live env rather than taken from the row
    # definition. A camera is only real when `image_observation` is on and one is registered,
    # and the frame's own shape is the honest camera size - so if anything ever stops
    # building one, the table says so instead of repeating what it was meant to do.
    has_camera = bool(env.config.get("image_observation")) and "rgb_camera" in (
        env.config.get("sensors") or {}
    )
    camera_size = ""
    stack_size = ""
    if isinstance(observation, dict) and "image" in observation:
        shape = observation["image"].shape
        camera_size = f"{int(shape[1])}x{int(shape[0])}"
        stack_size = int(shape[3]) if len(shape) > 3 else ""
    sensors = ",".join((("camera",) if has_camera else ()) + read)

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
        pack = None
    if pack is not None:
        pack.reset()

    budget = int(round(length * data_dt / sim_dt))
    if arguments.max_steps:
        budget = min(budget, arguments.max_steps)

    step_samples = []
    policy_samples = []
    sensor_samples = []
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
    while steps < budget:
        if steps == arguments.warmup:
            loop_started = time.perf_counter()
        mark = time.perf_counter()
        if pack is not None:
            pack()
        read_done = time.perf_counter()
        action = policy(observation) if policy is not None else [0.0, 0.0]
        decided = time.perf_counter()
        observation, _, terminated, truncated, info = env.step(action)
        stepped = time.perf_counter()

        if steps >= arguments.warmup:
            sensor_samples.append(read_done - mark)
            policy_samples.append(decided - read_done)
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
        camera_size=camera_size,
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
    "row", "render", "policy", "sensors", "camera_read_mode", "camera_size", "stack_size",
    "norm_pixel", "observation_kind", "force_fps",
    "status", "skip_reason", "steps", "measured_steps", "warmup_steps",
    "sim_seconds", "wall_seconds",
    "realtime_factor", "reset_seconds",
    "step_ms_mean", "step_ms_median", "step_ms_p95", "step_ms_p99", "step_ms_max",
    "policy_ms_median", "sensor_ms_median", "arrive_dest", "ended_by",
]

HEADER = (
    "  #  render     policy  sensors        decide  physics  rpt   steps   sim s  wall s"
    "  x real  ms/step  policy    p95"
)


def table_line(record):
    if record["status"] != "ok":
        return "  {:<2} {:<10} {:<7} {:<14} {:>6} Hz   skipped: {}".format(
            record["row"], record["render"], record["policy"], record["sensors"] or "-",
            _hz(record["step_hz"]), record["skip_reason"],
        )
    return (
        "  {:<2} {:<10} {:<7} {:<14} {:>4} Hz {:>5} Hz  x{:<3} {:>5}  {:>6.1f}  {:>6.1f}"
        "  {:>6.2f}x {:>7.2f} {:>7.2f} {:>6.2f}".format(
            record["row"], record["render"], record["policy"], record["sensors"] or "-",
            _hz(record["step_hz"]), _hz(record["physics_hz"]), record["decision_repeat"],
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


def row_listing():
    """Every row, rendered from `ROWS` itself.

    From the dict rather than from a copy, so a row added later cannot be missing here -
    the failure this exists to prevent is a listing that describes a tool from last month.
    """
    lines = ["  #  render     policy  sensors         physics  isolates"]
    for number, row in sorted(ROWS.items()):
        marks = "  [default]" if number in DEFAULT_ROWS else ""
        if row["policy"] == "remote":
            marks += "  [needs --policy-url]"
        lines.append(
            "  {:<2} {:<10} {:<7} {:<15} {:<8} {}{}".format(
                number,
                row["render"] or "none",
                row["policy"],
                ",".join(carried(row)) or "-",
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
        f"{ROWS_DOC}. The default pair differs only in who drives; read `policy` rather "
        "than the difference between them.",
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
    parser.add_argument("--policy-url", default=None, help="Where row 3's model is listening.")
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

    if arguments.list_rows:
        print(row_listing())
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
        print("  cam  {}x{} RGB, 3-frame stack, drawn and read inside env.step on every "
              "offscreen row".format(*CAMERA_SIZE))
    print("")
    print(HEADER)

    prime(arguments, wanted)

    records = []
    measured = 0
    for dataset in arguments.dataset:
        dataset = os.path.abspath(dataset)
        try:
            native_hz = dataset_step_hz(dataset, arguments.scenario_index)
        except Exception as error:  # noqa: BLE001 - reported per dataset, never fatal
            print(f"  {dataset} could not be read: {error}")
            continue

        for number in wanted:
            row = ROWS[number]
            step_hz = arguments.step_hz or native_hz
            physics_hz = arguments.physics_hz or row.get("physics_hz")
            offscreen = row["render"] == "offscreen"
            record = dict(
                (field, "") for field in FIELDS
            )
            record.update(facts)
            record.update(
                label=label, timestamp=stamp, gl_max_texture=ceiling or "",
                dataset=os.path.basename(os.path.normpath(dataset)),
                row=number, render=row["render"] or "none", policy=row["policy"],
                # Declared, so a row that never gets as far as an env still says what it
                # would have carried. A row that runs overwrites all four from its own env.
                sensors=",".join(carried(row)),
                # MetaDrive draws the camera and reads it inside `env.step` when
                # `image_observation` is on (`image_obs.py:85`), so the image costs what it
                # costs there on every offscreen row. No row reads one in the timing loop as
                # well: that forces a second render pass and would charge the benchmark for a
                # frame no training loop draws.
                camera_read_mode="observation" if offscreen else "none",
                camera_size="x".join(str(one) for one in CAMERA_SIZE) if offscreen else "",
                stack_size=3 if offscreen else "", norm_pixel="True" if offscreen else "",
                observation_kind="image+state41" if offscreen else "lidarstate161",
                step_hz=step_hz, physics_hz=physics_hz or (1.0 / step_config(step_hz)[
                    "physics_world_step_size"]),
                status="skipped",
            )

            reason = ""
            if row["render"] in ("offscreen", "3D") and ceiling is None:
                reason = "no GL context, so no camera can be built here"
            elif row["render"] == "3D" and not has_display:
                reason = "no display ($DISPLAY and $WAYLAND_DISPLAY unset)"
            elif row["policy"] == "remote" and not arguments.policy_url:
                reason = "needs --policy-url"

            env = None
            if not reason:
                try:
                    env = build_env(dataset, row, step_hz, physics_hz)
                    result = drive(env, row, arguments, url=arguments.policy_url)
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
                )
                for key in (
                    "sensors", "camera_size", "stack_size", "observation_kind",
                    "gl_renderer", "force_fps", "scenario_id", "sim_dt_s", "physics_dt_s",
                    "decision_repeat", "steps", "measured_steps", "warmup_steps", "sim_seconds",
                    "wall_seconds", "realtime_factor", "reset_seconds", "arrive_dest",
                    "ended_by",
                ):
                    record[key] = result[key]
                record["physics_hz"] = 1.0 / result["physics_dt_s"]
                measured += 1

            records.append(record)
            print(table_line(record))

    print("")
    left = [number for number in sorted(ROWS) if number not in wanted]
    if left:
        print("  not run: {}  (--rows adds them; --list-rows says what they measure)".format(
            ",".join(str(number) for number in left)))
    print("  `policy` is the driver's own cost, timed around the call - read that rather than")
    print("  subtracting one row from another, which about 1 ms of run-to-run spread swamps.")
    print("  rpt is decision_repeat: physics ticks per env.step, so ms/step is not comparable")
    print("  across rates - x real is. Every offscreen row draws and reads a camera; MetaDrive")
    print("  builds the observation out of it inside env.step, so that cost is in ms/step and")
    print("  never in the policy or sensor columns. Row 2 against row 6 is what it costs.")
    print(f"  what every row and column means: {ROWS_DOC}  (or --list-rows)")

    if not arguments.no_csv and records:
        path = csv_path(arguments, records[0]["dataset"], label, stamp)
        if os.path.exists(path):
            print(f"  csv  NOT written: {path} already exists")
            return 1
        directory = os.path.dirname(os.path.abspath(path))
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDS)
            writer.writeheader()
            for record in records:
                writer.writerow(record)
        print(f"  csv  {path}")

    print("result       {}".format("OK" if measured else "FAILED: nothing was measured"))
    return 0 if measured else 1


def prime(arguments, wanted):
    """Build and throw away one env before anything is measured.

    Measured and not guessed: the **first** env of a process is systematically dearer than
    the ones after it - a graphics driver compiles shaders and fills caches on first use, and
    that cost lands on whichever row happens to be first. Since the headline here is one row
    subtracted from another, a first-row penalty is not noise but a bias in the answer. One
    short throwaway drive moves it out of the measurements.

    Never fatal: if this cannot run, the row that follows will say why properly.
    """
    if not wanted:
        return
    row = ROWS[wanted[0]]
    if row["policy"] == "remote":
        return
    env = None
    try:
        native_hz = dataset_step_hz(arguments.dataset[0], arguments.scenario_index)
        env = build_env(
            arguments.dataset[0], row, arguments.step_hz or native_hz,
            arguments.physics_hz or row.get("physics_hz"),
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


def csv_path(arguments, dataset_name, label, stamp):
    """Where this run's file goes. Stamped, so a second run never takes the first one's."""
    if arguments.csv:
        return arguments.csv
    name = f"step-timing-{label}-{stamp}.csv"
    if arguments.csv_dir:
        return os.path.join(arguments.csv_dir, name)
    # Beside the workspace's other reports, found from the dataset rather than assumed.
    workspace = os.path.dirname(os.path.abspath(arguments.dataset[0]))
    return os.path.join(workspace, "reports", name)


if __name__ == "__main__":
    raise SystemExit(main())
