"""`tools/av3_model.py` and `rigs/av3.txt` - the five conversions into the AV3 checkpoint.

Every one of them fails silently. A mirrored route, a swapped camera pair or a reversed frame
history all produce a model that loads, runs and returns twenty plausible waypoints, so
nothing downstream raises and the only symptom is a car that drives somewhere else. What can
be pinned here is pinned here; the rest is `tools/av3_probe.py`, which needs an engine.

**Pinned against sources rather than against comments**, the `test_model_probe` pattern:
`preprocess` is checked pixel-for-pixel against the fork's own `modifiers.py`, and the rig's
camera names against `model_dev.yml`'s `camera_order`, both read as files. If either moves,
this fails rather than a picture silently arriving in the wrong channel order.

The forward pass itself is out of reach - it needs a 1.2 GB TensorRT engine and a GPU - and
so is anything that takes a MetaDrive `agent`. Those go through stand-ins with exactly the
attributes the conversion reads.
"""

import importlib.util
import math
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))

import av3_model  # noqa: E402
from camera_rig import load_rig  # noqa: E402

numpy = pytest.importorskip("numpy")

FORK = Path(
    "/home/keith/Desktop/work/wingfin/wingfin-openpilot-temp/assets"
)
MODIFIERS = FORK / "modifiers" / "modifiers.py"
MODEL_DEV = FORK / "configurations" / "model_dev.yml"
RIG = REPO / "rigs" / "av3.txt"


# ---------------------------------------------------------------------------------------
# Stand-ins. Only the attributes each conversion actually reads.
# ---------------------------------------------------------------------------------------


class FakeTrajectory:
    """A straight route along +x, or one bending left, sampled by arc length."""

    def __init__(self, curvature=0.0, length=200.0):
        self.curvature = curvature
        self.length = length

    def local_coordinates(self, position):
        return 0.0, 0.0

    def position(self, along, lateral):
        if abs(self.curvature) < 1e-12:
            return (along, 0.0)
        radius = 1.0 / self.curvature
        angle = along * self.curvature
        return (math.sin(angle) * radius, (1.0 - math.cos(angle)) * radius)

    def heading_theta_at(self, along):
        return along * self.curvature


class FakeNavigation:
    def __init__(self, trajectory):
        self.reference_trajectory = trajectory


class FakeAgent:
    """At the world origin, heading due east, so the ego frame is the world frame."""

    def __init__(self, velocity=(0.0, 0.0), curvature=0.0, heading=0.0):
        self.velocity = velocity
        self.heading_theta = heading
        self.position = (0.0, 0.0)
        self.navigation = FakeNavigation(FakeTrajectory(curvature))


def _fork_modifier():
    if not MODIFIERS.exists():
        pytest.skip(f"the openpilot fork is not at {MODIFIERS}")
    spec = importlib.util.spec_from_file_location("fork_modifiers", MODIFIERS)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------------------
# 1. pixels
# ---------------------------------------------------------------------------------------


def test_preprocess_is_pixel_identical_to_the_forks_own_modifier():
    """Read as a file and executed, not reproduced from its docstring.

    `av3_model.preprocess` stops one step short of the fork's - it returns uint8 rather than
    dividing by 255 - because the ring holds a quarter of the bytes that way, and Phase A
    established that the divide is what creates the float in the first place. Undoing that
    one step has to give back the fork's array exactly, at every input size the rig can
    produce: 512x384 is what `rigs/av3.txt` renders, and 1440x1080 is what wing-sim's own
    cameras render, so both have to squash identically.
    """
    cv2 = pytest.importorskip("cv2")
    del cv2
    fork = _fork_modifier()
    generator = numpy.random.default_rng(0)
    for shape in ((1080, 1440, 3), (384, 512, 3), (288, 512, 3)):
        frame = generator.integers(0, 256, shape, dtype=numpy.uint8)
        theirs = fork.Modifiers.camera_preprocessing(frame)
        ours = av3_model.preprocess(frame, 512, 288).astype(numpy.float32) / 255.0
        assert theirs.shape == (3, 288, 512)
        assert numpy.array_equal(theirs, ours), shape


