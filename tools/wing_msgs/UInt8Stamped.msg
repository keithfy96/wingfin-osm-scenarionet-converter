# An enum-sized integer paired with the time of the sample it was decoded
# from. See Float64Stamped.msg for why the stamp nests with the value.
# Value tables (e.g. GEAR_*) stay with the message that uses the field.
builtin_interfaces/Time stamp
uint8 value
