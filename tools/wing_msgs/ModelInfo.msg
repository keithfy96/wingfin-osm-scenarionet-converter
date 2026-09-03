# Which driving model this run is executing — published LATCHED
# (wing_qos::latched(): KeepLast(1), reliable, transient_local) exactly once
# by whichever model node the launch layer started, so a recorder or late
# joiner can always recover it. Exactly one model node exists per run by
# construction (autonomy.launch.py's runner if/else), so this topic is
# single-writer the same way /perception/inference_control is.
#
# Until this message existed the model identity travelled only as launch
# parameters into wing_ui and an RCLCPP_INFO line — neither lands in a bag
# reliably, so a recorded drive could not say which weights produced its
# trajectories.

std_msgs/Header header

# The config selection: the model/<name>.yaml basename, i.e. what MODEL= was
# set to ("av2.1", "supercombo", ...).
string model_name

# The weights as configured: model.checkpoint from that YAML, unjoined with
# the container mount prefix (same display form wing_ui shows), e.g.
# "openpilot095/supercombo_fp32.onnx". The name identifies the artefact;
# where it was mounted from is a deployment detail.
string weight_name
