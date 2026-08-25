#!/usr/bin/env bash
#
# Load the AV3 checkpoint and say whether this machine can run it at all.
#
#   ./scripts/model-probe.sh                          # does the engine deserialise, and what does it cost
#   ./scripts/model-probe.sh -- --with-simulator      # the same, with MetaDrive already holding the card
#   ./scripts/model-probe.sh junction-1 -- --with-simulator
#   ./scripts/model-probe.sh -- --checkpoint /some/other.ep --passes 200
#
# Stage 9, Phase C.1. It answers two questions and nothing else: does a TensorRT engine
# compiled on another machine deserialise on this card, and does it fit beside the simulator.
# It does not drive and writes no file.
#
# Two ways this differs from the other scripts here, both deliberate:
#
#   * it runs on THIS repo's interpreter, not METADRIVE_PYTHON. torch 2.8 has no 3.8 wheel,
#     and it does not need one -- MetaDrive resolves and runs on 3.10 (see CLAUDE.md), so
#     `uv sync --group sim --group gpu --group model` puts the simulator and the model in one
#     environment. Every other tools/ file is still parsed with the 3.8 interpreter before
#     being believed; this one cannot be and does not need to be.
#   * the plain run does NOT select_gpu. CUDA finds the discrete card by itself; the PRIME
#     variables exist for CUDA-GL interop and there is no GL context here. --with-simulator
#     builds one, so that path does go through exec_with_gpu -- a GL context on the iGPU
#     beside a CUDA context on the RTX is cudaErrorUnknown(999) at construction, with nothing
#     in the message saying "wrong card".
#
# Read from .env, all optional:
#   MODEL_CHECKPOINT  the .ep to load. The fork checkout's copy otherwise.
#   WORKSPACE         which workspace --with-simulator builds its env over.
#   STEP_HZ           which of that workspace's datasets, for the same.
#   GPU               auto | nvidia | integrated, for --with-simulator only.

# shellcheck source=scripts/_common.sh
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

POSITIONAL=""
PASSTHROUGH=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --) shift; PASSTHROUGH=("$@"); break ;;
        -h|--help) sed -n '2,31p' "$SELF" | sed 's/^#\s\?//'; exit 0 ;;
        -*) die "unknown option: $1
  This script takes only a workspace. To pass $1 to model_probe.py, put it after --:
    ./scripts/model-probe.sh ${POSITIONAL:-<workspace>} -- $1" ;;
        *) POSITIONAL="$1" ;;
    esac
    shift
done

ARGS=(tools/model_probe.py)
if [[ -n "${MODEL_CHECKPOINT:-}" ]]; then
    ARGS+=(--checkpoint "$MODEL_CHECKPOINT")
fi

# The workspace is resolved only for --with-simulator, because without it there is no env to
# build and demanding a workspace would refuse a run that needs none.
WANTS_SIMULATOR=0
for argument in ${PASSTHROUGH[@]+"${PASSTHROUGH[@]}"}; do
    if [[ "$argument" == "--with-simulator" ]]; then
        WANTS_SIMULATOR=1
    fi
done

if [[ $WANTS_SIMULATOR -eq 1 ]]; then
    resolve_workspace "$POSITIONAL"
    resolve_dataset ${PASSTHROUGH[@]+"${PASSTHROUGH[@]}"}
    ARGS+=(--dataset "$DATASET")
    select_gpu
    note "workspace  $WS"
    note "dataset    ${DATASET#"$WS/"}"
    note "gpu        $GPU_NOTE"
elif [[ -n "$POSITIONAL" ]]; then
    die "a workspace means nothing without --with-simulator: the plain probe builds no
  simulator and opens no dataset. Either drop it, or:
    ./scripts/model-probe.sh $POSITIONAL -- --with-simulator"
fi

# Last wins in argparse, so anything repeated after -- overrides what this script chose.
ARGS+=(${PASSTHROUGH[@]+"${PASSTHROUGH[@]}"})

note "checkpoint ${MODEL_CHECKPOINT:-from the fork checkout}"
printf '\n'

if [[ $WANTS_SIMULATOR -eq 1 ]]; then
    exec_with_gpu uv run --group sim --group model python "${ARGS[@]}"
fi
exec uv run --group model python "${ARGS[@]}"
