"""The AV3 checkpoint, loaded here and fed from MetaDrive rather than from CARLA.

    from av3_model import AV3Model, load_config

    model = AV3Model(load_config(), checkpoint, decision_interval_s=0.05).load()
    model.observe(rig.read(), env.agent)         # once per decision, every decision
    prediction = model.predict(env.agent)        # (20, 8)
    rows = model.modelv2_rows(prediction)        # what the bridge's `from_predicted` eats

Stage 9 Phase C.3. `tools/openpilot_policy.py` fills the bridge's `waypoints` from
`waypoints_from_route` - the recorded route resampled at the car's *current* speed, which is
wing-sim's `route_gt.py` and is a **controller** test by construction. Phase 0 measured what
that costs: the bridge's median `accel_cmd` on `junction-1` is -0.30 m/s^2 with only 159 of
1559 calls positive, because a constant-speed trajectory carries no speed *intent* for the
longitudinal planner to read, and the car crawls at 4 m/s under a 36 km/h cruise. No pedal
calibration fixes that; only a model predicting where it wants to be does.

This is what `wing-sim/evaluation/src/inference_models/av3_trt.py` + `av3_base.py` do, minus
everything CARLA. It is **reimplemented rather than imported** for the reason every other
cross-checkout thing in `tools/` is: wing-sim is a read-only reference on this machine, its
package layout assumes its own `src/` on the path, and the halves that differ - the sensor
source, the route source, the frame - are exactly the halves this file exists to write.

**Every conversion below fails silently.** Nothing raises when a sign is wrong; the car
simply drives somewhere else. So each one is named, its source cited, and each is checked
by `tools/av3_probe.py` against a recorded drive before anything steers.

    1  pixels        rig camera (H, W, 3) uint8 BGR  ->  (3, 288, 512) float32 RGB in [0, 1]
    2  camera order  by NAME, against `rigs/av3.txt`, whose names and aims now agree
    3  history       a ring of `t_frames` frames `frame_stride_s` apart
    4  ego speed     [v_fwd / 8.09, v_lat_RIGHT / 0.27]
    5  route         (20, 7), MIRRORED out of MetaDrive's frame
    6  waypoints out (20, 8) straight through - already x-forward, y-RIGHT

**4 and 5 are one fact seen twice.** MetaDrive is right-handed with **y left** and yaw
CCW-positive; the frame the model was trained in is CARLA's, **y right** and yaw
CW-positive. That is a mirror, so `y`, `sin(theta)`, `yaw`, `yaw_rate`, `v_y` and curvature
all negate **together**, and `x`, `cos(theta)`, `v_x`, `a_x` do not. Getting half of it right
steers smoothly into the oncoming carriageway with nothing raising - the same failure
`openpilot_policy`'s docstring records for the bridge's own two negations.

**6 does NOT negate, and that is the one asymmetry.** `waypoints_from_route` flips `y`
because it *starts* from MetaDrive's left-positive route sensor. The model's output starts
in its own training frame, which is already the bridge's. wing-sim passes it through
unflipped too (`controllers/openpilot/controller.py:140, :189`).

**MetaDrive's camera really is BGR**, which is what makes conversion 1 the fork's modifier
verbatim rather than an adaptation of it. `BaseCamera.get_image` returns
`get_rgb_array_cpu()` unchanged for `mode="bgr"` and reverses the last axis for `mode="rgb"`
(`base_camera.py:110-113`), and `image_buffer.py:104-110` reads panda3d's RAM image, which is
BGRA. So `perceive()` hands back exactly what CARLA's `raw_data` does.

**The forward pass is about a second** - 947-1002 ms measured in Phase C.1, on a card capped
at 35 W of its 60 W rating. `env.step` is the tick, so a slow policy makes a slow drive and
never a wrong one; a `junction-1` route at `--step-hz 100 --decision-hz 20` is 758 decisions,
so about 13 minutes of wall clock. That is arithmetic, not a fault.

**`--image-on-cuda` is deliberately not wired into this path.** It is refused above a stride
of 1 unless `--draw-every-step`, which throws away the 4.2x the frame gate is worth, and
against a 1 s forward pass Phase B's 3 ms saving is noise. Said here so the next reader knows
it was decided rather than overlooked.
"""

from __future__ import annotations

import collections
import contextlib
import math
import os

import numpy

