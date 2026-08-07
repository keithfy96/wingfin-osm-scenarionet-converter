# Merging approaches starved the middle lane of the road they joined

- **Date:** 2026-08-07 12:34:23
- **Identified by:** Keith
- **Files changed:** `src/osm_scenario/generation.py`
  (`_balanced_merge_assignment`, `_kerb_first_key`), `tests/unit/test_generation.py`
- **Generator version:** `direct-osm-stage2-v10` → `direct-osm-stage2-v11`
- **Commit:** uncommitted at the time of writing

## Symptom

Lanes `72fdbea2a86f51e8` and `6932800f61a604a6` each entered correctly and then left
into the wrong lane of the road they join. Keith: *"why … the lane it enters from
correctly connects from the middle lane, but when it leaves, it instead proceeds to
the kerbside lanes? … does it not make sense for the middle lane to connect to the
middle lane on the opposing side?"*

At both nodes a two-lane road and a one-lane link merge into a three-lane
carriageway. The middle lane of the three was fed by **nothing**, and the link came
to rest on the **identical coordinate** as the road's kerbside lane — two lanes of
different ways drawn on top of each other:

```
  link 72fdbea2a86f51e8  ends  (-5.84, -124.82)
  road fa74351a73e87a68  ends  (-5.84, -124.82)     ← identical
```

```
 node 1928630009             + = left turn · − = right turn · left-hand traffic
 lane indices run centre-out: idx0 hugs the centreline, idx(n−1) is kerbside
 BEFORE — v10

   APPROACHES                          ┊  DESTINATION
   (arriving at the node)              ┊  way 776021091 · Perdana · 3 lanes
                                       ┊  (the node's only outgoing group)
 ═════════════════════════════════════ ┊ ═══════════════════════════════ KERB ══

   182502409  idx0/1  +14.81° ────┐    ┊
     1-lane link, no turn:lanes   ├───────────────►  idx2/3  b63366201b  nearside
   776021086  idx1/2  +0.17°  ────┘    ┊                    2 feeds — SHARED
     Perdana, 2 lanes, oneway          ┊
 ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┊─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─

        ✗  N O T H I N G   F E E D S   T H I S   L A N E  ✗
                                       ┊         idx1/3  eef18fbc84  MIDDLE
                                       ┊                             0 feeds
 ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┊─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─

   776021086  idx0/2  +0.17°  ─────────────────────►  idx0/3  a566b487c1  offside
                                       ┊                              1 feed

 ═════════════════════════════════════ ┊ ════════════════════════ CENTRELINE ══

   travel direction ─────────────────────────────────────────────────────────►
```

```
 node 13946726034            + = left turn · − = right turn · left-hand traffic
 BEFORE — v10 · the same defect mirrored: the link joins from the median side

   APPROACHES                          ┊  DESTINATION
   (arriving at the node)              ┊  way 776370584 · Perdana · 3 lanes
                                       ┊  (the node's only outgoing group)
 ═════════════════════════════════════ ┊ ═══════════════════════════════ KERB ══

   776021087  idx1/2  −0.03°  ─────────────────────►  idx2/3  e6db35d27f  nearside
     Perdana, 2 lanes, oneway          ┊                              1 feed

 ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┊─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─

        ✗  N O T H I N G   F E E D S   T H I S   L A N E  ✗
                                       ┊         idx1/3  37238b17cc  MIDDLE
                                       ┊                             0 feeds
 ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┊─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─

   776021087  idx0/2  −0.03°  ────┐    ┊
                                  ├───────────────►  idx0/3  ba662c1bbc  offside
   1530245742 idx0/1  −19.36° ────┘    ┊                    2 feeds — SHARED
     1-lane link · turn:lanes:forward=right

 ═════════════════════════════════════ ┊ ════════════════════════ CENTRELINE ══

   travel direction ─────────────────────────────────────────────────────────►
```

## Fundamental cause

Two things stacked, and the second is the one that matters.

**1. `_mapped_lane_index` cannot produce a middle index.** For a two-lane approach
onto a three-lane destination it computes `round(idx × (3−1) / (2−1))`, giving
`idx0 → 0` and `idx1 → 2`. Index 1 is unreachable for *any* input. The formula
preserves *relative* position across a lane-count change, which stretches a two-lane
block across the full width of a three-lane road instead of allocating lanes into it.

**2. Nothing allocated several approaches into one destination.**
`direct-osm-stage2-v10` had already established the principle — when the lane
arithmetic closes, allocate as a whole rather than one question at a time — but
`_balanced_approach_assignment` is scoped to a *single* approach, and asks whether
*its* lane count matches the total destination capacity. At a merge no single approach
can match: 2 ≠ 3 and 1 ≠ 3, even though **2 + 1 = 3**. So each approach picked its
target in ignorance of the other, and both the link and the road claimed the same lane.

