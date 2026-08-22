#!/usr/bin/env bash
#
# Report every output MetaDrive can produce on a converted dataset, with samples on disk.
#
#   ./scripts/sensor-survey.sh                       # workspace from .env
#   ./scripts/sensor-survey.sh junction-1            # override the workspace for this run
#   ./scripts/sensor-survey.sh -- --policy straight  # everything after -- goes to the tool
#   ./scripts/sensor-survey.sh -- --camera-width 640 --camera-height 360
#   ./scripts/sensor-survey.sh -- --step-hz 100      # the 100 Hz dataset, sensors at 100 Hz
#   GPU=integrated ./scripts/sensor-survey.sh        # force the built-in graphics
#
#   # a multi-camera rig, in place of the single forward camera and the point cloud
#   ./scripts/sensor-survey.sh -- --camera-rig rigs/cams.txt
#   ./scripts/sensor-survey.sh -- --camera-rig rigs/cams.txt \
#       --rig-record --steps 60     # every step to <workspace>/sensor-survey/rig/*.npy
#
# Answers "what can a model actually see": camera, lidar, IMU, GPS. Writes PNGs, the point
# cloud, the observation and a per-step CSV into <workspace>/sensor-survey/.
#
# Why a script rather than a command, exactly as for drive.sh: MetaDrive runs on its own
# interpreter, and which GPU renders is settled by the GLX loader before python starts. The
# cameras here need a render context, so both matter.
#
# Read from .env, all optional:
#   METADRIVE_PYTHON  the MetaDrive checkout's interpreter
#   GPU               auto (default), nvidia, or integrated
#   STEP_HZ           how many times a second the simulator advances; MetaDrive's own 10
#                     applies when unset. It is what the IMU is differenced over, so it is
#                     what makes the recorded acceleration a 100 Hz signal rather than a 10 Hz
#                     one -- and it is the rate the policy is called at. It also picks
#                     *which* dataset is surveyed, because a workspace holds one per rate:
#                     STEP_HZ=100 surveys scenarionet-100hz. A --step-hz after -- wins.

# shellcheck source=scripts/_common.sh
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

POSITIONAL=""
PASSTHROUGH=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --) shift; PASSTHROUGH=("$@"); break ;;
        -h|--help) sed -n '2,33p' "$SELF" | sed 's/^#\s\?//'; exit 0 ;;
        -*) die "unknown option: $1
  This script takes only a workspace. To pass $1 to sensor_survey.py, put it after --:
    ./scripts/sensor-survey.sh ${POSITIONAL:-<workspace>} -- $1" ;;
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

# The survey drives, so it needs a recorded car for the same reason drive.sh does:
# ScenarioMapManager.reset calls get_sdc_track() unconditionally and a map-only dataset dies
# on KeyError('None') deep inside MetaDrive, which reads like a broken dataset and is not one.
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
  Pick routes in $WS/inspection/stage-6-route-builder.html, save routes/routes.json, then:
    uv run osm-scenario convert -w $WS --config $CONFIG \\
      --routes $WS/routes/routes.json"
fi

# Shared with drive.sh and step-timing.sh in _common.sh, which is also where the container's
# EGL path is explained.
select_gpu

ARGS=(tools/sensor_survey.py "$DATASET" --render offscreen)
if [[ -n "${STEP_HZ:-}" ]]; then
    ARGS+=(--step-hz "$STEP_HZ")
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

exec_with_gpu "$MD_PY" "${ARGS[@]}"
