"""Drive MetaDrive's ego from wing-sim's openpilot bridge.

    python examples/openpilot_server.py --backend stub --port 8642
    # then, in another terminal, from inside scripts/:
    ./drive.sh junction-1 -- --agent-policy remote --policy-url http://127.0.0.1:8642 \
        --sensors imu,route --render none

This fills the slot stage 7c left open. 7c built the socket - `tools/policy_client.py` on the
simulator's side, `examples/policy_server.py` on the model's - proved it lossless, and said
"still the model". `wing-sim/openpilot/` is a candidate for what goes there, and this module
is the translation between the two.

**What the bridge is.** A controller, not a driver. `bridge/zapeta/server.py` fronts
openpilot's real `plannerd` + `controlsd`; per tick it takes a *predicted path* and three ego
scalars and returns pedals. It never sees an image. So it needs a trajectory handed to it,
and the `route` sensor in `tools/policy_client.py` is what hands it one - the recorded ego
route in metres, which is the same object `TrajectoryNavigation` steers by.

**Three things that make the two ends fit, each read off the bridge rather than assumed:**

* **`carla_steer_curvature_gain: 0.0` selects a geometric branch whose output is already
  MetaDrive's.** `server.py:788` computes `-road_wheel_deg / max_steer_angle`, and
  `action[0] x max_steering` *is* the road-wheel angle in degrees (`base_vehicle.py:478`).
  Pass MetaDrive's own `max_steering` - 40 for the default vehicle - as `max_steer_angle` and
  no conversion is left over. The default path instead inverts an empirical CARLA curvature
  gain measured on Town10HD, which would mean nothing here.
* **Both ends negate, because MetaDrive is left-positive and CARLA is right-positive.** The
  bridge negates the column angle at ingress (`server.py:616`) and emits a right-positive
  steer, so the action is `-steer` and the waypoints' `y` is `-left`.
* **The waypoints need no model.** wing-sim ships `route_gt.py` for exactly this - route in,
  four points at t = 0.5/1.0/1.5/2.0 s using the car's *current* speed - and this rebuilds it
  against the `route` sensor rather than a CARLA route parquet.

**Four things that bite, all measured or read rather than guessed:**

* **`target_speed` defaults to 0**, which is a stop. `server.py:614` is
  `float(msg.get("target_speed", 0.0))`, so an omitted target is not "no opinion", it is
  "stand still". It has to be sent every tick.
* **`steer_ratio` in `init` is stored and never used.** The bridge divides by
  `self.CP.steerRatio` - the fork's own car params - on both ingress (`:646`) and egress
  (`:788`). The two cancel when ours matches, so a mismatch does not change the output scale;
  it mis-reports the current wheel angle to the rate limiter and the lag compensation.
  `DEFAULT_STEER_RATIO` is 12.0 because that is what wing-sim's own config sends.
* **The bridge is written for 20 Hz.** `_DT_MDL = 0.05` sets its lag compensation, its
  curvature-rate limit and its per-tick steer window, and `step_seconds` is the interval
  between two `act()` calls rather than between two `env.step`s - so **`--step-hz 100
  --decision-hz 20` is the answer**, and a better one than converting a 20 Hz dataset: the
  same 0.05 s control interval with ten times the physics under it. Measured on `mosque`:
  868 calls over 4337 steps, `arrive_dest=True`, completion 0.950, and no note from `spec`.
  A run at any other decision rate mis-scales the three limits above, and says so.
* **`accel_map.py` is CARLA pedal calibration**, not physics - two 8x11 tables from a
  "Town10HD calibration sweep on Tesla M3 @ 20 Hz sync", whose zero crossing is the CARLA
  Tesla's own -1.582 m/s^2 of drag. MetaDrive's car coasts at -0.364, so **every request to
  slow down more gently than that comes back as throttle** - 137 of 201 on `junction-1`, and
  the car ran away and left the road. `--longitudinal table` is the re-measurement,
  `tools/pedal_sweep.py` makes it and `tools/pedal_map.py` reads it. Steering is unaffected,
  because that path is geometric.

`StubBridge` speaks the same protocol with a pure-pursuit law and needs no fork, no Docker
and no SSH key. It is what proves the frame, the signs and the round trip before the real
bridge is available to blame - the role `--backend replay` plays for the 7c wire.
"""

from __future__ import annotations

import contextlib
import json
import math
import os
import socket
import struct
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pedal_map import PedalMap, PedalMapError  # noqa: E402

