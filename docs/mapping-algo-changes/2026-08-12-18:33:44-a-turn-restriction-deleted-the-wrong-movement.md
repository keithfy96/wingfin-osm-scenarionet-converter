# A via-way turn restriction always deleted its last movement, and sealed a road off

- **Date:** 2026-08-12 18:33:44
- **Asked by:** Keith — "please look at 74aeb5092adbac68, 6764bd574b73d0b7, and finally
  09326c75a32bd6a9, why are they forbidden, instead of going into review? also, the first 2 are
  actually required, i need them to turn right, the last i will be rejecting cause its technically
  a uturn"
- **Files changed:** `src/osm_scenario/topology.py`, `src/osm_scenario/generation.py`,
  `web/src/panel.ts`, `web/src/controls.ts`, `tests/unit/test_topology.py`,
  `tests/unit/test_generation.py`, `tests/fixtures/osm/via-way-restriction.osm`

## Symptom

Relation `10421009` is a `no_u_turn` on the route `777160373` → `39619063` → `777160375`. It was
enforced by forbidding the two movements out of `39619063`, which are the only movements out of
`39619063`. Both lanes of Persiaran Meranti were therefore dead ends, and the right turn onto
Persiaran Perdana that `turn:lanes=right|right` names explicitly could not be driven.

The traffic that lost it — `756118314`, Meranti's own approach, two lanes — is not named anywhere
in the relation.

| at v18 | |
| --- | --- |
| movements out of way `39619063` | **2, both forbidden** |
| live exits on `39619063` idx0/2 and idx1/2 | **0 and 0** |
| ways feeding `39619063` | `756118314` ×2 (not in the relation), `777160373` ×1 |
| feeds on `777160375` idx0/3 and idx1/3 at node 474928793 | 1 and 1 |

Neither movement raised a finding, so Stage 3 never asked: `forbidden` connectors are removed
silently and only `review_required` ones become `ambiguous_connector` blockers.

## Fundamental cause

**A restriction names a route; a connector is one step of one.** `ConnectorFeature` is
`from-lane → to-lane` at a node and remembers neither the road the car was on before it nor the
one it takes after. A via-way relation forbids the sequence FROM → VIA → TO, so enforcing it means
deleting one step — and deleting a step stops *everyone* who uses that step, not only the drivers
on the prohibited route.

`via_way_resolution` deleted the **last** step every time and never asked whether that was safe.
There are two candidates and each has its own test, and the two look in opposite directions:

- deleting the last step (VIA → TO) is exact only if **nothing else feeds VIA**
- deleting the first step (FROM → VIA) is exact only if **VIA leads nowhere else**

On `10421009` they give opposite answers, which is the whole of the defect.

```
 relation 10421009  no_u_turn  777160373 → 39619063 → 777160375
 + = left turn · − = right turn · left-hand traffic
 lane indices run centre-out: idx0 hugs the centreline (offside), idx(n−1) is kerbside

   NODE 1927184814                              NODE 474928793
   (the way onto Persiaran Meranti)             (Meranti's right turn onto Perdana)

 ══════════════════════════════════════════════════════════════════════════ KERB ══

  756118314 idx0/2 ──┐                                    ┌──►  777160375 idx0/3
  Persiaran Meranti  │  +5.8°  4de6b16c7d13515b   −91.7°  │     c21d5460fc71993a
  turn:lanes         │         ACTIVE           6764bd57  │     offside
   = right|right     ├──────► 39619063 idx0/2 ────────────┤     ✗ WAS FORBIDDEN ✗
                     │        86602054f094204a            │
  777160373 idx0/3 ──┘                                    │
  Persiaran Perdana     −88.6°  ed1e8a2432e248dd          │
  3 lanes, oneway               ✗ NOW FORBIDDEN ✗         │
                                                          │
  756118314 idx1/2 ─────────► 39619063 idx1/2 ────────────┤     ✗ WAS FORBIDDEN ✗
  turn:lanes           +5.8°   c0530c25fd9abf94   −91.7°  └──►  777160375 idx1/3
   = right|right       12a4e1a9f375baca            74aeb509     d2aa783c96ced0ae
                       ACTIVE                                   middle

 ═════════════════════════════════════════════════════════════════════ CENTRELINE ══

   the prohibited route:  777160373 ──(−88.6°)──► 39619063 ──(−91.7°)──► 777160375
                                     net −180.3°, a U-turn, and correctly banned

   what deleting the LAST step also deleted:
     756118314 ──(+5.8°)──► 39619063 ──(−91.7°)──► 777160375
     ✗✗  BOTH EXITS OF 39619063 GONE — every lane turning into Meranti was trapped  ✗✗

   why deleting the FIRST step costs nothing:
     39619063 leads to 777160375 and to nothing else, so anyone entering it from
     777160373 was going to make the prohibited turn. 777160373 idx0/3 keeps its
     through movement onto 776021089, so no lane loses its only exit either.

   travel direction (westbound) ──────────────────────────────────────────────────►
```

It is the standing principle — surveyed evidence must never be the reason a lane loses its only
exit — reaching a source it had not been applied to. The rule was already enforced for `turn:lanes`
in `_side_filtered_candidates` and `_stranded_permission_fallback`; relations had never been asked
the same question.