def test_a_float_frame_is_refused_by_name():
    """Phase A's trap, one bus along: a camera read with `to_float=True` inflates 8x on the
    CPU and the divide below would then happen twice."""
    frame = numpy.zeros((384, 512, 3), dtype=numpy.float32)
    with pytest.raises(av3_model.ModelError) as raised:
        av3_model.preprocess(frame, 512, 288)
    assert "uint8" in str(raised.value)


# ---------------------------------------------------------------------------------------
# 2. camera order
# ---------------------------------------------------------------------------------------


def test_the_rig_offers_exactly_the_cameras_the_model_reads():
    """`camera_order` is a contract with the weights - `model_dev.yml` says so in as many
    words - and the whole reason `rigs/av3.txt` exists rather than a mapping onto
    `rigs/cams.txt` is that these two lists can then be compared directly."""
    if not MODEL_DEV.exists():
        pytest.skip(f"the openpilot fork is not at {MODEL_DEV}")
    config = av3_model.load_config(MODEL_DEV)
    rig = load_rig(RIG, read_interval_s=None)
    assert sorted(rig.names) == sorted(config.camera_order)


def test_every_rig_cameras_name_agrees_with_where_it_actually_points():
    """The failure `CLAUDE.md`'s parked note fell into, pinned so it cannot come back.

    `rigs/cams.txt` names its back pair the opposite of its own yaws, which is why mapping the
    model's six onto it was ambiguous. Here the names come from wing-sim's spec and so do the
    aims, so `front_right` must resolve to something on the right. `camera_rig.Camera.aim`
    reads the yaw under the CARLA convention, which is the one `_parse` converts from.
    """
    rig = load_rig(RIG, read_interval_s=None)
    for camera in rig.cameras:
        if camera.name.endswith("_middle"):
            continue
        side = camera.name.rsplit("_", 1)[1]
        assert side in camera.aim, f"{camera.name} aims {camera.aim}"


def test_the_rig_renders_four_three_so_the_squash_is_a_real_one():
    """`CLAUDE.md` used to say the resize was a no-op for us, and that is a geometry error.

    The model eats 16:9 and the modifier gets there by squashing a 4:3 frame vertically by
    1.33x - which is what it was trained on. A rig rendering 16:9 natively would give a
    vertical field of view a third narrower, silently.
    """
    rig = load_rig(RIG, read_interval_s=None)
    for camera in rig.cameras:
        assert camera.width * 3 == camera.height * 4, camera.name


# ---------------------------------------------------------------------------------------
# 3. the temporal ring
# ---------------------------------------------------------------------------------------


def test_the_ring_spans_the_training_stride_at_the_rate_it_is_read():
    ring = av3_model.FrameHistory(5, 0.5, 0.05)
    assert (ring.stride, ring.depth) == (10, 41)
    assert ring.sample_index == [0, 10, 20, 30, 40]
    assert ring.spacing_note is None


def test_a_read_interval_that_cannot_divide_the_stride_says_so_rather_than_refusing():
    """`av3_base` warns and carries on, and so does this: the read interval is a property of
    the drive's rate, and a run at a rate that does not divide 0.5 s is still a run. What it
    must not do is stay quiet - the model then sees history spaced differently to how it was
    trained, which is exactly what `model_dev.yml`'s own comment warns never stops a run."""
    ring = av3_model.FrameHistory(5, 0.5, 0.3)
    assert ring.spacing_note is not None
    assert "0.6" in ring.spacing_note


def test_the_ring_fills_on_the_first_observation_and_then_slides():
    ring = av3_model.FrameHistory(3, 0.2, 0.1)
    frame = numpy.full((2, 3, 4, 5), 7, dtype=numpy.uint8)
    ring.observe(frame, numpy.array([7.0, 0.0], dtype=numpy.float32))
    images, ego = ring.sampled()
    # Filled rather than left short, so a prediction can run on the first decision.
    assert images.shape == (1, 3, 2, 3, 4, 5)
    assert ego.shape == (1, 3, 2)
    assert numpy.allclose(images, 7 / 255.0)

    for value in (8, 9, 10, 11):
        ring.observe(
            numpy.full((2, 3, 4, 5), value, dtype=numpy.uint8),
            numpy.array([float(value), 0.0], dtype=numpy.float32),
        )
    images, ego = ring.sampled()
    # Newest LAST, `av3_base._sampled_images`'s own ordering: index 0 of the sample is the
    # oldest frame in a full ring. A reversed history is another thing that runs and is wrong.
    assert [round(float(v) * 255) for v in images[0, :, 0, 0, 0, 0]] == [7, 9, 11]
    assert [float(v) for v in ego[0, :, 0]] == [7.0, 9.0, 11.0]


