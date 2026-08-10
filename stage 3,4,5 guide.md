# Stage 3, 4, 5 Guide

How the review stages hand work to each other, what each one owns, and what it
refuses to do. Stage 6 is included at the end because it is the thing all three
are gating, and because it does not exist yet.

Stages 1 and 2 are covered elsewhere: `README.md` and `guide/project-guide.md`
for Stage 1, `docs/policies/stage-2-generation-v1.md` for Stage 2.

---

## The chain in one picture

```
 STAGE 3 (browser, manual)          STAGE 4 (apply-review)              STAGE 5 (validate-map)          STAGE 6
 ─────────────────────────          ──────────────────────              ──────────────────────          ───────
 inspection/stage-3-review.html     reads  review.json                  reads lane-model/reviewed.json  NOT BUILT
        │  reviewer decides                │                                   │  (never writes it)
        ▼                                  ├─► review/reviewed.osm             ▼
    review.json  ──────────────────────────┤   (tags; source/map.osm         reports/map-validation.json
    (the export, hand-made)                │    is re-checksummed, never       reports/map-validation.md
                                           │    written)                       manifest → stage_5
                                           ├─► review/applied-decisions.json          │
                                           ├─► lane-model/reviewed.json               │ status != passed
                                           │   (REGENERATED, not patched)             │ → exit 1
                                           ├─► reports/reviewed-comparison.{json,md}  ▼
                                           ├─► inspection/stage-4-comparison.html   convert → scenario.pkl
                                           └─► manifest → stage_4                     (no command, no module)
```

---

## Commands

```bash
uv run osm-scenario inspect      -w workspaces/junction-1 --view review      # Stage 3 view
uv run osm-scenario apply-review -w workspaces/junction-1 \
                                 --submission workspaces/junction-1/review.json   # Stage 4
uv run osm-scenario validate-map -w workspaces/junction-1                    # Stage 5
```

`-w` is required on all of them. `--config config/default.yaml` is optional and
defaults to `config_version: 1`.

---

## Stage 3 — the decision surface (`review.py`, manual)

`inspect --view review` renders `inspection/stage-3-review.html`: a single-file
page carrying the whole lane model as `window.__REVIEW_PAYLOAD__`, hosting the
compiled TypeScript client from `web/` (committed as
`src/osm_scenario/assets/review-client.js`, so an installed CLI never needs
Node).

The map itself comes from `generation.build_review_payload`, the same builder
the read-only Stage 2 audit uses. Stage 3 adds the **identity a review is bound
to** — workspace, source checksum, generation fingerprint, preliminary-model
checksum — and per-finding scoping for bulk actions.

The reviewer answers findings and exports `review.json`. That file is the only
review artifact Stage 4 accepts. Its shape:

| Key | What it holds |
| --- | --- |
| `submission_version` | Schema version of the export |
| `exported_at` | When the reviewer exported |
| `identity` | Workspace, checksums, generation fingerprint the decisions were made against |
| `decisions` | One entry per finding: `finding_id`, `rule`, `status`, `decided_at`, `evidence_checksum`, `location`, and a `value` for overrides |
| `readiness` | Whether the reviewer considers the review complete |

Decision statuses:

| Status | Meaning | Allowed on a blocker? |
| --- | --- | --- |
| `unresolved` | Not answered | **No** — Stage 4 refuses |
| `accepted` | The proposal stands as generated | Yes |
| `overridden` | The reviewer supplies a different value | Yes |
| `not_applicable` | The finding does not apply here | Yes — and this is the only thing that softens a Stage 5 error |
| `ignored` | Parked to stop crowding the queue | **No** — warnings only |

`junction-1` currently carries 140 decisions.

Stage 3 is read-only with respect to the model. It records what a human
concluded and nothing else.

---

## Stage 4 — `apply-review` (`apply_review.py`)

Turns those conclusions into a **second lane model**, built by the same
`build_lane_model` core Stage 2 used.

### Gates, in order

1. `_check_stage_1_and_2` — the workspace is a passed Stage 1B whose artifacts
   match their recorded checksums.
2. `_check_submission` — submission schema, string identifiers, source
   checksums, **generation fingerprint**, per-finding **evidence checksums**,
   allowed decision types, referenced OSM/generated features, and **no
   unresolved or ignored blockers** (`apply_review.py:208`).

Any failure stops the run non-zero. A review made against a model that has since
moved is not migrated silently.

### What it writes

| Artifact | What it is |
| --- | --- |
| `review/reviewed.osm` | The source OSM plus the tags the review materialised |
| `review/applied-decisions.json` | `applied_at`, `materialised_osm_tags`, `non_osm_overrides`, and the whole `submission` — the audit record |
| `lane-model/reviewed.json` | Fully regenerated reviewed lane model |
| `reports/reviewed-comparison.{json,md}` | Preliminary versus reviewed |
| `inspection/stage-4-comparison.html` | The same comparison, visual |
| `source/manifest.json` → `stage_4` | Status, artifact paths and checksums |

