# CLAUDE.md

Instructions for Claude Code working in this repo. Sections A and B are standing
instructions — follow them every run, without being reminded.

---

## A. Junction and lane diagrams must be genuinely visual

Whenever you explain lane topology at a node — in a plan, in an answer, or in a doc
— **draw it as a plan-view diagram** in the format shown below. Do not substitute a
table of IDs, a bullet list of mappings, or prose. The reader must be able to see
what is wrong without holding four lane indices in their head or cross-referencing
anything.

**Every diagram must carry, inline:**

- travel direction, and the kerb and centreline edges drawn and labelled
- the index convention in the header — indices run **centre-out**, `idx0` hugs the
  centreline (offside), `idx(n−1)` is kerbside (nearside) — and the angle sign
  convention, `+` = left turn, `−` = right turn
- **every destination lane as its own channel**, labelled `idxN/M`, its lane ID,
  nearside / middle / offside, and its **feed count**
- **every approach labelled where it is drawn** — way ID, `idxN/M`, turn angle, and
  any `turn:lanes` tag or ramp/link role
- shared feeds drawn as a visible merge into one lane
- **starved lanes called out inside the drawing**, not in prose underneath
- no "see the table above", no bare hex IDs the reader has to look up

Re-derive every ID, index, angle and count from the generated model by script
before drawing. Never copy figures by hand from an earlier message.

Both reference cases below show the **pre-v11 model**. The defect they illustrate was
fixed in `direct-osm-stage2-v11` (`_balanced_merge_assignment`), so neither node looks
like this any more — see
`docs/mapping-algo-changes/2026-08-07-12:34:23-merging-approaches-starve-the-middle-lane.md`
for the after view. They are kept because they remain the worked examples of the
required format.

### Reference case 1 — node 13946726034 (pre-v11)

```
 node 13946726034            + = left turn · − = right turn · left-hand traffic
 lane indices run centre-out: idx0 hugs the centreline, idx(n−1) is kerbside

   APPROACHES                       ┊  DESTINATION — way 776370584, 3 lanes
   (arriving at the node)           ┊  (the node's only outgoing group)
                                    ┊
 ══════════════════════════════════ ┊ ═══════════════════════════════ KERB ══

   776021087  idx1/2   −0.03° ───────────────►  idx2/3  e6db35d27f  nearside
                                    ┊                                 1 feed
 ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─┊─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─

        ✗  N O T H I N G   F E E D S   T H I S   L A N E  ✗
                                    ┊         idx1/3  37238b17cc  MIDDLE
                                    ┊                              0 feeds
 ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─┊─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─

   776021087  idx0/2   −0.03° ──┐   ┊
                                ├─────────────►  idx0/3  ba662c1bbc  offside
   1530245742 idx0/1   −19.4° ──┘   ┊                       2 feeds — SHARED
     link · turn:lanes:forward=right ┊
 ══════════════════════════════════ ┊ ════════════════════════ CENTRELINE ══

   travel direction ───────────────────────────────────────────────────────►
```

### Reference case 2 — node 1928630009 (pre-v11)

```
 node 1928630009             + = left turn · − = right turn · left-hand traffic
 same defect, mirrored: the joining road takes the kerbside lane instead

   APPROACHES                       ┊  DESTINATION — way 776021091, 3 lanes
   (arriving at the node)           ┊  (the node's only outgoing group)
                                    ┊
 ══════════════════════════════════ ┊ ═══════════════════════════════ KERB ══

   182502409  idx0/1   +14.8° ──┐   ┊
     ramp, no turn:lanes        ├─────────────►  idx2/3  b63366201b  nearside
   776021086  idx1/2   +0.2°  ──┘   ┊                       2 feeds — SHARED
                                    ┊
 ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─┊─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─

        ✗  N O T H I N G   F E E D S   T H I S   L A N E  ✗
                                    ┊         idx1/3  eef18fbc84  MIDDLE
                                    ┊                              0 feeds
 ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─┊─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─

   776021086  idx0/2   +0.2°  ───────────────►  idx0/3  a566b487c1  offside
                                    ┊                                 1 feed

 ══════════════════════════════════ ┊ ════════════════════════ CENTRELINE ══

   travel direction ───────────────────────────────────────────────────────►
```

