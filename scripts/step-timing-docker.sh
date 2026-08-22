#!/usr/bin/env bash
#
# The step-timing sweep, in the container, so two machines' CSVs mean something side by side.
#
#   ./scripts/step-timing-docker.sh                       # workspace from .env, every rate
#   ./scripts/step-timing-docker.sh mosque                # override the workspace for this run
#   ./scripts/step-timing-docker.sh mosque -- --rows 6    # no graphics: the floor
#   ./scripts/step-timing-docker.sh mosque -- --rows 2,6  # what the camera costs
#   ./scripts/step-timing-docker.sh mosque -- --camera-rig rigs/cams.txt
#   ./scripts/step-timing-docker.sh mosque -- --rate-sets scripts/rate-sets.csv
#   STEP_TIMING_LABEL=desktop ./scripts/step-timing-docker.sh mosque
#
# Everything after -- goes to step_timing.py exactly as it does for step-timing.sh, which this
# runs inside the container -- there is no second copy of the sweep. --camera-rig rigs/cams.txt
# and --rate-sets scripts/rate-sets.csv are the same strings in there too, the repo being mounted
# at /work. RIG_DIR is for a spec kept outside it.
#
# --rate-sets is how to run a batch in here: a file of whole configurations, one a row, driven
# one after another in one process into one CSV with a rate_set column. One process is what makes
# them comparable -- prime is paid once and the machine columns are identical by construction.
#
# Why a container at all. Every row of the CSV carries the host, CPU, GPU, GL ceiling and the
# python / numpy / MetaDrive versions, precisely so two machines' files concatenate into one
# spreadsheet -- and that only means something if both machines are provably running the same
# simulator. `uv.lock` plus a MetaDrive commit pin is provable; reproducing a venv by hand on
# each box is not.
#
# Three things about the container that are not obvious:
#
#   * It renders on the real GPU with no X server, through EGL. The image ships the glvnd ICD
#     manifest the NVIDIA container toolkit does not install -- without that file every context
#     silently lands on llvmpipe, which does not fail, it just runs the benchmark on the CPU.
#     Check `gl_renderer` in the CSV: it must name the card, never Mesa or llvmpipe.
#   * There is one interpreter in there, not two. MetaDrive runs on 3.10 alongside the converter,
#     so run-stages-*.sh and pytest work in the container as well as this does.
#   * Row 7 does not. It opens a window and there is no display.
#
# The commands, the .env keys and what to do when it breaks: docs/container.md.
#
# Read from .env, all optional:
#   WORKSPACE          which workspace, when none is given as an argument
#   RIG_DIR            host directory holding the camera-rig spec; mounted read-only at /rig
#   STEP_TIMING_LABEL  names the machine in the CSV; the hostname otherwise. Set it -- a
#                      container's hostname is a random id.

set -euo pipefail

SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

die() {
    printf '\n  %s\n\n' "$*" >&2
    exit 1
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    sed -n '2,44p' "$SELF" | sed 's/^#\s\?//'
    exit 0
fi

cd "$REPO_ROOT"

command -v docker >/dev/null 2>&1 || die "docker is not on PATH."
docker compose version >/dev/null 2>&1 || die "docker compose is not available (needs the v2+ plugin)."

# The shell does not export these, and compose needs them to run the container as the caller --
# without which every report written into the mounted workspace is owned by root.
DOCKER_UID="$(id -u)"
DOCKER_GID="$(id -g)"
export DOCKER_UID DOCKER_GID
export STEP_TIMING_LABEL="${STEP_TIMING_LABEL:-$(hostname)}"

exec docker compose run --rm sim scripts/step-timing.sh "$@"
