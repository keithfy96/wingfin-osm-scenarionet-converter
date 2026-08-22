# Shared setup for the stage runner scripts. Sourced by them, never executed.
#
# Everything here is about getting the two things every stage command needs -- the
# workspace and the config -- from one place, so switching workspaces is one edit in
# .env rather than six edits on a command line.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# The absolute path of the script that sourced this one, resolved *before* the cd below.
# Every script's -h handler prints its own header with `sed` over this path, and
# `${BASH_SOURCE[0]}` there is relative to the directory the user was standing in -- which
# stops resolving the moment we leave it. That broke `-h` for all five scripts run from
# inside `scripts/`, which is where they are normally run from.
SELF="$(cd "$(dirname "${BASH_SOURCE[1]}")" && pwd)/$(basename "${BASH_SOURCE[1]}")"

cd "$REPO_ROOT"

# .env is per-machine and gitignored; .env.example is the committed template.
if [[ -f .env ]]; then
    set -a
    # shellcheck disable=SC1091
    source .env
    set +a
fi

CONFIG="${CONFIG:-config/default.yaml}"

# Built as an array so the optional -v never has to be an empty word.
CLI=(uv run osm-scenario)
if [[ "${VERBOSE:-0}" == "1" ]]; then
    CLI+=(-v)
fi

die() {
    printf '\n  %s\n\n' "$*" >&2
    exit 1
}

note() {
    printf '  %s\n' "$*"
}

banner() {
    printf '\n\033[1m== %s\033[0m\n' "$*"
}

