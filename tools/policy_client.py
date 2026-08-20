"""Drive the ego from a model hosted in another process, over localhost.

    from policy_client import RemotePolicy, SensorPack, sensor_config

Stage 7c. Stage 7b established that passing `[steering, throttle]` to `env.step` *is* driving
the ego, and left the slot `policy = IdmDriver(env)` for a model. This is the same slot filled
by something that is not in this process at all.

**Why a socket rather than importing the model.** MetaDrive's venv is **Python 3.8.20 with
numpy 1.24**, which most current ML tooling has dropped, and this repo's is 3.10 / numpy 2.2.
A model that runs on neither still has to drive. So the model stays wherever it already runs
and the drive calls it - and because `env.step` is the tick (nothing in the simulator moves
between two calls), a policy that takes a while makes the drive slow and never makes it wrong.
Anything that *does* run on 3.8 needs none of this: pass a plain callable instead.

**One measurement decides the whole design, and it is a trap rather than a tuning knob.** A
localhost round trip carrying the 161-float observation out and two floats back costs:

* **41.0 ms** with a stock `http.server` and a stock client - Nagle's algorithm meeting
  delayed ACK, on both ends, and nothing to do with Python being slow;
* **0.126 ms** with `TCP_NODELAY` set.

Against `env.step`'s own **0.954 ms** median, the second is free and the first makes every
drive 43x longer than the simulator that is doing the work. So the client sets `TCP_NODELAY`
and reuses one connection, and `examples/policy_server.py` sets `disable_nagle_algorithm`.
Miss either half and the cost comes back.

**It refuses a bad action rather than passing it on.** MetaDrive's `action_check` is off by
default (`base_env.py:69`) and `EnvInputPolicy` simply clips (`env_input_policy.py:36`), so a
model emitting [0, 1] instead of [-1, 1] silently loses the ability to brake, a saturating one
turns steering into a switch at full lock, and `NaN` is not clipped at all - `min(max(nan, -1),
1)` is `nan` in Python - and reaches `setSteeringValue`. Every one of those reads as a driving
problem. They are raised here instead.
"""

from __future__ import annotations

import base64
import contextlib
import http.client
import json
import os
import socket
import sys
import time
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from geodesy import aeqd_inverse, projection_origin  # noqa: E402

# What `--sensors` accepts. "observation" is not in the list because it is always sent - it is
# the argument the policy is called with.
SENSORS = ("imu", "gps", "camera", "depth", "semantic", "point-cloud")

# The sensors that cost a render context and hundreds of KB a step, against the numeric ones
# that cost about a kilobyte. Named so a caller can warn rather than discover.
HEAVY_SENSORS = ("camera", "depth", "semantic", "point-cloud")

_SENSOR_KEYS = {
    "camera": "rgb_camera",
    "depth": "depth_camera",
    "semantic": "semantic_camera",
    "point-cloud": "point_cloud",
}


class PolicyError(RuntimeError):
    """The hosted policy could not be reached, or answered with something undrivable."""


def sensor_config(names, camera_size=(320, 180), point_cloud_size=(200, 64)):
    """The MetaDrive `sensors` entries the named sensors need. `{}` when none do.

    `imu` and `gps` need nothing registered - both are read off the physics body and the
    scenario's own metadata.
    """
    from metadrive.component.sensors.depth_camera import DepthCamera
    from metadrive.component.sensors.point_cloud_lidar import PointCloudLidar
    from metadrive.component.sensors.rgb_camera import RGBCamera
    from metadrive.component.sensors.semantic_camera import SemanticCamera

    width, height = camera_size
    config = {}
    if "camera" in names:
        config["rgb_camera"] = (RGBCamera, width, height)
    if "depth" in names:
        config["depth_camera"] = (DepthCamera, width, height)
    if "semantic" in names:
        config["semantic_camera"] = (SemanticCamera, width, height)
    if "point-cloud" in names:
        # ego_centric=True: points arrive in the car's own frame, which is what a perception
        # model wants. False gives world coordinates, which move with the map.
        config["point_cloud"] = (PointCloudLidar, point_cloud_size[0], point_cloud_size[1], True)
    return config


def encode_array(array):
    """An ndarray as JSON: dtype, shape, and the raw buffer base64'd.

    Self-describing and reconstructed in one line -
    `np.frombuffer(base64.b64decode(d["b64"]), d["dtype"]).reshape(d["shape"])` - which matters
    because the server may not be Python and must not have to guess a layout.
    """
    import numpy

    array = numpy.ascontiguousarray(array)
    return {
        "dtype": str(array.dtype),
        "shape": list(array.shape),
        "b64": base64.b64encode(array.tobytes()).decode("ascii"),
    }