# The fixed horizon the waypoint times are spread over. `av3_base.MODEL_HORIZON_S`, and NOT a
# `model_dev.yml` key - it is `Av3ModelSettings.waypoint_horizon_s`'s default
# (`model_dev_config.py:19`) and the shipped config does not carry it. With the 20 waypoints
# this checkpoint emits that is 0.1 s spacing.
MODEL_HORIZON_S = 2.0

# `av3_base.MODELV2_OUTPUT_WIDTH`: [x, y, yaw, yaw_rate, v_x, v_y, a_x, a_y]. The narrower
# layout (2, waypoints only) exists in wing-sim and this checkpoint does not use it - Phase
# C.1 read (1, 20, 8) straight out of the archive - so a width of 2 is refused here rather
# than silently taking the `derive` path.
MODELV2_OUTPUT_WIDTH = 8

# `routes/route.py:ROUTE_FEATURE_DIM`.
ROUTE_FEATURE_DIM = 7

# The settings the weights were trained with, tracked in this repo at `config/model_dev.yml`.
#
# It used to default to the fork checkout's copy, at an absolute path into one laptop's home
# directory - and that is a default that is correct on exactly one machine. It was also the only
# copy in existence: the file came out of the fork's `assets.zip` and was in no git repository at
# all until `2e686a2`, which is why vendoring the fork missed it (the vendored tree is
# `wingfin-openpilot-temp/openpilot/`; the config sits in the sibling `assets/`). A rig was handed
# the 1.2 GB `.ep` and not the 4.3 KB yml, and failed at load with nothing naming the cause.
#
# Derived from `__file__` rather than named, the way `drive.py:893` derives `rigs/av3.txt`, so it
# follows a worktree or a bind mount instead of pinning one checkout. `/work/config/model_dev.yml`
# in the container resolves to this same file through the repo mount.
#
# `MODEL_CONFIG` still overrides it, mirroring the `MODEL_CHECKPOINT` that `model_probe.py:413`
# reads for the `.ep` - one convention for the pair rather than one each. That override is not
# decoration: the yml names its own `checkpoint:` and `route_path:`, so it is paired to one set of
# weights. This is the default pair, not the only one. Read at import, so the two callers that
# already say `arguments.model_config or av3_model.DEFAULT_CONFIG` (`av3_probe.py:204`,
# `drive.py:1341`) pick it up with no change of their own.
DEFAULT_CONFIG = os.environ.get(
    "MODEL_CONFIG",
    os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "model_dev.yml"
    ),
)

# Read out of `model_dev.yml` and required to be present. Nothing here is defaulted: a
# silently-defaulted `frame_stride_s` is the exact failure that file's own comment warns
# about - "A wrong frame_stride_s never [stops the run]: the model runs, on history spaced
# differently to how it was trained, and the run still scores."
REQUIRED_KEYS = (
    "camera_order",
    "t_frames",
    "frame_stride_s",
    "ego_velocity_scale",
    "n_route",
    "route_spacing_m",
    "route_max_offset_m",
    "expected_camera_image_width",
    "expected_camera_image_height",
)


class ModelError(RuntimeError):
    """Something about the model, its config or its inputs. Always says which."""


class Config:
    """`model_dev.yml`'s `model:` block, with every field this needs present."""

    def __init__(self, values, path=None):
        self.path = path
        missing = [key for key in REQUIRED_KEYS if values.get(key) is None]
        if missing:
            raise ModelError(
                "{} has no {} under `model:`. Nothing here is defaulted - the shipped "
                "config's own comment says a wrong frame_stride_s never stops a run, it "
                "just feeds the model history it was not trained on.".format(
                    path or "the config", ", ".join(missing)
                )
            )
        self.camera_order = [str(name) for name in values["camera_order"]]
        self.t_frames = int(values["t_frames"])
        self.frame_stride_s = float(values["frame_stride_s"])
        scale = values["ego_velocity_scale"]
        if not isinstance(scale, (list, tuple)) or len(scale) != 2:
            raise ModelError(
                f"ego_velocity_scale is {scale!r}; this checkpoint is AV31 and takes the "
                "two-component [s_lon, s_lat] form"
            )
        self.ego_velocity_scale = (float(scale[0]), float(scale[1]))
        self.n_route = int(values["n_route"])
        self.route_spacing_m = float(values["route_spacing_m"])
        self.route_max_offset_m = float(values["route_max_offset_m"])
        self.image_width = int(values["expected_camera_image_width"])
        self.image_height = int(values["expected_camera_image_height"])
        # Read and NOT applied, exactly as in wing-sim, where `Evaluation.cleanup` hardcodes
        # `reference_offset_m=0.0` and its own `docs/reference/known-gaps.md` records that
        # nothing reads this field. Carried so `av3_probe` can say so beside a cross-track
        # number that the anchor would bias in corners.
        self.waypoint_reference = str(values.get("waypoint_reference", "origin"))
        if len(self.camera_order) != len(set(self.camera_order)):
            raise ModelError(f"camera_order repeats a name: {self.camera_order}")
        if self.t_frames < 1:
            raise ModelError(f"t_frames is {self.t_frames}")
        if self.frame_stride_s <= 0:
            raise ModelError(f"frame_stride_s is {self.frame_stride_s}")


