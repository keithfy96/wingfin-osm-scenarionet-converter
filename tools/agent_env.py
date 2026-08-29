"""A ScenarioEnv an external policy can drive, a recorder for what it did, and a baseline driver.

    from agent_env import make_env, ActionRecorder, IdmDriver

Stage 7b. The ego is driven by `env.step([steering, throttle_brake])` - two floats in
[-1, 1], `base_vehicle.py:472-520` - and that is the whole of the connection. It is the same
socket stage 7a's keyboard uses: `ManualControlPolicy` **subclasses** `EnvInputPolicy`
(`manual_control_policy.py:37`), and the two differ only in where the two numbers originate.
So nothing here wires a model to a car. It removes the three obstacles between a training
loop and a correctly built map.

**`make_env` exists because the map settings are not defaults.** `tools/drive.py` works out
the terrain square, the semantic texture's resolution and the lane-line width inline from
argparse, and an outside loop should not have to reproduce eighty lines of that to get a map
whose roads do not stop halfway. This module **imports** those helpers rather than copying or
refactoring them - the cross-`tools/` import `drive.py:584` already uses for `signal_control`,
in the other direction. `drive.py` is not rearranged onto this: a working file is not worth
disturbing to save one duplicated composition.

Everything else is MetaDrive's own and passes straight through as `**overrides`, so
`agent_policy`, `discrete_action`, `action_check`, `manual_control`, `max_lateral_dist` and
`set_static` are all reachable without this module knowing they exist. In particular
**`agent_policy` is deliberately not set**: `ScenarioEnv` inherits `EnvInputPolicy` from
`base_env.py:55`, which is what makes the action argument reach the car.

Three things that fail silently and are worth reading before the first loop:

* **`action_check` is off** (`base_env.py:69`), so nothing verifies the action is in range -
  `EnvInputPolicy` simply clips it (`env_input_policy.py:36`). Output in [0, 1] instead of
  [-1, 1] and the car **cannot brake at all**, because `_apply_throttle_brake` only brakes
  below zero. Output far outside the range and every step saturates, so steering behaves like
  a switch at full lock. And `NaN` passes through unclipped - `min(max(nan, -1), 1)` is `nan`
  in Python - and reaches `setSteeringValue`. Pass `action_check=True` while developing.
* **`max_lateral_dist` (4 m, `scenario_env.py:84`) ends the episode** when the car is that far
  sideways of its *recorded* route. A policy that has not learned to steer crosses it within a
  second or two, and that is MetaDrive's rule rather than a fault in the map. Reachable here
  as an ordinary override, the same way `drive.py --max-lateral-dist` exposes it.
* **The route is still chosen by hand.** `ScenarioMapManager.reset` calls `get_sdc_track()`
  unconditionally and `TrajectoryNavigation`'s reference line *is* that tape, so `routes.json`
  and `convert --routes` stay exactly as they are. An agent-driven ego makes the tape the goal
  rather than the drive.
"""

from __future__ import annotations

import logging
import os
import sys

# The same directory-on-path import `drive.py:584` uses to reach `signal_control`. Both files
# run on MetaDrive's interpreter rather than this repo's, so neither is an installed module and
# there is no package to import through.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from drive import (  # noqa: E402
    DRIVABLE_AREA_EXTENSION_M,
    FALLBACK_MAX_TEXTURE,
    HEIGHT_SCALE,
    LINE_INTERVAL_M,
    LINE_WIDTH_M,
    _EgoPace,
    _keep_line_ends,
    _max_texture_dimension,
    _recorded_cruise_mps,
    _region_for,
    _set_line_width,
    _set_semantic_detail,
    data_step_seconds,
    sim_step_seconds,
    step_config,
)
from gpu_frames import to_host  # noqa: E402
from idm_driving import windowed_policy_class  # noqa: E402

# Re-exported rather than re-derived, so `drive.py`, `sensor_survey.py` and anything built on
# `make_env` share one definition of the two clocks. `step_config` returns MetaDrive config
# keys, so it can be splatted straight into `make_env(**step_config(hz))`.
__all__ = [
    "ActionRecorder",
    "IdmDriver",
    "data_step_seconds",
    "make_env",
    "sim_step_seconds",
    "step_config",
]


