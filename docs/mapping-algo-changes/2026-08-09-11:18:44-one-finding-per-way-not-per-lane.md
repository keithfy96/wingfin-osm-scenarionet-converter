# One finding per way, not one per graph edge

- **Rule affected:** `lane_count_inference`, `lane_width_default`, `speed_default`
- **Generator version:** `direct-osm-stage2-v14` → `direct-osm-stage2-v15`
- **Lane model schema:** unchanged at 3

## Symptom

Way `1351503426` produced **126 review findings**: 42 `lane_count_inference`,
42 `lane_width_default` and 42 `speed_default`, for a road that raises exactly four
questions — how many lanes forward, how many backward, how wide, how fast.

```
way 1351503426  →  split into 42 graph edges  →  42 lanes generated

   speed_default         42 findings   1 distinct answer   (50.0)
   lane_width_default    42 findings   1 distinct answer   (3.5)
   lane_count_inference  42 findings   2 distinct answers  (forward 1, backward 1)
```

Across junction-1 this was 489 findings where 106 were meant, and it put 105 of the
138 blockers gating Stage 4 behind repeated answers to the same question.

## Fundamental cause

These three rules ask about a **way**. Accepting or overriding one writes a way-level
OSM tag — `lanes:forward` / `lanes:backward`, `width`, `maxspeed`. There is no
per-lane component to any of them.

But they were emitted from inside the lane-building loop, which iterates
**graph edges**, not ways. A way is split into as many edges as it has intermediate
junction nodes, so the number of times a reviewer was asked a way-level question was
determined by how finely the road happened to be segmented — a property of the
network's topology, not of the question. A long residential street asked 42 times;
a short one asked twice.

The finding's scope had drifted from the scope of the decision it asks for. The lane
list was already carried in `affected_feature_ids`, so nothing was gained by
re-asking: every duplicate named a different lane of the same way and proposed the
same answer.

## Fix

`generation.py` accumulates these three during the edge loop instead of emitting, in
dicts keyed by the scope of the decision, then emits one finding per entry after the
loop:

| rule | key | emitted |
|---|---|---|
| `lane_count_inference` | way ids, direction, count, reason, confidence | one per way + direction |
| `lane_width_default` | way ids, width | one per way |
| `speed_default` | way ids, speed | one per way |

`affected_feature_ids` becomes `sorted(set(...))` of every lane the entry accumulated,
so one finding still names every lane it covers. Sorting matters because
`deterministic_id` folds those ids in positionally; unsorted, the identifier would
depend on edge iteration order.

**The proposed value is part of the key.** Two edges of one way that genuinely
disagreed about lane count, width or speed land in different entries and still produce
two findings. Only questions that were already identical are merged.

## Verification

Workspace regenerated; before/after compared by script against a snapshot of the v14
model, not by eye.

| rule | before | after |
|---|---|---|
| `lane_count_inference` | 105 | **24** |
| `lane_width_default` | 192 | **41** |
| `speed_default` | 192 | **41** |
| `ambiguous_connector` | 29 | 29 |
| `lane_transition_count_mismatch` | 19 | 19 |
| `turn_permission_geometry_conflict` | 3 | 3 |
| `signal_lane_association` | 1 | 1 |
| **total** | **541** | **158** |

- **Blockers 138 → 57.** Severity rules unchanged; only duplication removed.
- **No lane lost its finding.** The union of `affected_feature_ids` per rule is
  identical before and after, for all seven rules.
- **The four untouched rules kept their identity** — all 52 findings have the same
  identifiers and the same evidence checksums as before.
- Merged findings' affected ids are sorted and unique; all 158 identifiers are unique.
- Two consecutive `generate-map` runs produce a byte-identical `preliminary.json`.
- `preliminary.json` 1699 KB → 956 KB; the review page 1213 KB → 838 KB.
- `uv run pytest` 88 passed (2 new); `uv run ruff check` clean.

Decisions previously recorded against the merged rules are invalidated, which is
correct: both the identifier and the evidence checksum derive from
`affected_feature_ids`, and a decision made against one of 42 duplicates cannot be
transferred to the merged finding without asserting something the reviewer never said.
They return to `unresolved` through the existing migration.

## Note on provenance

Keith did not flag this one — it surfaced while answering his question about whether
Stage 4 requires a fully cleared review. He was shown the measurements and chose the
full three-rule scope. Recorded here because it is a mapping-algorithm correction with
a measurable before and after, which is what this folder is for.