# ---------------------------------------------------------------------------------------
# Framing. Copied from `wing-sim/openpilot/bridge/bridge_protocol.py` rather than imported,
# for the reason wing-sim's own docs give for duplicating it on their two sides: the ends run
# different interpreters, and there is no import that would catch the drift. 4-byte
# big-endian length, then that many bytes of UTF-8 JSON. Strictly synchronous.
# `tests/unit/test_openpilot_policy.py` round-trips it so drift shows up as a test failure.
# ---------------------------------------------------------------------------------------

HEADER_FMT = "!I"
HEADER_SIZE = struct.calcsize(HEADER_FMT)

# What one `env.step` at 20 Hz is. The bridge's `_DT_MDL`, and the rate its per-tick limits
# are counted at; reported by `OpenpilotDriver` when the drive is running at anything else.
BRIDGE_DT_S = 0.05

# `route_gt.py`'s own grid and floor. 4 is in `run.sh`'s prebuilt acados menu ("4 16 20 32"),
# so the lateral MPC for it starts instantly rather than compiling on first use.
WAYPOINT_OFFSETS_S = (0.5, 1.0, 1.5, 2.0)
MIN_WAYPOINT_SPEED_MPS = 3.0

# See the module docstring: the bridge divides by its own `CP.steerRatio` whatever this says.
DEFAULT_STEER_RATIO = 12.0

# The Tesla envelope the bridge itself plans within (`bridge_constants.py`), used by the
# `accel` longitudinal mode below to turn an acceleration into MetaDrive's one signed number.
TESLA_ACCEL_MAX_MPS2 = 2.0
TESLA_ACCEL_MIN_MPS2 = -3.48

# `table` is the measured one and the only one that is a calibration; the other two are the
# two ways of being wrong that existed before there was a table. Both stay: `pedal` is what
# the bridge emits and what a CARLA consumer gets, so it has to remain reproducible, and
# `accel` is the sign-correct fallback when no table has been measured for a vehicle.
LONGITUDINAL_MODES = ("pedal", "accel", "table")


class BridgeError(RuntimeError):
    """The bridge could not be reached, or answered with something undrivable."""


def send_msg(sock, data):
    payload = json.dumps(data).encode("utf-8")
    sock.sendall(struct.pack(HEADER_FMT, len(payload)) + payload)


def recv_msg(sock):
    header = b""
    while len(header) < HEADER_SIZE:
        chunk = sock.recv(HEADER_SIZE - len(header))
        if not chunk:
            raise ConnectionError("connection closed")
        header += chunk
    length = struct.unpack(HEADER_FMT, header)[0]
    payload = b""
    while len(payload) < length:
        chunk = sock.recv(length - len(payload))
        if not chunk:
            raise ConnectionError("connection closed")
        payload += chunk
    return json.loads(payload)


# ---------------------------------------------------------------------------------------
# The translation
# ---------------------------------------------------------------------------------------


def sample_route(points, spacing_m, distance_m):
    """The route point `distance_m` along the path, interpolated between the samples.

    `points` is `sensors["route"]["points_m"]` - (ahead, left) in metres at a fixed arc
    spacing, index 0 at the car's own projection onto the route. Interpolated rather than
    rounded to the nearest index as `route_gt.py` does, which costs nothing and is exact at
    the offsets rather than within a metre of them.
    """
    if not points:
        raise BridgeError("the route sensor sent no points")
    index = max(0.0, float(distance_m) / float(spacing_m))
    low = int(index)
    if low >= len(points) - 1:
        # Past the end of the route. The last point is the destination, and holding there is
        # right: the car is meant to stop, not to be steered at something beyond the map.
        return list(points[-1])
    fraction = index - low
    ahead = points[low][0] + fraction * (points[low + 1][0] - points[low][0])
    left = points[low][1] + fraction * (points[low + 1][1] - points[low][1])
    return [ahead, left]


def waypoints_from_route(route, speed_mps, offsets=WAYPOINT_OFFSETS_S):
    """`[[x_forward, y_right, t], ...]` in the bridge's frame, from the `route` sensor.

    Constant-speed, the reasoning `route_gt.py` writes down: placing the points at the car's
    *own* projected positions keeps them reachable. Placing them where a recording says the
    car will be sends the MPC at a target it cannot make whenever the two speeds differ.

    The `y` flip is the whole of the frame conversion - MetaDrive's ego frame is x ahead and
    y to the **left**, the bridge's is CARLA's x ahead and y to the **right**.
    """
    points = route["points_m"]
    spacing = float(route.get("spacing_m", 2.0))
    speed = max(MIN_WAYPOINT_SPEED_MPS, float(speed_mps))
    waypoints = []
    for offset in offsets:
        ahead, left = sample_route(points, spacing, speed * offset)
        waypoints.append([float(ahead), -float(left), float(offset)])
    return waypoints