Deliberately **not** `review/review.json` — that is one path segment away from
the hand-made Stage 3 export that is its input, and two files a directory apart
with the same name is a mistake waiting to be made.

### The split: OSM-native versus override

`_overrides_from` divides the decisions in two:

- **OSM-native** — the decision's effect is a tag, so it is written into
  `review/reviewed.osm` and generation reads it back out on the next run.
- **Non-OSM overrides** — connector selection, signal-to-lane association,
  inferred stop-line placement — stay live in `applied-decisions.json` and are
  handed to the generator as `ReviewOverrides`.

**Two rules write a tag today**, and both had to clear the same bar: the tag
written must **invert what `generation.py` reads**, and the override must be
proved by regenerating and reading the lane back — not by the tag appearing in
the file.

| Rule | Tag written | How the round trip is guaranteed |
| --- | --- | --- |
| `lane_count_inference` | `lanes` (whole carriageway) or `lanes:<direction>` | Both are `_directional_lane_count`'s explicit branches, which short-circuit the inference |
| `turn_permission_geometry_conflict` | `turn:lanes` or `turn:lanes:<direction>` | The reviewer's movement is set into the same `\|`-split slot `_turn_permissions` indexes, so the kerbside-first ordering is never reimplemented |

The remaining four in `_OSM_NATIVE_RULES` are **refused by name**, not
half-applied — a review that appears to have been applied but was not is worse
than a run that stops:

| Rule | Tag it would need to write |
| --- | --- |
| `speed_default` | `maxspeed` |
| `lane_width_default` | `width` |
| `lane_transition_count_mismatch` | `lanes` on the destination way |
| `restriction_effect_review` | the turn restriction relation |

### Regenerated, never patched

The reviewed model is rebuilt from `reviewed.osm` through the **same**
public-driving-v1 road selection Stage 1A uses and Stage 1B's `_project_graph`.
Both matter:

- Rebuilding from OSM without the selection policy readmits every excluded way.
- Using a different projection puts the reviewed model in a different coordinate
  frame from the preliminary one.

Patching `preliminary.json` in place would be wrong regardless: a lane-count
change renames lane IDs, connector IDs and every finding downstream of them.
Only running the generator again gets all of that right.

### Two invariants it enforces rather than asserts

- **`source/map.osm` is never written.** It is acquisition evidence. Stage 4
  re-checksums it after the run and fails if it moved.
- **The reviewed model is keyed on `sha256(review/reviewed.osm)`**, not on a
  rebuilt GraphML — osmnx stamps a build timestamp into GraphML, which would
  mint a new fingerprint for a byte-identical model on every run.

### Findings are joined by question, not identifier

A finding's `identifier` covers its `affected_feature_ids`, which for a
way-level rule is that way's lane list. Re-laning a road therefore renames every
question about it, and a comparison keyed on the identifier would report the
same question as both removed and created. `_question_key` keys way-level rules
on the way instead, with a discriminator where one way legitimately asks the
same rule twice (`lane_count_inference` uses `direction`).

This join is also what produces `findings_still_open` — the field Stage 5 reads
rather than re-deriving. **A reappearing blocker is not open work:** accepting
an inference leaves the map unchanged, so the same question comes back on every
regeneration. Only Stage 4 can tell the two apart.

---

## Stage 5 — `validate-map` (`validation.py`)

Decides whether the Stage 4 model is fit to convert. **Read-only** — it never
writes `lane-model/reviewed.json`, so a failed validation costs nothing but a
report.

### Gates

1. `_check_stage_4` — the manifest says `stage_4.status == passed` **and**
   `sha256(lane-model/reviewed.json)` still matches what Stage 4 signed. Checked
   before any geometry: a stale model makes every later answer describe a map
   nobody reviewed.
2. `_read_comparison` — Stage 4's comparison report exists and is new enough to
   carry `findings_still_open`.

### Checks

| Function | Covers |
| --- | --- |
| `_geometry_issues` | Non-finite, empty, self-intersecting geometry; centreline outside its polygon |
| `_reference_issues` | Dangling or non-reciprocal entry/exit and neighbour references |
| `_connector_issues` | Connector endpoints that do not meet the lanes they join |
| `_restriction_issues` | Movements violating reviewed restrictions or turn permissions |
| `_signal_issues` | Unassociated signals, invalid stop-line associations |
| `_boundary_report` | Lanes without entry or exit, routing components |

Plus one synthesised issue, `open_blocking_finding`, if `findings_still_open` is
non-empty.

### Triage — a judgement is not a silence

Every issue is sorted into errors or warnings by `_dispositioned_osm_ids`:

