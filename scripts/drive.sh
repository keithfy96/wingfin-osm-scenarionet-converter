#!/usr/bin/env bash
#
# Drive a converted dataset in MetaDrive, on the discrete GPU when the machine has one.
#
#   ./scripts/drive.sh                          # workspace from .env, 3D window
#   ./scripts/drive.sh junction-1               # override the workspace for this run
#   ./scripts/drive.sh -- --render 2D           # everything after -- goes to tools/drive.py
#   ./scripts/drive.sh -- --line-width-m 0.1    # thinner lane lines, this run only
#   ./scripts/drive.sh -- --step-hz 100         # 100 Hz; the dataset must match
#   GPU=integrated ./scripts/drive.sh           # force the built-in graphics
#
# Why a script rather than a command: MetaDrive runs on its own interpreter (3.8 / numpy 1.24,
# against this repo's 3.10 / numpy 2.2), and which GPU renders is settled by the GLX loader
# before python starts -- so driving anything means two environment variables in front of a
# long absolute path, every time.
#
# Read from .env, all optional:
#   METADRIVE_PYTHON  the MetaDrive checkout's interpreter
#   GPU               auto (default), nvidia, or integrated
#   LINE_WIDTH_M      painted lane-line width in metres; drive.py's own default applies
#   LINE_INTERVAL_M   how finely a painted line is sampled; 2.0 restores MetaDrive's dashes
#                     when unset, and a --line-width-m after -- still wins over this
#   STEP_HZ           how many times a second the simulator advances; MetaDrive's own 10
#                     applies when unset. A dataset converted at another rate must be driven
#                     at that rate -- drive.py refuses a mismatch rather than driving it wrong

# shellcheck source=scripts/_common.sh
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

POSITIONAL=""
PASSTHROUGH=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --) shift; PASSTHROUGH=("$@"); break ;;
        -h|--help) sed -n '2,25p' "$SELF" | sed 's/^#\s\?//'; exit 0 ;;
        -*) die "unknown option: $1
  This script takes only a workspace. To pass $1 to drive.py, put it after --:
    ./scripts/drive.sh ${POSITIONAL:-<workspace>} -- $1" ;;
        *) POSITIONAL="$1" ;;
    esac
    shift
done

resolve_workspace "$POSITIONAL"

MD_PY="${METADRIVE_PYTHON:-/home/keith/Desktop/work/wingfin/metadrive/.venv/bin/python}"
[[ -x "$MD_PY" ]] || die "no MetaDrive interpreter at $MD_PY.
  It is a different interpreter from this repo's on purpose -- MetaDrive is 3.8 / numpy 1.24.
  Set METADRIVE_PYTHON in .env if the checkout lives somewhere else."

DATASET="$WS/scenarionet"
[[ -f "$DATASET/dataset_summary.pkl" ]] || die "no dataset at $DATASET.
  Run ./scripts/run-stages-4-6.sh${POSITIONAL:+ $POSITIONAL} first."

# ScenarioEnv has no start-and-end setting: ScenarioMapManager.reset calls get_sdc_track()
# unconditionally, so a dataset with no recorded car dies on KeyError('None') deep inside
# MetaDrive. The summary records sdc_id per scenario, so the same fact is one cheap read away.
if ! "$MD_PY" - "$DATASET" <<'PY'
import pickle
import sys

path = sys.argv[1] + "/dataset_summary.pkl"
with open(path, "rb") as handle:
    summary = pickle.load(handle)
sys.exit(0 if summary and all(entry.get("sdc_id") for entry in summary.values()) else 1)
PY
then
    die "$DATASET is map-only -- it has no recorded car, so there is nothing to drive.
  MetaDrive would fail with KeyError: None, which reads like a broken dataset and is not one.
  Pick routes in $WS/inspection/stage-6-route-builder.html, save routes/routes.json, then:
    uv run osm-scenario convert -w $WS --config $CONFIG \\
      --routes $WS/routes/routes.json"
fi

# Which card renders is decided by the GLX loader at process start, so it cannot be a flag on
# drive.py -- it has to be set in the environment of the process before it exists.
GPU="${GPU:-auto}"
USE_NVIDIA=0
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

ARGS=(tools/drive.py "$DATASET" --render 3D)
if [[ -n "${LINE_WIDTH_M:-}" ]]; then
    ARGS+=(--line-width-m "$LINE_WIDTH_M")
fi
if [[ -n "${LINE_INTERVAL_M:-}" ]]; then
    ARGS+=(--line-interval-m "$LINE_INTERVAL_M")
fi
if [[ -n "${STEP_HZ:-}" ]]; then
    ARGS+=(--step-hz "$STEP_HZ")
fi
# Last wins in argparse, so anything repeated after -- overrides what this script chose.
ARGS+=(${PASSTHROUGH[@]+"${PASSTHROUGH[@]}"})

note "workspace  $WS"
note "python     $MD_PY"
if [[ $USE_NVIDIA -eq 1 ]]; then
    note "gpu        discrete, via PRIME offload (GPU=$GPU)"
else
    note "gpu        whatever the display is on (GPU=$GPU)"
fi
printf '\n'

# exec, not run_stage: run_stage pipes through tee and times the run, and a 3D window wants
# neither. The `gpu` line drive.py prints is the confirmation of which card was actually used.
if [[ $USE_NVIDIA -eq 1 ]]; then
    exec env __NV_PRIME_RENDER_OFFLOAD=1 __GLX_VENDOR_LIBRARY_NAME=nvidia \
        "$MD_PY" "${ARGS[@]}"
fi
exec "$MD_PY" "${ARGS[@]}"