def ego_state(sensors, steering, max_steering_deg, steer_ratio=DEFAULT_STEER_RATIO):
    """The three scalars the bridge's `step` message wants.

    `steering` is the **last action this driver returned**, not a measurement: in a
    synchronous simulator a commanded steer is applied within the same tick, so the command
    is the wheel state and there are no actuator dynamics to model. wing-sim says the same
    thing about CARLA, and reads `get_control().steer` for it.

    Negated on the way out because the bridge takes a right-positive column angle and negates
    it back at ingress (`server.py:616`).
    """
    imu = sensors.get("imu")
    if not imu:
        raise BridgeError(
            "the bridge needs speed and yaw rate, which come from the imu sensor. "
            "Add --sensors imu,route to the drive."
        )
    column_deg = float(steering) * float(max_steering_deg) * float(steer_ratio)
    return {
        "v_ego": float(imu["speed_mps"]),
        "yaw_rate": float(imu["angular_velocity_radps"][2]),
        "steering_angle_deg": -column_deg,
    }


def to_metadrive_action(reply, longitudinal="pedal", speed_mps=0.0, table=None):
    """`{"steer", "throttle", "brake"}` -> `[steering, throttle_brake]`, both in [-1, 1].

    The steer is negated because the bridge emits CARLA's right-positive normalised value,
    and it needs nothing else: `carla_steer_curvature_gain: 0.0` selects a geometric branch
    whose output is already `road_wheel_deg / max_steer_angle`. Measured against the real
    bridge - a 124.95 deg column angle came back as steer 0.2603, which is 124.95 / 12 / 40
    exactly. That half of the fit is right.

    **The longitudinal half is not, and `longitudinal` is which of the two wrongs to take.**
    MetaDrive wants one signed number, braking below zero (`base_vehicle.py:494`), which is
    why an action in [0, 1] cannot brake at all.

    * `pedal` is what the bridge emits and what a CARLA consumer uses: `throttle - brake`,
      produced by `accel_map.accel_to_carla` from two 8x11 tables measured in a "Town10HD
      calibration sweep on Tesla M3 @ 20 Hz sync". **Its zero crossing is not at zero.**
      Measured off a real `junction-1` drive: `accel_cmd` -1.91 gives brake 0.042, -1.65
      gives nothing at all, and **-1.55 gives throttle 0.204**, rising monotonically to 0.43.
      So every request to slow down gently - the commonest request there is - comes back as
      a fifth to a half of full throttle. On a route whose trajectory carries no speed intent
      (`waypoints_from_route` is `route_gt.py`'s constant-speed model, by construction) there
      is nothing opposing that, and the car ran away from 13.9 to 20.5 m/s and left the road.
    * `accel` ignores the two pedals and normalises `accel_cmd`, which is in m/s^2 and owes
      nothing to any vehicle, by the Tesla envelope the bridge itself plans within. **This is
      not a calibration either** - MetaDrive's `action[1]` is engine force and brake force,
      not acceleration, so the magnitude is only roughly right. What it is is *sign*-correct
      and unit-consistent, which on this simulator the pedal map is not.
    * `table` takes the same `accel_cmd` and looks it up in a pedal map **measured on this
      car**, by `tools/pedal_sweep.py`, which is what the other two are standing in for. It
      is the only one of the three that is a calibration, and it needs `speed_mps`: MetaDrive
      cuts the engine entirely above `max_speed_km_h`, so what a throttle is worth depends on
      how fast the car is already going, and nothing else in this module needs to know that.

    Refused here rather than at `RemotePolicy._validated`, so a bad number names the bridge
    that produced it instead of the wire that carried it.
    """
    if longitudinal not in LONGITUDINAL_MODES:
        raise BridgeError(f"unknown longitudinal mode {longitudinal!r}")
    if not isinstance(reply, dict):
        raise BridgeError(f"the bridge replied with {type(reply).__name__}, not an object")
    if reply.get("type") == "error" or "steer" not in reply:
        raise BridgeError(f"the bridge did not reply with a control: {str(reply)[:200]}")
    steer = -float(reply["steer"])
    if longitudinal in ("accel", "table"):
        if "accel_cmd" not in reply:
            raise BridgeError(
                f"--longitudinal {longitudinal} needs `accel_cmd`, which this reply does not "
                "carry. The stub answers in pedals only; use --longitudinal pedal with it."
            )
        accel = float(reply["accel_cmd"])
        # Before the clip, not after: `min(1.0, nan)` is 1.0 in Python, so clipping first
        # would turn a NaN into full throttle rather than into the refusal below.
        if accel != accel or accel in (float("inf"), float("-inf")):
            raise BridgeError(f"the bridge returned accel_cmd = {accel}")
        if longitudinal == "table":
            if table is None:
                raise BridgeError(
                    "--longitudinal table needs a pedal map, and none was loaded. Measure "
                    "one with ./scripts/pedal-sweep.sh, or use --longitudinal accel."
                )
            throttle_brake = table.pedal_for(accel, speed_mps)
        else:
            envelope = TESLA_ACCEL_MAX_MPS2 if accel >= 0.0 else -TESLA_ACCEL_MIN_MPS2
            throttle_brake = max(-1.0, min(1.0, accel / envelope))
    else:
        throttle_brake = float(reply.get("throttle", 0.0)) - float(reply.get("brake", 0.0))
    for name, value in (("steering", steer), ("throttle_brake", throttle_brake)):
        if value != value or value in (float("inf"), float("-inf")):
            raise BridgeError(f"the bridge returned {name} = {value}, which MetaDrive does "
                              "not clip - it reaches setSteeringValue as it stands")
        if not -1.0 <= value <= 1.0:
            raise BridgeError(
                f"the bridge returned {name} = {value:.4f}, outside [-1, 1]. Its `steer` is "
                "meant to be normalised already; check `max_steer_angle` in the init message."
            )
    return [steer, throttle_brake]


