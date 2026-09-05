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
from dataclasses import replace
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
    SECONDS_PER_WEEK,
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
        for topic, _msgtype, content in messages(frame):
            # `/sensing/gnss/imu/utc_ref` is the one exception, and it is the exception on
            # purpose: a `TimeReference` exists to express one clock in terms of another, so its
            # `time_ref` is the declared UTC and differs from `header.stamp` by exactly the
            # epoch. Publishing them equal would say "our clock is UTC". Its header is still
            # swept below with everything else.
            if topic == ros_schema.GNSS_UTC_REF:
                assert content["time_ref"] == stamp(
                    ros_schema.GPS_EPOCH_UNIX_S + frame.sim_time_s
                )
                seen.update(_stamps(content["header"]))
                continue
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
        """Run through the real parser rather than a shape heuristic.

        This used to assert two whitespace-separated tokens per line, which held while every
        definition here was a compact string written by hand. The `sbg_driver` ones are upstream
        files carrying comments and blank lines, and a rule that would have rejected them was
        never testing the thing that matters: `rosbags` has to be able to parse the text, and
        a definition that cannot be parsed cannot be registered or written.
        """
        from rosbags.typesys import get_types_from_msg

        for name, text in EXTRA_DEFINITIONS.items():
            assert name.count("/") == 2 and "/msg/" in name, name
            parsed = get_types_from_msg(text, name)
            assert name in parsed, name
            assert parsed[name][1], f"{name} declares no fields"


class TestTheRigCoverageLedger:
    """`RIG_TOPICS` - the vehicle's own recording as data, and the count derived off it.

    This exists because a prose ledger and a code table drift apart in silence, and they have
    now done so twice. First within the repo: `docs/rosbag.md` verdicted the topics and
    `MISSING_DEFINITIONS` listed what we lacked a `.msg` for, and **nothing cross-referenced the
    two**, so both of the following were true at once and neither was visible to any check:

    * `/sensing/gnss/imu_data` was producible and absent from `MISSING_DEFINITIONS` altogether -
      one character away from `/sensing/gnss/imu/data`, which we do publish.
    * `/sensing/gnss/imu/temp` and `/sensing/gnss/status` sat *in* it, as though a `.msg` were
      all that stood between a simulator and a real receiver's temperature.

    Both are now structurally impossible rather than merely fixed: `MISSING_DEFINITIONS` is
    computed from these rows, so a topic can no longer be in one and not the other.

    **Then between the repo and the vehicle**, which is the drift these rows now close. The 55
    they described came from `bag_audit.html`, an audit of an older recording, and by 2026-09-03
    five of them were wrong: `/control/actuators` had become `/control/openpilot/actuators`,
    `/vehicle/actuators_output` was gone, `/sensing/lidar/points` had become
    `/sensing/lidar/points/soa_zstd`, and both CAN topics had stopped being recorded. So the
    rows are now built from `tools/reference_bag.json`, extracted from the vehicle's own bag, and
    only the *judgements* - not producible, truth-rather-than-measurement, still to build - are
    kept in code.
    """

    def test_the_ledger_is_the_reference_bags_own_50(self):
        reference = ros_schema._reference()["topics"]
        assert len(ros_schema.RIG_TOPICS) == 50
        assert {row.topic for row in ros_schema.RIG_TOPICS} == set(reference)

    def test_every_rate_is_the_recordings_own_and_not_a_number_typed_in(self):
        """The half of a row that must never be maintained by hand - see the class docstring."""
        reference = ros_schema._reference()["topics"]
        for row in ros_schema.RIG_TOPICS:
            assert row.hz == reference[row.topic]["hz"], row.topic

    def test_every_type_we_declare_is_the_type_the_vehicle_publishes(self):
        """The check that caught `/sensing/gnss/pose`, and the reason the table is derived.

        We published `geometry_msgs/PoseStamped` there; the vehicle publishes
        `sensor_msgs/NavSatFix`. **Same topic name, different contents** - the fault this
        module's opening paragraph warns about, shipped since stage 10 and invisible to every
        check that existed, because no check had the vehicle's own answer to compare against.
        """
        reference = ros_schema._reference()["topics"]
        for topic, (declared, _family) in TOPICS.items():
            if topic in reference:
                assert declared == reference[topic]["type"], topic

    def test_the_verdicts_split_the_way_the_doc_argues_them(self):
        counts = ros_schema.rig_coverage()["verdicts"]
        assert counts == {
            ros_schema.DIRECT: 17,
            ros_schema.APPROXIMATE: 19,
            ros_schema.IMPOSSIBLE: 14,
        }

    def test_36_is_50_less_the_fourteen_a_simulator_cannot_honestly_produce(self):
        """The target, and the reason it is not 50.

        A bag claiming all 50 would be claiming a cabin camera, a microphone, a GNSS receiver's
        own temperature and health, and **six image sensors with an exposure and a gain**. Each
        of those absences is a fact about the vehicle, and a consumer can test for a topic that
        is not there - it cannot test for one that is there and invented.
        """
        assert ros_schema.rig_coverage()["producible"] == 36

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

        **Phases 1 to 4 landed, so 30 is now the floor and all four are gone from the
        ladder.** A phase that is done leaves the `absent` table entirely rather than lingering
        with a count of zero; the running total starting at 30 is what says so.
        """
        ledger = ros_schema.rig_coverage()
        per_phase = {phase: len(rows) for phase, rows in ledger["absent"].items()}
        assert per_phase == {}, "a phase with nothing left in it leaves the table entirely"
        assert len(ledger["produced"]) == 36
        assert len(ledger["produced"]) == ledger["producible"], "the ladder is finished"

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
        the `sensor_msgs/Imu` we publish. Confusing them is how one of them vanished.

        **Phase 2 published both**, which removes the asymmetry that made the defect possible
        and replaces it with a sharper requirement: two topics one character apart must carry
        two different types on two different builders, or one of them is quietly the other.
        """
        assert "/sensing/gnss/imu/data" in TOPICS
        assert "/sensing/gnss/imu_data" in TOPICS
        assert TOPICS["/sensing/gnss/imu/data"][0] != TOPICS["/sensing/gnss/imu_data"][0]
        assert BUILDERS["/sensing/gnss/imu/data"] is not BUILDERS["/sensing/gnss/imu_data"]
        assert "/sensing/gnss/imu_data" not in ros_schema.AWAITING_BUILDER

    def test_missing_definitions_is_derived_and_never_names_a_topic_off_the_ledger(self):
        rig = {row.topic for row in ros_schema.RIG_TOPICS}
        for topic, reason in ros_schema.MISSING_DEFINITIONS.items():
            assert topic in rig, topic
            assert ros_schema.RIG_BY_TOPIC[topic].producible, topic
            assert reason, topic

    def test_a_row_says_what_it_needs_exactly_when_we_do_not_write_it(self):
        """`phase` is the plan's record of which phase owns a topic and stays true after that
        phase lands; `produced` is a question about `TOPICS`, asked now. Conflating the two is
        how a row claims to be waiting on work that is already done, which made the coverage
        total and the per-phase breakdown disagree by exactly the number of rows that had
        landed."""
        for row in ros_schema.RIG_TOPICS:
            if row.producible and not row.produced:
                assert row.needs, row.topic
                assert row.phase is not None, row.topic

    def test_the_declared_twenty_nine_are_the_rig_topics_this_module_actually_builds(self):
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
            # Phase 2: seven `sbg_driver` types and two that were core all along.
            "/sensing/gnss/ekf_nav",
            "/sensing/gnss/ekf_quat",
            "/sensing/gnss/ekf_euler",
            "/sensing/gnss/imu_data",
            "/sensing/gnss/gps_pos",
            "/sensing/gnss/gps_vel",
            "/sensing/gnss/utc_time",
            "/sensing/gnss/imu/pos_ecef",
            "/sensing/gnss/imu/utc_ref",
            # Phase 4, and the only six whose payload has to be decoded before it can be read.
            *ros_schema.CAMERA_PACKET_TOPICS,
            # Phase 5, slice 2: the only three built from what the drive *commanded* rather
            # than from what it observed.
            ros_schema.VEHICLE_STATE,
            ros_schema.VEHICLE_ENGAGEMENT,
            ros_schema.CONTROL_ACTUATORS,
            # Slice 3: the vehicle's own cloud, compressed, beside our plain one.
            ros_schema.LIDAR_POINTS_COMPRESSED,
            # Slice 4: written only on a drive with a model at the wheel.
            ros_schema.PREDICTED_TRAJECTORY,
            ros_schema.INFERENCE_CONTROL,
            ros_schema.MODEL_INFO,
        }
        # Phase 3's cloud is no longer one of the vehicle's topics - it publishes a compressed
        # one under another name - so it is counted as ours rather than as coverage.
        assert ros_schema.LIDAR_POINTS in ros_schema.SIMULATOR_EXTRAS

    def test_a_bag_is_counted_by_what_reached_the_wire_not_by_what_was_declared(self):
        """30 declared, 12 written on a plain drive with no rig, no lidar and no projection.

        **Three** independent reasons a declared topic misses the wire now, and this pins each.
        `/tf_static` is guarded by `if mounts:` in `ros_bag.py`, and the six `camera_info_latched`
        topics and the six `image_raw/ffmpeg` ones all need a rig, so a drive that mounts no
        `--camera-rig` writes none of the thirteen. `/sensing/lidar/points` needs `--ros-lidar`
        and its builder returns None without a sweep in the frame. The four `GEODETIC_TOPICS`
        need a real position. Reporting the declared figure against such a bag would hide all
        eighteen, and every phase widens the gap rather than narrowing it.
        """
        declared = {row.topic for row in ros_schema.rig_coverage()["declared"]}
        assert len(declared) == 36

        # No `--camera-rig`: the transform tree, the six lenses and the six camera streams are
        # all declared and absent. The cloud is no longer counted here at all - see above.
        asked_for = {
            "/tf_static",
            *ros_schema.CAMERA_INFO_TOPICS,
            *ros_schema.CAMERA_PACKET_TOPICS,
        }
        no_rig = ros_schema.rig_coverage(declared - asked_for)
        assert len(no_rig["declared"]) == 36
        assert len(no_rig["produced"]) == 23

        # No projection either: the five topics that need a real position drop as well. Three
        # are phase 2's and `/sensing/gnss/pose` joined them when it became a `NavSatFix`, so
        # this gap grew with each correction rather than shrinking.
        neither = ros_schema.rig_coverage(
            declared - asked_for - set(ros_schema.GEODETIC_TOPICS)
        )
        assert len(neither["produced"]) == 18

    def test_the_two_rates_above_the_simulator_tick_are_recorded_as_such(self):
        """`env.step` is the world tick, so every rate here is a decimation of `--step-hz`.
        These two are not, and a bag that claims them is claiming a clock it does not have."""
        fast = {row.topic: row.hz for row in ros_schema.RIG_TOPICS if (row.hz or 0) > 100.0}
        assert fast == {
            "/sensing/gnss/imu/velocity": 199.99,
            "/sensing/lidar/imu": 203.05,
            "/tf": 100.89,
        }

    def test_the_coverage_report_runs_with_no_bag_at_all(self):
        """It is a question about the code, not about a recording, and answers before one
        exists. `ros_probe.py` otherwise requires a bag and refuses without one."""
        import io

        import ros_probe

        rendered = io.StringIO()
        assert ros_probe.coverage(None, out=rendered)
        text = rendered.getvalue()
        assert "36 / 36" in text
        assert "17 direct, 19 approximate, 14 not producible" in text

    def test_the_probe_still_refuses_a_run_with_neither_a_bag_nor_coverage(self):
        import ros_probe

        with pytest.raises(SystemExit):
            ros_probe.main([])

    def test_the_probe_binds_numpy_at_module_scope(self):
        """`probe()` reads `numpy` in three places and used to *import* it in only two of them,
        both behind a condition: `if clouds:` and `if streams:`. The third reader is the
        steering-sign check, in the same function, so a bag with neither a point cloud nor
        camera packets reached it with the name unbound and the whole probe died with
        `UnboundLocalError: local variable 'numpy' referenced before assignment`.

        **That is the plainest useful bag there is.** It hid for so long because the crash also
        needs the bag to be self-driven: a replay bag writes no vehicle state, so `turning` is
        empty, the branch never runs, and `bags/j1-lights` passed all 13 of its checks either
        way. It took an `--agent-policy idm` drive recorded without `--ros-camera` or
        `--ros-lidar` - which is what a rig records first - to reach it.

        Asserting the module attribute rather than re-running `probe()`, because reproducing it
        properly needs a written bag and this pins the one thing that was wrong.
        """
        import ros_probe

        assert hasattr(ros_probe, "numpy"), (
            "numpy must be bound at module scope - a conditional import inside probe() is what "
            "made the steering-sign check crash on a bag with no lidar and no cameras"
        )
        source = (REPO / "tools" / "ros_probe.py").read_text(encoding="utf-8")
        assert "        import numpy" not in source, (
            "an indented `import numpy` is back inside a function; every reader of the name in "
            "probe() must be able to rely on the module-scope binding"
        )


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

    `MISSING_DEFINITIONS` used to give the reason as "type not in the audit", which was true and
    pointed at the wrong place. `bag_audit.html` records *rates*; the only message type named
    anywhere in it is `geometry_msgs/TwistStamped`. The definitions were never going to be there,
    and the reason now names the command that gets them instead.

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


