"""`tools/idm_driving.py` - the policy both the ego and the traffic cars drive with.

Like `test_traffic_manager.py`, this reaches into `tools/` from this repo's 3.10 while the
module itself runs on MetaDrive's 3.8. MetaDrive is an opt-in dependency group (`--group
sim`), so `windowed_policy_class()` cannot be called against the real base class here. It is
called against a **stub** base carrying MetaDrive's own `acceleration` and `desired_gap`
verbatim (`idm_policy.py:304-321`), which is enough to assert the two things the subclass
actually changes: the exponent, and *when* the command is recomputed.
"""

import ast
import math
import sys
import types
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))

import idm_driving  # noqa: E402


class _StubLidar:
    def get_surrounding_objects(self, _control_object):
        return []


class _StubCar:
    def __init__(self, speed_km_h=0.0, position=(0.0, 0.0)):
        self.speed_km_h = speed_km_h
        self.position = position
        self.heading_theta = 0.0
        self.heading = (1.0, 0.0)
        self.velocity_km_h = (speed_km_h, 0.0)
        self.lidar = _StubLidar()


class _StubLane:
    length = 100.0
    segment_property = [
        {"start_point": (0.0, 0.0), "end_point": (1.0, 0.0), "length": 1.0},
        {"start_point": (1.0, 0.0), "end_point": (2.0, 0.0), "length": 1.0},
    ]
    end = (2.0, 0.0)

    def heading_theta_at(self, _longitudinal):
        return 0.0


class _StubPID:
    def get_result(self, error):
        return -error

    def reset(self):
        pass


def _install_fake_metadrive(monkeypatch):
    """The four names `windowed_policy_class` imports, with IDM's real longitudinal law."""

    class TrajectoryIDMPolicy:
        ACC_FACTOR = 1.0
        DEACC_FACTOR = -5
        DELTA = 10.0
        DISTANCE_WANTED = 10.0
        TIME_WANTED = 1.5
        NORMAL_SPEED = 40
        IDM_MAX_DIST = 20

        def __init__(self, control_object, traj_to_follow):
            self.control_object = control_object
            self.traj_to_follow = traj_to_follow
            self.routing_target_lane = traj_to_follow
            self.target_speed = self.NORMAL_SPEED
            self.lateral_pid = _StubPID()
            self.last_action = [0, 0]
            self.action_info = {}

        # Verbatim from metadrive/policy/idm_policy.py:304-321.
        def acceleration(self, front_obj, dist_to_front):
            ego = self.control_object
            target = self.target_speed or 1e-6
            acceleration = self.ACC_FACTOR * (
                1 - math.pow(max(ego.speed_km_h, 0) / target, self.DELTA)
            )
            if front_obj:
                speed_diff = self.desired_gap(ego, front_obj) / (dist_to_front or 1e-2)
                acceleration -= self.ACC_FACTOR * (speed_diff**2)
            return acceleration

        def desired_gap(self, ego, front_obj):
            ab = -self.ACC_FACTOR * self.DEACC_FACTOR
            dv = ego.speed_km_h - front_obj.speed_km_h
            return self.DISTANCE_WANTED + ego.speed_km_h * self.TIME_WANTED + (
                ego.speed_km_h * dv / (2 * math.sqrt(ab))
            )

    class FrontBackObjects:
        found = None

        @classmethod
        def get_find_front_back_objs_single_lane(cls, *_args, **_kwargs):
            found = cls.found

            class _Result:
                def front_object(self):
                    return found

            return _Result()

    def _module(name, **attributes):
        module = types.ModuleType(name)
        for key, value in attributes.items():
            setattr(module, key, value)
        monkeypatch.setitem(sys.modules, name, module)
        return module

    _module("metadrive")
    _module("metadrive.component")
    _module("metadrive.component.vehicle")
    _module("metadrive.component.vehicle.PID_controller", PIDController=lambda *a: _StubPID())
    _module("metadrive.policy")
    _module(
        "metadrive.policy.idm_policy",
        FrontBackObjects=FrontBackObjects,
        TrajectoryIDMPolicy=TrajectoryIDMPolicy,
    )
    _module("metadrive.utils")
    _module("metadrive.utils.math", wrap_to_pi=lambda x: x)
    monkeypatch.setattr(idm_driving, "_POLICY_CLASS", None)
    return FrontBackObjects


@pytest.fixture
def policy_class(monkeypatch):
    front_back = _install_fake_metadrive(monkeypatch)
    cls = idm_driving.windowed_policy_class()
    cls._test_front_back = front_back
    yield cls
    idm_driving._POLICY_CLASS = None


def _policy(policy_class, speed_km_h, target_km_h):
    policy = policy_class(_StubCar(speed_km_h=speed_km_h), _StubLane())
    policy.target_speed = target_km_h
    return policy


# --- the exponent --------------------------------------------------------------------------