# ---------------------------------------------------------------------------------------
# The connection
# ---------------------------------------------------------------------------------------


class BridgeConnection:
    """One TCP connection to the bridge: `init` once, then a `step` per tick.

    A connection is what the bridge scopes its per-episode state to
    (`_reset_per_connection_state`), so a new scenario gets a new connection rather than a
    reset message - there is no reset message.
    """

    def __init__(self, host="127.0.0.1", port=5558, connect_timeout=60.0, step_timeout=0.5):
        self.host = host
        self.port = int(port)
        self._connect_timeout = float(connect_timeout)
        self._step_timeout = float(step_timeout)
        self._sock = None
        self.steps = 0

    def connect(self, **init_fields):
        """Connect and send `init`, returning once the bridge answers `ready`."""
        self.close()
        try:
            sock = socket.create_connection((self.host, self.port), self._connect_timeout)
        except OSError as error:
            raise BridgeError(
                f"cannot reach the bridge at {self.host}:{self.port} - "
                f"{type(error).__name__}: {error}"
            ) from error
        # The same 41 ms -> 0.126 ms `tools/policy_client.py` documents, one layer further
        # out. This socket carries a round trip per simulator tick too.
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.settimeout(self._connect_timeout)

        message = {
            "type": "init",
            "steer_ratio": DEFAULT_STEER_RATIO,
            "max_steer_angle": 40.0,
            "n_waypoints": len(WAYPOINT_OFFSETS_S),
            "zapeta_longitudinal_mode": "blended_except_creep",
            "telemetry_mpc_outputs": False,
            # 0 is what selects the geometric steer branch. The whole fit turns on it.
            "carla_steer_curvature_gain": 0.0,
            "carla_steer_understeer_coef": 0.0,
        }
        message.update(init_fields)
        send_msg(sock, message)
        reply = recv_msg(sock)
        if reply.get("type") != "ready":
            sock.close()
            raise BridgeError(f"the bridge did not become ready: {str(reply)[:200]}")
        sock.settimeout(self._step_timeout)
        self._sock = sock
        self.steps = 0
        return message

    def step(self, payload):
        if self._sock is None:
            raise BridgeError("step() before connect()")
        try:
            send_msg(self._sock, payload)
            reply = recv_msg(self._sock)
        except (OSError, ConnectionError) as error:
            raise BridgeError(
                f"the bridge stopped answering at step {self.steps} - "
                f"{type(error).__name__}: {error}"
            ) from error
        self.steps += 1
        return reply

    def close(self):
        if self._sock is None:
            return
        # Both guarded: a bridge that has already died must not turn teardown into an error.
        with contextlib.suppress(OSError):
            send_msg(self._sock, {"type": "shutdown"})
        with contextlib.suppress(OSError):
            self._sock.close()
        self._sock = None