def load_config(path=DEFAULT_CONFIG):
    """Read the fork's `model_dev.yml`. PyYAML is a dependency of this repo already."""
    import yaml

    path = os.path.abspath(path)
    if not os.path.exists(path):
        raise ModelError(f"no model config at {path}")
    with open(path) as handle:
        document = yaml.safe_load(handle) or {}
    section = document.get("model")
    if not isinstance(section, dict):
        raise ModelError(f"{path} has no `model:` block")
    return Config(section, path=path)


# ---------------------------------------------------------------------------------------
# 1. pixels
# ---------------------------------------------------------------------------------------


def preprocess(bgr, width, height):
    """(H, W, 3) uint8 BGR -> (3, height, width) **uint8** RGB, channel-first.

    `assets/modifiers/modifiers.py:camera_preprocessing` without its final `/ 255.0`, which
    `_sampled_images` applies instead so the ring can hold a quarter of the bytes. Phase A
    established that the divide is the thing that *creates* the float and that
    `(uint8 / 255 * 255).round()` returns all 256 values exactly, so nothing is lost by
    holding the picture the way it was rendered.

    The resize is **not** a no-op and must not be made one. The rig renders 4:3 and the model
    eats 16:9: this is a straight vertical squash by 1.33x, and the model was trained on the
    squashed picture. Rendering 512x288 natively instead would give a vertical field of view
    a third narrower than the model has ever seen, with nothing raising. See `rigs/av3.txt`.
    """
    import cv2

    if bgr.ndim != 3 or bgr.shape[2] != 3:
        raise ModelError(f"a camera frame must be (H, W, 3); got {bgr.shape}")
    if bgr.dtype != numpy.uint8:
        raise ModelError(
            f"a camera frame must be uint8; got {bgr.dtype}. Read the rig with "
            "`to_float=False` - a float frame here is Phase A's 8x inflation coming back."
        )
    if bgr.shape[:2] != (height, width):
        bgr = cv2.resize(bgr, (width, height), interpolation=cv2.INTER_AREA)
    # After the resize, not before: the same pixels either way, and the fork measures 37 ms
    # of difference at 1440x1080.
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    return numpy.ascontiguousarray(numpy.transpose(rgb, (2, 0, 1)))


# ---------------------------------------------------------------------------------------
# 3. the temporal ring
# ---------------------------------------------------------------------------------------


