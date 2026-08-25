#!/usr/bin/env bash
#
# Watch a drive that `tools/drive.py --export-drive` wrote, in a 3D window on this machine.
#
#   ./scripts/watch-drive.sh ../workspaces/junction-1/drives/rig
#   ./scripts/watch-drive.sh <dir> -- --render 2D      # everything after -- goes to drive.py
#   GPU=integrated ./scripts/watch-drive.sh <dir>      # force the built-in graphics
#
# This is the other half of --export-drive, and it exists because the machine that produces
# the interesting drives has no screen. The rig runs the model headless and writes the drive
# out; this opens it here. Nothing re-runs: the ego is placed on the positions it really took,
# which is what --agent-policy replay -- drive.py's default -- does.
#
# Why a script of its own rather than a flag on drive.sh: drive.sh takes a *workspace*, and
# `resolve_workspace` requires source/manifest.json and source/map.osm (`_common.sh:66-67`).
# An exported drive is a bare dataset directory with neither. What it shares with drive.sh is
# the only reason drive.sh exists -- MetaDrive's own 3.8 interpreter, and a GPU chosen by the
# GLX loader before python starts -- so both come from _common.sh.
#
# The exported dataset's timestamps are MetaDrive's, not the drive's: its converter stamps
# 0.1 s a frame whatever rate the drive ran at. That is why no --step-hz is passed here --
# MetaDrive's own default 10 Hz is what the file claims to be, so the two agree and drive.py's
# rate check passes. The picture is right either way; one recorded frame is one env.step.
#
# Read from .env, all optional:
#   METADRIVE_PYTHON  the MetaDrive checkout's interpreter
#   GPU               auto (default), nvidia, or integrated

# Where the user was standing, captured before _common.sh cds to the repo root. A drive
# directory is an ordinary path rather than a workspace name, and `./watch-drive.sh
# ../workspaces/...` from inside `scripts/` -- which is where these are run from -- stops
# meaning anything once we have left that directory.
CALLER_PWD="$PWD"

# shellcheck source=scripts/_common.sh
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

POSITIONAL=""
PASSTHROUGH=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --) shift; PASSTHROUGH=("$@"); break ;;
        -h|--help) sed -n '2,27p' "$SELF" | sed 's/^#\s\?//'; exit 0 ;;
        -*) die "unknown option: $1
  This script takes only a directory. To pass $1 to drive.py, put it after --:
    ./scripts/watch-drive.sh ${POSITIONAL:-<dir>} -- $1" ;;
        *) POSITIONAL="$1" ;;
    esac
    shift
done

[[ -n "$POSITIONAL" ]] || die "which drive? Give the directory --export-drive wrote:
    ./scripts/watch-drive.sh ../workspaces/<workspace>/drives/<label>"
DRIVE="${POSITIONAL%/}"
# Taken as the user meant it first, and as a repo-relative path second -- which is the shape
# drive.py itself prints when it finishes an --export-drive, that path being relative to the
# repo root it runs from.
if [[ "$DRIVE" != /* ]]; then
    if [[ -d "$CALLER_PWD/$DRIVE" ]]; then
        DRIVE="$CALLER_PWD/$DRIVE"
    elif [[ ! -d "$DRIVE" ]]; then
        die "no such directory: $DRIVE
  Looked in $CALLER_PWD and in $REPO_ROOT."
    fi
fi
[[ -d "$DRIVE" ]] || die "no such directory: $DRIVE"
# Named rather than left to MetaDrive: a directory without it dies inside read_dataset_summary
# with a FileNotFoundError several frames deep, which reads as a broken install.
[[ -f "$DRIVE/dataset_summary.pkl" ]] || die "$DRIVE holds no dataset_summary.pkl, so it is not
  a dataset. --export-drive writes one; a directory of anything else is not watchable here."

MD_PY="${METADRIVE_PYTHON:-/home/keith/Desktop/work/wingfin/metadrive/.venv/bin/python}"
[[ -x "$MD_PY" ]] || die "no MetaDrive interpreter at $MD_PY.
  It is a different interpreter from this repo's on purpose -- MetaDrive is 3.8 / numpy 1.24.
  Set METADRIVE_PYTHON in .env if the checkout lives somewhere else."

# See drive.sh: which card renders is settled by the GLX loader before python starts, so it
# cannot be a flag.
select_gpu

# Last wins in argparse, so anything repeated after -- overrides what this script chose.
ARGS=(tools/drive.py "$DRIVE" --render 3D)
ARGS+=(${PASSTHROUGH[@]+"${PASSTHROUGH[@]}"})

note "drive      $DRIVE"
note "python     $MD_PY"
note "gpu        $GPU_NOTE"
printf '\n'

exec_with_gpu "$MD_PY" "${ARGS[@]}"
