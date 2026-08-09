# Partial Export and Draft Recovery

- User goal: Keith could not find a `review.json`, and needs to save partial work and
  come back to it.
- Run timestamp: 2026-08-09 10:41:00 +08 (Asia/Singapore)

## Actions Taken

- `ReviewState.toSubmission()` no longer throws on unresolved blockers. An unfinished
  review exports with `readiness.ready` false.
- The export button and filename follow readiness: *Export review.json* /
  `review.json` when ready, *Export partial review* / `review.partial.json` when not,
  so a partial file cannot be mistaken for a finished one in the UI or on disk.
- Added `findRecoverableDrafts()`: drafts saved under an earlier generation of the
  same workspace and source OSM are offered back on boot, routed through
  `loadDecisions` so anything whose evidence checksum moved returns to unresolved.
- `DraftStore` gained `length` / `key(i)` for the scan; `memoryStore()` in the test
  fixtures implements them.

## Files and Directories Created or Modified

- `web/src/state.ts`, `main.ts`, `persistence.ts`;
  `src/osm_scenario/assets/review-client.js`
- `web/test/persistence.test.ts` (new), `state.test.ts`, `submission.test.ts`,
  `fixtures.ts`

## Commands and Tools

- In `web/`: `npx tsc --noEmit`, `npx vitest run`, `node build.mjs`.
- `uv run pytest`, `uv run ruff check`,
  `uv run osm-scenario inspect -w workspaces/junction-1 --view review`.

## What Worked

- Reusing `loadDecisions` for recovery rather than writing a second restore path.
  Recovering a draft across a regeneration is the same problem as importing a review
  across one, and it already had the right rule: carry a decision over only when the
  finding exists and its evidence checksum is unchanged.
- Keeping the draft key narrow. Widening it to ignore the fingerprint would have made
  a draft about different geometry look current; offering it back explicitly, with a
  migration summary, says what actually happened.

## What Went Wrong

- **This was a defect I introduced.** The export gate in `toSubmission()` was stricter
  than `docs/implementation-plan/README.md:282`, which makes unresolved blockers
  prevent *promotion* and names Stage 4 the authoritative gate. Combined with a draft
  key that includes the generation fingerprint, regenerating twice today left Keith
  with no way to retrieve decisions he had already made — no export, and no draft.
- Autosave itself was never broken: `persist()` already ran on every decision. The
  symptom was entirely the orphaned key, which is worth recording because "autosave is
  broken" would have been the wrong fix.

## Current State

- `npx vitest run` 40 passed (5 new); `npx tsc --noEmit` clean; `uv run pytest` 86
  passed; `uv run ruff check` clean.
- junction-1 has 138 blockers of 541 findings, so the export path in use is the
  partial one until those are decided.

## Known Gaps

- The complete-export path (button label and filename switching when
  `blockers_unresolved` reaches zero) is covered by unit tests only. Proving it in the
  browser would mean deciding 138 blockers by hand, which was not done.
- `review.json` is still a browser download with no home in the workspace;
  `review/` and `apply-review` remain Stage 4 work.
- Recovery offers the newest matching draft only. Older ones stay in storage,
  unreachable through the UI.

## Not Written

- No `docs/mapping-algo-changes/` entry: no algorithm code was touched.
