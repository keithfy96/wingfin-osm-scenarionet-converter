#!/usr/bin/env bash
#
# The openpilot bridge container -- the "controller" of tier 4 in docs/running-a-test.md.
# A second image, separate from `sim`, because the openpilot fork is Python 3.8 and torch has
# no 3.8 wheel. `docker/openpilot/README.md` has the why; this script has the commands.
#
#   ./scripts/bridge.sh status        # is it up? run this before a tier 4 drive
#   ./scripts/bridge.sh build         # clone the fork if needed, then build the image
#   ./scripts/bridge.sh start         # run it on port 5558
#   ./scripts/bridge.sh stop          # remove the container; the image stays
#   ./scripts/bridge.sh logs          # what it has said, following
#   ./scripts/bridge.sh save <file>   # write the image to a .tar.gz to carry to another machine
#
# Read from .env, all optional:
#   BRIDGE_PORT   the TCP port the bridge listens on; 5558 when unset, which is what
#                 examples/openpilot_server.py assumes on both sides
#   BRIDGE_IMAGE  the image tag; metadrive-wingfin-openpilot:prod when unset
#   BRIDGE_NAME   the container name; metadrive-wingfin-openpilot-bridge when unset
#
# `build` needs nothing but this repo. The openpilot fork is vendored at
# docker/openpilot/deps/openpilot -- 309 MB of tracked files -- so a fresh clone can build the
# bridge with no access to the private zapetaai org, which is access nobody could grant a rig
# remotely. `save` is still there for a machine with no network: it writes the built image to a
# .tar.gz for `docker load` on the far side, 5.5 GB against a 309 MB clone.
#
# Budget half an hour for the first build on a machine with nothing cached. Three parts: the apt
# + pyenv + poetry base, which dominates and is the part not measured here; the scons compile of
# cereal, boardd and the two MPC libraries; and the acados lateral-MPC solver prebuild, which took
# under 5 s. Later builds that touch only bridge/ take seconds -- the Dockerfile copies it last on
# purpose.

# shellcheck source=scripts/_common.sh
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

BRIDGE_PORT="${BRIDGE_PORT:-5558}"
BRIDGE_IMAGE="${BRIDGE_IMAGE:-metadrive-wingfin-openpilot:prod}"
BRIDGE_NAME="${BRIDGE_NAME:-metadrive-wingfin-openpilot-bridge}"
CONTEXT="$REPO_ROOT/docker/openpilot"

usage() { sed -n '2,30p' "$SELF" | sed 's/^#\s\?//'; }

# The container's own status word, or empty for "no container of that name at all". Docker
# prints nothing and exits 0 for a name that does not exist, which is why this is a filter
# rather than an inspect -- inspect exits 1 and would trip `set -e`.
container_status() {
    docker ps -a --filter "name=^/${BRIDGE_NAME}$" --format '{{.Status}}'
}

image_exists() {
    [[ -n "$(docker images -q "$BRIDGE_IMAGE" 2>/dev/null)" ]]
}

cmd_status() {
    local status
    status="$(container_status)"
    note "image      $BRIDGE_IMAGE"
    if image_exists; then
        note "           present, $(docker images --format '{{.Size}}' "$BRIDGE_IMAGE" | head -1)"
    else
        note "           NOT BUILT -- ./scripts/bridge.sh build, or load a copy"
    fi
    note "container  $BRIDGE_NAME"
    case "$status" in
        Up*)
            note "           $status"
            printf '\n'
            note "ready. Do NOT run 'start' again -- the name is taken by a working container."
            ;;
        "")
            note "           none"
            printf '\n'
            if image_exists; then
                note "not running. ./scripts/bridge.sh start"
            else
                note "nothing to run yet. ./scripts/bridge.sh build"
            fi
            ;;
        *)
            note "           $status"
            printf '\n'
            note "it started once and stopped. './scripts/bridge.sh logs' for why, then 'stop' and 'start'."
            ;;
    esac
    # Whether anything is actually listening is the question a drive asks, and a container can be
    # Up with a dead server inside it. Cheap enough to answer here rather than a minute into a run.
    printf '\n'
    if python3 -c "
import socket, sys
s = socket.socket(); s.settimeout(2)
sys.exit(0 if s.connect_ex(('127.0.0.1', $BRIDGE_PORT)) == 0 else 1)
" 2>/dev/null; then
        note "port       something is listening on 127.0.0.1:$BRIDGE_PORT"
    else
        note "port       nothing is listening on 127.0.0.1:$BRIDGE_PORT"
    fi
}

