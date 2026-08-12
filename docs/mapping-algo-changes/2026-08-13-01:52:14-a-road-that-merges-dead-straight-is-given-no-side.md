# A road that merges dead straight is given no side, so it lands on index 0

- **Date:** 2026-08-13 01:52:14
- **Identified by:** Keith
- **Files changed:** src/osm_scenario/generation.py (`_merge_side`, the movement loop in
  `build_lane_model`)
- **Generator version:** direct-osm-stage2-v19 → direct-osm-stage2-v20

## Symptom

Keith, on the mosque map: "look at lane 23e3a806d4add9d0, way 935525161, it connects to
6338f91abdcf167, which causes it to cross 3 lanes, why does it not connect to
89e4496769eac71a, the left most lane on the way?"

Node `8010943717` is a merge — **4 lanes arrive, 3 leave**. Way `935525161` is a 1-lane
`secondary` (*Persiaran Perdana*), way `859423755` a 3-lane `secondary_link`, and both join
way `756118317`, a 3-lane `secondary` carrying the same street name. The single lane arrives
kerbside of every lane of the link and was sent to the link's *offside* lane.

```
 node 8010943717 · mosque · left-hand traffic · 4 lanes arrive, 3 leave
 + = left turn · − = right turn · indices run centre-out: idx0 hugs the centreline
 (offside), idx2 is kerbside (nearside) · lateral figures measured 40 m before the
 node, where the two carriageways are still apart; + is toward the kerb

  APPROACHES — 40 m out                ┊  DESTINATION — way 756118317, 3 lanes
                                       ┊  (the node's only outgoing group)
 ══════════════════════════════════════┊═════════════════════════════ KERB ══
                                       ┊
  935525161 idx0/1  −0.01°  +3.50 m ●  ┊  idx2/3  89e4496769eac71a   nearside
    1 lane · no turn:lanes         ╲   ┊          +7.00 m              1 feed
    KERBSIDE OF ALL THREE           ╲  ┊             ▲
                                     ╲ ┊             │
  859423755 idx2/3  −8.57°  +2.03 m ●──╳─────────────┘
    3-lane secondary_link             ╲┊
 ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ╲─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─
                                        ╲ ┊  idx1/3  eaba2db7fd11a1b2   MIDDLE
  859423755 idx1/3  −8.57°  −1.51 m ●────╳────────►   +3.50 m            1 feed
                                         ╲┊
 ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─╲─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─
                                           ╲
  859423755 idx0/3  −8.57°  −5.05 m ●───────┴────►   idx0/3  e6338f91abdcf167
                                       ┊            +0.00 m           offside
                                       ┊            2 feeds — SHARED
 ══════════════════════════════════════┊═══════════════════════ CENTRELINE ══

  travel direction ─────────────────────────────────────────────────────────►

  ╳ = measured, not sketched. The tapered centreline of 23e3a806d4add9d0 ran
      *inside* b673ef196c0d4c3c's lane surface for 15.37 m and inside
      359b5a7947c283b0's for 14.04 m, then merged onto e6338f91abdcf167 on top of
      65fba8b3b1712727. No lane was starved — the defect was the crossing.
```

After:

```
 node 8010943717 · AFTER · the merging lane lands on the side it arrived from

  935525161 idx0/1  −0.01°  +3.50 m ●────────────►  idx2/3  89e4496769eac71a
    kerbside of all three           ┊                       nearside · 2 feeds
                                    ┊                       SHARED with b673ef19
  859423755 idx2/3  −8.57°  +2.03 m ●────────────►  idx2/3  89e4496769eac71a
 ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─┊─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─
  859423755 idx1/3  −8.57°  −1.51 m ●────────────►  idx1/3  eaba2db7fd11a1b2
                                    ┊                       MIDDLE · 1 feed
 ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─┊─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─
  859423755 idx0/3  −8.57°  −5.05 m ●────────────►  idx0/3  e6338f91abdcf167
                                    ┊                       offside · 1 feed
  no stream crosses another · the through carriageway keeps its own mapping
```

## Fundamental cause

**The side of a merge was read from an angle threshold, which is a question about turns.**

Three things had to miss in order for index 0 to be the answer:

1. `_balanced_merge_assignment` requires the approaches' lanes to fill the destination
   exactly. 1 + 3 = 4 into 3, so it declined. `_balanced_approach_assignment` did fire for
   the link (3 lanes into 3) and gave it the correct identity mapping. The single lane got
   no allocation at all.
2. `movement_side` then answered `None`. The turn is **−0.01°**, under
   `side_movement_min_degrees` (10.0). That threshold asks *is this a turn?* — and this lane
   does not turn. It merges.
3. With no side, `_mapped_lane_index` skipped the proportional branch (`lane_count` is 1)
   and fell to its last line, `min(source.lane_index, target_count - 1)` = `min(0, 2)` = 0.

So **a single-lane approach always landed on index 0 whichever side of the carriageway it
arrived on**, and geometry then followed the mapping rather than correcting it:
`_merge_taper_plan` saw an `active`, `through` connector whose source has fewer lanes than
its target and pulled the lane's own end onto `e6338f91abdcf167`'s start, bending it over
`merge_taper_length_m`. That is why the *lane* was drawn crossing, not only the connector.

