"""Drive a converted dataset in MetaDrive, with terrain settings that fit an OSM-sized map.

    <metadrive-checkout>/.venv/bin/python tools/drive.py <dataset> --render 3D

Like `check_dataset.py` this is not part of the package and imports nothing from it, because
it does not run on the same Python: the repo targets 3.10 and numpy 2, both MetaDrive
checkouts run 3.8 and numpy 1.24.

`python -m scenarionet.sim` exists and loads the same dataset, so why this. Because in 3D it
shows a map whose roads stop and an ego that sinks into the ground, and none of the four
settings that fix that can be reached from it. What it leaves at its defaults:

* **`map_region_size` (1024).** The terrain is one square of exactly this many metres, centred
  on the world origin - `base_engine.py:386` hard-codes `center_p = [0, 0]` - and the loader
  centralises the scenario on the ego's start, so the square is centred on wherever the drive
  begins. Outside it there is no ground and no flattened road at all. Rather than guess, this
  script measures each scenario and picks the smallest power of two that covers it.

* **The semantic texture, which has no config key at all.** MetaDrive builds the image that
  paints road surface and lane lines at `map_region_size x 22` pixels square - or x11 at 4096
  (`constants.py:499`): 22528 at 1024, 45056 at 4096. A GL context reports its own ceiling -
  measured 16384 on this machine's Intel iGPU and 32768 on its RTX 4050 - and past it the
  texture cannot be uploaded, which is what "the roads stop" looks like. There is no option for
  the 22, so it is patched below; see `_set_semantic_detail`. The ceiling itself is asked of a
  throwaway GL context rather than assumed, because it doubles between this machine's two GPUs
  and the whole resolution follows from it; see `_max_texture_dimension`.

* **Lane-line width, which also has no config key.** `terrain.py:625` passes literal pixel
  thicknesses to `BaseMap.get_semantic_map`, so a line is `thickness / pixels_per_meter` metres
  wide and its real width moves with the size of the map. `--line-width-m` asks in metres
  instead and works out the pixels; see `_set_line_width`.

* **A painted line is drawn short, and that is a fault rather than a setting.**
  `resample_polyline` steps with `np.arange(0, length, interval)` and never reaches the endpoint,
  so every line over 4 m loses up to a whole interval off its end - 554.7 m of paint across 585
  of `mosque`'s 690 painted lines. `_keep_line_ends` puts it back, unconditionally. The interval
  is `--line-interval-m`, because MetaDrive's 2 m also sags inside every curve.

* **`height_scale` (50).** `use_mesh_terrain` is false by default, so the car drives on a flat
  collision plane at z=0 while the *visible* ground is a noise heightfield around it. On
  `junction-1` at 50, the ground within 25 m of the drive reaches +10.4 m and 12% of it stands
  above the road - so the car is buried where it rises and floating where it dips. At 1 those
  become +0.2 m and 0%. The road itself is flattened either way; it is only the surroundings
  that need to come down to match, which is what `_ground_around` below measures.

* **`reactive_traffic`.** `sim.py` has the line commented out, so traffic there is always pure
  replay. Exposed here as `--reactive`, which matters once a scenario holds more than the ego.

It also stops at the end of the dataset. `sim.py` loops to 1,000,000 scenarios and finishes on
`AssertionError: Scenario Index ... out of range`, which reads like a fault in the data and is
not one.

Reports rather than asserts: every scenario prints what it did, so a partial failure says how
far the drive got.
"""

from __future__ import annotations

import argparse
import contextlib
import logging
import math
import os
import signal
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Imported at the top, unlike `agent_env` and `signal_control` below, because argparse needs
# the sensor names to build its help text before anything else runs. It costs nothing:
# `policy_client` imports only the standard library and `geodesy` at module level, and reaches
# for MetaDrive lazily inside the one function that needs it.
from frame_gate import install as install_frame_gate  # noqa: E402
from idm_driving import windowed_policy_class  # noqa: E402
from policy_client import HEAVY_SENSORS as HEAVY_POLICY_SENSORS  # noqa: E402
from policy_client import SENSORS as POLICY_SENSORS  # noqa: E402
from speed_profile import profile_speeds, speed_at  # noqa: E402

# Both ends of the range `base_env` accepts: it asserts the value is a power of two within
# these bounds before handing it to `TerrainProperty`.
MIN_REGION_M = 512
MAX_REGION_M = 4096

# The smallest ceiling any current GL context reports, used only when the real one cannot be
# had: `_max_texture_dimension` fails, or nothing is being rendered. Checked against the real
# number after the window exists either way.
FALLBACK_MAX_TEXTURE = 16384

# A real road marking is about 0.10-0.15 m wide. MetaDrive's own thicknesses are in *pixels*
# (2 for white, 3 for yellow, `terrain.py:625`), so what they draw depends on the size of the
# map: 2 px is 0.50 m on `mosque`'s 4096 m square at 4 px/m and 0.0625 m on `junction-1`'s
# 1024 m square at 32 - wrong in both directions from opposite ends. Asking in metres is the
# only form that is right on both. 0 restores MetaDrive's own pixel counts.
LINE_WIDTH_M = 0.15

# How finely a painted line is sampled before it is drawn into the semantic texture. MetaDrive's
# own default is 2 m (`base_map.py:194`, and `terrain.py:620` never passes anything else), which
# on a curve lays 2 m chords inside an arc whose road polygon is filled at full resolution - so
# the tarmac shows past its own line. At 0.25 m the paint is off the true edge by more than a
# texel over about a metre of `mosque`'s 23 km of painted line, against 52.7 m of bare road edge
# at 2 m.
#
# **It also moves the dashes, and that is the one thing here a reader will notice.** A broken line
# is drawn by skipping `floor(STRIPE_LENGTH * 2 / interval)` samples, so at 2 m it comes out 2 m
# on / 2 m off and at anything finer 3 m / 3 m. 3 m is what MetaDrive's own `STRIPE_LENGTH = 1.5`
# asks for and the 2 m is the flooring; `--line-interval-m 2.0` puts the old dashes back and still
# keeps the line ends, which is a separate fix - see `_keep_line_ends`.
LINE_INTERVAL_M = 0.25

# Ground either side of the flattened road, in metres. MetaDrive's 7 leaves our lanes as
# narrow ribbons because they are single carriageways rather than Waymo's full road surfaces.
DRIVABLE_AREA_EXTENSION_M = 10

# Not 0: at zero panda3d builds a singular transform for the terrain node and dies with
# "Tried to invert singular LMatrix4". 1 is the smallest value that keeps the ground within
# about a metre of the plane the car actually drives on.
HEIGHT_SCALE = 1

# MetaDrive's own value (`scenario_env.py:84`), repeated here so the flag that exposes it has a
# default that changes nothing. It is the width of the corridor the ego may drive in either side
# of its recorded route, and beyond it the episode ends as `out_of_road`. That is right for a
# replayed car, which is on the route by construction, and a real limit for anything that steers
# itself: the IDM ego reaches a measured 4.26 m, and a human takes a different turn on purpose.
MAX_LATERAL_DIST_M = 4.0

# How much of its target speed a car regulating to one actually holds, used only to size the
# step budget for `--agent-policy idm`. IDM has no integral: `acceleration` is
# `1 - (v/target)^DELTA`, which is zero *at* the target, so the car settles wherever a small
# positive command balances drag - and MetaDrive applies `setBrake(2.0)` on all four wheels
# even at full throttle (`base_vehicle.py:498`). Measured on `junction-1`, 1044 steps to
# `arrive_dest`: mean target 39.7 km/h against a mean actual 35.9, a ratio of 0.906, median
# deficit 2.2 km/h. 0.85 is that with room, and it is a *budget* figure - it bounds how long
# the run may take and changes nothing about how the car drives.
IDM_TRACKING_RATIO = 0.85

# What queueing behind other cars costs, as a multiple of the free-flow drive, and applied only
# under `--traffic live`. The budget already carries a named term for every other cause of
# legitimate extra time - the tracking deficit above, and the longest red in `_longest_red` -
# and had none at all for the one thing `--traffic live` exists to create. Measured on
# `junction-1` at 10 Hz, `--agent-policy idm`, each run driven to `arrive_dest` with the budget
# raised by hand: no traffic 412 steps; 25 cars on seed 0, 645; 50 cars on seed 0, 656; 25 cars
# on seed 3, 412. The delay is **seed-dependent rather than count-dependent** - doubling the
# cars added 11 steps and changing the seed removed all 233 - so no factor is right for every
# run and `--extra-seconds` exists beside this one. 2.0 against a worst measured 1.57 is the
# same "measured, with room" shape as the 0.906 -> 0.85 above.
#
# It costs nothing on a drive that arrives: the loop ends on `arrive_dest`, not on the bound.
# Only a drive that was going to fail anyway pays for the larger number.
TRAFFIC_DELAY_FACTOR = 2.0

# How far past the recording a drive has to run before `_tape_ran_out` says so, as a share of
# the recording. See that function for why it is not "one step past".
TAPE_OVERRUN_SHARE = 0.1


def _default_traffic_file(dataset: str) -> str:
    """`<workspace>/traffic/traffic.json`, worked out from the dataset directory.

    A dataset lives at `<workspace>/scenarionet-<rate>hz`, so the workspace is its parent -
    the same relationship `routes.json` and `signals.json` already sit in. The plan is *not*
    per rate: `traffic.json` carries geometry and no timing at all, so one file serves every
    rate a workspace holds, exactly as one `routes.json` does.
    """
    return os.path.join(os.path.dirname(os.path.abspath(dataset)), "traffic", "traffic.json")


def _next_power_of_two(value: float) -> int:
    size = MIN_REGION_M
    while size < value and size < MAX_REGION_M:
        size *= 2
    return size


def _region_for(dataset: str) -> tuple[int, float, str]:
    """The smallest terrain square covering every scenario, and what forced that size.

    Measured after centralisation, because that is the state MetaDrive drives: the loader
    shifts everything so the ego's first position is the origin, and the terrain square is
    centred there. So what matters is the largest single-axis offset from the ego's start,
    which is exactly what `TerrainProperty.point_in_map` tests.
    """
    import numpy
    from metadrive.scenario.utils import read_dataset_summary, read_scenario_data

    _, lookup, mapping = read_dataset_summary(dataset)
    furthest = 0.0
    where = "no map features"
    for file_name in lookup:
        path = os.path.join(dataset, mapping[file_name], file_name)
        scenario = read_scenario_data(path, centralize=True)
        points = [
            numpy.asarray(feature["polyline"])[:, :2]
            for feature in scenario["map_features"].values()
            if "polyline" in feature
        ]
        if not points:
            continue
        reach = float(numpy.abs(numpy.concatenate(points)).max())
        if reach > furthest:
            furthest = reach
            where = scenario["id"]
    return _next_power_of_two(2 * furthest), furthest, where


# MetaDrive's own physics tick, and the floor on how fine `step_config` will make one. Both
# keys are configurable; this is only the default `physics_world_step_size`.
PHYSICS_TICK_S = 0.02

# MetaDrive's own `env.step` rate, the product of its two defaults (0.02 x 5 = 0.1 s). What an
# unflagged run advances by, and so the rate `--decision-hz` is counted against when `--step-hz`
# was not passed.
DEFAULT_STEP_HZ = 10.0


def step_config(step_hz):
    """The two MetaDrive keys that make one `env.step` last `1 / step_hz` seconds.

    `env.step` advances `physics_world_step_size` x `decision_repeat` and nothing else - the
    chain is `base_env.py:435,462-466` -> `base_engine.py:431-441` -> `engine_core.py:385-387`,
    where `doPhysics(dt, 1, dt)` takes one fixed substep, so both keys are genuinely honoured.

    `ceil` on the repeat keeps the physics tick from ever being *coarser* than MetaDrive's own
    0.02 s: a slower rate is served by repeating a fine tick, never by taking a coarse one.
    **10 Hz returns exactly (0.02, 5)**, which is what makes `--step-hz 10` and no flag the
    same run; 100 Hz gives (0.01, 1). The pair is not exposed directly, because the rate is
    their product and `decision_repeat` is load-bearing in ways a caller should not have to
    know - onscreen it also decides how many times `taskMgr.step()` runs per `env.step`, and
    every camera buffer is redrawn on each of those.
    """
    dt = 1.0 / float(step_hz)
    repeat = max(1, int(math.ceil(dt / PHYSICS_TICK_S - 1e-9)))
    return {"physics_world_step_size": dt / repeat, "decision_repeat": repeat}


def decision_stride(step_hz, decision_hz):
    """How many `env.step`s pass between two decisions. 1 when nothing was asked for.

    The middle rate of `world tick / decision + camera / physics`. MetaDrive has no clock for
    it: `env.step` *is* the world tick, the policy is called once per step, and
    `base_engine.py:458` calls `task_manager.step()` once per step so every camera buffer
    redraws on it. There is no per-sensor rate anywhere in the sensor config either, which is
    `name=(cls, *args)` with no slot for one. So a decision rate below the world tick is a
    stride counted in the caller's loop, and this is the one place that counts it.

    Refused rather than rounded when the ratio is not a whole number, for the same reason
    `step_timing.rate_keys` refuses a physics rate that does not divide a step: a rate nobody
    asked for is worse than an error. A decision cannot be *finer* than a world tick either -
    nothing moves between two steps - so the ratio has a floor of 1.
    """
    if decision_hz is None:
        return 1
    ratio = float(step_hz) / float(decision_hz)
    stride = int(round(ratio))
    if stride < 1 or abs(ratio - stride) > 1e-6:
        raise ValueError(
            f"{decision_hz:g} Hz decisions do not divide a {step_hz:g} Hz step: that is "
            f"{ratio:.4g} steps per decision. A decision cannot be finer than a world tick, "
            "and a fraction of one is not a rate."
        )
    return stride


def decides_on(step, stride):
    """Whether a decision is taken on step `step` (0-based). Every step at stride 1.

    A predicate rather than an inline `%` because two loops share it - this module's and
    `step_timing.drive`'s - and a benchmark whose schedule differs from the tool it is meant
    to be timing would be measuring something nobody runs.
    """
    return step % stride == 0


def sim_step_seconds(env) -> float:
    """How long one `env.step` advances the simulator. MetaDrive's own derivation.

    `waypoint_policy.py:61-65` computes it exactly this way. One of the *two clocks* this
    script has to keep apart: this is the engine's, and `data_step_seconds` is the tape's.
    They are equal only when the dataset was converted at the rate the drive is running at.

    The engine does not exist until the first `reset`, so the env's own config stands in
    before then. It holds the same two keys - `BaseEngine.global_config` is built from it -
    and asking the engine once it exists keeps this the authority rather than a copy.
    """
    engine = getattr(env, "engine", None)
    config = env.config if engine is None else engine.global_config
    return float(config["physics_world_step_size"]) * int(config["decision_repeat"])


def data_step_seconds(scenario) -> float:
    """How long one recorded frame of this scenario covers. The other clock.

    `metadata.ts` spacing *is* the rate the dataset was written at - it is an integer step
    index times the interval - so there is no `dt` key to read and none is wanted. A map-only
    scenario has a single timestamp and no spacing, and a signal plan records the step it was
    baked against, so both are tried before falling back to MetaDrive's default.
    """
    metadata = scenario.get("metadata") or {}
    stamps = metadata.get("ts")
    if stamps is not None and len(stamps) > 1:
        step = float(stamps[1]) - float(stamps[0])
        if step > 0.0:
            return step
    plan = metadata.get("signals") or {}
    if plan.get("time_step_s"):
        return float(plan["time_step_s"])
    return 0.1


def _longest_red(scenario) -> float:
    """How many **seconds** the longest red in this scenario's plan lasts.

    The headroom a self-driving policy needs on top of the recording: a car that stops has to
    wait out a whole red in the worst case, and the recording is only as long as a drive that
    never stopped. Zero when the scenario carries no plan, which is most of them.

    Seconds rather than steps, and that is the fix rather than a tidy-up. This used to divide
    by the *plan's* step to produce a number the loop then counted in *env* steps - two
    different clocks, equal only by today's coincidence. The caller converts, because the
    caller is the one that knows which clock it is counting in.
    """
    plan = (scenario.get("metadata") or {}).get("signals")
    if not plan:
        return 0.0
    cycle = float(plan["cycle_seconds"])
    longest = max(
        cycle - float(group["green_seconds"]) - float(group["yellow_seconds"])
        for group in plan["groups"]
    )
    return max(longest, 0.0)