class TestTheSbgFamily:
    """The nine channels stage 11 phase 2 landed, and the two things they can get wrong.

    Seven of them are `sbg_driver` types, which `Stores.ROS2_HUMBLE` has never heard of, so the
    first risk is the one `EXTRA_DEFINITIONS` has always carried: **a field out of order
    serialises silently and deserialises into nonsense.** That is why the definitions are files
    copied verbatim from a named upstream commit rather than text retyped here, and why the
    round trip below goes through real CDR rather than comparing dicts.

    The second is arithmetic. Every value in all nine is a re-shaping of one position and one
    velocity, so a swapped north and east, a bearing measured from north instead of east, or a
    second path to latitude that drifted from the first all produce a bag that opens, decodes
    and plots a car on a road. The tests are therefore mostly one channel against another.
    """

    _PROJECTION = Projection(origin_lat=3.15, origin_lon=101.6, offset_x=0.0, offset_y=0.0)

    def _frame(self, **over):
        base = dict(
            index=3,
            sim_time_s=1.25,
            ego=_ego(heading=0.3, velocity_east=8.0, velocity_north=3.0, yaw_rate=0.11,
                     roll=0.01, pitch=-0.02),
            projection=self._PROJECTION,
        )
        base.update(over)
        return Frame(**base)

    # -- the definitions ------------------------------------------------------------------
    def test_the_definitions_are_files_on_disk_not_text_retyped_into_this_repo(self):
        """"Copied verbatim" is a claim somebody has to be able to check against upstream, and
        a `.msg` rewrapped into a Python string literal cannot be diffed against anything."""
        names = sorted(path.stem for path in ros_schema.SBG_MSG_DIR.glob("*.msg"))
        assert names == [
            "SbgEkfEuler", "SbgEkfNav", "SbgEkfQuat", "SbgEkfStatus",
            "SbgGpsPos", "SbgGpsPosStatus", "SbgGpsVel", "SbgGpsVelStatus",
            "SbgImuData", "SbgImuStatus", "SbgUtcTime", "SbgUtcTimeStatus",
        ]
        for name in names:
            assert f"sbg_driver/msg/{name}" in EXTRA_DEFINITIONS

    def test_the_five_nested_status_types_are_carried_as_well_as_the_seven_published_ones(self):
        """A message type is useless without the submessages it names, and only seven of the
        twelve are ever a topic. Registering `SbgEkfNav` without `SbgEkfStatus` fails loudly at
        parse time, which is the good case - but it fails at the first bag, not in a test."""
        published = {
            TOPICS[topic][0]
            for topic in (
                ros_schema.SBG_EKF_NAV, ros_schema.SBG_EKF_QUAT, ros_schema.SBG_EKF_EULER,
                ros_schema.SBG_IMU_DATA, ros_schema.SBG_GPS_POS, ros_schema.SBG_GPS_VEL,
                ros_schema.SBG_UTC_TIME,
            )
        }
        loaded = {key for key in EXTRA_DEFINITIONS if key.startswith("sbg_driver/")}
        assert published < loaded
        assert len(loaded - published) == 5

    def test_the_driver_version_is_pinned_and_travels_with_every_bag(self):
        """CDR carries no field names, so a consumer decoding these with its own installed
        `sbg_driver` needs to know which one they were written against - and the field lists
        did change between releases (`SbgEkfStatus` went 16 fields to 23, two of them removed).
        """
        import ros_bag

        assert ros_schema.SBG_DRIVER_VERSION == "3.4.0"
        assert len(ros_schema.SBG_DRIVER_COMMIT) == 40
        writer = ros_bag.BagWriter("unused", topics=None, notes={})
        assert writer.summary()["sbg_driver_version"] == "3.4.0"

    def test_every_sbg_type_round_trips_through_real_cdr(self):
        """The check the whole copy-rather-than-invent rule exists for. A field in the wrong
        order does not raise here either - but a value that comes back changed does, and every
        field this module writes is read back below."""
        import ros_bag

        store = ros_bag._typestore()
        frame = self._frame()
        for topic, msgtype, content in messages(frame):
            if not msgtype.startswith(("sbg_driver/", "sensor_msgs/msg/TimeReference")):
                continue
            message = ros_bag._message(store, msgtype, content)
            back = store.deserialize_cdr(store.serialize_cdr(message, msgtype), msgtype)
            assert back.header.frame_id == BASE_FRAME, topic
            assert back.header.stamp.sec == 1 and back.header.stamp.nanosec == 250_000_000

    # -- one position, several channels ---------------------------------------------------
    def test_the_geodetic_topics_vanish_together_when_the_dataset_has_no_projection(self):
        """A bag holding `ekf_nav` on a dataset that cannot say where it is would be holding a
        latitude of zero, off the coast of Ghana. `gnss_fix_message` already refused; the three
        new ones that need a real position have to refuse in exactly the same breath."""
        written = {topic for topic, _, _ in messages(_frame())}
        assert written & set(ros_schema.GEODETIC_TOPICS) == set()
        with_projection = {topic for topic, _, _ in messages(self._frame())}
        assert set(ros_schema.GEODETIC_TOPICS) <= with_projection

    def test_ekf_nav_gps_pos_and_nav_sat_fix_are_the_same_position(self):
        """There is no lever arm and no Kalman filter in a simulator, so the fused solution, the
        raw one and the driver's `NavSatFix` are one position reached three ways. A difference
        could only be a second conversion path - which is how 93.8 m of origin shift goes
        missing without anything looking wrong."""
        frame = self._frame()
        fix = ros_schema.gnss_fix_message(frame)
        nav = ros_schema.ekf_nav_message(frame)
        raw = ros_schema.gps_pos_message(frame)
        assert nav["latitude"] == fix["latitude"] == raw["latitude"]
        assert nav["longitude"] == fix["longitude"] == raw["longitude"]
        assert nav["altitude"] == fix["altitude"] == raw["altitude"]

    def test_pos_ecef_is_where_the_fix_puts_it_and_hangs_off_the_earth_frame(self):
        """REP-105's `earth` is the only frame in this bag that is not ours, and it is what
        joins a drive on junction-1 to anything else on the planet. Under `map` it would be
        93.8 m of local metres wearing global units."""
        import geodesy

        frame = self._frame()
        fix = ros_schema.gnss_fix_message(frame)
        point = ros_schema.pos_ecef_message(frame)
        assert point["header"]["frame_id"] == ros_schema.EARTH_FRAME
        expected = geodesy.geodetic_to_ecef(fix["latitude"], fix["longitude"], fix["altitude"])
        assert (point["point"]["x"], point["point"]["y"], point["point"]["z"]) == expected
        # Kuala Lumpur, not the centre of the earth and not the other hemisphere.
        assert math.dist(expected, (0.0, 0.0, 0.0)) == pytest.approx(6_378_000, abs=30_000)

    # -- the conventions that are silent when wrong ---------------------------------------
    def test_velocity_is_enu_so_x_is_east_and_y_is_north(self):
        """The driver publishes NED under its other setting, where the same two floats mean
        north and east. Reading one as the other is a 90 degree error that still plots a car
        driving along a road."""
        nav = ros_schema.ekf_nav_message(self._frame())
        vel = ros_schema.gps_vel_message(self._frame())
        assert (nav["velocity"]["x"], nav["velocity"]["y"]) == (8.0, 3.0)
        assert vel["velocity"] == nav["velocity"]

    def test_the_course_is_measured_from_east_counter_clockwise_not_from_north(self):
        """ENU course is zero pointing east; NED course is zero pointing north and counts the
        other way. Driving due north is 90 degrees under one and 0 under the other."""
        north = ros_schema.gps_vel_message(
            self._frame(ego=_ego(velocity_east=0.0, velocity_north=5.0))
        )
        assert north["course"] == pytest.approx(90.0)
        east = ros_schema.gps_vel_message(
            self._frame(ego=_ego(velocity_east=5.0, velocity_north=0.0))
        )
        assert east["course"] == pytest.approx(0.0)
        assert 0.0 <= ros_schema.gps_vel_message(
            self._frame(ego=_ego(velocity_east=-5.0, velocity_north=-5.0))
        )["course"] < 360.0

    def test_the_course_comes_from_the_velocity_rather_than_from_the_heading(self):
        """They are not the same thing - a car sliding has a course that differs from where it
        is pointing - and taking the easy one would make the probe's cross-check tautological."""
        turned = ros_schema.gps_vel_message(self._frame(ego=_ego(
            heading=2.0, velocity_east=5.0, velocity_north=0.0
        )))
        assert turned["course"] == pytest.approx(0.0)

    def test_ekf_euler_and_ekf_quat_and_the_imu_describe_one_rotation(self):
        frame = self._frame()
        quat = ros_schema.ekf_quat_message(frame)
        euler = ros_schema.ekf_euler_message(frame)
        assert quat["quaternion"] == ros_schema.imu_message(frame)["orientation"]
        assert (euler["angle"]["x"], euler["angle"]["y"], euler["angle"]["z"]) == (
            0.01, -0.02, 0.3
        )
        assert quat["quaternion"] == quaternion(0.3, -0.02, 0.01)

    def test_the_raw_gyro_is_the_number_the_other_imu_topic_publishes(self):
        """`/sensing/gnss/imu_data` and `/sensing/gnss/imu/data` are one character apart and
        two different types. Confusing them is how one of them fell out of the ledger."""
        frame = self._frame()
        raw = ros_schema.sbg_imu_message(frame)
        assert raw["gyro"] == ros_schema.imu_message(frame)["angular_velocity"]
        assert TOPICS[ros_schema.SBG_IMU_DATA][0] == "sbg_driver/msg/SbgImuData"
        assert TOPICS[ros_schema.GNSS_IMU][0] == "sensor_msgs/msg/Imu"

    # -- absence, in a message type with no way to state it -------------------------------
    def test_what_the_simulator_does_not_have_is_nan_and_never_a_plausible_zero(self):
        """`sensor_msgs/Imu` has a -1 for this and `NavSatFix` a covariance type; `SbgImuData`
        has neither, and every float32 a temperature field can hold is a temperature - 0.0 reads
        as a sensor sitting at freezing. NaN is the only value a consumer cannot quietly
        believe, and `/sensing/gnss/imu/temp` is excluded from the 45 for the same reason this
        is absent."""
        frame = self._frame()
        raw = ros_schema.sbg_imu_message(frame)
        assert math.isnan(raw["temp"])
        for field in ("accel", "delta_vel", "delta_angle"):
            assert all(math.isnan(value) for value in raw[field].values()), field
        assert math.isnan(ros_schema.ekf_nav_message(frame)["undulation"])
        assert math.isnan(ros_schema.gps_pos_message(frame)["undulation"])

    def test_an_accuracy_of_zero_is_a_true_statement_and_stays_a_number(self):
        """The distinction NaN would destroy. A 1-sigma accuracy of zero says the position is
        exact, and for ground truth that is *correct* - unlike a temperature, which does not
        exist at all. Turning these into NaN would throw away a real fact about the data."""
        frame = self._frame()
        assert ros_schema.ekf_nav_message(frame)["position_accuracy"] == {
            "x": 0.0, "y": 0.0, "z": 0.0
        }
        assert ros_schema.gps_vel_message(frame)["course_acc"] == 0.0
        assert ros_schema.ekf_quat_message(frame)["accuracy"]["x"] == 0.0
        assert ros_schema.utc_time_message(frame)["clk_bias_std"] == 0.0

    def test_the_satellite_counts_use_the_messages_own_not_available_rather_than_ours(self):
        """0xFF is what `SbgGpsPos.msg` itself documents as N/A, so nothing has to learn one of
        our conventions to read it. A type that provides an absence value gets used."""
        raw = ros_schema.gps_pos_message(self._frame())
        assert raw["num_sv_tracked"] == raw["num_sv_used"] == 0xFF
        assert raw["base_station_id"] == 0 and raw["diff_age"] == 0

    def test_the_filter_claims_a_full_solution_aided_by_nothing(self):
        """`solution_mode` 4 is NAV_POSITION and ground truth meets every `*_valid` bound. But
        there is no receiver, no magnetometer and no odometer, so every `*_used` flag is False -
        a True on `gps1_pos_used` would describe a fusion that never happened."""
        status = ros_schema.ekf_nav_message(self._frame())["status"]
        assert status["solution_mode"] == 4
        assert all(status[name] for name in
                   ("attitude_valid", "heading_valid", "velocity_valid", "position_valid"))
        used = {name: value for name, value in status.items() if name.endswith("_used")}
        assert used and not any(used.values()), sorted(k for k, v in used.items() if v)

    # -- the clock, which the simulator does not have ------------------------------------
    def test_the_drive_declares_the_gps_epoch_rather_than_stamping_a_believable_date(self):
        """A wall clock taken at conversion time would make the bag claim the drive happened
        then, and make two runs of one drive differ. 1980-01-06 is a sentinel nobody mistakes
        for a recording session, and `clock_utc_status` 0 says the same thing in-band."""
        utc = ros_schema.utc_time_message(self._frame())
        assert (utc["year"], utc["month"], utc["day"]) == (1980, 1, 6)
        assert utc["clock_status"]["clock_utc_status"] == 0
        assert not utc["clock_status"]["clock_utc_sync"]

    def test_elapsed_time_inside_the_drive_is_exact_even_though_the_date_is_a_sentinel(self):
        utc = ros_schema.utc_time_message(self._frame(sim_time_s=3661.25))
        assert (utc["hour"], utc["min"], utc["sec"]) == (1, 1, 1)
        assert utc["nanosec"] == pytest.approx(250_000_000, abs=1)
        assert utc["gps_tow"] == 3_661_250

    def test_utc_ref_offsets_the_two_clocks_rather_than_claiming_they_are_the_same(self):
        """Publishing `time_ref` equal to `header.stamp` says "our clock is UTC", which is the
        one thing a simulated drive cannot claim - and `TimeReference` exists precisely to
        express one clock in terms of another."""
        ref = ros_schema.utc_ref_message(self._frame())
        assert ref["header"]["stamp"] == stamp(1.25)
        assert ref["time_ref"]["sec"] - ref["header"]["stamp"]["sec"] == (
            ros_schema.GPS_EPOCH_UNIX_S
        )
        assert "simulated" in ref["source"]

    def test_the_device_clock_wraps_instead_of_overflowing_its_field_mid_drive(self):
        """uint32 microseconds is 71.6 minutes. Left to overflow it would raise inside CDR on
        one frame somewhere in the middle of a long drive - the wrap is what the device does."""
        long_run = ros_schema.ekf_nav_message(self._frame(sim_time_s=7200.0))
        assert 0 <= long_run["time_stamp"] < 2**32
        assert ros_schema.utc_time_message(self._frame(sim_time_s=SECONDS_PER_WEEK + 5.0))[
            "gps_tow"
        ] == 5000


