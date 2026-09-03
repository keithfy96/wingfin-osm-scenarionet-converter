# Everything the stack knows about the car, in one message.
#
# Our analogue of openpilot's cereal `carState` (car.capnp:32-120), and it
# exists for the same reason that struct does: vehicle state is read by five
# nodes and every one of them wants a consistent snapshot, not four topics
# that went quiet at different moments.
#
# Replaces three §1d messages, retired with it:
#
#   wing_msgs/VehicleSpeed    /localization/vehicle_speed  -> v_ego
#   wing_msgs/WheelSpeeds     /vehicle/wheel_speeds        -> wheel_speed_*
#   wing_msgs/SteeringReport  /vehicle/steering_report     -> steering_*
#
# and adds the driver's pedals, which had no typed carrier at all — they
# reached wing_ui as two raw CanSignals under /vehicle/can_messages/PEDAL and
# reached nothing else.
#
# ── One publisher, chosen by profile ────────────────────────────────────────
#
# Exactly one node publishes this on any given run, which is the arrangement
# /vehicle/wheel_speeds already had:
#
#   vehicle, replay -> openpilot_cereal_bridge, decoded from cereal `can`.
#                      openpilot owns the panda, so nothing else can.
#   sim             -> carla_bridge, straight off the CARLA ego actor.
#
# The two cannot collide: the bridge decodes CAN only when it is not itself
# synthesising it (`publish_can`), and that is the same interlock that kept
# the old wheel-speeds topic single-writer. See cereal_bridge_node.cpp.
#
# ── Units ───────────────────────────────────────────────────────────────────
#
# SI throughout, with one documented exception below. Note in particular that
# `wheel_speed_*` is m/s and NOT the DBC's km/h: WheelSpeeds carried the raw
# km/h signal and stated no unit at all, and the /3.6 lived two nodes away in
# gnss_pose_node. The conversion now happens where the DBC value is
# decoded, so every consumer of this message can read a speed without knowing
# which bus it came off.
#
# ── Per-field stamps (added 2026-08-21) ─────────────────────────────────────
#
# Every field is a {stamp, value} pair (Float64Stamped / BoolStamped /
# UInt8Stamped): the aggregate is accumulated in place from DBC messages
# arriving at four different rates, and the single header stamp can only
# describe the OLDEST fact in the message (offerStamp keeps the min for
# exactly that reason). The nested stamp is the source time of the CAN frame
# (or sim tick) that last updated THAT field, so a recorded drive can tell a
# fresh steering angle from one riding along with a newer wheel speed.
#
# ⚠️ The header keeps its meaning: stamp of the oldest fact among the groups
# that fired this publish, i.e. header.stamp <= every field's stamp that was
# updated this cycle. A field whose stamp is zero has NEVER been filled —
# which finally makes the all-defaults hazard below diagnosable on the wire.

std_msgs/Header header

# ── Ego motion ──────────────────────────────────────────────────────────────

# Fused ego speed, m/s. The mean of the four wheel speeds, plus a standstill
# epsilon, times the per-vehicle correction — the arithmetic §4 of
# docs/migration/can_type_mapping.md freezes, and it is still computed from
# the km/h signals before conversion so the frozen order of operations is
# unchanged. See fuseWheelSpeeds() in openpilot_cereal_bridge.
wing_msgs/Float64Stamped v_ego

# Longitudinal acceleration, m/s². Differenced from v_ego by the publisher,
# not a CAN signal: the BYD DBC declares no longitudinal accel and openpilot's
# own carState fills aEgo the same way on cars that lack one. Noisy at the
# wheel-speed rate, and honest about where it came from.
wing_msgs/Float64Stamped a_ego

# Below openpilot's own standstill threshold (v_ego < 0.01 m/s). A boolean
# because that is the form every consumer wants and a threshold repeated in
# five places is a threshold that drifts in four of them.
#
# ⚠️ This is MOTION standstill. It is NOT the field LongControl wants — see
# cruise_standstill directly below, and do not substitute one for the other.
wing_msgs/BoolStamped standstill

# The ACC's own "stopped and holding" state, which is what openpilot's
# LongControl reads as cruiseState.standstill (longcontrol.py:98). On BYD it
# is ACC_CMD.STANDSTILL_STATE, a signal WE transmit and read back:
# carcontroller.py:67 sets it to `standstill and actuators.accel < 0`, so it
# means "stopped AND braking", not merely "stopped".
#
# ⚠️ Added 2026-08-12 because three consumers were feeding LongControl the
# MOTION standstill above instead, which deadlocks the car at every stop:
# standstill -> stay_stopped -> kStopping -> accel < 0, while
# starting_condition requires !standstill and so is never true. Symptom is an
# engaged, unfaulted stack braking forever with a healthy plan asking to
# accelerate (5066 cycles of it, measured 2026-08-11).
#
# ⚠️ Producers that have no ACC must publish FALSE, not copy `standstill`.
# CARLA has no ACC_CMD loop to read back, so the simulator sets it false.
wing_msgs/BoolStamped cruise_standstill

# ── Wheels ──────────────────────────────────────────────────────────────────
#
# Per-corner speed in **m/s**. In sim all four carry the ego speed: CARLA
# models no per-wheel dynamics, so the spread is zero by construction and not
# because the car is going straight.
wing_msgs/Float64Stamped wheel_speed_front_left
wing_msgs/Float64Stamped wheel_speed_front_right
wing_msgs/Float64Stamped wheel_speed_rear_left
wing_msgs/Float64Stamped wheel_speed_rear_right

