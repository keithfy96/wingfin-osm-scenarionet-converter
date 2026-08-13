# Lanes were dealt across a destination a turn restriction forbids

- **Date:** 2026-08-13 04:42:58
- **Identified by:** Keith
- **Files changed:** `src/osm_scenario/generation.py` (`_restricted_groups`,
  `_balanced_merge_assignment`, `_side_filtered_candidates`, the per-node allocation),
  `src/osm_scenario/topology.py` (`node_restriction_forbids`)
- **Generator version:** direct-osm-stage2-v20 → direct-osm-stage2-v21

## Symptom

Every vehicle on mosque way `859423756` is required to turn right — relation 18555950 is a
node-via `no_left_turn` that removes the straight-on into `859423755`. Only the offside lane
`2bcdc3d7e3a3716e` was wired up. `c4d955bf1344fed1` (idx1) and `345b4023d28f7bad` (idx2)
ended node `8010943714` with **no exit at all**, and `ca1771d510b37708` / `d1d053abee5091e1`
had **no entry**. One node on, `859423754` idx1 `2f8298215af3c592` and idx2
`8b96c9736eda8fe3` had no entry either, so Keith's route
`859423756 → 935525165 → 859423754` existed on one lane of three.

```
 node 8010943714   + = left · − = right · left-hand traffic · idx0 = offside, idx2 = kerbside
 drawn in the order the generator worked: side rule first, restriction ~250 lines later

   APPROACH — 859423756, 3 lanes    ┊  DEST A — 859423755, 3 lanes (exit north)
                                    ┊  DEST B — 935525165, 3 lanes (ring → Persiaran Ara)
 ══════════════════════════════════ ┊ ══════════════════════════════════════ KERB ══
   idx2/3  345b4023                 ┊
     +29.09° through ───────────────┊─►  A idx2/3  bf446da0   ✗ forbidden, rel 18555950
     −41.76° right   ─── STRUCK ────┊ ✗  B idx2/3  d1d053ab   0 FEEDS
 ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─┊─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─
   idx1/3  c4d955bf                 ┊
     +29.09° through ───────────────┊─►  A idx1/3  bf398615   ✗ forbidden, rel 18555950
     −41.76° right   ─── STRUCK ────┊ ✗  B idx1/3  ca1771d5   0 FEEDS
 ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─┊─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─
   idx0/3  2bcdc3d7   ← the only lane the side rule let turn right
     +29.09° through ───────────────┊─►  A idx0/3  dae30e97   ✗ forbidden, rel 18555950
     −41.76° right   ───────────────┊─►  B idx0/3  34d10b4e   ✓ ACTIVE, 1 feed
 ══════════════════════════════════ ┊ ═══════════════════════════════ CENTRELINE ══
```

After the fix, the same node:

```
   idx2/3  345b4023  −41.76° ───────┊─►  B idx2/3  d1d053ab  ✓ ACTIVE  d5e4f87be3da3a08
   idx1/3  c4d955bf  −41.76° ───────┊─►  B idx1/3  ca1771d5  ✓ ACTIVE  b2e958d79456481b
   idx0/3  2bcdc3d7  −41.76° ───────┊─►  B idx0/3  34d10b4e  ✓ ACTIVE  ef145c50f1c3d7a9
   and the three ✗ movements into 859423755 are unchanged, still forbidden, same ids
```

## Fundamental cause

**The lanes were dealt out before the restrictions were known, so both rules that decide
where a lane lands were shown a destination that was about to be deleted.**

Node-via restrictions were only read at `generation.py:2218`, after the whole candidate list
was final. Two things had already happened by then:

1. `_balanced_approach_assignment` deals an approach's lanes straight across its destinations
   when the arithmetic closes, and returns `None` otherwise because a lane then genuinely
   serves more than one movement. It counted 3 lanes arriving against **6** lanes of
   destination — `859423755` (3) plus `935525165` (3) — and stood aside. Discount the
   forbidden destination and it is 3 against 3, which closes.

2. `_side_filtered_candidates` then struck the −41.76° right turn from idx1 and idx2: a right
   turn in left-hand traffic is `offside`, and `side_lane_index("offside", 3)` is `0`. Its
   no-stranding catch — *keep the straightest movement if the filter would leave the lane
   with nowhere to go* — did not fire, because `kept` was not empty. Each lane still held the
   +29.09° straight-on, **and that straight-on was present only because of the clause
   immediately above it**: *"a candidate a node-via restriction forbids is kept so the
   restriction still has something to act on."* The lane was judged to have somewhere to go
   on the strength of a movement that existed purely in order to be deleted.

Node `1983979095` failed identically. `935525165 → 859423754` measures −23.60°, which
`classify_movement` calls `through` but `movement_side` still puts `offside` — any movement
past `side_movement_min_degrees` (10°) carries a side regardless of its class — so only idx0
kept it, and idx1/idx2 were left holding only the −126.54° movement rel 18555952 forbids.

