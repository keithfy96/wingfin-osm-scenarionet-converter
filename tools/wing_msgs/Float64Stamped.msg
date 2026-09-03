# A scalar paired with the time of the sample it was decoded from.
#
# Exists for VehicleState's per-field freshness: the aggregate is accumulated
# in place from DBC messages arriving at four different rates, so one header
# stamp can only describe the OLDEST fact in the message (see offerStamp in
# openpilot_cereal_bridge/can_signal_map.cpp). Nesting the stamp with the
# value lets every field say when it was actually measured.
#
# A bare builtin_interfaces/Time rather than std_msgs/Header, deliberately:
# these live inside a message that already carries the frame-bearing header,
# and a frame_id per scalar would be 20 empty strings on the wire.
#
# ⚠️ A zero stamp means the field has never been filled — the same
# zero-default hazard VehicleState's engagement block documents, now at least
# visible per field instead of silent.
builtin_interfaces/Time stamp
float64 value