def _step_budget(*, recorded_s, pace_s, red_s, extra_s, traffic, sim_dt):
    """How many `env.step`s this drive may take, and the named terms that made that number.

    Pure, so `tests/unit/test_drive_budget.py` can pin it - the arithmetic used to be four
    lines inline in `main()` and therefore only checkable by driving.

    **Seconds in, steps out, converted once and here.** Every term arrives in seconds because
    that is the only unit they share: `recorded_s` comes off the recorded timestamps, `pace_s`
    off the ego's own speed profile, `red_s` off the signal plan's cycle and `extra_s` off a
    flag - four different clocks written by four different things. The one conversion that
    matters is by **`sim_dt`, never `data_dt`**: at `--step-hz 100` ten seconds is a thousand
    steps, not a hundred, and `_longest_red`'s docstring records what happened the last time
    those two were confused.

    The terms, in the order they are printed:

    - **the drive itself**, which is the longer of the recording and what a car that has to
      *steer* to the route takes (`pace_s` inflated by `IDM_TRACKING_RATIO`, and again by
      `TRAFFIC_DELAY_FACTOR` when there is traffic to queue behind). `pace_s` is None for a
      replayed ego, whose position is set frame by frame - it cannot be delayed by anything,
      so neither the tracking deficit nor the traffic factor may reach it.
    - **a red**, the worst wait the plan can impose, and zero for the scenarios with no plan.
    - **--extra-seconds**, which is the operator saying this particular drive needs longer.

    Returns `(budget, parts)`, `parts` being `[(label, steps), ...]` so the failure message can
    say which term produced the number rather than leaving it to be reverse-engineered.
    """

    def in_steps(seconds):
        return int(round(seconds / sim_dt))

    own = None
    if pace_s is not None:
        own = pace_s / IDM_TRACKING_RATIO
        if traffic:
            own *= TRAFFIC_DELAY_FACTOR

    if own is not None and own > recorded_s:
        drive_s, label = own, "the drive itself"
        if traffic:
            label += f", x{TRAFFIC_DELAY_FACTOR:g} for --traffic live"
    else:
        drive_s, label = recorded_s, "the recording"

    parts = [(label, in_steps(drive_s))]
    if red_s > 0:
        parts.append(("a red", in_steps(red_s)))
    if extra_s > 0:
        parts.append(("--extra-seconds", in_steps(extra_s)))
    return sum(count for _, count in parts), parts


def _budget_reason(budget, parts):
    """The "did not arrive" line for a drive the budget ended, naming the knob that raises it.

    This used to be a three-way branch on the red allowance, and it read the wrong number back:
    `allowance == 0` printed "ran out of recorded steps", which stopped being true the day the
    pace term was added. A `junction-1` drive cut off at 424 was told it had run out of a
    recording that is 379 frames long. Printing the terms cannot go stale the same way.
    """
    if len(parts) == 1:
        breakdown = f"{parts[0][1]} for {parts[0][0]}"
    else:
        breakdown = f"{budget} = " + " + ".join(
            f"{count} for {label}" for label, count in parts
        )
    return f"ran out of steps ({breakdown}); raise it with --extra-seconds"


def _tape_ran_out(scenario, *, steps, recorded_steps, length, lights):
    """What stopped when the drive outlived the recording, or None when nothing did.

    The budget can now legitimately exceed the tape - `--traffic live` doubles the drive term
    and `--extra-seconds` adds to it - and three things end at the last recorded frame whether
    the car has arrived or not. All three were read out of the MetaDrive 0.4.3 checkout rather
    than assumed:

    - `ScenarioTrafficManager.after_step` **removes every replayed pedestrian and cyclist**
      once `episode_step >= current_scenario_length`
      (`manager/scenario_traffic_manager.py:136-140`). A generated crossing simply is not there
      for the part of the drive that runs past the tape.
    - **Cones and barriers stay.** The same block skips static objects deliberately, with the
      comment "static object will not be cleaned!" - so a lane closure holds for the whole
      drive and a walker does not.
    - `ScenarioLightManager.after_step` returns early past the same bound
      (`manager/scenario_light_manager.py:69`), so `--lights tape` **freezes on its last
      colour**. A frozen red is a drive that can never arrive, which looks exactly like a car
      that has broken. `--lights live` evaluates the plan from the episode clock and keeps
      cycling; `signal_control.py`'s own docstring says so.

    None of it raises, none of it is logged, and none of it is visible from the summary line -
    which is the whole reason this exists.

    **A tenth of the drive, not a single step past the tape.** A paced car has always outrun
    the recording a little - the budget's drive term is the ego's own duration inflated by
    `IDM_TRACKING_RATIO`, which is longer than the tape by construction - and on `junction-1`
    free-flow that is 33 steps of 412, 8%, all of it after the car has arrived. Warning there
    would put a line on every idm drive ever run. Under 25 cars it is 266 of 645, 41%, and the
    walkers are missing from the part of the drive that was still going somewhere.
    """
    if steps <= recorded_steps * (1.0 + TAPE_OVERRUN_SHARE):
        return None
    walkers = sum(
        1
        for track_id, track in (scenario.get("tracks") or {}).items()
        if track.get("type") in ("PEDESTRIAN", "CYCLIST")
        and track_id != (scenario.get("metadata") or {}).get("sdc_id")
    )
    frozen = lights == "tape" and bool(scenario.get("dynamic_map_states"))
    if not walkers and not frozen:
        return None
    ends = []
    if walkers:
        ends.append(
            f"{walkers} recorded pedestrian(s) and cyclist(s) were removed there "
            "(cones and barriers stay)"
        )
    if frozen:
        ends.append("--lights tape froze on its last colour there - --lights live keeps cycling")
    return (
        f"note: {steps} steps against a {length}-frame recording. " + "; ".join(ends) + "."
    )


def _refuse_mismatch(scenario, *, policy, lights, sim_dt, data_dt):
    """Why this scenario cannot be driven at a rate other than the one it was written at.

    Returns the message, or `""` when the mismatch is harmless. Three things consume the
    recording one frame per `env.step` with no interpolation, so at a different rate they run
    the tape at the ratio of the two clocks rather than at the speed it records:

    - `ReplayEgoCarPolicy` (`replay_policy.py:41-65`) sets the ego's position from frame
      `episode_step`. At 100 Hz against a 10 Hz tape the car flies the route at 10x. And
      `parse_object_state` hard-codes 0.1 s when it differentiates positions into an angular
      velocity, so even a matched-length tape would spin at the wrong rate.
    - `ScenarioLightManager.after_step` (`scenario_light_manager.py:68-75`) indexes
      `object_state[episode_step]` the same way, so a baked tape changes colour at the wrong
      moment.
    - any non-ego track, for the first reason. Nothing generates those here yet; written now
      so it is right when Stage 8 does.

    Refused rather than warned about, because none of the three fails - each simply drives
    something other than what the dataset says.
    """
    rates = (
        f"the simulator is running at {sim_dt:g} s per step and this dataset was written "
        f"at {data_dt:g} s ({1.0 / sim_dt:g} Hz against {1.0 / data_dt:g} Hz)"
    )
    fix = (
        f"Either re-run with --step-hz {1.0 / data_dt:g} to match the dataset, or "
        f"re-convert it with --step-hz {1.0 / sim_dt:g} to match this run."
    )
    if policy == "replay":
        return (
            "REFUSED: --agent-policy replay consumes one recorded frame per env.step with "
            f"no interpolation, and {rates}. The car would drive the route at "
            f"{data_dt / sim_dt:.2g}x speed.\n             {fix}"
        )
    if lights == "tape" and scenario.get("dynamic_map_states"):
        return (
            "REFUSED: a baked light tape is indexed by env.step the same way a replayed "
            f"track is, and {rates}. Every light would change colour at the wrong "
            f"moment.\n             {fix} Or drive the lights live with --lights live."
        )
    sdc = (scenario.get("metadata") or {}).get("sdc_id")
    others = [key for key in (scenario.get("tracks") or {}) if key != sdc]
    if others:
        return (
            f"REFUSED: {len(others)} non-ego track(s) are replayed one recorded frame per "
            f"env.step, and {rates}.\n             {fix}"
        )
    return ""


class _EgoPace:
    """Hands the IDM ego the speed its own route allows, and reads back what it asked for.

    `--agent-policy idm` was getting MetaDrive's stock `TrajectoryIDMPolicy`, whose
    `target_speed` is set once in `__init__` to `NORMAL_SPEED` - a flat 40 km/h - and never
    written again, because `TrajectoryIDMPolicy.act` does not call `lane_change_policy`.
    Nothing anywhere in `metadrive/policy/` slows a car for a corner: `acceleration` reads
    `target_speed`, the car's speed and the gap to the car in front, and that is the whole
    longitudinal law. So the ego arrived at every junction turn doing 40 and left the road -
    measured before this existed as `out_of_road` at 4.26 m lateral against a 4 m limit.

    This is the ego's half of what `tools/traffic.py:before_step` already does for every
    traffic car. The geometry is the policy's own windowed route arrays, so the speed and the
    steering are read off exactly the same line.
    """

    def __init__(self, cruise_mps: float) -> None:
        self.cruise_mps = cruise_mps
        self.slowest_kph = float("inf")
        self.duration_s = 0.0
        self._policy = None
        self._travelled = None
        self._speed = None

    def start_episode(self, env, policy=None) -> None:
        """`policy` for a caller driving from outside the engine - see `agent_env.IdmDriver`,
        which holds its own instance of the same class so the action really is what moves the
        car. Left to the engine's own ego policy otherwise."""
        self._policy = policy if policy is not None else env.engine.get_policy(env.agent.name)
        self.slowest_kph = float("inf")
        route, _arc = self._policy._route_arrays()
        self._travelled, self._speed = profile_speeds(route, cruise_mps=self.cruise_mps)
        # How long this car's own drive takes, which is not how long the recording takes.
        # The tape is built at `ego_route.LATERAL_ACCEL_MPS2` 8.5, which works for a car whose
        # positions are set directly; anything that has to *steer* to them gets 4.0, and a
        # gentler corner is a longer drive. Sizing the step budget on the recording instead
        # cuts the run off partway - measured on `junction-1`, at 85% of the route.
        steps = self._travelled[1:] - self._travelled[:-1]
        pace = (self._speed[1:] + self._speed[:-1]) / 2.0
        self.duration_s = float((steps / pace).sum())

    def before_step(self, env) -> None:
        """Set the target *before* the step, because `act` reads it during the step.

        `route_coordinates` is called here and again inside `steering_control` a moment
        later, with the car in the same place both times - the window is centred on the
        progress this call leaves behind, still contains the same nearest vertex, and returns
        the same `along`. Reading `_progress` from the previous step instead would be a step
        stale into a corner, which is the fault the traffic profile is pinned against.
        """
        along, _lateral = self._policy.route_coordinates(env.agent.position)
        target_kph = 3.6 * speed_at(self._travelled, self._speed, along)
        self._policy.target_speed = target_kph
        self.slowest_kph = min(self.slowest_kph, target_kph)


def _recorded_cruise_mps(scenario) -> float:
    """The fastest the recorded car goes, which is the posted limit the drive was built for.

    Read off the tape rather than configured: `convert --speed-kph` may already have
    overridden the road's own limit, and a second knob here could disagree with it.
    """
    sdc = (scenario.get("metadata") or {}).get("sdc_id")
    track = ((scenario.get("tracks") or {}).get(sdc) or {}).get("state") or {}
    velocity = track.get("velocity")
    if velocity is None or not len(velocity):
        return 40.0 / 3.6
    fastest = float(max(math.hypot(float(v[0]), float(v[1])) for v in velocity))
    return fastest if fastest > 1.0 else 40.0 / 3.6


def _baked_stops(scenario) -> list[dict]:
    """The reds the recorded car was written to stop for, if any."""
    return list(((scenario.get("metadata") or {}).get("sdc_route") or {}).get("stops") or [])


def _metadrive_native_texture(region_m: int) -> int:
    """The texture MetaDrive would build for this region if nothing were patched.

    `TerrainProperty.get_semantic_map_pixel_per_meter` is `22 if map_region_size != 4096 else
    11`, so the 4096 m case is not `region * 22`. Only printed, to say what the patch below is
    standing in for.
    """
    return region_m * (11 if region_m == MAX_REGION_M else 22)