cmd_build() {
    [[ -d "$CONTEXT" ]] || die "no $CONTEXT -- this checkout is missing docker/openpilot/."

    banner "fork checkout"
    # The fork is VENDORED -- 309 MB of tracked files under deps/openpilot, so a fresh clone of
    # this repo can build the bridge with no access to the private zapetaai org. `pull.sh` is
    # only for re-vendoring at a different commit, and is not run here.
    if [[ -f "$CONTEXT/deps/openpilot/SConstruct" ]]; then
        note "vendored   $(du -sh "$CONTEXT/deps/openpilot" | cut -f1) at $(grep -oE '[0-9a-f]{40}' "$CONTEXT/deps/openpilot/VENDORED.md" | head -1 | cut -c1-9)"
        # The ten mode-120000 paths. scons dies on `Missing SConscript 'rednose/SConscript'` if a
        # transport dropped them, which reads like a broken Dockerfile and is not. Checked rather
        # than repaired, because there is no git in here to repair from any more.
        local lost=0 p
        for p in rednose laika tinygrad selfdrive/hardware \
                 third_party/libyuv/x64/include third_party/snpe/x86_64 \
                 third_party/snpe/larch64 third_party/acados/x86_64/lib/libqpOASES_e.so \
                 third_party/acados/larch64/lib/libqpOASES_e.so \
                 third_party/acados/Darwin/lib/libqpOASES_e.dylib; do
            [[ -L "$CONTEXT/deps/openpilot/$p" ]] || { note "MISSING SYMLINK  $p"; lost=1; }
        done
        [[ $lost -eq 0 ]] || die "the vendored tree lost symlinks in transit -- git checks them out
  as mode 120000, so this means a copy that flattened them. Re-clone this repo rather than rsync."
        note "symlinks   all 10 present"
    else
        note "not vendored -- fetching the fork instead (needs SSH access to zapetaai)"
        "$CONTEXT/pull.sh"
    fi

    banner "image"
    note "context    $CONTEXT"
    note "tag        $BRIDGE_IMAGE"
    note "budget half an hour if nothing is cached"
    printf '\n'
    docker build -t "$BRIDGE_IMAGE" -f "$CONTEXT/Dockerfile" "$CONTEXT"
    printf '\n'
    note "built. ./scripts/bridge.sh start"
}

cmd_start() {
    local status
    status="$(container_status)"
    case "$status" in
        Up*) die "$BRIDGE_NAME is already up ($status). Nothing to do -- 'status' to confirm." ;;
        "") ;;
        *) die "a stopped $BRIDGE_NAME is holding the name ($status).
  ./scripts/bridge.sh logs    # why it stopped
  ./scripts/bridge.sh stop    # then start again" ;;
    esac

    image_exists || die "no image $BRIDGE_IMAGE.
  ./scripts/bridge.sh build                     # needs SSH access to zapetaai
  gunzip < bridge-image.tar.gz | docker load    # or a copy from a machine that has it"

    # --network host for the same reason compose.yaml uses it: the translator reaches this on
    # 127.0.0.1 whether it is itself in a container or not, and a bridge network in front of a
    # per-decision round trip would be a cost the timing rows exist to measure.
    # The env is the fork's simulation contract -- no panda, no firmware query, and the 5-point
    # T_IDXS that matches the AV3 model's 2 s horizon.
    docker run -d --name "$BRIDGE_NAME" --network host \
        -e SIMULATION=1 -e NOBOARD=1 -e SKIP_FW_QUERY=1 -e "FINGERPRINT=TESLA MODEL 3" \
        -e OPENPILOT_TRAJECTORY_TYPE=0 -e BRIDGE_PORT="$BRIDGE_PORT" \
        -e PYTHONPATH=/opt/bridge:/opt/openpilot:/opt/project/common \
        -w /opt/project "$BRIDGE_IMAGE" python3 -m zapeta.server >/dev/null

    printf '\n'
    note "started $BRIDGE_NAME on 127.0.0.1:$BRIDGE_PORT"
    note "./scripts/bridge.sh status    # confirm it is listening"
}

cmd_stop() {
    [[ -n "$(container_status)" ]] || die "no container named $BRIDGE_NAME. Nothing to stop."
    docker rm -f "$BRIDGE_NAME" >/dev/null
    note "removed $BRIDGE_NAME. The image is untouched."
}

cmd_logs() {
    [[ -n "$(container_status)" ]] || die "no container named $BRIDGE_NAME. Nothing to show."
    # A socket.timeout traceback at the end is not a crash: it is the last drive disconnecting.
    # The server catches it and goes back to listening, which is why it is still up.
    docker logs -f "$BRIDGE_NAME"
}

cmd_save() {
    local out="${1:-}"
    [[ -n "$out" ]] || die "save needs a file: ./scripts/bridge.sh save bridge-image.tar.gz"
    image_exists || die "no image $BRIDGE_IMAGE to save."
    note "writing $out -- about 6.2 GB compressed, so this is minutes"
    docker save "$BRIDGE_IMAGE" | gzip > "$out"
    note "done. On the other machine:  gunzip < $(basename "$out") | docker load"
}

case "${1:-status}" in
    -h|--help|help) usage; exit 0 ;;
    status) cmd_status ;;
    build) cmd_build ;;
    start) cmd_start ;;
    stop) cmd_stop ;;
    logs) cmd_logs ;;
    save) shift; cmd_save "${1:-}" ;;
    *) die "unknown command: $1
  ./scripts/bridge.sh [status|build|start|stop|logs|save <file>]
  ./scripts/bridge.sh --help" ;;
esac
