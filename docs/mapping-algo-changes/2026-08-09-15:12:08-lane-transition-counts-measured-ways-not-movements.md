# `lane_transition_count_mismatch` compared two roads' widths, not the movement

- **Date:** 2026-08-09 15:12:08
- **Identified by:** Keith
- **Files changed:** src/osm_scenario/generation.py (`_lane_collapse_findings`,
  `generate_lane_model`)
- **Generator version:** direct-osm-stage2-v15 → direct-osm-stage2-v16

## Symptom

Keith selected the first `lane_transition_count_mismatch` in the Stage 3 queue —
node 474929865, affecting `092264ff002b1f4c` and `5fe50f735e40d7c2`, proposed
`{incoming_lane_count: 2, outgoing_lane_count: 1}` — and asked three things: does
this mean two lanes become one; why does it say two incoming lanes when only one is
highlighted; and why is the turn it depicts, which is not a valid turn, not offered
for review.

None of the three had an innocent answer.

```
 node 474929865              + = left turn · − = right turn · left-hand traffic
 lane indices run centre-out: idx0 hugs the centreline, idx(n−1) is kerbside

   APPROACHES                        ┊  DESTINATIONS — three separate groups
   (arriving at the node)            ┊
                                     ┊
 ════════════════════════════════════┊═══════════════════════════════ KERB ══
                                     ┊
   776022254  idx1/2   +89.0° ───────────►  idx0/1  5fe50f735e40d7c2  single
     Persiaran Perdana                ┊       way 776021084 Persiaran Kenanga
     oneway, 2 lanes, no turn:lanes   ┊       lanes=2, lanes:backward=1
     kerbside lane makes the left     ┊       1 feed · active
                                     ┊
        ✗  THE FINDING NAMED  776022254 idx0/2 → 5fe50f735e40d7c2  ✗
        ✗  no such movement exists: idx0 never enters this way     ✗
                                     ┊
 ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┊─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─
                                     ┊
   776022254  idx1/2   +4.6°  ───────────►  idx1/2  fa74351a73e87a68  nearside
                                     ┊       way 776021086 · 1 feed · active
                                     ┊
   776022254  idx0/2   +4.6°  ──┐    ┊
                                ├────────►  idx0/2  9d1142bdbe10a335  offside
   1530245743 idx0/1   −122.4° ─┘    ┊       way 776021086 · 2 feeds — SHARED
     joining road                     ┊       one active, one review
                                     ┊
 ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┊─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─
                                     ┊
   776022254  idx0/2   −133.5° ─┐    ┊
                                ├────────►  idx0/1  d2e454f7d7517d46  single
   776021084  idx0/1   −42.4°  ─┘    ┊       way 1530245742 · 2 feeds — SHARED
     far side of Persiaran Kenanga    ┊       one review, one active
                                     ┊
 ════════════════════════════════════┊════════════════════════ CENTRELINE ══

   travel direction ──────────────────────────────────────────────────────►
```

Persiaran Perdana keeps both lanes through the node into way 776021086. Its kerbside
lane makes a left turn into a *different* road, Persiaran Kenanga, whose backward
carriageway is one lane wide. The "2 → 1" compared one road's width against another
road's, across a turn. The lane the finding named, `092264ff002b1f4c`, has no movement
into the destination at all.

Measured across all 19 of these findings in junction-1, counting only surviving
connectors and continuations:

| what the movements actually did | findings |
|---|---:|
| no movement exists at all | 2 |
| one approach lane feeds one destination lane | 16 |
| two approach lanes collapse onto one destination lane | 1 |

Eighteen of nineteen described no lane-count change.

## Fundamental cause

Three defects stacked in one emit block, `generation.py:1411–1435` at v15.

**The counts came from the ways, the warning was about the lanes.** The trigger was
`source.lane_count != len(targets)` — the approach way's tagged lane count against the
destination group's size. Those are two different carriageways whenever the movement is
a turn, and their widths are unrelated. What the proportional mapping can actually get
wrong is narrower: `_mapped_lane_index` sending two lanes to the same target, so two
streams of traffic are handed one lane. The rule never measured that.

