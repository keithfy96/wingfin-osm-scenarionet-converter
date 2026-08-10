# A signal at the edge of the extract governs the lanes it releases

- **Date:** 2026-08-10 22:05:43
- **Identified by:** Keith
- **Files changed:** `src/osm_scenario/generation.py` (`_signal_association`, the signal
  block and the stop-line loop in `build_lane_model`, `ReviewOverrides`),
  `src/osm_scenario/osm_source.py` (`way_terminus_nodes`),
  `src/osm_scenario/validation.py` (`_way_terminus_nodes`),
  `src/osm_scenario/apply_review.py` (`_overrides_from`, `_regenerate`)
- **Generator version:** `direct-osm-stage2-v17` → `direct-osm-stage2-v17` (unchanged;
  see Verification)

## Symptom

`junction-1`'s only traffic signal, node `1927184932`, was `review_required` with
`lane_ids: []`, raising a **blocker** reading *"signal has no generated approaching
lane"*. Keith's answer of `not_applicable` — "It's where the map starts in this case" —
was the only one that ever closed it; a later export answering `accepted` left the
blocker open and failed Stage 5.

The node is index 0 of way `1173001826`, the only way containing it. Traffic **enters**
the extract there.

```
 node 1927184932 · way 1173001826 (Persiaran Meranti's crossing road, oneway, 3 lanes)
 lane indices run centre-out: idx0 hugs the centreline (offside), idx(n−1) is kerbside
 nothing approaches: this node is the first node of the only way that contains it

        ✗  N O T H I N G   U P S T R E A M  —  E D G E   O F   E X T R A C T  ✗
                                    ┊
 ══════════════════════════════════ ┊ ═══════════════════════════════ KERB ══
   ┏━━━━━━━━━━┓                     ┊
   ┃  SIGNAL  ┃═══════════════════════►  idx2/3  1cae945509  nearside   0 entries
   ┃   node   ┃ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┊─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─
   ┃1927184932┃═══════════════════════►  idx1/3  4628081f4c  MIDDLE     0 entries
   ┃          ┃ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┊─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─
   ┗━━━━━━━━━━┛═══════════════════════►  idx0/3  1f39b9cd20  offside    0 entries
 ══════════════════════════════════ ┊ ════════════════════════ CENTRELINE ══

   travel direction ───────────────────────────────────────────────────────►

   lanes_by_end[1927184932]   = []            ← the only thing the rule looked at
   lanes_by_start[1927184932] = the three lanes above   ← never consulted
```

## Fundamental cause

The rule decided `mapped` versus `review_required` on a single boolean — *is
`lanes_by_end[node]` empty* — and treated the empty case as one thing. It is two.

A signal with no approaching lane at a node **a source way runs through** is a defect:
the road is there and the lane that should meet it is missing. A signal with no
approaching lane at a node that **terminates every way containing it** is not a defect at
all; the file was cut at the signal, the approach is outside the map, and no review can
produce one. Collapsing the two made the second unanswerable — the generator asked a
question whose only truthful answer was "this question cannot be answered here", and
`_decision_is_satisfied` keys satisfaction on the signal becoming `mapped`, so no verdict
except `not_applicable` could ever close it.

The predicate that separates them already existed and was already trusted: Stage 5's
`_way_terminus_nodes` uses it to report 39 of `junction-1`'s dead-end lanes as the edge
of the extract rather than as errors. It lived two stages downstream of the decision that
needed it, and was applied to lanes only. So the same physical fact — a feature at the rim
of the extract — was computed automatically for lanes and left to a human for signals.

Second, smaller error, found while fixing the first: the stop-line loop places a line two
metres before a lane's **downstream end**, which is the signal only for a lane that ends
there. Associating a signal with lanes that *start* at it would have put three stop lines
at the far end of those lanes — 14 m past the junction, facing the wrong way — and raised
three `inferred_stop_line` warnings about a place where nothing stops.

## Fix

