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
# Nothing else asks. `docker compose run` reuses whatever holds the `metadrive-wingfin-sim` tag
# and never
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
# No image yet says nothing, because compose is about to build one.
#
# **An image with no label says nothing either, and that is the point.** Absent means unknown, not
# missing: the label was added after the groups were, so an image built in between carries all three
# groups and no label to prove it. Reading absent as "all three missing" is exactly what happened --
# the rig was told it was missing sim, gpu and model, and the same run then loaded torch, TensorRT
# and the checkpoint. A check that fires on a working image teaches people to ignore it, which costs
# more than the check is worth.
#
# What this does NOT catch, and cannot: a version bump inside a group whose name did not change, or
# an image old enough to predate the label.
check_image_groups() {
    local want have missing=()
    # The uv sync line only -- awk drops comment lines, which now discuss --group themselves.
    want=$(awk '/^[^#]*uv sync/' docker/Dockerfile | grep -o -- '--group [a-z]*' | awk '{print $2}')
    [[ -n "$want" ]] || return 0
    [[ -n "$(docker image inspect metadrive-wingfin-sim \
        --format '{{.Id}}' 2>/dev/null)" ]] || return 0
    have=$(docker image inspect metadrive-wingfin-sim \
        --format '{{index .Config.Labels "wingfin.groups"}}' 2>/dev/null)
    # Unlabelled: an image from before the label existed. Nothing can be said about it from here,
    # so nothing is said. See the header.
    [[ -n "$have" ]] || return 0

    local group
    for group in $want; do
        [[ " $have " == *" $group "* ]] || missing+=("$group")
    done
    [[ ${#missing[@]} -gt 0 ]] || return 0

    note "the metadrive-wingfin-sim image is missing the ${missing[*]} dependency $(
        [[ ${#missing[@]} -eq 1 ]] && echo group || echo groups). Rebuild it:"
    note "  docker compose build"
    note "until then anything needing those packages refuses -- they are not in the image."
}
check_image_groups

# Was the image built before the last change to the lock it was built from?
#
# **This is the half `check_image_groups` above cannot see, and it is not hypothetical.** That
# check compares group *names*, so it is blind to a package ADDED to a group that already
# existed -- and on 2026-09-04 `av` reached the `ros` group against an image that carried `ros`
# and not `av`. The label matched, the check said nothing, and the first thing that noticed was
# `--ros-camera needs \`av\`` four minutes into a terrain build, after the rig had loaded, the
# model had resolved and the policy had connected. Its own comment predicted exactly this.
#
# Commit dates and not mtimes: a fresh `git clone` stamps every file with the checkout time, so
# an mtime comparison on a rig says "the lock is newer than the image" about a lock nobody
# touched. `%cI` is stable across clones. An uncommitted lock is caught separately, because a
# lock edited and not yet committed is the same staleness with no commit date to read.
#
# A note and never a die, for `check_image_groups`' reason: a --no-model drive or a sweep runs
# perfectly well on an image that predates a dependency it does not use, and a check that fires
# on a working image teaches people to ignore it.
check_image_lock() {
    local built lock_changed built_s lock_s
    built=$(docker image inspect metadrive-wingfin-sim \
        --format '{{.Created}}' 2>/dev/null) || return 0
    [[ -n "$built" ]] || return 0

    if [[ -n "$(git status --porcelain uv.lock 2>/dev/null)" ]]; then
        note "uv.lock has uncommitted changes; the metadrive-wingfin-sim image cannot have them."
        note "  docker compose build"
        return 0
    fi

    lock_changed=$(git log -1 --format=%cI -- uv.lock 2>/dev/null) || return 0
    [[ -n "$lock_changed" ]] || return 0

    built_s=$(date -d "$built" +%s 2>/dev/null) || return 0
    lock_s=$(date -d "$lock_changed" +%s 2>/dev/null) || return 0
    [[ "$built_s" -lt "$lock_s" ]] || return 0

    note "the metadrive-wingfin-sim image was built $(date -d "$built" '+%Y-%m-%d %H:%M'), before"
    note "uv.lock last changed $(date -d "$lock_changed" '+%Y-%m-%d %H:%M'). A package added to a"
    note "group the image already has is invisible to the group check above. Rebuild:"
    note "  docker compose build"
}
check_image_lock

exec docker compose run --rm ${RUN_FLAGS[@]+"${RUN_FLAGS[@]}"} sim "$@"
