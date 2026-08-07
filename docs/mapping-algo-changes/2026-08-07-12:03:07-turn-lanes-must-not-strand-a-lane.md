# `turn:lanes` was allowed to delete a lane's only exit

- **Date:** 2026-08-07 12:03:07
- **Identified by:** Keith
- **Files changed:** `src/osm_scenario/generation.py`
  (`_stranded_permission_fallback`, the per-lane candidate loop),
  `tests/unit/test_generation.py`
- **Generator version:** `direct-osm-stage2-v9` → `direct-osm-stage2-v10`
- **Commit:** `1f0f5ef`

## Symptom

Lane `0afbd72e5f0b9450` (way 1530245742, `idx0/1`) showed **"Leaves to none"** in
the Stage 2 review. It should attach to `ba662c1bbce21423` on way 776370584.

Ways 1530245742 and 776370584 share node `13946726034` and are correctly joined in
OSM. No relation was missing — relations carry turn restrictions, not connectivity.
The lane simply had every candidate exit discarded and dead-ended silently, with
no finding raised.

```
 node 13946726034            + = left turn · − = right turn · left-hand traffic
 BEFORE the fix — the tagged approach reached nothing at all

   APPROACHES                       ┊  DESTINATION — way 776370584, 3 lanes
                                    ┊
 ══════════════════════════════════ ┊ ═══════════════════════════════ KERB ══

   776021087  idx1/2   −0.03° ───────────────►  idx2/3  e6db35d27f  nearside
                                    ┊
 ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─┊─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─
                                    ┊         idx1/3  37238b17cc  MIDDLE
 ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─┊─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─

   776021087  idx0/2   −0.03° ───────────────►  idx0/3  ba662c1bbc  offside
                                    ┊
   1530245742 idx0/1   −19.4°  ──✗  ┊   ✗  D E A D   E N D  ✗
     link · turn:lanes:forward=right ┊   all 3 candidate exits discarded
 ══════════════════════════════════ ┊ ════════════════════════ CENTRELINE ══

   travel direction ───────────────────────────────────────────────────────►
```

## Fundamental cause

An **inferred** angle threshold was allowed to overrule a **surveyed** tag, and the
resolution of that disagreement was to cut the network.

Way 1530245742 carries `turn:lanes:forward=right`. Its only geometric continuation
onto way 776370584 measures **−19.36°**. `classify_movement` bins everything within
35° as `through`, so the movement was labelled `through`. `through` is not in the
alias set for `right`, so the permission filter matched nothing:

```python
elif source.turn_permissions and not any(
    movement_matches(permission, movement) for permission in source.turn_permissions
):
    continue
```

All three candidates hit that `continue` and the candidate set emptied.

The reasoning error is in what the two inputs mean. `turn:lanes` is surveyed
evidence of which movements are **permitted**; the movement class is inferred by
binning an angle against a threshold constant. A tag that matches nothing on offer
is a *disagreement between the two*, not proof that no exit exists — and resolving
it by dropping every candidate disconnects the drivable network on the strength of
a magic number, silently.

The control case isolates it: way 776021087's lanes reach the same node with no
`turn:lanes` tag, so `source.turn_permissions` is falsy, the filter is skipped
entirely, and both lanes connect `through` at −0.03° with status `active`. Identical
geometry, opposite outcome, decided solely by the presence of a tag.

The same no-stranding principle was already encoded next door in
`_side_filtered_candidates` — the permission filter simply predated it.

## Fix

New helper `_stranded_permission_fallback` in `generation.py`, mirroring
`_side_filtered_candidates` rather than inventing a second mechanism: same
`if kept or not removed or has_continuation` guard, same
`min(abs(angle), to_lane_id)` deterministic tie-break.

The candidate loop no longer `continue`s on a permission mismatch. It routes the
candidate by a `permitted` flag — permitted ones to `candidates_for_lane`, rejected
ones to `permission_removed`. If the permitted set ends up empty **and** the lane
has no straight-on continuation, the straightest rejected movement is restored.

Each restore emits a `turn_permission_geometry_conflict` finding — severity
`blocker`, confidence `low` — recording the tag, the restored movement and angle,
and every rejected movement, so the disagreement surfaces for review instead of
vanishing. The rule sorts first in the audit HTML `reviewPriority` map.

**Deliberately not done:** the restored connector's `movement` is left as the
geometrically classified value (`through`), not relabelled to the tagged `right`.
Relabelling would feed a fabricated movement into `movement_side`,
`movement_family` ambiguity counting and restriction matching. The tag is recorded
in the finding instead.

The `reverse` drops — `uturn_status == "excluded"`, non-zero `lane_index` without
an explicit reverse tag — remain genuine drops. They are separate rules, not this
tag-vs-angle conflict.

## Verification

Measured at commit `1f0f5ef`, regenerating `workspaces/junction-1` with
`config/default.yaml` at an unchanged config checksum:

| | before | after |
|---|---:|---:|
| connectors | 109 | **112** |
| dead-end lanes | 19 | **16** |
| `active` | 69 | **72** |
| `review_required` | 35 | 35 |
| `forbidden` | 5 | 5 |
| findings | 557 | **560** |
| `turn_permission_geometry_conflict` | 0 | **3** |

Zero pre-existing connectors were removed, and none changed status or movement.
Nothing was newly stranded. The three newly connected lanes were exactly the three
predicted: `0afbd72e5f` (way 1530245742) and `1831f85bcf` / `027a3ef89e` (both way
756118314), all tagged `right`.

`0afbd72e5f0b9450 → ba662c1bbce21423`, `through`, −19.36°, `active` — the
attachment Keith expected.

62/62 tests pass; `uv run ruff check` clean. Two tests added: one covering the
Kenanga case and tie-break determinism, one covering the three cases where the
fallback must stay off (a permitted candidate exists, a continuation exists,
nothing was removed).

`ruff format --check` still reports 8 files needing reformatting — pre-existing at
`HEAD` for both touched files, deliberately not addressed.

> Later note: the working model now reads 110 connectors / 552 findings /
> 74 `active`, because commit `96ae00d` ("fixed origin point of turn lane
> `dc1ae82400f47f4a`") landed afterwards. The table above is the before/after for
> **this** change alone. The dead-end count is still 16 and the 3
> `turn_permission_geometry_conflict` findings are still present.


NOTES** Keith

Basically in this situation the lane did not connect to the outgoing exit lane, it was left stranded.