Both cases were the **same defect**, mirrored, and both are now fixed.

---

## B. Log every corrected mapping-algorithm mistake — automatically

When a mapping-algorithm mistake is corrected, write an entry in
`docs/mapping-algo-changes/` **without being asked**. The folder is a countable
record of real corrections; it must not fill up with session notes.

Filename: `YYYY-MM-DD-HH:MM:SS-<algo-change-desc>.md` (get the timestamp from
`date +"%Y-%m-%d-%H:%M:%S"`, do not invent one).

**Write an entry only when all three hold:**

1. **Keith identified the mistake** — he pointed at a lane, connector or mapping
   that is wrong. Not something you noticed and decided to change.
2. **The fix changed algorithm code** in `src/osm_scenario/` — generation,
   topology, lane mapping. Not docs, config, or tests.
3. **The fix was verified** — workspace regenerated, before/after counts compared,
   no existing connector regressed, `uv run pytest` passes.

**Do not write an entry for:** investigations that ended without a code fix;
doc-, config- or test-only changes; refactors Keith did not flag; attempts that
were reverted; or anything where you are unsure. When unsure, ask — do not create
the file speculatively.

Required headings, so entries stay comparable: **Symptom**, **Fundamental cause**,
**Fix**, **Verification**. The fundamental cause is the point of the record — say
*why the algorithm produced the wrong result*, not merely which line changed. Full
template in `docs/mapping-algo-changes/README.md`.

---

## C. Repo facts you cannot get from reading the source

`README.md` and `guide/project-guide.md` cover Stage 1 only. The Stage 2 generator
is `src/osm_scenario/generation.py`; geometry and movement classification live in
`src/osm_scenario/topology.py`.

### Commands

```bash
uv run osm-scenario generate-map -w workspaces/junction-1 --config config/default.yaml
uv run pytest
uv run ruff check
```

The `-w` is required and easy to miss. `ruff format --check` fails on 8
pre-existing files and is **not** a gate — do not mass-reformat to satisfy it.

### Workspaces

`workspaces/` is gitignored, so its contents never appear in `git status` — run
`ls workspaces/` rather than assuming. `junction-1` is the working one.
Generation refuses to run when `source/map.osm` drifts from the sha256 in
`source/manifest.json`; Keith hand-edits the OSM mid-session, so re-check rather
than trusting a number from earlier in the conversation.

### Reference checkouts — read MetaDrive, do not guess at it

Both are on this machine. Neither is a dependency of this repo, and nothing in
`git status` will remind you they exist.

- `/home/keith/Desktop/work/wingfin/metadrive/` — MetaDrive **0.4.3**, the format
  this converter targets. When a question is "what does MetaDrive do with this
  field", the answer is in here, not in a recollection of the docs.
- `/home/keith/Desktop/work/wingfin/scenarionet/` — ScenarioNet: the dataset
  tooling, and the Waymo / nuPlan / nuScenes / Argoverse converters worth
  comparing our output against when a field's shape is in doubt.

`tests/unit/test_conversion.py` loads MetaDrive's real `ScenarioDescription`
straight from the first path (`METADRIVE_SRC`, near line 356) and is marked
`skipif` on the directory being absent — so a moved or renamed checkout **silently
drops the schema gate** rather than failing.

Two traps that have already cost a session, both measured rather than read:

- **Neither checkout can unpickle our scenario.** Both `.venv`s are Python 3.8 /
  numpy 1.24; we write with numpy 2.2, so loading raises `ModuleNotFoundError: No
  module named 'numpy._core'`. The `sanity_check` test passes because it loads
  MetaDrive's *source file* into this repo's 3.10 environment — it checks the
  schema, never the round trip.
