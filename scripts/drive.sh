#!/usr/bin/env bash
#
# Drive a converted dataset in MetaDrive, on the discrete GPU when the machine has one.
#
#   ./scripts/drive.sh                          # workspace from .env, 3D window
#   ./scripts/drive.sh junction-1               # override the workspace for this run
#   ./scripts/drive.sh -- --render 2D           # everything after -- goes to tools/drive.py
#   ./scripts/drive.sh -- --line-width-m 0.1    # thinner lane lines, this run only
#   ./scripts/drive.sh -- --step-hz 100         # drives that workspace's 100 Hz dataset
#   ./scripts/drive.sh -- --step-hz 100 --decision-hz 20   # 100/20/100: 20 Hz decisions
#   GPU=integrated ./scripts/drive.sh           # force the built-in graphics
#
#   # the AV3 model supplying the trajectory a hosted controller steers by:
#   METADRIVE_PYTHON=../.venv/bin/python ./drive.sh junction-1 -- \
#       --agent-policy remote --policy-url http://127.0.0.1:8642 \
#       --model-checkpoint <the .ep> --sensors imu,route \
#       --step-hz 100 --decision-hz 20 --render offscreen
#
# **--model-checkpoint needs THIS repo's interpreter**, not the 3.8 checkout venv: torch 2.8
# has no 3.8 wheel and does not need one, MetaDrive running on 3.10. Point METADRIVE_PYTHON
# at .venv/bin/python for that run, after `uv sync --group sim --group gpu --group model`.
# A pass takes about a second, so a whole drive is minutes rather than seconds -- env.step is
# the tick, so that makes it slow and never wrong.
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
#                     applies when unset. It also picks *which* dataset is driven, because a
#                     workspace holds one per rate -- STEP_HZ=100 drives scenarionet-100hz --
#                     and a dataset can only be replayed at the rate it was written at. A
#                     --step-hz after -- wins over this, for the dataset as well as the run.
#   DECISION_HZ       how many times a second the policy is asked, the --sensors are read
#                     and -- offscreen -- the cameras are drawn, when that should be slower
#                     than the simulator itself. Unset it is the step rate: MetaDrive has no
#                     separate clock for it, env.step being the world tick, the policy call
#                     and the camera draw at once, so the middle rate is a stride counted in
#                     the tool's own loop. Must divide the step rate. STEP_HZ=100 with
#                     DECISION_HZ=20 is what openpilot's bridge is written for (_DT_MDL
#                     0.05). It does NOT pick the dataset -- STEP_HZ still does. Under
#                     --render 3D the draw is never gated: the window is the point of it.
#                     -- --draw-every-step puts the draw back on the world tick offscreen.
#   MODEL_CHECKPOINT  the AV3 .ep to drive with, which drive.py reads as the default for
#                     --model-checkpoint. Set, a drive loads the model and takes about a
#                     second a decision; unset, the waypoints come from the recorded route at
#                     constant speed, which is a controller test rather than a model one.
#                     compose.yaml sets it in the container, so -e MODEL_CHECKPOINT= is how a
#                     container run opts out.

# shellcheck source=scripts/_common.sh
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

POSITIONAL=""
PASSTHROUGH=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --) shift; PASSTHROUGH=("$@"); break ;;
        -h|--help) sed -n '2,56p' "$SELF" | sed 's/^#\s\?//'; exit 0 ;;
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

resolve_dataset ${PASSTHROUGH[@]+"${PASSTHROUGH[@]}"}

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
# drive.py -- it has to be set in the environment of the process before it exists. Shared with
# sensor-survey.sh and step-timing.sh in _common.sh, which is also where the container's EGL
# path is explained.
select_gpu

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
if [[ -n "${DECISION_HZ:-}" ]]; then
    ARGS+=(--decision-hz "$DECISION_HZ")
fi
# Last wins in argparse, so anything repeated after -- overrides what this script chose.
ARGS+=(${PASSTHROUGH[@]+"${PASSTHROUGH[@]}"})

note "workspace  $WS"
# Named because a workspace holds one dataset per rate, so which one this is cannot be read
# off the workspace alone.
note "dataset    ${DATASET#"$WS/"}"
note "python     $MD_PY"
note "gpu        $GPU_NOTE"
printf '\n'

# exec, not run_stage: run_stage pipes through tee and times the run, and a 3D window wants
# neither. The `gpu` line drive.py prints is the confirmation of which card was actually used.
exec_with_gpu "$MD_PY" "${ARGS[@]}"