class TestTheLidarSweep:
    """`lidar_points_message` - phase 3, and the only payload in this bag that is a shape.

    Everything else here is a handful of numbers whose meaning is carried by a field name. A
    `PointCloud2` is a wall of bytes plus a header describing how to read it, so a mistake in
    either does not produce a wrong number - it produces a different cloud, or garbage, with
    every field of the message still perfectly well-formed.

    The rotation is the part that had to be got right. MetaDrive hands the sweep over on world
    axes with its origin at the sensor, and turning that into the sensor's own frame is one
    rotation by minus the car's heading. Backwards, it is the same points rigidly rotated: a
    plausible road, a plausible density, a plausible extent, and every point behind the car.
    """

    HEADING = math.radians(101.2)
    """junction-1's own first heading, so these cases sit where the measured ones do rather than
    at zero - where a rotation and its inverse are the same thing and nothing is tested."""

    def _cloud(self, world_points, max_range_m=200.0, heading=None):
        """A frame carrying one sweep. `world_points` is `(height, width, 3)`, world axes."""
        import numpy

        ego = Ego(
            x=0.0, y=0.0, z=0.0,
            heading=self.HEADING if heading is None else heading,
            velocity_east=0.0, velocity_north=0.0, speed=0.0, yaw_rate=0.0,
            roll=0.0, pitch=0.0,
        )
        cloud = ros_schema.LidarCloud(
            points=numpy.asarray(world_points, dtype=float),
            fov_deg=65.0,
            max_range_m=max_range_m,
        )
        return Frame(index=3, sim_time_s=0.4, ego=ego, extra={"lidar": cloud})

    def _read(self, message):
        import numpy

        return numpy.frombuffer(bytes(message["data"]), dtype="<f4").reshape(
            message["height"], message["width"], 3
        )

    def _bearing(self, heading, distance):
        """A point `distance` ahead of a car on `heading`, on world axes."""
        return (distance * math.cos(heading), distance * math.sin(heading), 0.0)

    def test_a_drive_with_no_lidar_drops_the_topic_whole(self):
        """The shape `gnss_fix_message` already uses: None, not an empty cloud.

        A zero-point `PointCloud2` every frame would be a channel a consumer subscribes to, gets
        messages on, and never sees a return in - which reads as a sensor that saw nothing
        rather than as a drive that never had one.
        """
        assert ros_schema.lidar_points_message(_frame()) is None

    def test_a_point_ahead_of_the_car_is_ahead_of_the_sensor(self):
        """+x is forward. This is the check the whole frame choice exists to make possible."""
        message = ros_schema.lidar_points_message(
            self._cloud([[self._bearing(self.HEADING, 10.0)]])
        )
        x, y, z = self._read(message)[0, 0]
        assert x == pytest.approx(10.0, abs=1e-4)
        assert y == pytest.approx(0.0, abs=1e-4)
        assert z == pytest.approx(0.0, abs=1e-4)
        assert message["header"]["frame_id"] == ros_schema.LIDAR_FRAME

    def test_a_point_to_the_cars_left_gets_a_positive_y(self):
        """REP-103: +y is LEFT. The sign that mirrors a whole cloud without changing its shape."""
        left = self._bearing(self.HEADING + math.pi / 2, 7.0)
        x, y, _ = self._read(ros_schema.lidar_points_message(self._cloud([[left]])))[0, 0]
        assert x == pytest.approx(0.0, abs=1e-4)
        assert y == pytest.approx(7.0, abs=1e-4)

    def test_rotating_the_wrong_way_puts_the_road_behind_the_car(self):
        """Why `ros_probe` can catch the sign at all, stated as an arithmetic fact.

        A forward cone de-rotated by *plus* the heading lands at twice the heading from +x -
        202.4 deg here, which is behind. Nothing about the cloud's shape says so, which is why
        the probe tests the bearing of every point rather than looking at the cloud.
        """
        ahead = self._bearing(self.HEADING, 10.0)
        turned = math.atan2(ahead[1], ahead[0]) + self.HEADING
        assert abs(math.degrees(((turned + math.pi) % (2 * math.pi)) - math.pi)) > 90.0

    def test_a_ray_that_hit_nothing_keeps_its_slot_and_is_nan(self):
        """The type's own convention for an absent point, and the reason the cloud stays organised.

        Dropping the misses would compact the sweep and destroy the one structure a lidar has -
        which beam a return came from. Filling them with zeros would put a dense ball of points
        at the sensor's own origin, which is a reading, not an absence.
        """
        import numpy

        near = self._bearing(self.HEADING, 10.0)
        far = self._bearing(self.HEADING, 9000.0)
        message = ros_schema.lidar_points_message(self._cloud([[near, far]]))
        points = self._read(message)
        assert message["height"] == 1 and message["width"] == 2
        assert numpy.isfinite(points[0, 0]).all()
        # All three components, never one or two: a half-NaN point is a point.
        assert numpy.isnan(points[0, 1]).all()
        assert message["is_dense"] is False

    def test_is_dense_is_a_claim_about_this_sweep_rather_than_a_constant(self):
        """True when every ray hit, so a reader can trust the flag instead of always testing."""
        near = self._bearing(self.HEADING, 10.0)
        assert ros_schema.lidar_points_message(self._cloud([[near, near]]))["is_dense"] is True

    def test_the_range_gate_is_measured_before_the_rotation_not_after(self):
        """So a wrong rotation cannot also decide which returns survive.

        A rotation preserves length, so the two orders agree today - and they would stop
        agreeing the moment anything translated as well, at which point the gate would be
        keeping a different set of points than the one it was measured on.
        """
        message = ros_schema.lidar_points_message(
            self._cloud([[self._bearing(self.HEADING, 150.0)]], max_range_m=100.0)
        )
        import numpy

        assert numpy.isnan(self._read(message)[0, 0]).all()

    def test_the_header_describes_the_bytes_that_are_actually_there(self):
        """Three float32 at 0, 4, 8, `point_step` 12, `row_step` a whole row, and that many bytes.

        A reader trusts these five numbers over the payload. Disagree by one and it reads the
        next point's x as this point's z, all the way down, with nothing raising.
        """
        near = self._bearing(self.HEADING, 10.0)
        message = ros_schema.lidar_points_message(self._cloud([[near, near], [near, near]]))
        assert [(f["name"], f["offset"], f["datatype"], f["count"]) for f in message["fields"]] == [
            ("x", 0, ros_schema.POINTFIELD_FLOAT32, 1),
            ("y", 4, ros_schema.POINTFIELD_FLOAT32, 1),
            ("z", 8, ros_schema.POINTFIELD_FLOAT32, 1),
        ]
        assert message["point_step"] == ros_schema.POINT_STEP == 12
        assert message["row_step"] == 12 * message["width"]
        assert len(bytes(message["data"])) == message["height"] * message["row_step"]

    def test_the_payload_is_little_endian_by_dtype_rather_than_by_luck(self):
        """`is_bigendian: false` is a claim about the bytes in the bag, not about this machine.

        Every machine this runs on is little-endian, so a native-order array would be right by
        accident here and wrong on the first big-endian host that ever writes one.
        """
        message = ros_schema.lidar_points_message(
            self._cloud([[self._bearing(self.HEADING, 10.0)]])
        )
        import struct

        assert message["is_bigendian"] is False
        assert bytes(message["data"])[:4] == struct.pack("<f", 10.0)

    def test_the_cloud_carries_the_frames_own_stamp(self):
        """One instant, one stamp - a sweep stamped anything else is a sweep of another moment."""
        message = ros_schema.lidar_points_message(
            self._cloud([[self._bearing(self.HEADING, 10.0)]])
        )
        assert message["header"]["stamp"] == stamp(0.4)