class FrameHistory:
    """`t_frames` frames `frame_stride_s` apart, out of a ring filled once per decision.

    `av3_base.__init__`'s arithmetic, with one difference stated rather than hidden: there,
    `tick_rate` is the CAMERA rate and `observe` is called on every camera tick including the
    ones the model does not predict on. Here the camera draw and the decision are gated
    together by `tools/frame_gate.py` at `--decision-hz`, so the two rates are the same number
    and the ring is filled exactly once per prediction.

    **The ego state is buffered beside the pictures and sampled with them**, because the
    engine takes `(1, T, 2)` and not `(1, 2)` - `av3_base` keeps `_ego_buf` alongside
    `_image_buf` for exactly this. Tiling the current speed T times would feed the model a car
    that has been going at its present speed for the last two seconds, which is a different
    claim from the one the history makes and one nothing would raise about.

    Pictures are held as **uint8**: at `--decision-hz 20` the stride is 10 and the depth 41,
    so 41 x 6 x 3 x 288 x 512 is **108.8 MB** of host RAM. The same ring in preprocessed
    float32 would be 435 MB for a picture that is 8-bit at the buffer.
    """

    def __init__(self, t_frames, frame_stride_s, read_interval_s):
        if read_interval_s <= 0:
            raise ModelError(f"the read interval is {read_interval_s} s")
        self.read_interval_s = float(read_interval_s)
        self.stride = max(1, int(round(frame_stride_s / read_interval_s)))
        self.depth = (t_frames - 1) * self.stride + 1
        self.sample_index = [k * self.stride for k in range(t_frames)]
        self.actual_stride_s = self.stride * self.read_interval_s
        self.requested_stride_s = float(frame_stride_s)
        self._frames = collections.deque(maxlen=self.depth)
        self._ego = collections.deque(maxlen=self.depth)

    @property
    def spacing_note(self):
        """What to say when the read interval cannot divide the training stride.

        `av3_base` warns and carries on, and this does the same rather than refusing: the
        stride is a property of the *drive's* rate, and a run at 10 Hz decisions is still a
        run. But it is history the model was not trained on, so it must be said out loud.
        """
        if abs(self.actual_stride_s - self.requested_stride_s) <= 1e-9:
            return None
        return (
            f"frame_stride_s is {self.requested_stride_s:g} s and the cameras are read every "
            f"{self.read_interval_s:g} s, which does not divide it - so the model will see "
            f"frames {self.actual_stride_s:g} s apart instead. --decision-hz "
            f"{1.0 / self.requested_stride_s:g} (or any rate that divides it) is what matches."
        )

    def observe(self, stack, ego):
        """One decision's worth: `(cameras, 3, H, W)` uint8 and a `(2,)` ego state.

        The ring is FILLED on the first call rather than left short, so a prediction can run
        immediately - `av3_base.observe`'s own behaviour. The alternative is `depth` decisions
        at the start of every episode with nothing to steer by.
        """
        if not self._frames:
            for _ in range(self.depth):
                self._frames.append(stack)
                self._ego.append(ego)
        else:
            self._frames.append(stack)
            self._ego.append(ego)

    def reset(self):
        self._frames.clear()
        self._ego.clear()

    def sampled(self):
        """`((1, T, cameras, 3, H, W) float32 in [0, 1], (1, T, 2) float32)`.

        `av3_base._sampled_images` / `_sampled_ego` index a newest-*last* deque at
        `[0, stride, 2*stride, ...]`, so index 0 is the OLDEST frame in a full ring. The order
        is reproduced exactly rather than reasoned about: a reversed history is another thing
        that runs and is wrong.
        """
        if not self._frames:
            raise ModelError("sampled() before observe() - the ring is empty")
        images = numpy.stack([self._frames[i] for i in self.sample_index], axis=0)
        ego = numpy.stack([self._ego[i] for i in self.sample_index], axis=0)
        return (images.astype(numpy.float32) / 255.0)[None, ...], ego[None, ...]


# ---------------------------------------------------------------------------------------
# 4 and 5. the mirror
# ---------------------------------------------------------------------------------------


def ego_state(agent, velocity_scale):
    """`[v_fwd / s_lon, v_lat_right / s_lat]` float32, from MetaDrive's own velocity.

    `av3_base._build_ego_state` reads CARLA's `velocity_x` / `velocity_y` and its `rotation_yaw`,
    where `-vx sin + vy cos` is the component to the **right**. MetaDrive's world frame is
    right-handed with y north and `heading_theta` CCW from +x, so the identical expression is
    the component to the **left** - hence the negation, and hence conversion 4.

    Read off `agent.velocity` rather than off `agent.speed`, which is a magnitude and cannot
    tell a sideways slide from forward motion.
    """
    velocity = agent.velocity
    heading = float(agent.heading_theta)
    cos_heading, sin_heading = math.cos(heading), math.sin(heading)
    east, north = float(velocity[0]), float(velocity[1])
    forward = east * cos_heading + north * sin_heading
    left = -east * sin_heading + north * cos_heading
    s_lon, s_lat = velocity_scale
    return numpy.array([forward / s_lon, -left / s_lat], dtype=numpy.float32)


