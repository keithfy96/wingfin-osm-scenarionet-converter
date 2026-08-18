"""Drive a converted dataset and report every output MetaDrive can produce, with samples.

    <metadrive-checkout>/.venv/bin/python tools/sensor_survey.py \\
        workspaces/junction-1/scenarionet --render offscreen

Stage 7c. The question this answers is "what can a model actually see" - camera, lidar, IMU,
GPS - which cannot be answered from `examples/drive_with_a_policy.py`, because the 161-float
array that loop passes around is **not sensor data**. It is MetaDrive's RL observation:
`LidarStateObservation`, a normalised summary built for a network rather than for a driver.
Everything below the observation section here is reached some other way, and this file exists
so that which one is a decision made from a measurement rather than from a guess.

Nothing here is a dependency of anything. It imports `agent_env` for the map settings and is
otherwise standalone, and it writes only into its output directory.

Four things it exists to report, because each of them is invisible from the observation alone
and each has bitten:

* **The 120-laser ray lidar is blind on this data.** `Lidar.perceive` scans
  `physics_world.dynamic_world` and our datasets hold exactly one car - the ego. So all 120
  values sit at 1.0 for the whole drive. That is not a misconfiguration and will change on its
  own when stage 8 puts traffic on the map, which is why this tool measures it every run
  rather than repeating a number from a document.
* **The road is seen by the *side detector*, not by the lidar.** 12 lasers against the static
  world, and they are what actually move.
* **The observation is normalised into [0, 1] while the action is [-1, 1].** A model that
  matches its output range to its input range cannot brake.
* **GPS is exact here**, and needs no dependency - see `geodesy`.
"""

from __future__ import annotations

import argparse
import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from agent_env import IdmDriver, make_env  # noqa: E402
from geodesy import aeqd_inverse, projection_origin  # noqa: E402

# Where the point cloud's unhit rays land. They are put on the depth buffer's far plane and
# come back as coordinates thousands of metres away - measured -18438 m to +10991 m on a
# 200x64 cloud - so a raw min/max of that array describes the sky rather than the scene.
POINT_CLOUD_MAX_RANGE_M = 200.0

# The observation's blocks, as `LidarStateObservation` lays them out for `ScenarioEnv`'s
# sensor configuration (`scenario_env.py:61`): side_detector 12 lasers, lane_line_detector
# off, lidar 120. The widths are asserted against the real observation below rather than
# trusted, because turning a detector on moves every boundary after it.
OBSERVATION_BLOCKS = (
    ("side detector, 12 lasers, static world - road edges", 12),
    ("heading error, speed, steering, last throttle, last steering", 5),
    ("yaw rate", 1),
    ("lateral offset within the lane", 1),
    ("navigation - next 10 route points (ahead, sideways), clipped at 30 m", 22),
    ("ray lidar, 120 lasers, 50 m, dynamic world", 120),
)


def _straight_driver(steering=0.0, throttle=0.6):
    """Keith's "just go straight": a constant action, ignoring everything it is shown."""

    def drive(observation=None):
        return [steering, throttle]

    return drive


def _moved(samples, tolerance=1e-6):
    """How many columns of a (steps, width) stack are not constant over the drive."""
    import numpy

    stacked = numpy.stack(samples)
    return int((stacked.std(axis=0) > tolerance).sum()), stacked


