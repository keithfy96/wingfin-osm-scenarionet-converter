# `wingfin_msgs` message definitions

The `.msg` text for the vehicle package's types, loaded at import by
`ros_schema._wingfin_definitions`. `Stores.ROS2_HUMBLE` has never heard of the package, so
without these a topic carrying one of its types cannot be written at all — the rule
`ros_schema.py` states at the top of the file: no definition, no topic, because a topic
serialised against a guessed field list is worse than an absent one.

**This directory is how stage 11 phase 5 lands.** Fifteen of the rig's forty-five producible
topics are waiting on types only this package defines, and the plan for them has never been to
reconstruct the field lists — it is to read them out of a bag the rig recorded:

```bash
uv run python tools/ros_defs.py /path/to/ros2_mig_phase_5_p1 \
    --write tools/wingfin_msgs --package wingfin_msgs
```

Every file that command writes is registered by the loader with no edit to `ros_schema.py`.

## What is here today, and why it is ours

| file | origin |
|---|---|
| `TrafficLight.msg` | ours — invented for `/perception/traffic_lights`, a topic the rig's bag does not have |
| `TrafficLightArray.msg` | ours — same |

A traffic light is a position and a colour name, and no ROS core message carries that.
`visualization_msgs/Marker` would put the state in an RGBA value, which renders nicely and is
poor training data: *"was this light red"* should not be a floating-point comparison.

Inventing a type is safe **here** in a way it would not be for one of the rig's own topics,
because rosbag2 writes the definition text into the bag itself — a reader decodes these without
our package, and there is no installed `wingfin_msgs` anywhere for them to disagree with.

Both files were produced by the command above, run against `bags/j1-lights`, and came back byte
for byte identical to the string literals they replaced. That was the point of moving them: the
loader phase 5 depends on is now carried by every bag test in the suite, rather than being
exercised for the first time on the day the rig's file arrives.

## The trap: ours and the rig's share one namespace

`wingfin_msgs` is *the vehicle's* package. The two files above are ours, sitting in it because
that is the package name the topic they serve was written under — so a rig bag carrying a real
`wingfin_msgs/TrafficLightArray` would land on top of ours, and every bag written afterwards
would serialise our traffic lights against a field list nothing in this repo agreed to. CDR
carries no field names, so nothing downstream would raise.

`ros_defs.vendor` refuses that: a target file whose text differs from the recovered definition is
reported as `CONFLICT` and skipped, and `report` returns non-zero. Resolving one is a decision
for a person — either the rig's definition wins and `/perception/traffic_lights` is rebuilt
against it, or ours is renamed out of the collision. `--force` exists for after that decision,
not instead of it.

## They do not travel into the bag as written

`rosbags` regenerates the definition from its parsed typestore when it writes a connection, so a
bag carries the field list alone. That is what a decoder needs and it round-trips exactly; it is
also why comments in a vendored file cost nothing and are worth keeping when upstream has them,
as `tools/sbg_msgs/` does.
