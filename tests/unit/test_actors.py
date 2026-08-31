"""Reading an actor plan, and turning it into the tracks MetaDrive replays.

Every rule here has a twin in `web/src/actor/actors-file.ts`. The page's copy is what tells a
person which actor is wrong while it is still on screen; this one is what decides.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from osm_scenario.actors import (
    ACTORS_VERSION,
    CYCLIST,
    EXTENT_MARGIN_M,
    PEDESTRIAN,
    TRAFFIC_BARRIER,
    TRAFFIC_CONE,
    ActorPlanError,
    actor_tracks,
    crosswalk_features,
    read_actor_plan,
)
from osm_scenario.lane_model import LaneFeature, Point2D, PreliminaryLaneModel

WIDTH = 4.0

IDENTITY = {
    "generation_fingerprint": "fingerprint",
    "reviewed_lane_model_sha256": "model-sha",
}

# A transverse Mercator on the prime meridian, so a degree of longitude at the equator is a
# known number of metres and the fixture's lanes can sit at plain coordinates. The real
# workspaces use a UTM zone; what matters for these tests is only that the CRS is metric,
# because a geographic one would make projection the identity and hide every sign error.
_TMERC = "+proj=tmerc +lat_0=0 +lon_0=0 +k=1 +x_0=0 +y_0=0 +datum=WGS84 +units=m +no_defs"

_METADATA = {
    "generator_version": "test",
    "lane_model_schema_version": 1,
    "source_checksum": "source",
    "projected_graph_checksum": "graph",
    "configuration_checksum": "config",
    "generation_fingerprint": "fingerprint",
    "coordinate_system_wkt": _TMERC,
}


def _lane(identifier: str, *, x0: float = 0.0, x1: float = 50.0) -> LaneFeature:
    half = WIDTH / 2
    return LaneFeature(
        identifier=identifier,
        source_way_ids=["200"],
        source_edge=["1", "2", "0"],
        lane_index=0,
        lane_count=1,
        direction="forward",
        road_class="residential",
        width_m=WIDTH,
        speed_limit_kph=50.0,
        centerline=[Point2D(x=x0, y=0.0), Point2D(x=x1, y=0.0)],
        polygon=[
            Point2D(x=x0, y=-half),
            Point2D(x=x1, y=-half),
            Point2D(x=x1, y=half),
            Point2D(x=x0, y=half),
            Point2D(x=x0, y=-half),
        ],
        boundaries=[],
    )


def _model() -> PreliminaryLaneModel:
    return PreliminaryLaneModel.model_validate(
        {
            "metadata": _METADATA,
            "lanes": [_lane("a", x0=0.0, x1=50.0).model_dump()],
            "connectors": [],
        }
    )


MODEL = _model()


def _latlon(x: float, y: float) -> list[float]:
    """`[lat, lon]` for a point in the fixture's metric frame, the way the page writes it."""
    from pyproj import Transformer

    lon, lat = Transformer.from_crs(_TMERC, "EPSG:4326", always_xy=True).transform(x, y)
    return [lat, lon]


def _raw(*actors: dict[str, Any], **update: Any) -> dict[str, Any]:
    plan: dict[str, Any] = {
        "actors_version": ACTORS_VERSION,
        "identity": dict(IDENTITY),
        "actors": list(actors)
        or [
            {
                "name": "p1",
                "kind": "pedestrian",
                "path": [_latlon(25.0, -10.0), _latlon(25.0, 10.0)],
                "speed_mps": 1.0,
            }
        ],
    }
    plan.update(update)
    return plan


def _read(*actors: dict[str, Any], **update: Any):
    return read_actor_plan(
        _raw(*actors, **update), model=MODEL, model_sha256="model-sha", source="actors.json"
    )


# --- the file ----------------------------------------------------------------------------


def test_a_plan_from_another_version_is_refused() -> None:
    with pytest.raises(ActorPlanError, match="unsupported actors_version"):
        _read(actors_version=ACTORS_VERSION + 1)


