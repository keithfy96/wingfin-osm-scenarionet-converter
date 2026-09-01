#!/usr/bin/env bash
#
# Look at a bag this repo wrote, in rviz2.
#
#   ./scripts/ros-view.sh bags/j1-001
#   ./scripts/ros-view.sh bags/j1-001 --loop          # repeat instead of playing once
#   ./scripts/ros-view.sh bags/j1-001 --rate 0.5      # anything else after the bag goes to bag play
#   ./scripts/ros-view.sh --build                     # rebuild the viewer image
#
# **Every check in docs/testing-ros.md is numeric.** This is the one that is not. What it shows
# that a number cannot: whether the car crawls along the road or teleports between frames,
# whether the boxes sit ON the road and move WITH the people rather than hovering or lagging a
# frame, and whether the route runs where the car actually went. All three are consistent with
# every relationship ros_probe.py checks and would still be visibly wrong.
#
# A separate image from wingfin-sim, and the reasoning is in docker/ros-viewer/Dockerfile: a bag
# is a file, and looking at one needs ROS and a display, not MetaDrive and 13.3 GB of CUDA.
#
# **`--delay` is what makes a single pass work, and it took three tries to find that out.**
# `/planning/route` is one message at t=0. rviz2 needs about two seconds to start, so it used to
# subscribe after that message had already gone by, and the route drew only because playback
# looped and brought it round again 36 s later.
#
# Recording the topic as transient-local (`ros_bag._latched_qos`) is correct and was not enough:
# **`ros2 bag play` does not serve a latched message from a durability cache to a late joiner.**
# Measured, 3 trials of 3, a transient-local subscriber joining 5 s in received nothing at all --
# with no warning either, because the QoS now matches. The QoS fix removes the incompatibility;
# it does not move the message.
#
# `--delay` does, by not publishing until every subscriber is up: 3 trials of 3, a subscriber
# joining 2 s into a 4 s delay received the whole 29,325-byte route. **If the route stops drawing
# on a single pass, this delay is the first thing to look at.**
#
# The loop also resets rviz2 every lap -- `TF_OLD_DATA`, then `Detected jump back in time` -- which
# is correct behaviour for a clock that restarts and reads exactly like a fault.
#
# **use_sim_time, and the bag's own /clock.** Every stamp in the bag is simulator time starting at
# epoch zero, so a viewer on the wall clock puts every message ~56 years in the past and tf drops
# all of it -- an empty screen with no error anywhere. `ros2 bag play --clock` publishes /clock
# and rviz2 runs with use_sim_time:=true, and neither half works without the other.

set -euo pipefail
SELF="${BASH_SOURCE[0]}"
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

IMAGE=wingfin-ros-viewer
RVIZ_CONFIG=config/rviz/bag.rviz

build_image() {
    note "building $IMAGE from docker/ros-viewer/Dockerfile"
    docker build -f docker/ros-viewer/Dockerfile -t "$IMAGE" .
}

if [[ "${1:-}" == "--build" ]]; then
    build_image
    exit 0
fi
if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    sed -n '2,/^[^#]/p' "$SELF" | sed '/^[^#]/d; s/^#\s\?//'
    exit 0
fi

LOOP=0
if [[ "${1:-}" == "--loop" ]]; then LOOP=1; shift; fi

BAG="${1:-}"
[[ -n "$BAG" ]] || die "no bag. Give one:
    ./scripts/ros-view.sh bags/j1-001
  Record one first if there is none:
    METADRIVE_PYTHON=.venv/bin/python ./scripts/ros-bag.sh junction-1 -- --out bags/j1-001"
shift
[[ -d "$BAG" ]] || die "no bag at $BAG - nothing has written one there yet.
  A bag is a directory holding a .mcap and a metadata.yaml, not a file."
[[ -f "$RVIZ_CONFIG" ]] || die "no rviz config at $RVIZ_CONFIG"