`osm_source.way_terminus_nodes(snapshot)` is now the one implementation of "no source way
continues through this node". `generation.build_lane_model` calls it on the snapshot it
already holds, so the function stays pure — it opens no file. `validation._way_terminus_nodes`
keeps its name, signature and `ValidationError` conversion and delegates to it.

`generation._signal_association` is a pure four-case decision:

| lanes end there | lanes start there | node terminates every way | association | finding |
| --- | --- | --- | --- | --- |
| yes | — | — | the lanes that end there | none |
| no | yes | **yes** | the lanes it **releases** | warning, `medium` |
| no | yes | no | none | blocker, `low` |
| no | no | — | none | blocker, `low` |

The stop-line loop now skips any lane that does not end at the signal.

`ReviewOverrides` gained `signal_lane_associations`, keyed on **OSM node id**. Stage 4
collected that override before and dropped it — `ReviewOverrides` carried only connector
ids — so `Choose lanes` was recorded in the audit file and applied to nothing, which is
the second reason the blocker could never be closed. It is now applied, refuses a lane not
in the model, and is hashed into the Stage 4 fingerprint. Keyed on the node rather than
the finding because a finding's identifier covers its `affected_feature_ids`: the moment
an association changes, a verdict filed under the old id matches nothing.

**Deliberately not done.** The third row is not associated. Row three is a real defect,
and `_decision_is_satisfied` reads `mapped` as the reviewer's answer having been met, so
naming a guess there would let `accepted` close a question the generator cannot answer.
No third `SignalAssociation.status` was added either: `validation.py` and `apply_review.py`
both spell the test `status != "mapped"`, so a new value would be read as unassociated by
both and reproduce the original bug one layer down. And the warning is still **raised** —
the boundary case is classified, not suppressed.

## Verification

`workspaces/junction-1` regenerated on a scratch copy; the real workspace was not written
to. Before → after:

| | before | after |
| --- | ---: | ---: |
| lanes | 285 | 285 |
| connectors | 116 | 116 |
| findings | 140 | 140 |
| blockers | 58 | **57** |
| stop lines | 0 | 0 |
| signal `1927184932` | `review_required`, 0 lanes | **`mapped`, 3 lanes** |

Per-rule finding delta across every rule: **0**. No connector changed status, no lane was
added, removed or stranded. The one blocker that disappeared is the signal finding, which
became a warning.

An intermediate build — before the stop-line loop was gated — produced 143 findings and 3
stop lines placed 14 m past the junction, which is what that gate exists to prevent and
what the new `test_a_released_lane_gets_no_stop_line` pins.

The terminus refactor was proved inert before it was relied on: both readings of
`workspaces/junction-1/source/map.osm` return the **same 74 nodes**. The one deleted way,
`776021090`, carries 0 `<nd>` refs, so it contributed nothing to either. Where a deleted
way ever does carry refs the new reading is the correct one — the graph the model is built
from never had that way.

Stage 4 and Stage 5 run end to end on the scratch copy with the signal finding `accepted`:
`findings_still_open` is `[]`, and Stage 5 reports **`passed`, 0 errors, 0 warnings** —
the `unassociated_signal` issue is not raised at all, where before it was a dispositioned
warning.

`uv run pytest` 199 passed (was 186; 13 new). `uv run ruff check` clean. In `web/`:
`tsc --noEmit` clean, `vitest run` 52 passed, bundle rebuilt.

**`GENERATOR_VERSION` was not bumped**, and that is a judgement worth recording rather than
assuming. Against bumping: Keith has a live review of 140 decisions, and the fingerprint is
compared whole, so a bump discards all of them. For bumping: an unbumped version cannot
tell a model built before this change from one built after. What settles it here is that
the only model content this change touches is the boundary-signal case, and that case
*always* changes the finding's identifier (`affected_feature_ids` goes from `[]` to the
released lanes), so Gate 2 refuses any stale review that would otherwise slip through. No
silent staleness is possible. Bump when between reviews.
