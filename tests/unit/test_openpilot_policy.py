"""`tools/openpilot_policy.py` - the translation between our socket and wing-sim's bridge.

Like `test_step_timing.py`, this reaches into `tools/` from this repo's 3.10. Nothing here
needs MetaDrive: the route arrives as a plain dict from the `route` sensor, and `StubBridge`
is a real socket, so the whole path from a route to an action is testable without an engine.

Every assertion is about a **sign or a frame**, because those are what fail silently. A
flipped `y` steers the car neatly into the wrong side of the road; a missing negation makes
the drive look like a badly tuned controller. MetaDrive would clip or swallow all of it.
"""

from __future__ import annotations

import json
import math
import socket
import struct
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))

from openpilot_policy import (  # noqa: E402
    DEFAULT_STEER_RATIO,
    HEADER_FMT,
    WAYPOINT_OFFSETS_S,
    BridgeError,
    OpenpilotDriver,
    StubBridge,
    ego_state,
    recv_msg,
    sample_route,
    send_msg,
    to_metadrive_action,
    waypoints_from_route,
)

SPACING_M = 2.0


def straight_route(points=25, spacing=SPACING_M):
    """A dead straight route ahead of the car, as `SensorPack` would send it."""
    return {
        "frame": "ego_x_ahead_y_left",
        "points_m": [[index * spacing, 0.0] for index in range(points)],
        "spacing_m": spacing,
        "longitudinal_m": 0.0,
        "lateral_m": 0.0,
        "remaining_m": points * spacing,
    }


def curving_route(radius=30.0, points=25, spacing=SPACING_M, leftwards=True):
    """An arc of `radius` bending left (or right), sampled at the same arc spacing."""
    sign = 1.0 if leftwards else -1.0
    samples = []
    for index in range(points):
        angle = index * spacing / radius
        samples.append([radius * math.sin(angle), sign * radius * (1.0 - math.cos(angle))])
    route = straight_route(points, spacing)
    route["points_m"] = samples
    return route


# -- the wire ---------------------------------------------------------------------------


def test_the_framing_matches_wing_sims_own_encoder():
    """A 4-byte big-endian length then UTF-8 JSON, byte for byte.

    The framing is copied rather than imported - wing-sim duplicates it on their own two
    sides for the same reason - so nothing but a test catches drift. This encodes the way
    `bridge_protocol.py` does and decodes with ours.
    """
    message = {"type": "step", "waypoints": [[1.0, -2.0, 0.5]], "ego": {"v_ego": 8.4}}
    payload = json.dumps(message).encode("utf-8")
    theirs = struct.pack(HEADER_FMT, len(payload)) + payload

    left, right = socket.socketpair()
    try:
        left.sendall(theirs)
        assert recv_msg(right) == message
        send_msg(right, message)
        assert left.recv(4) == struct.pack(HEADER_FMT, len(payload))
    finally:
        left.close()
        right.close()


def test_a_closed_connection_is_an_error_rather_than_an_empty_message():
    left, right = socket.socketpair()
    left.close()
    with pytest.raises(ConnectionError):
        recv_msg(right)
    right.close()


# -- the route becomes waypoints --------------------------------------------------------


def test_sampling_interpolates_between_the_route_points():
    route = straight_route()
    route["points_m"] = [[0.0, 0.0], [2.0, 1.0], [4.0, 0.0]]
    ahead, left = sample_route(route["points_m"], 2.0, 1.0)
    assert ahead == pytest.approx(1.0)
    assert left == pytest.approx(0.5)


def test_sampling_past_the_end_holds_the_destination():
    """Not an error: the last point is where the drive ends, and holding there is right."""
    route = straight_route(points=5)
    assert sample_route(route["points_m"], SPACING_M, 1000.0) == route["points_m"][-1]


def test_a_straight_route_asks_for_no_sideways_movement():
    waypoints = waypoints_from_route(straight_route(), speed_mps=10.0)
    assert len(waypoints) == len(WAYPOINT_OFFSETS_S)
    for x, y, t in waypoints:
        assert y == pytest.approx(0.0, abs=1e-9)
        assert x == pytest.approx(10.0 * t)


def test_a_left_hand_bend_gives_negative_y_because_the_bridge_is_right_positive():
    """The one assertion that catches a flipped frame.

    MetaDrive's ego frame is x ahead, y to the **left**; the bridge takes CARLA's, y to the
    **right**. A route bending left must therefore arrive at the bridge with negative `y`.
    Get this backwards and the car steers smoothly into the oncoming carriageway.
    """
    waypoints = waypoints_from_route(curving_route(leftwards=True), speed_mps=10.0)
    assert all(y < 0.0 for _, y, _ in waypoints)
    mirrored = waypoints_from_route(curving_route(leftwards=False), speed_mps=10.0)
    assert all(y > 0.0 for _, y, _ in mirrored)


def test_a_stopped_car_still_gets_waypoints_ahead_of_it():
    """`route_gt.py`'s floor, for its reason: at v = 0 every point collapses onto the car."""
    waypoints = waypoints_from_route(straight_route(), speed_mps=0.0)
    assert waypoints[-1][0] > 5.0
    pairs = zip(waypoints, waypoints[1:], strict=False)
    assert all(later[0] > earlier[0] for earlier, later in pairs)


# -- the ego state and the reply --------------------------------------------------------


