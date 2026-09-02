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

    def test_covariance_is_unknown_zeros_and_never_the_minus_one(self):
        """Zeros mean "measured, uncertainty not modelled". -1 means "not produced at all".

        rviz2's Odometry display found this: a -1 on the diagonal is not positive-semidefinite,
        so it warned `Negative eigenvalue found for position` once a frame and drew no ellipse.
        Worse than the warning is what the -1 claims - that a pose which is exact ground truth
        should be discarded. `sensor_msgs/NavSatFix` in this same file had it right all along.
        """
        message = ros_schema.odometry_message(_frame())
        for field in ("pose", "twist"):
            covariance = message[field]["covariance"]
            assert len(covariance) == 36
            assert covariance == [0.0] * 36, f"{field} covariance still claims to be invalid"

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

    def test_only_the_quantity_we_do_not_produce_carries_the_minus_one(self):
        """The -1 is `sensor_msgs/Imu`'s "I do not produce this", not "I am unsure of this".

        Orientation and angular velocity are exact here, so a -1 on them told every consumer to
        throw away ground truth - and rviz2 refused to draw the covariance at all. Only the
        acceleration, which is genuinely not synthesised, keeps it.
        """
        imu = ros_schema.imu_message(_frame())
        assert imu["orientation_covariance"] == [0.0] * 9
        assert imu["angular_velocity_covariance"] == [0.0] * 9
        assert imu["linear_acceleration_covariance"] == [-1.0] + [0.0] * 8

    def test_no_message_this_module_writes_claims_an_invalid_pose(self):
        """One sweep over every builder, so a new one cannot reintroduce the -1 unnoticed."""
        frame = _frame(projection=Projection(3.15, 101.6, 0.0, 0.0))
        poses = [
            ros_schema.odometry_message(frame)["pose"]["covariance"],
            ros_schema.odometry_message(frame)["twist"]["covariance"],
            *[
                detection["results"][0]["pose"]["covariance"]
                for detection in ros_schema.objects_message(frame)["detections"]
            ],
        ]
        assert poses, "no pose covariances were checked - the sweep found nothing"
        for covariance in poses:
            assert covariance == [0.0] * 36


class TestTheTopicTable:
    def test_every_per_frame_topic_has_a_builder_and_the_reverse(self):
        latched = {ros_schema.ROUTE, ros_schema.TF_STATIC, *ros_schema.CAMERA_INFO_TOPICS}
        assert set(BUILDERS) == set(TOPICS) - latched
        assert set(BUILDERS) <= set(TOPICS)
        # The other half of the same rule: a latched topic is one `ros_bag.start_episode` writes,
        # and the family in `TOPICS` is what decides its QoS. A topic with no builder and the
        # "state" family would be declared, offered volatile and never written at all.
        assert all(TOPICS[topic][1] == "latched" for topic in latched)

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


