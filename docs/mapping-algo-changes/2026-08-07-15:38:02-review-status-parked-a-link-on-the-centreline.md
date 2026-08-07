# Connector review status parked a link on the road's centreline

- **Date:** 2026-08-07 15:38:02
- **Identified by:** Keith
- **Files changed:** `src/osm_scenario/generation.py` (`_merge_taper_plan`,
  `GENERATOR_VERSION`), `tests/unit/test_generation.py`
- **Generator version:** `direct-osm-stage2-v11` → `direct-osm-stage2-v12`
- **Commit:** uncommitted at the time of writing

## Symptom

Lane `cab3280515d4b733` — the one-lane `secondary_link` on way `182502406` leaving
Persiaran Meranti at node `1928630073` — was drawn beginning on the **centreline** of
the two-lane road it leaves, not on the kerbside lane that feeds it. Keith: *"you can
see where it enters the lane, but for some reason the polygon is attached to the
centerline, not the kerbside lane, i thought this was fixed."*

It is a coordinate equality. The link began at the OSM node itself, which is the exact
midpoint of the two approach lane ends:

```
  approach idx0/2  433fa8957650a8dd  ends    (83.20, -146.10)
  approach idx1/2  b3f6c9d7ed8c200f  ends    (79.78, -146.87)   ← the lane that feeds it
  their midpoint                             (81.49, -146.49)
  OSM node 1928630073                        (81.49, -146.49)   ← identical
  link     cab3280515d4b733          starts  (81.49, -146.49)   ← identical
```

The link's gap to the only lane that feeds it was **1.750 m** — half a lane width.

```
 node 1928630073            + = left turn · − = right turn · left-hand traffic
 lane indices run centre-out: idx0 hugs the centreline, idx(n−1) is kerbside
 BEFORE — v11

   APPROACH                            ┊  DESTINATIONS
   way 756118316 · tertiary · 2 lanes  ┊  way 182502406 · secondary_link · 1 lane
   no turn:lanes                       ┊  way 756118314 · tertiary       · 2 lanes
                                       ┊
 ═════════════════════════════════════ ┊ ═══════════════════════════════ KERB ══
                                       ┊
                            ┌─ +16.98° ───►  idx0/1  cab3280515  LINK
                            │          ┊                   1 feed · review_required
   idx1/2  b3f6c9d7ed       │          ┊    ✗ starts (81.49, -146.49) — the OSM
   kerbside ────────────────┤          ┊      node, i.e. the CENTRELINE of the
   ends (79.78, -146.87)    │          ┊      2-lane road it leaves: 1.750 m from
     ONE LANE, TWO EXITS    │          ┊      idx1, the only lane that feeds it ✗
                            │          ┊
                            └─ +0.77°  ───►  idx1/2  027a3ef89e  nearside
                                       ┊                   1 feed · review_required
                                       ┊      starts (79.79, -146.89) — 0.02 m
 ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┊─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─

   idx0/2  433fa89576  offside ─ +0.77° ───►  idx0/2  1831f85bcf  offside
   ends (83.20, -146.10)               ┊                   1 feed · active
                                       ┊      starts (83.19, -146.08) — 0.02 m
 ═════════════════════════════════════ ┊ ════════════════════════ CENTRELINE ══

   travel direction ─────────────────────────────────────────────────────────►

   no starved lane — every destination lane has exactly one feed. the mapping is
   right and the geometry is wrong, which is the opposite of v10 and v11.
```

## Fundamental cause

The mapping at this node is correct and the ambiguity in it is real. Two lanes arrive
and three lanes of destination capacity leave — `756118314` keeps two lanes and the
link takes one — so the approach is **oversubscribed** and the kerbside lane genuinely
goes straight *or* bears left. Both of its movements classify as `through`, so
`family_counts["through"] == 2` marked them ambiguous and both connectors became
`review_required`, which is the honest answer.

The defect is that `_merge_taper_plan` then refused to draw them:

```python
if connector.status != "active" or connector.movement != "through":
    continue
```

**Status was standing in for a question it does not answer.** A connector's status
answers *whether the movement is right*. Where a lane's free end sits is a different
question, and the junction node is the one answer that is never right — it is where
OSM ends a way, on the other road's centreline, which this function's own docstring
already said a lane must not stop on. Coupling the two meant that a movement needing
review was left parked on a centreline until someone reviewed it, and since the
ambiguity here is legitimate and will never be resolved by regeneration, *until
someone reviewed it* meant *for ever*.