The second half of the cause is what happens when **neither** step is exact. On a gyratory the
segments carry traffic from several entries by design, so no single deletion is faithful and there
is nothing to deduce. Guessing one is the same defect in a different shape, and leaving all of them
active would make a prohibited route drivable.

## Fix

**`topology.via_way_resolution` runs both tests** against a way-level adjacency
(`topology.way_adjacency`, with generation's continuations merged in), and deletes whichever step
is exact. It prefers the last when both are, so a restriction already enforced correctly keeps
forbidding the same connector id and the review decision attached to it does not move. The reason
recorded on the `RestrictionEffect` says which step went and, for a prefix removal, which way's
traffic the other choice would have taken.

The adjacency is deliberately an **upper bound** on what reaches a way: it is read per way rather
than per lane or per direction, so it can over-count and never under-count. Over-counting sends a
restriction to review; under-counting would delete a movement carrying legal traffic. The asymmetry
is the safety of the test, and it is why the continuations are merged in even though neither
workspace has one that changes an answer.

**When neither step is exact**, the resolver returns `review_required` *with* the last step's
movements, and `generation` holds them: the candidates gain a `restriction_not_expressible`
ambiguity cause, so each becomes a `review_required` connector with an `ambiguous_connector`
blocker naming the relation. `review_required` is already excluded from the lane graph, so the
prohibited route is no more drivable while the question is open than it was before.

`RestrictionEffect.forbidden_connector_ids` still names only movements actually forbidden — a
restriction held for review forbade nothing and must not claim to — so the held ids ride on the
findings instead. No schema change.

**A new `restriction_enforced_leg` warning** records every via-way restriction the generator
enforced by itself, with the step removed and the reason the other was rejected. A warning, not a
blocker: only blockers gate export, so there is nothing to act on. It exists because the generator
now chooses between two defensible enforcements, and that choice should not be made out of sight.

`GENERATOR_VERSION` is `direct-osm-stage2-v19`.

## Verification

`uv run pytest` **335 passed** (was 326). `uv run ruff check` clean. In `web/`: `npm run typecheck`
clean, `npm run test` **138 passed**.

Both new rules were checked by removing them. Forcing the last step back fails three tests
(`test_the_via_way_suffix_is_kept_when_something_else_feeds_the_via_way` and two generation tests);
dropping the hold-for-review path fails
`test_a_via_way_restriction_with_traffic_at_both_ends_is_asked_rather_than_guessed`.

The new fixture `via-way-restriction.osm` carries one map per shape — a via way with a second
entry, one with only the prohibited entry, and one with traffic at both ends — and produces a
prefix removal, a suffix removal and a held pair respectively.

**`junction-1`, regenerated and diffed against a v18 model built from the same source:**

| | v18 | v19 |
| --- | ---: | ---: |
| `6764bd574b73d0b7`, `74aeb5092adbac68` (`39619063` → `777160375`) | forbidden | **active** |
| `ed1e8a2432e248dd` (`777160373` → `39619063`) | active | **forbidden** |
| `09326c75a32bd6a9`, rel `10761640` | forbidden | forbidden, same id |
| live exits on `39619063` idx0/2, idx1/2 | 0, 0 | **1, 1** |
| exits on `777160373` idx0/3 | 2 | 1, still onto `776021089` |
| findings | 140 | **142** — 2 `restriction_enforced_leg` warnings |
| findings removed, or with a changed evidence checksum | — | **0, 0** |
| lane ids, connector ids | — | identical |

The two `turn_permission_geometry_conflict` blockers at node 1927184814 are still raised. They are
about `turn:lanes=right|right` landing on the node where OSM split the way rather than on the node
the turn happens at, which is a different question and Keith's to judge.

**`mosque`:** rel `10421009` is fixed the same way and rel `10761640` is unchanged — 2 warnings.
Rels `15857900`, `15857902` and `18555951`, the three prohibitions on the `1173001827` →
`935525160` → `935525165` gyratory, come out `review_required` with a `restriction_effect_review`
blocker each. Their nine movements stay **forbidden** regardless, because three separate node-via
`no_right_turn` relations (`18555948`, `18555949`, `18555952`) name exactly those movements — a
node restriction names from-way, via-node and to-way, which is precisely what a connector encodes,
so it cannot over-forbid and is untouched by this change. 220 → 225 findings, 0 removed, 0 changed
checksums.

**Stage 3 and Stage 4.** Replaying `review/applied-decisions.json` against the regenerated
junction-1 model gives **139 carried, 0 invalidated, 0 unknown**; the three findings with no
decision are all warnings, so `blockers_unresolved` stays 0. Stage 4 on a copy with the migrated
submission produces a reviewed model with identical lane and connector ids and exactly the three
status changes above. `validate-map` passes, and `convert` plus `tools/check_dataset.py` on that
copy report `sanity_check PASS` and `result OK`, worst turn 10.1° per step, 0 steps over 30°.

`routes.json` and `signals.json` are pinned to the fingerprint, so junction-1's have to be redrawn
— as they already did, from Keith's own 15:50 `apply-review` run.
