#!/usr/bin/env bash
#
# Stages 4 to 6: apply the Stage 3 review, validate the reviewed map, and convert it into
# a ScenarioNet dataset. The dataset comes out map-only -- routes, signals and actors are
# drawn by hand afterwards, in the Stage 6 pages this run writes.
#
#   ./scripts/run-stages-4-6.sh                 # workspace from .env
#   ./scripts/run-stages-4-6.sh junction-1      # override it for this run

# shellcheck source=scripts/_common.sh
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

POSITIONAL=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        -h|--help) sed -n '2,8p' "$SELF" | sed 's/^#\s\?//'; exit 0 ;;
        -*) die "unknown option: $1" ;;
        *) POSITIONAL="$1" ;;
    esac
    shift
done

resolve_workspace "$POSITIONAL"

SUB="${REVIEW_SUBMISSION:-$WS/review.json}"
REVIEW_PAGE="$WS/inspection/stage-3-review.html"

STAGE_2="$(manifest_get stage_2.status || true)"
[[ "$STAGE_2" == "passed" ]] || die "$WS has no passed Stage 2 (status: ${STAGE_2:-absent}).
  Run ./scripts/run-stages-1-3.sh first."
[[ -f "$SUB" ]] || die "no review submission at $SUB.
  Decide the findings in $REVIEW_PAGE, download review.json, and save it there.
  Or point REVIEW_SUBMISSION at it in .env."

note "workspace  $WS"
note "config     $CONFIG"
note "review     $SUB"

if ! run_stage "Stage 4 - apply the review" \
    "${CLI[@]}" apply-review -w "$WS" --submission "$SUB" --config "$CONFIG"; then
    if grep -qi "review was made against generation" "$STAGE_LOG"; then
        die "the model moved after the review was written, so the decisions cannot be
  carried across. The page to redo it in is $REVIEW_PAGE.
  A full Stage 1 run mints a new fingerprint on its own; ./scripts/run-stages-1-3.sh
  --skip-fetch does not."
    fi
    if grep -qi "review belongs to workspace" "$STAGE_LOG"; then
        die "that review was exported from a different workspace. A review is bound to the
  workspace it was decided in; check REVIEW_SUBMISSION in .env, or export a fresh one
  from $REVIEW_PAGE."
    fi
    if grep -qi "unresolved or ignored" "$STAGE_LOG"; then
        die "the review still has open blockers. Every blocking finding needs a decision
  other than unresolved or ignored; answer them in $REVIEW_PAGE and export again."
    fi
    exit 1
fi

# validate-map exits non-zero on anything but a pass, deliberately -- a written report is
# not the same as a map fit to convert.
if ! run_stage "Stage 5 - validate the reviewed map" \
    "${CLI[@]}" validate-map -w "$WS" --config "$CONFIG"; then
    die "validation did not pass, so Stage 6 was not run. What failed is in
  $WS/reports/map-validation.md and $WS/inspection/stage-5-validation.html."
fi

run_stage "Stage 6 - convert to a ScenarioNet dataset" \
    "${CLI[@]}" convert -w "$WS" --config "$CONFIG"

banner "Stage 6 is yours"
note "the dataset is map-only: MetaDrive can load and check it, but not drive it."
note "it is in $WS/scenarionet-10hz: each rate gets its own directory, because a dataset"
note "can only be replayed at the rate it was written at. --step-hz 100 makes another."
note "routes    $WS/inspection/stage-6-route-builder.html   -> routes/routes.json"
note "signals   $WS/inspection/stage-6-signal-builder.html  -> signals/signals.json"
note "actors    $WS/inspection/stage-6-actor-builder.html   -> actors/actors.json"
note "then      ${CLI[*]} convert -w $WS --config $CONFIG \\"
note "            --routes $WS/routes/routes.json"
# --actors is listed apart because it is the one that cannot stand alone: a map-only dataset
# is a single frame and holds no tracks, so convert refuses it without --routes.
note "            [--signals $WS/signals/signals.json] [--actors $WS/actors/actors.json]"
printf '\n'
