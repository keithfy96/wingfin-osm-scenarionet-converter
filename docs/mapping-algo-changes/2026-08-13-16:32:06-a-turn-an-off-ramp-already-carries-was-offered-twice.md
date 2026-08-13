# A turn an off-ramp already carries was offered at the junction as well

- **Date:** 2026-08-13 16:32:06
- **Identified by:** Keith
- **Files changed:** src/osm_scenario/generation.py (`_link_bypass_ends`, `_link_bypass_way`,
  `_link_bypassed_groups`, the `blocked_groups` read and the post-loop pass in
  `build_lane_model`), web/src/panel.ts, web/src/controls.ts
- **Generator version:** direct-osm-stage2-v21 → direct-osm-stage2-v22

## Symptom

Keith, on connector `caae6ef86d734e46`: "why is this allowed? ... this is actually a special
situation where a left turn shouldn't be allowed", and then, given the three cases the shape
has: **"these two are wrong because there is an offramp before it."**

The two are both Persiaran Perdana:

| approach | off-ramp | rejoins | junction turn |
|---|---|---|---|
| way `1173001826` | `182502423`, `secondary_link`, 54 m, leaves 32.6 m early | Persiaran Meranti | `caae6ef86d734e46` +80.70° |
| way `776022254` | `182502392`, `secondary_link`, leaves 36.3 m early | Persiaran Kenanga | `12de93febd511aa8` +89.15° |

```
 node 7251588324 · Perdana × Meranti · left-hand traffic · 4 lanes in, 4 lanes out
 + = left turn · − = right turn · lane indices run centre-out: idx0 hugs the
 centreline (offside), idx(n−1) is kerbside (nearside)

     way 182502423, the off-ramp, leaves THIS SAME LANE 32.6 m back at node
     1927184850 (+17.61°, connector b84e34c5f92aebc0) and comes out at node
     7251564392 — which is where way 777160374's edge ends
              │
   APPROACHES │                          ┊  DESTINATION A — way 777160373
              │                          ┊  Persiaran Perdana, 3 lanes
 ═════════════╪══════════════════════════┊═════════════════════════ KERB ══
              │                          ┊
  1173001826  idx2/3  7c442504fa5d1766   ┊  idx2/3  cfec0ef9ebc74d15 nearside
    Persiaran Perdana, 3 lanes           ┊                             1 feed
    no turn:lanes                        ┊
      ├─ through  −3.62° ────────────────┊───────►
      └─ left    +80.70° ──┐  caae6ef86d734e46  ✗ THE SECOND OFFER
                           │             ┊
 ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ │─ ─ ─ ─ ─ ─ ─┊─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─
  1173001826  idx1/3  c388e30ffb2cf461   ┊  idx1/3  0175f3f649a546bd  MIDDLE
      └─ through  −3.62° ────────────────┊───────►                    1 feed
 ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ │─ ─ ─ ─ ─ ─ ─┊─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─
  1173001826  idx0/3  0b0e7cdbfeb471c7   ┊  idx0/3  3e85167a37bb09f6 offside
      └─ through  −3.62° ────────────────┊───────►                    1 feed
                           │             ┊
 ══════════════════════════╪═════════════┊═══════════════════ CENTRELINE ══
                           │
  777159293  idx0/1  a6ab945d47c56404 — Meranti crossing, 1 lane, 2.0 m long
    (the stub between Perdana's two carriageways)
      ├─ right  −90.75°  ✗ FORBIDDEN — 09326c75a32bd6a9
      │           rel 10761640 no_u_turn: 777160375 → via 777159293 → 777160373
      └─ through −6.44° ──┐
                          │
                          ▼
   DESTINATION B — way 777160374, Persiaran Meranti, 1 lane
        idx0/1  a4faee911623a708   ·   2 feeds before, 1 after
                   (the Meranti through, and — until now — the Perdana left turn)

   travel direction, Perdana ──────────────────────────────────────────────►
   nothing here was starved; the defect was the second offer, not a gap
```

## Fundamental cause

**Nothing asserted these turns; the generator's default did, and the evidence against them
had never been read.** At a decision node every non-reverse outgoing group is reachable from
the approach, and only evidence removes a movement. Neither extract contains a single `left`
in any `turn:lanes` value, and no restriction relation names either movement — so both were
generated and nothing questioned them. **No finding of any rule named either connector.**

The kerb-side rule did act, and correctly: `movement_side` → `nearside`,
`side_lane_index("nearside", 3)` = 2, inside `_side_filtered_candidates`, so idx0 and idx1
lost the turn and idx2 kept it. That rule only ever chooses *which lane* turns. Across the two
workspaces it decides 85 left-going movements, 31 on a multi-lane approach where it removed
something, and Keith reports every one of those correct.

The evidence it never read is the off-ramp: a `_link` way leaving the approach *before* the
junction and coming out where the turn lands. Physically the ramp is islanded off from the
through lanes, so the junction cannot carry the turn as well. The map says so and the reading
had never been written.