def test_a_plan_drawn_on_another_map_is_refused() -> None:
    """The only check available. An actor path names nothing that could be found missing, so a
    stale plan would silently put a pedestrian somewhere else - quite possibly in a
    carriageway - with nothing downstream noticing."""
    identity = {**IDENTITY, "generation_fingerprint": "different"}
    with pytest.raises(ActorPlanError, match="drawn on a different lane model"):
        _read(identity=identity)


def test_a_plan_with_no_actors_is_refused() -> None:
    with pytest.raises(ActorPlanError, match="contains no actors"):
        _read(actors=[])


def test_an_unknown_kind_names_what_is_allowed() -> None:
    with pytest.raises(ActorPlanError, match="expected one of barrier, cone, cyclist"):
        _read({"name": "x", "kind": "horse", "position": _latlon(10.0, 0.0)})


def test_two_actors_cannot_share_a_name() -> None:
    one = {"name": "x", "kind": "cone", "position": _latlon(10.0, 0.0)}
    with pytest.raises(ActorPlanError, match="uses the actor name 'x' twice"):
        _read(one, dict(one))


def test_an_actor_may_not_be_called_ego() -> None:
    """It is the recorded car's own track key, and two tracks under one key is one track."""
    with pytest.raises(ActorPlanError, match="recorded car's own track key"):
        _read({"name": "ego", "kind": "cone", "position": _latlon(10.0, 0.0)})


def test_a_static_actor_with_a_path_is_refused() -> None:
    with pytest.raises(ActorPlanError, match="does not move, so it has no path"):
        _read(
            {
                "name": "c",
                "kind": "cone",
                "path": [_latlon(0.0, 0.0), _latlon(10.0, 0.0)],
                "position": _latlon(0.0, 0.0),
            }
        )


def test_a_walking_actor_with_a_position_is_refused() -> None:
    with pytest.raises(ActorPlanError, match="takes a path rather than a position"):
        _read(
            {
                "name": "p",
                "kind": "pedestrian",
                "position": _latlon(0.0, 0.0),
                "speed_mps": 1.0,
            }
        )


def test_a_wait_past_the_end_of_the_path_is_refused() -> None:
    with pytest.raises(ActorPlanError, match="past the end of its"):
        _read(
            {
                "name": "p",
                "kind": "pedestrian",
                "path": [_latlon(25.0, -10.0), _latlon(25.0, 10.0)],
                "speed_mps": 1.0,
                "waits": [{"at_m": 500.0, "seconds": 1.0}],
            }
        )


# --- coordinates -------------------------------------------------------------------------


def test_lat_lon_from_the_page_arrives_as_metres_in_the_models_frame() -> None:
    """The page has no projection, so this is the only place the two frames meet. A sign
    error here puts every actor on the wrong side of the road and raises nothing."""
    plan = _read({"name": "c", "kind": "cone", "position": _latlon(25.0, 7.5)})
    x, y = plan.actors[0].position or (0.0, 0.0)
    assert x == pytest.approx(25.0, abs=0.01)
    assert y == pytest.approx(7.5, abs=0.01)


def test_a_point_far_outside_the_map_is_refused() -> None:
    """The guard against a `[lat, lon]` written the other way round, which no range check can
    see: `junction-1` is at lat 3.18, lon 101.6, and 3.18 is a perfectly good longitude."""
    with pytest.raises(ActorPlanError, match="outside this map"):
        _read(
            {
                "name": "c",
                "kind": "cone",
                "position": _latlon(25.0, EXTENT_MARGIN_M + 500.0),
            }
        )


# --- the tracks --------------------------------------------------------------------------


def _tracks(*actors: dict[str, Any], steps: int = 200):
    return actor_tracks(_read(*actors), steps=steps, time_step_s=0.1)


