# What openpilot's controlsd decided to send the car, read back into ROS.
#
# This is the return half of the openpilot boundary: PredictedTrajectory goes
# out as cereal modelV2, plannerd and controlsd act on it, and this comes back.
# Until it existed the boundary was one-way and openpilot was a spectator —
# `docs/migration/phase4_sim_adr.md`, "Control tap", scoped follow-up (1).
#
# The tap is `carControl.actuatorsOutput`, NOT `carControl.actuators`. The two
# differ by the CarController's rate limits and car-specific quirks
# (`car.capnp:327-330`): `actuators` is what the control law wanted,
# `actuatorsOutput` is what `CarController.apply()` actually encoded for CAN
# (`controlsd.py:958`). Anything comparing openpilot against another controller,
# or driving a simulator from it, wants the second — the first would credit
# openpilot with commands the car never received.
#
# Field names and units are openpilot's, deliberately unrenamed. This message
# is a faithful read-out of a cereal struct, and a divergence in names here
# would be a place for a unit error to hide.

std_msgs/Header header

# openpilot's log_mono_time for the carControl event, nanoseconds on
# CLOCK_BOOTTIME. `header.stamp` is the same instant converted to ROS time by
# the bridge, which costs one boot-offset addition per message — see the
# timestamp note in docs/migration/can_type_mapping.md §1. Keep the raw value:
# it is the only field that can be compared against an openpilot log directly.
int64 log_mono_time

# CarControl.enabled — false means every actuator field below is inert. The
# lateral and longitudinal halves engage separately.
bool enabled
bool lat_active
bool long_active

# ── CarControl.Actuators ────────────────────────────────────────────────────
# Ranges are openpilot's own, from car.capnp:338-352.

# 0.0 - 1.0.
float32 gas
float32 brake

# -1.0 - 1.0.
float32 steer

# The value actually sent over CAN, in the car's own units. Unlike `steer`
# this is not normalised and its scale is per-car.
float32 steer_output_can

float32 steering_angle_deg

# ── The DESIRED command, before the CarController ──────────────────────────
#
# Everything above is carControl.actuatorsOutput: what the CarController
# reports it put ON THE WIRE. These two are carControl.actuators: what the
# controller ASKED for, before any car-specific rate limiting.
#
# ⚠️ Off a real panda the two differ in a way that matters. BYD is an
# angle-control car and its CarController routes the angle through a
# command modificator that only advances when it actually transmits
# (byd/carcontroller.py:72). With boardd disabled — every bench, including
# sim — `steering_angle_deg` above is therefore ALWAYS 0.0 while the
# controller is commanding a real angle. Measured 2026-07-31 over 1145
# lat-active samples: desired -0.57..+1.13 deg, output 0.00..0.00.
#
# So a bench that steers from `steering_angle_deg` drives perfectly straight
# and blames its geometry. Use `steering_angle_desired_deg` there, and the
# output field on the vehicle where it is the real post-rate-limit signal.
#
# `accel` above does NOT have this problem — the longitudinal path fills it
# either way — which is exactly what made this hard to see: longitudinal
# worked, lateral silently did not.
float32 steering_angle_desired_deg
float32 steer_desired

# 1/m. The lateral MPC's solution, and the field to compare against our own
# path curvature — it is the one lateral output that is neither normalised nor
# car-specific.
float32 curvature

float32 speed
float32 accel

# LongControlState, from car.capnp:354-359. A consumer that only looks at
# `accel` cannot tell "commanding zero" from "stopped and holding", which is
# the distinction that matters when deciding whether a standstill is the
# controller working or the controller absent.
uint8 long_control_state
uint8 LONG_CONTROL_STATE_OFF=0
uint8 LONG_CONTROL_STATE_PID=1
uint8 LONG_CONTROL_STATE_STOPPING=2
uint8 LONG_CONTROL_STATE_STARTING=3
