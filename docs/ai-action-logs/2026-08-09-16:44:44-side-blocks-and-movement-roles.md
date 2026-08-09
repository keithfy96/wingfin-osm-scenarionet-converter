# A side picks where a block starts; and colouring which lane turns into which

- User goal: understand why a `turn_permission_geometry_conflict` was raised on correctly
  tagged `turn:lanes=right|right`, why the movement looked like a sideways teleport, and
  see which lane turns into which instead of a field of identical yellow.
- Run timestamp: 2026-08-09 16:44:44 +08 (Asia/Singapore)

## Actions Taken

- Diagnosed the teleport: `_mapped_lane_index` returned one index for every lane claiming
  a side, so both `turn:lanes=right|right` lanes went to index 0.
- Fixed it by making a side fix the block's **leading** index, with the rest dealt inward:
  `_side_block_offset` and `_tagged_side_block` in `generation.py`, plus
  `tagged_movement_side` extracted from `movement_side` so the tag has one reading.
- Bumped `GENERATOR_VERSION` to `direct-osm-stage2-v17`.
- Added `movement_roles` to the review payload — approach / destination per named lane,
  derived from the links at the node — and taught the client to prefer it over reading a
  connector's ends. Lane labels now carry the way; the queue row reads as a movement;
  Entry/Exit rows pluralise. `PAYLOAD_VERSION` 1 → 2.
- Updated `CLAUDE.md`'s starved-lane section and wrote the change-log entry.

## Files and Directories Created or Modified

- `src/osm_scenario/generation.py`, `topology.py`, `review.py`
- `tests/unit/test_generation.py`, `tests/unit/test_topology.py`
- `web/src/types.ts`, `details.ts`, `panel.ts`, `main.ts`; `web/test/details.test.ts`;
  `src/osm_scenario/assets/review-client.js`
- `CLAUDE.md`,
  `docs/mapping-algo-changes/2026-08-09-16:44:44-a-side-picks-where-a-block-starts.md`

## Commands and Tools

- `uv run osm-scenario generate-map -w workspaces/junction-1 --config config/default.yaml`
- `uv run osm-scenario inspect -w workspaces/junction-1 --view review`
- `uv run pytest`, `uv run ruff check`; in `web/`: `npx tsc --noEmit`, `npx vitest run`,
  `node build.mjs`.

## What Worked

- Answering the "will this break left turns?" question with a census rather than an
  argument. Counting every multi-lane approach group by turn family showed zero
  left-family cases, and surfaced node 7251588323 — a two-lane right turn that already
  maps correctly through `_balanced_approach_assignment`. That one row explained the
  whole shape of the bug: only oversubscribed approaches reach `_mapped_lane_index`.
- Predicting the two affected connectors before writing any code, then asserting exactly
  those two moved and every other connector was identical field for field.

## What Went Wrong

- The first plan proposed treating the straight-on movement as a continuation. It would
  have **suppressed** both `turn_permission_geometry_conflict` blockers. Keith rejected
  it: an explicit `turn:lanes` is adhered to even where the geometry disagrees, and the
  disagreement is raised as a review. Recorded in memory as
  `turn-lanes-adhered-then-reviewed` so it is not re-proposed.
- The plan asserted both blockers would be byte-identical afterwards. One necessarily
  changed identifier, because it names the restored movement's target and that target
  moved. Correct consequence, wrong prediction; the change-log entry states it.
- A nearside overflow test expectation was written in the wrong order (`[1, 0, 0]` for a
  list in lane-index order). The code was right, the expectation was not.

## Current State

- junction-1: 139 findings, **blockers unchanged at 57**,
  `lane_transition_count_mismatch` 1 → 0, lanes fed by nothing 22 → 21. Exactly two
  connectors re-targeted, exactly three lanes touched, geometry unmoved, two runs
  byte-identical.
- `uv run pytest` 103 passed (11 new); `npx vitest run` 54 passed (4 new); `ruff` and
  `tsc` clean.

## Known Gaps

- junction-1 has **no** multi-lane nearside block, so the left-turn branch of the fix is
  exercised only by unit tests. Regenerating the workspace cannot catch a sign error
  there.
- The panel remains DOM-bound, so the Entry/Exit rows, the pluralisation and the queue
  movement summary are not covered by a test; `movementEnds` and `label` are.
- Still open, out of scope and unplanned: relation 10421009 is a via-way `no_u_turn`
  whose via→to connectors were removed for **all** traffic rather than only traffic from
  the `from` way, leaving way 39619063 with zero exits.