class TestTheRigCoverageLedger:
    """`RIG_TOPICS` - the reference vehicle's 55 topics as data, and the count derived off them.

    This exists because a prose ledger and a code table drift apart in silence, and they had.
    `docs/rosbag.md` verdicted the 55 and `ros_schema.MISSING_DEFINITIONS` listed what we lacked
    a `.msg` for, and **nothing cross-referenced the two**, so both of the following were true
    at once and neither was visible to any check:

    * `/sensing/gnss/imu_data` was producible and absent from `MISSING_DEFINITIONS` altogether -
      one character away from `/sensing/gnss/imu/data`, which we do publish.
    * `/sensing/gnss/imu/temp` and `/sensing/gnss/status` sat *in* it, as though a `.msg` were
      all that stood between a simulator and a real receiver's temperature.

    Both are now structurally impossible rather than merely fixed: `MISSING_DEFINITIONS` is
    computed from these rows, so a topic can no longer be in one and not the other. The tests
    below are the rest of that guarantee - that the rows partition, that the phases add to the
    ladder they are judged against, and that our own ground-truth topics never earn credit
    against a bag that has none.
    """

    def test_the_ledger_is_the_reference_bags_own_55(self):
        assert len(ros_schema.RIG_TOPICS) == 55
        assert len({row.topic for row in ros_schema.RIG_TOPICS}) == 55

    def test_the_verdicts_split_the_way_the_doc_argues_them(self):
        counts = ros_schema.rig_coverage()["verdicts"]
        assert counts == {
            ros_schema.DIRECT: 24,
            ros_schema.APPROXIMATE: 21,
            ros_schema.IMPOSSIBLE: 10,
        }

    def test_45_is_55_less_the_ten_that_a_simulator_cannot_honestly_produce(self):
        """The target, and the reason it is not 55.

        A bag claiming all 55 would be claiming a CAN bus, a cabin camera, a microphone and a
        GNSS receiver's own temperature. Each of those absences is a fact about the vehicle,
        and a consumer can test for a topic that is not there - it cannot test for one that is
        there and invented.
        """
        assert ros_schema.rig_coverage()["producible"] == 45

    def test_every_declared_topic_is_a_rig_topic_or_a_declared_extra(self):
        """The check that would have caught both defects, and the reason phase 0 came first."""
        known = {row.topic for row in ros_schema.RIG_TOPICS} | set(ros_schema.SIMULATOR_EXTRAS)
        assert set(TOPICS) <= known, sorted(set(TOPICS) - known)

    def test_our_own_ground_truth_is_counted_apart_from_the_rigs_45(self):
        """The rig recorded no labels at all, so these four cannot be coverage of it.

        `/perception/inference_control` in its bag is the model's own configuration, not an
        answer. Counting our boxes and light colours towards the 45 would be marking our own
        paper with a mark the rig never offered.
        """
        rig = {row.topic for row in ros_schema.RIG_TOPICS}
        assert set(ros_schema.SIMULATOR_EXTRAS) & rig == set()
        ledger = ros_schema.rig_coverage()
        assert not set(ros_schema.SIMULATOR_EXTRAS) & {row.topic for row in ledger["produced"]}

    def test_every_producible_topic_is_either_written_or_owned_by_one_phase(self):
        """The partition. Without it a topic can fall out of the ledger and the total still
        looks plausible, which is how a coverage figure stops meaning anything."""
        ledger = ros_schema.rig_coverage()
        absent = [row for rows in ledger["absent"].values() for row in rows]
        assert len(ledger["produced"]) + len(absent) == ledger["producible"]
        assert len({row.topic for row in absent}) == len(absent), "a row owned by two phases"

    def test_the_phase_counts_are_the_ladder_every_later_phase_is_judged_against(self):
        """Stage 11's own summary: 8 -> 14 -> 23 -> 24 -> 30 -> 45.

        This is the acceptance criterion for phases 1-5 - each is done when the count moves by
        the number it claimed - so the claims live here rather than only in the plan's prose.

        **Phase 1 landed, so 14 is now the floor and 1 is gone from the ladder.** A phase that
        is done leaves the `absent` table entirely rather than lingering with a count of zero;
        the running total starting at 14 is what says so.
        """
        ledger = ros_schema.rig_coverage()
        per_phase = {phase: len(rows) for phase, rows in ledger["absent"].items()}
        assert per_phase == {2: 9, 3: 1, 4: 6, 5: 15}
        running, ladder = len(ledger["produced"]), []
        assert running == 14
        for phase in sorted(per_phase):
            running += per_phase[phase]
            ladder.append(running)
        assert ladder == [23, 24, 30, 45]

    def test_nothing_impossible_is_waiting_on_a_message_definition(self):
        """Defect two, made unrepresentable.

        `imu/temp` and `status` are physical sensor health. Listing them as definition-blocked
        said a `.msg` would unblock them, which invites somebody to go and find one.
        """
        for row in ros_schema.RIG_TOPICS:
            if row.verdict == ros_schema.IMPOSSIBLE:
                assert row.definition == "", row.topic
                assert row.phase is None, row.topic
                assert row.needs, f"{row.topic} must say why nothing will ever produce it"

    def test_the_two_gnss_imu_topics_one_character_apart_are_both_accounted_for(self):
        """Defect one. `/sensing/gnss/imu_data` is the SBG type; `/sensing/gnss/imu/data` is
        the `sensor_msgs/Imu` we publish. Confusing them is how one of them vanished."""
        assert "/sensing/gnss/imu/data" in TOPICS
        assert "/sensing/gnss/imu_data" not in TOPICS
        assert ros_schema.MISSING_DEFINITIONS["/sensing/gnss/imu_data"] == "sbg_driver/SbgImuData"

    def test_missing_definitions_is_derived_and_never_names_a_topic_off_the_ledger(self):
        rig = {row.topic for row in ros_schema.RIG_TOPICS}
        for topic, reason in ros_schema.MISSING_DEFINITIONS.items():
            assert topic in rig, topic
            assert ros_schema.RIG_BY_TOPIC[topic].producible, topic
            assert reason, topic

    def test_a_row_says_what_it_needs_exactly_when_we_do_not_write_it(self):
        for row in ros_schema.RIG_TOPICS:
            if row.producible:
                assert bool(row.needs) == (not row.produced), row.topic
                assert (row.phase is None) == row.produced, row.topic

    def test_the_declared_fourteen_are_the_rig_topics_this_module_actually_builds(self):
        ledger = ros_schema.rig_coverage()
        assert {row.topic for row in ledger["declared"]} == {
            "/tf",
            "/tf_static",
            "/localization/odometry",
            "/sensing/gnss/pose",
            "/sensing/gnss/imu/data",
            "/sensing/gnss/imu/velocity",
            "/sensing/gnss/imu/nav_sat_fix",
            "/sensing/lidar/imu",
            *ros_schema.CAMERA_INFO_TOPICS,
        }

    def test_a_bag_is_counted_by_what_reached_the_wire_not_by_what_was_declared(self):
        """14 declared, 7 written on a drive with no rig.

        `/tf_static` is guarded by `if mounts:` in `ros_bag.py` and the six `camera_info_latched`
        topics are written per camera, so a drive that mounts no `--camera-rig` declares a
        transform tree and six lenses and writes none of them. Reporting the declared figure
        against such a bag would hide exactly that, and phase 1 doubled how much it would hide.
        """
        rig_only = {"/tf_static", *ros_schema.CAMERA_INFO_TOPICS}
        written = {row.topic for row in ros_schema.rig_coverage()["declared"]} - rig_only
        ledger = ros_schema.rig_coverage(written)
        assert len(ledger["declared"]) == 14
        assert len(ledger["produced"]) == 7

    def test_the_two_rates_above_the_simulator_tick_are_recorded_as_such(self):
        """`env.step` is the world tick, so every rate here is a decimation of `--step-hz`.
        These two are not, and a bag that claims them is claiming a clock it does not have."""
        fast = {row.topic: row.hz for row in ros_schema.RIG_TOPICS if (row.hz or 0) > 100.0}
        assert fast == {"/sensing/gnss/imu/velocity": 200.0, "/sensing/lidar/imu": 202.9}

    def test_the_coverage_report_runs_with_no_bag_at_all(self):
        """It is a question about the code, not about a recording, and answers before one
        exists. `ros_probe.py` otherwise requires a bag and refuses without one."""
        import io

        import ros_probe

        rendered = io.StringIO()
        assert ros_probe.coverage(None, out=rendered)
        text = rendered.getvalue()
        assert "14 / 45" in text
        assert "phase 5" in text
        assert "24 direct, 21 approximate, 10 not producible" in text

    def test_the_probe_still_refuses_a_run_with_neither_a_bag_nor_coverage(self):
        import ros_probe

        with pytest.raises(SystemExit):
            ros_probe.main([])


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