# The display, and the two ways a container reaches one. Checked here rather than left to rviz2,
# whose failure is a Qt message several screens into its own startup noise.
[[ -n "${DISPLAY:-}" ]] || die "DISPLAY is not set, so there is no X server to draw on.
  On Wayland the XWayland socket is what this uses; \`echo \$DISPLAY\` should print something
  like :0. Over ssh, use \`ssh -X\`."
[[ -S /tmp/.X11-unix/X"${DISPLAY##*:}" || -e /tmp/.X11-unix ]] || die \
    "no X socket at /tmp/.X11-unix for DISPLAY=$DISPLAY."

docker image inspect "$IMAGE" >/dev/null 2>&1 || build_image

# The cookie, not `xhost +local:`. Relaxing X access control is a change to the machine's own
# security settings that outlives the command, and it is not needed: the container reads the same
# MIT-MAGIC-COOKIE the host uses. Root in the container can read a 0600 file owned by the host uid.
XAUTH="${XAUTHORITY:-$HOME/.Xauthority}"
[[ -r "$XAUTH" ]] || die "no readable X cookie at $XAUTH (set XAUTHORITY).
  Without it the container cannot authenticate to DISPLAY=$DISPLAY, and access control here is
  on -- \`xhost\` reports 'only authorized clients can connect'."

# Hardware GL if the machine has a render node, software if not. rviz2 runs either way -- without
# it Mesa fails to load its DRI driver, says so twice, and falls back; measured, it still reported
# OpenGl version: 4.5. Conditional rather than unconditional because a device that is not there
# is a hard `docker run` failure, and this must not be the reason a rig cannot look at a bag.
GL_FLAGS=()
if [[ -d /dev/dri ]]; then
    GL_FLAGS+=(--device /dev/dri:/dev/dri)
    note "gl         /dev/dri present - hardware rendering"
else
    note "gl         no /dev/dri - software rendering, which is slower and still correct"
fi

# `--loop` is accepted after the bag too, since that is where every other passthrough flag goes.
REST=()
for word in ${@+"$@"}; do
    if [[ "$word" == "--loop" ]]; then LOOP=1; else REST+=("$word"); fi
done
set -- ${REST[@]+"${REST[@]}"}

# Seconds of head start for rviz2 and the marker node, before the first message is published.
# See the header: without it the latched route is published into an empty graph.
PLAY_DELAY="${PLAY_DELAY:-4}"

PLAY_FLAGS=(--delay "$PLAY_DELAY")
[[ "$LOOP" == "1" ]] && PLAY_FLAGS+=(--loop)

BAG_NAME="$(basename "$BAG")"
note "bag        $(realpath -m "$BAG")"
note "config     $RVIZ_CONFIG"
note "display    $DISPLAY  (cookie $XAUTH)"
if [[ "$LOOP" == "1" ]]; then
    note "playing    /bags/$BAG_NAME on repeat after ${PLAY_DELAY}s -- rviz2 resets its clock each lap"
else
    note "playing    /bags/$BAG_NAME once, after a ${PLAY_DELAY}s head start  (add --loop to repeat)"
fi
printf '\n'

# One container running both, because they have to share a /clock and the ROS graph that carries
# it. `bag play` in the background and rviz2 in the foreground, so closing the window ends the
# run rather than leaving a player publishing into nothing.
#
# The extra arguments go in as **arguments**, after a `_` placeholder for $0, and not
# interpolated into the `bash -c` string. Interpolating splits anything containing a space --
# `--rate 0.5` happens to survive it and a path with a space does not.
# `-it` only when there is a terminal to attach. `docker run -it` without one fails outright
# ("the input device is not a TTY"), which would make this unusable from a script or a timeout --
# and a timed run is how the window gets checked without a person sitting in front of it.
TTY_FLAGS=()
[[ -t 0 ]] && TTY_FLAGS+=(-i -t)

exec docker run --rm ${TTY_FLAGS[@]+"${TTY_FLAGS[@]}"} \
    -e DISPLAY \
    -e XAUTHORITY=/tmp/.docker.xauth \
    -e QT_X11_NO_MITSHM=1 \
    -v "$XAUTH:/tmp/.docker.xauth:ro" \
    -v /tmp/.X11-unix:/tmp/.X11-unix:ro \
    -v "$(realpath -m "$BAG"):/bags/$BAG_NAME:ro" \
    -v "$(realpath -m "$RVIZ_CONFIG"):/rviz/bag.rviz:ro" \
    "${GL_FLAGS[@]}" \
    --network host \
    "$IMAGE" bash -c '
        python3 /opt/wingfin/light_markers.py --ros-args -p use_sim_time:=true &
        ros2 bag play "$@" --clock &
        exec rviz2 -d /rviz/bag.rviz --ros-args -p use_sim_time:=true
    ' _ "/bags/$BAG_NAME" ${PLAY_FLAGS[@]+"${PLAY_FLAGS[@]}"} ${@+"$@"}