**It fired before the movement was known to exist.** The finding was appended as soon as
a target was picked — upstream of permission filtering, reverse filtering,
`_side_filtered_candidates`, the ambiguity pass and the restriction pass. Candidates
those passes went on to discard still produced a finding, so two of the nineteen
described transitions with no movement in the finished model in either direction.

**It named one arbitrary lane of the approach.** `affected_feature_ids` was
`[source, *targets]`, deduped on `(node, source_way, target_way)`, so the first source
lane through the loop claimed the record and its siblings were never named. That is why
it read `incoming_lane_count: 2` while highlighting one lane, and why in 10 of 19
findings the named lane had no movement into the named destination. The count and the
highlight could not agree, because they came from different things.

## Fix

`_lane_collapse_findings(connectors, continuation_links, lane_lookup)`, called after the
connector loop, once every movement has been filtered, restored, side-resolved and
either kept or forbidden.

Links are grouped by `(node, approach edge, destination edge)`. Both kinds count:
connectors whose status is `active` or `review_required`, and direct continuations —
which never become movement candidates, so a genuinely narrowing carriageway would be
invisible if only connectors were read. A `forbidden` connector is excluded: the
movement does not exist, and counting it would show a reviewer two lanes with nothing
between them. Within a group the finding is emitted only when the distinct approach
lanes outnumber the distinct destination lanes they reach. `affected_feature_ids` holds
the feeders followed by the landed lanes, so the highlight and the counts are the same
measurement. `proposed_value` carries `incoming_lane_count`, `outgoing_lane_count` and
`destination_lane_count`.

Because one approach lane yields at most one target per group, `landed > feeders` cannot
occur; the condition needs no other branch. `continuation_links` is accumulated where
the continuation is already recorded onto `entry_lanes`/`exit_lanes`.

**What this deliberately does not do.** It says nothing about a destination lane that no
approach reaches. Lane starvation is a separate defect with its own entries, and
junction-1 still has 12 such lanes of which 11 are undiagnosed. No movement, angle,
status or geometry changes: this is a reporting change only.

## Verification

`workspaces/junction-1`, source `784b81ee…` matching `source/manifest.json`.

Model, before → after:

| | v15 | v16 |
|---|---:|---:|
| lanes | identical | identical |
| connectors | identical | identical |
| stop lines, signals, restrictions | identical | identical |
| findings of all other rules | 139 | 139, byte-identical |
| `lane_transition_count_mismatch` | 19 | **1** |
| total findings | 158 | **140** |

`lanes` and `connectors` compare byte-for-byte between the two runs, which is the
guarantee that matters here — the movement pass was not to be touched. The 139 findings
of other rules are identical objects, identifiers and checksums included.

The one survivor is `58517fe255e27068`, node 1927184814, naming `027a3ef89e3e7b88` and
`1831f85bcfe6bd84` (both lanes of way 756118314) landing on `86602054f094204a`
(way 39619063 idx0/2), `destination_lane_count: 2`. That is exactly the defect
`CLAUDE.md` records as still open: `turn:lanes=right|right` labels both lanes `offside`,
`side_lane_index("offside", 2)` returns `0` for both, and `39619063` idx1/2
`c0530c25fd9abf94` is left with zero entries. The rule now reports it; the mapping still
collides.

Audit assertions over the emitted findings: every named lane is an end of a real link at
its node (0 orphans), `len(affected_feature_ids) == incoming + outgoing`, and
`incoming > outgoing`. Two consecutive `generate-map` runs produce a byte-identical
`preliminary.json`.

`uv run pytest` 92 passed (4 new); `uv run ruff check` clean; `npx tsc --noEmit` clean;
`npx vitest run` 50 passed; bundle rebuilt. The regenerated review payload carries 140
findings and all three of the survivor's lanes in `geometry_ids`, so the highlight shows
two approach lanes when the finding says two.

**Prediction that was wrong.** The plan predicted the survivor would be at node
474928793. The pre-change audit grouped links by each finding's *own* destination edge,
so it could only see collapses that an existing finding already named — and the real
collapse is upstream at 1927184814, where the v15 finding pointed at a phantom pair. The
new code enumerates every surviving link instead of trusting the old finding set.
