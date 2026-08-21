#!/usr/bin/env bash
#
# Time env.step against the simulated time it buys, at every rate a workspace holds.
#
#   ./scripts/step-timing.sh                       # workspace from .env, every rate it holds
#   ./scripts/step-timing.sh junction-1            # override the workspace for this run
#   ./scripts/step-timing.sh junction-1 -- --rows 5           # one row on its own
#   ./scripts/step-timing.sh junction-1 -- --rows 2,6         # the pair that prices the camera
#   ./scripts/step-timing.sh junction-1 -- --physics-hz 100   # pin the integrator
#   ./scripts/step-timing.sh mosque -- --label rig-container  # name the machine in the CSV
#   ./scripts/step-timing.sh mosque -- --camera-rig ~/Desktop/work/wingfin/data/cams.txt
#   GPU=integrated ./scripts/step-timing.sh        # force the built-in graphics
#
# The default is rows 1-6; row 7 opens a window, so it is one --rows 7 away. Rows 1 and 2 differ
# only in who drives -- replay decides nothing, idm drives -- and row 3 puts a model behind
# --policy-url in the same seat, skipping itself with a reason when nothing is listening.
#
# Unflagged, every offscreen row draws one 320x180 camera this tool invented, and that is most of
# what a step costs. --camera-rig takes the same spec sensor-survey.sh takes and mounts the
# vehicle's own cameras instead, so the sweep prices the car being built rather than a stand-in.
#
# Every run prints a table and writes its own CSV into <workspace>/reports/, stamped with the
# moment it started. Nothing is appended to and nothing is overwritten, so two runs -- or two
# machines -- leave two files that concatenate into one spreadsheet.
#
# Why a script rather than a command, exactly as for drive.sh: MetaDrive runs on its own
# interpreter (3.8 / numpy 1.24, against this repo's 3.10 / numpy 2.2), and which GPU renders
# is settled by the GLX loader before python starts.
#
# Read from .env, all optional:
#   METADRIVE_PYTHON   the MetaDrive checkout's interpreter
#   GPU                auto (default), nvidia, or integrated
#   STEP_TIMING_LABEL  names the machine in the CSV; the hostname otherwise, which in a
#                      container is a random id. A --label after -- wins over it.
#
# STEP_HZ is deliberately *not* read here. This sweep drives every dataset the workspace
# holds, each at the rate it was written at -- picking one would be the opposite of the
# comparison. Pass --step-hz after -- to override that for every dataset at once.

# shellcheck source=scripts/_common.sh
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

POSITIONAL=""
PASSTHROUGH=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --) shift; PASSTHROUGH=("$@"); break ;;
        -h|--help) sed -n '2,38p' "$SELF" | sed 's/^#\s\?//'; exit 0 ;;
        -*) die "unknown option: $1
  This script takes only a workspace. To pass $1 to step_timing.py, put it after --:
    ./scripts/step-timing.sh ${POSITIONAL:-<workspace>} -- $1" ;;
        *) POSITIONAL="$1" ;;
    esac
    shift
done

resolve_workspace "$POSITIONAL"

MD_PY="${METADRIVE_PYTHON:-/home/keith/Desktop/work/wingfin/metadrive/.venv/bin/python}"
[[ -x "$MD_PY" ]] || die "no MetaDrive interpreter at $MD_PY.
  It is a different interpreter from this repo's on purpose -- MetaDrive is 3.8 / numpy 1.24.
  Set METADRIVE_PYTHON in .env if the checkout lives somewhere else."

# Every rate, not one: the comparison is between them.
DATASETS=()
while IFS= read -r line; do
    DATASETS+=("$line")
done < <(list_datasets)

# The sweep drives, so it needs a recorded car for the same reason drive.sh does:
# ScenarioMapManager.reset calls get_sdc_track() unconditionally and a map-only dataset dies on
# KeyError('None') deep inside MetaDrive, which reads like a broken dataset and is not one.
for DATASET in "${DATASETS[@]}"; do
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
done

select_gpu

ARGS=(tools/step_timing.py "${DATASETS[@]}")
# Last wins in argparse, so anything after -- overrides what this script chose.
ARGS+=(${PASSTHROUGH[@]+"${PASSTHROUGH[@]}"})

note "workspace  $WS"
note "datasets   $(printf '%s ' "${DATASETS[@]#"$WS/"}")"
note "python     $MD_PY"
if [[ $USE_NVIDIA -eq 1 ]]; then
    note "gpu        discrete, via PRIME offload (GPU=$GPU)"
else
    note "gpu        whatever the display is on (GPU=$GPU)"
fi
printf '\n'

if [[ $USE_NVIDIA -eq 1 ]]; then
    exec env __NV_PRIME_RENDER_OFFLOAD=1 __GLX_VENDOR_LIBRARY_NAME=nvidia \
        "$MD_PY" "${ARGS[@]}"
fi
exec "$MD_PY" "${ARGS[@]}"
