# The predicted trajectory, in openpilot's frame, ready for cereal modelV2.
#
# This — not ControlCommand — is what reaches openpilot. `control_node.py:326`
# returns early when running with openpilot, so the PID branch and its
# /twist_cmd never execute in that deployment: openpilot's own plannerd and
# controlsd do lateral and longitudinal control from modelV2. ControlCommand
# replaces /twist_cmd for the *non*-openpilot mode, which needs
# VehicleControlPredictor and is not ported.
#
# Field names and grouping follow openpilot_bridge/control_bridge.py:37-65
# rather than anything invented here, because plannerd reads the exact layout.
# Every array is the same length and shares `position_t` as its time base,
# which is how control_bridge.py fills all five XYZTData structs.
#
# The z components modelV2 also carries are always zero on this vehicle and
# are not sent; the bridge zero-fills them.

std_msgs/Header header

# Correlates a trajectory with the inference and the camera frame it came
# from. modelV2.frameId, and the reason frame_counter survived §1d as a real
# field instead of being smuggled through twist.linear.z.
int64 frame_counter
int64 img_tstamp

# Seconds from img_tstamp. For the four-waypoint model this is
# [0, 0.5, 1.0, 1.5, 2.0] — the origin, prepended by trajectory_node, plus the
# four predictions at 0.5 s spacing.
float64[] position_t

# Metres, REP-103 like every other frame in this workspace: x forward, y LEFT,
# z up. The model emits a different handedness and trajectory_node converts.
#
# ⚠️ NOT openpilot's frame, which this comment claimed until 2026-08-03.
# modelV2 is in openpilot's DEVICE frame — x forward, y RIGHT, z DOWN — so
# cereal_bridge_node negates the lateral and the yaw on the way in. Read a
# `position_y` here as left-positive; anything writing it into cereal owes the
# flip. See the note on kDeviceLateral in cereal_bridge_node.cpp.
float64[] position_x
float64[] position_y

float64[] velocity_x
float64[] velocity_y

float64[] acceleration_x
float64[] acceleration_y

# Heading and heading rate of the fitted trajectory, radians and radians/s,
# CCW-positive about REP-103's z-up — so positive is a LEFT turn.
float64[] orientation_z
float64[] orientation_rate_z
