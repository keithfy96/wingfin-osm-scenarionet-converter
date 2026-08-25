"""Run the AV3 model beside a recorded drive and check every conversion, while nothing steers.

    ./scripts/av3-probe.sh junction-1 -- --step-hz 100 --decision-hz 20

Stage 9 Phase C.3's stop point, and the most valuable thing in it. `tools/av3_model.py` writes
five conversions into the model - pixels, camera order, frame history, ego speed, route - and
**not one of them raises when it is wrong**. A mirrored route or a swapped camera pair
produces a model that runs, returns twenty plausible waypoints, and drives into the oncoming
carriageway. So they are checked here first, on a car being replayed from the tape, where the
answer is known.

It reports four things, in the order of what they actually prove:

1. **the camera map** - which rig camera fills which of the model's six slots, each one's
   resolved aim in words, and any rig camera nothing reads. Conversion 2.
2. **the ego state** beside the raw speed it was built from. Conversion 4.
3. **the navigation block** beside `policy_client.SensorPack`'s own `route` sensor. Both
   project the same `PointLane` onto the same car by different code, and the model's block is
   mirrored where the sensor's is not - so agreement after un-mirroring is a direct test of
   conversion 5, and the two disagreeing is a sign error rather than a rounding one.
4. **the predicted waypoints against where the recorded car actually went.** On a replay the
   future is not a guess: the ego's positions for the whole drive are recorded as it goes, and
   each prediction is scored against the position the car really reached at that horizon.
   Along-track and cross-track error, per horizon step.

**Item 4 is scored under both sign conventions on purpose.** Conversion 6 - that the model's
output is already y-RIGHT and needs no flip - is the one conversion this repo cannot read off
a source file, because it is a property of the weights. So the cross-track error is reported
for `y` as given and for `-y`, and whichever is smaller is the answer. Printing one number and
calling it right would be assuming exactly the thing that most needs checking.

**Nothing here steers.** The policy is `ReplayEgoCarPolicy`, so the drive is the tape whatever
the model says. That is the point: a bad driver and a wrong conversion look identical once the
model is in the loop, and this separates them beforehand.

**A pass costs about a second** (Phase C.1: 947-1002 ms), so `--decisions` bounds how many are
run and defaults to a number that finishes in under a minute. `--no-model` skips the checkpoint
entirely and still checks conversions 2, 4 and 5 - the three that need no forward pass - in a
few seconds.
"""

from __future__ import annotations

import argparse
import math
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DEFAULT_RIG = os.path.join(REPO, "rigs", "av3.txt")
DEFAULT_DECISIONS = 40


def _line(label, text):
    print(f"{label:<13}{text}")


def _ego_frame(here, heading, world):
    """A world point in the car's own frame: `(ahead, left)`, metres."""
    east = float(world[0]) - float(here[0])
    north = float(world[1]) - float(here[1])
    cos_heading, sin_heading = math.cos(heading), math.sin(heading)
    return (
        east * cos_heading + north * sin_heading,
        -east * sin_heading + north * cos_heading,
    )


def _median(values):
    return statistics.median(values) if values else float("nan")


def _synthetic_route(n_route, spacing_m, radius_m):
    """A navigation block for an arc of `radius_m`, in the model's own (fwd, RIGHT) frame.

    Positive radius bends RIGHT. Built with the same normalisation `av3_model.navigation`
    applies - `[fwd/H, right/H, cos t, sin t, curv*H, s_norm, valid]` with
    `H = n_route * spacing_m` - so the only thing that differs from a real block is the shape.
    """
    import numpy

    horizon = max(1e-6, n_route * spacing_m)
    curvature = 1.0 / float(radius_m)
    rows = []
    for index in range(n_route):
        along = index * spacing_m
        theta = curvature * along
        # Arc from the origin, tangent to +x at the start; +theta swings toward +y (right).
        forward = math.sin(theta) / curvature
        right = (1.0 - math.cos(theta)) / curvature
        rows.append(
            [
                forward / horizon,
                right / horizon,
                math.cos(theta),
                math.sin(theta),
                curvature * horizon,
                index / max(1, n_route - 1),
                1.0,
            ]
        )
    return numpy.asarray(rows, dtype=numpy.float32)