def _walker(**update: Any) -> dict[str, Any]:
    entry = {
        "name": "p1",
        "kind": "pedestrian",
        "path": [_latlon(25.0, -10.0), _latlon(25.0, 10.0)],
        "speed_mps": 1.0,
    }
    entry.update(update)
    return entry


def test_every_state_array_is_the_scenarios_length() -> None:
    """`_check_object_state_dict` asserts it, and each route in a dataset is a different
    length - so the same plan has to produce a different track per scenario."""
    for steps in (50, 200):
        for track in _tracks(_walker(), steps=steps).values():
            for array in track["state"].values():
                assert len(array) == steps


def test_the_object_id_matches_the_key_it_is_stored_under() -> None:
    tracks = _tracks(_walker(name="crossing-north"))
    for name, track in tracks.items():
        assert track["metadata"]["object_id"] == name


def test_a_walker_carries_the_sizes_metadrive_reads_unconditionally() -> None:
    """`parse_object_state` treats these as optional; `spawn_pedestrian` reads
    `state["width"]` whatever it says, so an omitted array is a KeyError at reset."""
    state = _tracks(_walker())["p1"]["state"]
    assert {"length", "width", "height"} <= set(state)
    assert state["length"].max() == pytest.approx(0.70)
    assert state["height"].max() == pytest.approx(1.75)


def test_a_static_object_carries_no_sizes_and_is_valid_throughout() -> None:
    """Valid throughout is not tidiness: `spawn_static_object` discards anything under
    `MIN_VALID_FRAME_LEN` as sensor noise, silently. And it reads position and heading only,
    so a size written here would be a claim about a body it builds to its own dimensions."""
    track = _tracks({"name": "c", "kind": "cone", "position": _latlon(25.0, 7.5)})["c"]
    assert track["type"] == TRAFFIC_CONE
    assert "width" not in track["state"]
    assert track["state"]["valid"].all()


def test_a_start_delay_holds_the_actor_out_of_the_scene() -> None:
    state = _tracks(_walker(start_delay_s=2.0))["p1"]["state"]
    assert not state["valid"][:20].any()
    assert state["valid"][20]
    # Zeroed where invalid, so a frame the actor does not exist on makes no claim about
    # where it is. `sanity_check` only enforces that under `valid_check=True`.
    assert not state["position"][:20].any()


def test_an_actor_that_arrives_is_gone_rather_than_left_standing() -> None:
    """20 m at 1 m/s is 200 steps of 0.1 s, so a 300-step scenario outlives the walk."""
    state = _tracks(_walker(), steps=300)["p1"]["state"]
    assert state["valid"][:200].all()
    assert not state["valid"][-50:].any()


def test_an_actor_whose_walk_starts_after_the_scenario_ends_is_left_out() -> None:
    """`sanity_check`'s own advice for a track with no valid frame is to remove it."""
    assert _tracks(_walker(start_delay_s=60.0), steps=200) == {}


def test_a_wait_lengthens_the_walk_by_exactly_its_seconds() -> None:
    plain = _tracks(_walker(), steps=600)["p1"]["state"]["valid"].sum()
    held = _tracks(
        _walker(waits=[{"at_m": 10.0, "seconds": 5.0}]), steps=600
    )["p1"]["state"]["valid"].sum()
    assert held - plain == pytest.approx(50, abs=1)


def test_a_cyclist_is_a_cyclist_and_is_longer_than_it_is_wide() -> None:
    track = _tracks(_walker(name="r", kind="cyclist", speed_mps=5.0))["r"]
    assert track["type"] == CYCLIST
    assert track["state"]["length"].max() > track["state"]["width"].max()


