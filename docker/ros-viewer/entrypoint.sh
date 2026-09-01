#!/usr/bin/env bash
# The distro, then our own overlay. `ros_entrypoint.sh` in the base image sources only the first,
# so `wingfin_msgs` would be absent from every command run through it.
set -e
. /opt/ros/jazzy/setup.bash
. /ws/install/setup.bash
exec "$@"
