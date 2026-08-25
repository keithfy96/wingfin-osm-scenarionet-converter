#!/usr/bin/env bash
#
# Run the AV3 model beside a recorded drive and check every input conversion.
#
#   ./scripts/av3-probe.sh junction-1 -- --step-hz 100 --decision-hz 20
#   ./scripts/av3-probe.sh junction-1 -- --no-model          # conversions 2/4/5, in seconds
#   ./scripts/av3-probe.sh junction-1 -- --decisions 0       # the whole drive, ~1 s a decision
#
# Stage 9, Phase C.3's stop point. Nothing steers: the ego is replayed from the tape, and the
# model predicts beside it so its five input conversions can be checked while the answer is
# still known. None of them raises when it is wrong -- a mirrored route or a swapped camera
# pair gives a model that runs and drives into the oncoming carriageway -- so this comes
# before anything is mounted on the car.
#
# It runs on THIS repo's interpreter rather than METADRIVE_PYTHON, for the reason
# model-probe.sh gives: torch 2.8 has no 3.8 wheel and does not need one, MetaDrive running on
# 3.10. It builds an offscreen GL context beside a CUDA one, so it goes through exec_with_gpu
# -- the two on different cards is cudaErrorUnknown(999) with nothing saying "wrong card".
#
#   uv sync --group sim --group gpu --group model
#
# Read from .env, all optional:
#   MODEL_CHECKPOINT  the .ep to load. The fork checkout's copy otherwise.
#   WORKSPACE         which workspace to drive.
#   STEP_HZ           which of its datasets, unless --step-hz is passed after --.
#   GPU               auto | nvidia | integrated.

# shellcheck source=scripts/_common.sh
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

POSITIONAL=""
PASSTHROUGH=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --) shift; PASSTHROUGH=("$@"); break ;;
        -h|--help) sed -n '2,25p' "$SELF" | sed 's/^#\s\?//'; exit 0 ;;
        -*) die "unknown option: $1
  This script takes only a workspace. To pass $1 to av3_probe.py, put it after --:
    ./scripts/av3-probe.sh ${POSITIONAL:-<workspace>} -- $1" ;;
        *) POSITIONAL="$1" ;;
    esac
    shift
done

resolve_workspace "$POSITIONAL"
resolve_dataset ${PASSTHROUGH[@]+"${PASSTHROUGH[@]}"}
select_gpu

ARGS=(tools/av3_probe.py "$DATASET")
if [[ -n "${MODEL_CHECKPOINT:-}" ]]; then
    ARGS+=(--checkpoint "$MODEL_CHECKPOINT")
fi
# Last wins in argparse, so anything repeated after -- overrides what this script chose.
ARGS+=(${PASSTHROUGH[@]+"${PASSTHROUGH[@]}"})

note "workspace  $WS"
note "dataset    ${DATASET#"$WS/"}"
note "gpu        $GPU_NOTE"
note "checkpoint ${MODEL_CHECKPOINT:-from the fork checkout}"
printf '\n'

exec_with_gpu uv run --group sim --group gpu --group model python "${ARGS[@]}"