class TestPointingAReaderAtNothing:
    """The likeliest mistake anyone makes with this stage, and it happened twice before this.

    `docs/testing-ros.md` reads in one tier what it recorded in the tier before, so a bag that
    is not there yet is the ordinary way to get it wrong - not an exotic one. Left to the
    libraries it is eleven lines of stack ending in `pathlib.read_bytes` or
    `rosbags/rosbag2/reader.py`, naming neither the bag nor anything to do about it.
    """

    def test_a_missing_bag_is_refused_with_the_command_that_writes_one(self, tmp_path):
        import ros_audit

        with pytest.raises(ValueError) as raised:
            ros_audit.refuse_if_missing(tmp_path / "not-a-bag")
        message = str(raised.value)
        assert "not-a-bag" in message
        assert "ros-bag.sh" in message, "the refusal has to name how to produce one"

    def test_both_readers_share_one_refusal_so_they_cannot_drift(self):
        import ros_audit
        import ros_probe

        assert ros_probe.ros_audit.refuse_if_missing is ros_audit.refuse_if_missing

    def test_a_bag_that_exists_is_returned_untouched(self, tmp_path):
        import ros_audit

        assert ros_audit.refuse_if_missing(tmp_path) == tmp_path


class TestTheArgumentLoopInTheScript:
    """`scripts/ros-bag.sh` could not run its own documented command, for a month.

    A bare `((expr))` returns exit status 1 when the expression evaluates to zero, and
    post-increment evaluates to the OLD value - so `((i++))` at `i == 0`, under the script's
    `set -euo pipefail`, exited 1 before the first line of output. `--out` is the first argument
    after `--` in every example in the README, the script's own help and the testing doc, which
    put `i` at exactly 0. The symptom was nothing at all: no bag, no message, no stderr.
    """

    def test_no_shell_script_increments_with_a_bare_double_paren(self):
        import re

        pattern = re.compile(r"^(?!\s*#).*\(\(\s*\w+\s*(\+\+|--)\s*\)\)")
        offenders = [
            f"{script.name}:{number}: {line.strip()}"
            for script in sorted((REPO / "scripts").glob("*.sh"))
            for number, line in enumerate(script.read_text().splitlines(), 1)
            if pattern.match(line) and "for ((" not in line
        ]
        assert not offenders, (
            "a bare ((i++)) is exit status 1 whenever i is 0, and every script here runs under "
            "set -e. Use i=$((i + 1)):\n  " + "\n  ".join(offenders)
        )

    def test_out_is_parsed_out_of_the_passthrough_wherever_it_sits(self):
        """Including first, which is the position that used to kill the script."""
        import subprocess
        import textwrap

        loop = (REPO / "scripts" / "ros-bag.sh").read_text()
        start = loop.index('OUT=""')
        end = loop.index("[[ -n \"$OUT\" ]]")
        script = textwrap.dedent(f"""
            set -euo pipefail
            PASSTHROUGH=("$@")
            {loop[start:end]}
            echo "$OUT|$WANT_LIGHTS|${{FILTERED[*]-}}"
        """)
        run = lambda *args: subprocess.run(  # noqa: E731
            ["bash", "-c", script, "_", *args], capture_output=True, text=True
        )

        first = run("--out", "bags/j1-001")
        assert first.returncode == 0, f"the loop exited {first.returncode} with no output"
        assert first.stdout.strip() == "bags/j1-001|0|"

        later = run("--traffic", "live", "--out", "bags/x", "--lights", "tape")
        assert later.stdout.strip() == "bags/x|1|--traffic live --lights tape"

        absent = run("--agent-policy", "idm")
        assert absent.stdout.strip() == "|0|--agent-policy idm"