# Resolve $1 (or $WORKSPACE) into $WS. A bare name means workspaces/<name>; anything
# with a slash in it is taken as a path exactly as written.
resolve_workspace() {
    local requested="${1:-${WORKSPACE:-}}"
    if [[ -z "$requested" ]]; then
        die "no workspace: set WORKSPACE in .env (copy .env.example) or pass one as an argument."
    fi
    if [[ "$requested" == */* ]]; then
        WS="${requested%/}"
    else
        WS="workspaces/$requested"
    fi
    if [[ ! -d "$WS" ]]; then
        local have
        have="$(find workspaces -mindepth 1 -maxdepth 1 -type d -printf '%f ' 2>/dev/null)"
        die "no such workspace: $WS (have: ${have:-none})"
    fi
    [[ -f "$WS/source/manifest.json" ]] || die "$WS has no source/manifest.json -- it is not a workspace."
    [[ -f "$WS/source/map.osm" ]] || die "$WS has no source/map.osm."
}

# Resolve which of $WS's datasets to drive into $DATASET, and the rate it holds into
# $DATASET_HZ. A workspace holds one dataset per rate -- `convert --step-hz 100` writes
# `scenarionet-100hz` and no flag writes `scenarionet-10hz` -- because a dataset can only be
# replayed at the rate it was written at, so the two cannot share a directory.
#
# The rate is read from the caller's own passthrough args first and from $STEP_HZ second, so
# there is one way to say it: `-- --step-hz 100` picks the 100 Hz dataset rather than
# pointing a 100 Hz simulator at the 10 Hz one and being refused by drive.py.
#
# Call after resolve_workspace, with the passthrough args: resolve_dataset "${PASSTHROUGH[@]}"
resolve_dataset() {
    DATASET_HZ="${STEP_HZ:-10}"
    # Last wins, the same way argparse takes a repeated option.
    local previous=""
    local word
    for word in "$@"; do
        if [[ "$previous" == "--step-hz" ]]; then
            DATASET_HZ="$word"
        elif [[ "$word" == --step-hz=* ]]; then
            DATASET_HZ="${word#--step-hz=}"
        fi
        previous="$word"
    done

    DATASET="$WS/scenarionet-${DATASET_HZ}hz"
    if [[ -f "$DATASET/dataset_summary.pkl" ]]; then
        return 0
    fi

    local have
    have="$(find "$WS" -mindepth 1 -maxdepth 1 -type d -name 'scenarionet-*hz' -printf '%f ' 2>/dev/null)"
    if [[ -n "$have" ]]; then
        die "no ${DATASET_HZ} Hz dataset at $WS/scenarionet-${DATASET_HZ}hz (have: $have).
  Drive one of those instead, or convert this rate:
    ${CLI[*]} convert -w $WS --config $CONFIG \\
      --routes $WS/routes/routes.json --step-hz $DATASET_HZ"
    fi

    # A dataset converted before the directory carried the rate. Left where it is rather
    # than renamed, so an existing build keeps driving, and only reached when the workspace
    # has no rate-named dataset at all -- once it has one, a bare `scenarionet` is a build
    # from before the rename and not an answer to which rate was asked for. drive.py reads
    # the rate off the pickle rather than the name, so a mismatch is still refused there.
    if [[ -f "$WS/scenarionet/dataset_summary.pkl" ]]; then
        DATASET="$WS/scenarionet"
        return 0
    fi

    die "$WS has no dataset at all. Run ./scripts/run-stages-4-6.sh $WS first."
}

# Print every dataset in $WS, one path per line, ordered by rate. Where `resolve_dataset`
# picks the one a drive asked for, this is for a tool that wants them all -- the whole point
# of the step-timing sweep is comparing one rate against another in the same workspace.
#
# Falls back to a bare pre-rename `scenarionet` only when there is no rate-named dataset at
# all, for the same reason `resolve_dataset` does: once a workspace has one, a bare directory
# is a build from before the rename rather than an answer about a rate.
list_datasets() {
    local found=0
    local name
    while IFS= read -r name; do
        [[ -f "$WS/$name/dataset_summary.pkl" ]] || continue
        printf '%s\n' "$WS/$name"
        found=1
    done < <(
        find "$WS" -mindepth 1 -maxdepth 1 -type d -name 'scenarionet-*hz' -printf '%f\n' \
            2>/dev/null | sed 's/^scenarionet-//; s/hz$//' | sort -n \
            | sed 's/^/scenarionet-/; s/$/hz/'
    )

    if [[ $found -eq 0 && -f "$WS/scenarionet/dataset_summary.pkl" ]]; then
        printf '%s\n' "$WS/scenarionet"
        found=1
    fi

    [[ $found -eq 1 ]] || die "$WS has no dataset at all. Run ./scripts/run-stages-4-6.sh $WS first."
}

# Decide which card renders into $USE_NVIDIA, and what to say about it into $GPU_NOTE. It cannot
# be a flag on the python side: the GLX loader settles it before the process exists, so it is two
# environment variables in front of the command.
#
# All three of drive.sh, sensor-survey.sh and step-timing.sh call this. They used to keep their own
# copies -- a working file not being disturbed to save a duplicated composition -- and the three
# then had to agree about a second environment none of them was written for. They did not: in the
# container all three printed "discrete, via PRIME offload", which is not what happened there.
# __NV_PRIME_RENDER_OFFLOAD and __GLX_VENDOR_LIBRARY_NAME are read by the *GLX* loader, and the
# image loads panda3d's EGL display first (docker/Dockerfile), where libglvnd picks the card from
# the ICD manifest at /usr/share/glvnd/egl_vendor.d/10_nvidia.json instead. Only the line was
# wrong -- the RTX rendered either way, which `gl_renderer` in the step-timing CSV is the check on.
#
# The variables are still set in there, deliberately. They cost nothing under EGL, and the image
# keeps `pandagl` as the aux display so that mounting an X socket still gets the 3D row -- which is
# the one path in the container where they would matter again. Dropping them to match the label
# would change which card renders on that path to fix a word.
select_gpu() {
    GPU="${GPU:-auto}"
    USE_NVIDIA=0
    IN_CONTAINER=0
    if [[ -f /.dockerenv ]]; then
        IN_CONTAINER=1
    fi
    case "$GPU" in
        auto)
            if [[ -e /usr/lib/x86_64-linux-gnu/libGLX_nvidia.so.0 ]] \
                && nvidia-smi -L >/dev/null 2>&1; then
                USE_NVIDIA=1
            fi
            ;;
        nvidia) USE_NVIDIA=1 ;;
        integrated) USE_NVIDIA=0 ;;
        *) die "GPU must be auto, nvidia or integrated (got: $GPU)" ;;
    esac

    if [[ $IN_CONTAINER -eq 1 ]]; then
        GPU_NOTE="picked by EGL in the container, not by GPU=$GPU"
    elif [[ $USE_NVIDIA -eq 1 ]]; then
        GPU_NOTE="discrete, via PRIME offload (GPU=$GPU)"
    else
        GPU_NOTE="whatever the display is on (GPU=$GPU)"
    fi
}

# exec a command with the offload variables in front of it where they do something. Call after
# select_gpu; shared with it so the environment a run was taken in cannot drift from the line
# that was printed about it.
exec_with_gpu() {
    if [[ $USE_NVIDIA -eq 1 ]]; then
        exec env __NV_PRIME_RENDER_OFFLOAD=1 __GLX_VENDOR_LIBRARY_NAME=nvidia "$@"
    fi
    exec "$@"
}

# Read a dotted key out of the workspace manifest. Prints nothing and returns 1 when
# the key is absent, so callers can distinguish missing from empty.
manifest_get() {
    python3 - "$WS/source/manifest.json" "$1" <<'PY'
import json
import sys

path, key = sys.argv[1], sys.argv[2]
value = json.load(open(path, encoding="utf-8"))
for part in key.split("."):
    if not isinstance(value, dict) or part not in value:
        sys.exit(1)
    value = value[part]
print(value)
PY
}

# Run one stage, timing it, and stop the whole run if it fails. The command's output is
# also kept in $STAGE_LOG so a caller can explain a known failure rather than leaving a
# bare non-zero exit.
STAGE_LOG="$(mktemp)"
trap 'rm -f "$STAGE_LOG"' EXIT

run_stage() {
    local title="$1"
    shift
    banner "$title"
    local started=$SECONDS
    local status=0
    "$@" 2>&1 | tee "$STAGE_LOG" || status=$?
    if [[ $status -ne 0 ]]; then
        return $status
    fi
    printf '  \033[2m(%ds)\033[0m\n' "$((SECONDS - started))"
}