**This is not the defect v10 fixed, although it looks identical on the map.** v10
removed a *duplicate mapping* that had made a connector unreviewable; once the
duplicate was gone the connector went active and the existing taper closed the gap by
itself. It never touched the taper. So v10 fixed one cause of the symptom and left the
other — a connector that is under review for a good reason — completely untreated.

Replaying the taper's own candidate test across junction-1 with the status guard
removed found **exactly one** connector qualifying, moving **one** endpoint, with no
competing candidate. The defect was rare, not systemic, which is why it survived two
generator versions aimed at the same symptom.

## Fix

`_merge_taper_plan()` in `generation.py` now skips only `forbidden` connectors instead
of everything that is not `active`. A prohibited movement does not exist, so it may not
drag geometry; a movement awaiting review does exist, and has to reach the lane it
enters.

Widening the input widens the chance that two candidates want the same lane end in two
different places, which `plan.setdefault` had been settling by whichever connector ID
sorted first. So the plan now ranks candidates — an active movement outranks one
awaiting review — and where the best rank available for an endpoint still names two
places, the endpoint is left where OSM put it rather than snapped arbitrarily. No
endpoint in junction-1 is contested, so this changes no output; it stops the widened
rule from creating a silent tie-break.

Nothing else changed. `_tapered_line` does the blend as before, and the connector
rebuild that already followed a tapered lane redrew the link's band without being
asked. Critically, the constraint stated above the taper still holds: movement, angle
and status are decided from untapered geometry and are not revisited, so this fix does
**not** promote the connector to active or alter its `+16.98°`.

```
 AFTER — v12 · node 1928630073 · the link starts on the lane that feeds it

   idx1/2  b3f6c9d7ed  kerbside ─ +16.98° ──►  idx0/1  cab3280515  LINK
   ends (79.78, -146.87)               ┊       starts (79.78, -146.87) — 0.000 m
                                       ┊       still review_required at +16.98°
   idx1/2  b3f6c9d7ed  kerbside ─ +0.77° ───►  idx1/2  027a3ef89e  nearside
   idx0/2  433fa89576  offside  ─ +0.77° ───►  idx0/2  1831f85bcf  offside

   the lane still serves two movements and both are still flagged for review —
   what changed is only where the link is drawn, not what it is allowed to do.
```

## Verification

Junction-1 regenerated and byte-identical on a second run.

| | v11 | v12 |
| --- | ---: | ---: |
| link `cab3280515d4b733` starts at | `(81.49, -146.49)` | `(79.78, -146.87)` |
| its gap to `b3f6c9d7ed8c200f` | 1.750 m | **0.000 m** |
| its distance from node `1928630073` | 0.00 m | 1.75 m |
| `merge_tapers` | 18 | **19** |
| connectors / active / review | 111 / 77 / 29 | 111 / 77 / 29 |
| total findings | 541 | 541 |
| destination lanes fed by nothing | 12 | 12 |

**Exactly one lane changed geometry** and one connector was redrawn — the link and its
own band. Lane and connector identifier sets are unchanged; no lane changed any
non-geometric field; no connector changed status, movement, angle, source or target;
and `findings`, `restrictions`, `signals` and `stop_lines` are identical objects.
Connector `8af3358680e52ce6` is still `review_required` at `+16.98°`, `06deaca4e54a9bed`
still `review_required`, `b114c0fafb51bbb4` still `active`.

Replaying the candidate test over the regenerated model: **0** lane endpoints left
short of the lane they meet, and **0** endpoints wanted in two places.

`uv run ruff check` clean; `uv run pytest -q` 71 passed. The two new tests were proved
to bite by breaking the rule three ways — restoring the active-only guard, dropping the
contested-endpoint guard, and dropping the active-outranks-review precedence — each
failing exactly the test that should catch it.

One observation outside this change, found while verifying it. The generation
fingerprint is not stable across re-running Stage 1 on an identical source: osmnx
writes a `created_date` graph attribute into the projected GraphML, so every `fetch`
produces different bytes, a different `projected_graphml` checksum and therefore a
different fingerprint. Rule 6 binds review decisions to that fingerprint, so a re-fetch
that changes nothing still invalidates them.
