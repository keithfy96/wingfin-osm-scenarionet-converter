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

# Does the image on this machine carry what docker/Dockerfile says it should?
#
# Nothing else asks. `docker compose run` reuses whatever holds the `wingfin-sim` tag and never
# rebuilds; a `docker compose build` run before the `git pull` that changed the Dockerfile rebuilds
# the OLD recipe out of BuildKit's cache in seconds. Both look exactly like success, and the first
# thing that notices is a refusal four minutes into a terrain build -- which is how a rig lost a
# morning to `--model-checkpoint needs torch` against an image built before `--group gpu --group
# model` was added to the sync line.
#
# The Dockerfile's LABEL is the copy that can be read back without starting a container. Not
# `docker history`, which is unreliable under the containerd image store, and above all not image
# size: the classic overlay2 store prints one SIZE, containerd prints CONTENT SIZE and DISK USAGE,
# and those are different measurements. `docker image inspect` reads labels the same way on both.
#
# A note and never a die: a sweep, or any --no-model drive, runs perfectly well on the older image.
# An image with no label at all is stale by construction -- it predates the label, which is the same
# build that predates the groups. No image yet says nothing, because compose is about to build one.
# What this does NOT catch, and cannot: a version bump inside a group whose name did not change.
check_image_groups() {
    local want have missing=()
    # The uv sync line only -- awk drops comment lines, which now discuss --group themselves.
    want=$(awk '/^[^#]*uv sync/' docker/Dockerfile | grep -o -- '--group [a-z]*' | awk '{print $2}')
    [[ -n "$want" ]] || return 0
    [[ -n "$(docker image inspect wingfin-sim --format '{{.Id}}' 2>/dev/null)" ]] || return 0
    have=$(docker image inspect wingfin-sim \
        --format '{{index .Config.Labels "wingfin.groups"}}' 2>/dev/null)

    local group
    for group in $want; do
        [[ " $have " == *" $group "* ]] || missing+=("$group")
    done
    [[ ${#missing[@]} -gt 0 ]] || return 0

    note "the wingfin-sim image is missing the ${missing[*]} dependency $(
        [[ ${#missing[@]} -eq 1 ]] && echo group || echo groups). Rebuild it:"
    note "  docker compose build"
    note "until then anything needing those packages refuses -- they are not in the image."
}
check_image_groups

exec docker compose run --rm ${RUN_FLAGS[@]+"${RUN_FLAGS[@]}"} sim "$@"