def test_the_velocity_exponent_is_four_not_ten(policy_class) -> None:
    """MetaDrive ships `DELTA = 10`, which is a relay rather than a controller: at 10% over
    the target it asks for -1.59, which the action clamp makes a full brake, and at 10% under
    it asks for +0.65. A car handed a 4-8 km/h corner speed by the profile can then only
    oscillate between stopped and over. MetaDrive's own `DELTA_RANGE` is [3.5, 4.5]."""
    assert policy_class.DELTA == 4.0

    over = _policy(policy_class, speed_km_h=8.8, target_km_h=8.0).acceleration(None, None)
    under = _policy(policy_class, speed_km_h=7.2, target_km_h=8.0).acceleration(None, None)
    assert -1.0 < over < 0.0, "10% over target must be a correction, not a clipped full brake"
    assert 0.0 < under < 0.5, "10% under target must not be two thirds of full throttle"


# --- when the command is recomputed --------------------------------------------------------


def test_the_command_is_recomputed_on_a_step_that_does_not_search(policy_class) -> None:
    """The whole of the start-stop. Stock `act` (`idm_policy.py:487-489`) returns
    `self.last_action[-1]` on every step it does not run the front-object search, and with
    `IDM_ACT_BATCH_SIZE` at 5 that latches one saturated pedal for 0.4 s at 10 Hz - longer
    than it takes to stop a car at corner speeds, after which `base_vehicle.py`'s 0.01 m/s
    DEADZONE latches the brake hard until a positive throttle arrives.

    What is expensive is the lidar sweep, not the arithmetic, so the search stays staggered
    and the command does not.
    """
    policy = _policy(policy_class, speed_km_h=20.0, target_km_h=8.0)
    braking = policy.act(True)[1]
    assert braking < 0.0

    policy.control_object.speed_km_h = 4.0
    coasting = policy.act(False)[1]
    assert coasting > 0.0, "a car now under its target must not still be holding the brake"


def test_the_expensive_search_still_runs_only_when_asked(policy_class) -> None:
    """The stagger is kept: `lidar.get_surrounding_objects` and the lane test in
    `get_find_front_back_objs_single_lane` are what cost, and `docs/reference/live-traffic.md`
    prices the speed half of IDM at a fifth of the cars per step."""
    policy = _policy(policy_class, speed_km_h=10.0, target_km_h=20.0)
    calls = []
    policy.control_object.lidar.get_surrounding_objects = lambda obj: calls.append(obj) or []

    policy.act(True)
    policy.act(False)
    policy.act(False)
    assert len(calls) == 1


def test_a_remembered_car_in_front_is_re_measured_rather_than_latched(policy_class) -> None:
    """The gap term is recomputed against where the front car *is*, not against the distance
    it was at when the search last ran. A leader pulling away must stop being a brake before
    the next search, or the follower spends up to 0.4 s braking for a car that has gone."""
    policy = _policy(policy_class, speed_km_h=20.0, target_km_h=20.0)
    leader = _StubCar(speed_km_h=20.0, position=(3.0, 0.0))
    policy_class._test_front_back.found = leader

    close = policy.act(True)[1]
    leader.position = (60.0, 0.0)
    far = policy.act(False)[1]
    assert far > close, "the gap term must follow the car it is measured against"


def test_a_front_car_that_has_been_cleared_is_forgotten_not_braked_for(policy_class) -> None:
    """A retired traffic car or a light that went out is destroyed between the search and the
    steps that follow it. Reading its position then raises, and braking for a ghost until the
    next search would be a stop with nothing in front of the car."""

    class _Gone:
        speed_km_h = 0.0

        @property
        def position(self):
            raise RuntimeError("destroyed")

    policy = _policy(policy_class, speed_km_h=10.0, target_km_h=20.0)
    policy_class._test_front_back.found = _Gone()
    policy.act(True)

    policy_class._test_front_back.found = None
    assert policy._front_obj is None
    assert policy.act(False)[1] > 0.0


# --- the reference window ------------------------------------------------------------------


def test_the_ego_gets_a_route_window_without_a_manager_to_prime_it(policy_class) -> None:
    """`tools/traffic.py` primes every car it spawns because it holds the localised route
    already. The ego has no manager to do that, and the whole point of moving this class out
    of `traffic.py` was that `--agent-policy idm` should get the same driving."""
    policy = _policy(policy_class, speed_km_h=10.0, target_km_h=20.0)
    along, lateral = policy.route_coordinates((1.0, 0.5))
    assert along == pytest.approx(1.0, abs=0.05)
    assert lateral == pytest.approx(-0.5, abs=0.05), "lateral is positive to the RIGHT"


def test_the_heading_pid_has_no_integral() -> None:
    """The stock heading PID's integral accumulates for ever and is never reset. Read at
    source because the class cannot be built without MetaDrive; the measurement behind it is
    in `docs/reference/live-traffic.md`."""
    source = (REPO / "tools" / "idm_driving.py").read_text(encoding="utf-8")
    policy = next(
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ClassDef) and node.name == "WindowedTrajectoryIDMPolicy"
    )
    init = next(
        node
        for node in ast.walk(policy)
        if isinstance(node, ast.FunctionDef) and node.name == "__init__"
    )
    assert "PIDController(1.2, 0.0, 3.5)" in ast.unparse(init)
