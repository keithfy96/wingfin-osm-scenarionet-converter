# Driving-model output: the predicted trajectory plus the pose it was
# predicted from.
#
# Carried over field-for-field from the ROS 1 `msg` package so that
# ros1_bridge mapping and bag conversion stay trivial. Do not reorder or
# rename fields without bumping the interface contract (see CHANGELOG).

std_msgs/Float64MultiArray waypoints
float64 x
float64 y
float64 theta
int64 frame_counter
int64 img_tstamp