def imu(speed=8.0, yaw_rate=0.0):
    return {
        "imu": {
            "speed_mps": speed,
            "angular_velocity_radps": [0.0, 0.0, yaw_rate],
            "velocity_mps": [speed, 0.0, 0.0],
            "roll_rad": 0.0,
            "pitch_rad": 0.0,
            "heading_rad": 0.0,
        }
    }


def test_the_column_angle_is_negated_on_the_way_to_the_bridge():
    """MetaDrive steers left-positive; the bridge negates a right-positive angle at ingress."""
    state = ego_state(imu(), steering=0.5, max_steering_deg=40.0, steer_ratio=12.0)
    assert state["steering_angle_deg"] == pytest.approx(-0.5 * 40.0 * 12.0)
    assert state["v_ego"] == pytest.approx(8.0)


def test_no_imu_is_refused_by_name_rather_than_read_as_a_stopped_car():
    with pytest.raises(BridgeError, match="imu"):
        ego_state({}, steering=0.0, max_steering_deg=40.0)


def test_the_reply_is_negated_and_the_two_pedals_become_one_signed_number():
    action = to_metadrive_action({"type": "control", "steer": 0.25, "throttle": 0.4, "brake": 0.0})
    assert action == pytest.approx([-0.25, 0.4])
    braking = to_metadrive_action({"type": "control", "steer": 0.0, "throttle": 0.0, "brake": 0.6})
    assert braking[1] == pytest.approx(-0.6)


@pytest.mark.parametrize(
    "reply",
    [
        {"type": "control", "steer": float("nan"), "throttle": 0.0, "brake": 0.0},
        {"type": "control", "steer": 1.5, "throttle": 0.0, "brake": 0.0},
        {"type": "control", "steer": 0.0, "throttle": float("inf"), "brake": 0.0},
        {"type": "error", "reason": "no planners"},
        {"type": "control"},
    ],
)
def test_everything_metadrive_would_have_swallowed_is_refused_here(reply):
    """The same set `RemotePolicy._validated` refuses, caught one hop earlier so the message
    names the bridge rather than the wire."""
    with pytest.raises(BridgeError):
        to_metadrive_action(reply)


# -- end to end, against a real socket ---------------------------------------------------


@pytest.fixture
def stub():
    bridge = StubBridge()
    yield bridge
    bridge.close()


def test_the_whole_path_steers_left_for_a_left_hand_bend(stub):
    """Route in, action out, over a real socket - and the two negations cancel.

    This is the assertion the stub exists for: an error anywhere in the chain (the y flip,
    the column angle, the reply negation) shows up as the car steering the wrong way, and
    nothing in MetaDrive would have said so.
    """
    driver = OpenpilotDriver(stub.host, stub.port, target_speed_mps=10.0)
    try:
        driver.episode({"vehicle": {"max_steering_deg": 40.0, "wheelbase_m": 2.47}})
        left = dict(imu(speed=8.0), route=curving_route(leftwards=True))
        steering, throttle_brake = driver.act(None, left, None)
        assert steering > 0.05, "a left bend must give left (positive) steering in MetaDrive"
        assert throttle_brake > 0.0, "below the target speed, so it should be accelerating"

        rightwards = dict(imu(speed=8.0), route=curving_route(leftwards=False))
        assert driver.act(None, rightwards, None)[0] < -0.05
    finally:
        driver.close()


def test_a_straight_route_is_driven_straight(stub):
    driver = OpenpilotDriver(stub.host, stub.port, target_speed_mps=10.0)
    try:
        driver.episode({"vehicle": {"max_steering_deg": 40.0, "wheelbase_m": 2.47}})
        steering, _ = driver.act(None, dict(imu(), route=straight_route()), None)
        assert steering == pytest.approx(0.0, abs=1e-9)
    finally:
        driver.close()


def test_over_the_target_speed_it_brakes(stub):
    driver = OpenpilotDriver(stub.host, stub.port, target_speed_mps=4.0)
    try:
        driver.episode({"vehicle": {"max_steering_deg": 40.0, "wheelbase_m": 2.47}})
        _, throttle_brake = driver.act(None, dict(imu(speed=14.0), route=straight_route()), None)
        assert throttle_brake < 0.0, "MetaDrive brakes below zero; [0, 1] cannot brake at all"
    finally:
        driver.close()


def test_a_drive_without_the_route_sensor_is_refused_by_name(stub):
    driver = OpenpilotDriver(stub.host, stub.port)
    try:
        driver.episode({"vehicle": {"max_steering_deg": 40.0, "wheelbase_m": 2.47}})
        with pytest.raises(BridgeError, match="--sensors imu,route"):
            driver.act(None, imu(), None)
    finally:
        driver.close()


def test_the_rate_mismatch_is_reported_rather_than_left_to_be_discovered(stub):
    """The bridge's `_DT_MDL` is 0.05. A 10 Hz dataset is twice that and nothing errors."""
    driver = OpenpilotDriver(stub.host, stub.port)
    notes = driver.spec({"step_seconds": 0.1, "sensors": ["imu", "route"]})
    assert any("_DT_MDL" in note for note in notes)
    assert not driver.spec({"step_seconds": 0.05, "sensors": ["imu", "route"]})


def test_missing_sensors_are_named_at_spec_time(stub):
    driver = OpenpilotDriver(stub.host, stub.port)
    notes = driver.spec({"step_seconds": 0.05, "sensors": ["gps"]})
    assert any("imu" in note for note in notes)
    assert any("route" in note for note in notes)


def test_the_default_steer_ratio_is_what_wing_sim_sends():
    assert DEFAULT_STEER_RATIO == 12.0
