# Review-Required Movements Reachable

- User goal: two U-turns into lane `b6e51e4cab42384b` were marked **review** in the
  Stage 3 popup with no way to act on them, and no statement of why they were held.
- Run timestamp: 2026-08-08 19:42:07 +08 (Asia/Singapore)

## Actions Taken

- **Generator names the ambiguity cause.** `MovementCandidate.ambiguous` was a
  four-clause `or` computed inline; it is now `bool(_ambiguity_causes(...))`, with the
  causes carried on the candidate as `ambiguity_causes`. The triggers are unchanged.
- `ambiguous_connector` findings quote the headline cause in their reason, list any
  further causes after it, and carry `ambiguity_causes` and `turn_angle_degrees` in
  `proposed_value`.
- `GENERATOR_VERSION` bumped to `direct-osm-stage2-v13`.
- **The status pill became a control.** A link row now carries the connector it was
  generated as, and its status opens the blocker that names that connector — or
  focuses the movement when no finding names it. Continuations stay inert.
- The lane popup states in words how many movements at the lane need review.
- Queue rows describe a connector finding as the movement it is —
  `<from lane> → <to lane> · reverse -160.2° at node 1928630157`.

## Files and Directories Created or Modified

- `src/osm_scenario/generation.py` — `_ambiguity_causes`, `_ambiguity_reason`,
  `_BORDERLINE_TURN_BAND`, `_CAUSE_LABELS`, `GENERATOR_VERSION`
- `src/osm_scenario/topology.py` — `MovementCandidate.ambiguity_causes`
- `web/src/details.ts`, `panel.ts`, `style.css`; `src/osm_scenario/assets/review-client.js`
- `tests/unit/test_generation.py`, `web/test/details.test.ts`

## Commands and Tools

- `uv run pytest`, `uv run ruff check`,
  `uv run osm-scenario generate-map -w workspaces/junction-1 --config config/default.yaml`,
  `uv run osm-scenario inspect -w workspaces/junction-1 --view review`.
- In `web/`: `npx tsc --noEmit`, `npx vitest run`, `node build.mjs`.

## What Worked

- Snapshotting every connector id by status *before* regenerating, then diffing.
  "Naming the cause changes nothing about what is flagged" is a claim worth proving
  rather than asserting.
- Keeping the causes on the candidate rather than recomputing them at the finding.
  The old code recomputed `_unproven_sharp_movement` with
  `source=lane_lookup[candidate.from_lane_id]` where the ambiguity pass had used the
  loop's `source`; one source of truth removes the chance of those disagreeing.

## What Went Wrong

- The first client test claimed a connector-backed link was a continuation. The
  fixture had no link that came only from `exit_lanes`, so the assertion could not
  have failed for the right reason. Added `lane-d` as a genuine continuation.

## Current State

- `uv run pytest` 84 passed (3 new); `uv run ruff check` clean; `npx vitest run`
  27 passed (2 new); `npx tsc --noEmit` clean.
- Before/after junction-1, by script: **29** `review_required` connectors with
  identical ids, **77** active and **5** forbidden likewise, all **541** finding
  identifiers unchanged, all 29 `ambiguous_connector` evidence checksums changed,
  every one carrying at least one cause.
- Causes across the 29: 21 `uturn_without_evidence`, 3 `competing_movements`,
  2 `unproven_sharp_movement`, 2 U-turn + competing, 1 competing + borderline.
  The single reason shared by 27 findings became 9 distinct sentences.

## Generated Code Details

- Finding identifiers are `deterministic_id("finding", rule, source_type, *source_ids,
  *affected_feature_ids)` and exclude the reason, so the findings kept their ids while
  their `evidence_checksum` — which does cover the reason — changed. A review imported
  from before this change returns those 29 decisions to unresolved, which is the
  intended migration behaviour.
- The version bump was required, not cosmetic: `generation_fingerprint` is derived
  from the generator version, schema version, source and graph checksums and config,
  not from model content. Without the bump two different `preliminary.json` files
  would have claimed one fingerprint.