class SensorPack:
    """Reads the named sensors off a live env each step, ready to go on the wire.

    Refuses at construction for a sensor that is not registered, rather than sending nothing
    for it - a model quietly receiving no camera is the failure this exists to prevent. Use
    `sensor_config` to build the env's `sensors` entry from the same list.
    """

    def __init__(self, env, names=()):
        self._env = env
        self._names = tuple(names)
        unknown = [name for name in self._names if name not in SENSORS]
        if unknown:
            raise PolicyError(
                "unknown sensor(s): {}. Known: {}".format(", ".join(unknown), ", ".join(SENSORS))
            )
        registered = env.config.get("sensors", {})
        missing = [
            name
            for name in self._names
            if name in _SENSOR_KEYS and _SENSOR_KEYS[name] not in registered
        ]
        if missing:
            raise PolicyError(
                "{} requested but not registered on the env. Pass "
                "sensor_config({}) into the env's `sensors`, and note that a camera also "
                "needs a render context - offscreen or 3D.".format(
                    ", ".join(missing), list(self._names)
                )
            )
        self._origin = None
        self._shift = (0.0, 0.0)

    def reset(self):
        """Re-read the projection for the scenario that has just been loaded."""
        metadata = self._env.engine.data_manager.current_scenario["metadata"]
        self._origin = projection_origin(metadata.get("coordinate_system_wkt"))
        shift = metadata.get("old_origin_in_current_coordinate", [0.0, 0.0])
        self._shift = (float(shift[0]), float(shift[1]))

    def describe(self):
        """What the server is told once, in `/spec`, about what it is going to receive."""
        return {
            "sensors": list(self._names),
            "projection": {
                "kind": "azimuthal_equidistant_wgs84",
                "origin_latitude": self._origin[0] if self._origin else None,
                "origin_longitude": self._origin[1] if self._origin else None,
                "metadrive_to_projected_offset": list(self._shift),
            },
        }

    def __call__(self):
        if not self._names:
            return {}
        agent = self._env.agent
        engine = self._env.engine
        packed = {}

        if "imu" in self._names:
            body = agent.body
            packed["imu"] = {
                "velocity_mps": [float(v) for v in body.get_linear_velocity()],
                "angular_velocity_radps": [float(v) for v in body.getAngularVelocity()],
                "roll_rad": float(agent.roll),
                "pitch_rad": float(agent.pitch),
                "heading_rad": float(agent.heading_theta),
                "speed_mps": float(agent.speed),
            }

        if "gps" in self._names:
            if self._origin is None:
                self.reset()
            position = [float(v) for v in agent.origin.getPos()]
            easting = position[0] - self._shift[0]
            northing = position[1] - self._shift[1]
            latitude, longitude = (
                aeqd_inverse(self._origin[0], self._origin[1], easting, northing)
                if self._origin
                else (None, None)
            )
            packed["gps"] = {
                "latitude": latitude,
                "longitude": longitude,
                "altitude_m": position[2],
                "metadrive_position": position,
            }

        for name in self._names:
            key = _SENSOR_KEYS.get(name)
            if key is None:
                continue
            import numpy

            frame = engine.get_sensor(key).perceive(to_float=True, new_parent_node=agent.origin)
            packed[name] = encode_array(numpy.asarray(frame, dtype=numpy.float32))

        return packed