**It is not one node's quirk, and the map corroborates it.** Both extracts contain exactly
three such ramps, and all three have a duplicating junction turn — the third,
`191861354` into Persiaran Kenanga at node `474922037`, is *already* forbidden by a surveyed
restriction. Somebody looked at that junction and said what Keith said.

## Fix

`_link_bypass_ends(snapshot)` records, for every `_link` way, where the ramp meets the network
again. `_link_bypass_way(source, target, ...)` names the ramp that already serves a movement,
and it fires only when **both** ends match — the ramp leaves the node the approach's own edge
*starts* at, and comes out at the node the destination's edge *ends* at — **and** the movement
carries a side.

Three readings were measured and rejected, and each guard is one of them:

- **Matching the ramp's end against any node of the destination *way*** flags **22**
  connectors in mosque against the tight test's 5, six of them carriageways carrying straight
  on at +2.45° and +5.07°. A ramp replaces a turn, never a road going ahead — hence the side
  test as well as the node test.
- **Keeping only the chain's final node** loses the Kenanga case entirely. `182502392` comes
  out at `1928630157` and a *different* ramp, `182502409`, starts there; walking through reads
  `1928630009`, which is where the second ramp goes. Nothing distinguishes one ramp mapped as
  two ways from two ramps in series, so **every way boundary along the chain is recorded**.
- **Removing without a no-stranding guard.** A ramp says a turn is taken elsewhere, never that
  a lane has no exit, so a movement that is the lane's last one stays. Read after the
  restrictions resolve, because a restriction may have taken the exit that would have counted.

**The ramp is read before the lanes are dealt out**, which is the v21 lesson applying again
(`2026-08-13-04:42:58-...`): `_balanced_approach_assignment` and `_balanced_merge_assignment`
cannot be right about where a lane lands while counting a destination that is about to go.
`_link_bypassed_groups` joins `_restricted_groups` in `blocked_groups`, with the same
carve-outs and for the same reasons — only the allocation is blinded, the movements are still
generated and keep their ids, and nothing is hidden where that would leave the approach no
destination at all.

The status becomes `forbidden`, and a **warning**, `movement_served_by_link_bypass`, records
the connector, the ramp, the node it leaves and how far before the junction. A warning, not a
blocker: the reviewer is being told what the generator did, not asked to confirm something
nobody disputes — the same standing as `restriction_enforced_leg`. Only blockers gate export.

## Verification

Both workspaces regenerated. **Exactly two connectors changed, in both:**

| | mosque | junction-1 |
|---|---|---|
| `caae6ef86d734e46` | active → **forbidden** | active → **forbidden** |
| `12de93febd511aa8` | active → **forbidden** | active → **forbidden** |
| lanes | 405 → 405 | 285 → 285 |
| connectors | 200 → 200 (0 lost, 0 gained) | 116 → 116 (0 lost, 0 gained) |
| connectors re-targeted | 0 | 0 |
| blockers | 63 → 63 | 57 → 57 |
| warnings | 163 → 165 | 85 → 87 |

The two new warnings in each are the record of the two removals, and the third case at
`474922037` is untouched — the restriction still outranks this reading and forbids it first.

**One lane is newly starved, and it was already half starved before.** Kenanga
`5fe50f735e40d7c2`, the block `474929865 → 1928630157`, had two ways in: the removed left turn,
and `7046b111f705c203` — which is `review_required` on `competing_movements, borderline_angle`
and so was already an open blocker awaiting Keith. Accepting it in Stage 3 feeds the lane.
Nothing else changed: no other lane newly starved, none newly fed, in either workspace.

**Route sweep, 3,000 random lane pairs per workspace**, before against after:

| | mosque | junction-1 |
|---|---|---|
| routes built | 358 → 357 | 554 → 547 |
| worst per-vertex turn | 35.38° → 35.38° | 50.92° → 50.92° |
| routes gained | 0 | 0 |

All eight losses were traced and all eight are the correct answer:

- **five** reached the Kenanga block above, which now has no *active* feed
- **two** ended **on** the bypassed block itself — Meranti `a4faee911623a708`, the 26.8 m
  between the junction and where the ramp merges. A Perdana car never drives that block now,
  which is what a slip road means: it joins Meranti *beyond* it
- **one** started in Perdana's middle lane on the last 19.1 m edge, after the ramp had already
  left. Too late to take it, on the map and on the road

`uv run pytest` **367 passed**, eight of them new; `uv run ruff check` clean;
`npm run typecheck` and `npm test` (139) clean in `web/`. The new fixture
`tests/fixtures/osm/link-bypass.osm` carries four cases — the ramp rejoining, the ramp coming
out elsewhere, a carriageway carrying straight on, and a lane the removal would strand — plus
two unit tests on the chain walk covering the fork and the two-ramps-in-series reading.

Both Stage 4 models were already stale (mosque v18, junction-1 v17, against a v21 preliminary),
so the version bump costs no live review.
