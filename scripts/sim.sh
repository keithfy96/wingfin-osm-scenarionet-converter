#!/usr/bin/env bash
#
# Run anything inside the `sim` container -- MetaDrive, the converter and the AV3 model, on one
# 3.10 interpreter. Every container command in docs/running-a-test.md and docs/container.md goes
# through here.
#
#   ./sim.sh id                                   # who the container thinks you are
#   ./sim.sh bash                                 # a shell in there; exit to leave
#   ./sim.sh scripts/drive.sh junction-1 -- --render offscreen
#   ./sim.sh python3 examples/openpilot_server.py --backend stub --port 8643
#   ./sim.sh --no-model scripts/drive.sh junction-1 -- ...   # leave the checkpoint out
#
# Why this exists rather than `docker compose run --rm sim ...` typed out: compose.yaml runs the
# container as ${DOCKER_UID:-1000}:${DOCKER_GID:-1000}, and **a shell does not export those**. Run
# compose directly and the container is uid 1000 whoever you are -- which on a machine where you
# are not 1000 means every report written into the mounted workspace has the wrong owner, and
# `import torch_tensorrt` dies with `KeyError: 'getpwuid(): uid not found'` the moment a
# checkpoint loads, because the /etc/passwd mount exists so it can look your *name* up. Silent
# until it is not. This script reads them from `id -u` / `id -g`, so they cannot go stale against
# the account actually running -- which is also why they do not belong in .env.
#
# It is the same three lines step-timing-docker.sh and container-check.sh already carry; this
# generalises them so nothing else has to.
#
# Options, before the command:
#   --no-model    run with MODEL_CHECKPOINT empty, so the drive does NOT load the AV3 model.
#                 compose.yaml sets that variable for you, so a drive loads the model unless
#                 told otherwise -- a quarter of an hour instead of a minute. Setting it empty
#                 in your own shell will not do it: ${VAR:-default} treats empty as unset, so it
#                 has to be passed on the run itself, which is what this does.
#
# Read from .env: everything compose.yaml reads -- MODEL_DIR, RIG_DIR, STEP_TIMING_LABEL.
# METADRIVE_PYTHON must stay commented there; the image sets its own.

# shellcheck source=scripts/_common.sh
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

RUN_FLAGS=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        -h|--help) sed -n '2,33p' "$SELF" | sed 's/^#\s\?//'; exit 0 ;;
        --no-model) RUN_FLAGS+=(-e MODEL_CHECKPOINT=); shift ;;
        *) break ;;
    esac
done

[[ $# -gt 0 ]] || die "nothing to run.
  ./sim.sh <command>        e.g. ./sim.sh bash, ./sim.sh scripts/drive.sh mosque
  ./sim.sh --help"

cd "$REPO_ROOT"

command -v docker >/dev/null 2>&1 || die "docker is not on PATH."
docker compose version >/dev/null 2>&1 || die "docker compose is not available (needs the v2+ plugin)."

# The whole reason this script exists. See the header.
DOCKER_UID="$(id -u)"
DOCKER_GID="$(id -g)"
export DOCKER_UID DOCKER_GID

# compose.yaml sets `tty: true` for interactive use, and `docker compose run` then tries to
# allocate one. With no terminal on stdin -- backgrounded with &, piped into grep, run from cron
# -- that fails or mangles the output, which is how a documented two-terminal command turns into
# "it works when I type it and not when I script it". -T asks for no TTY, and is exactly wrong
# when there IS one: `./sim.sh bash` needs the terminal. So it follows stdin rather than a flag.
[[ -t 0 ]] || RUN_FLAGS+=(-T)

exec docker compose run --rm ${RUN_FLAGS[@]+"${RUN_FLAGS[@]}"} sim "$@"