class RemotePolicy:
    """A hosted model, called as `policy(observation) -> [steering, throttle]`.

    Drops into the slot `IdmDriver` occupies in `examples/drive_with_a_policy.py`, and is what
    `tools/drive.py --agent-policy remote` uses.
    """

    def __init__(self, url, pack=None, timeout=30.0, step_seconds=0.1):
        parsed = urllib.parse.urlsplit(url if "//" in url else "//" + url, scheme="http")
        if parsed.scheme != "http":
            raise PolicyError(f"only http:// is supported, got {url!r}")
        self.host = parsed.hostname or "127.0.0.1"
        self.port = parsed.port or 80
        self.path = parsed.path.rstrip("/") or ""
        self._pack = pack
        self._timeout = timeout
        # What one `env.step` advances the simulator by, sent to the server in `/spec` so a
        # model integrating anything - a velocity, an accumulated error - is working in the
        # right units. Passed in rather than assumed: MetaDrive's 0.1 s is a default and
        # `tools/drive.py --step-hz` moves it.
        self._step_seconds = float(step_seconds)
        self._connection = None
        self.calls = 0
        self.seconds = 0.0
        self.sent_bytes = 0
        self.step = 0

    # -- transport ---------------------------------------------------------------------
    def _connect(self):
        connection = http.client.HTTPConnection(self.host, self.port, timeout=self._timeout)
        connection.connect()
        # Half of the 41 ms -> 0.126 ms; `examples/policy_server.py` sets the other half.
        connection.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self._connection = connection

    def _post(self, endpoint, payload, retry=True):
        if self._connection is None:
            self._connect()
        body = json.dumps(payload).encode("utf-8")
        self.sent_bytes += len(body)
        try:
            self._connection.request(
                "POST", self.path + endpoint, body, {"Content-Type": "application/json"}
            )
            response = self._connection.getresponse()
            data = response.read()
        except (http.client.HTTPException, OSError) as error:
            # A keep-alive connection the server has since closed fails on the *next* request
            # rather than when it is closed, so one reconnect is ordinary rather than an error.
            self.close()
            if retry:
                return self._post(endpoint, payload, retry=False)
            raise PolicyError(
                f"cannot reach the policy at "
                f"http://{self.host}:{self.port}{self.path}{endpoint} - "
                f"{type(error).__name__}: {error}. "
                "Is examples/policy_server.py running?"
            ) from error
        if response.status == 404:
            return None
        if response.status != 200:
            raise PolicyError(
                "policy returned HTTP {} for {}: {}".format(
                    response.status, endpoint, data[:200].decode("utf-8", "replace")
                )
            )
        return json.loads(data)

    def close(self):
        if self._connection is not None:
            with contextlib.suppress(OSError):
                self._connection.close()
            self._connection = None

    # -- the run -----------------------------------------------------------------------
    def spec(self, extra=None):
        """Tell the server once what it is about to be sent. A server may not implement it."""
        payload = {
            "observation": {
                "layout": [
                    {"name": "side_detector", "start": 0, "size": 12},
                    {"name": "vehicle_state", "start": 12, "size": 5},
                    {"name": "yaw_rate", "start": 17, "size": 1},
                    {"name": "lateral_in_lane", "start": 18, "size": 1},
                    {"name": "navigation", "start": 19, "size": 22},
                    {"name": "ray_lidar", "start": 41, "size": 120},
                ],
                "range": [0.0, 1.0],
                "note": "the layout holds for ScenarioEnv's default detectors; a run that "
                "turns one on or off changes every boundary after it, and an image "
                "observation replaces the vector entirely",
            },
            "action": {"names": ["steering", "throttle_brake"], "range": [-1.0, 1.0]},
            "step_seconds": self._step_seconds,
        }
        if self._pack is not None:
            payload.update(self._pack.describe())
        if extra:
            payload.update(extra)
        return self._post("/spec", payload)

    def start_episode(self, scenario_id=""):
        self.step = 0
        if self._pack is not None:
            self._pack.reset()
        self._post("/episode", {"scenario_id": str(scenario_id)})

    def __call__(self, observation=None):
        import numpy

        if isinstance(observation, dict):
            encoded = {
                key: encode_array(numpy.asarray(value, dtype=numpy.float32))
                for key, value in observation.items()
            }
        elif observation is None:
            encoded = None
        else:
            encoded = encode_array(numpy.asarray(observation, dtype=numpy.float32))

        payload = {"step": self.step, "observation": encoded}
        if self._pack is not None:
            payload["sensors"] = self._pack()

        started = time.perf_counter()
        reply = self._post("/act", payload)
        self.seconds += time.perf_counter() - started
        self.calls += 1
        self.step += 1

        if reply is None:
            raise PolicyError("the policy has no /act endpoint (HTTP 404)")
        return self._validated(reply.get("action"))

    @staticmethod
    def _validated(action):
        """Everything MetaDrive would have swallowed, refused here instead."""
        if action is None:
            raise PolicyError('the reply has no "action" field')
        try:
            values = [float(value) for value in action]
        except (TypeError, ValueError) as error:
            raise PolicyError(f"action is not two numbers: {action!r}") from error
        if len(values) != 2:
            raise PolicyError(
                f"action must be [steering, throttle_brake], got {len(values)} value(s)"
            )
        # Indexed rather than zipped: ruff asks for `zip(..., strict=)` and this file runs on
        # MetaDrive's Python 3.8, where that keyword does not exist.
        for index, name in enumerate(("steering", "throttle_brake")):
            value = values[index]
            if value != value or value in (float("inf"), float("-inf")):
                # NaN is the one MetaDrive does not even clip: min(max(nan, -1), 1) is nan,
                # and it reaches setSteeringValue.
                raise PolicyError(f"{name} is {value}, which MetaDrive does not clip")
            if not -1.0 <= value <= 1.0:
                raise PolicyError(
                    f"{name} is {value:.4f}, outside [-1, 1]. MetaDrive would clip it silently: an "
                    "action in [0, 1] cannot brake at all, and a saturating one makes "
                    "steering a switch at full lock."
                )
        return values
