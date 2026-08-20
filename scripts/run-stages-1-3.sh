#!/usr/bin/env bash
#
# Stages 1 to 3: acquire and normalize the source, generate the preliminary lane model,
# and render the Stage 3 review page. It stops there because Stage 3 is a person sitting
# in a browser answering findings -- nothing downstream can be run until they have.
#
#   ./scripts/run-stages-1-3.sh                 # workspace from .env
#   ./scripts/run-stages-1-3.sh junction-1      # override it for this run
#   ./scripts/run-stages-1-3.sh --skip-fetch    # source/map.osm has not changed
#
# --skip-fetch is not only a time saver: Stage 1 rebuilds the projected GraphML, whose
# checksum feeds the Stage 2 generation fingerprint, and osmnx stamps a build timestamp
# into it -- so a full run mints a new fingerprint and unbinds any existing review.json
# even when the OSM is byte-identical. Skip Stage 1 whenever the map itself has not moved.

# shellcheck source=scripts/_common.sh
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

SKIP_FETCH=0
POSITIONAL=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --skip-fetch) SKIP_FETCH=1 ;;
        -h|--help) sed -n '2,15p' "$SELF" | sed 's/^#\s\?//'; exit 0 ;;
        -*) die "unknown option: $1" ;;
        *) POSITIONAL="$1" ;;
    esac
    shift
done

resolve_workspace "$POSITIONAL"

# Stage 1 rewrites source/manifest.json from scratch, so anything the run needs out of it
# has to be read now. The driving side is only ever supplied on the command line, and the
# manifest is the only record of what it was.
SOURCE_TYPE="$(manifest_get source.type || true)"
if [[ "$SOURCE_TYPE" != "local_file" ]]; then
    die "$WS was acquired from '$SOURCE_TYPE', not a local file. Re-running fetch would
  re-download and overwrite source/map.osm, taking any hand edits with it. Run Stage 1
  yourself if that is what you want; this script will not do it for you."
fi
SIDE="${DRIVING_SIDE:-$(manifest_get driving_side || true)}"
[[ -n "$SIDE" ]] || die "no driving_side in $WS/source/manifest.json; set DRIVING_SIDE in .env."

note "workspace  $WS"
note "config     $CONFIG"
note "driving    $SIDE"

if [[ $SKIP_FETCH -eq 1 ]]; then
    banner "Stage 1 skipped (--skip-fetch)"
    note "source/map.osm is assumed unchanged since the last run."
else
    # Stage 1 rebuilds road-network-local.graphml, and osmnx stamps a build timestamp into
    # GraphML -- so its checksum moves on every run even when map.osm is byte-identical.
    # That checksum is an input to the Stage 2 generation fingerprint, so any existing
    # review is unbound by this. Measured, not assumed: two Stage 1 runs over the same
    # map.osm produced two different graphml checksums.
    if [[ -f "$WS/review.json" ]]; then
        note ""
        note "note: Stage 1 will mint a new generation fingerprint (the graphml carries a"
        note "      build timestamp), so $WS/review.json will no longer apply."
        note "      Use --skip-fetch to keep it when source/map.osm has not changed."
    fi
    # map.osm already lives in $WS/source, so acquisition uses it in place rather than
    # copying it -- which is what makes this safe to re-run over a hand-edited map.
    run_stage "Stage 1 - fetch and normalize" \
        "${CLI[@]}" fetch --osm-file "$WS/source/map.osm" --driving-side "$SIDE" -w "$WS"
fi

if ! run_stage "Stage 2 - generate the preliminary lane model" \
    "${CLI[@]}" generate-map -w "$WS" --config "$CONFIG"; then
    if [[ $SKIP_FETCH -eq 1 ]] && grep -qi "checksum\|sha256\|drift" "$STAGE_LOG"; then
        die "source/map.osm no longer matches the checksum Stage 1 recorded, so it was
  edited after the last run. Re-run without --skip-fetch."
    fi
    exit 1
fi

run_stage "Stage 3 - render the review page" \
    "${CLI[@]}" inspect -w "$WS" --view review

REVIEW_PAGE="$WS/inspection/stage-3-review.html"
banner "Stage 3 is yours"
note "open      $REVIEW_PAGE"
note "decide    every blocking finding -- Stage 4 refuses a map with open blockers"
note "export    review.json, and put it at $WS/review.json"
note "then      ./scripts/run-stages-4-6.sh${POSITIONAL:+ $POSITIONAL}"
printf '\n'
