# `wing_msgs` — the vehicle's own message package

Recovered **verbatim from the vehicle's own recording**, not written from a specification:

    bag       bags/074143/drive_20260826-074143_0.mcap
    recorded  2026-08-26,  3783.56 s,  6,267,599 messages,  50 topics
    read      2026-09-03

    uv run python tools/ros_defs.py bags/074143 --write tools/wing_msgs --package wing_msgs

Nine files: six message types on topics of their own, plus the three `{stamp, value}` wrappers
`VehicleState` is built out of. `ros_schema._vendored_definitions` loads them at import.

## Not to be confused with `tools/wingfin_msgs/`

`wing_msgs` is **the vehicle's**. `wingfin_msgs` is **ours** — two types invented for
`/perception/traffic_lights`, a topic the vehicle does not have. Different packages, no
collision, and the earlier assumption that they were one package was simply wrong.

## The comments are the specification, and three of them are load-bearing

Nothing else in this repo states these, and each is silent when got wrong:

- **`VehicleState.steering_angle_deg` is in degrees.** Every other angle in the package is SI
  (`EgoPose.heading_rad`, `GnssPose.yaw_rad`, `PredictedTrajectory.orientation_z`). The degree
  convention runs unbroken from the DBC through `ControlCommand`, `ActuatorsOutput` and
  openpilot's own `steeringAngleDeg`, and converting one link of that chain in isolation puts a
  factor of 57.3 somewhere no reader would look for it.
- **`wheel_speed_*` is m/s, not the DBC's km/h.** The `/3.6` used to live two nodes away. In
  simulation all four carry the ego speed — the definition says exactly that about the CARLA
  bridge, so following it is following a stated convention rather than inventing one.
- **`cruise_standstill` must be published `false` by a producer with no ACC**, never copied from
  `standstill` beside it. The comment records the measured cost of that substitution: an
  engaged, unfaulted stack braking forever with a healthy plan asking to accelerate, 5,066
  cycles of it.

## Absence has a representation, and it should be used

Every `VehicleState` field is a `{stamp, value}` pair, and **a field whose stamp is zero has
never been filled**. That is the message's own in-band way of saying "no data", and it is what
the simulator should use for `steering_torque`, `steering_pressed`, `door_open`,
`seatbelt_unlatched` and `blindspot_*` — quantities a simulated car does not have. A plausible
`false` in those fields is a claim; a zero stamp is the truth. Same principle as NaN-rather-than-
zero in `tools/sbg_msgs/`.

`EngagementStatus` says the same thing at the topic level: *absence of this message is a state*.
So it is written only when something is actually driving, and a consumer is expected to render
"no data" as disengaged rather than holding the last value.

## They do not travel into the bag as written

`rosbags` regenerates the definition from its parsed typestore when it writes a connection, so a
bag carries the field list alone, comments stripped. That is what a decoder needs and it
round-trips exactly; the prose lives here.
