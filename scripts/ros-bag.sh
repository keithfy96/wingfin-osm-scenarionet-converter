#!/usr/bin/env bash
#
# Record a drive as a ROS 2 bag, in the container the vehicle rig records.
#
#   ./scripts/ros-bag.sh junction-1 -- --out bags/junction-1-001
#   ./scripts/ros-bag.sh junction-1 -- --out bags/run2 --traffic live
#   ./scripts/ros-bag.sh --audit bags/junction-1-001      # read one back, index only
#
# Everything after -- goes to tools/drive.py, so every flag a drive already has works here:
# --agent-policy, --traffic live, --lights live, --step-hz, --extra-seconds, --camera-rig.
# --out is this script's own name for --ros-bag.
#
# **This needs Python 3.10+, and on the host drive.py runs on the MetaDrive checkout's 3.8.**
# `rosbags` has no 3.8 wheel and does not need one -- MetaDrive runs perfectly well on 3.10.
# Two ways round it, and the script checks before anything is built:
#
#   ./scripts/sim.sh --no-model ./scripts/ros-bag.sh ...   # the container: one 3.10 interpreter
#   METADRIVE_PYTHON=.venv/bin/python ./scripts/ros-bag.sh ...
#       after: uv sync --group sim --group ros         # name every group -- one alone removes
#                                                        the others
#
# **`--no-model` is not optional in the container.** compose.yaml always sets MODEL_CHECKPOINT to
# the mounted /models path, drive.py takes it as the default for --model-checkpoint, and a
# checkpoint implies --agent-policy remote -- so a plain replay drive in there refuses with
# "needs --agent-policy remote, not replay" before it opens a bag. `sim.sh --no-model` passes
# `-e MODEL_CHECKPOINT=`, which is the only way to clear it: compose's `${MODEL_CHECKPOINT:-...}`
# substitutes the default for an empty value as readily as for an unset one.
#
# **The preflight is the point of having a script at all.** A bag whose traffic-light topic is
# empty because the dataset was converted without --signals is, months later, indistinguishable
# from a junction that genuinely had no lights. So this reads the dataset first and says what is
# actually in it -- and refuses --lights against a dataset carrying none, rather than recording
# an empty channel. Measured on the two workspaces as they stand: junction-1 holds 101
# pedestrians, 25 cyclists, 24 barriers and **8 traffic lights** (converted in 2026-09-02);
# mosque holds 2/1/1 and still no lights, so --lights is still refused there:
#
#   uv run osm-scenario convert -w workspaces/mosque --config config/default.yaml \
#       --routes workspaces/mosque/routes/routes.json \
#       --signals workspaces/mosque/signals/signals.json \
#       --actors workspaces/mosque/actors/actors.json
#
# Convert-time arguments are deliberately not ConverterConfig fields, so that re-run does not
# move generation_fingerprint and the Stage 3 review keeps applying.
#
# Read from .env, all optional: METADRIVE_PYTHON, GPU, STEP_HZ, DECISION_HZ.

set -euo pipefail
SELF="${BASH_SOURCE[0]}"
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

if [[ "${1:-}" == "--audit" ]]; then
    shift
    [[ $# -ge 1 ]] || die "--audit needs a bag directory"
    exec uv run python tools/ros_audit.py "$@"
fi

POSITIONAL=""
PASSTHROUGH=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --) shift; PASSTHROUGH=("$@"); break ;;
        # The header block, whose last line is `Read from .env`. A hardcoded range silently
        # truncates --help the moment a paragraph is added above it, which is what happened when
        # the container's --no-model note went in; `sed` stops at the first non-comment instead.
        -h|--help) sed -n '2,/^[^#]/p' "$SELF" | sed '/^[^#]/d; s/^#\s\?//'; exit 0 ;;
        -*) die "unknown option: $1
  This script takes a workspace, or --audit. To pass $1 to drive.py, put it after --:
    ./scripts/ros-bag.sh ${POSITIONAL:-<workspace>} -- $1" ;;
        *) POSITIONAL="$1" ;;
    esac
    shift
done

resolve_workspace "$POSITIONAL"

MD_PY="${METADRIVE_PYTHON:-/home/keith/Desktop/work/wingfin/metadrive/.venv/bin/python}"
[[ -x "$MD_PY" ]] || die "no MetaDrive interpreter at $MD_PY.
  Set METADRIVE_PYTHON in .env if the checkout lives somewhere else."

resolve_dataset ${PASSTHROUGH[@]+"${PASSTHROUGH[@]}"}

# `--out` is this script's name for drive.py's `--ros-bag`. Pulled out of the passthrough so the
# preflight can name the destination before the drive starts.
OUT=""
FILTERED=()
WANT_LIGHTS=0
for ((i = 0; i < ${#PASSTHROUGH[@]}; i++)); do
    case "${PASSTHROUGH[$i]}" in
        # `i=$((i + 1))` and never `((i++))`. A bare `((expr))` returns exit status 1 when expr
        # evaluates to zero, and post-increment evaluates to the OLD value -- so with `set -e`
        # above, skipping past `--out` when it is the FIRST argument after `--` (i == 0) killed
        # the script silently: exit 1, not one byte on stdout or stderr, no bag. Which is the
        # form this script's own help, the README and docs/testing-ros.md all show, so the
        # documented command was the one that could not work. `i=$(( ))` is an assignment and
        # its status is the assignment's, not the arithmetic's.
        --out) OUT="${PASSTHROUGH[$((i + 1))]:-}"; i=$((i + 1)) ;;
        --lights) WANT_LIGHTS=1; FILTERED+=("${PASSTHROUGH[$i]}") ;;
        *) FILTERED+=("${PASSTHROUGH[$i]}") ;;
    esac
