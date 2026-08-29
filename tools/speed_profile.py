"""How fast a car may be at each point of a route, from the curvature of the route itself.

    from speed_profile import profile_speeds, speed_at

MetaDrive has no such thing. `IDMPolicy.acceleration` (`idm_policy.py:304-312`) is its whole
longitudinal law and its only inputs are `target_speed`, the car's speed and the gap to the
car in front; road curvature is not an input to any speed decision anywhere in
`metadrive/policy/`, and `lane.speed_limit` - which `ScenarioLane` does read out of our
datasets - is consulted by nothing but a tollgate reward. So the speed a corner allows has to
be handed in from outside as `target_speed`, and this is what computes it.

**This is a port, not a new rule.** The three passes below are
`src/osm_scenario/ego_route.py:speed_profile`, which builds the recorded drive, and
`tests/unit/test_speed_profile.py` asserts the two agree on real route geometry. It is
duplicated rather than imported for the reason `traffic.py:9` gives: `tools/` runs on
MetaDrive's 3.8 venv and cannot import the package.

**It does not resample.** `ego_route._densify` is right for its own polylines, which are
sampled to `POLYLINE_TOLERANCE_M` 5 mm; the ego's recorded track is about a metre between
points, and densifying that linearly would concentrate each vertex's whole turn into 0.1 m
and report a radius the corner does not have.
"""

import math

import numpy as np

LATERAL_ACCEL_MPS2 = 4.0
"""Cornering budget. `ego_route`'s own figure is 8.5 and this is deliberately not it.

8.5 is not a comfort figure - it is pinned to the ego's 30-degrees-per-step gate - and it
works for the recorded drive because a replayed car's positions are set directly and nothing
has to *steer* to them. Anything driven by `WindowedTrajectoryIDMPolicy` steers with a 1 m
preview, and the sweep in `docs/reference/live-traffic.md` is monotonic and steep: over the
same five episodes, 8.5 put 54 cars off the tarmac at a worst 45.22 m, 6.0 put 38 off at
12.36 m, and 4.0 put 27 off at 3.76 m.
"""

ACCEL_MPS2 = 5.0
BRAKE_MPS2 = 6.0
"""How fast the speed may change. The backward pass is what puts the braking *before* the
corner rather than at it."""

MIN_SPEED_MPS = 1.0
"""The slowest the profile asks for while the car is still meant to be moving. A floor on the
geometry, not a statement about a car that is deliberately stationary."""


def profile_speeds(polyline, cruise_mps, lateral_accel_mps2=LATERAL_ACCEL_MPS2):
    """(cumulative distance, allowed speed) at every vertex of `polyline`, in m and m/s.

    Three passes, in the order a driver does them: the curvature at a point caps the speed
    there, then a forward and a backward pass bound how fast the speed may change.
    """
    points = np.asarray(polyline, dtype=np.float64)[..., :2]
    steps = np.hypot(*np.diff(points, axis=0).T)
    travelled = np.concatenate(([0.0], np.cumsum(steps)))
    limit = np.full(len(points), float(cruise_mps), dtype=np.float64)

    if len(points) >= 3:
        # Curvature as turn per metre, which is what the steering wheel does. The obvious
        # alternative - the circumradius of each triple - reads a polyline's *concentrated*
        # bend as if it were spread over the whole window.
        direction = np.diff(points, axis=0)
        heading = np.arctan2(direction[:, 1], direction[:, 0])
        turn = np.abs((np.diff(heading) + np.pi) % (2 * np.pi) - np.pi)
        span = (steps[:-1] + steps[1:]) / 2.0
        bends = turn > 1e-9
        radius = np.full(len(turn), np.inf)
        radius[bends] = span[bends] / turn[bends]
        limit[1:-1] = np.minimum(limit[1:-1], np.sqrt(lateral_accel_mps2 * radius))

    limit = np.clip(limit, min(MIN_SPEED_MPS, cruise_mps), cruise_mps)

    speed = limit.copy()
    for index in range(1, len(speed)):
        reachable = math.sqrt(speed[index - 1] ** 2 + 2.0 * ACCEL_MPS2 * steps[index - 1])
        speed[index] = min(speed[index], reachable)
    for index in range(len(speed) - 2, -1, -1):
        stoppable = math.sqrt(speed[index + 1] ** 2 + 2.0 * BRAKE_MPS2 * steps[index])
        speed[index] = min(speed[index], stoppable)
    return travelled, speed


def speed_at(travelled, speed, along):
    """The allowed speed at `along` metres, taking the slower of the two vertices around it.

    The slower of the pair rather than an interpolation between them, for the reason
    `traffic._allowed_mps` refuses to interpolate toward the next pool: on the approach to a
    corner an interpolation hands back a speed the corner does not allow.
    """
    index = int(np.searchsorted(travelled, along))
    lo = min(max(index - 1, 0), len(speed) - 1)
    hi = min(max(index, 0), len(speed) - 1)
    return float(min(speed[lo], speed[hi]))