class TestTheLidarMountAndItsCeiling:
    """Where the sensor is bolted on, and how many buffers may stand beside it."""

    def test_the_lidar_goes_through_the_same_frame_swap_the_cameras_do(self):
        """0.8 m forward and 1.5 m up in MetaDrive's frame is x=+0.8, y=0, z=+1.5 in ROS.

        Through `_ros_mount`, the single place that swaps the two frames. Two copies of that
        conversion is two chances to negate the wrong one, and a `/tf_static` where the cameras
        are right and the lidar is mirrored is not something a reader would ever look for.
        """
        import ros_frame

        mount = ros_frame.lidar_mount()
        assert set(mount) == {ros_schema.LIDAR_FRAME}
        x, y, z, yaw = mount[ros_schema.LIDAR_FRAME]
        right, forward, up = ros_frame.LIDAR_MOUNT
        assert (x, y, z) == (forward, -right, up) == (0.8, 0.0, 1.5)
        assert yaw == 0.0

    def test_the_cloud_ceiling_is_below_the_all_rgb_one(self):
        """Two different faults at two different sizes, and the lower one is silent.

        Past `MAX_IMAGE_BUFFERS` the run crashes, which is loud. Past
        `MAX_BUFFERS_WITH_POINT_CLOUD` the run succeeds and the cloud comes back empty, so the
        smaller number is the one a drive has to be refused against.
        """
        import camera_rig

        assert camera_rig.MAX_BUFFERS_WITH_POINT_CLOUD < camera_rig.MAX_IMAGE_BUFFERS

    def test_a_lidar_size_is_rays_by_beams_and_anything_else_is_named(self):
        import drive

        assert drive._lidar_size("200x64") == (200, 64)
        for bad in ("200", "200x", "axb", "0x64", "200x-1", ""):
            with pytest.raises(ValueError, match="--ros-lidar"):
                drive._lidar_size(bad)


class TestTheSbgDefinitionsInAWrittenBag:
    """What actually reaches the file, which is not quite what `tools/sbg_msgs/` holds.

    A bag is self-describing: rosbag2 writes each type's `.msg` text into it so a reader decodes
    it without the package that wrote it. That is the whole protection against the version
    mismatch `tools/sbg_msgs/README.md` measures - a subscriber built against `sbg_driver` 3.1.0
    reading a 3.4.0 `SbgEkfStatus` does not fail, it reads `dvl_bt_used` as `gps1_course_used`
    and carries on, because CDR carries no field names.

    **`rosbags` regenerates that text from its parsed typestore rather than copying ours**, so
    the comments do not survive and the field list is what a consumer gets. This pins the half
    that matters: every field, in order, in the seven published types and the five nested ones.
    """

    @staticmethod
    def _fields(text):
        stripped = (line.split("#")[0].strip() for line in text.splitlines())
        return [line.replace("sbg_driver/", "") for line in stripped if line]

    @pytest.fixture(scope="class")
    def written(self, tmp_path_factory):
        import ros_bag

        path = tmp_path_factory.mktemp("sbg") / "bag"
        frame = Frame(
            index=0, sim_time_s=0.0, ego=_ego(),
            projection=Projection(3.15, 101.6, 0.0, 0.0),
        )
        with ros_bag.BagWriter(path, topics=None, notes={}) as bag:
            bag.write(frame)
        from rosbags.rosbag2 import Reader

        with Reader(path) as reader:
            return {c.msgtype: c.msgdef.data for c in reader.connections}

    def test_every_sbg_type_reached_the_bag_as_a_connection_of_its_own(self, written):
        published = {
            TOPICS[topic][0]
            for topic in TOPICS
            if TOPICS[topic][0].startswith("sbg_driver/")
        }
        assert len(published) == 7
        assert published <= set(written)

    def test_the_field_list_in_the_bag_is_the_field_list_upstream_wrote(self, written):
        """A field out of order serialises silently and deserialises into nonsense. This is the
        only place that is checked against the upstream text rather than against our own code."""
        import re

        checked = set()
        for msgtype, blob in written.items():
            if not msgtype.startswith("sbg_driver/"):
                continue
            blocks = re.split(r"^=+$", blob, flags=re.M)
            stem = msgtype.rsplit("/", 1)[-1]
            source = ros_schema.SBG_MSG_DIR / f"{stem}.msg"
            assert self._fields(blocks[0]) == self._fields(source.read_text()), msgtype
            checked.add(stem)
            # The nested status submessages ride along in the same blob, and are as easy to get
            # wrong - `SbgGpsPosStatus` went from 7 fields to 22 between two releases. Counted
            # by name rather than by visit: `SbgEkfStatus` is nested in three of the seven.
            for block in blocks[1:]:
                head, _, body = block.strip().partition("\n")
                if not head.startswith("MSG: sbg_driver/"):
                    continue
                stem = head.rsplit("/", 1)[-1]
                nested = ros_schema.SBG_MSG_DIR / f"{stem}.msg"
                assert self._fields(body) == self._fields(nested.read_text()), head
                checked.add(stem)
        assert len(checked) == 12, f"expected all twelve types, saw {sorted(checked)}"

    def test_the_comments_do_not_travel_and_the_readme_says_where_they_went(self, written):
        """Measured, not assumed - and the reason `tools/sbg_msgs/README.md` had to be corrected.
        If a later `rosbags` starts carrying them this test is the notification."""
        assert "#" not in written["sbg_driver/msg/SbgEkfNav"]
        assert "Kalman" not in written["sbg_driver/msg/SbgEkfNav"]
        assert "0xFF if N/A" in (ros_schema.SBG_MSG_DIR / "SbgGpsPos.msg").read_text()


