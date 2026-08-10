# turn:lanes Overrides Apply

Keith selected `Set movement` on two `turn_permission_geometry_conflict` blockers
and found the decision unusable: Stage 4 refused the rule by name. His point was
that applying a Stage 3 decision is what the stages after Stage 3 are *for*. This
is the second OSM tag write to land, after `lane_count_inference`.

## Actions Taken

1. Removed `turn_permission_geometry_conflict` from `_OSM_NATIVE_RULES`, so Gate 5
   no longer refuses an override on it.
2. Added `_TURN_LANE_MOVEMENTS` (the six values the Stage 3 dropdown offers) and
   `_override_movement`, and a `_check_override_value` branch validating
   `{"movement": <token>}`. `none` is excluded although OSM permits it:
   `movement_matches("none", ...)` is false for every movement, so writing it
   would strand the lane the override was meant to free.
3. Added `_turn_lane_slot`, which resolves the finding's **approach** lane
   (`affected_feature_ids[0]`, not the restored destination) to
   `(way, direction, slot, lane_count)`. The slot inverts
   `generation._turn_permissions`: under left-hand traffic `idx0` is the last slot.
4. `_overrides_from` gained a `driving_side` parameter and now collects
   `{way: {direction: {slot: movement}}}`, refusing three things by name: an
   override restating the value already in the tag, two overrides disagreeing
   about one slot, and a way carrying both a lane-count and a turn:lanes override
   (re-laning moves every slot).
5. Added `_turn_lanes_tag`, which edits the very `|`-split string
   `_turn_permissions` indexes into and writes back to the key that function
   reads first. It refuses a slot count that does not match the lanes the model
   built, and a bare `turn:lanes` on a two-way way.
6. `_write_reviewed_osm` takes the turn-lane overrides alongside the lane counts;
   the byte-copy short-circuit now requires both to be empty.
7. `_decision_is_satisfied` takes the decision status and returns `False` for an
   **overridden** conflict that is still in the reviewed model. Without it, an
   override that writes a tag permitting nothing available would regenerate the
   same blocker and read as resolved, and Stage 5 would pass a map with an
   unanswered conflict.
8. `_markdown` gained a "Ways retagged in reviewed.osm" table. A tag write is now
   a routine act and was visible only in JSON.

## Files and Directories Created or Modified

- `src/osm_scenario/apply_review.py` - all of the above.
- `src/osm_scenario/comparison_view.py` - docstring said Stage 4 refuses any
  OSM-tag decision.
- `web/src/controls.ts` - comment only; `overrideEffect` for this rule already
  described this behaviour and is now true. The compiled bundle is unchanged.
- `tests/fixtures/osm/turn-lanes-conflict.osm` - new. The T junction with way
  `200` made a two-lane one-way tagged `turn:lanes=left|left`, which the junction
  cannot offer.
- `tests/unit/test_apply_review.py` - nine tests.
- `docs/policies/stage-2-finding-reference.md` - the rule's section, written
  earlier the same day, said the override was refused.
- `stage 3,4,5 guide.md`, `docs/implementation-plan/README.md` - the same claim.

## Commands and Tools

```bash
uv run pytest                 # 186 passed (was 177)
uv run ruff check             # clean
uv run osm-scenario apply-review -w <scratch>/junction-1 --submission <...>.json
uv run osm-scenario validate-map -w <scratch>/junction-1
```

Every workspace run was against a **copy** of `junction-1` in the scratchpad.
`workspaces/junction-1` was not written to.

## What Worked

- Editing the source string rather than composing a fresh value from the model.
  The reader splits on `|` and takes slot `n`; setting slot `n` of that same
  split is the inverse by construction, so the kerbside-first ordering exists in
  exactly one place.
- The fixture produces one finding per lane of a two-lane approach, which is what
  makes the inversion testable: overriding the offside lane `idx0` must produce
  `left|right`, not `right|left`. A naive `slots[lane_index]` passes every other
  test in the file.
- On the junction-1 copy, `movement: through` on both lanes of `756118314` wrote
  `turn:lanes=through|through`, the lanes read back `['through']`, and both
  blockers at node `1927184814` resolved with no lane left without an exit.

## What Went Wrong

- The first workspace copy was named `ws`, and Stage 4 refuses a review whose
  identity names another workspace. Renaming the directory was the fix - the
  check is correct.
- Keith's current `review.json` cannot be applied for two reasons that predate
  this work, and both surfaced only once Gate 5 stopped masking them. See below.

## Current State

An `overridden` `turn_permission_geometry_conflict` decision now writes
`turn:lanes` into `review/reviewed.osm` and regenerates. `accepted`,
`not_applicable` and `overridden` are all live for this rule; `unresolved` and
`ignored` still stop the run, as for any blocker.

`workspaces/junction-1` is untouched by this session: its Stage 4 outputs are
still those of the 08:38 export, and its Stage 5 report still reads `passed`.

## Known Gaps

- **Keith's export carries a stale `lane_count_inference` override.** Decision
  `1504e9a140653758` holds `{"lanes": 2, "lanes_forward": 1}`, the three-field
  shape an older client emitted. The current client writes `{"lane_count": n}`.
  Stage 4 refuses it by name rather than guessing which field was meant, so that
  finding has to be decided again in Stage 3.
- **The signal blocker changed answer between exports.** `61ac148f3c39d2a5` was
  `not_applicable` with a reason in the applied review and is `accepted` in the
  current one. Accepting it cannot satisfy it - the finding only fires when no
  approaching lane exists - so it stays open and fails Stage 5. Unrelated to this
  change.
- **Way `756118314`'s conflict is still a source-side question.** `Set movement →
  right` is refused as a no-op, correctly: the tag already says `right`. What the
  node actually shows is that `turn:lanes` was copied onto both halves of
  Meranti while only the downstream half turns.
- **The via-way `no_u_turn` (relation `10421009`) is over-applied.** It forbids
  `39619063 → 777160375` for all traffic rather than only traffic arriving from
  `777160373`, which is why both of Meranti's lanes have zero exits. Untouched,
  and not reachable by any `turn:lanes` value.
- The Stage 3 dropdown still defaults to `through` and is never prefilled from
  the finding. Harmless now that a wrong pick is caught, but it does mean the
  reviewer must look before pressing.

## Not Written

No `docs/mapping-algo-changes/` entry. That folder records corrected mistakes in
the **lane-mapping algorithm**; this is a Stage 4 review-application feature that
had not been built. `generation.py` and `topology.py` are unchanged, and no
connector moved except through a tag the reviewer asked for.