class TestTheContainerCarriesTheRosGroup:
    """The container was the documented way out of the host's Python 3.8, and could not do it.

    `--ros-bag` runs on `METADRIVE_PYTHON`, which on the host is the MetaDrive checkout's 3.8 and
    has no `rosbags` wheel. Three places name the container as the answer - `ros-bag.sh`'s header,
    the last trap in `docs/reference/ros-bags.md`, and the refusal `refuse_if_unsupported` prints -
    and all three were right about the interpreter and wrong about the library: the image synced
    `sim gpu model` and never `ros`, so every bag it was pointed at was refused.

    Nothing caught it because nothing compares what the tools need against what the image holds.
    """

    @staticmethod
    def _sync_groups():
        """The union of `--group` flags across every uv sync line, exactly as `sim.sh` reads it.

        `scripts/sim.sh:97` unions them with awk rather than reading one line, which is what lets
        a group live in its own layer. This mirrors that, so the two cannot disagree about what
        the recipe asks for.
        """
        import re

        found = set()
        for line in (REPO / "docker" / "Dockerfile").read_text().splitlines():
            head, _, _ = line.partition("uv sync")
            if not _ or "#" in head:
                continue
            found.update(re.findall(r"--group ([a-z]+)", line))
        return found

    def test_the_image_recipe_installs_rosbags(self):
        assert "ros" in self._sync_groups(), (
            "docker/Dockerfile does not sync the `ros` group, so `rosbags` is not in the image "
            "and `./scripts/sim.sh ./scripts/ros-bag.sh ...` refuses - which is the command "
            "ros_frame.refuse_if_unsupported tells a 3.8 host to run."
        )

    def test_the_groups_label_matches_the_recipe(self):
        """`sim.sh` warns off this label, so a group it does not name is a group it cannot check.

        The label is last in the Dockerfile precisely so keeping it in step costs no build cache;
        this is what notices when someone adds a group above and forgets it.
        """
        import re

        text = (REPO / "docker" / "Dockerfile").read_text()
        found = re.search(r'LABEL wingfin\.groups="([^"]*)"', text)
        assert found, "no wingfin.groups LABEL in docker/Dockerfile"
        assert set(found.group(1).split()) == self._sync_groups(), (
            f"the label says {found.group(1)!r} and the uv sync lines ask for "
            f"{' '.join(sorted(self._sync_groups()))!r}"
        )

    def test_the_group_exists_and_is_locked(self):
        """`--frozen` in the image means an unlocked group fails the build, not the run.

        Read with a regex rather than `tomllib`, which is 3.11 and this repo is 3.10.
        """
        import re

        block = re.search(
            r"^ros = \[(.*?)\]", (REPO / "pyproject.toml").read_text(), re.S | re.M
        )
        assert block and "rosbags" in block.group(1), "no `ros` dependency group in pyproject.toml"
        assert 'name = "rosbags"' in (REPO / "uv.lock").read_text(), (
            "`rosbags` is not in uv.lock, so the image's `uv sync --frozen` fails at build time"
        )

    def test_the_refusal_names_the_right_fix_in_each_place(self):
        """`uv sync` is the fix on the host and is useless in the container.

        A container's environment is baked into the image, so a sync inside one edits something
        the next `docker compose run` discards. Telling a caller the wrong one of these costs a
        rebuild either way; naming both is the whole value of the message.
        """
        import ros_frame

        inside = ros_frame.missing_group_message(True)
        outside = ros_frame.missing_group_message(False)
        # On the instruction, not on the word: the container message may *mention* `uv sync` to
        # say why it will not help, so what must not appear there is the command itself.
        assert "docker compose build" in inside and "uv sync --group" not in inside
        assert "uv sync --group sim --group ros" in outside
        assert "docker compose build" not in outside


class TestTheScriptsOwnHelp:
    """`--help` printed a fixed line range, so adding a paragraph truncated it silently."""

    def test_help_prints_the_whole_header(self):
        import subprocess

        printed = subprocess.run(
            ["bash", str(REPO / "scripts" / "ros-bag.sh"), "--help"],
            capture_output=True,
            text=True,
            cwd=REPO,
        )
        assert printed.returncode == 0, printed.stderr
        # The last line of the comment block, so a range that stops early fails here rather
        # than quietly dropping whatever was added most recently.
        for wanted in ("--no-model", "The preflight is the point", "Read from .env"):
            assert wanted in printed.stdout, f"--help stops before {wanted!r}"


