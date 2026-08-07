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

### Reference case 1 — node 13946726034

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

### Reference case 2 — node 1928630009

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

Both cases are the **same defect**, mirrored — see "Known-wrong" in section C.

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
`ls workspaces/` rather than assuming. `junction-1` is currently the only one.
Generation refuses to run when `source/map.osm` drifts from the sha256 in
`source/manifest.json`; Keith hand-edits the OSM mid-session, so re-check rather
than trusting a number from earlier in the conversation.

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

### Known-wrong, not yet fixed: starved middle lanes

Two independent defects stack to leave lanes fed by nothing — both reference
diagrams in section A are this:

1. **`_mapped_lane_index` (`generation.py:292`) cannot produce a middle index.**
   For a 2-lane approach onto a 3-lane destination it computes
   `round(idx × (3−1) / (2−1))` → `idx0→0`, `idx1→2`. Index 1 is unreachable for
   *any* input. The formula stretches lane order across the full width instead of
   allocating lanes.
2. **Each approach picks its target independently.** Nothing tracks what another
   approach into the same outgoing group already claimed, so a ramp or link lands
   on a lane the main road also feeds. `_balanced_approach_assignment`
   (`generation.py:320`) solves the mirror case — one approach onto several
   destinations — but there is no equivalent for several approaches onto one.

In `junction-1` this starves **4** lanes across 50 partially-fed multi-lane groups:

| way | lane | at node |
|---|---|---|
| 776370584 | idx1/3 `37238b17cc` | 13946726034 |
| 776021091 | idx1/3 `eef18fbc84` | 1928630009 |
| 39619063 | idx1/2 `c0530c25fd` | 1927184814 |
| 776021087 | idx0/2 `8caffc7049` | 13946726031 |

Fixing this touches every multi-lane group and needs its own plan and its own
change-log entry.
