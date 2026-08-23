#!/usr/bin/env bash
#
# Measure what MetaDrive's car does when you hold a pedal, and write the table.
#
#   ./scripts/pedal-sweep.sh                     # workspace from .env
#   ./scripts/pedal-sweep.sh junction-1          # override the workspace for this run
#   ./scripts/pedal-sweep.sh -- --no-write       # measure and print, write nothing
#   ./scripts/pedal-sweep.sh -- --speed-step 0.5 --pedal-step 0.025    # a finer grid
#   ./scripts/pedal-sweep.sh -- --out /tmp/other-car.json
#
# Writes calibration/metadrive-pedal-map.json, which
# `examples/openpilot_server.py --longitudinal table` reads. Takes about ten seconds.
#
# The openpilot bridge plans in m/s^2 and converts to pedals with a table measured on a
# Tesla in CARLA, whose zero-throttle drag is -1.582 m/s^2 against MetaDrive's -0.364 -- so
# every request to slow down gently comes back as throttle. This is that measurement made
# against the car actually being driven.
#
# Why a script rather than a bare command, as for drive.sh: MetaDrive runs on its own
# interpreter. No GPU and no display are needed -- the sweep renders nothing -- so there is
# no select_gpu here, which is the one way it differs from sensor-survey.sh.
#
# Read from .env, all optional:
#   METADRIVE_PYTHON  the MetaDrive checkout's interpreter
#   STEP_HZ           picks which of the workspace's datasets is opened. The table itself is
#                     the same at any rate -- the forces do not change -- and the rate it was
#                     measured at is recorded in the file.

# shellcheck source=scripts/_common.sh
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

POSITIONAL=""
PASSTHROUGH=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --) shift; PASSTHROUGH=("$@"); break ;;
        -h|--help) sed -n '2,28p' "$SELF" | sed 's/^#\s\?//'; exit 0 ;;
        -*) die "unknown option: $1
  This script takes only a workspace. To pass $1 to pedal_sweep.py, put it after --:
    ./scripts/pedal-sweep.sh ${POSITIONAL:-<workspace>} -- $1" ;;
        *) POSITIONAL="$1" ;;
    esac
    shift
done

resolve_workspace "$POSITIONAL"

MD_PY="${METADRIVE_PYTHON:-/home/keith/Desktop/work/wingfin/metadrive/.venv/bin/python}"
[[ -x "$MD_PY" ]] || die "no MetaDrive interpreter at $MD_PY.
  Set METADRIVE_PYTHON in .env if the checkout lives somewhere else."

resolve_dataset ${PASSTHROUGH[@]+"${PASSTHROUGH[@]}"}

# The sweep drives, so it needs a recorded car for the reason drive.sh gives: MetaDrive's
# ScenarioMapManager.reset calls get_sdc_track() unconditionally and a map-only dataset dies
# with KeyError('None'). It never *follows* that car -- it holds zero steering and its own
# pedals -- but the scenario still has to have one.
if ! "$MD_PY" - "$DATASET" <<'PY'
import pickle
import sys

path = sys.argv[1] + "/dataset_summary.pkl"
with open(path, "rb") as handle:
    summary = pickle.load(handle)
sys.exit(0 if summary and all(entry.get("sdc_id") for entry in summary.values()) else 1)
PY
then
    die "$DATASET is map-only -- it has no recorded car, so there is nothing to spawn.
  Pick routes in $WS/inspection/stage-6-route-builder.html, save routes/routes.json, then:
    uv run osm-scenario convert -w $WS --config $CONFIG \\
      --routes $WS/routes/routes.json"
fi

ARGS=(tools/pedal_sweep.py "$DATASET")
if [[ -n "${STEP_HZ:-}" ]]; then
    ARGS+=(--step-hz "$STEP_HZ")
fi
# Last wins in argparse, so anything repeated after -- overrides what this script chose.
ARGS+=(${PASSTHROUGH[@]+"${PASSTHROUGH[@]}"})

note "workspace  $WS"
note "dataset    ${DATASET#"$WS/"}"
note "python     $MD_PY"
printf '\n'

exec "$MD_PY" "${ARGS[@]}"
