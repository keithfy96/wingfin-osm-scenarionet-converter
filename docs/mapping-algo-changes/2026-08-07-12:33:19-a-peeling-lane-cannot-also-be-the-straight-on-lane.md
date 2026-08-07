# A peeling lane was also kept as the straight-on lane

- **Date:** 2026-08-07 12:33:19
- **Identified by:** Keith
- **Files changed:** `src/osm_scenario/generation.py`
  (`_balanced_approach_assignment`, `_approach_blocks`, the per-lane candidate loop),
  `tests/unit/test_generation.py`
- **Generator version:** `direct-osm-stage2-v9` → `direct-osm-stage2-v10`
- **Commit:** `96ae00d`

## Symptom

Lane `4b348c220a9e0572` — the one-lane link peeling off Persiaran Perdana at node
`1928630015` — was drawn beginning **on the middle lane** of the three-lane
carriageway it leaves, not on the kerbside lane that feeds it. Keith: *"as you can
see when it enters the left turn, it starts from the mid line, when it should start
from the left most lane."*

It is a coordinate equality, not an impression. The link began at exactly the point
where Perdana's *middle* lane ends:

```
  Perdana idx1/3  a957ad53b4e56ebe  ends    (-79.62, -139.51)
  link    4b348c220a9e0572          starts  (-79.62, -139.51)     ← identical
  Perdana idx2/3  29ea5e0f4cd72da7  ends    (-79.99, -136.03)     ← where it belongs
```

The kerbside lane's gap to the link it feeds was **3.50 m**.

```
 node 1928630015             + = left turn · − = right turn · left-hand traffic
 lane indices run centre-out: idx0 hugs the centreline, idx(n−1) is kerbside
 BEFORE — v9

   APPROACH                            ┊  DESTINATIONS
   way 776022253 · Perdana · 3 lanes   ┊  way 182502392 · 1-lane link · +19.30°
   oneway · no turn:lanes              ┊  way 776022254 · Perdana on · 2 lanes
                                       ┊
 ═════════════════════════════════════ ┊ ═══════════════════════════════ KERB ══

   idx2/3  29ea5e0f4c  kerbside ──┬───── +19.30° ──►  idx0/1  4b348c220a  LINK
                                  │     ┊                             1 feed
     ✗  O N E   L A N E ,         │     ┊
        T W O   E X I T S  ✗      └───── +0.21°  ──►  idx1/2  1a98ee4e3c  nearside
                                        ┊                             1 feed
 ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─┊─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─

   idx1/3  a957ad53b4  middle ────┐     ┊
                                  ├───── +0.21°  ──►  idx0/2  092264ff00  offside
   idx0/3  c284f6b525  offside ───┘     ┊                    2 feeds — SHARED

 ═════════════════════════════════════ ┊ ════════════════════════ CENTRELINE ══

   travel direction ─────────────────────────────────────────────────────────►

 both movements off idx2/3 were review_required — duplicate through movements
```

## Fundamental cause

Not the link's geometry, and not the connector band drawn over it. The band is a
faithful lane-width ribbon over a Bézier whose only inputs are the two lane
endpoints and the OSM node; it can only be as right as the endpoints it is given.

The cause is that **destinations were asked one at a time.** The node loop ran
`for from_id in incoming:` and then `for targets in outgoing_groups.values():`, and
`_mapped_lane_index(source, len(targets), side)` answered a purely local question:
*which lane of this one destination does this one source lane feed?* Nothing ever
checked whether the approach as a whole balanced. So:

- the link, departing at +19.30°, is a nearside movement, and the side rule handed it
  the approach's kerbside lane — correctly, in isolation;
- the continuation, at +0.21°, independently ran the proportional mapping
  `round(lane_index × (2−1) / (3−1))`, which sends idx0→0, idx1→0 (Python's
  round-half-to-even on 0.5) and idx2→1;