class TestTheCameraPacketDefinition:
    """Phase 4's first finding, and it was sitting in the file from stage 10.

    The `FFMPEGPacket` definition vendored here was **wrong in four ways**, and could not have
    raised while no camera topic was written: the fields were in a different order, two were the
    wrong size, one that upstream does not have was invented, and one upstream does have was
    missing. A bag written against it would open perfectly - rosbag2 stores the definition beside
    the data, so our own reader would agree with itself - and any consumer with the real package
    installed would read the encoding string out of the width field.
    """

    def test_the_definition_is_the_upstream_one_character_for_character(self):
        """Pinned rather than described. `get_types_from_msg` ignores comments, alignment and
        the tab, so nothing in the parse would notice this drifting; the point of pinning the
        text is that "verbatim from 1.1.2" stays a claim a reader can check against the commit."""
        assert ros_schema.FFMPEG_PACKET_MSG == (
            "std_msgs/Header header\n"
            "int32 width       # original image width\n"
            "int32 height      # original image height\n"
            "string encoding\t  # encoding used\n"
            "uint64 pts        # packet pts\n"
            "uint8  flags      # packet flags\n"
            "bool is_bigendian # true if machine stores in big endian format\n"
            "uint8[] data      # ffmpeg compressed payload\n"
        )
        assert (
            EXTRA_DEFINITIONS["ffmpeg_image_transport_msgs/msg/FFMPEGPacket"]
            == ros_schema.FFMPEG_PACKET_MSG
        )
        assert ros_schema.FFMPEG_MSGS_VERSION == "1.1.2"
        assert len(ros_schema.FFMPEG_MSGS_COMMIT) == 40

    def test_the_fields_parse_in_the_order_and_the_widths_upstream_declares(self):
        """CDR carries no field names, so **order is the wire format**. A consumer using its own
        installed `ffmpeg_image_transport_msgs` decodes by position and nothing else."""
        from rosbags.typesys import get_types_from_msg

        parsed = get_types_from_msg(
            ros_schema.FFMPEG_PACKET_MSG, "ffmpeg_image_transport_msgs/msg/FFMPEGPacket"
        )
        _constants, fields = parsed["ffmpeg_image_transport_msgs/msg/FFMPEGPacket"]
        assert [name for name, _ in fields] == [
            "header",
            "width",
            "height",
            "encoding",
            "pts",
            "flags",
            "is_bigendian",
            "data",
        ]
        widths = {name: spec[1][0] for name, spec in fields if spec[0].name == "BASE"}
        assert widths == {
            "width": "int32",
            "height": "int32",
            "encoding": "string",
            "pts": "uint64",
            "flags": "uint8",
            "is_bigendian": "bool",
        }

    def test_the_definition_this_replaced_is_not_still_reachable(self):
        """`frame_id` was the invented field, and it is the one a reader would look for."""
        text = ros_schema.FFMPEG_PACKET_MSG
        assert "frame_id" not in text
        assert "uint32 pts" not in text


class TestTheCameraPackets:
    """The six `image_raw/ffmpeg` topics.

    `ros_encode.py` makes the bytes; this makes the message around them.
    """

    def packet(self, name="front_left", frame_id="cam_left", keyframe=True):
        return ros_schema.CameraPacket(
            name=name,
            frame_id=frame_id,
            width=512,
            height=288,
            encoding="libx264",
            pts=7,
            keyframe=keyframe,
            data=b"\x00\x00\x00\x01\x67payload",
        )

    def frame(self, packets):
        return Frame(
            index=3,
            sim_time_s=0.3,
            ego=Ego(x=0.0, y=0.0, z=0.0, heading=0.0, velocity_east=0.0,
                    velocity_north=0.0, speed=0.0),
            extra={"camera_packets": tuple(packets)},
        )

    def test_six_topics_under_the_rigs_own_names(self):
        assert len(ros_schema.CAMERA_PACKET_TOPICS) == 6
        assert ros_schema.CAMERA_PACKET_TOPICS[0] == (
            "/sensing/camera/cam_sync_rig/front_left/image_raw/ffmpeg"
        )
        for topic in ros_schema.CAMERA_PACKET_TOPICS:
            assert TOPICS[topic] == ("ffmpeg_image_transport_msgs/msg/FFMPEGPacket", "sensor")
            assert topic in BUILDERS

    def test_the_rate_family_is_sensor_and_that_is_what_says_it_is_not_every_step(self):
        """`frame_gate` re-uses the last drawn picture on a held step. A `state` family here
        would put `stride` re-encodes of that held buffer in the bag for every real frame - each
        one tiny, valid, and a statement that the world stood still."""
        families = {TOPICS[t][1] for t in ros_schema.CAMERA_PACKET_TOPICS}
        assert families == {"sensor"}
        assert TOPICS[ros_schema.LIDAR_POINTS][1] == "sensor"

    def test_the_message_carries_the_frame_id_rather_than_the_topics_own_name(self):
        """The two names a camera has, and the join between the picture and the mount.

        The topic is the rig's (`front_left`) and the `frame_id` is the spec's (`cam_left`),
        which is what `/tf_static` calls it. On `rigs/cams.txt` the labels and the geometry
        already disagree, so a consumer that wants to know where a picture was taken from has to
        follow `frame_id` into the transform rather than read the topic name.
        """
        built = ros_schema.camera_packet_message(self.frame([self.packet()]), "front_left")
        assert built["header"]["frame_id"] == "cam_left"
        assert built["width"] == 512 and built["height"] == 288
        assert built["encoding"] == "libx264"
        assert built["pts"] == 7
        assert built["is_bigendian"] is False
        assert bytes(built["data"]) == b"\x00\x00\x00\x01\x67payload"

    def test_a_keyframe_is_flagged_with_libavs_own_value(self):
        """`AV_PKT_FLAG_KEY` is 1, and a decoder joining mid-bag looks for it and nothing else."""
        assert ros_schema.PACKET_FLAG_KEY == 1
        key = ros_schema.camera_packet_message(self.frame([self.packet()]), "front_left")
        inter = ros_schema.camera_packet_message(
            self.frame([self.packet(keyframe=False)]), "front_left"
        )
        assert key["flags"] == 1
        assert inter["flags"] == 0

    def test_a_camera_with_no_packet_this_frame_drops_its_topic_rather_than_repeating(self):
        """None is the ordinary case, not an error: most frames of a strided drive are not
        decision frames, and a drive with no `--ros-camera` never has one."""
        frame = self.frame([self.packet()])
        assert ros_schema.camera_packet_message(frame, "rear_middle") is None
        empty = Frame(index=0, sim_time_s=0.0, ego=frame.ego)
        assert ros_schema.camera_packet_message(empty, "front_left") is None

    def test_the_six_topics_are_absent_from_a_frame_that_carries_no_pictures(self):
        """`messages` drops a topic whose builder returns None, so the camera channels are
        simply not in a bag rather than being in it with holes."""
        ego = Ego(x=0.0, y=0.0, z=0.0, heading=0.0, velocity_east=0.0,
                  velocity_north=0.0, speed=0.0)
        written = {topic for topic, _, _ in messages(Frame(index=0, sim_time_s=0.0, ego=ego))}
        assert not (written & set(ros_schema.CAMERA_PACKET_TOPICS))
        with_pictures = {
            topic for topic, _, _ in messages(self.frame([self.packet(), self.packet(
                "rear_middle", "cam_back")]))
        }
        assert with_pictures & set(ros_schema.CAMERA_PACKET_TOPICS) == {
            "/sensing/camera/cam_sync_rig/front_left/image_raw/ffmpeg",
            "/sensing/camera/cam_sync_rig/rear_middle/image_raw/ffmpeg",
        }

    def test_each_topic_gets_its_own_builder_rather_than_one_shared_closure(self):
        """Six topics reading one loop variable is the classic late-binding bug, and it would
        put `rear_right`'s picture on all six channels with nothing raising."""
        builders = [BUILDERS[t] for t in ros_schema.CAMERA_PACKET_TOPICS]
        assert len(set(map(id, builders))) == 6
        frame = self.frame([self.packet("rear_right", "cam_back_right")])
        built = [builder(frame) for builder in builders]
        assert [b is not None for b in built] == [False, False, False, False, False, True]