def make_env(
    dataset_dir,
    *,
    render=None,
    line_width_m=LINE_WIDTH_M,
    line_interval_m=LINE_INTERVAL_M,
    verbose=False,
    **overrides,
):
    """A `ScenarioEnv` over a converted dataset, with the terrain settings that fit an OSM map.

    `render` is `None` (no graphics at all, and so no terrain), `"3D"` for a window, or
    `"offscreen"` for the full terrain built into a buffer - the only way to check the view
    without a display. `**overrides` are MetaDrive config keys and win over everything chosen
    here, including `map_region_size`.

    `line_width_m` and `line_interval_m` are named rather than passed through `**overrides`
    because they are **not** MetaDrive config keys - there is no key that reaches either, which
    is why `drive.py` patches them - and MetaDrive rejects a config key it does not know.
    """
    from metadrive.component.sensors.rgb_camera import RGBCamera
    from metadrive.envs.scenario_env import ScenarioEnv
    from metadrive.scenario.utils import get_number_of_scenarios

    dataset = os.path.abspath(dataset_dir)

    region = overrides.get("map_region_size")
    if region is None:
        region, _, _ = _region_for(dataset)

    # The GL ceiling is only worth a whole subprocess when something is going to be textured.
    ceiling = _max_texture_dimension() if render is not None else None
    pixels_per_meter = max(1, (ceiling or FALLBACK_MAX_TEXTURE) // region)
    _set_semantic_detail(pixels_per_meter)
    if line_width_m > 0:
        _set_line_width(max(1, round(line_width_m * pixels_per_meter)), line_interval_m)
    # Unconditional, exactly as in `drive.py`: a painted line drawn short is a fault in the
    # renderer rather than a preference about how it looks.
    _keep_line_ends()

    config = {
        "data_directory": dataset,
        "num_scenarios": get_number_of_scenarios(dataset),
        "use_render": render == "3D",
        # `agent_policy` is left alone on purpose - see the module docstring.
        "map_region_size": region,
        "height_scale": HEIGHT_SCALE,
        "drivable_area_extension": DRIVABLE_AREA_EXTENSION_M,
        # Longer than any generated route; the caller's loop decides when to stop.
        "horizon": 100000,
        "show_logo": False,
        "show_fps": False,
        "log_level": logging.WARNING,
    }
    if render == "offscreen":
        config["image_observation"] = True
        config["sensors"] = {"rgb_camera": (RGBCamera, 320, 240)}
    config.update(overrides)
    # `sensors` is the one override that must merge rather than replace. `image_observation`
    # reads `config["sensors"][image_source]` (`image_obs.py:68`) with `image_source` defaulting
    # to `rgb_camera`, so a caller registering a depth camera and a point-cloud lidar - without
    # knowing that - loses the key the observation is built from and the env dies at
    # construction with `KeyError: 'rgb_camera' does not exist in existing config`. Merging
    # keeps the caller's own `rgb_camera` when it supplies one, so nothing is silently ignored.
    if "sensors" in overrides and render == "offscreen":
        # ...but only when the caller has not already named a camera of their own to build the
        # observation from. A rig registers seven cameras and points `image_source` at one of
        # them, and adding this eighth on top is a whole render pass every step feeding
        # nothing - measured on `junction-1`, `env.engine.sensors` held `rgb_camera` beside the
        # seven while `image_source` read `cam_front`. It also spends one of the very few
        # image buffers panda3d holds reliably (`camera_rig.MAX_IMAGE_BUFFERS`).
        source = (overrides.get("vehicle_config") or {}).get("image_source", "rgb_camera")
        if source in overrides["sensors"]:
            config["sensors"] = dict(overrides["sensors"])
        else:
            merged = {"rgb_camera": (RGBCamera, 320, 240)}
            merged.update(overrides["sensors"])
            config["sensors"] = merged

    if verbose:
        print(
            f"map region   {region} m; road texture {region * pixels_per_meter} px square "
            f"({pixels_per_meter} px/m)"
        )
    return ScenarioEnv(config)


class IdmDriver:
    """MetaDrive's own `TrajectoryIDMPolicy`, called from outside and fed through `env.step`.

    This is the baseline a model replaces, and it is taken rather than written for one reason
    nothing learned can supply: **it is the only way to test the plumbing.** `drive.py
    --agent-policy idm` already runs this class *inside* the engine, where the action passed to
    `env.step` is ignored. Running the same class from outside, over an env whose `agent_policy`
    is `EnvInputPolicy`, must produce the same drive - and if it does, the numbers being passed
    really are what moves the car. Any later difference is then the model rather than the wiring.

    **It reads the engine, not the observation** - `self.control_object`, its lidar, its
    navigation (`idm_policy.py:239-297`) - so it is not a `policy(obs)` in the gym sense. The
    `obs` argument is accepted and ignored so that the loop's signature is the one a model will
    use and the swap is one line.

    The policy is rebuilt whenever the ego is, because its two PID controllers carry state
    across steps and the route it follows is the episode's.
    """

    def __init__(self, env):
        self._env = env
        self._policy = None
        self._pace = None

    def _build(self):
        # `windowed_policy_class()` and not MetaDrive's own, because `drive.py --agent-policy
        # idm` drives that one: this class exists to be the *same* drive from outside, and two
        # different policies would make any difference between them unreadable.
        self._policy = windowed_policy_class()(
            # The same three arguments `agent_manager.py:48-50` passes when it builds this
            # policy itself, `current_sdc_route` included.
            self._env.agent, 0, self._env.engine.map_manager.current_sdc_route
        )
        # And the same pacing, for the same reason. Without it the ego is handed a flat
        # 40 km/h everywhere and runs wide of every junction turn.
        self._pace = _EgoPace(_recorded_cruise_mps(self._env.engine.data_manager.current_scenario))
        self._pace.start_episode(self._env, policy=self._policy)

    def __call__(self, observation=None):
        if self._policy is None or self._policy.control_object is not self._env.agent:
            self._build()
        self._pace.before_step(self._env)
        # `act`'s first parameter is `do_speed_control`; the agent manager passes the agent id
        # there (`policy.act(agent_id)`), which is a non-empty string and so always true. True
        # is the same thing said honestly.
        steering, throttle = self._policy.act(True)
        return [float(steering), float(throttle)]


class ActionRecorder:
    """`(observation, executed action)` pairs, one per `env.step()`, for imitation learning.

    It reads `vehicle.current_action` (`base_vehicle.py:974`) rather than the action that was
    passed in, so it captures what the car **actually executed, whatever produced it**: pointed
    at `drive.py --agent-policy manual` it records human demonstrations, pointed at a loop
    through `env.step` it records the model's. That is the whole reason it is built once here
    rather than inside the example, and the reason stage 7a comes first.

    Two policies write nothing meaningful into it, and both for the same reason - their `act`
    returns `None`, so `BaseVehicle.before_step` never appends (`base_vehicle.py:225-226`) and
    `current_action` keeps its initial `(0.0, 0.0)`:

    * `ReplayEgoCarPolicy`, which sets position directly from the tape. Measured: a 352-step
      `junction-1` replay records 352 actions and **every one of them is `[0, 0]`**. The file
      is written rather than refused, so `all_zero` below is what says it is not a drive.
    * `WaypointPolicy`, the other socket (`waypoint_policy.py:96`), where the action is a path
      rather than pedals.

    `record` is called *after* `env.step`, with the observation that produced the action.

    **The observation is two different shapes and the recorder has to know both.** With no
    graphics it is a flat 161-number vector. Under `--render offscreen` MetaDrive swaps the
    observation for `ImageStateObservation`, which returns `{"image", "state"}` -- a
    `(H, W, C, stack)` camera stack and a **41**-number state with no lidar block at all
    (`image_obs.py:40`). Ravelling that dict was what made `--record --render offscreen` die
    with `TypeError: float() argument must be a string or a real number, not 'dict'` until
    2026-08-24; the image half is kept now, which is the half worth having.

    **The image is stored as uint8 and nothing is lost by it.** `norm_pixel` makes the stack
    float32 in [0, 1] (`image_obs.py:75-77`), but the camera renders 8-bit and it is
    `BaseCamera._format`'s `ret / 255` that created the float in the first place
    (`base_camera.py:208-214`) -- Phase A measured `(v / 255 * 255).round()` returning all 256
    values exactly. So `round(x * 255)` is the same picture at a quarter of the size: 518 KB a
    step against 2.07 MB, which on a measured 352-step `junction-1` drive is 182.5 MB of frames
    rather than 729.9, and **84.4 MB** on disk once `savez_compressed` has had them.
    `image_scale` goes in the file so a reader gets the float back without guessing.

    **The whole stack is kept, not just the newest frame**, though the older two are the
    previous steps' frames and look redundant. They are not recoverable: under `--decision-hz`
    the frame gate returns the stack *unrolled* on a held step (`tools/frame_gate.py`,
    `image_obs.py:80-88`), so rebuilding a stack from a sequence of newest frames would shift
    frames the real stack never shifted.
    """

    def __init__(self, normalised_images=True, store_images=True):
        """`normalised_images` says the image half is float32 in [0, 1] and may be scaled.

        Decided by the caller because the recorder cannot see the env: `drive.py` reads
        `norm_pixel` and the image source off the config it built. It matters because a
        `PointCloudLidar` image source is float32 and **unbounded** (`image_obs.py:73-74`) --
        metres, not pixels -- and multiplying that by 255 would destroy it exactly as Phase A's
        `to_float=False` would have.

        `store_images=False` keeps the state half and drops the frames, for a caller that wants
        a small file. It is the flag, not the default: the frames are the reason an offscreen
        recording is worth making.
        """
        self.observations = []
        self.images = []
        self.actions = []
        self.episode_starts = []
        self.scenario_ids = []
        self.normalised_images = bool(normalised_images)
        self.store_images = bool(store_images)
        self._image_scale = None

    def start_episode(self, scenario_id=""):
        self.episode_starts.append(len(self.actions))
        self.scenario_ids.append(str(scenario_id))

    def record(self, observation, vehicle):
        import numpy

        state = observation
        image = None
        if isinstance(observation, dict):
            state = observation.get("state", ())
            image = observation.get("image")
        # An `.npz` is host bytes. Under `image_on_cuda` the offscreen observation's image half
        # is a CuPy array and `numpy.asarray` on one raises rather than copying, so the copy is
        # made here and named -- see `tools/gpu_frames`.
        self.observations.append(numpy.asarray(to_host(state), dtype=numpy.float32).ravel())
        if image is not None and self.store_images:
            self.images.append(self._as_uint8(numpy.asarray(to_host(image))))
        self.actions.append(numpy.asarray(vehicle.current_action, dtype=numpy.float32))

    def _as_uint8(self, image):
        """The frame as 8-bit, or untouched when it is not a normalised picture."""
        import numpy

        if not self.normalised_images or image.dtype == numpy.uint8:
            # Already 8-bit: nothing was divided, so nothing has to be multiplied back. That
            # is `norm_pixel` off, where MetaDrive hands over the render as it came.
            self._image_scale = 1.0
            return image
        self._image_scale = 255.0
        return numpy.rint(image * 255.0).astype(numpy.uint8)

    @property
    def image_scale(self):
        """Divide a stored image by this to get back the float the car was handed.

        What was *applied*, not what was asked for: a caller can pass `normalised_images=True`
        and then hand over frames that are already 8-bit, and a scale of 255 there would tell
        a reader to divide a picture that was never multiplied.
        """
        return 1.0 if self._image_scale is None else self._image_scale

    def __len__(self):
        return len(self.actions)

    def all_zero(self):
        """True when no policy ever wrote an action - a recording of a car nobody drove."""
        import numpy

        return bool(self.actions) and not numpy.stack(self.actions).any()

    def save_arrays(self):
        """Everything the file will hold, as arrays, or `{}` when nothing was recorded.

        Separate from `save` so the contents can be asserted without a path -- the recorder is
        the one half of this module pytest can reach, the env being out of its interpreter's
        reach entirely. **It consumes the frames**: `_stacked_images` drops the list once it has
        stacked it, so this is a once-per-recorder call, which is how it is used.
        """
        import numpy

        if not self.actions:
            return {}
        arrays = {
            "observations": numpy.stack(self.observations),
            "actions": numpy.stack(self.actions),
            "episode_starts": numpy.asarray(self.episode_starts, dtype=numpy.int64),
            "scenario_ids": numpy.asarray(self.scenario_ids),
        }
        images = self._stacked_images()
        if images is not None:
            arrays["images"] = images
            # In the file rather than inferred from the dtype, because uint8 is not proof of
            # a scale -- a camera whose `norm_pixel` is off is uint8 and already unscaled.
            arrays["image_scale"] = numpy.asarray(self.image_scale, dtype=numpy.float32)
        return arrays

    def save(self, path):
        """Write an `.npz` and return the shapes that went into it, or None if nothing did.

        Returns a dict rather than the old `(observations, actions)` pair, because there is a
        third array now and a silently-lengthened tuple is how a caller ends up printing the
        wrong one.
        """
        import numpy

        arrays = self.save_arrays()
        if not arrays:
            return None
        directory = os.path.dirname(os.path.abspath(path))
        if directory:
            os.makedirs(directory, exist_ok=True)
        numpy.savez_compressed(path, **arrays)
        images = arrays.get("images")
        return {
            "observations": arrays["observations"].shape,
            "actions": arrays["actions"].shape,
            "images": None if images is None else images.shape,
            "image_scale": self.image_scale,
        }

    def _stacked_images(self):
        """One array of every frame, and the list dropped before anything compresses it.

        **The frames are unavoidably held twice**, and that was measured before it was
        accepted. Filling a pre-allocated `numpy.empty` frame by frame and freeing each as it
        is copied -- which is what this was, for an afternoon -- gives exactly the same peak,
        because the destination is allocated whole before the first frame can be dropped:
        348.1 MB against 348.2 over 352 `junction-1` frames. No saving at all, so the loop
        that looked like one is not here.

        Releasing the list before `savez_compressed` runs **is** worth something, and only a
        little: 348.1 MB against 364.4 across the whole build-stack-compress span, 16 MB of
        364, because `savez_compressed` streams in chunks rather than building a second copy.
        Hence two lines rather than fifteen.
        """
        import numpy

        if not self.images:
            return None
        stacked = numpy.stack(self.images)
        self.images = []
        return stacked