def _max_texture_dimension() -> int | None:
    """The GL ceiling the card really reports, or None if it could not be asked.

    Asked in a throwaway subprocess, and that is the whole difficulty: the ceiling is only
    knowable once a GL context exists, and by the time `env.engine.win` does, MetaDrive has
    already read `get_semantic_map_pixel_per_meter` and built the terrain from it. A separate
    process has a context of its own and depends on nothing about MetaDrive's reset ordering.

    It inherits this process's environment, so it sees whichever card the GLX loader was
    pointed at - which is how `scripts/drive.sh` setting `__NV_PRIME_RENDER_OFFLOAD` reaches
    this number. Measured on this machine: 16384 on the Intel iGPU, 32768 on the RTX 4050.
    """
    import subprocess

    probe = (
        "from panda3d.core import loadPrcFileData;"
        "loadPrcFileData('', 'window-type offscreen\\naudio-library-name null');"
        "from direct.showbase.ShowBase import ShowBase;"
        "print(ShowBase(windowType='offscreen').win.getGsg().getMaxTextureDimension())"
    )
    try:
        finished = subprocess.run(
            [sys.executable, "-c", probe],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    for line in reversed(finished.stdout.splitlines()):
        if line.strip().isdigit():
            return int(line.strip())
    return None


def _set_semantic_detail(pixels_per_meter: int) -> None:
    """Pin the semantic texture's resolution.

    One of this script's four monkeypatches - `_set_line_width` is the next - and for the same
    reason: `Terrain` reads this through `TerrainProperty.get_semantic_map_pixel_per_meter()`,
    whose body is `22 if map_region_size != 4096 else 11`, and there is no config key anywhere
    that reaches it, so a map big enough to need a 2048 m square cannot be textured without
    replacing the method. Writing to `TerrainProperty` is MetaDrive's own mechanism for the
    neighbouring value: `base_env.py:335` sets `TerrainProperty.map_region_size` the same way.

    Nothing in the MetaDrive checkout is edited; delete this call and the default returns.
    """
    from metadrive.constants import TerrainProperty

    TerrainProperty.get_semantic_map_pixel_per_meter = classmethod(lambda cls: pixels_per_meter)


def _set_line_width(thickness_px: int, sample_interval_m: float) -> None:
    """Pin how wide a painted lane line is, and how finely it is sampled before it is painted.

    The second monkeypatch, and the thickness has to *override* rather than re-default:
    `terrain.py:625` passes `white_line_thickness=2, yellow_line_thickness=3` explicitly, over
    `BaseMap.get_semantic_map`'s own defaults of 1 and 1. So the wrapper drops whatever the
    caller asked for. There is no config key between the two.

    White and yellow are given the same number. Our data has neither type of yellow line -
    `conversion.py` writes only `ROAD_EDGE_BOUNDARY` and `ROAD_LINE_BROKEN_SINGLE_WHITE` - so
    the yellow value is unreachable today, and a different one would be an unexplained
    difference on the first map that does have one.

    `line_sample_interval` is *added* rather than overridden - `terrain.py:620` never passes it,
    so its default of 2 m stands and there is nothing to drop. See `_keep_line_ends` for what
    that interval costs and `LINE_INTERVAL_M` for the dashes it moves.

    Nothing in the MetaDrive checkout is edited; delete this call and the defaults return.
    """
    from metadrive.component.map.base_map import BaseMap

    original = BaseMap.get_semantic_map

    def with_line_width(self, *arguments, **keywords):
        keywords["white_line_thickness"] = thickness_px
        keywords["yellow_line_thickness"] = thickness_px
        keywords["line_sample_interval"] = sample_interval_m
        return original(self, *arguments, **keywords)

    BaseMap.get_semantic_map = with_line_width


def _keep_line_ends() -> None:
    """Stop MetaDrive throwing away the last piece of every painted line.

    `metadrive/utils/math.py:269 resample_polyline` steps with `np.arange(0, length, interval)`,
    which **never includes the endpoint**, and `scenario_map.py:74/90` runs it over every line
    longer than `interval * 2` before the raster is painted. So every line over 4 m is drawn
    short by up to a whole interval: **554.7 m of paint discarded across 585 of `mosque`'s 690
    painted lines** and 448.2 m across 453 of `junction-1`'s 548, a mean 0.95 m off the end of
    each. It takes lane edges, dividers and junction kerbs alike, so a kerb butted 0.10 m into
    the line beside it has both ends chopped off and the join it was built to close reopens.

    What a reader sees is road edge with no thick line on it and only the shader's own hairline -
    Keith: *"some of the edges are still not containing proper thick lane edges."* Measured
    against the road outline at one texel: `mosque` 324.0 m of it, of which 185.0 m is purely
    this and 52.7 m more is the chord sag a finer interval removes; `junction-1` 420.8 m, 203.5 m
    and 101.4 m. What is left at both is **86.3 m and 115.9 m, which is the 39 and 38 road ends**
    `_junction_kerb_boundaries` leaves bare on purpose and nothing else - the same figure as if
    the resampling were removed altogether.

    The third monkeypatch, and the last of the three about paint. Both importers took the name
    rather than the module, so the two bindings are rebound separately and nothing else in
    MetaDrive imports it: `scenario_map` is the raster, `scenario_block` the collision ghosts,
    which must agree with what is drawn.
    """
    import numpy as np
    from metadrive.component.map import scenario_map
    from metadrive.component.scenario_block import scenario_block

    original = scenario_map.resample_polyline

    def to_the_end(points, target_distance):
        resampled = original(points, target_distance)
        last = np.asarray(points)[-1]
        if len(resampled) and np.allclose(resampled[-1], last):
            return resampled
        return np.vstack([resampled, last[: resampled.shape[1]]])

    scenario_map.resample_polyline = to_the_end
    scenario_block.resample_polyline = to_the_end


def _record_every_step() -> None:
    """Stop MetaDrive recording nothing at all when `decision_repeat` is 1.

    `RecordManager` fills one `FrameInfo` per physics tick and appends the batch once per
    `env.step`. Which tick did the filling is tracked in `current_frame_count`, and
    `after_step` guards the append on it being **truthy** (`record_manager.py:110`). That
    counter is only ever advanced by `RecordManager.step()`, and the engine calls that from
    inside its physics loop under `if ... and i < step_num - 1` (`base_engine.py:443`) --
    which is never true when `step_num` is 1. So at `decision_repeat == 1` the counter stays 0,
    the guard stays false, and the episode ends holding nothing but the reset frame.

    That is not a corner: `step_config` returns `(0.01, 1)` at **100 Hz**, the rate the openpilot
    bridge is driven at and the rate every interesting drive on the rig runs at. Measured on a
    `--step-hz 100` junction-1 drive before this existed: 3516 steps in, **1 frame** out.

    The guard is the whole bug -- `current_frames` is the thing being appended, so asking
    whether it exists is both the correct question and the one the code below already answers
    for every other rate. `after_step` then calls `step()` itself, which fills tick 0 with the
    post-physics state, and MetaDrive's own assertion that the batch is `decision_repeat` long
    holds at 1 exactly as it does at 5.

    The fourth monkeypatch, and installed only under `--export-drive`: a run that is not
    exporting has `record_episode` off and never reaches any of this.
    """
    from metadrive.manager.record_manager import RecordManager

    def after_step(self, *args, **kwargs):
        if self.engine.record_episode and self.current_frames is not None:
            self.step()
            assert len(self.current_frames) == self.engine.global_config["decision_repeat"], (
                "Number of Frame Mismatch!"
            )
            self.episode_info["frame"].append(self.current_frames)
        return {}

    RecordManager.after_step = after_step


def _duration(seconds):
    """A wall-clock span, in the largest two units that say anything.

    A drive here spans four orders of magnitude -- a replayed `junction-1` is 40 s, the same
    route under the AV3 model is hours -- so a fixed unit is unreadable at one end or the other.
    Seconds below a minute, `14m20s` below an hour, `5h30m` above it.
    """
    seconds = int(round(seconds))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m{seconds % 60:02d}s"
    return f"{seconds // 3600}h{(seconds % 3600) // 60:02d}m"


def _progress_line(
    steps,
    budget,
    sim_seconds,
    completion,
    speed_ms,
    ms_per_step,
    policy_ms,
    elapsed_seconds,
):
    """One heartbeat, as printed. Pure, so the two shapes below can be pinned by a test.

    The loop it reports on prints nothing at all between `ego starts at ...` and the scenario
    summary -- 40 seconds of silence on a replay, and hours on a model drive, which is the case
    that matters: `--agent-policy remote` has **no step budget** (see where `budget` is set to
    None below), so the drive ends only when the episode does or at MetaDrive's `horizon` of
    100000. A route that completes is about 758 decisions at `--decision-hz 20`; one that runs
    to the horizon is 20000, five and a half hours. From a terminal those are the same thing,
    and so is a hung socket.

    Two shapes, because the two cases have different truths to tell:

        progress     step 1800 of 3695 (49%), 18.0 s driven, completion 0.462, 47 km/h,
                     1.1 ms/step, 2s elapsed, ~2s left
        progress     step 4200, 42.0 s driven, completion 0.081, 12 km/h, 205 ms/step
                     (12 ms of it the policy round trip), 14m20s elapsed

    **No ETA when `budget` is None.** The route fraction is the only progress that exists for a
    car driving itself, and extrapolating it would put a confident number on a car that may be
    circling. Completion standing still while the step count climbs is itself the reading, and
    an ETA would hide it behind arithmetic.

    `ms_per_step` is the caller's measurement of the interval **just ended**, not of the whole
    run: a drive that stalls shows the figure climbing rather than having the stall averaged
    into the minutes that went well. `policy_ms` is the round trip's share of it over the same
    interval, or None when nothing is hosted -- the same split the end-of-run `policy` line
    makes, and for the same reason: a slow model and a slow socket look identical per step.
    """
    head = f"step {steps}"
    if budget:
        head += f" of {budget} ({100.0 * steps / budget:.0f}%)"
    parts = [head, f"{sim_seconds:.1f} s driven"]
    # `completion == completion` is the nan test, and nan is what `info` yields before the
    # environment has reported a route fraction at all.
    if completion == completion:
        parts.append(f"completion {completion:.3f}")
    parts.append(f"{speed_ms * 3.6:.0f} km/h")

    # A tenth of a millisecond matters at replay speed (1.1 ms a step) and is noise at model
    # speed (205), so the precision follows the magnitude rather than being fixed at one.
    rate = f"{ms_per_step:.1f} ms/step" if ms_per_step < 10 else f"{ms_per_step:.0f} ms/step"
    if policy_ms is not None:
        # Same magnitude-following precision, and for a sharper reason here: a local stub
        # answers in 0.4 ms, which a bare `{:.0f}` renders as "0 ms" and reads as broken.
        share = f"{policy_ms:.1f}" if policy_ms < 10 else f"{policy_ms:.0f}"
        rate += f" ({share} ms of it the policy round trip)"
    parts.append(rate)

    parts.append(f"{_duration(elapsed_seconds)} elapsed")
    if budget:
        parts.append(f"~{_duration((budget - steps) * ms_per_step / 1000.0)} left")
    return "progress     " + ", ".join(parts)


def _container_path_refusal(path):
    """The message for an `--export-drive` naming a container path, typed outside the container.

    `/work` is the container's name for the repo and nothing else's -- `compose.yaml` bind-mounts
    `.:/work` and works from there. Out here it is an ordinary absolute path at the filesystem
    root, so the flag would try to create it and fail with a bare `Permission denied` naming
    nothing that helps.

    The answer is not a different absolute path, it is a *relative* one: `scripts/_common.sh`
    cds to the repo root before any script does anything, and the container's working directory
    is the repo, so `workspaces/<ws>/drives/<label>` is one string on both machines and in both
    environments. `rigs/README.md` calls that "one path, everywhere" and it is why
    `rigs/cams.txt` needs no translation either.

    Returns None when there is nothing to say, so the caller reads as the check it is.
    """
    if not path.startswith("/work/"):
        return None
    import env_hint

    if env_hint.in_container():
        return None
    return (
        f"result       FAILED: --export-drive {path} names a path inside the `sim` "
        "container, and this is not the container.\n"
        "             Drop the /work/ -- a repo-relative path is one string everywhere, "
        "in here and out:\n"
        f"               --export-drive {path[len('/work/') :]}"
    )


# What a drive export puts in its directory, and nothing else. `sd_*.pkl` is MetaDrive's own
# naming -- every scenario file goes through `SD.get_export_file_name`, which builds
# `sd_<dataset>_<version>_<id>.pkl` (`scenario/scenario_description.py:388`) -- and the two
# constants are `ScenarioDescription.DATASET.SUMMARY_FILE` and `.MAPPING_FILE`. Written out
# here rather than imported so the precheck can run before MetaDrive is imported at all,
# importing it pulling in panda3d for a question about a directory. A copy needs the original
# pinned to it: `test_the_owned_names_are_metadrives_own` reads all three out of the checkout's
# source text, which works whether or not MetaDrive is installed in this venv.
_EXPORT_SUMMARY = "dataset_summary.pkl"
_EXPORT_MAPPING = "dataset_mapping.pkl"


def _export_files(directory):
    """Split a directory into the files a drive export owns and the files it does not.

    **Only what this tool wrote may be deleted.** `--export-drive` takes a directory rather
    than a file, and a directory is the one argument a person mistypes into something that
    already matters -- so a replace that trusted the path would be one typo away from removing
    a workspace. Ownership is decided by name, against the three shapes
    `extract_dataset_summary_and_mapping` produces, and everything else is reported back for
    the caller to refuse on.

    Returns `(owned, foreign)`, both sorted lists of bare names. A directory that does not
    exist is `([], [])` -- there is nothing to replace and nothing in the way.
    """
    try:
        names = sorted(os.listdir(directory))
    except FileNotFoundError:
        return [], []
    owned, foreign = [], []
    for name in names:
        if name in (_EXPORT_SUMMARY, _EXPORT_MAPPING) or (
            name.startswith("sd_") and name.endswith(".pkl")
        ):
            owned.append(name)
        else:
            foreign.append(name)
    return owned, foreign


def _clear_export(directory) -> int:
    """Remove the previous export from `directory`, leaving anything else untouched.

    The write that follows is a **replace**, not a merge, and this is the half that makes it
    one. `dataset_summary.pkl` names only what the run that wrote it exported, so a second,
    shorter drive into the same directory would otherwise leave the first drive's `sd_*.pkl`
    beside a summary that does not list them -- a dataset that reads as smaller than it is,
    which is exactly the fault the old "refuses a non-empty directory" rule existed to prevent.
    Replacing is the version of that rule that survives stopping a drive early, because
    re-running into the same `drives/<label>` is then the ordinary gesture rather than a mistake.

    Returns how many files were removed, so the caller can say whether anything was replaced.
    """
    owned, _ = _export_files(directory)
    for name in owned:
        os.remove(os.path.join(directory, name))
    return len(owned)


class _EarlyClose:
    """Ctrl-C ends the drive at the next frame boundary instead of throwing the drive away.

    **The recording only reaches disk after the loop ends.** Every frame lives in MetaDrive's
    `RecordManager` until `engine.dump_episode()` is called, and that call sits after the drive
    loop -- so a `KeyboardInterrupt` runs the `finally` that closes the engine and takes the
    whole recording with it. On a drive under the AV3 model, where a decision is about a second
    and the budget is fifty minutes, that is the difference between having the run and not: a
    car that *stalls* never terminates, so the loop keeps stepping to a budget nobody wants to
    wait out, and Ctrl-C was the only way out.

    **At a frame boundary, not wherever the signal lands.**
    `convert_recorded_scenario_exported` asserts `frames[-1].episode_step == episode_len - 1`
    (`scenario/utils.py:143`), so an episode dumped from inside `env.step` -- half a frame
    appended -- fails an assert rather than exporting short. The handler therefore only sets a
    flag; the loop reads it at the top, before its next step, where the last frame is complete.

    **The second Ctrl-C still kills the run, but it exits rather than raises.** A graceful stop
    that could not itself be interrupted would be a worse bargain than the one it replaces, so
    the second signal always gets out. What it must not do is *raise*. The handler underneath
    is Python's own, and a `KeyboardInterrupt` surfaces wherever the process happens to be --
    which under `--render 3D` is inside panda3d's C++ render call. Unwinding a half-drawn frame
    segfaulted on this laptop on 2026-08-28, and the driver was then left freeing the VA space
    of a process that had died mid-ioctl: a cascade of `NVRM: GPU0 kgmmuInvalidateTlb_GM107:
    TLB invalidation failed` ending in GPU_IN_FULLCHIP_RESET, with the card gone from
    `nvidia-smi` and unbound in `lspci` until a reboot. `os._exit` instead -- no unwinding, no
    destructors, and the kernel closes the GL context's fd exactly as it does for any process
    that exits normally.

    Scoped to the drive loop, because a signal handler is process-global: outside the loop
    there is no partial recording to save, and Ctrl-C during argument parsing or `env.close()`
    should keep meaning what it has always meant.
    """

    #: The exit the second Ctrl-C takes. An attribute only so a test can watch it being called
    #: without ending the test runner; nothing else should replace it.
    _exit = staticmethod(os._exit)

    def __init__(self, on_request=None, on_kill=None):
        self.asked = False
        self._on_request = on_request
        self._on_kill = on_kill
        self._previous = None

    def _handle(self, signum, frame):  # noqa: ARG002 - the signal module's signature
        self.asked = True
        # Arm the second stage before anything else runs, so a Ctrl-C during the export -- or
        # during a frame slow enough that the loop has not yet come back around to read the
        # flag -- still gets out. Deliberately *not* `restore()`: putting the previous handler
        # back is what made the second Ctrl-C a KeyboardInterrupt, and the class docstring
        # says what that did to the GPU. The original is still put back by `restore`, on the
        # way out, so Ctrl-C outside the loop keeps meaning what it always meant.
        self.arm_exit()
        if self._on_request is not None:
            self._on_request()

    def arm_exit(self):
        """From now on, any Ctrl-C exits at once rather than setting a flag.

        Armed by the first Ctrl-C, and again by the drive once the loop is over: past that
        point there is no next frame boundary to stop at, so a handler that only sets a flag
        would swallow the press entirely. Idempotent.
        """
        with contextlib.suppress(ValueError):
            signal.signal(signal.SIGINT, self._kill)

    def _kill(self, signum, frame):  # noqa: ARG002 - the signal module's signature
        """The second Ctrl-C: leave now, without unwinding anything."""
        if self._on_kill is not None:
            # A message is not worth staying in a handler for. Whatever it raises, the exit
            # below still has to happen -- that is the whole contract of the second Ctrl-C.
            with contextlib.suppress(Exception):
                self._on_kill()
        # `os._exit` runs no `finally`, no `atexit` and no buffer flush of its own, and a
        # message the user never sees is exactly why they press it a third time.
        for stream in (sys.stdout, sys.stderr):
            with contextlib.suppress(Exception):
                stream.flush()
        # 128 + SIGINT, which is what a shell reports for a process ended by Ctrl-C.
        self._exit(130)

    def install(self):
        """Take over SIGINT. Paired with `restore`, which must run however the drive ends."""
        with contextlib.suppress(ValueError):
            # ValueError only when this is not the main thread, where a handler cannot be
            # installed at all. A drive that cannot be closed early is worse than one that
            # can; a drive that refuses to start is worse than both.
            self._previous = signal.signal(signal.SIGINT, self._handle)
        return self

    def restore(self):
        """Put back whatever handler was there before, once. Safe to call twice."""
        previous, self._previous = self._previous, None
        if previous is not None:
            with contextlib.suppress(ValueError):
                signal.signal(signal.SIGINT, previous)

    # `install`/`restore` rather than only `with`, because the drive loop already sits inside a
    # try/finally that closes the engine, and the handler has to be put back on exactly that
    # path. The context manager is the same pair, and is what the tests use.
    def __enter__(self):
        return self.install()

    def __exit__(self, *_):
        self.restore()
        return False


def _settle_on_the_road(env) -> tuple:
    """Let a replayed car find its ride height, which at `decision_repeat` 1 it never can.

    **The first physics call after a teleport moves the body by exactly nothing**, and a
    replayed car is teleported every `env.step`. Traced on `junction-1`: at `decision_repeat`
    2 the first `doPhysics` of the pair reports 0.00000 -> 0.00000 and the second 0.00000 ->
    -0.00020, and at `decision_repeat` **1** every call is a first call, so z never leaves
    the value it was spawned with. Bullet's, not MetaDrive's -- `set_position` writes the
    transform from outside and one `stepSimulation(dt, 1, dt)` re-syncs rather than integrates.

    The value it is stuck at is **0**, because that is what the dataset carries: every
    scenario here has `position[:, 2]` identically zero, and `BaseVehicle.reset` spawns at
    `position[-1]` when the position is 3-D (`base_vehicle.py:361`) rather than at its own
    `HEIGHT / 2` fallback. Physics is what normally lifts the car off that, to a measured
    **0.537 m** at 10 Hz -- so at 100 Hz the car is drawn half a ride height under the road.
    Which is what a person saw, in a window, and no number in the drive summary said.

    Nothing is patched to fix it. The physics world is simply stepped here, at reset, with no
    teleport in between -- so calls 2 onwards *do* integrate and the suspension settles. It
    reaches 0.53908 in 105 ticks, against the 0.5365-0.5384 physics arrives at unaided at
    every other rate. That height then survives the whole drive for free: the replay policy
    hands `set_position` a **2-D** position (`parse_object_state` drops z at its default
    `include_z_position=False`), and a 2-D `set_position` keeps the body's current z
    (`base_object.py:300-301`).

    Deliberately not `HEIGHT / 2`, MetaDrive's own spawn fallback: that is 0.595 here and
    would float the car 5.6 cm. Physics knows the answer; this only gives it room to say it.

    Returns `(from, to, ticks)`. The caller decides when to call it -- every rate but this one,
    and every policy that actually drives, must not.
    """
    before = float(env.agent.origin.getZ())
    previous = None
    # Bounded because this is a settling loop and a car that will not settle must not hang a
    # drive. 105 ticks was measured; 2000 is two seconds of simulated suspension.
    for ticks in range(1, 2001):
        env.engine.step_physics_world()
        current = float(env.agent.origin.getZ())
        if previous is not None and ticks > 10 and abs(current - previous) < 1e-5:
            break
        previous = current
    return before, float(env.agent.origin.getZ()), ticks


def _images_are_normalised(env) -> bool:
    """True when the observation's image half is float32 in [0, 1] and may be stored as 8-bit.

    `norm_pixel` decides it for a camera (`image_obs.py:75-77`), and the round trip through
    uint8 is exact because the camera rendered 8-bit in the first place. But a
    `PointCloudLidar` image source is float32 and **unbounded** whatever `norm_pixel` says
    (`image_obs.py:73-74`) -- metres, not pixels -- so it is asked about by name rather than by
    `issubclass(cls, BaseCamera)`, which it would pass (it subclasses `DepthCamera`).
    """
    from metadrive.component.sensors.point_cloud_lidar import PointCloudLidar

    if not env.config.get("norm_pixel", True):
        return False
    source = env.config.get("vehicle_config", {}).get("image_source")
    entry = env.config.get("sensors", {}).get(source)
    return not (entry and entry[0] is PointCloudLidar)


def _first_camera(engine):
    """The first registered `BaseCamera`, as `(name, sensor)`, or `None`.

    `MainCamera` is deliberately not eligible: it is a `BaseSensor` rather than a
    `BaseCamera` and its `perceive` takes a different signature, so reading it here would
    be a second code path for one line of output. Imported inside the function because
    everything in `tools/` has to keep importing in an environment with no MetaDrive.
    """
    from metadrive.component.sensors.base_camera import BaseCamera

    sensors = getattr(engine, "sensors", None) or {}
    for name in sorted(sensors):
        if isinstance(sensors[name], BaseCamera):
            return name, sensors[name]
    return None


def _cuda_frame_report(observation, engine) -> str:
    """One line saying whether a rendered frame really is on the card, read off the frame.

    Never off the flag: a device array carries `__cuda_array_interface__` and no
    `__array_interface__`, so a switch that did nothing shows up here as `numpy.ndarray`
    rather than as the thing that was asked for.

    **Two places to look, because the render mode decides which one holds the frame.**
    Offscreen the observation *is* the 3-frame camera stack and `image_obs.py:55-65` makes
    it a CuPy array. Under `--render 3D` the observation is 161 floats and the frame exists
    only inside a registered camera -- so a version of this that reads the observation alone
    prints "nothing to check" in exactly the mode a reader most wants the proof, which is
    what it did until 2026-08-24.
    """
    from gpu_frames import is_device_array

    frame = observation.get("image") if isinstance(observation, dict) else None
    source = "observation"
    if frame is None:
        found = _first_camera(engine)
        if found is None:
            return "frames       no image observation and no camera registered to check"
        source = f"camera {found[0]}"
        try:
            # No parent node: this copies the buffer the frame pass has already filled
            # rather than forcing a second scene render (`base_camera.py:188`).
            frame = found[1].perceive(to_float=False)
        except Exception as error:  # noqa: BLE001 - reported, never fatal to a drive
            return f"frames       {source} could not be read: {type(error).__name__}: {error}"
    if is_device_array(frame):
        pointer = frame.__cuda_array_interface__["data"][0]
        return "frames       on the GPU, from the {}: {} {} {}, device pointer {}".format(
            source,
            type(frame).__module__ + "." + type(frame).__name__,
            tuple(frame.shape),
            frame.dtype,
            hex(pointer),
        )
    return (
        f"frames       --image-on-cuda was asked for and the {source} is "
        f"{type(frame).__name__} in host memory"
    )


def _ground_around(engine, path, radius_m=25):
    """How high the visible ground stands beside the car, over the whole drive.

    This is the measurement the symptom needs. The car rides a flat collision plane, so its
    own height is ride height whatever the terrain does - probing directly under it proves
    nothing, and the road is flattened under it in any case. What is actually wrong in a bad
    render is the *landscape*: at MetaDrive's default `height_scale` the ground beside an OSM
    road stands tens of metres above it, so the car is inside a hillside rather than on a road.

    Read back from the texture the terrain actually uploaded, so it reports what is drawn
    rather than re-deriving MetaDrive's arithmetic and hoping the two agree.
    """
    import numpy

    terrain = engine.terrain
    if not getattr(terrain, "render", False) or terrain.heightfield_tex is None:
        return None
    image = terrain.heightfield_tex.getRamImage()
    if not image:
        return None
    field = numpy.frombuffer(image.getData(), dtype=numpy.uint16)
    size = terrain.heightfield_tex.getXSize()
    if field.size != size * size:
        return None
    field = field.reshape((size, size))

    # The same placement `base_map.get_height_map` rasterises with: one pixel per metre,
    # column from x and row from y, both about the centre of the square.
    metres = field.astype(numpy.float64) / 65536.0 * terrain._height_scale * 2
    ground = terrain.origin.getZ() + metres

    highest = -1e9
    above = 0
    total = 0
    for x, y in path:
        column = int(x + size / 2)
        row = int(y + size / 2)
        window = ground[
            max(0, row - radius_m) : row + radius_m, max(0, column - radius_m) : column + radius_m
        ]
        if window.size == 0:
            continue
        highest = max(highest, float(window.max()))
        above += int((window > 0.5).sum())
        total += int(window.size)
    if not total:
        return None
    return highest, above / total


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("dataset", help="Directory holding dataset_summary.pkl")
    parser.add_argument(
        "--render",
        default="none",
        choices=["none", "offscreen", "2D", "3D", "semantic"],
        help="`none` skips graphics entirely, which also means no terrain is built, so it "
        "checks the drive and not the view. `offscreen` builds the full 3D terrain into a "
        "buffer instead of a window - the only way to check the view without a display.",
    )
    parser.add_argument(
        "--scenario-index", type=int, default=None, help="Drive one scenario instead of all"
    )
    parser.add_argument(
        "--map-region-size",
        type=int,
        default=None,
        help="Terrain square in metres, a power of two in [512, 4096]. Measured from the "
        "dataset when not given.",
    )
    parser.add_argument(
        "--semantic-pixels-per-meter",
        type=int,
        default=None,
        help="Resolution of the road-surface texture. Chosen to fit the GL ceiling the card "
        f"reports when not given, or {FALLBACK_MAX_TEXTURE} px if it cannot be asked.",
    )
    parser.add_argument(
        "--line-width-m",
        type=float,
        default=LINE_WIDTH_M,
        help="How wide a painted lane line should be, in metres. MetaDrive's own thickness is "
        "in pixels, so its real width changes with the size of the map; this asks in metres "
        f"and works out the pixels. Default {LINE_WIDTH_M} m, about a real road marking. "
        "1 px is the floor, so a very large map cannot go as thin as a small one. 0 uses "
        "MetaDrive's own 2 px / 3 px.",
    )
    parser.add_argument(
        "--line-interval-m",
        type=float,
        default=LINE_INTERVAL_M,
        help="How finely a painted line is sampled before it is drawn, in metres. MetaDrive's "
        f"own value is 2.0, which sags inside every curve. Default {LINE_INTERVAL_M}. Anything "
        "under 1.5 also makes broken lines 3 m / 3 m instead of 2 m / 2 m; 2.0 restores both.",
    )
    parser.add_argument(
        "--step-hz",
        type=float,
        default=None,
        help="How many times a second the simulator advances. MetaDrive's own rate is 10, "
        "which is what an unflagged run uses. Set from the two keys env.step multiplies "
        "together, so 100 gives a 0.01 s physics tick with one substep. A dataset carries "
        "the rate it was converted at, and a replayed track is consumed one recorded frame "
        "per step with no interpolation - so a mismatch is refused rather than driven at the "
        "wrong speed.",
    )
    parser.add_argument(
        "--decision-hz",
        type=float,
        default=None,
        help="How many times a second the policy is consulted and the --sensors are read, "
        "when that should be slower than the simulator itself. Unset, it is the step rate: "
        "MetaDrive has no separate clock for it, so `env.step` is the world tick, the "
        "decision and the camera sample all at once. Must divide --step-hz. On "
        "--agent-policy replay it gates the reads and the camera draw alone, MetaDrive "
        "calling the replay policy in-engine every step whatever this says; on `idm`, "
        "`manual` and `remote` the action is held across the skipped steps. Offscreen the "
        "cameras are drawn at this rate too, not at the world tick. openpilot's bridge is "
        "written for 20 Hz (_DT_MDL 0.05), so --step-hz 100 --decision-hz 20 matches it.",
    )
    parser.add_argument(
        "--draw-every-step",
        action="store_true",
        help="Redraw the offscreen cameras on every world tick even at a lower --decision-hz. "
        "Off by default; kept so the gate's worth stays measurable. No effect under --render "
        "3D, where the draw is never gated - the window is the point of it, ForceFPS steps "
        "the task manager inside the substep loop, and --agent-policy manual polls the "
        "keyboard there.",
    )
    parser.add_argument("--height-scale", type=int, default=HEIGHT_SCALE)
    parser.add_argument("--drivable-area-extension", type=int, default=DRIVABLE_AREA_EXTENSION_M)
    parser.add_argument(
        "--reactive",
        action="store_true",
        help="Give traffic behind the ego an IDM policy instead of replaying it",
    )
    parser.add_argument(
        "--agent-policy",
        default="replay",
        choices=["replay", "idm", "manual", "remote"],
        help="`replay` teleports the ego onto its recorded positions, so it passes through "
        "red lights - it has no dynamics to interrupt. `idm` follows the same recorded route "
        "as a reference line while braking for obstacles and for the wall a red light puts "
        "across the lane, so route completion stops being exact by construction. `manual` "
        "hands the wheel to the keyboard - WASD, and the arrow keys are not bound "
        "(`manual_controller.py:50-55`) - and requires --render 3D; the recorded route becomes "
        "the goal rather than the drive, and the run is not bounded by the recording's length. "
        "`remote` hands it to a model hosted in another process, over --policy-url; it is the "
        "same code path as `manual`, differing only in where the two numbers come from.",
    )
    parser.add_argument(
        "--policy-url",
        default=None,
        help="Where the hosted model is listening, for --agent-policy remote. e.g. "
        "http://127.0.0.1:8642 - see examples/policy_server.py.",
    )
    parser.add_argument(
        "--sensors",
        default="",
        help="Comma-separated extras sent to a --policy-url alongside the observation: "
        + ", ".join(POLICY_SENSORS)
        + ". `imu` and `gps` cost about a kilobyte a step and need nothing registered; a "
        "camera or the point cloud costs hundreds of KB and needs --render 3D or offscreen.",
    )
    parser.add_argument(
        "--max-lateral-dist",
        type=float,
        default=MAX_LATERAL_DIST_M,
        help="How far sideways of the recorded route the ego may get before MetaDrive ends the "
        f"episode as `out_of_road`. MetaDrive's own value is {MAX_LATERAL_DIST_M} m "
        "(`scenario_env.py:84`) and is the default here, so nothing changes unless it is asked "
        "for. It is on the command line because the recorded route is a *reference line* rather "
        "than a set of walls: under --agent-policy manual a deliberate wrong turn ends the run "
        "within a second, and the same gate cuts off the idm ego at a measured 4.26 m.",
    )
    parser.add_argument(
        "--record",
        default=None,
        help="Write (observation, executed action) pairs to this .npz, for imitation learning. "
        "It reads `vehicle.current_action`, so it captures what the car actually did rather "
        "than what was asked of it - which is why a keyboard drive under --agent-policy manual "
        "produces a file the same shape as one recorded through env.step (see "
        "`examples/drive_with_a_policy.py`). Under --agent-policy replay every recorded action "
        "is [0, 0]: that policy sets the car's position directly and never acts.",
    )
    parser.add_argument(
        "--record-no-images",
        action="store_true",
        help="With --record --render offscreen, keep only the 41-number state half and write "
        "no `images` array. The camera stack is 518 KB a step: measured on a 352-step "
        "junction-1 drive, 84.4 MB on disk with the frames against 29 KB without. No effect "
        "under --render none or 3D, where the observation carries no image to drop.",
    )
    parser.add_argument(
        "--export-drive",
        default=None,
        help="Write the drive that just happened to this directory, as a ScenarioNet dataset "
        "this same tool can drive. It is how a headless machine hands a drive to a machine "
        "with a screen: run it on the rig under --render offscreen, copy the directory back, "
        "and open it here with scripts/watch-drive.sh. It records object states rather than "
        "pixels, so nothing extra is drawn and no --render mode is ruled out. Note the two "
        "neighbours: --record writes (observation, action) pairs for imitation learning, and "
        "--agent-policy replay *reads* a dataset - point it at what this wrote and the ego "
        "retraces the drive. Give it a **repo-relative** directory - "
        "workspaces/<ws>/drives/<label> - and it is one string everywhere: scripts/_common.sh "
        "cds to the repo root and the container works from /work, which is the repo, so the "
        "same command exports on a rig and watches on a laptop. The file carries the rate it "
        "was driven at -- MetaDrive stamps "
        "every export 0.1 s a frame regardless, which this overwrites -- so watch-drive.sh "
        "reads it back and a wrong --step-hz is refused rather than drawn as a spiking car.",
    )
    parser.add_argument(
        "--ros-bag",
        default=None,
        help="Write the drive to this directory as a ROS 2 bag (MCAP, per-chunk zstd), under "
        "the same topic names and message types the vehicle rig records - so a simulated bag "
        "and a real one are interchangeable to whatever reads them. Unlike the rig's, this one "
        "also carries ground truth: a 3D box on every object actually in the scene, the colour "
        "of every traffic light, the ego's pose, and the route. Every topic of one frame shares "
        "one stamp taken from the simulator's clock, so the labels and the pixels of an instant "
        "agree. Drive-time, like --record and --export-drive: it touches no fingerprint and "
        "cannot invalidate a Stage 3 review. Needs Python 3.10+ for `rosbags`, which on the "
        "host means the container - see the refusal message if it fires. Read a bag back with "
        "`uv run python tools/ros_audit.py <dir>`.",
    )
    parser.add_argument(
        "--ros-topics",
        default=None,
        help="Comma-separated subset of the --ros-bag topics to write, for a smaller file. "
        "Default is all of them. Names are the full topic paths, e.g. "
        "/localization/odometry,/perception/objects.",
    )
    parser.add_argument(
        "--ros-bag-past-tape",
        action="store_true",
        help="Keep recording after the drive outlives the recording. Off by default, and the "
        "default is the useful one: past the last recorded frame MetaDrive removes every "
        "replayed pedestrian and cyclist while keeping cones and barriers, so a busy junction "
        "renders empty. Those frames are not mislabelled - the boxes still match the pixels - "
        "they are unrepresentative, and training on them teaches a model this junction has no "
        "people in it. Measured under 25 cars on junction-1: 266 of 645 steps.",
    )
    parser.add_argument(
        "--progress-seconds",
        type=float,
        default=30.0,
        help="How often the drive says it is still running, in seconds of wall clock. 0 turns "
        "it off, and --render 3D ignores it because the window already says so. A wall-clock "
        "interval rather than a step count on purpose: a replayed step is about 1 ms and a "
        "step under the AV3 model about 200, so any step interval that suits one is silent "
        "for hours or unreadable for the other. The line carries route completion, so a car "
        "that is circling shows as a step count climbing against a completion that is not.",
    )
    parser.add_argument(
        "--extra-seconds",
        type=float,
        default=0.0,
        help="Seconds of extra room in the step budget, for anything that makes this drive "
        "legitimately slower than the one the budget was sized for. The budget already "
        "carries a term for the IDM tracking deficit, one for the longest red, and - under "
        "--traffic live - a factor for queueing behind other cars; this is for what none of "
        "them predicted. In seconds rather than steps because a step is 0.1 s or 0.01 s "
        "depending on --step-hz and the seconds are the same number at both. No effect under "
        "--agent-policy manual or remote, which have no budget at all. The `did not arrive` "
        "line prints the budget's terms, so it says how much is being added to what.",
    )
    parser.add_argument(
        "--lights",
        default="tape",
        choices=["tape", "live"],
        help="`tape` replays `dynamic_map_states` - the same colour at the same step on every "
        "episode. `live` drives the same lights from `metadata.signals` and an offset drawn "
        "per episode, so the step number stops predicting the colour.",
    )
    parser.add_argument(
        "--light-seed",
        type=int,
        default=None,
        help="Seed for --lights live, so a run can be repeated.",
    )
    parser.add_argument(
        "--image-on-cuda",
        action="store_true",
        help="Keep rendered camera frames in GPU memory as CuPy arrays instead of copying "
        "them to the host. Needs --render offscreen and the `gpu` dependency group; MetaDrive "
        "asserts at env construction when the three packages behind `_cuda_enable` are not "
        "importable, so a missing install is loud rather than a silent fall back to the CPU. "
        "Refused with --render 3D, where MetaDrive's own teardown raises after a correct "
        "drive. Worth nothing over a socket -- the wire needs host bytes, so the frame is "
        "copied back anyway -- and worth everything to a model in this same process, which "
        "reads the pointer.",
    )
    parser.add_argument(
        "--traffic",
        default="none",
        choices=["none", "live"],
        help="`live` puts other cars on the road, generated from the reviewed lane graph and "
        "driven by MetaDrive's own IDM. They are not in the dataset - a recorded track has to "
        "be as long as the episode, so the road would empty around a slow agent - so this is "
        "the only way to see them. Needs `osm-scenario traffic` to have been run.",
    )
    parser.add_argument(
        "--traffic-count",
        type=int,
        default=None,
        help="How many cars are on the road at once under --traffic live.",
    )
    parser.add_argument(
        "--traffic-seed",
        type=int,
        default=0,
        help="Seed for --traffic live, so a run can be repeated. The engine's own scenario "
        "seed is mixed in beside it, which is what makes two resets differ.",
    )
    parser.add_argument(
        "--traffic-speed",
        choices=("profile", "flat"),
        default="profile",
        help="Whether traffic slows for the corners its route actually turns through. On by "
        "default: MetaDrive's IDM aims for a flat 40 km/h everywhere, and 29.5%% of "
        "junction-1's route distance allows less than that on curvature alone. `flat` is for "
        "measuring what the profile is worth, not for driving.",
    )
    parser.add_argument(
        "--traffic-give-way",
        choices=("on", "off"),
        default="on",
        help="Whether traffic gives way where two routes cross. On by default: IDM brakes "
        "only for cars on its own lane, so a junction full of it collides. `off` is for "
        "measuring what the rule is worth, not for driving.",
    )
    parser.add_argument(
        "--traffic-file",
        default=None,
        help="traffic.json to read. Defaults to <workspace>/traffic/traffic.json, worked out "
        "from the dataset directory.",
    )
    parser.add_argument(
        "--camera-rig",
        default=None,
        help="A CARLA-shaped camera spec to mount on the ego (rigs/av3.txt is the AV3 "
        "model's). Needs --render offscreen or 3D; the spec's tick_rate must match the "
        "interval the cameras are read at, which is --decision-hz.",
    )
    parser.add_argument(
        "--model-checkpoint",
        # `MODEL_CHECKPOINT` for `model_probe.py:413`'s reason, and because this was the one
        # tool that did not honour it: `compose.yaml` sets it to the mounted /models path, so
        # in the container every other entry point found the checkpoint by itself and this one
        # alone had to be handed a literal path that only exists inside a container.
        default=os.environ.get("MODEL_CHECKPOINT"),
        help="An AV3 .ep checkpoint to run at every decision, supplying the trajectory the "
        "hosted controller consults. Implies --camera-rig rigs/av3.txt and "
        "--agent-policy remote. About a second a forward pass on this card. "
        "MODEL_CHECKPOINT is the default.",
    )
    parser.add_argument(
        "--model-config",
        default=None,
        help="model_dev.yml for --model-checkpoint. The fork checkout's copy otherwise.",
    )
    parser.add_argument(
        "--waypoints",
        choices=("modelv2", "derive"),
        default="modelv2",
        help="What a model's prediction is sent as. `modelv2` forwards its own yaw, velocity "
        "and acceleration per waypoint to the bridge's `from_predicted`; `derive` sends "
        "positions only and lets the bridge reconstruct them by a cubic fit, which is the "
        "path every measurement before Stage 9 Phase C.2 was taken on.",
    )
    arguments = parser.parse_args()

    # Refused rather than worked around. `ManualControlPolicy.__init__` reads the keyboard
    # through panda3d only when `use_render` is on; without it, it falls back to opening a
    # *pygame* window purely to collect key strikes (`manual_control_policy.py:50-56`), so the
    # car would be driven from a blank window with no view of the map. The failure without this
    # check is a window that never appears, which says nothing about the cause.
    if arguments.agent_policy == "manual" and arguments.render != "3D":
        print(
            f"result       FAILED: --agent-policy manual needs --render 3D, not "
            f"--render {arguments.render}. Without a rendered window MetaDrive reads the "
            f"keyboard through a blank pygame window instead."
        )
        return 1

    sensor_names = tuple(name.strip() for name in arguments.sensors.split(",") if name.strip())
    if arguments.agent_policy == "remote" and not arguments.policy_url:
        print("result       FAILED: --agent-policy remote needs --policy-url")
        return 1
    if arguments.policy_url and arguments.agent_policy != "remote":
        print(
            f"result       FAILED: --policy-url only drives under --agent-policy remote, not "
            f"{arguments.agent_policy}. The other three ignore the action passed to env.step."
        )
        return 1
    # --model-checkpoint implies the rig it was trained on and the socket that consults it.
    # Defaulted rather than refused: there is exactly one rig this checkpoint reads, and
    # naming it on every command line would be a way to get it wrong.
    if arguments.model_checkpoint and not arguments.camera_rig:
        arguments.camera_rig = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "rigs", "av3.txt"
        )
    if arguments.model_checkpoint and arguments.agent_policy != "remote":
        print(
            f"result       FAILED: --model-checkpoint supplies a trajectory for a hosted "
            f"controller to steer by, so it needs --agent-policy remote, not "
            f"{arguments.agent_policy}. The model does not produce pedals; the bridge does."
        )
        return 1
    # Refused here, before the terrain build, and printing the interpreter: torch missing is
    # otherwise a traceback several minutes into a run. `_cuda_enable`'s refusal above does
    # the same thing for the same reason.
    if arguments.model_checkpoint:
        try:
            import torch  # noqa: F401
        except ImportError:
            import env_hint

            print(
                "result       FAILED: --model-checkpoint needs torch, which is not installed "
                f"in {sys.executable}.\n"
                + env_hint.install_hint(("sim", "gpu", "model"))
            )
            return 1
        if not os.path.exists(arguments.model_checkpoint):
            print(f"result       FAILED: no checkpoint at {arguments.model_checkpoint}")
            return 1
    # A rig is cameras, and a camera has to be rendered before it can be read - the same rule
    # the heavy --sensors are refused under, one line below.
    if arguments.camera_rig and arguments.render not in ("offscreen", "3D"):
        print(
            f"result       FAILED: --camera-rig needs --render offscreen or 3D, not "
            f"--render {arguments.render}. base_env.py:343 drops every camera when nothing "
            "is rendering, so the rig would be mounted on nothing."
        )
        return 1
    # A camera has to be rendered before it can be read: `base_env.py:343` drops every camera
    # from the sensor list when nothing is rendering, so asking for one under --render none
    # sends nothing and says nothing. Refused instead.
    heavy = [name for name in sensor_names if name in HEAVY_POLICY_SENSORS]
    if heavy and arguments.render == "none":
        print(
            f"result       FAILED: --sensors {','.join(heavy)} needs --render 3D or offscreen. "
            f"Without a render context MetaDrive builds no camera at all."
        )
        return 1
    # Same rule, one step further along: `image_on_cuda` is handed to each registered camera
    # at construction (`engine_core.py:615`), so with no camera registered there is nothing
    # for it to reach and the flag would be accepted and do nothing at all.
    if arguments.image_on_cuda and arguments.render == "none":
        print(
            "result       FAILED: --image-on-cuda needs --render offscreen. The key reaches "
            "cameras only (engine_core.py:615), and --render none builds none."
        )
        return 1
    # And 3D is refused for a fault at the *other* end of the run. The drive itself is correct
    # -- measured on `junction-1`, 352 of 370 steps, arrive_dest=True, completion 0.953 -- but
    # `env.close()` then raises `cudaErrorInvalidGraphicsContext(219)` out of
    # `MainCamera.unregister` (`main_camera.py:585`, via `base_engine.py:529`), which hands a
    # CUDA graphics resource back against a GL context that has already gone. MetaDrive's bug,
    # and not one this repo patches -- but it makes a successful drive exit non-zero, so the
    # status stops meaning "the dataset is drivable", which is the whole job of this tool.
    #
    # Refused rather than caught because the pairing buys nothing today: the point of holding a
    # frame on the card is a model reading the pointer in this same process (Phase C), and 3D
    # is for a person to watch. Should Phase C ever want to watch a CUDA-fed model in a window,
    # this is the line to revisit -- catching that one error around `close()` is the other way.
    if arguments.image_on_cuda and arguments.render == "3D":
        print(
            "result       FAILED: --image-on-cuda cannot be used with --render 3D. The drive "
            "is fine, but MainCamera.unregister (main_camera.py:585) raises "
            "cudaErrorInvalidGraphicsContext(219) during env.close(), so a successful drive "
            "exits non-zero. Watch with --render 3D alone; keep frames on the card with "
            "--render offscreen --image-on-cuda."
        )
        return 1
    if arguments.image_on_cuda:
        # MetaDrive raises rather than falling back, but from two different places with two
        # different hints: offscreen it is `ImageObservation.__init__` (`image_obs.py:57`),
        # which gates on cupy alone and says "pip install cupy-cuda12x"; under `--render 3D`
        # no image observation is built, so it is `BaseCamera.__init__`
        # (`base_camera.py:56`), whose hint is "pip install pypiwin32" -- Windows advice for
        # a Linux box. Both fire minutes into a terrain build. Answered here instead, naming
        # the interpreter, because the usual cause is running the *checkout's* 3.8 venv
        # rather than this repo's, and the group is installed in this repo's.
        from metadrive.component.sensors.base_camera import _cuda_enable

        if not _cuda_enable:
            import env_hint

            print(
                "result       FAILED: --image-on-cuda needs cupy, PyOpenGL and cuda-python "
                "importable, and `metadrive.component.sensors.base_camera._cuda_enable` is "
                f"False on {sys.executable}.\n"
                + env_hint.install_hint(("sim", "gpu"))
                + "\ncuda-python must be < 13: 13.0 dropped the `cuda.cudart` shim this imports."
            )
            return 1

    # Checked here, before the terrain build, for the reason the checkpoint above is: a drive
    # is minutes, and a directory that cannot be written is knowable in microseconds. A
    # directory that already holds a drive is **replaced**, never merged: `save_dataset` writes
    # a summary naming only what this run exported, so merging would leave the previous drive's
    # `sd_*.pkl` beside a summary that does not list them - a dataset that reads as smaller than
    # it is. Replacing rather than refusing, because Ctrl-C stopping a drive early makes
    # re-running into the same `drives/<label>` the ordinary gesture; see `_clear_export`.
    # Anything the export does not own is still refused, so a mistyped directory is safe.
    if arguments.export_drive:
        # Checked before the two below because it is the one that can be *answered* rather than
        # only reported: see `_container_path_refusal`.
        refusal = _container_path_refusal(arguments.export_drive)
        if refusal:
            print(refusal)
            return 1
        owned, foreign = _export_files(arguments.export_drive)
        if foreign:
            print(
                f"result       FAILED: --export-drive {arguments.export_drive} holds "
                f"{len(foreign)} file(s) this did not write "
                f"({', '.join(foreign[:3])}{', ...' if len(foreign) > 3 else ''}).\n"
                "             A drive export replaces a directory, so it will only write into "
                "an empty one or one holding a previous drive."
            )
            return 1
        if os.path.exists(arguments.export_drive) and not os.path.isdir(arguments.export_drive):
            print(
                f"result       FAILED: --export-drive {arguments.export_drive} is a file, not "
                "a directory. It writes a dataset directory, not one file."
            )
            return 1
        # Made now, not at the end: creating it is the only honest test that it *can* be
        # created, and finding out otherwise after a drive is the whole thing this block
        # exists to avoid. `workspaces/<ws>/drives/` will not exist the first time, and
        # having the caller mkdir -p before a flag will work is a step with no purpose.
        try:
            os.makedirs(arguments.export_drive, exist_ok=True)
        except OSError as error:
            print(
                f"result       FAILED: --export-drive cannot create "
                f"{arguments.export_drive}: {error}"
            )
            return 1

    import numpy

    print(f"interpreter  python {sys.version.split()[0]} / numpy {numpy.__version__}")

    dataset = os.path.abspath(arguments.dataset)

    if arguments.map_region_size is None:
        region, furthest, where = _region_for(dataset)
        print(
            f"map region   {region} m; the map reaches {furthest:.0f} m "
            f"from the ego's start in {where}"
        )
    else:
        region = arguments.map_region_size
        print(f"map region   {region} m, as asked")

    pixels_per_meter = arguments.semantic_pixels_per_meter
    if pixels_per_meter is None:
        # Only worth a whole process when something is going to be textured, and only when the
        # answer has not already been given on the command line.
        ceiling = _max_texture_dimension() if arguments.render != "none" else None
        if ceiling is None:
            ceiling = FALLBACK_MAX_TEXTURE
            source = f"assuming {FALLBACK_MAX_TEXTURE} px"
        else:
            source = f"the card reports {ceiling} px"
        pixels_per_meter = max(1, ceiling // region)
    else:
        source = "as asked"
    texture = region * pixels_per_meter
    print(
        f"road texture {texture} px square ({pixels_per_meter} px/m, {source}). MetaDrive's "
        f"own choice here would be {_metadrive_native_texture(region)} px, which is why this "
        f"is patched."
    )
    _set_semantic_detail(pixels_per_meter)

    # Metres in, pixels out, and the two do not meet exactly: `cv2.polylines` takes an integer
    # and cannot go below 1, so on a big map the floor decides the width rather than the
    # request does. Say which happened rather than rounding quietly.
    dashes = 2.0 if arguments.line_interval_m > 1.5 else 3.0
    if arguments.line_width_m > 0:
        thickness = max(1, round(arguments.line_width_m * pixels_per_meter))
        drawn = thickness / pixels_per_meter
        floored = "; 1 px is the floor at this resolution" if thickness == 1 else ""
        print(
            f"lane lines   {thickness} px = {drawn:.3f} m "
            f"(asked {arguments.line_width_m:.3f} m{floored}), sampled every "
            f"{arguments.line_interval_m:.2f} m, dashes {dashes:.0f} m on / {dashes:.0f} m off"
        )
        _set_line_width(thickness, arguments.line_interval_m)
    else:
        print("lane lines   MetaDrive's own 2 px / 3 px, as asked")

    # Unconditional, and not behind `--line-width-m`: a line drawn short is a fault in the
    # renderer rather than a preference about how it looks. See `_keep_line_ends`.
    _keep_line_ends()

    from metadrive.component.sensors.rgb_camera import RGBCamera
    from metadrive.envs.scenario_env import ScenarioEnv
    from metadrive.policy.env_input_policy import EnvInputPolicy
    from metadrive.policy.replay_policy import ReplayEgoCarPolicy
    from metadrive.scenario.utils import get_number_of_scenarios

    # `TrajectoryIDMPolicy` subclasses `IDMPolicy`, whose `lane_change_policy` checks whether
    # the object in front is a `BaseTrafficLight` - so it is the only ego policy here that
    # *reacts* to one. `ReplayEgoCarPolicy` sets the car's position directly each step and
    # would drive through a wall of any kind; it stops at a red only because the converter
    # wrote the stop into the positions, which is why the two lines below differ in where the
    # stopping comes from rather than in whether it happens.
    #
    # `manual` is not a third class here. `agent_manager.agent_policy` (`agent_manager.py:64-75`)
    # resolves in a fixed order - `TakeoverPolicy` first, then `manual_control`, then this value
    # - so setting `manual_control` below is what selects `ManualControlPolicy`, and the class
    # named here is not consulted for the ego at all. It is `EnvInputPolicy` rather than a
    # placeholder because that is the class `ManualControlPolicy` subclasses
    # (`manual_control_policy.py:37`): the two differ only in where `[steering, throttle]` comes
    # from, so the pair says what is really happening. And the value is not entirely dead -
    # `scenario_traffic_manager.py:65` reads it again to decide whether the ego is a replayed
    # car, which is what stops traffic spawning on top of a car that is driving itself. That
    # matters only once a scenario holds traffic, but naming `ReplayEgoCarPolicy` here would be
    # a wrong answer waiting for one.
    policy = {
        "replay": ReplayEgoCarPolicy,
        # `windowed_policy_class` rather than MetaDrive's own, and the difference is the
        # whole of `docs/reference/live-traffic.md`: the stock class projects the car onto
        # the *whole* route every step, winds up a heading integral it never resets, and
        # latches one saturated pedal for four steps in five. A subclass is a drop-in -
        # `agent_manager.py:49` tests `issubclass(..., TrajectoryIDMPolicy)` before it
        # hands over `current_sdc_route`.
        "idm": windowed_policy_class(),
        "manual": EnvInputPolicy,
        # The same class as `manual`, and that is the fact rather than a shortcut:
        # `ManualControlPolicy` subclasses `EnvInputPolicy` (`manual_control_policy.py:37`),
        # so a keyboard drive and a hosted model's drive differ only in where the two numbers
        # come from. `manual_control` below is what separates them.
        "remote": EnvInputPolicy,
    }[arguments.agent_policy]
    print(
        "ego policy   {} - {}".format(
            arguments.agent_policy,
            {
                "replay": "replayed positions; it stops only where the recording stops",
                "idm": "driven along the recorded route; it brakes for red lights itself",
                # `h` rather than a paraphrase: MetaDrive keeps the real list in
                # `constants.py:37` and shows it in the window. Naming `q` here anyway because
                # `b` is the one key that makes the car stop responding - the keyboard does not
                # steer in top-down view - and `b` does not toggle back out of it.
                "manual": "driven from the keyboard: WASD, `h` for MetaDrive's own key list, "
                "`q` back to the driving view from `b`'s top-down one",
                "remote": f"driven by the model at {arguments.policy_url}; the recorded route "
                "becomes the goal rather than the drive",
            }[arguments.agent_policy],
        )
    )
    if arguments.agent_policy == "manual":
        # MetaDrive keeps the real list in `constants.py:37` and shows it on `h`; printed here
        # too because the first run is the one where nobody knows to press `h`. The focus line
        # is the failure this cannot check for us: panda3d reads the keyboard through the
        # window, so keys pressed while another window has focus reach nothing, and the ego -
        # which spawns at the *recorded* speed rather than at rest - drives off on its own.
        from metadrive.constants import HELP_MESSAGE

        print("             " + HELP_MESSAGE.replace("\n", "\n             ").rstrip())
        print(
            "             Click the window before driving: the keys go to whichever window has "
            "focus.\n             The on-screen `steering` and `throttle` are what the car is "
            "executing - if\n             they stay at 0 while you press W or A, the window is "
            "not getting the keys."
        )

    environment_class = ScenarioEnv
    if arguments.lights == "live":
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from signal_control import live_signal_env

        environment_class = live_signal_env(arguments.light_seed)

    traffic_plan = None
    if arguments.traffic == "live":
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from traffic import DEFAULT_COUNT as TRAFFIC_DEFAULT_COUNT
        from traffic import LOST_LATERAL_M as TRAFFIC_LOST_LATERAL_M
        from traffic import TrafficError, load_plan, traffic_env

        traffic_path = arguments.traffic_file or _default_traffic_file(dataset)
        try:
            traffic_plan = load_plan(traffic_path)
        except (OSError, ValueError, TrafficError) as error:
            print(f"result       FAILED: --traffic live needs a traffic plan: {error}")
            print(
                "             Build one with: uv run osm-scenario traffic -w <workspace>"
            )
            return 1
        cars = (
            arguments.traffic_count
            if arguments.traffic_count is not None
            else TRAFFIC_DEFAULT_COUNT
        )
        # Composed onto whatever `--lights` already chose rather than replacing it. Both are
        # whole env classes, so assigning here would silently drop the lights - and a red
        # light is the only thing separating conflicting movements, IDM having no give-way.
        environment_class = traffic_env(
            environment_class,
            plan=traffic_plan,
            count=cars,
            seed=arguments.traffic_seed,
            give_way=arguments.traffic_give_way == "on",
            follow_speed_profile=arguments.traffic_speed == "profile",
        )
        print(
            f"traffic      {cars} car(s) from {len(traffic_plan['routes'])} route(s) "
            f"in {traffic_path}"
        )

    count = get_number_of_scenarios(dataset)
    if arguments.scenario_index is not None and not 0 <= arguments.scenario_index < count:
        print(f"result       FAILED: --scenario-index must be below {count}")
        return 1

    # Terrain is only built when something is rendering it: `Terrain.reset` guards the whole
    # heightfield and texture path on `self.render or use_mesh_terrain`. So `--render none`
    # deliberately exercises none of what this script configures, and `offscreen` is how the
    # same work happens without a display.
    offscreen = {}
    if arguments.render == "offscreen":
        offscreen = {"image_observation": True, "sensors": {"rgb_camera": (RGBCamera, 320, 240)}}

    # A camera asked for by --sensors has to be registered on the env before it exists to be
    # read. Merged rather than assigned: `image_observation` looks its source up by the name
    # `rgb_camera` (`image_obs.py:68`), so replacing the dict above would kill the env at
    # construction with a KeyError that names a sensor nobody asked about.
    if sensor_names:
        from policy_client import sensor_config

        registered = sensor_config(sensor_names)
        if registered:
            merged = dict(offscreen.get("sensors", {}))
            merged.update(registered)
            offscreen["sensors"] = merged

    # Folded in only when it was asked for, so an unflagged run's config is unchanged
    # key-for-key rather than merely equal by arithmetic.
    rate = {} if arguments.step_hz is None else step_config(arguments.step_hz)
    # Counted against the rate actually in force, which is MetaDrive's own 10 when --step-hz
    # was not passed - so `--decision-hz 5` alone is a legal 2x stride rather than an error
    # about a flag the caller did not use.
    effective_hz = arguments.step_hz if arguments.step_hz is not None else DEFAULT_STEP_HZ
    # Folded in only when asked for, the same way `rate` is: `record_episode` defaults to False
    # in `BASE_DEFAULT_CONFIG` (`base_env.py:265`), so an unflagged run's config stays unchanged
    # key-for-key rather than merely equal by value.
    recording = {}
    if arguments.export_drive:
        recording = {"record_episode": True}
        _record_every_step()
        # Said before the drive rather than discovered after one. A drive that stalls does not
        # terminate - `terminated` and `truncated` stay false and the loop steps to its budget -
        # so the moment this is wanted is fifty minutes into a run nobody wants to sit through,
        # and a flag nobody knew about is the same as no flag. `owned` is what the export is
        # about to replace; naming it is the only warning that a previous drive is going.
        print(
            f"export       {arguments.export_drive} - Ctrl-C stops the drive and exports "
            "what it has; a second Ctrl-C kills the run"
        )
        if owned:
            print(
                f"             replacing a previous drive there ({len(owned)} file(s)); "
                "an export replaces its directory rather than merging into it"
            )
    try:
        stride = decision_stride(effective_hz, arguments.decision_hz)
    except ValueError as error:
        print(f"result       FAILED: {error}")
        return 2
    decision_seconds = stride / float(effective_hz)
    # `frame_gate` refuses this pairing on its own terms -- the CUDA frame is filled by a
    # panda3d draw callback rather than returned by `perceive`, so a *held* frame is a buffer
    # something else is still writing -- but it raises after the env is up, minutes into a
    # terrain build. Said here instead, and only when the stride really would hold a frame:
    # at stride 1 the gate never holds anything, so the two flags do not conflict there.
    if arguments.image_on_cuda and stride > 1 and not arguments.draw_every_step:
        print(
            f"result       FAILED: --image-on-cuda cannot be gated. At {arguments.decision_hz:g} "
            f"Hz the draw is held for {stride - 1} of every {stride} steps, and a held CUDA "
            "frame is a buffer still being written. Add --draw-every-step, or drop one flag."
        )
        return 1
    if stride > 1:
        print(
            "decision     {:g} Hz: every {} env.step, {:g} s apart. {}".format(
                arguments.decision_hz, stride, decision_seconds,
                "the reads only - MetaDrive calls the replay policy in-engine on every "
                "step" if arguments.agent_policy == "replay"
                else "the action is held across the steps in between",
            )
        )
        # Said rather than implied, because it is the half a reader cannot see from the
        # outside: whether the frames themselves came at this rate or only the looks at them.
        if arguments.render == "offscreen":
            print(
                "             cameras   {}".format(
                    f"drawn every env.step ({effective_hz:g} Hz) - --draw-every-step"
                    if arguments.draw_every_step
                    else f"drawn at {arguments.decision_hz:g} Hz too, not at the world tick"
                )
            )
        elif arguments.render == "3D":
            print(
                f"             cameras   the window is drawn every env.step "
                f"({effective_hz:g} Hz): the draw is never gated under --render 3D"
            )
    if rate:
        print(
            "step rate    {:g} Hz: physics_world_step_size={:g}, decision_repeat={}".format(
                arguments.step_hz, rate["physics_world_step_size"], rate["decision_repeat"]
            )
        )
        # Three things inside MetaDrive are written in steps rather than in seconds. None is
        # a fault in the dataset and none is ours to patch - a reference checkout is not
        # edited here - so each is said out loud and left alone.
        for condition, warning in (
            (
                arguments.agent_policy == "idm",
                "TrajectoryIDMPolicy will not drive identically at this rate: "
                "`PIDController` has no timestep in it at all (`PID_controller.py:1-22`), so "
                "both its gains scale with the rate, and `LANE_CHANGE_FREQ = 50` and "
                "`IDM_ACT_BATCH_SIZE = 5` are counted in steps.",
            ),
            (
                arguments.agent_policy == "manual" and arguments.step_hz > 10.0,
                f"the keyboard will feel {arguments.step_hz / 10.0:.0f}x slower: "
                "`STEERING_INCREMENT` is applied per env.step, so the wheel takes that "
                "many more steps to reach full lock.",
            ),
            (
                arguments.render == "3D" and arguments.step_hz > 10.0,
                "the display targets {:.0f} fps: `ForceFPS` takes its interval from "
                "`physics_world_step_size`, so the drive will run slower than wall-clock."
                "".format(1.0 / rate["physics_world_step_size"]),
            ),
        ):
            if condition:
                print("             note: " + warning)

    # The rig, after the stride is known: its `tick_rate` is checked against the interval the
    # cameras will really be read at, which is 1/--step-hz times that stride. Nothing here
    # resamples, so a spec asking for a rate the drive is not running at is refused by name.
    rig = None
    if arguments.camera_rig:
        from camera_rig import RigError, load_rig

        try:
            rig = load_rig(arguments.camera_rig, read_interval_s=decision_seconds)
        except RigError as error:
            print(f"result       FAILED: {error}")
            return 1
        # `image_observation` builds the observation from `config["sensors"][image_source]`
        # (`image_obs.py:68`), and that name defaults to `rgb_camera`, which a rig does not
        # have. Pointing it at a rig camera is what stops a dead 320x240 buffer being
        # registered beside the rig and rendered every step - one of the very few image
        # buffers panda3d holds reliably (`camera_rig.MAX_IMAGE_BUFFERS`).
        merged = dict(offscreen.get("sensors", {}))
        merged.pop("rgb_camera", None)
        merged.update(rig.sensors())
        offscreen["sensors"] = merged
        offscreen["vehicle_config"] = dict(image_source=rig.image_source())
        for line in rig.describe():
            print("rig          " + line if line[:1].isdigit() else "             " + line)

    env = environment_class(
        {
            "data_directory": dataset,
            "num_scenarios": count,
            "use_render": arguments.render == "3D",
            "agent_policy": policy,
            **rate,
            **offscreen,
            **recording,
            # The one key that selects `ManualControlPolicy`; see the comment on `policy` above.
            "manual_control": arguments.agent_policy == "manual",
            "reactive_traffic": arguments.reactive,
            # Off unless asked for, so an unflagged run's config is unchanged key-for-key.
            # It reaches every registered camera through `engine_core.py:615` and, offscreen,
            # the observation stack itself (`image_obs.py:55-65`).
            "image_on_cuda": arguments.image_on_cuda,
            "map_region_size": region,
            "height_scale": arguments.height_scale,
            "max_lateral_dist": arguments.max_lateral_dist,
            "drivable_area_extension": arguments.drivable_area_extension,
            # Longer than any generated route; the loop below ends on the scenario's own
            # length rather than on a step budget.
            "horizon": 100000,
            "show_logo": False,
            "show_fps": False,
            "log_level": logging.WARNING,
        }
    )

    # Built here rather than imported at the top so that a run without --record does not depend
    # on `agent_env` at all, and so the only cost of the flag existing is this branch.
    # One entry per scenario driven, filled at the end of each episode. It has to be
    # collected *inside* the loop rather than dumped once at the end: `RecordManager`
    # starts a fresh `episode_info` on every `before_reset` (`record_manager.py:50`), so
    # after a two-scenario run the engine holds only the second one.
    exported = []
    reported_settle = False
    ros_bag = None
    ros_projection = None
    ros_tape_steps = 0
    ros_stopped_at = None
    ros_topics = (
        {name.strip() for name in arguments.ros_topics.split(",") if name.strip()}
        if arguments.ros_topics
        else None
    )
    if arguments.ros_bag:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import ros_frame

        # Before anything is built. `rosbags` needs 3.10 and `tools/` also runs on the MetaDrive
        # checkout's 3.8, so an unsupported interpreter has to be a refusal at the top rather
        # than an ImportError three hundred frames into a recording.
        #
        # Caught rather than raised, in the `result FAILED:` form every other refusal in this
        # file uses. The message already names both ways out; a traceback above it only buries
        # them, and the interpreter is the single most common thing to have wrong here.
        try:
            ros_frame.refuse_if_unsupported()
        except ros_frame.RosFrameError as error:
            print(f"result       FAILED: {error}")
            return 1

    recorder = None
    if arguments.record:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from agent_env import ActionRecorder

        # Both read off the env rather than off the flags: whether the image half is a
        # normalised picture is MetaDrive's `norm_pixel` and the image source's class, and
        # `_images_are_normalised` is where that is worked out.
        recorder = ActionRecorder(
            normalised_images=_images_are_normalised(env),
            store_images=not arguments.record_no_images,
        )

    # The model, after the env, because loading a 1.2 GB TensorRT engine before the terrain
    # build would put both peaks on the card at once for no reason. `av3_model` reads the
    # config with PyYAML and refuses a missing key rather than defaulting it.
    model = None
    if arguments.model_checkpoint:
        import av3_model

        try:
            model = av3_model.AV3Model(
                av3_model.load_config(arguments.model_config or av3_model.DEFAULT_CONFIG),
                arguments.model_checkpoint,
                decision_seconds,
            ).load()
        except av3_model.ModelError as error:
            print(f"result       FAILED: {error}")
            env.close()
            return 1
        note = model.history.spacing_note
        print(
            f"model        {model.n_waypoints} waypoints x {model.output_width} over "
            f"{av3_model.MODEL_HORIZON_S:g} s, loaded in {model.load_seconds:.1f} s; "
            f"history {model.config.t_frames} frames "
            f"{model.history.actual_stride_s:g} s apart"
        )
        if note:
            print("             note: " + note)
        print(
            "             sent as {}".format(
                "modelv2 - its own yaw, yaw rate, velocity and acceleration per waypoint, "
                "straight into the bridge's from_predicted"
                if arguments.waypoints == "modelv2"
                else "positions only; the bridge reconstructs the rest by a cubic fit"
            )
        )

    # Built after the env, because `SensorPack` checks the sensors it was asked for are really
    # registered rather than sending nothing for them.
    remote = None
    if arguments.agent_policy == "remote":
        from policy_client import RemotePolicy, SensorPack

        remote = RemotePolicy(
            arguments.policy_url,
            pack=SensorPack(env, sensor_names),
            # The interval between two *calls*, which is what a controller integrating
            # anything is working in - not `sim_step_seconds`, which is the interval between
            # two `env.step`s and is `stride` times shorter. openpilot's bridge counts its lag
            # compensation and its curvature-rate limit against exactly this number
            # (`openpilot_policy.BRIDGE_DT_S`), so getting it wrong mis-scales them silently.
            step_seconds=sim_step_seconds(env) * stride,
        )
        if model is not None:
            # `init` is sent once a connection and the bridge builds its lateral MPC from
            # `n_waypoints`, so this cannot wait for the first `/act`. 20 is in the fork's
            # prebuilt AV3_MPC_MENU ("4 16 20 32"), so the solver for it exists already and
            # the first tick is not a code-generation pause.
            remote.episode_extra = {"n_waypoints": model.n_waypoints}
        # The rates go through `spec`'s existing `extra`, which is why `policy_client` needs
        # no new field: what a model has to know is the interval between two calls, and
        # `step_seconds` above already carries it. These name the clocks underneath it, so a
        # controller written for one rate can say so - openpilot's does.
        remote.spec(
            {
                "dataset": dataset,
                "rates": {
                    "decision_hz": effective_hz / stride,
                    "world_tick_hz": effective_hz,
                    "physics_hz": float(env.config["decision_repeat"])
                    / sim_step_seconds(env),
                    "steps_per_decision": stride,
                },
            }
        )
        print(
            "sensors      {}".format(
                ", ".join(sensor_names) if sensor_names else "the observation only"
            )
        )

    indices = (
        [arguments.scenario_index] if arguments.scenario_index is not None else list(range(count))
    )
    reported_gpu = False
    failures = 0
    # Installed after the first reset, which is when the engine exists, and once - `install`
    # wraps `env.reset`, so doing it per scenario would stack a wrapper per episode. It
    # returns None unless this env renders offscreen; see `frame_gate`.
    gate = None
    gate_settled = False
    # Printed from the handler itself, not from the loop, because under the AV3 model a step is
    # about a second: a Ctrl-C that says nothing for a second reads as a Ctrl-C that did not
    # land, and the next one kills the run this exists to save.
    closing = _EarlyClose(
        lambda: print(
            "stopping     Ctrl-C - finishing this frame, then "
            + ("exporting the drive so far" if arguments.export_drive else "closing")
            + ". Ctrl-C again exits at once, keeping nothing.",
            flush=True,
        ),
        lambda: print(
            "stopping     Ctrl-C again - exiting now. "
            + (
                "The drive so far is lost: it lives in MetaDrive's RecordManager until the "
                "loop ends, and nothing is written on this path."
                if arguments.export_drive
                else "Nothing was being exported."
            ),
            flush=True,
        ),
    )
    if arguments.ros_bag:
        from ros_bag import BagWriter

        ros_bag = BagWriter(
            arguments.ros_bag,
            topics=ros_topics,
            notes={
                "dataset": dataset,
                "step_hz": effective_hz,
                "decision_hz": effective_hz / stride,
                "agent_policy": arguments.agent_policy,
                "traffic": arguments.traffic,
                "lights": arguments.lights,
            },
        ).__enter__()

    # Built on the first reset, once the scenario is loaded and the ego's route exists.
    pace = None
    try:
        closing.install()
        for index in indices:
            observation, _ = env.reset(seed=index)
            # Only where the car cannot settle itself: a replayed ego at one physics tick per
            # teleport. See `_settle_on_the_road`. Every other rate reaches the same height on
            # its own, so this fires on nothing that was already right.
            if arguments.agent_policy == "replay" and env.config["decision_repeat"] == 1:
                was, now, ticks = _settle_on_the_road(env)
                if not reported_settle:
                    reported_settle = True
                    print(
                        f"ride height  settled the replayed car from z {was:.3f} to {now:.3f} m "
                        f"in {ticks} physics ticks. At decision_repeat 1 the body never "
                        f"integrates -- every physics call follows a teleport -- so without "
                        f"this it stays at the dataset's z and is drawn under the road."
                    )
            if rig is not None:
                # After the reset, not before: `mount` parents each camera to
                # `env.agent.origin`, and the ego does not exist until the scenario is loaded.
                rig.mount(env)
            if model is not None:
                # The ring is the model's memory of *this* drive; carrying it across episodes
                # would open the next one with two seconds of the last one's road.
                model.start_episode()
            if not gate_settled:
                gate_settled = True
                # Never under `image_on_cuda`: `frame_gate.install` refuses it outright,
                # and at stride 1 -- the only pairing that reaches here, the refusal above
                # having taken the rest -- there is nothing for the gate to hold anyway.
                if not arguments.draw_every_step and not arguments.image_on_cuda:
                    gate = install_frame_gate(env)
                if arguments.image_on_cuda:
                    print(_cuda_frame_report(observation, env.engine))

            if not reported_gpu:
                reported_gpu = True
                window = env.engine.win
                limit = window.getGsg().getMaxTextureDimension() if window else None
                driver = window.getGsg().getDriverRenderer() if window else None
                if limit is None:
                    print("gpu          no graphics context; terrain is not built at all")
                elif texture > limit:
                    print(
                        f"gpu          {driver} reports a {limit} px limit, and the road "
                        f"texture is {texture} px. The road surface will not render. Re-run "
                        f"with --semantic-pixels-per-meter {max(1, limit // region)} or lower."
                    )
                    failures += 1
                else:
                    print(
                        f"gpu          {driver}, {limit} px limit; "
                        f"the {texture} px texture fits"
                    )

            length = env.engine.data_manager.current_scenario_length
            scenario = env.engine.data_manager.current_scenario
            scenario_id = scenario["id"]
            if arguments.agent_policy == "idm":
                # Per episode: the policy object is rebuilt on every reset, and so is the
                # route it follows. The cruise speed is read off this scenario's own tape.
                pace = _EgoPace(_recorded_cruise_mps(scenario))
                pace.start_episode(env)
            # The two clocks. `sim_dt` is how far one `env.step` advances the simulator;
            # `data_dt` is how far one recorded frame covers. Equal only when the dataset was
            # converted at the rate this run is driving at, which is what `_refuse_mismatch`
            # below is about.
            sim_dt = sim_step_seconds(env)
            data_dt = data_step_seconds(scenario)
            path_every = max(1, int(round(0.1 / sim_dt)))
            lights = getattr(env.engine, "light_manager", None)
            if ros_bag is not None:
                import ros_frame

                # Where the recording ends, in this drive's own steps. Past it MetaDrive
                # removes every replayed pedestrian and cyclist while keeping cones and
                # barriers, so a busy junction renders empty - frames that are not mislabelled
                # but are unrepresentative, and training on them teaches a model this junction
                # has no people in it. The bag stops there unless asked not to.
                ros_tape_steps = int(round(length * data_dt / sim_dt))

                # Per scenario: the CRS and MetaDrive's re-centring shift both belong to this
                # scenario, and the shift is the 93.8 m trap - carried once here rather than
                # subtracted at every call site.
                ros_projection = ros_frame.projection_of(scenario)
                ros_bag.start_episode(
                    ros_frame.read(env, 0, 0.0, ros_projection),
                    route=ros_frame.route_of(env),
                    mounts=ros_frame.mounts_from_rig(rig) if rig is not None else None,
                )
            if recorder is not None:
                recorder.start_episode(scenario_id)
            if remote is not None:
                remote.start_episode(scenario_id)

            # The recording's length is the right bound for a replayed car - there is nothing
            # after the last recorded position - and the wrong one for any policy that drives
            # itself. A car of its own that stops at a red needs more steps than the recording
            # has, and cutting it off there reports `did not arrive` for a car that was still
            # driving. MetaDrive itself does not impose this: `horizon` is 100000 above and
            # `ScenarioEnv`'s `allowed_more_steps` defaults to None.
            #
            # A human is bounded by neither. He may sit at the kerb before pulling away, take a
            # wrong turn and come back, or drive the route twice, and none of that is a fault to
            # be cut off after a fixed number of steps. So `manual` has no budget at all -
            # `None` - and the loop ends only when the episode terminates or the window closes.
            # A hosted model is in exactly the same position, and for the same reason: it is
            # not following the tape, so the tape's length is not a bound on how long its drive
            # legitimately takes.
            # Relative, not absolute, and 1 ppm rather than 1e-9. `data_dt` is read off the
            # recorded timestamps, and MetaDrive writes those as **float32** in a scenario it
            # exported itself (`metadrive/scenario/utils.py:154`), so a 10 Hz drive comes back
            # as 0.10000000149 -- 1.5e-9 out, and refused by an absolute 1e-9 with a message
            # reading "10 Hz against 10 Hz". Measured on a --export-drive of junction-1. The
            # differences this exists to catch are 10x apart, so 1 ppm gives up nothing: at
            # 0.1 s that is 1e-7, five orders below the smallest real mismatch.
            mismatch = abs(sim_dt - data_dt) > 1e-6 * max(sim_dt, data_dt)
            if mismatch:
                refusal = _refuse_mismatch(
                    scenario,
                    policy=arguments.agent_policy,
                    lights=arguments.lights,
                    sim_dt=sim_dt,
                    data_dt=data_dt,
                )
                if refusal:
                    print(refusal)
                    return 1

            if arguments.agent_policy in ("manual", "remote"):
                budget, budget_parts = None, None
                # Not a detail: `ScenarioEnv` puts the ego on the tape's first position *with
                # the speed recorded there*, so the drive starts mid-traffic rather than at a
                # standstill, and a driver who is still reaching for the keys is already
                # moving. `p` pauses if that is not wanted.
                print(
                    f"             ego starts at {float(env.agent.speed) * 3.6:.0f} km/h - the "
                    "recorded speed, not a standstill; `p` pauses"
                )
            else:
                # Steps of *this run*, not frames of the recording. The recording covers
                # `length * data_dt` seconds; how many `env.step`s that is depends on the sim
                # clock, and the red the car may have to sit out is seconds of its own. Every
                # term goes in as seconds and `_step_budget` does the one conversion; see its
                # docstring for why that is not a tidy-up.
                #
                # The drive term is the longer of the two drives, never the shorter: the
                # recording is the right bound for a replayed car, and a paced IDM one takes
                # as long as its own speed profile says it does. Measured on `junction-1`
                # before that term existed: the ego drove the whole route and reached
                # `arrive_dest` in 1044 steps, was cut off at the recording's 960, and was
                # reported as "did not arrive" at 85% of the route.
                budget, budget_parts = _step_budget(
                    recorded_s=length * data_dt,
                    pace_s=None if pace is None else pace.duration_s,
                    red_s=(
                        0.0 if arguments.agent_policy == "replay" else _longest_red(scenario)
                    ),
                    extra_s=max(arguments.extra_seconds, 0.0),
                    traffic=arguments.traffic == "live",
                    sim_dt=sim_dt,
                )

            # A baked stop is computed against the plan's written offsets, so it is right
            # under `--lights tape` and wrong under `--lights live`, which draws a fresh
            # offset every episode. The recorded car will stand still at a green, or drive
            # through a red, and neither is a fault in the data - it is the wrong pairing.
            stops = _baked_stops(scenario)
            if stops and arguments.lights == "live" and arguments.agent_policy == "replay":
                print(
                    f"             note: this track has {len(stops)} baked stop(s), written "
                    "against the plan's own offsets. --lights live redraws the offset, so the "
                    "car will wait at the wrong moment. Use --lights tape, or "
                    "--agent-policy idm."
                )
            # Transitions rather than a colour per step: 651 colours is not a report, and the
            # step a light turns green is the number that answers both questions here - did
            # the ego wait for it, and does that step move between episodes.
            changes = {}
            previous = {}
            heights = []
            speeds = []
            path = []
            info = {}
            steps = 0
            action = [0, 0]
            # Off under 3D, where the window is the heartbeat. Everything else here is
            # headless -- offscreen, none and the top-down renders write to a file or to
            # nothing -- and headless is where a running drive and a hung one look the same.
            # A negative interval is off as well, rather than every-step: `interval >= -5` is
            # true on every tick, and a line a millisecond is not a heartbeat.
            progress_every = arguments.progress_seconds
            if arguments.render == "3D" or progress_every <= 0:
                progress_every = 0.0
            started_wall = last_report = time.perf_counter()
            last_steps = 0
            last_policy = (0.0, 0)
            while budget is None or steps < budget:
                # Read here, at the top, and never anywhere inside the step. MetaDrive appends
                # a frame in `after_step`, and `convert_recorded_scenario_exported` asserts
                # `frames[-1].episode_step == episode_len - 1` (`scenario/utils.py:143`) -- so
                # an episode dumped from part-way through a step fails an assert rather than
                # exporting short. Between two steps the last frame is complete, which is the
                # only place a partial recording is a valid one.
                if closing.asked:
                    break
                previous_observation = observation
                # `[0, 0]` for the three policies that ignore it - `replay` and `idm` are
                # chosen through `agent_policy` and never read the argument, and `manual` reads
                # the keyboard instead. Only `remote` puts anything here, which is exactly what
                # makes it the same socket as the keyboard rather than a fourth mechanism.
                #
                # On a step between two decisions the previous action is **held**, never reset
                # to `[0, 0]`: zeroing it would take the foot off the throttle four steps in
                # five and the car would coast rather than drive at a lower decision rate.
                deciding = decides_on(steps, stride)
                if gate is not None:
                    gate.before_step(deciding)
                if deciding and model is not None:
                    # Observe then predict, in that order, so the frame being predicted on is
                    # the newest in the ring rather than one decision stale - `av3_base`'s own
                    # ordering. Both are inside the `deciding` branch, so a held step neither
                    # reads a camera nor spends a forward pass.
                    model.observe(rig.read(), env.agent)
                    prediction = model.predict(env.agent)
                    remote.extra = {
                        # Sent even under `--waypoints modelv2`: `server.py:_handle_step`
                        # reads `waypoints` FIRST and returns a hard stop on an empty list,
                        # before it looks at `modelv2` at all.
                        "waypoints": model.waypoints(prediction),
                    }
                    if arguments.waypoints == "modelv2":
                        remote.extra["modelv2"] = model.modelv2_rows(prediction)
                if deciding and remote is not None:
                    action = remote(observation)
                if pace is not None:
                    pace.before_step(env)
                observation, _, terminated, truncated, info = env.step(action)
                if ros_bag is not None and (
                    arguments.ros_bag_past_tape or steps < ros_tape_steps
                ):
                    # After `env.step` and before anything else consumes the env, beside
                    # `recorder.record` - the slot this file already reserves for one consumer
                    # per frame. `steps` is the loop's own counter, so the sim time is the
                    # frame's own and not `engine.episode_step`, which is one ahead.
                    ros_bag.write(
                        ros_frame.read(env, steps, steps * sim_dt, ros_projection)
                    )
                    ros_stopped_at = None
                elif ros_bag is not None and ros_stopped_at is None:
                    ros_stopped_at = steps
                if recorder is not None:
                    # The observation that *produced* the action, paired with the action the
                    # car executed for it - so the one from before the step, not the one the
                    # step returned. An imitation learner is fitting the first to the second.
                    recorder.record(previous_observation, env.agent)
                heights.append(float(env.agent.origin.getZ()))
                speeds.append(float(env.agent.speed))
                if lights is not None:
                    # `engine.episode_step`, not the loop counter: the engine increments
                    # inside `env.step`, so the two differ by one and the whole point of this
                    # report is that the step number matches the plan's arithmetic.
                    now = env.engine.episode_step
                    for light in lights.spawned_objects.values():
                        was = previous.get(light.id)
                        if light.status != was:
                            if was is not None:
                                changes.setdefault(light.id, []).append((now, light.status))
                            previous[light.id] = light.status
                # Every 0.1 s is plenty: the windows overlap heavily at that spacing. Counted
                # from the sim clock, so the trace stays the same shape whatever the rate.
                if steps % path_every == 0:
                    path.append(tuple(env.agent.position))
                steps += 1
                if progress_every:
                    wall = time.perf_counter()
                    interval = wall - last_report
                    if interval >= progress_every:
                        # Both rates are measured across the interval that just ended rather
                        # than across the run, so a drive that slows down says so on the next
                        # line instead of having it averaged into the minutes that went well.
                        stepped = steps - last_steps
                        policy_ms = None
                        if remote is not None:
                            calls = remote.calls - last_policy[1]
                            if calls:
                                policy_ms = 1000 * (remote.seconds - last_policy[0]) / calls
                            last_policy = (remote.seconds, remote.calls)
                        print(
                            _progress_line(
                                steps,
                                budget,
                                steps * sim_dt,
                                float(info.get("route_completion", float("nan"))),
                                float(env.agent.speed),
                                1000 * interval / stepped,
                                policy_ms,
                                wall - started_wall,
                            ),
                            # Not decoration. `scripts/sim.sh` adds `-T` when stdin is not a
                            # tty, and python block-buffers a pipe -- so a heartbeat written
                            # for a terminal would sit unflushed for hours in exactly the case
                            # it is wanted, a long run being logged. Same reason
                            # `tools/pedal_sweep.py` flushes its own progress.
                            flush=True,
                        )
                        last_report = wall
                        last_steps = steps
                if arguments.render == "3D":
                    overlay = {"scenario": scenario_id}
                    if arguments.agent_policy == "manual":
                        # The action the car is *executing*, on screen, because the one thing
                        # that goes wrong here is invisible: panda3d reads the keyboard through
                        # the window's own focus, so a window that does not have it leaves the
                        # policy returning [0, 0] with nothing to say so. The ego also spawns at
                        # the recorded speed rather than at rest, so a car nobody is steering
                        # drives off on its own and looks exactly like a car being steered
                        # badly. These two numbers separate the cases: press W and watch
                        # `throttle` move; if it stays at 0 the window is not getting the keys.
                        #
                        # `controller` is a different question and answers the other half:
                        # `action_info["manual_control"]` (`manual_control_policy.py:87`) is the
                        # policy's own flag for whether it consulted the keyboard *at all* this
                        # step, which it does not in top-down view or when the camera is not
                        # tracking this car. False there, and `q` is what fixes it.
                        steering, throttle = (float(v) for v in env.agent.current_action)
                        consulted = env.engine.get_policy(env.agent.id).action_info.get(
                            "manual_control"
                        )
                        overlay.update(
                            {
                                "steering (A/D)": f"{steering:+.2f}",
                                "throttle (W/S)": f"{throttle:+.2f}",
                                "speed": f"{float(env.agent.speed) * 3.6:.0f} km/h",
                                "controller": "read" if consulted else "IGNORED - press q",
                            }
                        )
                    env.render(text=overlay)
                elif arguments.render in ("2D", "semantic"):
                    env.render(
                        mode="top_down",
                        film_size=(3000, 3000),
                        semantic_map=arguments.render == "semantic",
                        # `sim.py` passes `target_vehicle_heading_up`, which 0.4.3 deprecates
                        # in favour of this name.
                        target_agent_heading_up=False,
                    )
                if terminated or truncated:
                    break

            # The car rides on a flat collision plane, so its height should stay at ride
            # height for the whole drive. A z far from there is the terrain and the physics
            # disagreeing, which is the failure this script exists to make visible.
            print(
                "scenario {:<3} {}: {} of {} steps ({} recorded frames at {:g} s), "
                "arrive_dest={}, completion {:.3f}, vehicle z {:.3f}..{:.3f} m".format(
                    index,
                    scenario_id,
                    steps,
                    "?" if budget is None else budget,
                    length,
                    data_dt,
                    bool(info.get("arrive_dest", False)),
                    float(info.get("route_completion", float("nan"))),
                    min(heights) if heights else float("nan"),
                    max(heights) if heights else float("nan"),
                )
            )
            outlived = _tape_ran_out(
                scenario,
                steps=steps,
                recorded_steps=int(round(length * data_dt / sim_dt)),
                length=length,
                lights=arguments.lights,
            )
            if outlived:
                print("             " + outlived)
            if not info.get("arrive_dest", False):
                # `arrive_dest=False` on its own does not say whether the drive was wrong or
                # merely different. `out_of_road` under `--agent-policy idm`, for instance, is
                # the lateral controller losing the reference line, which says nothing about
                # the data. Naming the reason is the difference between the two.
                named = ("out_of_road", "crash", "crash_object", "crash_vehicle", "max_step")
                reasons = [name for name in named if info.get(name)]
                if closing.asked:
                    # Ahead of every other reason, and ahead of `reasons` below, because it is
                    # the one the operator already knows and none of the others explains. A
                    # drive stopped by hand did not run out of steps and did not leave the road.
                    ran_out = "stopped early at your request"
                    reasons = []
                elif budget_parts is not None and steps >= budget:
                    ran_out = _budget_reason(budget, budget_parts)
                else:
                    # There is no budget at all under `manual` and `remote`, and under the
                    # others the loop can be left before the budget is spent - so if the steps
                    # did not run out, MetaDrive ended the episode for a reason this script
                    # does not name rather than the drive being cut off.
                    ran_out = "the episode ended without arriving"
                print(
                    "             did not arrive: {}{}".format(
                        ", ".join(reasons) or ran_out,
                        (
                            # `{:g}` so an untouched limit still prints as `4` rather than `4.0`:
                            # the value now arrives as a float from `--max-lateral-dist`, and a
                            # flag whose default changes nothing should change nothing here either.
                            "; lateral {:.2f} m against a {:g} m limit".format(
                                info["lateral_dist"], env.config["max_lateral_dist"]
                            )
                            if info.get("out_of_road") and "lateral_dist" in info
                            else ""
                        ),
                    )
                )
                # Under `manual` the driver is the variable, so not arriving says nothing about
                # the dataset - a wrong turn or a kerb is the human's, and reporting `FAILED`
                # for it would make the exit status mean something different in this mode than
                # in the other two. Printed either way; only counted for a policy that drives
                # the route the same way every time. `remote` is the same case with a model in
                # the driving seat: the exit status must keep meaning "the dataset is drivable"
                # rather than "the model drove it".
                # Not counted when the drive was stopped by hand, for the same reason the two
                # policies below are exempt: the exit status means *the dataset is drivable*,
                # and a run the operator cut short says nothing either way about that.
                if not closing.asked and arguments.agent_policy not in ("manual", "remote"):
                    failures += 1

            if pace is not None:
                # The two numbers that say whether the ego was ever asked to drive its
                # own corners: without this it is handed a flat 40 km/h everywhere, which
                # is faster than most of a junction map allows.
                print(
                    f"             ego paced to its route: cruise "
                    f"{pace.cruise_mps * 3.6:.1f} km/h, slowest corner asked for "
                    f"{pace.slowest_kph:.1f} km/h"
                )
            cars = getattr(env.engine, "live_traffic_manager", None)
            if cars is not None:
                print(
                    f"             traffic: {len(cars.spawned_objects)} car(s) on the road, "
                    f"{cars.cars_spawned} spawned and {cars.cars_retired} retired over the "
                    f"episode, {cars.collisions} collision(s), episode "
                    f"{cars.episode_index} of seed {arguments.traffic_seed}, "
                    f"give way {arguments.traffic_give_way}, "
                    f"speed {arguments.traffic_speed}"
                )
                if cars.cars_lost:
                    # Reported apart from the arrivals, because it is a fault rather than a
                    # completed route: nothing steers a traffic car by the road, so one
                    # carried wide of its line is taken off the map rather than left to drive
                    # across whatever is there.
                    print(
                        f"             {cars.cars_lost} car(s) strayed more than "
                        f"{TRAFFIC_LOST_LATERAL_M:.0f} m off their own route and were taken "
                        "off the map; each was replaced at a route start"
                    )
                if cars.on_road_low < cars.car_count:
                    # A road that empties is the fault baked traffic has and live traffic is
                    # meant not to have, so it is said rather than left in the numbers.
                    print(
                        f"             the road fell to {cars.on_road_low} car(s) at its "
                        f"emptiest, against the {cars.car_count} asked for: the pool has no "
                        "free start to release a replacement onto"
                    )

            if lights is not None and lights.spawned_objects:
                offset = getattr(lights, "episode_offset_seconds", None)
                print(
                    "             {} light(s){}".format(
                        len(lights.spawned_objects),
                        ""
                        if offset is None
                        else f", phase offset {offset:.1f} s drawn for this episode",
                    )
                )
                for light_id, transitions in sorted(changes.items()):
                    greens = [step for step, status in transitions if status.endswith("GREEN")]
                    print(
                        "             {} turns green at step(s) {}".format(
                            light_id,
                            ", ".join(str(step) for step in greens[:6]) or "never in this run",
                        )
                    )
                # Only meaningful under `--agent-policy idm`: a replayed ego is placed on its
                # recorded positions, so its speed is the recording's and no light can change
                # it. Printed either way, because that is the fact worth seeing.
                stopped = sum(1 for speed in speeds if speed < 0.2)
                print(
                    "             ego was below 0.2 m/s for {} of {} steps (min {:.2f} m/s)".format(
                        stopped, len(speeds), min(speeds) if speeds else float("nan")
                    )
                )
            elif arguments.lights == "live":
                print("             no lights: this dataset was converted without --signals")

            beside = _ground_around(env.engine, path)
            if beside is not None:
                highest, share = beside
                print(
                    f"             ground within 25 m of the drive reaches {highest:+.1f} m; "
                    f"{share:.0%} of it stands above the road"
                )

            # Converted here, while the engine is alive, rather than after the `finally` that
            # closes it: `dump_episode` reads the record manager off the engine. The conversion
            # is generic where `replay_episode` is not -- it reads `map_data["map_features"]`
            # only (`metadrive/scenario/utils.py:131`), which every map has, while
            # `ReplayManager.reset` reads `map_config` and `block_sequence` and spawns a
            # `PGMap`, neither of which a ScenarioNet map carries. That is why this writes a
            # dataset instead of a replay file.
            if arguments.export_drive and steps == 0:
                # Ctrl-C during the terrain build or the settling loop. The engine holds only
                # the reset frame, and a one-frame scenario is not a drive: `SD.sanity_check`
                # inside `extract_dataset_summary_and_mapping` is the wrong place to find that
                # out, and an export written from it would be watchable and empty.
                print("             nothing to export: the drive was stopped before its first step")
            elif arguments.export_drive:
                import numpy as np
                from metadrive.scenario.utils import convert_recorded_scenario_exported

                episode = env.engine.dump_episode()
                recorded = convert_recorded_scenario_exported(episode)
                # **The timestamps MetaDrive writes are wrong at any rate but 10 Hz, and the
                # error is visible rather than academic.** `convert_recorded_scenario_exported`
                # refuses any `scenario_log_interval` but 0.1 (`utils.py:135`) and stamps the
                # array `0.1 * i` regardless, so a 100 Hz drive claims to be a 10 Hz one.
                #
                # Played back, `ReplayTrafficParticipantPolicy.act` sets position, heading **and
                # velocity** from the tape each step (`replay_policy.py:62-65`). The velocity is
                # the real 12.6 m/s, and a simulator that believes the file then advances 0.1 s
                # of physics -- so the body coasts 1.26 m forward and the next frame teleports it
                # back to a position 0.126 m along. The car spikes back and forth, once a frame,
                # over a drive whose recorded line is smooth. Keith watched exactly that.
                #
                # Corrected here rather than asked for, MetaDrive having refused. `sim_dt` is
                # this run's own `env.step` interval, and one recorded frame is one `env.step`.
                # Fixing it is what makes `data_step_seconds` (:256) read the truth, which is
                # what makes `_refuse_mismatch` below refuse a wrong-rate replay instead of
                # drawing a car that never drove that way.
                stamps = recorded["metadata"]["ts"]
                recorded["metadata"]["ts"] = np.asarray(
                    [sim_dt * index for index in range(len(stamps))], dtype=np.float32
                )
                exported.append(recorded)

            # After the export, not before it: the scenario that was interrupted is still a
            # drive and still worth writing. Only the ones after it are abandoned.
            if closing.asked:
                if index != indices[-1]:
                    print(
                        f"             stopped early: {indices.index(index) + 1} of "
                        f"{len(indices)} scenario(s) driven"
                    )
                break
    finally:
        # `arm_exit` here and `restore` last, rather than restoring first. The original reason
        # to restore first was that a flag-setting handler left installed would swallow a
        # Ctrl-C during `env.close()`; arming the *exit* handler answers that without handing
        # SIGINT back to Python -- and `env.close()` is the worst place in the run to raise.
        # It unwinds panda3d's GL context and bullet's physics world, and a KeyboardInterrupt
        # landing in `bullet_world.remove(node)` there is what segfaulted on 2026-08-28 and
        # left the driver freeing the VA space of a process that had died mid-ioctl.
        closing.arm_exit()
        try:
            if ros_bag is not None:
                # Before `env.close()`, deliberately. Closing a bag writes its index and its
                # metadata; doing that after the GL teardown would put it downstream of the one
                # call in this file most likely to die badly.
                ros_bag.__exit__(None, None, None)
            if model is not None:
                model.close()
            env.close()
        finally:
            closing.restore()

    if model is not None:
        print(
            f"model        {model.n_waypoints} waypoints supplied per decision, sent as "
            f"{arguments.waypoints}"
        )

    if remote is not None and remote.calls:
        # Reported against `env.step`'s own 0.954 ms rather than alone, because the number only
        # means something beside the work it is added to. A figure near 40 ms is not a slow
        # model: it is Nagle's algorithm meeting delayed ACK because one end of the socket did
        # not set TCP_NODELAY, and it costs 43x the simulator per step.
        print(
            f"policy       {remote.calls} calls to {arguments.policy_url}, "
            f"{1000 * remote.seconds / remote.calls:.3f} ms each on average "
            f"(env.step itself is about 1 ms), {remote.seconds:.2f} s total"
        )
        # Printed because the one surprise here is invisible otherwise. Under --render
        # offscreen MetaDrive turns the observation itself into a stack of camera frames
        # (image_observation is what keeps a camera alive at all, base_env.py:343), so the
        # drive sends 3.6 MB a step whether or not any --sensors were asked for, and the round
        # trip goes from 0.9 ms to about 28. --render none and --render 3D both leave the
        # observation as the 161-float vector.
        print(
            f"             {remote.sent_bytes / remote.calls / 1024:.1f} KB sent per step"
            + (
                " - most of it the observation itself, which --render offscreen turns into a "
                "stack of camera frames whether or not any --sensors were asked for. --render "
                "none and --render 3D both leave it at 161 floats."
                if arguments.render == "offscreen"
                else ""
            )
        )
        remote.close()

    if ros_bag is not None:
        counts = ros_bag.summary()["messages_per_topic"]
        total = sum(counts.values())
        print(
            f"ros bag      {ros_bag.frames} frames, {total} messages across "
            f"{len(counts)} topics -> {arguments.ros_bag}"
        )
        if ros_stopped_at is not None:
            print(
                f"             stopped at step {ros_stopped_at} of {steps}: the recording ends "
                f"at {ros_tape_steps} and past it MetaDrive drops every replayed pedestrian "
                "and cyclist, so those frames show an emptier junction than the scenario has. "
                "--ros-bag-past-tape keeps them."
            )
        print(
            "             read it back with: uv run python tools/ros_audit.py "
            f"{arguments.ros_bag}"
        )

    if recorder is not None:
        # Said rather than left to be discovered: a replayed car's policy returns None every
        # step, so nothing is ever written to `current_action` and the file comes out as a
        # column of zeros - 352 of them on `junction-1`, measured. It is still saved, because a
        # recording of the observations is a real thing to want; it is simply not a drive.
        empty = recorder.all_zero()
        written = recorder.save(arguments.record)
        if written is None:
            print("recorded     nothing: the drive took no steps")
        else:
            observations = written["observations"]
            images = written["images"]
            size_kb = os.path.getsize(arguments.record) / 1024
            size = f"{size_kb / 1024:.1f} MB" if size_kb >= 1024 else f"{size_kb:.0f} KB"
            print(
                f"recorded     {observations[0]} steps, observations {observations}, "
                f"actions {written['actions']} -> {arguments.record} ({size})"
                + (
                    # Said with the scale beside it, because a uint8 array of pixels is not
                    # what the car was handed - it was handed float32 in [0, 1], and this is
                    # the number that gets it back. See `ActionRecorder`.
                    f"\n             images {images} uint8, divide by "
                    f"{written['image_scale']:g} for the float the car saw"
                    if images
                    else ""
                )
                + (
                    "\n             every action is [0, 0]: --agent-policy replay sets the "
                    "car's position directly and never acts, so there is nothing here to "
                    "imitate. Use manual or idm."
                    if empty
                    else ""
                )
            )

    if exported:
        # MetaDrive's `extract_dataset_summary_and_mapping` builds the summary and the mapping
        # and runs `SD.sanity_check` on each scenario (`utils.py:456`), so a drive that
        # converted into something undrivable is refused here rather than found later in a 3D
        # window. Its sibling `save_dataset` is not used, and the reason is the whole point of
        # the flag: it writes with a plain `pickle.dump`, and this process is the container's
        # numpy 2.2 while the machine that will *watch* the drive opens it on MetaDrive's own
        # 3.8 and numpy 1.24. A numpy-2 pickle fails to open there with
        # `ModuleNotFoundError: No module named 'numpy._core'`. `portable_pickle` is the
        # answer this repo already gives for its converted datasets.
        import portable_pickle
        from metadrive.scenario.scenario_description import ScenarioDescription
        from metadrive.scenario.utils import (
            dict_recursive_remove_array_and_set,
            extract_dataset_summary_and_mapping,
        )

        summary, mapping, scenarios = extract_dataset_summary_and_mapping(
            exported, "wingfin", "drive"
        )
        # Here, after the sanity check and immediately before the write, rather than at the
        # precheck: a drive that fails on its way to a dataset must leave the previous export
        # standing. Between this line and the last `dump` below the directory is incomplete,
        # and that window is milliseconds against a drive of minutes.
        replaced = _clear_export(arguments.export_drive)
        size_kb = 0
        for name, scenario in scenarios.items():
            size_kb += portable_pickle.dump(
                scenario, os.path.join(arguments.export_drive, name)
            ) / 1024
        size_kb += portable_pickle.dump(
            dict_recursive_remove_array_and_set(summary),
            os.path.join(arguments.export_drive, ScenarioDescription.DATASET.SUMMARY_FILE),
        ) / 1024
        size_kb += portable_pickle.dump(
            mapping,
            os.path.join(arguments.export_drive, ScenarioDescription.DATASET.MAPPING_FILE),
        ) / 1024
        frames = sum(int(scenario.get("length", 0)) for scenario in exported)
        size = f"{size_kb / 1024:.1f} MB" if size_kb >= 1024 else f"{size_kb:.0f} KB"
        print(
            f"exported     {len(exported)} scenario(s), {frames} frames -> "
            f"{arguments.export_drive} ({size})"
            + (f", replacing {replaced} file(s)" if replaced else "")
        )
        print(
            "             watch it with: scripts/watch-drive.sh "
            + os.path.relpath(arguments.export_drive)
        )

    print("result       {}".format("FAILED" if failures else "OK"))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
