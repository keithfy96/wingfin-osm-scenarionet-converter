#!/usr/bin/env bash
#
# Open a sensor-survey point cloud in an interactive 3-D viewer.
#
#   ./scripts/view-point-cloud.sh                    # workspace from .env
#   ./scripts/view-point-cloud.sh junction-1         # override the workspace
#   ./scripts/view-point-cloud.sh mosque -- --colour distance-ahead
#   ./scripts/view-point-cloud.sh -- --max-range 0   # keep the far-plane misses too
#
# Why a script rather than a command, and two reasons that are nothing to do with
# each other:
#
#   1. Open3D is not a dependency of this repo and must not become one -- it is a
#      450 MB wheel used to look at a file, not to build one. `uv run --with open3d`
#      resolves it into a throwaway overlay, cached after the first run. It also
#      must not go into MetaDrive's venv, which is a reference checkout.
#   2. This desktop is Wayland (XDG_SESSION_TYPE=wayland), and Open3D's GLFW window
#      picks the Wayland backend and then fails GLEW initialisation -- create_window
#      returns False and get_render_option() returns None. Unsetting WAYLAND_DISPLAY
#      sends it through XWayland instead, where it works. That is one env var in
#      front of a long command, every time.
#
# Unlike drive.sh this runs on *this repo's* interpreter (3.10), not MetaDrive's --
# nothing here touches MetaDrive.

# shellcheck source=scripts/_common.sh
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

POSITIONAL=""
PASSTHROUGH=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --) shift; PASSTHROUGH=("$@"); break ;;
        -h|--help) sed -n '2,24p' "${BASH_SOURCE[0]}" | sed 's/^#\s\?//'; exit 0 ;;
        -*) die "unknown option: $1
  This script takes only a workspace. To pass $1 to view_point_cloud.py, put it after --:
    ./scripts/view-point-cloud.sh ${POSITIONAL:-<workspace>} -- $1" ;;
        *) POSITIONAL="$1" ;;
    esac
    shift
done

resolve_workspace "$POSITIONAL"

CLOUD="$WS/sensor-survey/point-cloud.npy"
[[ -f "$CLOUD" ]] || die "no point cloud at $CLOUD.
  Write one with the sensor survey first:
    ./scripts/sensor-survey.sh ${POSITIONAL:-}"

note "cloud      $CLOUD"

exec env -u WAYLAND_DISPLAY XDG_SESSION_TYPE=x11 \
    uv run --with open3d python tools/view_point_cloud.py "$CLOUD" "${PASSTHROUGH[@]}"
