"""`tools/speed_profile.py` - the corner speeds, and that they still match the package's.

The three passes here are a port of `osm_scenario.ego_route.speed_profile`, duplicated rather
than imported because `tools/` runs on MetaDrive's 3.8 venv and cannot import the package
(`tools/traffic.py:9`). This repo's 3.10 can import both, so this is where the two are held
together: a drift between them would give the ego a different corner from the one the
recorded drive was built for, and nothing at runtime would say so.
"""

import json
import sys
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))

import speed_profile as tools_profile  # noqa: E402

from osm_scenario.ego_route import PROFILE_SAMPLE_M, speed_profile  # noqa: E402


def _arc(radius: float, turn: float, step: float = PROFILE_SAMPLE_M) -> list:
    angles = np.arange(0.0, turn, step / radius)
    return [(radius * np.sin(a), radius * (1.0 - np.cos(a))) for a in angles]


def _straight(length: float, step: float = PROFILE_SAMPLE_M) -> list:
    return [(x, 0.0) for x in np.arange(0.0, length, step)]


def _chain(*pieces) -> np.ndarray:
    """Lay the pieces end to end, each translated onto the last point of the one before."""
    out = [np.asarray(pieces[0][0], dtype=float)]
    for piece in pieces:
        piece = np.asarray(piece, dtype=float)
        out.extend(piece[1:] - piece[0] + out[-1])
    return np.asarray(out, dtype=float)


# --- the port agrees with the package ------------------------------------------------------


@pytest.mark.parametrize(
    "polyline",
    [
        pytest.param(_chain(_straight(60.0)), id="straight"),
        pytest.param(_chain(_straight(30.0), _arc(10.0, np.pi / 2)), id="sweeping-bend"),
        pytest.param(_chain(_straight(20.0), _arc(3.0, np.pi)), id="hairpin"),
    ],
)
def test_the_port_matches_the_packages_own_profile(polyline: np.ndarray) -> None:
    """Same three passes, same constants, same answer.

    Fed at `PROFILE_SAMPLE_M` so the package's `_densify` is a no-op and the only difference
    left is the arithmetic. The tools port deliberately does *not* densify - the ego's
    recorded track is about a metre between points, and densifying that linearly would
    concentrate each vertex's whole turn into 0.1 m and report a radius the corner has not
    got - which is a difference of input, not of rule.
    """
    lateral = tools_profile.LATERAL_ACCEL_MPS2
    _dense, mine_travelled, theirs = speed_profile(
        polyline, cruise_mps=13.9, lateral_accel_mps2=lateral
    )
    travelled, mine = tools_profile.profile_speeds(polyline, cruise_mps=13.9)
    # A tenth of a millimetre and a centimetre a second: `_densify` spaces its samples
    # evenly over the *total* length rather than stepping at exactly `PROFILE_SAMPLE_M`, so
    # even a polyline handed to it at its own sample rate comes back a whisker off. That is a
    # resampling artefact, and the assertion is that the rule is the same, not the sampling.
    assert len(mine) == len(theirs)
    assert travelled == pytest.approx(mine_travelled, abs=1e-3)
    assert mine == pytest.approx(theirs, abs=1e-2)


def test_the_ports_constants_are_the_packages(monkeypatch) -> None:
    """All but the cornering budget, which is deliberately different - see the docstring on
    `LATERAL_ACCEL_MPS2`."""
    from osm_scenario import ego_route

    assert tools_profile.ACCEL_MPS2 == ego_route.ACCEL_MPS2
    assert tools_profile.BRAKE_MPS2 == ego_route.BRAKE_MPS2
    assert tools_profile.MIN_SPEED_MPS == ego_route.MIN_SPEED_MPS
    assert tools_profile.LATERAL_ACCEL_MPS2 == 4.0 < ego_route.LATERAL_ACCEL_MPS2


# --- what the profile is for ---------------------------------------------------------------


def test_a_corner_is_capped_by_what_the_radius_allows() -> None:
    polyline = _chain(_straight(40.0), _arc(10.0, np.pi / 2))
    _travelled, speed = tools_profile.profile_speeds(polyline, cruise_mps=13.9)
    assert speed.min() == pytest.approx(np.sqrt(tools_profile.LATERAL_ACCEL_MPS2 * 10.0), rel=0.02)


def test_the_braking_lands_before_the_corner_not_at_it() -> None:
    """The backward pass is the whole reason there are three of them. A profile that only
    capped the corner would have the car arrive at the entry doing the speed limit."""
    polyline = _chain(_straight(40.0), _arc(10.0, np.pi / 2))
    travelled, speed = tools_profile.profile_speeds(polyline, cruise_mps=13.9)
    at_entry = tools_profile.speed_at(travelled, speed, 39.9)
    ten_before = tools_profile.speed_at(travelled, speed, 30.0)
    assert at_entry < ten_before < 13.9


def test_the_speed_looked_up_is_the_slower_of_the_pair_it_falls_between() -> None:
    """Never an interpolation toward the next vertex: on the approach to a corner that hands
    back a speed the corner does not allow. Same rule as `traffic._allowed_mps`."""
    travelled = np.asarray([0.0, 10.0, 20.0])
    speed = np.asarray([13.9, 13.9, 4.0])
    assert tools_profile.speed_at(travelled, speed, 15.0) == pytest.approx(4.0)


def test_a_route_shorter_than_three_points_still_returns_a_profile() -> None:
    """There is no curvature to read off two points, and the caller must still get a speed
    rather than an exception - `_route_arrays` can hand over a very short trajectory."""
    travelled, speed = tools_profile.profile_speeds([(0.0, 0.0), (5.0, 0.0)], cruise_mps=13.9)
    assert len(speed) == 2
    assert speed[-1] == pytest.approx(13.9)


# --- real geometry, when the workspace is there --------------------------------------------


def test_junction_1s_own_routes_ask_for_less_than_forty(monkeypatch) -> None:
    """The claim the whole change rests on, re-derived rather than quoted: MetaDrive's flat
    `NORMAL_SPEED` of 40 km/h is faster than a large share of this map allows."""
    plan = REPO / "workspaces" / "junction-1" / "traffic" / "traffic.json"
    if not plan.exists():
        pytest.skip("junction-1 has no traffic plan built")
    routes = json.loads(plan.read_text(encoding="utf-8"))["routes"]
    slow = 0.0
    total = 0.0
    for route in routes:
        polyline = np.asarray(route["polyline"], dtype=float)
        travelled, speed = tools_profile.profile_speeds(polyline, cruise_mps=route["speed_mps"])
        steps = np.diff(travelled)
        total += float(steps.sum())
        slow += float(steps[speed[:-1] < 40.0 / 3.6].sum())
    assert total > 0.0
    assert slow / total > 0.2, "most of the reason the ego ran wide is the corners"
