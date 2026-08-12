# A one-lane road was given a lane each way, and the phantom lane broke the merge

- **Date:** 2026-08-12 16:36:30
- **Asked by:** Keith — "this part this looks broken ... as you can see the bottom lane
  persiaran perdana, its centerlanes do not go straight like they should in the junction-1
  map", then "could you make it such that if a way has just one lane, it will generate a one
  way road and not a two way road?", then "if there is a way out, it can proceed, if there is
  no way out, it should be raised in the next section stage 3 review"
- **Files changed:** `src/osm_scenario/osm_source.py`, `src/osm_scenario/generation.py`,
  `tests/unit/test_osm_source.py`, `tests/unit/test_generation.py`,
  `tests/fixtures/osm/single-lane.osm`

## Symptom

On the `mosque` extract, `1530245742` and `1530245743` — the two Persiaran Kenanga slips —
are tagged `lanes=1` with no `oneway`. Each generated **six** lane segments where the source
describes one lane, half of them `backward`, and the phantom half produced:

| | mosque | junction-1, same junction |
| --- | ---: | ---: |
| lane segments per slip | **6** | 3 |
| connectors at nodes 13946726031 / 13946726034 / 474929865 | **5 / 4 / 12** | 3 / 3 / 7 |
| connectors touching a phantom backward lane | **8** | 0 |
| `reverse` connectors across the westbound carriageway | **3**, 14.3–15.0 m | 0 |

One of the three was `1530245743 → 1530245743` at +180°, a U-turn from a road onto itself.
Keith saw them as orange bands lying diagonally across Persiaran Perdana, which is what put
him onto it — the lane centrelines themselves were straight in both maps and measured
identical, so what looked bent was a connector drawn over the road.

## Fundamental cause

### `lanes=1` with nothing else fell through to "one lane each way"

`_directional_lane_count` asks `_carries_whole_carriageway(tags)`, which was true only for an
explicit `oneway` or a roundabout. With neither, `lanes=1` reached the last branch,
`max(1, total // 2)`, and returned **1 for each direction** — so a road the source says is one
lane wide came out two lanes and 7 m across.

The mapper who wrote `lanes=1` and left `oneway` off far more often meant a one-way slip than
a single-track road with passing places. Nothing read it that way.

### The phantom lane then defeated the balanced merge

`_balanced_merge_assignment` deals several approaches into the one carriageway they all join,
and it requires each approach to have exactly one live destination — that requirement is what
keeps the allocation unambiguous. The backward lane of `1530245742` gave `776021087` a second
one, so the rule returned `{}` and `_mapped_lane_index` decided instead. It cannot produce a
middle index, so at node 13946726034:

```
 node 13946726034 — mosque, before        + = left turn · − = right turn · left-hand traffic
 lane indices run centre-out: idx0 hugs the centreline (offside), idx(n−1) is kerbside

   APPROACHES                       ┊  DESTINATION — way 776370584, 3 lanes
                                    ┊
 ══════════════════════════════════ ┊ ═══════════════════════════════ KERB ══

   776021087  idx1/2   −0.0° ───────────────►  idx2/3  e6db35d27f  nearside
     Perdana · oneway · 2 lanes      ┊                                1 feed
 ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┊─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─

        ✗  N O T H I N G   F E E D S   T H I S   L A N E  ✗
                                    ┊         idx1/3  37238b17cc  MIDDLE
                                    ┊                              0 feeds
 ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┊─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─

   776021087  idx0/2   −0.0° ──┐    ┊
                               ├──────────────►  idx0/3  ba662c1bbc  offside
   1530245742 idx0/1  −18.6° ──┘    ┊                      2 feeds — SHARED
     Kenanga slip · lanes=1          ┊
     no oneway tag                   ┊
     turn:lanes:forward=right        ┊
 ══════════════════════════════════ ┊ ════════════════════════ CENTRELINE ══

   ◄─── 776021087 idx0/2  −161.5°  onto 1530245742's PHANTOM backward lane
          0f06a851da · 14.32 m · one of the three bands Keith could see

   travel direction (westbound) ────────────────────────────────────────────►
```

It is the shape of CLAUDE.md's reference case 1, arrived at from a new direction: not the
lane arithmetic failing, but a lane that should not have existed changing what the arithmetic
was asked.

## Fix