## Known Gaps

- **The end-to-end invariant is not under test.** "Every `review_required` connector
  is named by exactly one `ambiguous_connector` finding" was verified by script
  against junction-1 (29/29), not by a test: `tests/fixtures/osm/tiny.osm` is the only
  OSM fixture and it produces no connectors at all. A junction fixture would unlock
  connector coverage generally and is worth its own change.
- Unchanged from the previous run: no ambiguous-crossing, missing-tag or
  direction-warning layers; no browser smoke test; no signal-association overlay;
  `apply-review` (Stage 4) does not exist.

## Follow-up in the same run — the two ends of a movement are drawn

Keith could read the description of a movement finding but not see which lanes it
meant. Selecting a finding now paints the **entry lane orange** (`#f76707`) and the
**exit lane green** (`#37b24d`), on top of the yellow selection, and the same two
colours are used for the "Entry lane" / "Exit lane" chips in the detail pane and the
connector popup, so a chip and the lane it names are recognisably one thing.

- `FeatureIndex.movementEnds()` reads the ends off the connector — a finding names
  only the connector, so there is nowhere else to get them. Ids that are not
  connectors contribute nothing rather than guessing a direction.
- `focusFeatures` now takes a `FocusPlan` and paints roles last, so they win over the
  plain selection colour where the two overlap.
- Verified against junction-1: all 29 movement findings resolve to two drawn lanes.
- Client tests 28 passed (1 new, covering a connector whose far lane was never drawn).

## Follow-up — the queue highlight and the detail pane are one selection

Keith reported unrelated rows looking highlighted while the detail pane showed the row
he had clicked.

- **Cause: a CSS class collision.** A queue row carries `finding.severity` as a class,
  so every `warning`-severity row matched the `.warning` banner rule added earlier for
  notices — cream background, orange border. Two unrelated rows read as selected.
  The banner class is now `.notice`, and the reason is written above the rule.
- The selection and the detail pane are now kept in step in both directions: a
  finding the filters have dropped stops being the selection, and `select()` from a
  map popup clears the filters rather than highlighting a row that is not rendered.
- The selected row scrolls itself into view, but only when the selection actually
  moved — recording a decision re-renders the queue, and scrolling then would yank
  the list out from under the reviewer.
- Not covered by a test: the panel is DOM-bound and the client test setup has no DOM
  environment. `applyFilters` (the pure part) is tested; the rendering rules are not.

## Follow-up — every button states what it does

Keith asked what accepting or rejecting a movement actually means. The UI never said.

- `ControlSpec` gained `acceptEffect` / `overrideEffect`, filled for all nine rules and
  the fallback, plus two shared constants for "Not applicable" and "Clear decision".
  The detail pane renders them as an always-visible block under the buttons.
- `ambiguous_connector` was the rule that prompted the question and read worst:
  "Accept movement" / "Reject movement" became "Keep this movement" / "Remove this
  movement", and the question now asks whether the movement should exist at all.
- The sentences say *where* a decision lands, because it differs per rule: connector
  selection, signal association and stop-line placement stay non-OSM overrides in
  `review/review.json`, while speed, width, lane counts, turn tags and restrictions
  are written into `review/reviewed.osm` and regenerated from.
- New `web/test/controls.test.ts`: every rule states an accept effect, an override
  effect exists exactly when an override button does, and an unknown rule falls back
  to a spec that still states its effect.
- Client tests 33 passed (5 new). All 7 rules present in junction-1 verified against
  the shipped bundle.

**These sentences are a contract, not observed behaviour.** They restate Stage 4 in
`docs/implementation-plan/README.md`, and `apply-review` does not exist yet. Whoever
builds Stage 4 has to keep `controls.ts` in step with what it actually does.

## Not Written

- No `docs/mapping-algo-changes/` entry. CLAUDE.md Section B criterion 2 asks for a
  fix to algorithm code that produced a wrong mapping. The same 29 connectors are
  flagged before and after; only their explanation and their reachability changed.
