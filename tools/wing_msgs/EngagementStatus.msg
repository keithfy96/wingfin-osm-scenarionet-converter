# Engagement state, republished from openpilot's cereal `controlsState` by
# the cereal bridge. openpilot owns engagement — the panda enable line and
# controlsd's state machine — so the ROS graph gets a read-out, not a vote.
#
# Absence of this message IS a state: on a bench replay controlsd sits in
# OPENPILOT_BLOCK and nothing publishes controlsState, so consumers must
# render "no data" as disengaged/unknown rather than holding the last value.

std_msgs/Header header

# Mirrors cereal's ControlsState.OpenpilotState.
uint8 STATE_DISABLED=0
uint8 STATE_PRE_ENABLED=1
uint8 STATE_ENABLED=2
uint8 STATE_SOFT_DISABLING=3
uint8 STATE_OVERRIDING=4
uint8 state

bool enabled
bool active
# Whether openpilot could be engaged right now (its own precondition check).
bool engageable

# Mirrors cereal's ControlsState.AlertStatus.
uint8 ALERT_NORMAL=0
uint8 ALERT_USER_PROMPT=1
uint8 ALERT_CRITICAL=2
uint8 alert_status

string alert_text1
string alert_text2
