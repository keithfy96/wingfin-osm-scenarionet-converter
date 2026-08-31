"""The checks `tools/check_dataset.py` makes over the non-ego tracks and the crossings.

The script itself runs under MetaDrive's 3.8, but these two helpers import nothing from it -
the one MetaDrive constant they need is passed in - so they can be exercised here, where a
check that never fires would otherwise go unnoticed until it failed to catch something.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))

from check_dataset import _actor_faults, _crosswalk_faults  # noqa: E402

MIN_STATIC_FRAMES = 20
STEPS = 60


def _state(steps: int = STEPS, **update: Any) -> dict[str, Any]:
    state: dict[str, Any] = {
        "position": np.zeros((steps, 3)),
        "heading": np.zeros(steps),
        "velocity": np.zeros((steps, 2)),
        "valid": np.ones(steps, dtype=bool),
        "length": np.full(steps, 0.7),
        "width": np.full(steps, 0.7),
        "height": np.full(steps, 1.75),
    }
    state.update(update)
    return state


def _scenario(**tracks: dict[str, Any]) -> dict[str, Any]:
    return {
        "length": STEPS,
        "metadata": {"sdc_id": "ego"},
        "tracks": {
            "ego": {
                "type": "VEHICLE",
                "state": _state(),
                "metadata": {"type": "VEHICLE", "object_id": "ego"},
            },
            **tracks,
        },
        "map_features": {},
    }


def _walker(name: str = "p1", **update: Any) -> dict[str, Any]:
    track = {
        "type": "PEDESTRIAN",
        "state": _state(),
        "metadata": {"type": "PEDESTRIAN", "object_id": name},
    }
    track.update(update)
    return track


def test_a_sound_plan_reports_nothing() -> None:
    scenario = _scenario(p1=_walker())
    assert _actor_faults(scenario, MIN_STATIC_FRAMES) == []


def test_the_recorded_car_is_not_audited_as_an_actor() -> None:
    """It is checked as a drive, further down the script, and it carries none of these
    fields - so folding it in here would report a fault on every dataset ever built."""
    assert _actor_faults(_scenario(), MIN_STATIC_FRAMES) == []


def test_a_mismatched_object_id_is_reported() -> None:
    track = _walker()
    track["metadata"] = {"type": "PEDESTRIAN", "object_id": "somebody-else"}
    faults = _actor_faults(_scenario(p1=track), MIN_STATIC_FRAMES)
    assert any("object_id" in fault for fault in faults)


def test_a_state_array_of_the_wrong_length_is_reported() -> None:
    track = _walker(state=_state(heading=np.zeros(STEPS - 1)))
    faults = _actor_faults(_scenario(p1=track), MIN_STATIC_FRAMES)
    assert any("not the scenario's" in fault for fault in faults)


def test_a_walker_without_its_sizes_is_reported() -> None:
    """The fault that is otherwise a bare KeyError inside `spawn_pedestrian`, naming neither
    the dataset nor the track."""
    state = _state()
    del state["width"]
    faults = _actor_faults(_scenario(p1=_walker(state=state)), MIN_STATIC_FRAMES)
    assert any("spawn_pedestrian" in fault for fault in faults)


def test_a_short_lived_static_object_is_reported() -> None:
    """It is not a short-lived cone, it is no cone at all: `spawn_static_object` discards
    anything under `MIN_VALID_FRAME_LEN` as sensor noise, without a word."""
    valid = np.zeros(STEPS, dtype=bool)
    valid[:5] = True
    track = {
        "type": "TRAFFIC_CONE",
        "state": {
            "position": np.zeros((STEPS, 3)),
            "heading": np.zeros(STEPS),
            "velocity": np.zeros((STEPS, 2)),
            "valid": valid,
        },
        "metadata": {"type": "TRAFFIC_CONE", "object_id": "c1"},
    }
    faults = _actor_faults(_scenario(c1=track), MIN_STATIC_FRAMES)
    assert any("discarded as noise" in fault for fault in faults)


# --- crossings ---------------------------------------------------------------------------

_LANE = {
    "type": "LANE_SURFACE_STREET",
    "polygon": np.array([[0.0, -2.0], [50.0, -2.0], [50.0, 2.0], [0.0, 2.0]]),
}


def _crosswalk(polygon: np.ndarray, **update: Any) -> dict[str, Any]:
    feature = {"type": "CROSSWALK", "polygon": polygon}
    feature.update(update)
    return feature


def _square(x: float, y: float = 0.0) -> np.ndarray:
    return np.array([[x - 2, y - 3], [x + 2, y - 3], [x + 2, y + 3], [x - 2, y + 3]])


def test_a_crossing_over_the_carriageway_reports_nothing() -> None:
    features = {"lane": _LANE, "cw": _crosswalk(_square(25.0))}
    assert _crosswalk_faults(features) == []


def test_a_crossing_that_touches_no_lane_is_reported() -> None:
    features = {"lane": _LANE, "cw": _crosswalk(_square(25.0, y=40.0))}
    assert any("zebra painted on grass" in fault for fault in _crosswalk_faults(features))


def test_a_self_intersecting_crossing_is_reported() -> None:
    """A bow-tie fills as two triangles and takes its stripe angle from the wrong edge."""
    bowtie = np.array([[0.0, -2.0], [10.0, 2.0], [10.0, -2.0], [0.0, 2.0]])
    features = {"lane": _LANE, "cw": _crosswalk(bowtie)}
    assert any("self-intersecting" in fault for fault in _crosswalk_faults(features))


def test_a_crossing_with_too_few_corners_is_reported() -> None:
    features = {"lane": _LANE, "cw": _crosswalk(np.array([[0.0, 0.0], [1.0, 1.0]]))}
    assert any("four corners" in fault for fault in _crosswalk_faults(features))


def test_a_crossing_that_looks_like_paint_is_reported() -> None:
    """The exemption from `_paint_on_tarmac` is structural - a crosswalk has no `lane_id` and
    no `polyline` - so it is asserted rather than left to be rediscovered by whoever next
    wonders why a zebra does not trip the paint-on-drivable-road check."""
    features = {"lane": _LANE, "cw": _crosswalk(_square(25.0), lane_id="lane")}
    assert any("exempt from" in fault for fault in _crosswalk_faults(features))