This is v10's defect seen from the opposite end. There, one approach apportions its
lanes across several destinations; here, several approaches apportion themselves
across one. Both express the same fact: **no lane may vanish and no lane may be
shared.**

The visible geometry was the taper faithfully executing the wrong mapping. Because a
two-lane approach has fewer lanes than a three-lane destination, the taper bends *its*
ends onto whatever targets it was given — so the block splayed to 7 m apart to reach
idx0 and idx2, and the link was pulled onto the same point as the road's kerbside lane.

## Fix

`_balanced_merge_assignment()` in `generation.py`, the dual of
`_balanced_approach_assignment`. Both now share `_kerb_first_key()`, so the
driving-side rule is written once.

It fires only when all three conditions hold at a node:

- at least two approaches;
- every approach has exactly one non-reverse destination group, and it is the same
  group for all of them;
- their lane counts together equal that group's lane count.

The second condition is what keeps the allocation unambiguous — at an ordinary
crossroads each approach has several destinations, so nothing fires. When all three
hold, approaches are ordered by how far each turns toward the kerb to join, and the
destination's lanes are handed out from the kerbside inward, each approach taking its
own lanes kerb first.

The result is keyed by approach edge, so it slots into the existing
`approach_assignments` and **the consuming loop needed no change at all** — the lane
suppression, the `lane_transition_count_mismatch` guard and the taper already read it.
A merge and a diverge can never claim the same approach, because every approach of a
clean merge brings strictly fewer lanes than the destination holds.

What it deliberately does not do: it does not touch `_mapped_lane_index`, which still
cannot reach a middle index and still decides every oversubscribed approach. And it
adds no geometry code — with the mapping corrected the existing taper closed the gaps.

```
 AFTER — v11 · node 1928630009 · 1 + 2 = 3, so every lane is fed exactly once

   182502409  idx0/1  +14.81° ─────────────────────►  idx2/3  b63366201b  nearside
     link, joins from the kerb side  ┊                                  1 feed
   776021086  idx1/2  +0.17°  ─────────────────────►  idx1/3  eef18fbc84  MIDDLE
                                     ┊                                  1 feed
   776021086  idx0/2  +0.17°  ─────────────────────►  idx0/3  a566b487c1  offside
                                     ┊                                  1 feed

 AFTER — v11 · node 13946726034 · mirrored, the link joins from the median side

   776021087  idx1/2  −0.03°  ─────────────────────►  idx2/3  e6db35d27f  nearside
   776021087  idx0/2  −0.03°  ─────────────────────►  idx1/3  37238b17cc  MIDDLE
   1530245742 idx0/1  −19.36° ─────────────────────►  idx0/3  ba662c1bbc  offside
     link, joins from the median side ┊                          1 feed each

   no starved lane · no shared feed · every gap 0.00 m after the taper
```

## Verification

Junction-1 regenerated and byte-identical on a second run.

| | v10 | v11 |
| --- | ---: | ---: |
| destination lanes fed by nothing | 14 | **12** |
| points where lanes of two ways both end | 4 | **2** |
| link `72fdbea2a86f51e8` end vs `fa74351a73e87a68` end | identical | 3.50 m apart |
| `lane_transition_count_mismatch` | 28 | 22 |
| total findings | 552 | 546 |
| connectors / active / review | 110 / 74 / 31 | 110 / 74 / 31 |

**Exactly two source lanes changed destination** — `fa74351a73e87a68` idx1/2
(idx2/3 → idx1/3) and `6932800f61a604a6` idx0/2 (idx0/3 → idx1/3) — and two lanes
changed geometry. All six lane pairs at both nodes now meet at **0.00 m** after the
taper. `eef18fbc845691d7` and `37238b17cc969fd4` became fed; no lane became unfed.

Node `7251564392` is the third clean merge in the workspace and was already correct;
it is byte-identical, which was the check that mattered most.

Everything from v10 holds: ramp `4b348c220a9e0572` still meets `29ea5e0f4cd72da7` at
0.000 m, `4fd9a6aef6e49335 → 7c8d557add2cb505` at 0.000 m, 0 equal-count continuations
changed lane index, longest *active* connector still 5.96 m, restrictions, stop lines
and signals byte-identical, audit JS parses.

`uv run ruff check .` clean; `uv run pytest -q` 68 passed. The three new tests were
proved to bite by breaking the rule three ways — ignoring `driving_side`, handing out
destination lanes offside-first, and dropping the "only one destination" condition —
each failing exactly the test that should catch it.

Two notes on the numbers. The `lane_transition_count_mismatch` drop is **−6, not −2**:
the finding is now suppressed for all six approaches at all three clean merge nodes,
including the one whose mapping did not change. That follows from the policy — a
balanced merge apportions its lanes rather than losing them — but it is more than the
two nodes that were repaired. And of the 12 destination lanes still fed by nothing,
only `c0530c25fd9abf94` at node `1927184814` is a known defect; the remaining 11 have
not been examined individually and are not claimed to be legitimate.