# ── Steering ────────────────────────────────────────────────────────────────
#
# ⚠️ DEGREES, not radians, and deliberately so for now. Every other angle in
# wing_msgs is SI (`EgoPose.heading_rad`, `GnssPose.yaw_rad`,
# `PredictedTrajectory.orientation_z`) and this one should be too — but the
# degree convention runs unbroken from the DBC through
# ControlCommand.steering_angle_deg, ActuatorsOutput.steering_angle_deg and
# openpilot's own `steeringAngleDeg`, and converting one link of that chain in
# isolation puts a factor of 57.3 somewhere no reader would look for one.
#
# TODO(units): migrate the whole steering chain to radians in one change —
# this field, ControlCommand, carla_bridge's negation-and-scale, and the
# vehicle layer's steer_ratio consumers. Tracked with the SI unification that
# moved wheel speeds to m/s.
#
# Positive = LEFT, the DBC convention.
wing_msgs/Float64Stamped steering_angle_deg

# ⚠️ NaN on this vehicle, and that is the honest value rather than a gap.
#
# ROS 1 published a /steer_rate topic, but `byd_can_strings.py` filled it from
# DRIVER_EPS_TORQUE — an unsigned EPS torque count, not a rate. The BYD DBC
# declares no steering-rate signal at all. 0.0 would claim the wheel is not
# turning; NaN says the vehicle does not source it. See can_type_mapping.md §3.
wing_msgs/Float64Stamped steering_rate_deg_s

# Driver torque on the wheel, in the DBC's own counts (DRIVER_EPS_TORQUE).
# Unitless and vehicle-specific, which is why it keeps the raw name — it is
# the evidence behind `steering_pressed`, kept so a consumer can apply its own
# threshold rather than trusting ours.
wing_msgs/Float64Stamped steering_torque

# |steering_torque| above the configured threshold. Named for openpilot's
# `steeringPressed` rather than the old SteeringReport.driver_override,
# because it is the same quantity computed the same way (6 counts, from
# selfdrive/steering_pressed.py get_byd_steering_pressed).
wing_msgs/BoolStamped steering_pressed

# ── Driver pedals ───────────────────────────────────────────────────────────
#
# 0.0 - 1.0, already fractions: the PEDAL message scales a 0-255 raw by 0.01,
# so a fully pressed pedal decodes to 1.0 with no further scaling here.
#
# ⚠️ These are the DRIVER's pedals, not our actuation command. What openpilot
# decided to send the car is ActuatorsOutput.gas / .brake, and the two must
# not be confused: carstate.py derives brakePressed from BRAKE_PEDAL and a
# pressed brake breaks the engage latch, so feeding back a commanded brake
# would disengage openpilot every time it slowed down.
#
# Both are 0.0 in sim — nobody is in the seat.
wing_msgs/Float64Stamped gas
wing_msgs/Float64Stamped brake

# ── Stalks ──────────────────────────────────────────────────────────────────
#
# ⚠️ driving_model_node does NOT read these yet: it still subscribes to
# /left_blinker and /right_blinker as raw CanSignals, which is what the ROS 1
# stack did and what tools/stack_replay publishes off the ROS 1 archive. They
# are carried here so a drive recording holds them in the typed message with
# everything else, and so the per-signal topics can be retired once the model
# node moves over.
#
# False in sim, and false on a ROS 2 replay: config/recording.yaml does not
# record the per-signal CAN topics, so a recorded drive says both stalks are
# off whatever the driver actually did.
wing_msgs/BoolStamped left_blinker
wing_msgs/BoolStamped right_blinker

# ── Engagement preconditions (added 2026-08-08) ─────────────────────────────
#
# What `controlsd` needs to decide whether the car may be engaged, and what
# must disengage it. These are as much "vehicle state" as a wheel speed, and
# they were absent because the only producer until now was the cereal bridge,
# which fills what our own stack reads rather than what a controller needs.
#
# ⚠️ **The bridge does not fill these.** It publishes this message from
# openpilot's decoded CAN and has no reason to — openpilot does its own
# engagement. A consumer must therefore treat all-false as "unknown", not as
# "safe": every field below is a REASON NOT TO ENGAGE when set, so a producer
# that leaves them zero reads as a healthy car. byd_vehicle_interface_node
# fills all of them.

# The cruise-engage latch. ⚠️ Not a signal the car sends — it is maintained
# from the SET and RES buttons and cleared by the brake, by cruise becoming
# unavailable, or by a door or seatbelt. Its RISING EDGE is what engages
# openpilot, and every clear is a disengagement.
wing_msgs/BoolStamped cruise_enabled

# Whether the car's own ACC is available at all. Losing this disengages.
wing_msgs/BoolStamped cruise_available

# m/s. The cruise set speed, floored at 30 km/h by the decode.
wing_msgs/Float64Stamped cruise_speed

# Gear, as the DBC's value table defines it. ⚠️ UNKNOWN is the default and is
# treated as a reason not to engage — a gear we cannot read must not read as
# Drive.
uint8 GEAR_UNKNOWN=0
uint8 GEAR_PARK=1
uint8 GEAR_DRIVE=2
uint8 GEAR_REVERSE=3
wing_msgs/UInt8Stamped gear

# Any of the four doors. Disengages.
wing_msgs/BoolStamped door_open

# ⚠️ The underlying signal is INVERTED relative to its name: SEATBELT_DRIVER
# reads 0 when unlatched. This field is the decoded sense — true means the
# belt is undone.
wing_msgs/BoolStamped seatbelt_unlatched

# |EPS torque| above 6 counts. Also published as steering_pressed above; kept
# distinct because openpilot treats a driver override on the wheel as a
# lateral override event rather than a fault.
wing_msgs/BoolStamped blindspot_left
wing_msgs/BoolStamped blindspot_right
