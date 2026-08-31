"""`tools/ros_schema.py` - the content of a ROS 2 bag, before any of it is serialised.

The whole module exists to be reachable from here. It imports neither MetaDrive nor `rosbags`,
so every rule it encodes - which frame a quantity is in, which way an angle grows, whether two
topics of one instant agree about the time - is checkable on this interpreter with no simulator,
no GPU and no bag on disk.

That matters more than it usually would, because **none of these mistakes raises**. A quaternion
built with the wrong sign serialises perfectly and puts every box on the wrong side of the car;
a twist published in the world frame instead of the car's is correct exactly while the car
drives east. The openpilot work already paid for this lesson once, where one end counted left
positive and the other negative and the car drove smoothly into the oncoming carriageway with
nothing raising anything.
"""

import math
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))

import ros_schema  # noqa: E402
from ros_schema import (  # noqa: E402
    BASE_FRAME,
    BUILDERS,
    EXTRA_DEFINITIONS,
    MAP_FRAME,
    TOPICS,
    Box,
    Ego,
    Frame,
    Light,
    Projection,
    messages,
    quaternion,
    stamp,
)


def _ego(**over):
    base = dict(x=10.0, y=20.0, z=0.5, heading=0.0, velocity_east=8.0, velocity_north=0.0,
                speed=8.0, yaw_rate=0.0)
    base.update(over)
    return Ego(**base)


def _frame(**over):
    base = dict(index=3, sim_time_s=1.25, ego=_ego())
    base.update(over)
    return Frame(**base)


def _stamps(value, found=None):
    """Every `builtin_interfaces/Time` anywhere in a built message, however deeply nested."""
    found = [] if found is None else found
    if isinstance(value, dict):
        if set(value) == {"sec", "nanosec"}:
            found.append((value["sec"], value["nanosec"]))
        else:
            for item in value.values():
                _stamps(item, found)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _stamps(item, found)
    return found


class TestTheStamp:
    def test_every_topic_of_one_frame_carries_the_identical_stamp(self):
        """The single most important property of the whole bag.

        MetaDrive's own bridge stamps each stream with the wall clock as it arrives at the far
        end of a socket, so the camera and the boxes of one `env.step` land tens of milliseconds
        apart - half a metre at 50 km/h, baked into the labels. One reading, taken once, shared
        by everything, is the difference.
        """
        frame = _frame(
            boxes=(Box("ped-1", "PEDESTRIAN", 1.0, 2.0, 0.0, 0.0, 0.6, 0.6, 1.8),),
            lights=(Light("light-a", "TRAFFIC_LIGHT_RED", 5.0, 6.0),),
        )
        seen = set()
        for _topic, _msgtype, content in messages(frame):
            seen.update(_stamps(content))
        assert len(seen) == 1, seen
        assert seen == {(1, 250_000_000)}

    def test_a_stamp_is_rounded_not_truncated(self):
        """float32 `ts` from MetaDrive does not land on exact nanoseconds.

        Truncating loses a nanosecond about half the time, which is harmless on its own and
        fatal to the check above: two topics built from one float could then disagree in the
        last digit and nothing downstream would ever say so.
        """
        assert stamp(0.1) == {"sec": 0, "nanosec": 100_000_000}
        assert stamp(2.0000000004) == {"sec": 2, "nanosec": 0}
        assert stamp(1.9999999996) == {"sec": 2, "nanosec": 0}

    def test_seconds_and_nanoseconds_split_the_way_ros_expects(self):
        assert stamp(3.75) == {"sec": 3, "nanosec": 750_000_000}
        assert stamp(0.0) == {"sec": 0, "nanosec": 0}