- so idx2 was committed to *both* destinations, while idx0 and idx1 collided on one
  target.

Both through movements off idx2 were then flagged `ambiguous_connector` as duplicates
and set `review_required`, and because the merge taper only acts on `active`
connectors it skipped them — leaving the link's start untouched on the OSM node,
which is where the middle lane happens to end.

The evidence needed to decide this was present the whole time, in tags, not geometry:
`776022253` is tagged `lanes=3`, `776022254` `lanes=2`, `182502392` `lanes=1`, all
`oneway=yes`, all sharing node `1928630015`. **3 = 2 + 1.** The arithmetic closes, so
every lane has exactly one destination and there was nothing to infer. As Keith put
it: a lane that tapers off cannot also be a straight road.

## Fix

`_balanced_approach_assignment()` in `generation.py`. When an approach's destinations
hold exactly as many lanes as the approach brings — reverse destinations excluded,
since a U-turn consumes no capacity — the approach is allocated as a whole rather
than one destination at a time. Destinations are ordered by how far each turns toward
the kerb (sign taken from `manifest["driving_side"]`, not from screen geometry), and
the approach's lanes are dealt out from the kerbside inward.

The consuming loop gained a `continue` for a lane the allocation gives no place in a
group — that is what removes the duplicate — and the
`lane_transition_count_mismatch` finding is suppressed for a balanced approach, whose
lanes are apportioned rather than lost.

What it deliberately does not do: when the counts do **not** close, the approach is
oversubscribed, a lane genuinely serves more than one movement — one lane that may go
left *or* straight — and `_mapped_lane_index` still decides. Sharing is real there and
stays reported rather than resolved. No geometry code changed; with the duplicate gone
the existing merge taper closed the gap by itself.

```
 AFTER — v10: 3 = 2 + 1, so each lane has exactly one destination

   idx2/3  29ea5e0f4c  kerbside ──────── +19.30° ──►  idx0/1  4b348c220a  LINK
                                        ┊                             1 feed
   idx1/3  a957ad53b4  middle   ──────── +0.21°  ──►  idx1/2  1a98ee4e3c  nearside
                                        ┊                             1 feed
   idx0/3  c284f6b525  offside  ──────── +0.21°  ──►  idx0/2  092264ff00  offside
                                        ┊                             1 feed

   every connector active · no shared feed · no lane with two exits
```

## Verification

Junction-1 regenerated and byte-identical on a second run.

| | v9 | v10 |
| --- | ---: | ---: |
| link `4b348c220a9e0572` starts at | `(-79.62, -139.51)` | `(-79.99, -136.03)` |
| its gap to `29ea5e0f4cd72da7` | 3.50 m | **0.000 m** |
| connector `455b3e2663f79bb7` | `review_required` | `active` |
| duplicate `de9de52604c5baff` | present | gone |
| connectors / active / review | 112 / 72 / 35 | 110 / 74 / 31 |
| `ambiguous_connector` | 35 | 31 |
| `lane_transition_count_mismatch` | 32 | 28 |
| total findings | 560 | 552 |

**3 of 256 source lanes changed destination**; 4 lanes changed geometry. Nothing else
moved: restrictions, stop lines and signals byte-identical, 0 equal-count
continuations changed lane index, longest *active* connector unchanged at 5.96 m,
`4fd9a6aef6e49335 → 7c8d557add2cb505` still meets at 0.000 m.

`uv run ruff check .` clean; `uv run pytest -q` 65 passed. The three new tests were
proved to bite by breaking the rule three ways — ignoring `driving_side`, dealing
lanes offside-first, and never allocating — each failing exactly the test that should
catch it.

**Not fixed by this change**, and confirmed still present afterwards: node
`1927184814`, where `turn:lanes=right|right` on way `756118314` makes both its lanes
`offside` so `side_lane_index("offside", 2)` returns `0` for both and they collide.
That approach is oversubscribed, so this rule never reaches it.