class TestTheGeneratedMessagePackage:
    """`tools/ros_msgs_package.py` - the same `.msg` text, as something a subscriber can build.

    MCAP carries the definition text, which is why a stock `ros:jazzy-ros-base` can *list*
    `wingfin_msgs/msg/TrafficLightArray`. Anything that wants the messages as objects - rviz2,
    or any node - needs generated type support instead, and that needs a colcon package. The
    package is emitted from `EXTRA_DEFINITIONS` rather than written beside it, because two copies
    of one definition drift silently: the bag keeps carrying what the writer registered while the
    package says something else, and a subscriber deserialises garbage without raising.
    """

    def test_the_msg_files_are_the_definitions_the_writer_registers(self):
        import ros_msgs_package

        files = ros_msgs_package.package_files("wingfin_msgs")
        for name, text in EXTRA_DEFINITIONS.items():
            if not name.startswith("wingfin_msgs/msg/"):
                continue
            short = name[len("wingfin_msgs/msg/") :]
            assert files[f"msg/{short}.msg"] == text, f"{short}.msg is not what the writer uses"

    def test_dependencies_are_read_off_the_fields_not_hardcoded(self):
        import ros_msgs_package

        messages = ros_msgs_package.messages_of("wingfin_msgs")
        found = ros_msgs_package.dependencies_of(messages, "wingfin_msgs")
        assert found == ["geometry_msgs", "std_msgs"]
        # A definition that grows a field must carry its package with it.
        grown = dict(messages, TrafficLight=messages["TrafficLight"] + "sensor_msgs/Image thumb\n")
        assert "sensor_msgs" in ros_msgs_package.dependencies_of(grown, "wingfin_msgs")

    def test_the_package_does_not_depend_on_itself(self):
        """`wingfin_msgs/TrafficLight[] lights` names its own package; ament fails on the cycle."""
        import ros_msgs_package

        messages = ros_msgs_package.messages_of("wingfin_msgs")
        assert "wingfin_msgs/TrafficLight[]" in messages["TrafficLightArray"]
        assert "wingfin_msgs" not in ros_msgs_package.dependencies_of(messages, "wingfin_msgs")

    def test_every_message_and_dependency_reaches_the_build_files(self):
        import ros_msgs_package

        files = ros_msgs_package.package_files("wingfin_msgs")
        for short in ros_msgs_package.messages_of("wingfin_msgs"):
            assert f'"msg/{short}.msg"' in files["CMakeLists.txt"]
        for dependency in ("geometry_msgs", "std_msgs"):
            assert f"<depend>{dependency}</depend>" in files["package.xml"]
            assert f"find_package({dependency} REQUIRED)" in files["CMakeLists.txt"]
        assert "<name>wingfin_msgs</name>" in files["package.xml"]

    def test_a_package_with_no_messages_is_refused(self):
        import ros_msgs_package

        with pytest.raises(ValueError, match="no nothing_msgs messages"):
            ros_msgs_package.package_files("nothing_msgs")


class TestReadingDefinitionsBackOutOfABag:
    """`tools/ros_defs.py` - the tool that unblocks the fifteen topics we have no `.msg` for.

    `MISSING_DEFINITIONS` gives the reason for four of them as "type not in the audit", which is
    true and points at the wrong place. `bag_audit.html` records *rates*; the only message type
    named anywhere in it is `geometry_msgs/TwistStamped`. The definitions were never going to be
    there.

    **They are in the bag.** rosbag2 writes each type's full `.msg` text into the file so a
    reader can decode it without the package that wrote it - which is the same property that made
    it safe for `ros_schema` to invent `wingfin_msgs/TrafficLight` in the first place. So the
    rig's own bag already carries the exact bytes for every type we lack, and Phase 5 of stage 11
    needs one `.mcap` file rather than the rig running or the wingfin source package.

    The gate below is a genuine self-test of that claim, and it needs no bag on disk: it writes
    one from `EXTRA_DEFINITIONS`, reads it back with nothing but the file, and demands the text
    come out identical. If ours survives the round trip the rig's will, because it is the same
    record in the same container.
    """

    @staticmethod
    def _bag(tmp_path):
        """A one-message bag carrying the invented type, written the way `ros_bag.py` writes."""
        import ros_bag

        path = tmp_path / "defs-bag"
        frame = Frame(
            index=0,
            sim_time_s=0.0,
            ego=Ego(x=0.0, y=0.0, z=0.0, heading=0.0, velocity_east=1.0, velocity_north=0.0,
                    speed=1.0),
            lights=(Light(name="l0", status="LIGHT_GREEN", x=1.0, y=2.0),),
        )
        with ros_bag.BagWriter(path, topics=None, notes={}) as bag:
            bag.write(frame)
        return path

    def test_our_own_definitions_come_back_byte_identical(self, tmp_path):
        import ros_defs

        found, undefined = ros_defs.read(self._bag(tmp_path))
        assert undefined == [], "the writer recorded no definition for some connection"
        for name in ("wingfin_msgs/msg/TrafficLightArray", "wingfin_msgs/msg/TrafficLight"):
            assert name in found, f"{name} did not survive the round trip"
            assert found[name].text == EXTRA_DEFINITIONS[name], f"{name} came back changed"

    def test_the_dependencies_come_back_too_not_only_the_top_type(self, tmp_path):
        """The point of the exercise: a type is useless without what its fields refer to."""
        import ros_defs

        found, _ = ros_defs.read(self._bag(tmp_path))
        assert {"std_msgs/msg/Header", "geometry_msgs/msg/Point"} <= set(found)

    def test_every_recovered_definition_parses(self, tmp_path):
        """The gate that keeps a bad paste out of `EXTRA_DEFINITIONS`.

        A field in the wrong order serialises silently and deserialises into nonsense, which is
        worse than an absent topic - the whole reason these are copied rather than inferred.
        """
        import ros_defs

        found, _ = ros_defs.read(self._bag(tmp_path))
        for name, entry in found.items():
            assert ros_defs.parses(name, entry.text) is None, f"{name} does not parse"

    def test_it_reports_nothing_new_for_a_bag_of_types_we_already_have(self, tmp_path, capsys):
        import ros_defs

        assert ros_defs.report(self._bag(tmp_path)) is True
        printed = capsys.readouterr().out
        assert "nothing new" in printed

    def test_a_rendered_entry_is_the_definition_it_came_from(self, tmp_path):
        """`render` output has to be pasteable, so it must evaluate back to the same text."""
        import ros_defs

        found, _ = ros_defs.read(self._bag(tmp_path))
        for name, entry in found.items():
            recovered = eval("{" + ros_defs.render(entry) + "}")  # noqa: S307 - our own output
            assert recovered == {name: entry.text}, f"{name} does not paste back"
            for line in ros_defs.render(entry).splitlines():
                assert len(line) <= 100, f"{name} renders past the line limit"

    def test_dependency_headers_are_normalised_to_the_ros_2_spelling(self):
        """`MSG: geometry_msgs/Point` and `geometry_msgs/msg/Point` are one type, not two.

        Keying on both spellings is how a recovered definition ends up in the dict under a name
        nothing ever looks up, so the type reads as still missing while sitting right there.
        """
        import ros_defs

        assert ros_defs.normalise("geometry_msgs/Point") == "geometry_msgs/msg/Point"
        assert ros_defs.normalise("geometry_msgs/msg/Point") == "geometry_msgs/msg/Point"
        pieces = ros_defs.split(
            "a_msgs/msg/Top",
            "a_msgs/Leaf leaf\n"
            + "=" * 80
            + "\nMSG: a_msgs/Leaf\nfloat64 x\n",
        )
        assert pieces == {"a_msgs/msg/Top": "a_msgs/Leaf leaf\n", "a_msgs/msg/Leaf": "float64 x\n"}

    def test_pointing_it_at_nothing_names_the_command_that_writes_a_bag(self, tmp_path):
        """The same refusal as `ros_audit` and `ros_probe`; all three are readers."""
        import ros_defs

        with pytest.raises(ValueError, match="no bag at"):
            ros_defs.read(tmp_path / "not-a-bag")
        assert ros_defs.main([str(tmp_path / "not-a-bag")]) == 1


