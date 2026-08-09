# A side said where every lane went, not where the block started

- **Date:** 2026-08-09 16:44:44
- **Identified by:** Keith
- **Files changed:** src/osm_scenario/generation.py (`_mapped_lane_index`,
  `_side_block_offset`, `_tagged_side_block`), src/osm_scenario/topology.py
  (`tagged_movement_side`, `movement_side`)
- **Generator version:** direct-osm-stage2-v16 → direct-osm-stage2-v17

## Symptom

Keith, looking at the blockers at node 1927184814: *"it seems like the movement is a
teleport from one lane to another"*, and then, on being told the tag was at fault:
*"39619063 should be a right turn, both lanes can turn right into the lane on the right,
that's why it's tagged `right|right`."*

He was right. Ways `756118314` and `39619063` are the same road, Persiaran Meranti,
meeting end-to-end. Both lanes do turn right — at the far end of `39619063`, node
474928793 — and the tag is repeated on the approach because the painted arrows run its
length. Nothing about the tagging is wrong.

```
 node 1927184814             + = left turn · − = right turn · left-hand traffic
 lane indices run centre-out: idx0 hugs the centreline, idx(n−1) is kerbside

   APPROACHES                       ┊  DESTINATION — way 39619063, 2 lanes
   (arriving at the node)           ┊  Persiaran Meranti, next segment of the same road
                                    ┊
 ═══════════════════════════════════┊═══════════════════════════════ KERB ══
                                    ┊
   756118314 idx1/2  +5.8° ──┐      ┊        idx1/2  c0530c25fd9abf94  nearside
     Persiaran Meranti       │      ┊                                   0 feeds
     turn:lanes=right|right  │      ┊
     ends 0.18 m short of ┄┄┄│┄┄┄┄┄┄┄┄┄┄┄►  ✗ N O T H I N G  F E E D S  I T ✗
     this lane — but is      │      ┊
     mapped 3.50 m across    │      ┊
                             │      ┊
 ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ │ ─ ─ ─┊─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─
                             │      ┊
   756118314 idx0/2  +5.8° ──┤      ┊
     turn:lanes=right|right  ├──────────────►  idx0/2  86602054f094204a  offside
   777160373 idx0/3 −88.6° ──┘      ┊                     3 feeds — SHARED
     Persiaran Perdana, 3 lanes     ┊
     no turn:lanes                  ┊
                                    ┊
 ═══════════════════════════════════┊════════════════════════ CENTRELINE ══

   travel direction ──────────────────────────────────────────────────────►
```

The lanes line up one for one — idx0's centreline ends **0.18 m** from the destination's
idx0, idx1's **0.18 m** from its idx1 — and the mapping still threw idx1 **3.50 m**
across, exactly one lane width, onto a lane already fed. `c0530c25fd9abf94` was fed by
nothing.

## Fundamental cause

`_mapped_lane_index` answered `side_lane_index(side, target_count)` whenever a side was
known: **one index, no matter which lane of the approach was asking.**

A side is a property of the movement, not of the lane. `turn:lanes=right|right` gives
both lanes `turn_permissions=['right']`; `movement_side()` treats an explicit tag as
decisive and never consults the geometry, so both came back `offside`; and
`side_lane_index("offside", 2)` is `0` for both. Two lanes, one answer.

That reading of "side" is right for a single lane peeling off toward the kerb and wrong
for a block. What a side actually fixes is **where the block starts** — its leading
lane — and the lanes behind it follow inward, keeping their lateral order. Reading it as
"where every lane goes" hands several streams of traffic one lane and starves the lane
beside it.

Two conditions had to hold together for it to bite, which is why only two places in
junction-1 do: several lanes of one approach must claim the same side, **and** the
balanced rules must have declined the approach. A clean multi-lane turn never reaches
`_mapped_lane_index` at all — node 7251588323 sends a two-lane right turn into a
two-lane destination and `_balanced_approach_assignment` deals it correctly. Only an
oversubscribed approach falls through to the proportional mapping, and both Meranti
segments are oversubscribed.