def test_the_ring_holds_uint8_pictures_rather_than_preprocessed_floats():
    """108.8 MB against 435 for the same 41-deep ring, and nothing is lost: the camera renders
    8-bit and `round(x * 255)` returns all 256 values exactly."""
    ring = av3_model.FrameHistory(2, 0.1, 0.1)
    ring.observe(
        numpy.full((1, 3, 2, 2), 200, dtype=numpy.uint8),
        numpy.zeros(2, dtype=numpy.float32),
    )
    images, _ = ring.sampled()
    assert images.dtype == numpy.float32
    assert numpy.allclose(images * 255.0, 200.0)


# ---------------------------------------------------------------------------------------
# 4 and 5. the mirror
# ---------------------------------------------------------------------------------------


def test_the_ego_states_lateral_is_right_positive():
    """MetaDrive's own lateral is LEFT-positive and the model's is RIGHT-positive, so this
    negates. Getting it wrong tells the model the car is sliding the other way, and nothing
    raises.

    The car faces due east, so world +y is its left; a velocity with +y in it must come back
    as a NEGATIVE lateral.
    """
    state = av3_model.ego_state(FakeAgent(velocity=(3.0, 4.0)), (1.0, 1.0))
    assert state[0] == pytest.approx(3.0)
    assert state[1] == pytest.approx(-4.0)


def test_the_ego_state_is_normalised_by_the_configured_scale():
    state = av3_model.ego_state(FakeAgent(velocity=(8.09, -0.27)), (8.09, 0.27))
    assert state[0] == pytest.approx(1.0)
    assert state[1] == pytest.approx(1.0)


def test_a_route_bending_left_comes_out_as_negative_right():
    """Conversion 5, the half that decides which way the car is told to go.

    The fake route curves toward world +y, which for a car facing east is LEFT. The model's
    second column is RIGHT-positive, so every point of it must be negative, and `sin(theta)`
    must be negative with it while `cos(theta)` stays positive.
    """
    block = av3_model.navigation(FakeAgent(curvature=1.0 / 40.0), 20, 2.0, 20.0)
    assert block.shape == (20, 7)
    assert block[-1, 0] > 0.0  # still mostly ahead
    assert block[-1, 1] < 0.0  # ...and to the left, so RIGHT is negative
    assert block[-1, 2] > 0.0  # cos(theta) does not flip
    assert block[-1, 3] < 0.0  # sin(theta) does
    assert block[-1, 4] < 0.0  # and curvature, being d(theta)/ds


def test_a_route_bending_right_mirrors_it_exactly():
    left = av3_model.navigation(FakeAgent(curvature=1.0 / 40.0), 20, 2.0, 20.0)
    right = av3_model.navigation(FakeAgent(curvature=-1.0 / 40.0), 20, 2.0, 20.0)
    assert numpy.allclose(left[:, 0], right[:, 0])  # forward is unchanged
    assert numpy.allclose(left[:, 2], right[:, 2])  # so is cos(theta)
    for column in (1, 3, 4):  # right, sin(theta), curvature all negate together
        assert numpy.allclose(left[:, column], -right[:, column])


def test_the_route_is_normalised_by_the_windows_own_length():
    block = av3_model.navigation(FakeAgent(), 20, 2.0, 20.0)
    horizon = 20 * 2.0
    # A straight route: the last point is 19 x 2 m ahead, normalised by n_route x spacing.
    assert block[-1, 0] * horizon == pytest.approx(38.0)
    assert block[-1, 5] == pytest.approx(1.0)
    assert block[-1, 6] == pytest.approx(1.0)


def test_a_car_off_its_route_is_fed_zeros_rather_than_an_error():
    """What the model was trained to read as "no route". A car pushed wide is an ordinary
    thing for a drive to contain, so this is not a failure."""

    class Wide(FakeTrajectory):
        def local_coordinates(self, position):
            return 0.0, 25.0

    agent = FakeAgent()
    agent.navigation.reference_trajectory = Wide()
    block = av3_model.navigation(agent, 20, 2.0, 20.0)
    assert not block.any()