The deeper fault is that **a side was only ever derived from one road at a time.** Which
side of a carriageway a road joins from is a *comparison* between the roads meeting at the
node; it has an answer at any angle, including zero. The absolute angle is a proxy that
happens to work when the joining road is steep enough, which is why this survived: of the
ten single-lane through-merges onto a shared destination across both workspaces (7 mosque,
3 junction-1), nine join at **+13.36° to +47.01°** and clear the threshold. Node
`8010943717` is the only one under 10°, and the only one where the single lane was not
already sitting on index 0's line.

## Fix

`_merge_side(approach, feeding, targets, driving_side)` in `generation.py`. Where more than
one approach block feeds an outgoing group, it ranks the blocks with the existing
`_kerb_first_key` — the ordering `_balanced_merge_assignment` already deals by — and returns
`nearside` for a block kerbward of every other, `offside` for one centreward of every other,
and `None` for one in the middle, where nothing is deducible from the ordering alone.

The movement loop now reads three sources in order of how much they are trusted:

1. an explicit `turn:lanes` side, via `tagged_movement_side` — surveyed evidence still
   outranks everything inferred from geometry
2. `_merge_side`
3. `movement_side`, the absolute angle against `side_movement_min_degrees`

and `_mapped_lane_index` last. When the merge side decides, the `side_block` handed to
`_side_block_offset` is the **whole** approach block rather than `_tagged_side_block`'s
tagged subset — nothing tagged it, so all of it merges together and all of it is dealt
inward from the side it arrived on. It never runs for a continuation: a carriageway that
merely bends past the threshold is not a merge.

## Verification

Both workspaces regenerated. The property the rule exists for, counted on the real maps —
a *stream* being approach lane, connector and destination lane, as a car drives it:

| crossing merge-stream pairs | mosque | junction-1 |
|---|---|---|
| before | 4 | 2 |
| after | **1** | **1** |

The survivor is the same node in both, `474928793`, and it survives correctly: way `39619063`
is tagged `turn:lanes=right|right`, the tag outranks the ordering, and the tag-versus-geometry
disagreement stays in review rather than being resolved by moving the movement.

Fifteen movements moved in mosque and two in junction-1 — more than the one Keith named,
because the rule is general and because a merging block is now dealt inward instead of every
lane of it answering the same index. Twelve of the seventeen are `forbidden`, which are not
driven; three are `review_required`, which the lane graph already excludes. Only **two** are
`active`: Keith's, and `e9afaf6bb8e88e24` below. Nothing regressed:

| | mosque | junction-1 |
|---|---|---|
| lanes | 405 → 405 | 285 → 285 |
| connectors | 192 → 192 | 116 → 116 |
| blockers | 61 → 61 | 57 → 57 |
| lanes fed by nothing | 24 → 24 (none newly starved, none newly fed) | 19 → 19 |

One finding disappeared and should have: `lane_transition_count_mismatch` at node
`1928630021`, "proportional lane-order mapping collapses 2 approach lanes onto 1 destination
lane". `e9afaf6bb8e88e24` idx0/2 moved from idx2 to idx1, so the two lanes no longer share
one — the defect it reported ended, rather than the finding being silenced. Four
`restriction_effect_review` / `ambiguous_connector` blockers were re-issued under new
identifiers because they name connectors that moved; the blocker counts are unchanged.

At the node itself: feeds `idx0=2, idx1=1, idx2=1` → `idx0=1, idx1=1, idx2=2`; the lane's
overlap with `359b5a7947c283b0`'s surface 14.04 m → **0.00 m**; its worst vertex bend
unchanged at 4.60°; its taper now bends +3.50 m toward the kerb instead of −3.50 m away.

Drives swept over 3,000 random lane pairs per workspace, on the new models:

| | mosque | junction-1 |
|---|---|---|
| routes built | 360 → 360 | 548 → 548 |
| worst vertex turn | 17.79° → 17.79° | 50.92° → 50.92° |
| routes through the moved movement | 3, worst 6.42° → 3, worst **7.33°** |  |

No route was lost or gained and the worst turn is unchanged. (junction-1's 50.92° is on the
*preliminary* model, which still holds `review_required` connectors; it is identical before
and after and is not from this change. The suite's own gate sweeps the reviewed model.)

`uv run pytest` **348 passed** (up from 341: five unit tests for `_merge_side` and two
workspace-backed ones). `uv run ruff check` clean.
`test_no_two_roads_merging_into_one_carriageway_cross_each_other` was run against the
pre-fix models to prove it bites: **3 unexplained crossings in mosque, 1 in junction-1**.

**Not re-checked from MetaDrive**, and it could not be: both workspaces' Stage 4 models were
already two and three generator versions stale (v18 and v17 against a v19 preliminary) before
this change, so the ScenarioNet datasets in them were not built from current data either way.
Rebuilding needs Stage 3 re-run, which is Keith's.