class TestTheCameraIntrinsics:
    """`camera_info_message` - the six `camera_info_latched` topics stage 11 phase 1 landed.

    These needed no message definition: `sensor_msgs/CameraInfo` is core, and every number in it
    was already sitting in `camera_rig.Camera`. What they need is a rig, which is why they are
    declared always and written only on a `--camera-rig` drive - the same shape as `/tf_static`,
    and the reason the coverage report prints "declared" and "on the wire" as two numbers.

    The fault to guard against is not a crash. A `CameraInfo` with the wrong focal length
    deserialises perfectly, draws a frustum in rviz2 and reprojects every box a few degrees off
    - so the checks below are on relationships (fx == fy, the principal point at the centre,
    `p` agreeing with `k`) plus one arithmetic anchor, rather than on a remembered number.
    """

    def _info(self, width=512, height=288, fov=70.0):
        return ros_schema.camera_info_message(
            1.5,
            ros_schema.CameraSpec(
                name="front_middle", frame_id="cam_front", width=width, height=height,
                fov_deg=fov,
            ),
        )

    def test_the_focal_length_is_the_horizontal_fov_the_lens_was_set_to(self):
        """`camera_rig.mount` calls panda3d's one-argument `setFov`, which is the HORIZONTAL
        angle. Reading it as vertical on a 16:9 frame is a 1.78x error in fx that nothing
        raises: the picture is unchanged and only the reprojection is wrong."""
        focal = ros_schema.focal_length_px(512, 70.0)
        assert focal == pytest.approx(256.0 / math.tan(math.radians(35.0)))
        assert focal == pytest.approx(365.6, abs=0.1)
        # The inverse has to come back, or a consumer recovering the FOV from K gets a different
        # camera than the one that rendered.
        recovered = 2.0 * math.degrees(math.atan((512 / 2.0) / focal))
        assert recovered == pytest.approx(70.0)

    def test_a_wider_lens_is_a_shorter_focal_length(self):
        assert ros_schema.focal_length_px(512, 120.0) < ros_schema.focal_length_px(512, 70.0)

    def test_the_pixels_are_square_and_the_principal_point_is_the_centre(self):
        info = self._info()
        assert info["k"][0] == info["k"][4]
        assert (info["k"][2], info["k"][5]) == (256.0, 144.0)
        assert info["k"][8] == 1.0
        assert (info["width"], info["height"]) == (512, 288)

    def test_p_is_k_with_a_zero_translation_column(self):
        """There is no stereo baseline because there is no stereo pair. A non-zero `p[3]` would
        tell a consumer this camera is offset from a rectified rig that does not exist."""
        info = self._info()
        k, p = info["k"], info["p"]
        assert p == [k[0], 0.0, k[2], 0.0, 0.0, k[4], k[5], 0.0, 0.0, 0.0, 1.0, 0.0]
        assert info["r"] == [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]

    def test_no_distortion_is_claimed_rather_than_a_measured_zero(self):
        """`plumb_bob` with five zeros would say a calibration was done and came out perfect,
        which no calibration ever does. An empty model against an empty `d` is ROS's way of
        saying the publisher does not model one - the same rule `UNKNOWN_COVARIANCE` follows."""
        info = self._info()
        assert info["distortion_model"] == ""
        assert info["d"] == []

    def test_the_frame_is_the_cameras_own_and_the_stamp_is_the_frames(self):
        info = self._info()
        assert info["header"]["frame_id"] == "cam_front"
        assert info["header"]["stamp"] == stamp(1.5)