class TestVendoringADefinitionToDisk:
    """`ros_defs.vendor` and `--write` - what turns phase 5 from a paste into a command.

    Recovering a definition and *keeping* it are two different problems. `render` solves the
    first and produces a Python string literal, which is where `tools/sbg_msgs/README.md`'s
    argument bites: **verbatim has to be checkable**, and a `.msg` rewrapped to fit a source line
    cannot be diffed against the bag it came out of. So the fifteen land as files.

    The whole path is exercised here against types we already own, which is deliberate. The one
    file phase 5 waits on is not in the repo and cannot be, so the alternative would be a loader
    whose first real use is the day it matters.
    """

    @staticmethod
    def _found(tmp_path):
        import ros_defs

        found, _ = ros_defs.read(TestReadingDefinitionsBackOutOfABag._bag(tmp_path))
        return found

    def test_a_vendored_file_holds_exactly_what_the_bag_carried(self, tmp_path):
        import ros_defs

        found = self._found(tmp_path)
        out = tmp_path / "msgs"
        verdict, _ = ros_defs.vendor(found["wingfin_msgs/msg/TrafficLight"], out)
        assert verdict == "written"
        text = (out / "TrafficLight.msg").read_text(encoding="utf-8")
        assert text == found["wingfin_msgs/msg/TrafficLight"].text
        assert text == EXTRA_DEFINITIONS["wingfin_msgs/msg/TrafficLight"]

    def test_writing_the_same_definition_twice_is_not_a_change(self, tmp_path):
        import ros_defs

        found = self._found(tmp_path)
        out = tmp_path / "msgs"
        ros_defs.vendor(found["wingfin_msgs/msg/TrafficLight"], out)
        verdict, _ = ros_defs.vendor(found["wingfin_msgs/msg/TrafficLight"], out)
        assert verdict == "unchanged", "a re-run must be idempotent, not a rewrite"

    def test_a_definition_that_disagrees_with_the_file_is_refused(self, tmp_path):
        """The collision that matters, and the reason `vendor` is not a `write_text` call.

        `wingfin_msgs` is the *vehicle's* package and two of its types are ours, invented for a
        topic the rig does not have. A rig bag carrying its own `TrafficLightArray` would land on
        top of ours, and CDR carries no field names - so every bag written afterwards would
        serialise our traffic lights against a field list nothing here agreed to, with nothing
        downstream raising. A collision is a question for a person.
        """
        import ros_defs

        found = self._found(tmp_path)
        out = tmp_path / "msgs"
        target = out / "TrafficLight.msg"
        out.mkdir()
        target.write_text("uint8 something_else\n", encoding="utf-8")

        verdict, detail = ros_defs.vendor(found["wingfin_msgs/msg/TrafficLight"], out)
        assert verdict == "CONFLICT"
        assert "--force" in detail
        assert target.read_text(encoding="utf-8") == "uint8 something_else\n", "it overwrote"

    def test_force_is_what_overwrites_and_nothing_else_is(self, tmp_path):
        import ros_defs

        found = self._found(tmp_path)
        out = tmp_path / "msgs"
        out.mkdir()
        (out / "TrafficLight.msg").write_text("uint8 something_else\n", encoding="utf-8")

        verdict, _ = ros_defs.vendor(found["wingfin_msgs/msg/TrafficLight"], out, force=True)
        assert verdict == "written"
        assert (out / "TrafficLight.msg").read_text(encoding="utf-8") == (
            EXTRA_DEFINITIONS["wingfin_msgs/msg/TrafficLight"]
        )

    def test_a_conflict_fails_the_run_rather_than_printing_and_returning_zero(self, tmp_path):
        import ros_defs

        out = tmp_path / "msgs"
        out.mkdir()
        (out / "TrafficLight.msg").write_text("uint8 something_else\n", encoding="utf-8")
        bag = TestReadingDefinitionsBackOutOfABag._bag(tmp_path)
        assert ros_defs.main([str(bag), "--write", str(out), "--package", "wingfin_msgs"]) == 1

    def test_the_package_filter_keeps_one_directory_to_one_package(self, tmp_path, capsys):
        """`tools/sbg_msgs/` holds `sbg_driver` and nothing else; the same rule applies here.

        Without the filter a bag of ours vendors `vision_msgs` alongside `wingfin_msgs`, and a
        directory named after one package holding another's types is how a loader keyed on the
        directory name starts registering a type under the wrong package.
        """
        import ros_defs

        bag = TestReadingDefinitionsBackOutOfABag._bag(tmp_path)
        out = tmp_path / "msgs"
        assert ros_defs.main([str(bag), "--write", str(out), "--package", "wingfin_msgs"]) == 0
        assert sorted(path.name for path in out.glob("*.msg")) == [
            "TrafficLight.msg",
            "TrafficLightArray.msg",
        ]

    def test_the_files_on_disk_are_what_the_module_loads(self):
        """The loader, not a copy of it: what phase 5 relies on with no edit to `ros_schema`."""
        import ros_schema

        on_disk = {
            f"wingfin_msgs/msg/{path.stem}": path.read_text(encoding="utf-8")
            for path in sorted(ros_schema.WINGFIN_MSG_DIR.glob("*.msg"))
        }
        assert on_disk, "tools/wingfin_msgs/ has no .msg files"
        for name, text in on_disk.items():
            assert EXTRA_DEFINITIONS[name] == text, f"{name} on disk is not what is registered"

    def test_a_new_msg_dropped_in_registers_with_no_source_edit(self, tmp_path, monkeypatch):
        """The claim phase 5 turns on, tested rather than asserted in a comment."""
        import ros_schema

        monkeypatch.setattr(ros_schema, "MSG_ROOT", tmp_path)
        monkeypatch.setattr(ros_schema, "VENDORED_PACKAGES", {"made_up_msgs": "made_up_msgs"})
        (tmp_path / "made_up_msgs").mkdir()
        (tmp_path / "made_up_msgs" / "Widget.msg").write_text(
            "std_msgs/Header header\nfloat64 speed\n", encoding="utf-8"
        )
        loaded = ros_schema._vendored_definitions()
        assert loaded == {
            "made_up_msgs/msg/Widget": "std_msgs/Header header\nfloat64 speed\n"
        }

    def test_it_says_which_of_the_fifteen_a_bag_carries(self, tmp_path, capsys):
        """"Is this file enough?" is the question, and a definition count does not answer it.

        The lookup runs topic-first because **the type names are themselves unknown** - that is
        the blockage, `bag_audit.html` recording rates and not types - so the only way to ask is
        "what did the recorder file this topic under".
        """
        import ros_defs
        import ros_schema

        ros_defs.report(TestReadingDefinitionsBackOutOfABag._bag(tmp_path))
        printed = capsys.readouterr().out
        assert ros_schema.AWAITING_BUILDER == {}
        assert "every topic the vehicle publishes and a simulator can honestly produce" in printed
        assert "left out on purpose" in printed

    def test_the_fifteen_are_still_fifteen_and_still_absent(self):
        """Nothing above declares a topic. Vendoring is the input to phase 5, not phase 5.

        And `MISSING_DEFINITIONS` is now **empty**, which is the result rather than an
        oversight: every type the vehicle publishes is vendored, recovered from its own
        recording. What is left is seven builders, which is a different problem and has its own
        name.
        """
        import ros_schema

        assert ros_schema.MISSING_DEFINITIONS == {}
        assert ros_schema.AWAITING_BUILDER == {}
        ledger = ros_schema.rig_coverage()
        assert ledger["absent"] == {}
        assert len(ledger["produced"]) == ledger["producible"] == 36


class TestARigBagArriving:
    """The branch that runs on the one file phase 5 waits on, exercised without it.

    Everything else about the ingest is tested against bags of ours, and bags of ours carry none
    of the fifteen - so the interesting half of `blocked_report`, and the whole of the "a type we
    have never seen lands and registers" path, would otherwise sit untested until the day it
    mattered. This writes a stand-in: a bag holding a `/vehicle/state` connection under a type
    nothing in this repo defines, which is exactly the shape the rig's bag has.

    **The stand-in's field list is not a guess at the rig's** and nothing here treats it as one.
    It exists to be recovered, not to be believed; the point being proved is that whatever text a
    bag carries comes back out of it and registers.
    """

    TOPIC = "/perception/model_info"
    TYPE = "made_up_msgs/msg/ModelInfo"
    TEXT = "std_msgs/Header header\nfloat64 speed\nfloat64 steering_angle\n"

    def _bag(self, tmp_path):
        from rosbags.rosbag2 import Writer
        from rosbags.typesys import Stores, get_types_from_msg, get_typestore

        store = get_typestore(Stores.ROS2_HUMBLE)
        store.register(get_types_from_msg(self.TEXT, self.TYPE))
        path = tmp_path / "rig-stand-in"
        with Writer(path, version=9) as writer:
            connection = writer.add_connection(
                self.TOPIC, self.TYPE, typestore=store, serialization_format="cdr"
            )
            message = store.types[self.TYPE]
            header = store.types["std_msgs/msg/Header"]
            stamp = store.types["builtin_interfaces/msg/Time"]
            writer.write(connection, 0, store.serialize_cdr(
                message(
                    header=header(stamp=stamp(sec=0, nanosec=0), frame_id="base_link"),
                    speed=1.0,
                    steering_angle=0.0,
                ),
                self.TYPE,
            ))
        return path

    def test_a_type_this_repo_has_never_seen_comes_back_out(self, tmp_path):
        import ros_defs

        found, undefined = ros_defs.read(self._bag(tmp_path))
        assert undefined == [], "the writer recorded no definition"
        assert self.TYPE in found
        assert found[self.TYPE].text == self.TEXT
        assert ros_defs.parses(self.TYPE, found[self.TYPE].text) is None

    def test_the_per_topic_list_counts_it(self, tmp_path, capsys, monkeypatch):
        """`0 carried` becomes `1 carried`, and the row for that topic names the type.

        Nothing is waiting any more - the ledger is 36 of 36 - so the branch is exercised
        against a stand-in list rather than deleted. It is the branch that answers *"is this
        file enough?"*, and the next type the vehicle adds puts it straight back into use.
        """
        import ros_defs
        import ros_schema

        monkeypatch.setattr(
            ros_schema, "AWAITING_BUILDER", {self.TOPIC: "a builder", "/vehicle/engagement": "x"}
        )
        ros_defs.report(self._bag(tmp_path))
        printed = capsys.readouterr().out
        assert "the 2 topics phase 5 still has to build: 1 carried by this bag" in printed
        assert f"+ {self.TOPIC}  {self.TYPE}" in printed
        assert "- /vehicle/engagement  not in this bag" in printed

    def test_vendoring_it_makes_ros_schema_carry_it(self, tmp_path, monkeypatch):
        """The claim phase 5 rests on, run end to end: bag -> file -> registered definition."""
        import ros_defs
        import ros_schema

        root = tmp_path / "vendored"
        out = root / "made_up_msgs"
        assert ros_defs.main(
            [str(self._bag(tmp_path)), "--write", str(out), "--package", "made_up_msgs"]
        ) == 0
        assert (out / "ModelInfo.msg").read_text(encoding="utf-8") == self.TEXT

        monkeypatch.setattr(ros_schema, "MSG_ROOT", root)
        monkeypatch.setattr(ros_schema, "VENDORED_PACKAGES", {"made_up_msgs": "made_up_msgs"})
        assert ros_schema._vendored_definitions()[self.TYPE] == self.TEXT

    def test_it_still_does_not_write_the_topic(self, tmp_path):
        """A definition is necessary and not sufficient - the builder is the missing half."""
        import ros_schema

        assert ros_schema.AWAITING_BUILDER == {}
        assert "/perception/model_info" in ros_schema.TOPICS