def test_velocity_is_read_off_the_positions_rather_than_the_stated_speed() -> None:
    """A step spent standing in a wait has to read as stationary: it is what
    `ReplayTrafficParticipantPolicy` hands to `set_velocity`, and what the walk animation
    picks its cycle from."""
    state = _tracks(_walker(waits=[{"at_m": 10.0, "seconds": 5.0}]), steps=600)["p1"]["state"]
    speeds = np.linalg.norm(state["velocity"], axis=1)[state["valid"]]
    assert speeds.min() == pytest.approx(0.0, abs=0.05)
    assert speeds.max() == pytest.approx(1.0, abs=0.05)


# --- crossings ---------------------------------------------------------------------------


def _polygons() -> dict[str, np.ndarray]:
    lane = MODEL.lanes[0]
    return {lane.identifier: np.array([[point.x, point.y] for point in lane.polygon])}


def test_nothing_is_painted_unless_a_crossing_was_asked_for() -> None:
    """The sources carry no surveyed crossing anywhere, so a zebra under every walker would
    be inventing infrastructure rather than converting it."""
    assert crosswalk_features(_read(_walker()), lane_polygons=_polygons()) == {}


def test_a_crossing_is_a_quadrilateral_over_the_carriageway_only() -> None:
    """`get_semantic_map` fills the polygon and takes the stripe angle from its longest edge,
    so a shape with more than four corners paints at an angle nobody chose."""
    plan = _read(_walker(crossing_width_m=4.0))
    features = crosswalk_features(plan, lane_polygons=_polygons())
    assert len(features) == 1
    feature = next(iter(features.values()))
    assert feature["type"] == "CROSSWALK"
    polygon = feature["polygon"]
    assert polygon.shape == (4, 2)
    # The lane is 4 m wide and the path crosses it square, so the painted band spans the
    # carriageway and not the 20 m of pavement either side of it.
    assert polygon[:, 1].max() - polygon[:, 1].min() == pytest.approx(WIDTH, abs=0.2)


def test_a_path_that_never_reaches_the_road_paints_nothing() -> None:
    entry = _walker(
        path=[_latlon(25.0, 20.0), _latlon(25.0, 30.0)], crossing_width_m=4.0
    )
    assert crosswalk_features(_read(entry), lane_polygons=_polygons()) == {}


# --- pinned against MetaDrive ------------------------------------------------------------


def _metadrive_type_module() -> Any:
    """`metadrive/type.py`, loaded by path.

    It imports nothing but `logging`, so unlike the schema it needs no stubbing - and reading
    the file rather than importing the package keeps panda3d out of the test run, which is the
    same trade `test_conversion._load_metadrive_schema` makes.
    """
    checkout = Path("/home/keith/Desktop/work/wingfin/metadrive/metadrive")
    if not checkout.is_dir():
        found = importlib.util.find_spec("metadrive")
        if found is None or not found.submodule_search_locations:
            return None
        checkout = Path(next(iter(found.submodule_search_locations)))
    path = checkout / "type.py"
    if not path.is_file():
        return None
    spec = importlib.util.spec_from_file_location("_metadrive_type_for_tests", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_TYPES = _metadrive_type_module()


@pytest.mark.skipif(_TYPES is None, reason="no MetaDrive checkout to pin the type names against")
def test_the_type_names_are_metadrives_own() -> None:
    """Spelled out in `actors.py` because MetaDrive is deliberately not a dependency. A typo
    reaches `_check_object_state_dict`'s `has_type` assert with a message that names neither
    the file nor the actor - and `after_reset` logs 'Do not support' and spawns nothing."""
    assert _TYPES is not None
    assert PEDESTRIAN == _TYPES.MetaDriveType.PEDESTRIAN
    assert CYCLIST == _TYPES.MetaDriveType.CYCLIST
    assert TRAFFIC_CONE == _TYPES.MetaDriveType.TRAFFIC_CONE
    assert TRAFFIC_BARRIER == _TYPES.MetaDriveType.TRAFFIC_BARRIER
    for name in (PEDESTRIAN, CYCLIST, TRAFFIC_CONE, TRAFFIC_BARRIER):
        assert _TYPES.MetaDriveType.has_type(name)