**`osm_source.single_lane_implies_oneway`** reads `lanes=1` as one lane in one direction, and
every surveyed tag switches it off — an explicit `oneway=no`, a `lanes:forward`/`lanes:backward`,
an existing `oneway`, a roundabout. It is a reading of one tag in the absence of others, never
a reason to overrule one that is present, which is the standing principle applied to a tag that
was previously not consulted at all.

**`_apply_single_lane_oneway` decides whether the network can afford the reading**, because
dropping the reverse direction of the only route off a spur does not simplify the model, it
strands everyone on it. A way is refused unless every node that could get out before still can
after, where getting out means reaching the main network **or driving off the edge of the map**.
The second half is not optional: the first version of the guard had only the first, and it
refused `1530245742` because its far end continues west out of the extract — a merge slip whose
exit is the file boundary is not a trap. Both anchors are pinned before anything is dropped —
the main network as one node of the largest strongly connected component, so it cannot shrink
under its own answer, and the edges of the map as the nodes that already had no way on at all.
A cul-de-sac tip on a two-way road is not one of those, because its way out is the reverse
direction that is in question.

Candidates are decided **one at a time against the graph the last one left**. Two ways can each
be spare while the other is two-way and be the only way out together; the fixture has that pair,
and the first is applied while the second is refused.

Refusals are not silenced. `manifest["road_selection"]["single_lane_oneway"]` records both
outcomes with the nodes a refusal would have stranded, and the `lane_count_inference` **blocker**
Stage 2 already raises on a `lanes=1` way is what carries the question into Stage 3 — the
reviewer answering it with a real count is the surveyed tag that switches the inference off.
No new finding rule was needed, and none was added.

**`_carries_whole_carriageway` gained `one_way_in_graph`**, derived once per model by
`_single_direction_ways` from the graph rather than from the manifest, so generation cannot
disagree with the stage that made the decision and a refusal is automatically a no-op. Without
it the change would have been half done: Stage 1 edits the graph and never the source OSM, which
is acquisition evidence, so the tags still say two-way and the surviving lane would still be
offset **1.75 m** off the road's centre, balancing against an oncoming block that is no longer
there. `test_a_single_lane_way_generates_one_centred_lane_when_stage_1_read_it_one_way` fails
with exactly that number if the flag is ignored.

`GENERATOR_VERSION` is `direct-osm-stage2-v18`.

## Verification

`uv run pytest` **326 passed** (was 310). `uv run ruff check` clean. In `web/`:
`npm run typecheck` clean, `npm run test` **133 passed**.

Both new guards were checked by removing them: disabling `one_way_in_graph` fails the
generation test at 1.75 m, and disabling the strand check fails two `osm_source` tests.

**`mosque`, regenerated through Stage 1 and Stage 2:**

| | before | after |
| --- | ---: | ---: |
| lane segments on `1530245742` / `1530245743` | 6 / 6 | **3 / 3, all forward** |
| connectors touching a phantom backward lane | 8 | **0** |
| connectors at 13946726031 / 13946726034 / 474929865 | 5 / 4 / 12 | **3 / 3 / 7** |
| feeds on `776370584` idx0/3, idx1/3, idx2/3 | 2, **0**, 1 | **1, 1, 1** |
| model connectors | 200 | **192** |
| `lane_count_inference` findings on the two slips | 4 | **0** |

All three node counts and all three feed counts now match junction-1 at the same junction.
`manifest["road_selection"]["single_lane_oneway"]` lists both ways under `applied`, none
`blocked`, Stage 1 `status: passed`.

**`junction-1` is unchanged by the rule and survives the version bump.** It has no `lanes=1`
two-way way, so `applied` and `blocked` are both empty and its graph still has 196 directed
edges. Regenerated, its preliminary model is **identical apart from metadata** — same 285 lanes,
116 connectors, 140 findings, the same finding identifiers and **0 changed evidence checksums**.
Replaying the Stage 3 migration over `review/applied-decisions.json` gives **139 carried, 0
invalidated, 0 unknown**. Running Stage 4 on a copy with that migrated submission produces a
reviewed model identical apart from metadata.

`convert` on `junction-1` fails on `signals.json`, and that is **not this change**: the same
failure reproduces on a copy of the workspace taken before any of it. Keith re-ran `apply-review`
at 15:50, which rewrote `reviewed.json` under the routes and signals he had drawn at 15:04.