#: The vehicle's own recording. 14.7 GB, not tracked, and the source of `reference_bag.json`.
PRODUCTION_BAG = Path(__file__).resolve().parents[2] / "bags" / "074143"


class TestTheProductionBag:
    """Read against the vehicle's own recording, when it is on this machine.

    `tools/reference_bag.json` is vendored precisely because the bag is 14.7 GB and `bags/` is
    not tracked, so the ledger has to work without it. These tests are the other half of that
    bargain: when the bag *is* present they re-derive the table from it, so a vendored file that
    has drifted from the recording it claims to describe cannot go unnoticed.

    Skipped rather than failed when the bag is absent - the same arrangement
    `test_conversion.py` has with the MetaDrive checkout, and with the same caveat, that a
    skipped gate is a gate that is not running.
    """

    @pytest.mark.skipif(not PRODUCTION_BAG.exists(), reason=f"no bag at {PRODUCTION_BAG}")
    def test_the_vendored_table_is_what_the_recording_says(self):
        import ros_defs

        assert ros_defs.reference_table(PRODUCTION_BAG) == ros_schema._reference()

    @pytest.mark.skipif(not PRODUCTION_BAG.exists(), reason=f"no bag at {PRODUCTION_BAG}")
    def test_a_truncated_recording_still_gives_up_every_definition(self):
        """The enabling change, against the file that motivated it.

        `rosbags`' `Reader` raises `File end magic is invalid` on this bag and returns nothing:
        rosbag2 writes its summary at the *end*, and a recording pulled off a vehicle is
        routinely cut short. Reading forwards from the header gets all thirty schemas out of the
        first few chunks of a 14.7 GB file.
        """
        import ros_defs

        found, undefined = ros_defs.read(PRODUCTION_BAG)
        assert undefined == []
        for name in (
            "wing_msgs/msg/VehicleState",
            "wing_msgs/msg/ActuatorsOutput",
            "flir_camera_msgs/msg/ImageMetaData",
            "point_cloud_interfaces/msg/CompressedPointCloud2",
        ):
            assert name in found, name
            assert ros_defs.parses(name, found[name].text) is None, name

    @pytest.mark.skipif(not PRODUCTION_BAG.exists(), reason=f"no bag at {PRODUCTION_BAG}")
    def test_what_we_vendored_is_byte_for_byte_what_the_bag_carried(self):
        """The claim the whole approach rests on, checked rather than asserted in a README."""
        import ros_defs

        found, _ = ros_defs.read(PRODUCTION_BAG)
        vendored = {
            name: text
            for name, text in EXTRA_DEFINITIONS.items()
            if name.split("/")[0] in ("wing_msgs", "flir_camera_msgs", "point_cloud_interfaces")
        }
        assert vendored, "nothing was vendored from the production bag"
        for name, text in vendored.items():
            assert found[name].text == text, f"{name} on disk is not what the bag carried"

    @pytest.mark.skipif(not PRODUCTION_BAG.exists(), reason=f"no bag at {PRODUCTION_BAG}")
    def test_the_ffmpeg_packet_we_ship_is_the_one_the_vehicle_ships(self):
        """Phase 4's wire format, against the vehicle rather than against a git tag.

        It was vendored from `ffmpeg_image_transport_msgs` 1.1.2 on the strength of a commit
        hash. This is the check that the vehicle is on the same one.
        """
        import ros_defs

        found, _ = ros_defs.read(PRODUCTION_BAG)
        theirs = found["ffmpeg_image_transport_msgs/msg/FFMPEGPacket"].text
        assert _fields(theirs) == _fields(ros_schema.FFMPEG_PACKET_MSG)

    @pytest.mark.skipif(not PRODUCTION_BAG.exists(), reason=f"no bag at {PRODUCTION_BAG}")
    def test_the_sbg_release_we_pinned_is_the_one_the_vehicle_runs(self):
        """Phase 2's `SBG_DRIVER_VERSION`, likewise. The field lists change between releases and
        a mismatch is silent - `tools/sbg_msgs/README.md` measures 3.1.0 against 3.4.0."""
        import ros_defs

        found, _ = ros_defs.read(PRODUCTION_BAG)
        compared = 0
        for name, text in EXTRA_DEFINITIONS.items():
            if name.startswith("sbg_driver/") and name in found:
                assert _fields(found[name].text) == _fields(text), name
                compared += 1
        assert compared >= 7, f"only {compared} SBG types were in the bag to compare"


def _fields(text: str) -> list[tuple[str, str]]:
    """The top block's `(type, name)` pairs - no comments, no constants, no dependencies."""
    out = []
    for line in text.splitlines():
        if line.startswith(("====", "MSG:")):
            break
        stripped = line.split("#")[0].strip()
        if stripped and "=" not in stripped.split()[-1]:
            out.append(tuple(stripped.split()[:2]))
    return out


class TestAStaleBag:
    """`ros_probe.stale_types` - a bag written before a type changed.

    A bag outlives the code that wrote it, and this repo has now changed a type once. Every
    check downstream reads fields by name, so the seven bags recorded before 2026-09-03 met the
    new `/sensing/gnss/pose` checks with an `AttributeError` and a traceback - which says nothing
    about the bag and reads as a broken tool. Reporting it is both the kinder behaviour and a
    real finding.
    """

    def test_a_topic_carrying_its_old_type_is_named_rather_than_crashed_on(self):
        import ros_probe

        by_topic = type("_T", (dict,), {})()
        by_topic.types = {ros_schema.GNSS_POSE: "geometry_msgs/msg/PoseStamped"}
        stale = ros_probe.stale_types(by_topic)
        assert stale == {
            ros_schema.GNSS_POSE: ("geometry_msgs/msg/PoseStamped", "sensor_msgs/msg/NavSatFix")
        }

    def test_a_bag_whose_types_all_match_reports_nothing(self):
        import ros_probe

        by_topic = type("_T", (dict,), {})()
        by_topic.types = {topic: declared for topic, (declared, _) in TOPICS.items()}
        assert ros_probe.stale_types(by_topic) == {}

    def test_a_topic_the_module_does_not_declare_is_not_stale(self):
        """A bag may carry topics we never wrote. Those are not our business to police."""
        import ros_probe

        by_topic = type("_T", (dict,), {})()
        by_topic.types = {"/some/other/thing": "std_msgs/msg/String"}
        assert ros_probe.stale_types(by_topic) == {}