**Not the cause, though it looks like one:** the `turn:lanes` tag. The two
`turn_permission_geometry_conflict` blockers at that node are correct and are kept. The
tag names a right turn that is not available there, and that is exactly the kind of
disagreement a reviewer must be shown. Keith's instruction: *"if there is explicit
information given about turn lanes, it should be adhered to even if it geometrically
doesn't make sense, just raise it as a review."* An earlier draft of this fix would have
suppressed both blockers by reclassifying the movement as a continuation; it was
withdrawn for that reason.

## Fix

`_mapped_lane_index` takes an optional `side_block`. Where a side is known, the index is
the side's own index plus the lane's position within the block, stepping inward —
`+1` per lane from the centreline for an offside block, `−1` per lane from the kerb for a
nearside one — clamped to the destination. `_side_block_offset` orders the block from its
side inward so the leading lane is always offset 0.

`_tagged_side_block` builds the block from **only** the lanes an explicit `turn:lanes`
puts on that side. Where an approach carries no tag, `_side_filtered_candidates` already
leaves the side-most lane alone with the movement, so treating its neighbours as a block
would deal them into lanes they never reach. The tag is read through
`tagged_movement_side`, extracted from `movement_side` in `topology.py` and now shared by
both, so deciding a side and grouping the lanes that share it cannot drift apart.

**What it deliberately does not do.** It does not change permission filtering, does not
touch `side_lane_index` or its use in `_side_filtered_candidates`, and does not remove a
single finding that reports a tag disagreeing with the geometry. Where the destination
has no room the clamp still collapses the block, and `lane_transition_count_mismatch`
still reports the sharing.

## Verification

`workspaces/junction-1`, source `784b81ee…` matching `source/manifest.json`.

| | v16 | v17 |
|---|---:|---:|
| lanes | 281 | 281 |
| connectors | 111 | 111 |
| connector status | 77 / 29 / 5 | 77 / 29 / 5 |
| direct continuations | 208 | 208 |
| **blockers** | **57** | **57** |
| `lane_transition_count_mismatch` | 1 | **0** |
| findings | 140 | 139 |
| lanes fed by nothing | 22 | **21** |

**Exactly two connectors changed their target**, and they are the two predicted before
any code was written:

| node | approach | was | now |
|---|---|---|---|
| 1927184814 | way 756118314 idx1/2 | idx0 (through, active) | **idx1** |
| 474928793 | way 39619063 idx1/2 | idx0 (right, forbidden) | **idx1** |

Every other connector is identical field for field. **Exactly three lanes changed**, and
only in `entry_lanes` / `exit_lanes` — no geometry moved. `c0530c25fd9abf94` is now fed
by `027a3ef89e3e7b88`, index for index. No lane became newly starved.

Both `turn_permission_geometry_conflict` blockers at 1927184814 survive, as intended.
One changed identifier (`586408b4…` → `31f7cfe4…`) because `affected_feature_ids` names
the restored movement's target, which moved from `86602054f094204a` to
`c0530c25fd9abf94`. Its rule, severity, node, source lane, rejected and restored
movements are unchanged; it now names the lane the movement actually reaches. Finding
counts per rule and per severity are otherwise identical.

Two consecutive `generate-map` runs produce a byte-identical `preliminary.json`.

`uv run pytest` 103 passed (11 new); `uv run ruff check` clean; `npx tsc --noEmit` clean;
`npx vitest run` 54 passed; bundle rebuilt; review view regenerated at
`payload_version: 2`.

**A coverage gap worth recording.** junction-1 contains **no** multi-lane nearside block
— zero left-family groups with more than one feeding lane — so regenerating the workspace
cannot exercise the nearside branch at all. It is carried entirely by unit tests
(`test_a_nearside_block_is_dealt_inward_from_the_kerb`, and the overflow case), which are
the only thing standing behind the left-turn path until a workspace contains one.