- The issue's OSM feature was marked **`not_applicable`** by the reviewer in
  Stage 3 → **warning**, carrying `dispositioned_by: <finding id>`.
- Otherwise → **error**.

Stage 5 re-derives conditions from the model, so it will happily re-detect
something a human already looked at and ruled out — `junction-1`'s traffic
signal at the upstream edge of the extract is exactly that. Re-deriving it as an
error would make the review pointless: the reviewer would have to answer the
same question in a second place, and answering it there would still not silence
this one. The issue is still reported, still carries its feature IDs, and still
names the finding that dispositioned it — it just stops failing the map.

**Corollary: the only place to disposition an issue is Stage 3.** There is no
suppression list in Stage 5.

### Two calibrated constants, both measured against `junction-1`

- **`_POLYGON_EPS_M = 1e-9`** — a centreline lies on its own polygon boundary by
  construction, because the polygon is `centerline.buffer(width / 2)`
  (`generation._lane_surface`). `Polygon.contains` is false for every lane in
  the map, and even `covers` fails on 11 of 285 by floating-point noise: worst
  point-to-polygon distance 2.8e-15 m, worst excursion length exactly zero.
- **`_CONNECTOR_MEET_M = 0.05`** — coupled to the 0.05 m gap threshold in
  `topology.connector_curve`. Below that gap the curve degenerates to a
  three-point stub whose far end stays on the *incoming* lane. 32 of
  `junction-1`'s 83 active connectors are stubs, so an exact-endpoint assertion
  would fail every merge in the map. **Keep the two in step.**

### A lane that stops dead is usually the extract edge

All 39 of `junction-1`'s no-entry and no-exit lanes end at a node that
terminates every source way containing it: the road does not continue there
because the extract was cut there. A lane stopping at a node the road runs
*through* is the real defect, and there are none. Reporting the first as an
error would bury the second under 39 false alarms — so extract-edge lanes are
reported as boundary **facts**, not issues.

### Outputs and exit code

`reports/map-validation.json`, `reports/map-validation.md`, and a `stage_5`
record in `source/manifest.json` carrying status, the validated model's path and
sha256, issue counts, and the boundary facts.

Errors → `status: failed` → **the CLI exits non-zero**. A failed validation is a
result, not a crash — but the exit code has to say so, or a pipeline reads "wrote
a report" as "the map is fit to convert".

### `junction-1` as of this writing

```
status   passed
errors   0
warnings 1   unassociated_signal (dispositioned)
boundary lanes_at_the_extract_edge 39 · without_entry 19 · without_exit 20
         routing_components [121, 70, 44, 22, 19, 9]
```

---

## Stage 6 — not built

There is no `convert` command and no module for it. The only references in the
codebase are the plan and Stage 5's markdown line, "an error means the map is not
fit to convert; Stage 6 stays blocked until it is gone."

Planned shape, from `docs/implementation-plan/README.md`:

- Convert the reviewed model directly into `ScenarioDescription.map_features` —
  centreline, polygon, type, speed, boundaries, neighbours, entry/exit.
- Map-only: `tracks={}`, `dynamic_map_states={}`, a minimal one-step scenario
  envelope, no fabricated vehicles or traffic-light timing.
- Preserve source OSM IDs and review provenance in metadata.
- Write `scenario.pkl`, `dataset_summary.pkl`, `dataset_mapping.pkl`.
- Keep MetaDrive/ScenarioNet **out of the converter's core dependencies**: run
  `ScenarioDescription.sanity_check()`, reload the dataset, build the MetaDrive
  map and verify a route across representative junctions in a lockfile-pinned
  isolated environment.

---

## What ties the stages together

Each stage hands off through `source/manifest.json` plus a checksum, and each
refuses to run on a hand-off it cannot verify:

```
source/map.osm sha ──► preliminary.json + generation fingerprint
                              │
                              ├─ Stage 3 binds review.json to that fingerprint
                              │  and to a per-finding evidence checksum
                              ▼
       Stage 4 refuses a review whose fingerprint or evidence drifted
                              │
                              ├─ signs lane-model/reviewed.json into manifest.stage_4
                              ▼
       Stage 5 refuses a model whose sha moved since Stage 4 signed it
                              │
                              ├─ records status into manifest.stage_5
                              ▼
       Stage 6 is meant to refuse a model Stage 5 did not pass
```

And the ownership boundary is strict in one direction: **nothing downstream
re-decides anything upstream.** Stage 4 owns "what did the reviewer conclude".
Stage 5 owns only "is the resulting geometry self-consistent". Stage 5 cannot
answer a finding, and Stage 4 cannot declare a map valid.

The standing rule from `CLAUDE.md` applies across all three: a tag-versus-geometry
conflict is never fixed by making the finding stop being raised. Fix the mapping
and keep the review.