# ---------------------------------------------------------------------------------------
# A stand-in bridge, so the path can be proven without the fork
# ---------------------------------------------------------------------------------------


class StubBridge:
    """A real socket speaking the real protocol, with a pure-pursuit law behind it.

    Not a mock: it binds, frames, inits and replies exactly as the bridge does, so it
    exercises the wire, the frame and both sign conventions. What it does **not** do is
    anything openpilot does - no MPC, no lag compensation, no longitudinal state machine.
    It is here to answer "is the plumbing right" before there is a fork to blame, and a
    drive that stays on the road under it is that answer.
    """

    def __init__(self, host="127.0.0.1", port=0):
        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind((host, port))
        self._server.listen(4)
        self.host, self.port = self._server.getsockname()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._running = True
        self._thread.start()

    def _serve(self):
        while self._running:
            try:
                connection, _ = self._server.accept()
            except OSError:
                return
            threading.Thread(target=self._session, args=(connection,), daemon=True).start()

    def _session(self, connection):
        connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        try:
            message = recv_msg(connection)
            if message.get("type") != "init":
                send_msg(connection, {"type": "error", "reason": "expected init"})
                return
            settings = {
                "max_steer_angle": float(message.get("max_steer_angle", 40.0)),
                "wheelbase_m": float(message.get("wheelbase_m", 2.5)),
            }
            send_msg(connection, {"type": "ready"})
            while self._running:
                message = recv_msg(connection)
                kind = message.get("type")
                if kind == "shutdown":
                    return
                if kind != "step":
                    send_msg(connection, {"type": "error", "reason": f"unknown type {kind}"})
                    continue
                send_msg(connection, self.control(message, settings))
        except (OSError, ConnectionError, ValueError):
            return
        finally:
            with contextlib.suppress(OSError):
                connection.close()

    @staticmethod
    def control(message, settings):
        """Pure pursuit on the waypoints, answered in the bridge's own conventions."""
        waypoints = message.get("waypoints") or []
        ego = message.get("ego") or {}
        speed = float(ego.get("v_ego", 0.0))
        target = float(message.get("target_speed", 0.0))
        if not waypoints:
            # What the bridge itself does with an empty list: a hard stop, built directly.
            return {"type": "control", "steer": 0.0, "throttle": 0.0, "brake": 1.0}

        # A lookahead that grows with speed, floored so a stopped car still has one.
        lookahead = max(6.0, 0.6 * speed)
        chosen = waypoints[-1]
        for waypoint in waypoints:
            if math.hypot(waypoint[0], waypoint[1]) >= lookahead:
                chosen = waypoint
                break
        ahead, right = float(chosen[0]), float(chosen[1])
        chord = ahead * ahead + right * right
        # Left-positive curvature, which is openpilot's sign and MetaDrive's.
        curvature = 0.0 if chord < 1e-6 else -2.0 * right / chord
        road_wheel_deg = math.degrees(math.atan(settings["wheelbase_m"] * curvature))
        # And out in CARLA's right-positive normalised form, exactly as `server.py:788`.
        steer = max(-1.0, min(1.0, -road_wheel_deg / settings["max_steer_angle"]))

        accel = max(-3.0, min(2.0, 0.6 * (target - speed)))
        throttle = max(0.0, min(1.0, accel / 2.0))
        brake = max(0.0, min(1.0, -accel / 3.0))
        return {
            "type": "control",
            "steer": steer,
            "throttle": throttle,
            "brake": brake,
            "stub": True,
            "lookahead_m": lookahead,
            "curvature": curvature,
        }

    def close(self):
        self._running = False
        with contextlib.suppress(OSError):
            self._server.close()


# ---------------------------------------------------------------------------------------
# The driver the HTTP server calls
# ---------------------------------------------------------------------------------------