def navigation(agent, n_route, spacing_m, max_offset_m):
    """`(n_route, 7)` float32: `[fwd/H, right/H, cos t, sin t, curv*H, s_norm, valid]`.

    `routes/route.py:RouteNavigator.get_navigation`, rebuilt against the live route instead of
    a route parquet. Two differences, both stated rather than papered over:

    * **the nearest point.** wing-sim runs a heading-gated argmin over every route vertex
      (`ROUTE_HEADING_COS_GATE` 0.5), because a CARLA route may double back within the gate's
      reach. Here the route is a `PointLane` and `local_coordinates` returns the arc-length and
      the perpendicular offset directly - MetaDrive's own projection, the one
      `TrajectoryNavigation` steers by. On a route that does not cross itself the two agree;
      on one that does, `local_coordinates` is the better answer, not the worse one.
    * **the off-route guard** is that perpendicular offset rather than the distance to the
      nearest vertex. The same number wherever the route is smooth at the scale of
      `route_max_offset_m`.

    THE MIRROR. `right = -left`, `sin(theta)` negates with it, and curvature - which is
    `d(theta)/ds` - negates because theta does. `fwd`, `cos(theta)`, `s_norm` and `valid` do
    not. Half of this is a car that steers confidently into the oncoming carriageway.
    """
    trajectory = agent.navigation.reference_trajectory
    if trajectory is None:
        raise ModelError(
            "the model needs the recorded route, and this scenario has none. Convert "
            "with --routes."
        )
    horizon = max(1e-6, n_route * spacing_m)
    longitudinal, lateral = trajectory.local_coordinates(agent.position)
    if abs(float(lateral)) > max_offset_m:
        # What wing-sim does off-route: a block of zeros, which is what the model was trained
        # to read as "no route". Not an error - a car pushed wide of its route is an ordinary
        # thing for a drive to contain.
        return numpy.zeros((n_route, ROUTE_FEATURE_DIM), dtype=numpy.float32)

    heading = float(agent.heading_theta)
    cos_heading, sin_heading = math.cos(heading), math.sin(heading)
    here = agent.position
    length = float(trajectory.length)

    forward = numpy.zeros(n_route)
    right = numpy.zeros(n_route)
    theta = numpy.zeros(n_route)
    valid = numpy.zeros(n_route)
    along = numpy.zeros(n_route)
    for index in range(n_route):
        wanted = float(longitudinal) + index * spacing_m
        valid[index] = 1.0 if wanted <= length else 0.0
        clamped = min(wanted, length)
        along[index] = clamped
        world = trajectory.position(clamped, 0.0)
        east = float(world[0]) - float(here[0])
        north = float(world[1]) - float(here[1])
        forward[index] = east * cos_heading + north * sin_heading
        left = -east * sin_heading + north * cos_heading
        right[index] = -left
        # `heading_theta_at` clamps to the final segment past the end, which is what the
        # arc-length clamp above already assumes.
        route_heading = float(trajectory.heading_theta_at(clamped))
        # CCW-positive in MetaDrive, so negated into the model's CW-positive frame. Wrapped
        # before the negation so the wrap is done once, on the quantity that has a branch cut.
        theta[index] = -(((route_heading - heading) + math.pi) % (2 * math.pi) - math.pi)

    step = numpy.gradient(along)
    with numpy.errstate(divide="ignore", invalid="ignore"):
        curvature = numpy.where(
            numpy.abs(step) > 1e-6, numpy.gradient(numpy.unwrap(theta)) / step, 0.0
        )
    s_norm = numpy.arange(n_route, dtype=numpy.float64) / max(1, n_route - 1)
    return numpy.stack(
        [
            forward / horizon,
            right / horizon,
            numpy.cos(theta),
            numpy.sin(theta),
            curvature * horizon,
            s_norm,
            valid,
        ],
        axis=1,
    ).astype(numpy.float32)


# ---------------------------------------------------------------------------------------
# 6. what the bridge is sent
# ---------------------------------------------------------------------------------------


def waypoint_times(n_waypoints, horizon_s=MODEL_HORIZON_S):
    """`av3_base.uniform_waypoint_times`: t_i = horizon * (i + 1) / N, t=0 excluded."""
    if n_waypoints < 1:
        raise ModelError(f"n_waypoints must be >= 1, got {n_waypoints}")
    return [horizon_s * (index + 1) / n_waypoints for index in range(n_waypoints)]