class TestWhatTheCarWasTold:
    """`vehicle_state_message`, `engagement_message`, `actuators_output_message` - phase 5.

    The only three topics in this bag built from what the drive **commanded** rather than from
    what it observed, which is what makes them checkable at all: a command and its consequence
    are two independently produced quantities.

    `wing_msgs/VehicleState`'s own comments are the specification, and the three tests that
    matter here are the three traps it states outright - degrees not radians, m/s not km/h, and
    a `cruise_standstill` that must be produced rather than copied.
    """

    @staticmethod
    def _frame(steering=0.5, throttle_brake=0.3, speed=10.0, engaged=True, **kwargs):
        controls = ros_schema.Controls(
            steering=steering,
            throttle_brake=throttle_brake,
            max_steering_deg=40.0,
            policy="idm",
            engaged=engaged,
            **kwargs,
        )
        return Frame(
            index=7,
            sim_time_s=1.5,
            ego=Ego(x=1.0, y=2.0, z=0.0, heading=0.0, velocity_east=speed, velocity_north=0.0,
                    speed=speed, yaw_rate=0.2),
            controls=controls,
        )

    def test_the_wheel_angle_is_in_degrees_and_scaled_by_the_cars_own_maximum(self):
        """The first trap. Every other angle in `wing_msgs` is SI and this one is not.

        Converting one link of the DBC -> ControlCommand -> ActuatorsOutput -> openpilot chain
        in isolation puts a factor of 57.3 somewhere no reader would look, so the conversion
        happens once, in `Controls`, and `max_steering` comes off the car rather than a constant.
        """
        message = ros_schema.vehicle_state_message(self._frame(steering=0.5))
        assert message["steering_angle_deg"]["value"] == pytest.approx(20.0)
        assert ros_schema.vehicle_state_message(self._frame(steering=-1.0))[
            "steering_angle_deg"
        ]["value"] == pytest.approx(-40.0)

    def test_left_is_positive_all_the_way_through(self):
        """MetaDrive is left-positive and so is REP-103; CARLA is not, and that difference has
        already driven a car into oncoming traffic once in this repo."""
        message = ros_schema.vehicle_state_message(self._frame(steering=0.25))
        assert message["steering_angle_deg"]["value"] > 0
        assert ros_schema.actuators_output_message(self._frame(steering=0.25))["steer"] > 0

    def test_all_four_wheels_carry_the_ego_speed_in_metres_per_second(self):
        """The second trap, and it is the definition's own instruction rather than our shortcut:
        a simulator models no per-wheel dynamics, so the spread is zero **by construction** and
        not because the car happens to be going straight."""
        message = ros_schema.vehicle_state_message(self._frame(speed=13.5))
        wheels = [message[f"wheel_speed_{corner}"]["value"] for corner in
                  ("front_left", "front_right", "rear_left", "rear_right")]
        assert wheels == [13.5] * 4
        assert message["v_ego"]["value"] == 13.5

    def test_cruise_standstill_is_produced_false_and_never_copied_from_standstill(self):
        """The third trap, and the one with a measured cost.

        Three consumers fed `LongControl` the *motion* standstill instead, which deadlocks the
        car at every stop: standstill -> stay_stopped -> kStopping -> accel < 0, while
        `starting_condition` needs `not standstill` and so is never true. 5,066 cycles of an
        engaged, unfaulted stack braking forever with a healthy plan asking to accelerate.

        We have no ACC loop to read back, so the answer is false - a fact, not a default.
        """
        stopped = ros_schema.vehicle_state_message(self._frame(speed=0.0))
        assert stopped["standstill"]["value"] is True
        assert stopped["cruise_standstill"]["value"] is False
        moving = ros_schema.vehicle_state_message(self._frame(speed=10.0))
        assert moving["standstill"]["value"] is False
        assert moving["cruise_standstill"]["value"] is False

    def test_what_the_simulator_does_not_have_carries_a_zero_stamp(self):
        """`wing_msgs`' own in-band absence, and the vehicle uses it too - measured on a real
        message from `bags/074143`, where five of these seven are unfilled on the vehicle as
        well. A plausible `false` in a blindspot field is a claim; a zero stamp is the truth."""
        message = ros_schema.vehicle_state_message(self._frame())
        for name in ("steering_torque", "steering_pressed", "door_open", "seatbelt_unlatched",
                     "blindspot_left", "blindspot_right", "cruise_speed"):
            assert message[name]["stamp"] == {"sec": 0, "nanosec": 0}, name
        for name in ("v_ego", "a_ego", "gas", "brake", "steering_angle_deg", "gear"):
            assert message[name]["stamp"] != {"sec": 0, "nanosec": 0}, name

    def test_throttle_and_brake_are_the_two_halves_of_one_signed_number(self):
        assert ros_schema.vehicle_state_message(self._frame(throttle_brake=0.4))["gas"][
            "value"] == pytest.approx(0.4)
        assert ros_schema.vehicle_state_message(self._frame(throttle_brake=0.4))["brake"][
            "value"] == 0.0
        braking = ros_schema.vehicle_state_message(self._frame(throttle_brake=-0.7))
        assert braking["gas"]["value"] == 0.0
        assert braking["brake"]["value"] == pytest.approx(0.7)

    def test_steer_output_can_is_nan_because_there_is_no_can_bus(self):
        """A zero there would say the column was commanded and did nothing."""
        assert math.isnan(ros_schema.actuators_output_message(self._frame())["steer_output_can"])

    def test_curvature_is_measured_rather_than_restated_from_the_command(self):
        """The one field in `ActuatorsOutput` that is a second opinion rather than an echo, and
        so the only one that can disagree with the steering. `yaw_rate / speed`."""
        message = ros_schema.actuators_output_message(self._frame(speed=10.0))
        assert message["curvature"] == pytest.approx(0.02)
        # Below a crawl the quotient explodes, so it is not reported rather than reported huge.
        assert ros_schema.actuators_output_message(self._frame(speed=0.0))["curvature"] == 0.0

    def test_nothing_driving_writes_none_of_the_three(self):
        """`EngagementStatus`'s own comment: *absence of this message IS a state*. A replay
        teleports the ego onto recorded positions and commands nothing, so a `VehicleState` full
        of zeros would say the car was asked for nothing and drove the route by coincidence."""
        frame = Frame(index=0, sim_time_s=0.0,
                      ego=Ego(x=0.0, y=0.0, z=0.0, heading=0.0, velocity_east=0.0,
                              velocity_north=0.0, speed=0.0))
        assert frame.controls is None
        assert ros_schema.vehicle_state_message(frame) is None
        assert ros_schema.engagement_message(frame) is None
        assert ros_schema.actuators_output_message(frame) is None

    def test_engagement_names_the_policy_because_nothing_else_does(self):
        message = ros_schema.engagement_message(self._frame(engaged=True))
        assert message["state"] == 2 and message["enabled"] is True
        assert "idm" in message["alert_text1"]
        idle = ros_schema.engagement_message(self._frame(engaged=False))
        assert idle["state"] == 0 and idle["enabled"] is False

    def test_the_differences_are_zero_on_a_frame_with_no_predecessor(self):
        """No previous frame means no difference - not an acceleration of zero measured."""
        message = ros_schema.vehicle_state_message(self._frame())
        assert message["a_ego"]["value"] == 0.0
        assert message["steering_rate_deg_s"]["value"] == 0.0
        moved = ros_schema.vehicle_state_message(
            self._frame(accel=-1.25, steering_rate_deg_s=8.0)
        )
        assert moved["a_ego"]["value"] == -1.25
        assert moved["steering_rate_deg_s"]["value"] == 8.0


class TestWhatTheModelPredicted:
    """`predicted_trajectory_message` and its two companions - phase 5, slice 4.

    **Only the mirror matters here**, and it matters more than anything else in the phase.
    `wing_msgs/PredictedTrajectory` carries REP-103 - x forward, **y LEFT**, `orientation_z`
    CCW-positive - and says so in a comment that exists because it was wrong the other way for
    months: *"⚠️ NOT openpilot's frame, which this comment claimed until 2026-08-03."*

    Our checkpoint emits the opposite handedness. Get half the flip right and the published
    trajectory is a plausible drive down a plausible road on the wrong side of it, with nothing
    raising anything - the failure `openpilot-and-the-model.md` records for the bridge.

    These run without `torch` and without a checkpoint, which is the point: **no model drive is
    possible on this machine**, so the mirror is pinned by arithmetic rather than by a drive.
    """

    @staticmethod
    def _frame(rows=None):
        import numpy

        if rows is None:
            # [x, y, yaw, yaw_rate, v_x, v_y, a_x, a_y] - one waypoint, every column distinct
            # and non-zero, so a flip that hits the wrong column cannot hide behind a match.
            rows = numpy.array([[1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0]])
        return Frame(
            index=3,
            sim_time_s=0.5,
            ego=Ego(x=0.0, y=0.0, z=0.0, heading=0.0, velocity_east=0.0, velocity_north=0.0,
                    speed=0.0),
            prediction=ros_schema.ModelPrediction(
                waypoints=rows,
                times_s=tuple(0.1 * (i + 1) for i in range(len(rows))),
                frame_counter=42,
                model_name="av3",
                weight_name="av3/checkpoint.ep",
                ego_x=10.0, ego_y=-4.0, ego_heading=0.25,
            ),
        )

    def test_exactly_the_five_lateral_columns_flip(self):
        """`y`, `yaw`, `yaw_rate`, `v_y`, `a_y`. The longitudinal four do not move."""
        message = ros_schema.predicted_trajectory_message(self._frame())
        assert message["position_x"] == [1.0]
        assert message["position_y"] == [-2.0]
        assert message["orientation_z"] == [-3.0]
        assert message["orientation_rate_z"] == [-4.0]
        assert message["velocity_x"] == [5.0]
        assert message["velocity_y"] == [-6.0]
        assert message["acceleration_x"] == [7.0]
        assert message["acceleration_y"] == [-8.0]

    def test_the_mirror_is_one_constant_applied_once(self):
        """Five sign changes spread across a builder is five places to get one fact wrong."""
        assert ros_schema.MIRRORED_COLUMNS == (1, 2, 3, 5, 7)

    def test_a_model_turning_left_publishes_a_left_turn(self):
        """The whole point, stated as a drive rather than as indices. The checkpoint's frame is
        y-RIGHT, so its *negative* y is a left turn; REP-103's left is positive."""
        import numpy

        left_in_the_models_frame = numpy.array([[10.0, -3.0, -0.2, 0.0, 8.0, 0.0, 0.0, 0.0]])
        message = ros_schema.predicted_trajectory_message(self._frame(left_in_the_models_frame))
        assert message["position_y"][0] > 0, "a left turn must publish a positive y"
        assert message["orientation_z"][0] > 0, "and a positive yaw"

    def test_the_prediction_is_not_mutated_on_its_way_out(self):
        """The array belongs to the model and is read again next decision; mirroring it in place
        would flip it once per topic and leave the second reader with the first one's answer."""
        import numpy

        rows = numpy.array([[1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0]])
        frame = self._frame(rows)
        ros_schema.predicted_trajectory_message(frame)
        ros_schema.inference_control_message(frame)
        assert rows[0][1] == 2.0, "the caller's array was mirrored in place"

    def test_both_model_topics_describe_one_trajectory_in_one_frame(self):
        """`InferenceControlMsg` carries the same rows flattened. Two topics disagreeing about
        handedness is worse than either being wrong alone."""
        frame = self._frame()
        trajectory = ros_schema.predicted_trajectory_message(frame)
        flat = ros_schema.inference_control_message(frame)["waypoints"]["data"]
        assert flat[1] == trajectory["position_y"][0]
        assert flat[2] == trajectory["orientation_z"][0]

    def test_the_multiarray_declares_its_own_shape(self):
        """A consumer reshaping by a guessed width transposes the trajectory the first time the
        waypoint count changes, and the layout field exists to stop that."""
        layout = ros_schema.inference_control_message(self._frame())["waypoints"]["layout"]
        assert [(d["label"], d["size"]) for d in layout["dim"]] == [("waypoint", 1), ("field", 8)]

    def test_the_times_and_the_waypoints_have_to_be_the_same_length(self):
        import numpy

        frame = self._frame()
        broken = replace(
            frame,
            prediction=replace(frame.prediction, times_s=(0.1, 0.2)),
        )
        with pytest.raises(ValueError, match="waypoint times"):
            ros_schema.predicted_trajectory_message(broken)
        with pytest.raises(ValueError, match=r"\(N, 8\)"):
            ros_schema.predicted_trajectory_message(
                replace(frame, prediction=replace(frame.prediction,
                                                  waypoints=numpy.zeros((2, 3)),
                                                  times_s=(0.1, 0.2)))
            )

    def test_model_info_carries_the_weights_identity(self):
        """The type exists because this was previously unrecordable - the model's identity
        travelled only as a launch parameter and a log line, so a recorded drive could not say
        which weights produced its trajectories. A drive here had the same gap."""
        message = ros_schema.model_info_message(self._frame())
        assert message["model_name"] == "av3"
        assert message["weight_name"] == "av3/checkpoint.ep"

    def test_no_model_writes_none_of_the_three(self):
        frame = Frame(index=0, sim_time_s=0.0,
                      ego=Ego(x=0.0, y=0.0, z=0.0, heading=0.0, velocity_east=0.0,
                              velocity_north=0.0, speed=0.0))
        assert ros_schema.predicted_trajectory_message(frame) is None
        assert ros_schema.inference_control_message(frame) is None
        assert ros_schema.model_info_message(frame) is None