# ---------------------------------------------------------------------------------------
# 6. what the bridge is sent
# ---------------------------------------------------------------------------------------


def test_modelv2_rows_are_the_shape_from_predicted_reads():
    """`derive_modelv2.from_predicted` wants `[x, y, t, yaw, yaw_rate, v_x, v_y, a_x, a_y]`,
    which is the model's own eight columns with the time inserted THIRD rather than appended.
    It prepends its own t=0 anchor, so only the predicted points go here."""
    prediction = numpy.arange(20 * 8, dtype=numpy.float32).reshape(20, 8)
    rows = av3_model.modelv2_rows(prediction)
    assert len(rows) == 20
    assert all(len(row) == 9 for row in rows)
    first = rows[0]
    assert first[0] == 0.0 and first[1] == 1.0  # x, y
    assert first[2] == pytest.approx(0.1)  # t, inserted
    assert first[3:] == [2.0, 3.0, 4.0, 5.0, 6.0, 7.0]  # yaw..a_y, in order
    assert rows[-1][2] == pytest.approx(2.0)  # the horizon, exactly


def test_the_model_output_is_not_flipped_on_the_way_out():
    """The one asymmetry. `waypoints_from_route` negates `y` because it STARTS from
    MetaDrive's left-positive route sensor; the model's output starts in its own training
    frame, which is already the bridge's y-RIGHT. Measured end to end by
    `av3_probe --nav-sweep`: feeding the model a 30 m right-hand arc and then a left-hand one,
    with every other input held fixed, moved the predicted lateral by +1.109 m toward the
    right-hand bend.
    """
    prediction = numpy.zeros((4, 8), dtype=numpy.float32)
    prediction[:, 1] = [1.0, 2.0, 3.0, 4.0]
    rows = av3_model.modelv2_rows(prediction)
    assert [row[1] for row in rows] == [1.0, 2.0, 3.0, 4.0]
    assert [point[1] for point in av3_model.waypoints(prediction)] == [1.0, 2.0, 3.0, 4.0]


def test_waypoints_are_sent_as_well_as_modelv2_and_carry_the_same_times():
    """`server.py:_handle_step` reads `msg["waypoints"]` FIRST and returns a hard stop when it
    is empty, before it looks at `modelv2` at all. An empty list beside a full modelv2 block
    is a car that never moves."""
    prediction = numpy.arange(20 * 8, dtype=numpy.float32).reshape(20, 8)
    rows = av3_model.modelv2_rows(prediction)
    points = av3_model.waypoints(prediction)
    assert len(points) == len(rows) == 20
    for point, row in zip(points, rows, strict=True):
        assert point == [row[0], row[1], row[2]]


def test_a_waypoints_only_output_width_is_refused_rather_than_reinterpreted():
    with pytest.raises(av3_model.ModelError) as raised:
        av3_model.modelv2_rows(numpy.zeros((20, 2), dtype=numpy.float32))
    assert "8" in str(raised.value)


# ---------------------------------------------------------------------------------------
# the config
# ---------------------------------------------------------------------------------------


def test_nothing_in_the_config_is_defaulted():
    """A silently-defaulted `frame_stride_s` is the exact failure `model_dev.yml`'s own
    comment warns about: the model runs, on history spaced differently to how it was trained,
    and the run still scores."""
    for key in av3_model.REQUIRED_KEYS:
        values = {name: 1 for name in av3_model.REQUIRED_KEYS}
        values["camera_order"] = ["a"]
        values["ego_velocity_scale"] = [1.0, 1.0]
        del values[key]
        with pytest.raises(av3_model.ModelError) as raised:
            av3_model.Config(values)
        assert key in str(raised.value)


def test_a_scalar_velocity_scale_is_refused():
    values = {name: 1 for name in av3_model.REQUIRED_KEYS}
    values["camera_order"] = ["a"]
    values["ego_velocity_scale"] = 8.09
    with pytest.raises(av3_model.ModelError) as raised:
        av3_model.Config(values)
    assert "ego_velocity_scale" in str(raised.value)


def test_the_horizon_spreads_the_waypoint_times_evenly_to_it():
    assert av3_model.waypoint_times(4) == pytest.approx([0.5, 1.0, 1.5, 2.0])
    assert av3_model.waypoint_times(20)[-1] == pytest.approx(av3_model.MODEL_HORIZON_S)