class TestTheRigCameraNames:
    """Which rig topic a spec's camera goes out on, and the one place the answer is contested.

    `rigs/av3.txt` was generated from the checkpoint's own `camera_order`, so its six names are
    already the rig's and the map is the identity. `rigs/cams.txt` names its cameras after the
    file rather than the vehicle, needs a translation - and **contradicts itself about which way
    two of them face.** `camera_rig.Camera.aim` has the measurement: that file reads `+yaw` as
    right on its front pair and as left on its back pair, so two of its four side cameras are
    named backwards under either reading.

    The map follows the names; `/tf_static` carries the geometry; `camera_side_disagreements`
    names every camera where the two part company. None of the three is allowed to silently
    overrule another, because each is right about something different.
    """

    def _rig(self, name):
        import camera_rig

        return camera_rig.load_rig(REPO / "rigs" / name, read_interval_s=None)

    def test_the_av3_rig_needs_no_translation_at_all(self):
        import ros_frame

        cameras = ros_frame.cameras_from_rig(self._rig("av3.txt"))
        assert {camera.name for camera in cameras} == {
            "front_left", "front_middle", "front_right",
            "rear_left", "rear_middle", "rear_right",
        }
        assert all(camera.name == camera.frame_id for camera in cameras)
        assert ros_frame.unmapped_cameras(self._rig("av3.txt")) == ()

    def test_the_cams_rig_translates_to_the_same_six(self):
        import ros_frame

        cameras = ros_frame.cameras_from_rig(self._rig("cams.txt"))
        assert {camera.name for camera in cameras} == {
            "front_left", "front_middle", "front_right",
            "rear_left", "rear_middle", "rear_right",
        }
        by_rig_name = {camera.name: camera.frame_id for camera in cameras}
        assert by_rig_name["front_left"] == "cam_left"
        assert by_rig_name["front_middle"] == "cam_front"

    def test_the_spare_camera_gets_no_invented_topic(self):
        """`cam_front_wide` is a seventh buffer with no channel on the vehicle. It is mounted,
        rendered and in `/tf_static`, where it is honestly a seventh camera; giving it a
        `cam_sync_rig` topic would put a channel in our bag that the rig's bag cannot have."""
        import ros_frame

        rig = self._rig("cams.txt")
        assert len(rig.cameras) == 7
        assert len(ros_frame.cameras_from_rig(rig)) == 6
        assert ros_frame.unmapped_cameras(rig) == ("cam_front_wide",)

    def test_every_rig_name_maps_to_a_topic_the_reference_bag_has(self):
        rig_topics = {row.topic for row in ros_schema.RIG_TOPICS}
        for rig_name in set(ros_schema.RIG_CAMERA_NAMES.values()):
            assert ros_schema.camera_topic(rig_name) in rig_topics, rig_name

    def test_a_side_is_read_off_a_yaw_the_way_the_rig_names_divide_the_car(self):
        assert ros_schema.aim_side(math.radians(55.0)) == "left"
        assert ros_schema.aim_side(math.radians(-55.0)) == "right"
        assert ros_schema.aim_side(0.0) == "middle"
        assert ros_schema.aim_side(math.pi) == "middle"
        assert ros_schema.aim_side(math.radians(-179.0)) == "middle"
        assert ros_schema.named_side("front_left") == "left"
        assert ros_schema.named_side("rear_middle") == "middle"

    def test_the_av3_rig_has_no_camera_whose_name_and_aim_disagree(self):
        """Its header says both columns agree by construction. This is that claim, checked."""
        import ros_frame

        rig = self._rig("av3.txt")
        mounts = ros_frame.mounts_from_rig(rig)
        pairs = {
            camera.name: (camera.frame_id, mounts[camera.frame_id][3])
            for camera in ros_frame.cameras_from_rig(rig)
        }
        assert ros_schema.camera_side_disagreements(pairs) == []

    def test_the_cams_rig_disagrees_about_exactly_its_back_pair(self):
        """The known defect in the input file, pinned so it stays visible rather than becoming
        folklore. `cam_back_left` is spec `yaw: 125`, which under the reading its own front pair
        uses is 125 degrees to the RIGHT - so it publishes as `rear_left` and points rear-right.
        Neither the topic nor the transform is altered to hide it."""
        import ros_frame

        rig = self._rig("cams.txt")
        mounts = ros_frame.mounts_from_rig(rig)
        pairs = {
            camera.name: (camera.frame_id, mounts[camera.frame_id][3])
            for camera in ros_frame.cameras_from_rig(rig)
        }
        assert ros_schema.camera_side_disagreements(pairs) == [
            ("rear_left", "left", "right"),
            ("rear_right", "right", "left"),
        ]
        # The front pair is not affected, and that is the half the plan called out by name.
        assert pairs["front_left"][1] > 0
        assert pairs["front_right"][1] < 0