done
[[ -n "$OUT" ]] || die "no destination. Give one after --:
    ./scripts/ros-bag.sh ${WS##*/} -- --out bags/${WS##*/}-001"
[[ ! -e "$OUT" ]] || die "$OUT already exists.
  A bag is a recording, not an output file to overwrite. Move it or pick another name."

# The preflight. Reads the dataset the drive is about to replay and reports what is in it, so a
# missing channel is a refusal now rather than an absence discovered in a training run.
PREFLIGHT="$("$MD_PY" - "$DATASET" <<'PY'
import collections
import glob
import pickle
import sys

files = sorted(glob.glob(sys.argv[1] + "/sd_*.pkl"))
if not files:
    print("EMPTY")
    raise SystemExit(0)
with open(files[0], "rb") as handle:
    scenario = pickle.load(handle)
tracks = scenario.get("tracks") or {}
kinds = collections.Counter(track.get("type") for track in tracks.values())
lights = len(scenario.get("dynamic_map_states") or {})
crs = 1 if (scenario.get("metadata") or {}).get("coordinate_system_wkt") else 0
actors = sum(count for kind, count in kinds.items() if kind != "VEHICLE")
print(f"{len(files)} {actors} {lights} {crs} {scenario.get('length', 0)}")
print(" ".join(f"{kind}={count}" for kind, count in sorted(kinds.items())))
PY
)"
SCENARIOS=$(echo "$PREFLIGHT" | head -1 | cut -d' ' -f1)
ACTORS=$(echo "$PREFLIGHT" | head -1 | cut -d' ' -f2)
LIGHTS=$(echo "$PREFLIGHT" | head -1 | cut -d' ' -f3)
HAS_CRS=$(echo "$PREFLIGHT" | head -1 | cut -d' ' -f4)
BREAKDOWN=$(echo "$PREFLIGHT" | sed -n '2p')

note "workspace  $WS"
note "dataset    ${DATASET#"$WS/"}  ($SCENARIOS scenario(s))"
note "tracks     $BREAKDOWN"
note "lights     $LIGHTS in the dataset"
if [[ "$HAS_CRS" == "1" ]]; then
    note "gnss       real lat/lon available (the dataset carries its projection)"
else
    note "gnss       NO projection in this dataset -- the GNSS topics will be absent"
fi

if [[ "$WANT_LIGHTS" == "1" && "$LIGHTS" == "0" ]]; then
    die "you asked for --lights, and this dataset has no traffic lights in it.
  dynamic_map_states is empty, so the lights topic would be recorded empty -- which months
  later is indistinguishable from a junction that genuinely had none.
  Convert them in first (this does NOT move the fingerprint, so the Stage 3 review still
  applies). One line -- a backslash continuation does not survive every paste:
    uv run osm-scenario convert -w $WS --config $CONFIG --routes $WS/routes/routes.json --signals $WS/signals/signals.json --actors $WS/actors/actors.json"
fi
if [[ "$ACTORS" == "0" ]]; then
    note "warning    no pedestrians, cyclists or static objects -- the labels will be cars only"
fi

select_gpu

ARGS=(tools/drive.py "$DATASET" --render offscreen --ros-bag "$OUT")
if [[ -n "${STEP_HZ:-}" ]]; then ARGS+=(--step-hz "$STEP_HZ"); fi
if [[ -n "${DECISION_HZ:-}" ]]; then ARGS+=(--decision-hz "$DECISION_HZ"); fi
ARGS+=(${FILTERED[@]+"${FILTERED[@]}"})

note "python     $MD_PY"
# The absolute path, not the relative one the caller typed. `--out` is resolved against the
# current directory and nothing here changes directory, so a relative name is unambiguous to the
# script and not to the person reading the scroll-back an hour later -- who then greps for a
# `bags/` that is one `cd` away. Printed before the drive rather than after because
# `exec_with_gpu` below **execs**: bash is replaced by python, so nothing after it runs, and it
# stays that way -- putting a shell back between the terminal and python is what breaks the
# Ctrl-C handling that ends a drive at a frame boundary and still exports it.
# `realpath -m` and not a `cd`: the bag does not exist yet and neither does `bags/`, so
# anything that resolves by entering the directory prints "/j1-lights". -m resolves a path that is
# not there. The fallback covers a system without coreutils' realpath.
OUT_ABS="$(realpath -m "$OUT" 2>/dev/null || echo "$PWD/$OUT")"
note "out        $OUT_ABS"
printf '\n'

exec_with_gpu "$MD_PY" "${ARGS[@]}"