Neither half is sufficient alone, and this was measured rather than assumed. With only the
allocation fixed, the side rule still strikes the allocated candidate and the catch still
does not fire. With only the catch fixed, the target comes from
`_mapped_lane_index(source, 3, "offside", side_block=[])`, which answers `0` for every lane —
idx1 and idx2 both land on `34d10b4e` and the middle and kerbside lanes stay starved.

## Fix

- `topology.node_restriction_forbids(from_way_id, to_way_id, junction_node_id, relation)` —
  the `no_*` / `only_*` reading taken off `MovementCandidate` so it can also be asked *before*
  any candidate exists. `forbidden_by_node_restriction` delegates to it, so the allocation and
  the enforcement cannot drift apart.
- `generation._restricted_groups` — per approach, which destination groups a node-via
  restriction rules out at this node. `_balanced_approach_assignment` and
  `_balanced_merge_assignment` (new `blocked` keyword) are given the surviving destinations
  only.
- **The movements to the forbidden destination are still generated.** Only the allocation is
  blinded. A restriction that deletes nothing leaves nothing on the map to explain why the
  turn is missing, and `RestrictionEffect.forbidden_connector_ids` is the record it was
  obeyed. `allocated.get(group_key)` is `None` for a blocked group, so it falls through the
  existing `_mapped_lane_index` path and its connector ids do not move.
- **Nothing is hidden where that would leave the approach with no destination at all.** The
  filter exists to stop a doomed destination distorting the split between the ones drivers
  may take; with no survivors there is no split, and blinding the allocation only collapses
  the forbidden movements onto one lane and moves their ids. Junction-1 relation 16740674
  is that case, and it is why the guard exists — without it, two lanes of way `777159294`
  collapsed onto one and a settled forbidden connector id moved for no gain.
- `_side_filtered_candidates` — a candidate kept only because a restriction forbids it no
  longer counts toward "this lane has somewhere to go". When the catch fires it returns those
  candidates **as well as** the restored one, so the restriction still has its target.
- `blocks_by_group`, the feeder list `_merge_side` compares, was left alone deliberately.
  Filtering it too was tried and measured: it made no difference to any active connector and
  moved the forbidden connector ids of **seven** relations across both workspaces, including
  one in junction-1, which otherwise does not move at all.

## Verification

`uv run pytest` — **359 passed**. `uv run ruff check` — clean.

New fixture `tests/fixtures/osm/restricted-destination.osm` with two cases, both confirmed to
reproduce the defect on `HEAD` before the fix: case A (counts close once the forbidden exit is
discounted) produced only `100 idx0 → 120 idx0`; case B (counts do not close, so only the
catch can help) produced only `200 idx0 → 220 idx0`. Three new tests in `test_generation.py`
and one in `test_topology.py` asserting the two readings of the node-via rule agree.

Both workspaces regenerated and compared feature by feature against the pre-change models:

| | mosque | junction-1 |
| --- | --- | --- |
| lanes | 405 → 405, ids identical | 285 → 285, ids identical |
| connectors | 192 → 200 | 116 → 116 |
| active | 117 → **123** | 81 → 81 |
| forbidden | 37 → 37 | 4 → 4 |
| active connectors lost | **0** | **0** |
| lanes with no live feed | 36 → **30** | 21 → 21 |
| lanes with no live exit | 32 → **26** | 20 → 20 |
| restrictions whose forbidden ids moved | **none** | **none** |

The six new movements on mosque:

```
  859423756 idx1/3 → 935525165 idx1/3   −41.76°   b2e958d79456481b
  859423756 idx2/3 → 935525165 idx2/3   −41.76°   d5e4f87be3da3a08
  935525165 idx1/3 → 859423754 idx1/3   −23.60°   d81b6ed8675f8ea0
  935525165 idx2/3 → 859423754 idx2/3   −23.60°   8966d005e7ddb045
  1173001830 idx1/3 → 935525160 idx1/3  −44.94°   d430a480c720b02b
  1173001830 idx2/3 → 935525160 idx2/3  −44.94°   c8ae07555842cbf6
```

Keith's chain now runs one-to-one on all three lanes: `859423756` idx0/1/2 → `935525165`
idx0/1/2 → `859423754` idx0/1/2, offside to offside and kerbside to kerbside.

`ambiguous_connector` blockers on mosque went 38 → 40. Both new ones are `935525160` idx1 and
idx2 into `859423755` idx1/idx2 — lanes that previously held nothing but a forbidden movement
and are now asked rather than left starved. That is the finding doing its job, not a
regression, and it is not to be silenced.

Junction-1 is byte-for-byte unchanged apart from the generator version stamp. The version bump
moves `generation_fingerprint` for every workspace, so both Stage 3 reviews must be re-run and
`routes.json` / `signals.json` redrawn; connector ids carry no version, so every recorded
decision on a surviving connector carries.