class TestTheCameraTopicsInAWrittenBag:
    """The end of phase 1: six lenses and a transform tree, in a real MCAP file.

    Written with `rigs/cams.txt` itself rather than a hand-built rig, so a change to that file
    cannot quietly invalidate this - the same reason `TestTheCameraMountConversion` parses it.
    """

    @staticmethod
    def _bag(tmp_path):
        import camera_rig
        import ros_bag
        import ros_frame

        rig = camera_rig.load_rig(REPO / "rigs" / "cams.txt", read_interval_s=None)
        frame = _frame()
        path = tmp_path / "camera-bag"
        with ros_bag.BagWriter(path, topics=None, notes={}) as bag:
            bag.start_episode(
                frame,
                mounts=ros_frame.mounts_from_rig(rig),
                cameras=ros_frame.cameras_from_rig(rig),
            )
            bag.write(frame)
        return path

    @staticmethod
    def _read(path):
        import ros_probe

        by_topic, _ = ros_probe.load(path)
        return by_topic

    def test_six_camera_infos_and_one_tf_static_reach_the_bag(self, tmp_path):
        by_topic = self._read(self._bag(tmp_path))
        for topic in ros_schema.CAMERA_INFO_TOPICS:
            assert len(by_topic[topic]) == 1, topic
        assert len(by_topic[ros_schema.TF_STATIC]) == 1

    def test_the_transform_tree_carries_all_seven_cameras(self, tmp_path):
        """Six on topics and one without. `cam_front_wide` has no rig channel, but it is on the
        car and a bag that omitted its transform would be describing a rig it did not render."""
        by_topic = self._read(self._bag(tmp_path))
        _, message = by_topic[ros_schema.TF_STATIC][0]
        assert len(message.transforms) == 7
        assert all(t.header.frame_id == BASE_FRAME for t in message.transforms)

    def test_every_camera_info_joins_its_own_transform_by_frame_id(self, tmp_path):
        """The two halves a consumer needs - a lens and a mount - built from opposite ends of
        `camera_rig.Camera`. A camera in one and not the other is a half-converted rig, and both
        topics deserialise perfectly on their own, so nothing else would notice."""
        by_topic = self._read(self._bag(tmp_path))
        _, static = by_topic[ros_schema.TF_STATIC][0]
        frames = {t.child_frame_id for t in static.transforms}
        for topic in ros_schema.CAMERA_INFO_TOPICS:
            _, info = by_topic[topic][0]
            assert info.header.frame_id in frames, topic

    def test_the_intrinsics_survive_cdr_as_the_spec_wrote_them(self, tmp_path):
        """`k`, `r` and `p` are fixed-length float64 arrays and arrive as numpy, not as lists -
        the same shape trap `conversion.py` pins for the dataset pickles."""
        by_topic = self._read(self._bag(tmp_path))
        _, info = by_topic[ros_schema.camera_topic("front_middle")][0]
        assert (info.width, info.height) == (512, 288)
        assert info.k[0] == pytest.approx(ros_schema.focal_length_px(512, 70.0))
        assert info.k[0] == pytest.approx(info.k[4])
        assert (info.k[2], info.k[5]) == pytest.approx((256.0, 144.0))
        assert len(info.d) == 0

    def test_the_camera_topics_are_offered_latched(self, tmp_path):
        """Transient-local, like `/tf_static` and the route. One message per camera for a whole
        drive: offered volatile it goes out once while a viewer is still starting and is never
        seen again, and neither the player nor the subscriber says a word."""
        from rosbags.rosbag2 import Reader

        with Reader(self._bag(tmp_path)) as reader:
            offered = {c.topic: c.ext.offered_qos_profiles for c in reader.connections}
        for topic in ros_schema.CAMERA_INFO_TOPICS:
            assert offered[topic], topic
            assert offered[topic][0].durability.name == "TRANSIENT_LOCAL", topic

    def test_the_topic_selection_reaches_the_latched_topics_too(self, tmp_path):
        """`--ros-topics` says "the subset to write", and the latched three did not honour it.

        `ros_schema.messages` filtered the per-frame topics from the day it was written; the route
        and `/tf_static` were put unconditionally. That was two surprises and became eight once
        phase 1 added the lenses - a `--ros-topics /tf` bag holding eight channels nobody asked
        for. Nothing raises: the bag is valid and merely larger and stranger than requested.
        """
        import camera_rig
        import ros_bag
        import ros_frame

        rig = camera_rig.load_rig(REPO / "rigs" / "cams.txt", read_interval_s=None)
        frame = _frame()
        path = tmp_path / "picked-bag"
        picked = {ros_schema.TF, ros_schema.camera_topic("front_middle")}
        with ros_bag.BagWriter(path, topics=picked, notes={}) as bag:
            bag.start_episode(
                frame,
                route=((0.0, 0.0), (1.0, 1.0)),
                mounts=ros_frame.mounts_from_rig(rig),
                cameras=ros_frame.cameras_from_rig(rig),
            )
            bag.write(frame)
        assert set(self._read(path)) == picked