def modelv2_rows(prediction, horizon_s=MODEL_HORIZON_S):
    """`(N, 8)` -> N rows of `[x, y, t, yaw, yaw_rate, v_x, v_y, a_x, a_y]`.

    `from_predicted`'s shape exactly (`derive_modelv2.py:79`), which is where the time column
    lands third rather than last. It prepends its own t=0 anchor at the ego origin, so these
    are the predicted points only.

    **Nothing is negated here.** The model's frame is already the bridge's - see the module
    docstring's conversion 6 - and wing-sim's own controller passes the same numbers through
    unflipped (`controllers/openpilot/controller.py:189`).
    """
    prediction = numpy.asarray(prediction, dtype=numpy.float64)
    if prediction.ndim != 2 or prediction.shape[1] != MODELV2_OUTPUT_WIDTH:
        raise ModelError(
            f"modelv2 rows need an (N, {MODELV2_OUTPUT_WIDTH}) prediction; got "
            f"{prediction.shape}"
        )
    times = waypoint_times(prediction.shape[0], horizon_s)
    rows = []
    for index, row in enumerate(prediction):
        x, y, yaw, yaw_rate, v_x, v_y, a_x, a_y = (float(value) for value in row)
        rows.append([x, y, times[index], yaw, yaw_rate, v_x, v_y, a_x, a_y])
    return rows


def waypoints(prediction, horizon_s=MODEL_HORIZON_S):
    """`[[x, y, t], ...]`, the 3-wide list.

    Sent **as well as** `modelv2`, not instead of it: `server.py:_handle_step` reads
    `msg["waypoints"]` first and returns a hard stop when it is empty, *before* it looks at
    `modelv2` at all. An empty `waypoints` beside a full `modelv2` is a car that never moves.
    """
    prediction = numpy.asarray(prediction, dtype=numpy.float64)
    times = waypoint_times(prediction.shape[0], horizon_s)
    return [
        [float(row[0]), float(row[1]), times[index]]
        for index, row in enumerate(prediction)
    ]


# ---------------------------------------------------------------------------------------
# The model
# ---------------------------------------------------------------------------------------


