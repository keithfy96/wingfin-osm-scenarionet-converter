# Shared setup for the stage runner scripts. Sourced by them, never executed.
#
# Everything here is about getting the two things every stage command needs -- the
# workspace and the config -- from one place, so switching workspaces is one edit in
# .env rather than six edits on a command line.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# .env is per-machine and gitignored; .env.example is the committed template.
if [[ -f .env ]]; then
    set -a
    # shellcheck disable=SC1091
    source .env
    set +a
fi

CONFIG="${CONFIG:-config/default.yaml}"

# Built as an array so the optional -v never has to be an empty word.
CLI=(uv run osm-scenario)
if [[ "${VERBOSE:-0}" == "1" ]]; then
    CLI+=(-v)
fi

die() {
    printf '\n  %s\n\n' "$*" >&2
    exit 1
}

note() {
    printf '  %s\n' "$*"
}

banner() {
    printf '\n\033[1m== %s\033[0m\n' "$*"
}

# Resolve $1 (or $WORKSPACE) into $WS. A bare name means workspaces/<name>; anything
# with a slash in it is taken as a path exactly as written.
resolve_workspace() {
    local requested="${1:-${WORKSPACE:-}}"
    if [[ -z "$requested" ]]; then
        die "no workspace: set WORKSPACE in .env (copy .env.example) or pass one as an argument."
    fi
    if [[ "$requested" == */* ]]; then
        WS="${requested%/}"
    else
        WS="workspaces/$requested"
    fi
    if [[ ! -d "$WS" ]]; then
        local have
        have="$(find workspaces -mindepth 1 -maxdepth 1 -type d -printf '%f ' 2>/dev/null)"
        die "no such workspace: $WS (have: ${have:-none})"
    fi
    [[ -f "$WS/source/manifest.json" ]] || die "$WS has no source/manifest.json -- it is not a workspace."
    [[ -f "$WS/source/map.osm" ]] || die "$WS has no source/map.osm."
}

# Read a dotted key out of the workspace manifest. Prints nothing and returns 1 when
# the key is absent, so callers can distinguish missing from empty.
manifest_get() {
    python3 - "$WS/source/manifest.json" "$1" <<'PY'
import json
import sys

path, key = sys.argv[1], sys.argv[2]
value = json.load(open(path, encoding="utf-8"))
for part in key.split("."):
    if not isinstance(value, dict) or part not in value:
        sys.exit(1)
    value = value[part]
print(value)
PY
}

# Run one stage, timing it, and stop the whole run if it fails. The command's output is
# also kept in $STAGE_LOG so a caller can explain a known failure rather than leaving a
# bare non-zero exit.
STAGE_LOG="$(mktemp)"
trap 'rm -f "$STAGE_LOG"' EXIT

run_stage() {
    local title="$1"
    shift
    banner "$title"
    local started=$SECONDS
    local status=0
    "$@" 2>&1 | tee "$STAGE_LOG" || status=$?
    if [[ $status -ne 0 ]]; then
        return $status
    fi
    printf '  \033[2m(%ds)\033[0m\n' "$((SECONDS - started))"
}
