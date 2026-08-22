"""`tools/traffic.py` - the half that is testable without an engine.

Like `test_step_timing.py`, this reaches into `tools/` from this repo's 3.10 while the module
itself runs on MetaDrive's 3.8, so anything needing a simulator is out of reach: whether cars
crash is a real drive and cannot be asserted here. What *is* reachable is the arithmetic that
decides where a car is put, and it is worth reaching for - a heading taken across a vertex
rather than along a segment points the car somewhere it is not going, and nothing raises.

`traffic.py` imports MetaDrive lazily, inside `build_manager`, precisely so this import works.
"""

import json
import math
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))

from traffic import (  # noqa: E402
    END_MARGIN_M,
    MIN_GAP_M,
    TRAFFIC_VERSION,
    TrafficError,
    _cumulative,
    _pose_at,
    load_plan,
)


def _plan_file(tmp_path: Path, **update) -> Path:
    plan = {
        "traffic_version": TRAFFIC_VERSION,
        "identity": {"generation_fingerprint": "f", "reviewed_lane_model_sha256": "s"},
        "routes": [
            {
                "name": "traffic-000",
                "start_lane": "a",
                "end_lane": "b",
                "distance_m": 100.0,
                "speed_mps": 13.9,
                "polyline": [[0.0, 0.0], [100.0, 0.0]],
            }
        ],
    }
    plan.update(update)
    path = tmp_path / "traffic.json"
    path.write_text(json.dumps(plan), encoding="utf-8")
    return path


# --- reading the plan ---------------------------------------------------------------------


def test_a_plan_from_this_writer_is_accepted(tmp_path: Path) -> None:
    assert len(load_plan(_plan_file(tmp_path))["routes"]) == 1


def test_a_plan_from_a_future_writer_is_refused_rather_than_guessed_at(tmp_path: Path) -> None:
    with pytest.raises(TrafficError, match="unsupported traffic_version"):
        load_plan(_plan_file(tmp_path, traffic_version=TRAFFIC_VERSION + 1))


def test_a_route_with_one_point_is_refused(tmp_path: Path) -> None:
    """A single point is not a line to drive along, and `PointLane` does not say so.

    It is built from whatever array it is handed; the failure arrives later, as a car that
    sits still or a heading taken from a zero-length segment.
    """
    broken = [{"name": "t", "start_lane": "a", "end_lane": "b", "polyline": [[0.0, 0.0]]}]
    with pytest.raises(TrafficError, match="no polyline to drive along"):
        load_plan(_plan_file(tmp_path, routes=broken))


def test_an_empty_plan_is_refused(tmp_path: Path) -> None:
    with pytest.raises(TrafficError, match="contains no routes"):
        load_plan(_plan_file(tmp_path, routes=[]))


# --- putting a car somewhere --------------------------------------------------------------


def test_distance_along_a_line_is_measured_not_counted() -> None:
    assert _cumulative([(0.0, 0.0), (3.0, 4.0), (3.0, 9.0)]) == [0.0, 5.0, 10.0]


def test_a_pose_partway_along_a_segment_is_interpolated() -> None:
    points = [(0.0, 0.0), (10.0, 0.0)]
    position, heading = _pose_at(points, _cumulative(points), 2.5)
    assert position == pytest.approx((2.5, 0.0))
    assert heading == pytest.approx(0.0)


def test_the_heading_at_a_corner_is_the_segment_the_car_is_on() -> None:
    """Not a difference across the vertex, which is the average of two directions.

    A car sitting exactly on the corner of an L is travelling along one arm of it, and the
    bisector points into the verge. `atan2` over the *segment* is what says which arm.
    """
    points = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0)]
    lengths = _cumulative(points)
    _, before = _pose_at(points, lengths, 9.9)
    _, after = _pose_at(points, lengths, 10.1)
    assert before == pytest.approx(0.0)
    assert after == pytest.approx(math.pi / 2)


def test_a_distance_past_the_end_lands_on_the_end() -> None:
    """Clamped rather than extrapolated. A placement is chosen inside the line's length, so
    reaching here means an off-by-one somewhere - and extrapolating would put the car off the
    end of the road with no sign that anything went wrong."""
    points = [(0.0, 0.0), (10.0, 0.0)]
    position, _ = _pose_at(points, _cumulative(points), 999.0)
    assert position == pytest.approx((10.0, 0.0))


def test_a_negative_distance_lands_on_the_start() -> None:
    points = [(0.0, 0.0), (10.0, 0.0)]
    position, _ = _pose_at(points, _cumulative(points), -5.0)
    assert position == pytest.approx((0.0, 0.0))


def test_a_zero_length_segment_does_not_divide_by_it() -> None:
    """`route_polyline` leaves repeated vertices a fraction of a micrometre apart, which is
    `ego_route.COINCIDENT_M`'s whole subject. Landing on one must not be a ZeroDivisionError."""
    points = [(0.0, 0.0), (0.0, 0.0), (10.0, 0.0)]
    position, _ = _pose_at(points, _cumulative(points), 0.0)
    assert position == pytest.approx((0.0, 0.0))


def test_the_end_margin_clears_the_radius_the_policy_retires_a_car_in() -> None:
    """`TrajectoryIDMPolicy.DEST_REGION_RADIUS` is 2 m. A car placed inside it is cleared on
    the frame it appears, so the margin has to be comfortably outside - and comfortably is
    measured against `MIN_GAP_M`, or a replacement could be placed into the same trap."""
    assert END_MARGIN_M > 2.0
    assert END_MARGIN_M > MIN_GAP_M