class TestSigns:
    """The silent class of fault. Every assertion here is a direction, not a value."""

    def test_a_left_turn_is_a_positive_yaw(self):
        left = quaternion(math.radians(30))
        right = quaternion(math.radians(-30))
        assert left["z"] > 0 and right["z"] < 0
        assert left["w"] == pytest.approx(right["w"])

    def test_zero_yaw_is_the_identity_quaternion(self):
        assert quaternion(0.0) == pytest.approx({"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0})

    def test_a_quaternion_is_normalised(self):
        for yaw in (0.0, 0.5, -2.0, 3.0, math.pi):
            q = quaternion(yaw, pitch=0.2, roll=-0.1)
            assert math.sqrt(sum(v * v for v in q.values())) == pytest.approx(1.0)

    def test_the_transform_puts_the_car_where_the_car_is(self):
        frame = _frame(ego=_ego(x=-4.0, y=7.5, heading=math.radians(90)))
        (transform,) = ros_schema.tf_message(frame)["transforms"]
        assert transform["header"]["frame_id"] == MAP_FRAME
        assert transform["child_frame_id"] == BASE_FRAME
        assert transform["transform"]["translation"]["x"] == -4.0
        assert transform["transform"]["translation"]["y"] == 7.5
        assert transform["transform"]["rotation"]["z"] > 0


class TestOdometry:
    def test_the_twist_is_in_the_cars_frame_not_the_worlds(self):
        """REP-105 puts `twist` in `child_frame_id`, and the mistake here is invisible.

        A car driving north at 8 m/s while heading north is going 8 m/s *forward*. Publish the
        world-frame vector instead and this reads `east 0, north 8` - which is correct-looking,
        and identical to the right answer whenever the car happens to be pointing east.
        """
        frame = _frame(ego=_ego(heading=math.radians(90), velocity_east=0.0, velocity_north=8.0))
        twist = ros_schema.odometry_message(frame)["twist"]["twist"]
        assert twist["linear"]["x"] == pytest.approx(8.0)
        assert twist["linear"]["y"] == pytest.approx(0.0, abs=1e-9)

    def test_a_sideways_component_lands_on_the_left(self):
        frame = _frame(ego=_ego(heading=0.0, velocity_east=0.0, velocity_north=3.0))
        twist = ros_schema.odometry_message(frame)["twist"]["twist"]
        assert twist["linear"]["x"] == pytest.approx(0.0, abs=1e-9)
        assert twist["linear"]["y"] == pytest.approx(3.0)

    def test_the_pose_stays_in_the_world_frame(self):
        frame = _frame(ego=_ego(x=100.0, y=-50.0))
        message = ros_schema.odometry_message(frame)
        assert message["header"]["frame_id"] == MAP_FRAME
        assert message["child_frame_id"] == BASE_FRAME
        assert message["pose"]["pose"]["position"]["x"] == 100.0

    def test_covariance_is_marked_unknown_rather_than_invented(self):
        message = ros_schema.odometry_message(_frame())
        assert message["pose"]["covariance"][0] == -1.0
        assert len(message["pose"]["covariance"]) == 36


class TestObjects:
    def test_only_what_the_frame_carries_is_labelled(self):
        """No phantom labels: the boxes are exactly `frame.boxes`, never a superset.

        A walker whose tape has ended is gone from the render as well as from the world, so a
        box recovered from the dataset's track list would sit over empty road and teach a
        detector to hallucinate people.
        """
        boxes = (
            Box("ped-1", "PEDESTRIAN", 1.0, 2.0, 0.0, 0.0, 0.6, 0.6, 1.8),
            Box("car-9", "VEHICLE", -3.0, 4.0, 0.0, 0.0, 4.5, 1.9, 1.5),
        )
        message = ros_schema.objects_message(_frame(boxes=boxes))
        assert [d["id"] for d in message["detections"]] == ["ped-1", "car-9"]
        assert len(ros_schema.objects_message(_frame())["detections"]) == 0

    def test_boxes_are_in_the_world_frame(self):
        """Strictly more information than the ego-relative boxes MetaDrive's bridge publishes.

        World frame plus `/tf` recovers ego-relative; ego-relative alone never recovers world,
        because that bridge publishes no ego pose at all.
        """
        box = Box("car-9", "VEHICLE", -3.0, 4.0, 0.25, 0.0, 4.5, 1.9, 1.5)
        frame = _frame(ego=_ego(x=1000.0, y=2000.0), boxes=(box,))
        (detection,) = ros_schema.objects_message(frame)["detections"]
        assert detection["header"]["frame_id"] == MAP_FRAME
        assert detection["bbox"]["center"]["position"]["x"] == -3.0
        assert detection["bbox"]["center"]["position"]["y"] == 4.0

    def test_size_is_length_width_height_in_that_order(self):
        box = Box("car-9", "VEHICLE", 0.0, 0.0, 0.0, 0.0, 4.5, 1.9, 1.5)
        (detection,) = ros_schema.objects_message(_frame(boxes=(box,)))["detections"]
        assert detection["bbox"]["size"] == {"x": 4.5, "y": 1.9, "z": 1.5}

    def test_the_kind_travels_as_the_class_and_the_name_as_the_id(self):
        box = Box("ped-1", "PEDESTRIAN", 0.0, 0.0, 0.0, 0.0, 0.6, 0.6, 1.8)
        (detection,) = ros_schema.objects_message(_frame(boxes=(box,)))["detections"]
        assert detection["results"][0]["hypothesis"]["class_id"] == "PEDESTRIAN"
        assert detection["results"][0]["hypothesis"]["score"] == 1.0
        assert detection["id"] == "ped-1"


class TestTrafficLights:
    def test_the_status_string_is_passed_through_unaltered(self):
        """MetaDrive's own `LIGHT_*` word, so the dataset, the plan and the bag all agree.

        Mapping it onto an enum of ours would mean holding a translation table to check one
        against another, and the table would be the thing that went stale.
        """
        light = Light("light-a", "TRAFFIC_LIGHT_RED", 5.0, 6.0, lane="lane-7")
        (written,) = ros_schema.traffic_lights_message(_frame(lights=(light,)))["lights"]
        assert written["status"] == "TRAFFIC_LIGHT_RED"
        assert written["id"] == "light-a"
        assert written["lane"] == "lane-7"
        assert written["position"] == {"x": 5.0, "y": 6.0, "z": 0.0}


class TestGnss:
    def test_no_projection_means_no_fix_rather_than_a_fabricated_one(self):
        assert ros_schema.gnss_fix_message(_frame()) is None
        assert ros_schema.GNSS_FIX not in {topic for topic, _, _ in messages(_frame())}

    def test_the_origin_shift_is_applied(self):
        """The 93.8 m trap on junction-1.

        MetaDrive re-centres every scenario on the ego's first position and records the shift in
        `old_origin_in_current_coordinate`. Skip it and every reading is out by that much, on a
        road, looking entirely plausible. Two frames differing only by the offset must therefore
        land on different coordinates.
        """
        projection = Projection(
            origin_lat=3.15, origin_lon=101.6, offset_x=55.725, offset_y=-75.469
        )
        shifted = ros_schema.gnss_fix_message(_frame(projection=projection))
        unshifted = ros_schema.gnss_fix_message(
            _frame(projection=Projection(3.15, 101.6, 0.0, 0.0))
        )
        assert shifted is not None and unshifted is not None
        assert shifted["latitude"] != unshifted["latitude"]
        assert shifted["longitude"] != unshifted["longitude"]

    def test_a_fix_lands_near_the_projection_origin(self):
        """Ten metres east of the origin is a fraction of a degree, not the other hemisphere."""
        projection = Projection(origin_lat=3.15, origin_lon=101.6, offset_x=0.0, offset_y=0.0)
        frame = Frame(index=0, sim_time_s=0.0, ego=_ego(x=10.0, y=20.0), projection=projection)
        fix = ros_schema.gnss_fix_message(frame)
        assert fix["latitude"] == pytest.approx(3.15, abs=1e-3)
        assert fix["longitude"] == pytest.approx(101.6, abs=1e-3)
        assert fix["latitude"] > 3.15 and fix["longitude"] > 101.6

    def test_the_covariance_is_unknown_rather_than_a_confident_lie(self):
        """A simulator claiming 2 cm of GNSS uncertainty would be inventing a number, and a
        model trained against it learns to trust GNSS in a way a real receiver never earns."""
        projection = Projection(3.15, 101.6, 0.0, 0.0)
        fix = ros_schema.gnss_fix_message(_frame(projection=projection))
        assert fix["position_covariance_type"] == 0
        assert fix["position_covariance"] == [0.0] * 9

    def test_the_imu_reports_no_acceleration_rather_than_a_differenced_one(self):
        imu = ros_schema.imu_message(_frame())
        assert imu["linear_acceleration"] == {"x": 0.0, "y": 0.0, "z": 0.0}
        assert imu["linear_acceleration_covariance"][0] == -1.0


class TestTheTopicTable:
    def test_every_per_frame_topic_has_a_builder_and_the_reverse(self):
        latched = {ros_schema.ROUTE, ros_schema.TF_STATIC}
        assert set(BUILDERS) == set(TOPICS) - latched
        assert set(BUILDERS) <= set(TOPICS)

    def test_topics_are_named_the_way_the_reference_bag_names_them(self):
        for topic in TOPICS:
            assert topic.startswith("/"), topic
            assert " " not in topic

    def test_nothing_is_published_under_a_rig_topic_with_a_different_type(self):
        """The rule that keeps a simulated bag safe to point at a real pipeline.

        A subscriber expecting `wingfin_msgs/VehicleState` on `/vehicle/state` fails on a
        `geometry_msgs/TwistStamped` wearing that name - worse than the topic being absent. So
        every topic we cannot type correctly stays in `MISSING_DEFINITIONS` and out of `TOPICS`.
        """
        assert set(TOPICS) & set(ros_schema.MISSING_DEFINITIONS) == set()

    def test_the_selection_is_honoured(self):
        picked = {ros_schema.CLOCK, ros_schema.ODOMETRY}
        assert {topic for topic, _, _ in messages(_frame(), topics=picked)} == picked

    def test_the_vendored_definitions_are_parseable_message_text(self):
        for name, text in EXTRA_DEFINITIONS.items():
            assert name.count("/") == 2 and "/msg/" in name, name
            assert text.endswith("\n")
            for line in text.strip().splitlines():
                assert len(line.split()) == 2, (name, line)


class TestTheCameraMountConversion:
    """`ros_frame.mounts_from_rig` - two frames that differ in every axis, silently.

    This is the one conversion in Stage 10 that was written wrong first time. The rig spec is
    parsed *from* CARLA but stored in MetaDrive's ego frame (`camera_rig.py:130-131`), so
    converting as though `Camera.position` were still CARLA puts every camera in the wrong place
    and mirrors left and right - and nothing raises, because both frames are three floats.

        MetaDrive ego   x RIGHT, y FORWARD, z up, hpr[0] degrees, + is LEFT
        ROS base_link   x FORWARD, y LEFT,  z up, yaw radians,    + is LEFT
    """

    class _Camera:
        def __init__(self, name, position, heading_deg):
            self.name = name
            self.position = position
            self.hpr = (heading_deg, 0.0, 0.0)

    class _Rig:
        def __init__(self, cameras):
            self.cameras = cameras

    def _mounts(self, cameras):
        import ros_frame

        return ros_frame.mounts_from_rig(self._Rig(cameras))

    def test_forward_and_lateral_swap(self):
        """A camera 1 m forward is at ROS x=+1, not y=+1."""
        mounts = self._mounts([self._Camera("cam_front", (0.0, 1.0, 2.0), 0.0)])
        x, y, z, yaw = mounts["cam_front"]
        assert (x, y, z) == (1.0, 0.0, 2.0)
        assert yaw == 0.0

    def test_a_camera_on_the_right_gets_a_negative_y(self):
        """MetaDrive x is RIGHT; ROS y is LEFT. Miss the negation and the wing cameras swap."""
        mounts = self._mounts([self._Camera("cam_wing", (1.5, 0.0, 1.0), 0.0)])
        x, y, _z, _yaw = mounts["cam_wing"]
        assert x == 0.0
        assert y == -1.5

    def test_the_yaw_sign_is_already_right_and_must_not_be_flipped(self):
        """Both frames call left positive, so this conversion is degrees-to-radians only.

        `rigs/cams.txt` gives `cam_left` a spec `yaw: -55`, which `camera_rig.py:414` stores as
        `hpr[0] = +55`; +55 in ROS points 55 degrees left, which is where `cam_left` belongs.
        """
        mounts = self._mounts([self._Camera("cam_left", (0.0, 1.0, 2.0), 55.0)])
        assert mounts["cam_left"][3] == pytest.approx(math.radians(55.0))
        mounts = self._mounts([self._Camera("cam_right", (0.0, 1.0, 2.0), -55.0)])
        assert mounts["cam_right"][3] == pytest.approx(math.radians(-55.0))

    def test_the_real_rig_file_lands_where_its_names_say(self):
        """Parsed from `rigs/cams.txt` itself, so a change to it cannot quietly invalidate this."""
        import camera_rig
        import ros_frame

        rig = camera_rig.load_rig(REPO / "rigs" / "cams.txt", read_interval_s=None)
        mounts = ros_frame.mounts_from_rig(rig)
        assert mounts["cam_left"][3] > 0, "cam_left must aim left, i.e. positive yaw"
        assert mounts["cam_right"][3] < 0, "cam_right must aim right"
        assert mounts["cam_front"][3] == pytest.approx(0.0)
        assert mounts["cam_front"][0] > 0, "the front camera is ahead of the axle, so ROS x > 0"
        assert mounts["cam_back"][0] < 0, "the rear camera is behind it"
        for name, (_x, _y, z, _yaw) in mounts.items():
            assert z > 0, f"{name} is mounted above the ground"


class TestTheVersionGate:
    def test_a_bag_is_refused_on_an_interpreter_that_cannot_write_one(self):
        """`tools/` also runs on the MetaDrive checkout's 3.8, where `rosbags` cannot install.

        The refusal has to happen before the drive starts. An ImportError three hundred frames
        into a recording costs the whole run and says nothing useful about how to fix it.
        """
        import ros_frame

        assert sys.version_info >= (3, 10)
        ros_frame.refuse_if_unsupported()

    def test_a_dataset_with_no_projection_yields_no_gnss_rather_than_an_error(self):
        import ros_frame

        assert ros_frame.projection_of({"metadata": {}}) is None
        assert ros_frame.projection_of({}) is None
        assert ros_frame.projection_of(None) is None
