"""The stage 7b loop, runnable, with MetaDrive's own IDM standing in for a model.

    <metadrive-checkout>/.venv/bin/python examples/drive_with_a_policy.py \\
        workspaces/junction-1/scenarionet

This is executable documentation rather than a harness. It is deliberately **not** a
policy-loading tool - an earlier draft had `--policy PKG:CALLABLE` and it inverted control for
nothing. The environment is the API and the loop belongs to whoever is training; what this file
shows is that the loop is four lines and that the only thing a model changes is one of them:

    policy = IdmDriver(env)      # <- replace this with your own callable

A policy is anything callable that takes the observation and returns `[steering, throttle]`,
both in [-1, 1]. Nothing registers it, nothing subclasses anything, and MetaDrive never learns
it exists: the numbers reach the car because `ScenarioEnv`'s default `agent_policy` is
`EnvInputPolicy`, which reads exactly what was passed to `env.step`.

`--record` writes `(observation, executed action)` pairs. The same recorder runs behind
`tools/drive.py --record`, so a file made by driving with the keyboard and a file made here
have the same shape - which is what makes human demonstrations usable as training data.
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "tools"))

from agent_env import (  # noqa: E402
    ActionRecorder,
    IdmDriver,
    make_env,
    sim_step_seconds,
    step_config,
)
from policy_client import SENSORS, RemotePolicy, SensorPack, sensor_config  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("dataset", help="Directory holding dataset_summary.pkl")
    parser.add_argument(
        "--render",
        default=None,
        choices=["3D", "offscreen"],
        help="Omit for no graphics at all, which is what a training loop wants.",
    )
    parser.add_argument(
        "--record", default=None, help="Write (observation, action) pairs to this .npz"
    )
    parser.add_argument(
        "--max-lateral-dist",
        type=float,
        default=None,
        help="How far sideways of the recorded route the ego may get before MetaDrive ends the "
        "episode. MetaDrive's own 4 m applies when this is not given, and a policy that has "
        "not learned to steer crosses it in a second or two.",
    )
    parser.add_argument(
        "--policy-url",
        default=None,
        help="Drive with a model hosted in another process instead of the IDM - see "
        "examples/policy_server.py. e.g. http://127.0.0.1:8642",
    )
    parser.add_argument(
        "--step-hz",
        type=float,
        default=None,
        help="How many times a second the simulator advances. MetaDrive's own rate is 10. It "
        "is the rate the policy is called at, so it is also the control rate.",
    )
    parser.add_argument(
        "--sensors",
        default="",
        help="Comma-separated extras to send alongside the observation, for --policy-url: "
        + ", ".join(SENSORS)
        + ". The numeric ones cost about a kilobyte a step; a camera or the point cloud costs "
        "hundreds and needs --render offscreen or 3D.",
    )
    arguments = parser.parse_args()

    names = tuple(name.strip() for name in arguments.sensors.split(",") if name.strip())
    if names and not arguments.policy_url:
        parser.error("--sensors only means something with --policy-url")

    overrides = {}
    if arguments.max_lateral_dist is not None:
        overrides["max_lateral_dist"] = arguments.max_lateral_dist
    registered = sensor_config(names) if names else {}
    if registered:
        overrides["sensors"] = registered
    if arguments.step_hz is not None:
        overrides.update(step_config(arguments.step_hz))

    env = make_env(arguments.dataset, render=arguments.render, verbose=True, **overrides)
    if arguments.policy_url:
        policy = RemotePolicy(
            arguments.policy_url,
            pack=SensorPack(env, names),
            step_seconds=sim_step_seconds(env),
        )
        policy.spec({"dataset": arguments.dataset})
    else:
        policy = IdmDriver(env)
    recorder = ActionRecorder() if arguments.record else None

    try:
        for index in range(env.config["num_scenarios"]):
            observation, _ = env.reset(seed=index)
            scenario = env.engine.data_manager.current_scenario
            if recorder is not None:
                recorder.start_episode(scenario["id"])
            if isinstance(policy, RemotePolicy):
                policy.start_episode(scenario["id"])

            steps = 0
            info = {}
            while True:
                action = policy(observation)
                previous_observation = observation
                observation, _, terminated, truncated, info = env.step(action)
                if recorder is not None:
                    # The observation that produced the action, paired with what the car
                    # executed - read off the vehicle rather than from `action`, because those
                    # two differ whenever anything clips or ignores it. See `ActionRecorder`.
                    recorder.record(previous_observation, env.agent)
                steps += 1
                if arguments.render == "3D":
                    env.render(text={"scenario": scenario["id"]})
                if terminated or truncated:
                    break

            print(
                "scenario {:<3} {}: {} steps, arrive_dest={}, completion {:.3f}".format(
                    index,
                    scenario["id"],
                    steps,
                    bool(info.get("arrive_dest", False)),
                    float(info.get("route_completion", float("nan"))),
                )
            )
    finally:
        env.close()

    if isinstance(policy, RemotePolicy) and policy.calls:
        # Printed against env.step's own 0.954 ms rather than on its own: the number only means
        # something next to the work it is being added to. A median near 40 ms means TCP_NODELAY
        # is missing at one end, not that the model is slow.
        print(
            f"policy       {policy.calls} calls, "
            f"{1000 * policy.seconds / policy.calls:.3f} ms each on average, "
            f"{policy.seconds:.2f} s total"
        )
        policy.close()

    if recorder is not None:
        written = recorder.save(arguments.record)
        if written is None:
            print("recorded     nothing: the drive took no steps")
        else:
            observations, actions = written
            print(
                f"recorded     {observations[0]} steps, observations {observations}, "
                f"actions {actions} -> {arguments.record}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