class AV3Model:
    """The compiled checkpoint, its ring, and the five conversions into it."""

    def __init__(self, config, checkpoint, decision_interval_s, device="cuda"):
        self.config = config
        self.checkpoint = os.path.abspath(checkpoint)
        self.device = device
        self.history = FrameHistory(
            config.t_frames, config.frame_stride_s, decision_interval_s
        )
        self._module = None
        self._navigation = None
        self._torch = None
        self.n_waypoints = None
        self.output_width = None
        self.load_seconds = None

    # -- setup ---------------------------------------------------------------------------
    def load(self):
        """Deserialise the engine, run one pass of zeros, and read the output shape back.

        The warm-up is not a timing convenience: `av3_trt._warmup_inference` is where the
        waypoint count and the output width are **discovered**, and `av3_base.N_WAYPOINTS = 4`
        is a fallback until it runs rather than this model's count. This checkpoint emits 20.
        """
        import time

        import torch
        import torch_tensorrt

        if not os.path.exists(self.checkpoint):
            raise ModelError(f"no checkpoint at {self.checkpoint}")
        self._torch = torch
        started = time.perf_counter()
        # Logs two failures before succeeding - the `.pt2` package loader, then
        # `torch.jit.load` - and neither is an error. See CLAUDE.md.
        self._module = torch_tensorrt.load(self.checkpoint).module()

        cameras = len(self.config.camera_order)
        # bfloat16 is what the archive declares for all three inputs (Phase C.1 read it out of
        # the serialized graph), and it is what `av3_trt` uses whenever `route_path` is set -
        # which for a live route it always effectively is.
        self._navigation = torch.zeros(
            (1, self.config.n_route, ROUTE_FEATURE_DIM),
            dtype=torch.bfloat16,
            device=self.device,
        )
        images = torch.zeros(
            (
                1,
                self.config.t_frames,
                cameras,
                3,
                self.config.image_height,
                self.config.image_width,
            ),
            dtype=torch.bfloat16,
            device=self.device,
        )
        ego = torch.zeros(
            (1, self.config.t_frames, 2), dtype=torch.bfloat16, device=self.device
        )
        with torch.no_grad():
            output = self._module(images, self._navigation, ego)
        self.n_waypoints = int(output.shape[-2])
        self.output_width = int(output.shape[-1])
        self.load_seconds = time.perf_counter() - started
        if self.output_width != MODELV2_OUTPUT_WIDTH:
            raise ModelError(
                f"this checkpoint emits {self.output_width} columns per waypoint, and only "
                f"{MODELV2_OUTPUT_WIDTH} (full modelv2: x, y, yaw, yaw_rate, v_x, v_y, a_x, "
                "a_y) is wired here. A 2-wide waypoints-only model would have to go through "
                "the bridge's `derive` path instead."
            )
        return self

    def close(self):
        self._module = None
        self._navigation = None
        self.history.reset()
        if self._torch is not None and str(self.device).startswith("cuda"):
            with contextlib.suppress(RuntimeError):
                self._torch.cuda.empty_cache()

    # -- one decision --------------------------------------------------------------------
    def image_stack(self, frames):
        """`{name: (H, W, 3) uint8 BGR}` -> `(cameras, 3, H, W)` uint8 RGB, in model order.

        **By name**, which is conversion 2 and is only safe because `rigs/av3.txt` is built
        from wing-sim's own spec: its camera names and its resolved aims agree, where
        `rigs/cams.txt` names its back pair the opposite of its own yaws. `camera_order` is a
        contract with the weights - `model_dev.yml` says so - so a missing name is refused by
        name rather than filled with anything.
        """
        stacked = []
        for name in self.config.camera_order:
            frame = frames.get(name)
            if frame is None:
                raise ModelError(
                    "the rig has no camera called {!r}. The model reads {}; this rig offers "
                    "{}. `rigs/av3.txt` is the spec built for it.".format(
                        name,
                        ", ".join(self.config.camera_order),
                        ", ".join(sorted(frames)) or "nothing",
                    )
                )
            stacked.append(
                preprocess(frame, self.config.image_width, self.config.image_height)
            )
        return numpy.stack(stacked, axis=0)

    def observe(self, frames, agent):
        """One decision's frames and ego state into the ring.

        Call once per decision, before `predict`, exactly as `av3_base.predict` observes
        first - so the frame being predicted on is the newest in the ring rather than one
        decision stale.
        """
        self.history.observe(
            self.image_stack(frames), ego_state(agent, self.config.ego_velocity_scale)
        )

    def start_episode(self):
        self.history.reset()

    def predict(self, agent):
        """`(n_waypoints, 8)` float32, in the model's own frame.

        `observe` must have been called at least once - the ring is what carries the history,
        and a prediction on an empty one is refused rather than run on zeros.
        """
        if self._module is None:
            raise ModelError("predict() before load()")
        route = navigation(
            agent,
            self.config.n_route,
            self.config.route_spacing_m,
            self.config.route_max_offset_m,
        )
        return self._forward(route)

    def predict_with_navigation(self, agent, route):
        """`predict`, with the route replaced by a block the caller built.

        Only `tools/av3_probe.py` uses it, and only to ask the model a question a drive
        cannot: feed the same pictures and the same ego state with a synthetic arc bending
        one way and then the other, and see whether the output moves. A model that answers
        both with the same number is not reading the route at all, which no comparison
        against a recorded drive can distinguish from a route that is merely straight.
        """
        if self._module is None:
            raise ModelError("predict_with_navigation() before load()")
        route = numpy.asarray(route, dtype=numpy.float32)
        if route.shape != (self.config.n_route, ROUTE_FEATURE_DIM):
            raise ModelError(
                f"a navigation block must be ({self.config.n_route}, {ROUTE_FEATURE_DIM}); "
                f"got {route.shape}"
            )
        del agent  # the ego state is the ring's, not this call's
        return self._forward(route)

    def _forward(self, route):
        torch = self._torch
        self._navigation.copy_(
            torch.from_numpy(route[None]).to(dtype=torch.bfloat16, device=self.device)
        )
        sampled_images, sampled_ego = self.history.sampled()
        images = torch.from_numpy(numpy.ascontiguousarray(sampled_images)).to(
            dtype=torch.bfloat16, device=self.device
        )
        ego = torch.from_numpy(numpy.ascontiguousarray(sampled_ego)).to(
            dtype=torch.bfloat16, device=self.device
        )
        with torch.no_grad():
            output = self._module(images, self._navigation, ego)
        return output[0].detach().to("cpu", torch.float32).numpy()

    def modelv2_rows(self, prediction):
        return modelv2_rows(prediction)

    def waypoints(self, prediction):
        return waypoints(prediction)