def _distance_to_path(point, path):
    """Shortest distance from a point to a polyline, both in the same plane.

    **Not** the distance to the path point at the same time. The two are different questions
    and only this one is about the path's SHAPE: a model that predicts the right line at the
    wrong speed is metres away from where the car will be in two seconds and zero metres away
    from the line it will drive. Conversion 6 is a question about shape, so it is asked this
    way; the speed intent is reported separately, in the along-track columns.
    """
    px, py = point
    best = float("inf")
    for index in range(len(path) - 1):
        ax, ay = path[index]
        bx, by = path[index + 1]
        dx, dy = bx - ax, by - ay
        span = dx * dx + dy * dy
        if span < 1e-12:
            along = 0.0
        else:
            along = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / span))
        best = min(best, math.hypot(px - (ax + along * dx), py - (ay + along * dy)))
    return best


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check every AV3 input conversion against a recorded drive.",
    )
    parser.add_argument("dataset", help="Directory holding dataset_summary.pkl")
    parser.add_argument("--rig", default=DEFAULT_RIG, help="Camera spec (default rigs/av3.txt)")
    parser.add_argument("--checkpoint", default=None, help="The .ep to load")
    parser.add_argument("--model-config", default=None, help="model_dev.yml")
    parser.add_argument("--step-hz", type=float, default=None)
    parser.add_argument("--decision-hz", type=float, default=None)
    parser.add_argument("--scenario-index", type=int, default=0)
    parser.add_argument(
        "--decisions",
        type=int,
        default=DEFAULT_DECISIONS,
        help=f"How many forward passes to run (default {DEFAULT_DECISIONS}; 0 for the whole "
        "drive, which is about a second a decision)",
    )
    parser.add_argument(
        "--no-model",
        action="store_true",
        help="Skip the checkpoint. Conversions 2, 4 and 5 are still checked, in seconds.",
    )
    parser.add_argument(
        "--nav-sweep",
        type=float,
        default=30.0,
        help="Radius in metres of the synthetic arc fed to the navigation-response test; "
        "0 turns the test off (default 30)",
    )
    arguments = parser.parse_args()
    # A run of this is minutes - a terrain build, then about a second a forward pass - and
    # python block-buffers stdout the moment it is redirected to a file. Without this a log
    # stays empty until the process exits, which is indistinguishable from a hang.
    sys.stdout.reconfigure(line_buffering=True)

    import av3_model
    from agent_env import make_env
    from camera_rig import RigError, load_rig
    from drive import (
        DEFAULT_STEP_HZ,
        data_step_seconds,
        decides_on,
        decision_stride,
        sim_step_seconds,
        step_config,
    )
    from frame_gate import install as install_frame_gate
    from metadrive.policy.replay_policy import ReplayEgoCarPolicy
    from policy_client import ROUTE_SPACING_M, SensorPack

    checkpoint = arguments.checkpoint or os.environ.get("MODEL_CHECKPOINT")
    if not arguments.no_model:
        from model_probe import DEFAULT_CHECKPOINT

        checkpoint = checkpoint or DEFAULT_CHECKPOINT
        if not os.path.exists(checkpoint):
            print(f"result       FAILED: no checkpoint at {checkpoint}")
            return 2
        try:
            import torch  # noqa: F401
        except ImportError:
            import env_hint

            print(
                "result       FAILED: torch is not installed in this interpreter "
                f"({sys.executable}).\n" + env_hint.install_hint(("sim", "gpu", "model"))
            )
            return 2

    try:
        config = av3_model.load_config(arguments.model_config or av3_model.DEFAULT_CONFIG)
    except av3_model.ModelError as error:
        print(f"result       FAILED: {error}")
        return 2

    effective_hz = arguments.step_hz if arguments.step_hz is not None else DEFAULT_STEP_HZ
    try:
        stride = decision_stride(effective_hz, arguments.decision_hz)
    except ValueError as error:
        print(f"result       FAILED: {error}")
        return 2
    decision_interval = stride / float(effective_hz)

    try:
        rig = load_rig(arguments.rig, read_interval_s=decision_interval)
    except RigError as error:
        print(f"rig spec rejected: {error}", file=sys.stderr)
        return 2

    _line("dataset", arguments.dataset)
    _line("model cfg", config.path)
    for index, line in enumerate(rig.describe()):
        print(("rig          " if index == 0 else "             ") + line)
    _line(
        "decision",
        f"{1.0 / decision_interval:g} Hz - every {stride} env.step at {effective_hz:g} Hz, "
        f"{decision_interval:g} s apart",
    )

    # ---- conversion 2, before an env even exists ------------------------------------
    known = set(rig.names)
    missing = [name for name in config.camera_order if name not in known]
    unread = [name for name in rig.names if name not in config.camera_order]
    print()
    print("camera map   model slot        rig camera        aims")
    aims = {camera.name: camera.aim for camera in rig.cameras}
    for index, name in enumerate(config.camera_order):
        print(
            "             [{}] {:<14}{:<18}{}".format(
                index, name, name if name in known else "-- MISSING --", aims.get(name, "")
            )
        )
    if unread:
        print(
            "             note: {} in the rig and read by nothing".format(", ".join(unread))
        )
    if missing:
        print(
            "result       FAILED: the rig has no {}. `camera_order` in model_dev.yml is a "
            "contract with the weights.".format(", ".join(missing))
        )
        return 1

    # Built here only to report the arithmetic before the engine exists - the model builds its
    # own from the same three numbers. Printed rather than inferred because a `frame_stride_s`
    # the read interval cannot divide never stops a run: the model sees history spaced
    # differently to how it was trained, and scores anyway.
    history = av3_model.FrameHistory(config.t_frames, config.frame_stride_s, decision_interval)
    _line(
        "history",
        f"{config.t_frames} frames {history.actual_stride_s:g} s apart "
        f"(stride {history.stride}, ring {history.depth} deep, "
        f"{history.depth * len(rig) * 3 * config.image_height * config.image_width / 1e6:.1f} "
        "MB of uint8)",
    )
    if history.spacing_note:
        _line("", "note: " + history.spacing_note)

    rate = {} if arguments.step_hz is None else step_config(arguments.step_hz)
    env = make_env(
        arguments.dataset,
        render="offscreen",
        agent_policy=ReplayEgoCarPolicy,
        sensors=rig.sensors(),
        vehicle_config=dict(image_source=rig.image_source()),
        **rate,
    )

    model = None
    try:
        env.reset(seed=arguments.scenario_index)
        rig.mount(env)
        gate = install_frame_gate(env)
        pack = SensorPack(env, ("route",))
        pack.reset()

        scenario = env.engine.data_manager.current_scenario
        length = env.engine.data_manager.current_scenario_length
        sim_dt = sim_step_seconds(env)
        # The two clocks, `drive.py`'s own: `sim_dt` is how far one env.step advances the
        # simulator, `data_step_seconds` how far one recorded frame covers. Equal only when
        # the dataset was written at the rate it is being driven at, which a replay requires
        # anyway - so this is the recording's length counted in this run's steps.
        budget = int(round(length * data_step_seconds(scenario) / sim_dt))

        if not arguments.no_model:
            print()
            _line("checkpoint", checkpoint)
            model = av3_model.AV3Model(config, checkpoint, decision_interval)
            model.load()
            _line(
                "loaded",
                f"{model.n_waypoints} waypoints x {model.output_width} in "
                f"{model.load_seconds:.1f} s; horizon {av3_model.MODEL_HORIZON_S:g} s, "
                f"so {av3_model.MODEL_HORIZON_S / model.n_waypoints:g} s spacing",
            )

        # Which decisions to run the model on, **spread over the whole drive** rather than
        # taken from the front. The first second of a `junction-1` route is a straight line at
        # a constant recorded speed, so a run of consecutive decisions there reports v_lat
        # exactly 0.000 and a route dead ahead - the two readings the mirror cannot be wrong
        # about. The conversions this exists to check only say anything through a corner.
        decisions = [step for step in range(budget) if decides_on(step, stride)]
        wanted = arguments.decisions if arguments.decisions > 0 else len(decisions)
        if wanted < len(decisions):
            spacing = len(decisions) / float(wanted)
            chosen = {decisions[int(index * spacing)] for index in range(wanted)}
        else:
            chosen = set(decisions)

        # Every step's pose, so a prediction made at step k can be scored against where the
        # car really was at k + horizon/sim_dt once the drive is over.
        track = []
        samples = []
        pass_seconds = []
        levels = None
        sweep = {}
        steps = 0
        while steps < budget:
            track.append((tuple(env.agent.position), float(env.agent.heading_theta)))
            deciding = decides_on(steps, stride)
            gate.before_step(deciding)
            # Fed on EVERY decision, not only the sampled ones: the ring is the model's
            # history, and filling it only where a prediction is wanted would hand the model
            # five frames from five different parts of the drive.
            if deciding:
                frames = rig.read()
                # Not the first decision: at step 0 the buffers hold whatever `env.reset`
                # left, and all six read alike. A few decisions in they are six views.
                if levels is None and steps >= stride * 4:
                    # One reading, so "the model is fed black" is visible rather than
                    # inferred from a bad prediction. A camera whose buffer never fills
                    # renders a uniform frame and the model still returns twenty waypoints.
                    levels = {
                        name: (float(frame.mean()), float(frame.std()))
                        for name, frame in frames.items()
                    }
                if model is not None:
                    model.observe(frames, env.agent)
            if deciding and steps in chosen:
                state = av3_model.ego_state(env.agent, config.ego_velocity_scale)
                route = av3_model.navigation(
                    env.agent, config.n_route, config.route_spacing_m, config.route_max_offset_m
                )
                sensed = pack()["route"]
                sample = {
                    "step": steps,
                    "speed": float(env.agent.speed),
                    "ego_state": state,
                    "navigation": route,
                    "sensor_points": sensed["points_m"],
                    "sensor_lateral": sensed["lateral_m"],
                    "prediction": None,
                }
                if model is not None:
                    started = time.perf_counter()
                    sample["prediction"] = model.predict(env.agent)
                    pass_seconds.append(time.perf_counter() - started)
                samples.append(sample)
            # `[0, 0]` is ignored: the policy is `ReplayEgoCarPolicy`, which writes the
            # car's position from the tape. Nothing the model says moves anything.
            _, _, terminated, truncated, _ = env.step([0.0, 0.0])
            steps += 1
            if terminated or truncated:
                break
        # The final pose, so the last decision's own step has an entry. Predictions whose
        # horizon runs past the end of the drive are skipped rather than extrapolated - there
        # is nothing there to be scored against, and inventing it would flatter the model.
        track.append((tuple(env.agent.position), float(env.agent.heading_theta)))

        # **Does the route reach the output at all, and with which sign?** Everything above
        # infers that from a drive, which cannot separate "the model ignores the route" from
        # "the route is mirrored" from "the road ahead really is straight". This asks the
        # model directly: same pictures, same ego state, and a navigation block replaced by a
        # synthetic arc of known curvature - once bending right, once left. A model reading
        # the route answers with a lateral of the matching sign; one that is not answers with
        # the same number twice.
        if model is not None and arguments.nav_sweep > 0:
            for label, sign in (("right", +1.0), ("left", -1.0)):
                block = _synthetic_route(
                    config.n_route, config.route_spacing_m, sign * arguments.nav_sweep
                )
                sweep[label] = model.predict_with_navigation(env.agent, block)
    finally:
        if model is not None:
            model.close()
        env.close()

    print()
    _line("drove", f"{steps} of {budget} steps, {len(samples)} decision(s) sampled")
    if levels:
        _line(
            "frames",
            "mean/sd pixel level, a few decisions in: "
            + ", ".join(f"{name} {mean:.1f}/{sd:.1f}" for name, (mean, sd) in levels.items()),
        )
        flat = [name for name, (_, sd) in levels.items() if sd < 1.0]
        if flat:
            _line(
                "",
                "FAIL  {} render a flat frame. The model is being shown a blank picture and "
                "will still return twenty waypoints.".format(", ".join(flat)),
            )
    if pass_seconds:
        _line(
            "forward pass",
            f"median {_median(pass_seconds) * 1000:.0f} ms over {len(pass_seconds)} - "
            f"{_median(pass_seconds) * len(track) / max(1, stride):.0f} s for this whole drive",
        )

    if not samples:
        print("result       FAILED: no decision was sampled")
        return 1

    # ---- conversion 4 ----------------------------------------------------------------
    print()
    print("ego state    the pair the model is fed, beside the speed it was built from")
    print("             step   speed m/s   v_fwd norm   v_lat norm   v_fwd m/s   v_lat m/s")
    s_lon, s_lat = config.ego_velocity_scale
    for sample in samples[:6]:
        forward = float(sample["ego_state"][0]) * s_lon
        lateral = float(sample["ego_state"][1]) * s_lat
        print(
            "             {:<6} {:>9.3f}   {:>10.4f}   {:>10.4f}   {:>9.3f}   {:>9.3f}".format(
                sample["step"], sample["speed"], sample["ego_state"][0],
                sample["ego_state"][1], forward, lateral,
            )
        )
    worst = max(
        abs(math.hypot(s["ego_state"][0] * s_lon, s["ego_state"][1] * s_lat) - s["speed"])
        for s in samples
    )
    ok_ego = worst < 0.05
    print(
        "             {}  |[v_fwd, v_lat]| against agent.speed differs by at most "
        "{:.4f} m/s".format("ok  " if ok_ego else "FAIL", worst)
    )
    print(
        "             v_lat is RIGHT-positive here and MetaDrive's own is LEFT-positive; "
        "a replayed car on a left-hand road should read negative through a left turn"
    )

    # ---- conversion 5 ----------------------------------------------------------------
    print()
    print("navigation   the model's block against policy_client's own `route` sensor")
    print("             the sensor is (ahead, LEFT) m; the model's is (fwd, RIGHT)/H, mirrored")
    horizon = config.n_route * config.route_spacing_m
    compared = 0
    ahead_gap = 0.0
    left_gap = 0.0
    worst_at = None
    # Index 0 is the car's own projection onto the route, so it is (0, 0) on a car that is on
    # its route - it agrees under every sign convention and proves nothing. The whole window
    # is compared instead, and the row printed is the far end of it, where a mirrored route
    # is metres out rather than millimetres.
    far = config.n_route - 1
    for sample in samples:
        if not sample["navigation"].any():
            continue
        for index in range(min(config.n_route, len(sample["sensor_points"]))):
            sensor_ahead, sensor_left = sample["sensor_points"][index]
            model_ahead = float(sample["navigation"][index, 0]) * horizon
            model_left = -float(sample["navigation"][index, 1]) * horizon
            compared += 1
            ahead_gap = max(ahead_gap, abs(sensor_ahead - model_ahead))
            if abs(sensor_left - model_left) > left_gap:
                left_gap = abs(sensor_left - model_left)
                worst_at = (sample["step"], index)
    print("             step   point   ahead sensor / model      left sensor / -model")
    for sample in samples[:8]:
        if not sample["navigation"].any():
            print(f"             {sample['step']:<6} off-route (all zeros)")
            continue
        sensor_ahead, sensor_left = sample["sensor_points"][far]
        print(
            "             {:<6} {:<7} {:>8.3f} / {:>8.3f}     {:>8.3f} / {:>8.3f}".format(
                sample["step"], far, sensor_ahead,
                float(sample["navigation"][far, 0]) * horizon, sensor_left,
                -float(sample["navigation"][far, 1]) * horizon,
            )
        )
    ok_route = compared > 0 and ahead_gap < 0.05 and left_gap < 0.05
    if compared:
        print(
            "             {}  over {} point(s): at most {:.4f} m ahead and {:.4f} m across "
            "(worst at step {}, point {}). Both project the same PointLane by different code "
            "and the model's is mirrored, so a metre here is a sign error, not rounding."
            "".format(
                "ok  " if ok_route else "FAIL", compared, ahead_gap, left_gap,
                worst_at[0] if worst_at else "-", worst_at[1] if worst_at else "-",
            )
        )
    else:
        ok_route = False
        print("             FAIL  every sample was off-route; nothing to compare")
    turning = [
        sample for sample in samples
        if sample["navigation"].any() and abs(float(sample["navigation"][far, 1])) > 0.02
    ]
    print(
        f"             {len(turning)} of {len(samples)} sampled decisions have the far end "
        f"of the route more than {0.02 * horizon:.1f} m to one side - the mirror says "
        "nothing on a straight road"
    )
    print(
        f"             the spacing matches too: the sensor steps {ROUTE_SPACING_M:g} m "
        f"and the model {config.route_spacing_m:g} m"
    )

    # ---- conversion 6 ----------------------------------------------------------------
    ok_waypoints = True
    if samples[0]["prediction"] is not None:
        times = av3_model.waypoint_times(len(samples[0]["prediction"]))
        horizon_steps = int(round(times[-1] / sim_dt))
        print()
        print("waypoints    predicted, against where the recorded car actually went")
        print("             ahead     how far the model thinks it gets - its SPEED intent")
        print("             across    its own lateral, y as given, beside the car's RIGHT-")
        print("                       positive displacement. Conversion 6 says these agree")
        print("                       in sign with nothing flipped.")
        print("             off-path  how far the predicted point lies from the line the car")
        print("                       really drove - a question about SHAPE, with the speed")
        print("                       deficit above taken out of it")
        print(
            "             horizon   ahead pred / actual   across pred / actual   "
            "off-path y / -y"
        )
        # Scored only where the car really goes somewhere sideways: on a straight road the
        # predicted path and its mirror are the same line, so including those samples averages
        # the answer toward "no difference" - a null result with a mechanism rather than
        # evidence either way.
        turn_m = 1.0
        # And a mirror is only VISIBLE where the model itself predicts a lateral. Below this
        # the two conventions differ by less than the measurement can resolve, and the honest
        # verdict is "inconclusive" rather than whichever number happened to be smaller.
        resolve_m = 0.25
        ahead_rows = {}
        across_rows = {}
        off_rows = {}
        agreeing = 0
        resolvable = 0
        for sample in samples:
            prediction = sample["prediction"]
            here, heading = track[sample["step"]]
            future = [
                _ego_frame(here, heading, track[step][0])
                for step in range(
                    sample["step"], min(len(track), sample["step"] + horizon_steps + 1)
                )
            ]
            if len(future) < 2:
                continue
            bends = max(abs(left) for _, left in future) >= turn_m
            for index, seconds in enumerate(times):
                step = sample["step"] + int(round(seconds / sim_dt))
                if step >= len(track):
                    continue
                actual_ahead, actual_left = _ego_frame(here, heading, track[step][0])
                x = float(prediction[index][0])
                y = float(prediction[index][1])
                ahead_rows.setdefault(index, []).append((x, actual_ahead))
                if not bends:
                    continue
                # The across columns are over the TURNING samples only. A median over every
                # sample is ~0 on both sides whatever the model does, because most of a
                # `junction-1` route is straight - which reads as "the car went nowhere
                # sideways" and hides the one comparison this column exists for.
                across_rows.setdefault(index, []).append((y, -actual_left))
                off_rows.setdefault(index, []).append(
                    (
                        # `y` read as RIGHT-positive, so it enters the LEFT-positive ego
                        # frame negated; and then read as LEFT-positive, unchanged.
                        _distance_to_path((x, -y), future),
                        _distance_to_path((x, y), future),
                    )
                )
                if abs(y) >= resolve_m and abs(actual_left) >= turn_m:
                    resolvable += 1
                    agreeing += (y > 0) == (-actual_left > 0)
        for index, seconds in enumerate(times):
            ahead = ahead_rows.get(index)
            if not ahead:
                continue
            across = across_rows.get(index, [])
            off = off_rows.get(index, [])
            if off:
                given_m = _median([e[0] for e in off])
                flipped_m = _median([e[1] for e in off])
                tail = f"{given_m:>8.3f} / {flipped_m:<8.3f}"
            else:
                tail = "   - (nothing turning)"
            ahead_pred = _median([e[0] for e in ahead])
            ahead_real = _median([e[1] for e in ahead])
            across_pred = _median([e[0] for e in across]) if across else float("nan")
            across_real = _median([e[1] for e in across]) if across else float("nan")
            print(
                f"             {seconds:>5.1f} s  {ahead_pred:>7.2f} / {ahead_real:<9.2f}  "
                f"{across_pred:>8.2f} / {across_real:<10.2f}  {tail}"
            )
        as_given = [value for errors in off_rows.values() for value, _ in errors]
        as_flipped = [value for errors in off_rows.values() for _, value in errors]
        given, flipped = _median(as_given), _median(as_flipped)
        predicted_across = _median(
            [abs(value) for values in across_rows.values() for value, _ in values]
        )
        if not as_given:
            ok_waypoints = False
            print(
                f"             FAIL  no sampled decision had the car more than {turn_m:.1f} m "
                "sideways over the horizon, so the sign of y is untested. Sample more "
                "decisions (--decisions 0), or a route with a junction in it."
            )
        elif resolvable < 8:
            # NOT a pass and NOT a failure. Said as its own outcome because the difference
            # between the two columns is then smaller than the thing being measured, and
            # reporting whichever is numerically smaller would be reading noise as a verdict.
            print(
                f"             INCONCLUSIVE  the model predicts a median |y| of "
                f"{predicted_across:.3f} m, and only {resolvable} turning point(s) had it "
                f"past {resolve_m:.2f} m - so the two columns ({given:.3f} vs "
                f"{flipped:.3f} m) differ by less than this can resolve. The model is "
                "predicting a near-straight line here; the sign of y is untested until it "
                "predicts a real curve."
            )
        else:
            share = agreeing / float(resolvable)
            drive_says = "as given" if share >= 0.5 and given <= flipped else "negated"
            print(
                f"             over {resolvable} point(s) where the model predicted more "
                f"than {resolve_m:.2f} m of lateral on a bend, its sign agreed with the "
                f"car's own on {share:.0%}; off-path {given:.3f} m as given against "
                f"{flipped:.3f} m negated, so this leans {drive_says}."
            )
            print(
                "             this is CONTEXT, not the verdict on conversion 6: a model with "
                "a constant lateral bias reads exactly like a mirrored one here, and the nav "
                "response below is the test that separates them"
            )
        if sweep:
            print()
            print(
                "nav response the same pictures and ego state, with the route replaced by a "
                f"{arguments.nav_sweep:g} m arc"
            )
            print("             bend    predicted lateral at 2.0 s (y, model frame)")
            for label, prediction in sweep.items():
                print(f"             {label:<7} {float(prediction[-1][1]):+8.3f} m")
            right_y = float(sweep["right"][-1][1])
            left_y = float(sweep["left"][-1][1])
            spread = abs(right_y - left_y)
            print(
                f"             the two straddle {(right_y + left_y) / 2.0:+.3f} m, which is "
                "the model's standing lateral bias on this map with the route's own bend "
                "taken out - a domain-gap reading, and the reason the drive statistic above "
                "cannot settle the sign on its own"
            )
            if spread < 0.5:
                print(
                    f"             FAIL  the two differ by {spread:.3f} m. The model answers "
                    "a hard right-hand bend and a hard left-hand one with the same number, "
                    "so the navigation input is not reaching the output. Every route sign "
                    "conclusion above is untestable until this moves."
                )
                ok_waypoints = False
            elif right_y > left_y:
                ok_waypoints = True
                print(
                    f"             ok    right-hand bend gives the larger y, by "
                    f"{spread:.3f} m - so the model's +y is RIGHT, the bridge's own "
                    "convention, and conversion 6 flips nothing. Every other input is held "
                    "fixed across the two, so this is the sign and nothing else."
                )
            else:
                print(
                    f"             FAIL  LEFT-hand bend gives the larger y, by "
                    f"{spread:.3f} m - so the model's +y is LEFT and av3_model.modelv2_rows "
                    "must negate it."
                )
                ok_waypoints = False
        print(
            f"             model_dev.yml asks for waypoint_reference "
            f"{config.waypoint_reference!r}, which nothing applies - wing-sim hardcodes "
            "reference_offset_m 0.0 too, so a small systematic off-path bias in corners is "
            "the anchor rather than the model"
        )

    failed = not (ok_ego and ok_route and ok_waypoints)
    print()
    print("result       " + ("FAILED" if failed else "every checked conversion agrees"))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