def _write_image(path, array):
    """Save an (H, W, 1|3) float-or-uint array as a PNG. Returns what was written."""
    import numpy
    from PIL import Image

    array = numpy.asarray(array)
    if array.ndim == 3 and array.shape[2] == 1:
        array = array[:, :, 0]
    if array.dtype != numpy.uint8:
        finite = numpy.isfinite(array)
        low = float(array[finite].min()) if finite.any() else 0.0
        high = float(array[finite].max()) if finite.any() else 1.0
        span = high - low if high > low else 1.0
        array = numpy.clip((array - low) / span, 0.0, 1.0)
        array = (array * 255).astype(numpy.uint8)
    Image.fromarray(array).save(path)
    return os.path.basename(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("dataset", help="Directory holding dataset_summary.pkl")
    parser.add_argument(
        "--out",
        default=None,
        help="Where samples are written. Defaults to <dataset>/../sensor-survey, which is "
        "inside the workspace and so gitignored.",
    )
    parser.add_argument(
        "--render",
        default="offscreen",
        choices=["offscreen", "3D"],
        help="Every camera and the point cloud need a render context. `--render none` is not "
        "offered on purpose: Terrain.reset builds no ground without one, so the cameras "
        "would return a picture of nothing and the survey would report it as data.",
    )
    parser.add_argument(
        "--policy",
        default="idm",
        choices=["idm", "straight"],
        help="`idm` keeps the car on its route, so the sensors see a real drive. `straight` "
        "holds one constant action - it leaves the road within seconds, which is the point "
        "of it, and --max-lateral-dist is raised so MetaDrive does not end the episode there.",
    )
    parser.add_argument("--scenario", type=int, default=0, help="Which scenario to drive")
    parser.add_argument("--steps", type=int, default=400, help="Cap on steps")
    parser.add_argument(
        "--sample-at",
        type=int,
        default=40,
        help="The step whose camera frames and point cloud are written out",
    )
    parser.add_argument("--camera-width", type=int, default=320)
    parser.add_argument("--camera-height", type=int, default=180)
    parser.add_argument(
        "--point-cloud-size",
        default="200x64",
        help="WIDTHxHEIGHT of the point cloud; the height is the channel count, so 200x64 is "
        "a 64-channel lidar (MetaDrive's own example uses exactly this)",
    )
    parser.add_argument(
        "--max-lateral-dist",
        type=float,
        default=None,
        help="Defaults to MetaDrive's 4 m under --policy idm and to 1000 m under --policy "
        "straight, so a deliberate drive off the road is surveyed rather than cut short.",
    )
    arguments = parser.parse_args()

    import numpy
    from metadrive.component.sensors.depth_camera import DepthCamera
    from metadrive.component.sensors.point_cloud_lidar import PointCloudLidar
    from metadrive.component.sensors.rgb_camera import RGBCamera
    from metadrive.component.sensors.semantic_camera import SemanticCamera
    from metadrive.obs.state_obs import LidarStateObservation

    dataset = os.path.abspath(arguments.dataset)
    out = arguments.out or os.path.join(os.path.dirname(dataset), "sensor-survey")
    os.makedirs(out, exist_ok=True)
    cloud_width, _, cloud_height = arguments.point_cloud_size.partition("x")
    cloud_width, cloud_height = int(cloud_width), int(cloud_height or 64)

    lateral = arguments.max_lateral_dist
    if lateral is None:
        lateral = 4.0 if arguments.policy == "idm" else 1000.0

    env = make_env(
        dataset,
        render=arguments.render,
        verbose=True,
        max_lateral_dist=lateral,
        sensors=dict(
            rgb_camera=(RGBCamera, arguments.camera_width, arguments.camera_height),
            depth_camera=(DepthCamera, arguments.camera_width, arguments.camera_height),
            semantic_camera=(SemanticCamera, arguments.camera_width, arguments.camera_height),
            # `ego_centric=True`: points come back in the car's own frame, which is what a
            # perception model wants. False gives world coordinates, which move with the map.
            point_cloud=(PointCloudLidar, cloud_width, cloud_height, True),
        ),
    )

    policy = IdmDriver(env) if arguments.policy == "idm" else _straight_driver()
    observation, _ = env.reset(seed=arguments.scenario)
    scenario = env.engine.data_manager.current_scenario
    metadata = scenario["metadata"]

    # The 161-float vector is built here rather than taken from `env.step`, and that is the
    # whole reason this line exists. A camera can only be reached offscreen with
    # `image_observation=True` - `base_env.py:343` deletes every `BaseCamera` from the sensor
    # list otherwise - and that switch swaps `LidarStateObservation` for
    # `ImageStateObservation`, whose `observe` returns `{"image": ..., "state": ...}` with the
    # *state* being the 41-number `StateObservation` and **no lidar block at all**
    # (`image_obs.py:40`). So surveying the cameras and reporting the ordinary observation are
    # in conflict through the config, and building the observation directly is what resolves
    # it. `BaseObservation` reaches the engine through `get_engine()`, so this is legal from
    # outside for the same reason `IdmDriver` is.
    observation_module = LidarStateObservation(env.config)
    stepped_observation_shape = None

    origin = projection_origin(metadata.get("coordinate_system_wkt"))
    # MetaDrive re-centres every scenario on the ego's first position and records what it did
    # (`scenario_description.py:676`), so the shift back is in the file rather than inferred.
    shift = numpy.asarray(metadata.get("old_origin_in_current_coordinate", [0.0, 0.0]),
                          dtype=float)

    observations = []
    track = []
    previous_velocity = None
    info = {}
    info_samples = []
    steps = 0
    samples = {}

    while steps < arguments.steps:
        action = policy(observation)
        observations.append(
            numpy.asarray(observation_module.observe(env.agent), dtype=numpy.float32).ravel()
        )

        body = env.agent.body
        linear = [float(v) for v in body.get_linear_velocity()]
        angular = [float(v) for v in body.getAngularVelocity()]
        acceleration = (
            [(linear[i] - previous_velocity[i]) / 0.1 for i in range(3)]
            if previous_velocity is not None
            else [0.0, 0.0, 0.0]
        )
        previous_velocity = linear
        position = [float(v) for v in env.agent.origin.getPos()]
        projected = numpy.asarray(position[:2], dtype=float) - shift
        latitude, longitude = (
            aeqd_inverse(origin[0], origin[1], float(projected[0]), float(projected[1]))
            if origin
            else (float("nan"), float("nan"))
        )
        track.append(
            dict(
                step=steps,
                x=position[0], y=position[1], z=position[2],
                projected_x=float(projected[0]), projected_y=float(projected[1]),
                latitude=latitude, longitude=longitude,
                speed_mps=float(env.agent.speed),
                vx=linear[0], vy=linear[1], vz=linear[2],
                wx=angular[0], wy=angular[1], wz=angular[2],
                ax=acceleration[0], ay=acceleration[1], az=acceleration[2],
                roll=float(env.agent.roll), pitch=float(env.agent.pitch),
                heading=float(env.agent.heading_theta),
                steering=float(action[0]), throttle=float(action[1]),
            )
        )

        if steps == arguments.sample_at:
            for name in ("rgb_camera", "depth_camera", "semantic_camera", "point_cloud"):
                try:
                    sensor = env.engine.get_sensor(name)
                    samples[name] = numpy.asarray(
                        sensor.perceive(to_float=True, new_parent_node=env.agent.origin)
                    )
                except Exception as error:  # noqa: BLE001 - reported, never fatal
                    samples[name] = error

        observation, _, terminated, truncated, info = env.step(action)
        if stepped_observation_shape is None:
            stepped_observation_shape = (
                {key: numpy.asarray(value).shape for key, value in observation.items()}
                if isinstance(observation, dict)
                else numpy.asarray(observation).shape
            )
        info_samples.append(info)
        steps += 1
        if terminated or truncated:
            break

    env.close()

    lines = []
    add = lines.append
    add("")
    add("run          {} scenario {} ({}), policy {}, {} steps, render {}".format(
        os.path.basename(dataset), arguments.scenario, scenario["id"], arguments.policy,
        steps, arguments.render))
    add("ended        {}".format(
        "arrived" if info.get("arrive_dest") else
        "out of road" if info.get("out_of_road") else
        "crash" if info.get("crash") else
        "step cap" if steps >= arguments.steps else "terminated"))

    # ---- the RL observation -------------------------------------------------------------
    moved, stacked = _moved(observations)
    add("")
    add("OBSERVATION  the array env.step() returns - normalised, and NOT sensor data")
    add(
        f"             shape {stacked.shape}  dtype {stacked.dtype}  "
        f"range [{float(stacked.min()):.3f}, {float(stacked.max()):.3f}]  "
        f"{moved} of {stacked.shape[1]} values move"
    )
    width = sum(size for _, size in OBSERVATION_BLOCKS)
    if width == stacked.shape[1]:
        start = 0
        for label, size in OBSERVATION_BLOCKS:
            block = stacked[:, start:start + size]
            block_moved = int((block.std(axis=0) > 1e-6).sum())
            add(f"  [{start:>3}:{start + size:<3}] {block_moved:>3}/{size:<3} move   {label}")
            start += size
    else:
        add(
            f"  the block table does not apply: this run's observation is "
            f"{stacked.shape[1]} wide, not {width}. A detector was turned on or off."
        )
    add("             the observation is in [0, 1]; the action you send back is in [-1, 1]")
    numpy.save(os.path.join(out, "observation.npy"), stacked)
    add("")
    add("             built directly, NOT taken from env.step - because with a camera on,")
    add(f"             env.step returned {stepped_observation_shape}")
    add("             instead. Reaching a camera offscreen needs image_observation=True")
    add("             (base_env.py:343 deletes every camera from the sensor list without it),")
    add("             and that swaps in ImageStateObservation, whose `state` is 41 numbers")
    add("             with no lidar block at all. Turning cameras on changes what a model")
    add("             trained on the vector above would be handed.")

    # ---- cameras and the point cloud ----------------------------------------------------
    add("")
    add(f"CAMERAS      one frame each at step {arguments.sample_at}, written as PNG")
    for name in ("rgb_camera", "depth_camera", "semantic_camera"):
        value = samples.get(name)
        if isinstance(value, Exception):
            add(f"  {name:<16} FAILED {type(value).__name__}: {str(value)[:90]}")
        elif value is None:
            add(f"  {name:<16} not sampled: the drive ended before step {arguments.sample_at}")
        else:
            written = _write_image(os.path.join(out, name + ".png"), value)
            add(
                f"  {name:<16} shape {str(value.shape):<18} "
                f"dtype {str(value.dtype):<8} "
                f"range [{float(value.min()):.3f}, {float(value.max()):.3f}]  -> {written}"
            )

    add("")
    add("POINT CLOUD  a real 3-D cloud, in the car's own frame - x, y, z per ray")
    cloud = samples.get("point_cloud")
    if isinstance(cloud, Exception):
        add(f"  FAILED {type(cloud).__name__}: {str(cloud)[:110]}")
    elif cloud is None:
        add(f"  not sampled: the drive ended before step {arguments.sample_at}")
    else:
        ranges = numpy.linalg.norm(cloud, axis=-1)
        hit = ranges < POINT_CLOUD_MAX_RANGE_M
        add(
            f"  shape {cloud.shape}  dtype {cloud.dtype}  = "
            f"{cloud.shape[1]} rays over {cloud.shape[0]} channels"
        )
        add(
            f"  raw range [{float(ranges.min()):.1f}, {float(ranges.max()):.1f}] m - "
            "a ray that hits nothing lands on the depth buffer's far"
        )
        add(
            f"  plane, so the raw extent describes the sky. Within "
            f"{POINT_CLOUD_MAX_RANGE_M:.0f} m: {int(hit.sum())} of {hit.size} rays "
            f"({100.0 * hit.sum() / hit.size:.1f}%),"
        )
        if hit.any():
            add(
                f"  those spanning [{float(ranges[hit].min()):.2f}, "
                f"{float(ranges[hit].max()):.2f}] m."
            )
        numpy.save(os.path.join(out, "point-cloud.npy"), cloud)
        add("  -> point-cloud.npy")

    # ---- IMU and GPS --------------------------------------------------------------------
    columns = list(track[0].keys())
    with open(os.path.join(out, "track.csv"), "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(track)

    def span(key):
        values = [row[key] for row in track]
        return min(values), max(values)

    add("")
    add("IMU          assembled from the physics body - MetaDrive has no IMU sensor class")
    add("  velocity       3-D, m/s      x [{:+.2f}, {:+.2f}]  y [{:+.2f}, {:+.2f}]  "
        "z [{:+.2f}, {:+.2f}]".format(*span("vx"), *span("vy"), *span("vz")))
    add("  angular vel    3-D, rad/s    x [{:+.3f}, {:+.3f}]  y [{:+.3f}, {:+.3f}]  "
        "z [{:+.3f}, {:+.3f}]".format(*span("wx"), *span("wy"), *span("wz")))
    add("  acceleration   differenced over the 0.1 s step, m/s^2   "
        "x [{:+.2f}, {:+.2f}]  y [{:+.2f}, {:+.2f}]".format(*span("ax"), *span("ay")))
    add("  attitude       rad          roll [{:+.4f}, {:+.4f}]  pitch [{:+.4f}, {:+.4f}]  "
        "heading [{:+.3f}, {:+.3f}]".format(*span("roll"), *span("pitch"), *span("heading")))

    add("")
    add("GPS / GNSS   exact, not approximated - the dataset carries its own projection")
    if origin is None:
        add("  unavailable: metadata.coordinate_system_wkt is missing, is not azimuthal")
        add("  equidistant, or is not on WGS 84. Refused rather than guessed - a wrong origin")
        add("  reports a plausible position in the wrong place.")
    else:
        add("  projection     azimuthal equidistant on WGS 84, centred {:.9f}, {:.9f}".format(
            *origin))
        add(
            f"  recentring     MetaDrive shifted the scenario by "
            f"[{float(shift[0]):+.3f}, {float(shift[1]):+.3f}] m and recorded it"
        )
        add("  start          {:.9f}, {:.9f}".format(track[0]["latitude"],
                                                     track[0]["longitude"]))
        add("  end            {:.9f}, {:.9f}".format(track[-1]["latitude"],
                                                     track[-1]["longitude"]))
    add(
        f"  -> track.csv   {len(track)} rows, {len(columns)} columns: "
        "position, lat/lon, IMU, attitude, action"
    )

    # ---- what env.step hands back ------------------------------------------------------
    add("")
    add("INFO         the dict env.step() returns - not seen by a policy, but available")
    varying = []
    for key in sorted(info_samples[0]):
        try:
            values = {repr(sample.get(key)) for sample in info_samples}
        except TypeError:
            continue
        if len(values) > 1:
            varying.append(key)
    add(f"  {len(info_samples[0])} keys, {len(varying)} of them vary over the drive")
    add("  varying: {}".format(", ".join(varying) or "none"))

    add("")
    add(f"samples      {out}")
    add("")
    print("\n".join(lines))

    report = os.path.join(out, "survey.txt")
    with open(report, "w") as handle:
        handle.write("\n".join(lines) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