- **`ScenarioEnv` cannot reset on a map-only scenario.** `ScenarioMapManager.reset`
  calls `update_route()` unconditionally, which calls `get_sdc_track()`; with no
  recorded ego car that raises `KeyError('None')`. No config skips it — `no_map`
  does not. Driving our map needs an ego route from somewhere, and that is an
  open decision, not an oversight.

### Conventions that bite

- **OSM connectivity is via shared nodes.** Relations only carry turn restrictions.
  A missing connection is never a missing relation.
- **`direction: forward|backward`** is relative to OSM way node order, not to
  oncoming-ness. A "backward" lane is not necessarily oncoming traffic.
- **Lane indices run centre-out**: `idx0` = offside (against the centreline),
  `idx(n−1)` = nearside (kerbside). `driving_side` is `left`.
- **`signed_turn_angle` is CCW-positive**: `+` = left turn, `−` = right turn.
- **`entry_lanes` / `exit_lanes` hold a mix of ID kinds** — lane IDs for
  continuations (written at `generation.py:1147`) and connector IDs for junction
  movements (248 vs 74 in the current junction-1 model). Any lookup that assumes
  one kind fails silently on the other.

### The standing principle: surveyed tags outrank inferred angles

`turn:lanes` is surveyed evidence of which movements are *permitted*. The movement
class is *inferred* by binning a turn angle against threshold constants. Where the
two disagree, **the tag must never be the reason a lane loses its only exit** —
that cuts the drivable network on the strength of a magic number. Already enforced
in `_side_filtered_candidates` and `_stranded_permission_fallback`; follow the same
rule anywhere else the two sources of truth meet.

### Starved middle lanes: mostly fixed, one left

Two allocation rules now run before the proportional mapping, and between them they
cover both shapes where the lane arithmetic closes:

- `_balanced_approach_assignment` — **one** approach across **several** destinations
  (a lane peeling off cannot also be the straight-on lane). Added in v10.
- `_balanced_merge_assignment` — **several** approaches into **one** destination
  (a merging link must not land on a lane the main road already feeds). Added in v11.

`_mapped_lane_index` (`generation.py`) is unchanged and still **cannot produce a
middle index**: for a 2-lane approach onto a 3-lane destination
`round(idx × (3−1) / (2−1))` gives `idx0→0`, `idx1→2`, and index 1 is unreachable for
*any* input. It now only decides **oversubscribed** approaches — where the counts do
not close, a lane genuinely serves more than one movement, and the ambiguity is
reported rather than resolved. Clean diverges and clean merges no longer go through it.

**Fixed in v17 — the last diagnosed starved lane.** `39619063` idx1/2 `c0530c25fd` at
node 1927184814 is now fed by `027a3ef89e3e7b88`.

Way `756118314` is tagged `turn:lanes=right|right`, so both its lanes carry
`turn_permissions=['right']`. An explicit `turn:lanes` value outranks geometry in
`movement_side()`, so **both** lanes are labelled `offside`, and
`side_lane_index("offside", 2)` returned `0` for both — they collided on one target.
The approach is oversubscribed (2 lanes arriving, 5 lanes of destination capacity at
the node), so neither balanced rule reaches it and `_mapped_lane_index` decides.

`_mapped_lane_index` now takes the block of lanes an explicit tag puts on that side and
deals them from the side inward, so a side says where a block **starts** rather than
where every lane in it goes. A block of one is unchanged, so only a genuine collision
moves. See
`docs/mapping-algo-changes/2026-08-09-16:44:44-a-side-picks-where-a-block-starts.md`.

Two blockers remain at that node, and correctly: `turn:lanes=right|right` names a right
turn that is not available there, and that disagreement is Keith's to judge. **Never fix
a tag-versus-geometry conflict by making the finding stop being raised** — fix the
mapping and keep the review.

Two cautions when re-measuring this. A previous version of this table also listed
`776021087` idx0/2 `8caffc7049` at node 13946726031; under the criterion "no connector
and no continuation names it as a target" that lane was **already fed at v9**, so it
was either counted under a different criterion or listed in error. And `junction-1`
still has **21** lanes fed by nothing; most are network-boundary lanes rather than
defects, and none of the remainder has been diagnosed.
