# Ignoring Warnings

- User goal: set warnings aside — speed and lane-width defaults especially — so they
  stop crowding the queue, without deciding each one before Stage 4.
- Run timestamp: 2026-08-09 14:38:20 +08 (Asia/Singapore)

## Actions Taken

- Added a fifth decision status, `ignored`: a valid finding set aside unjudged, with
  the generator's proposal standing. Permitted on **warnings only**.
- `validateDecision` now takes the finding and refuses `ignored` on a blocker;
  `loadDecisions` drops an ignored blocker arriving from a file rather than trusting
  the file's claim.
- `Readiness` gained `ignored`, counted apart from `resolved`, so "decided" keeps
  meaning judged. `ready` is unchanged: blockers alone.
- `decideBulk` scope takes an optional `roadClass` — omitted means every road class,
  `null` still means unclassified only.
- Panel: a collapsible **Warnings** section with per-rule counts, *Ignore all* and
  *Restore*; a per-finding **Ignore** button on warnings; an **Ignored** option in the
  state filter; ignored rows muted; ignored counts in the queue header and readiness
  banner.
- `submission_version` is now 3, and `parseSubmission` accepts 1, 2 and 3.
- `docs/implementation-plan/README.md` records the status, its warnings-only rule, and
  that Stage 4 must reject `ignored` on a blocking finding.

## Files and Directories Created or Modified

- `web/src/types.ts`, `state.ts`, `panel.ts`, `controls.ts`, `submission.ts`,
  `style.css`; `src/osm_scenario/assets/review-client.js`
- `web/test/filters.test.ts` (new), `state.test.ts`, `submission.test.ts`,
  `controls.test.ts`
- `docs/implementation-plan/README.md`

## Commands and Tools

- In `web/`: `npx tsc --noEmit`, `npx vitest run`, `node build.mjs`.
- `uv run pytest`, `uv run ruff check`,
  `uv run osm-scenario inspect -w workspaces/junction-1 --view review`.

## What Worked

- Keeping `ignored` distinct from `accepted`. The generated map is identical either
  way, but Keith is scoring a VLM against decisions he made by hand: an accepted
  finding is ground truth and an ignored one is the absence of an answer. Collapsing
  them would have seeded his answer key with 82 judgements he never made.
- Enforcing the warnings-only rule in two places for two different threats — the
  button that cannot be pressed, and the file that cannot be trusted.

## What Went Wrong

- The plan said `parseSubmission` would reject `ignored` on a blocker. It cannot: the
  parser sees a file and has no severities. The check moved to `loadDecisions`, where
  the findings are known, and the deviation is recorded here rather than left to look
  like an oversight.

## Current State

- `npx vitest run` 50 passed (10 new); `npx tsc --noEmit` clean; `uv run pytest` 88
  passed; `uv run ruff check` clean.
- junction-1: 158 findings, 57 blockers, 101 warnings — `speed_default` 41,
  `lane_width_default` 41, `lane_transition_count_mismatch` 19. Ignoring the first two
  leaves a queue of **76** and does not move the blocker count.

## Known Gaps

- The panel is DOM-bound and the client tests have no DOM environment, so the
  Warnings section, the Ignore button and the muted row styling are not covered by a
  test. `applyFilters`, `validateDecision`, `readiness`, `decideBulk` and
  `loadDecisions` — the rules that decide what ignoring *means* — are.
- Stage 4 does not exist, so its obligation to reject an ignored blocker is written
  in the spec and enforced nowhere yet.

## Not Written

- No `docs/mapping-algo-changes/` entry: no algorithm code was touched.