class OpenpilotDriver:
    """`/spec`, `/episode` and `/act` on one side; `init`, `step` and `shutdown` on the other.

    Holds the two things neither end sends every tick: the car's steering geometry, which
    arrives once per episode because `/spec` is sent before the ego exists, and the last
    action, which is what the bridge is told the wheel is at.
    """

    def __init__(self, host="127.0.0.1", port=5558, target_speed_mps=10.0,
                 steer_ratio=DEFAULT_STEER_RATIO, offsets=WAYPOINT_OFFSETS_S,
                 longitudinal="pedal", pedal_map=None):
        self.bridge = BridgeConnection(host, port)
        self.target_speed_mps = float(target_speed_mps)
        self.steer_ratio = float(steer_ratio)
        self.offsets = tuple(offsets)
        if longitudinal not in LONGITUDINAL_MODES:
            raise BridgeError(f"unknown longitudinal mode {longitudinal!r}")
        self.longitudinal = longitudinal
        # A path, an already-loaded PedalMap, or None. Loaded here rather than in the server
        # so that a bad table is refused at construction rather than on the first step of a
        # drive that has already built a map and opened a window.
        # Re-raised as a BridgeError so that everything this module can fail with is one
        # type: `examples/openpilot_server.py` and `tools/drive.py` both catch that already,
        # and a second exception class would reach them as a traceback.
        if isinstance(pedal_map, str):
            try:
                pedal_map = PedalMap.load(pedal_map)
            except PedalMapError as error:
                raise BridgeError(str(error)) from error
        if longitudinal == "table" and pedal_map is None:
            raise BridgeError(
                "--longitudinal table needs a pedal map. Measure one with:\n"
                "    ./scripts/pedal-sweep.sh junction-1"
            )
        self.pedal_map = pedal_map
        self.max_steering_deg = 40.0
        self.wheelbase_m = 2.5
        self.step_seconds = None
        self.last_action = [0.0, 0.0]
        self.last_reply = {}
        # The speed the last action was chosen at. Only `table` reads it while driving; it is
        # kept for every mode so a telemetry line means the same thing whichever was used.
        self.last_v_ego = 0.0
        self.notes = []

    def spec(self, payload):
        """Told once, before the ego exists. Only the rate is knowable here, and it matters."""
        self.notes = []
        self.step_seconds = payload.get("step_seconds")
        sensors = payload.get("sensors") or []
        for needed in ("imu", "route"):
            if needed not in sensors:
                self.notes.append(
                    f"the drive is not sending `{needed}`; add --sensors imu,route"
                )
        if self.step_seconds and abs(self.step_seconds - BRIDGE_DT_S) > 1e-9:
            ratio = self.step_seconds / BRIDGE_DT_S
            self.notes.append(
                f"the drive steps every {self.step_seconds:.3f} s and the bridge is written "
                f"for {BRIDGE_DT_S:.2f} s (_DT_MDL). Its lag compensation and curvature-rate "
                f"limit are counted per tick, so they are scaled by {ratio:.1f}x here. "
                "--decision-hz 20 is what matches it, at any --step-hz that 20 divides."
            )
        return self.notes

    def episode(self, payload):
        """A new scenario: read the car's geometry, then a fresh connection and a fresh init."""
        vehicle = payload.get("vehicle") or {}
        self.max_steering_deg = float(vehicle.get("max_steering_deg", self.max_steering_deg))
        self.wheelbase_m = float(vehicle.get("wheelbase_m", self.wheelbase_m))
        self.last_action = [0.0, 0.0]
        # Once an episode, not once a step: the forces are sampled when the vehicle is built
        # (`pg_space.py:239-240`) and do not move while it drives. `/episode` is the first
        # message that has a car to describe at all - `/spec` is sent before `env.reset()`.
        if self.pedal_map is not None:
            self.notes = list(self.pedal_map.vehicle_notes(vehicle))
        return self.bridge.connect(
            max_steer_angle=self.max_steering_deg,
            steer_ratio=self.steer_ratio,
            # Ours, not the protocol's. The real bridge ignores what it does not know; the
            # stub needs a wheelbase to turn a curvature into a wheel angle.
            wheelbase_m=self.wheelbase_m,
        )

    def act(self, observation, sensors, spec):
        del observation, spec  # the bridge reads the route and the imu, never the RL vector
        route = sensors.get("route")
        if not route:
            raise BridgeError(
                "the bridge needs the route, which the drive is not sending. "
                "Add --sensors imu,route."
            )
        state = ego_state(sensors, self.last_action[0], self.max_steering_deg, self.steer_ratio)
        payload = {
            "type": "step",
            "waypoints": waypoints_from_route(route, state["v_ego"], self.offsets),
            "ego": state,
            # Never omitted: `server.py:614` reads a missing target as 0.0, which is a stop.
            "target_speed": self.target_speed_mps,
            "creep_state": "idle",
        }
        self.last_v_ego = state["v_ego"]
        self.last_reply = self.bridge.step(payload)
        self.last_action = to_metadrive_action(
            self.last_reply, self.longitudinal, state["v_ego"], self.pedal_map
        )
        return self.last_action

    def close(self):
        self.bridge.close()
