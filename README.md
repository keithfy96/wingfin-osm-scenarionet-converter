# Wingfin — OpenStreetMap → ScenarioNet converter

Turns a raw OpenStreetMap extract into a lane-level driving map that MetaDrive can
load and drive, with a human review step in the middle.

OSM describes roads as centrelines with tags. A driving simulator needs individual
lanes, with widths, polygons, and an explicit answer to "which lane can you get to
from this lane". Most of that answer is not in the source data — it has to be
inferred from lane counts, turn tags and junction geometry. This repo does the
inference, **shows you every place it had to guess**, lets you decide those cases in
a browser, and only then writes the dataset.

Six stages, each one refusing to run on a hand-off it cannot verify:

```
  ┌── 1 ──────┐  ┌── 2 ─────────┐  ┌── 3 ──────┐  ┌── 4 ──────┐  ┌── 5 ────────┐  ┌── 6 ──────┐
  │  fetch    │→ │ generate-map │→ │  inspect  │→ │  apply-   │→ │  validate-  │→ │  convert  │
  │           │  │              │  │  --view   │  │  review   │  │  map        │  │           │
  │ acquire + │  │ build lanes  │  │  review   │  │ regenerate│  │ is it self- │  │ ScenarioNet
  │ normalize │  │ + connectors │  │ (browser, │  │ with your │  │ consistent? │  │ pickles   │
  │           │  │ + findings   │  │  manual)  │  │ decisions │  │             │  │           │
  └───────────┘  └──────────────┘  └───────────┘  └───────────┘  └─────────────┘  └───────────┘
    source/        lane-model/       review.json    lane-model/    reports/map-     scenarionet/
    normalized/    preliminary       (you export    reviewed       validation       *.pkl
                   .json             it by hand)    .json
```

Everything lives in a **workspace** — one directory per map extract, holding the
source, every intermediate model, the reports, and the browser views. Workspaces are
gitignored.

---

## Setup

```bash
uv sync --dev
uv run osm-scenario --help
```

`workspaces/junction-1` is the working example throughout. `workspaces/mosque` is an
older snapshot kept for the docs.

---

## How to use

### Stage 1 — acquire and normalize

```bash
uv run osm-scenario fetch \
  --osm-file path/to/map.osm \
  --workspace workspaces/junction-1 \
  --driving-side left
```

Creates the workspace. Give it exactly one source — `--osm-file`, `--place "Name"`,
or `--bbox WEST SOUTH EAST NORTH` — and `--driving-side` is required, there is no
default. It copies the OSM into `source/map.osm`, applies the `public-driving-v1`
road-selection policy, builds a directed graph, reprojects it into local metres, and
audits the source data for missing lane counts, broken connectivity, restrictions and
signals.

### Inspect — the visual checkpoints

```bash
uv run osm-scenario inspect --workspace workspaces/junction-1 --view stage-1
```

`--view` takes five values:

| View | What you see |
| --- | --- |
| `source` | Raw acquired OSM — which ways were included, which excluded, and why |
| `normalized` | The directed, projected road graph |
| `audit` | Stage 1B data audit: missing tags, restrictions, crossings, signals, direction warnings, plus OSM way/node search |
| `stage-1` | The combined Stage 1 view (default) |
| `review` | **The Stage 3 decision surface** over a generated lane model |

Each writes a single self-contained HTML file into `<workspace>/inspection/`. Open it
in a browser — no server needed.

### Stage 2 — generate the lane model

```bash
uv run osm-scenario generate-map \
  --workspace workspaces/junction-1 \
  --config config/default.yaml
```

Builds every lane, every junction movement, and a list of **findings** — the places
the generator had to infer something or found two sources of truth disagreeing.
Writes `lane-model/preliminary.json` and the read-only audit view
`inspection/stage-2-review-audit.html`.

`--config` is optional everywhere; without it you get built-in defaults, **not**
`config/default.yaml`.

### Stage 3 — review the findings (browser, manual)

```bash
uv run osm-scenario inspect -w workspaces/junction-1 --view review
```

Open `inspection/stage-3-review.html`. Each finding is a question about a specific
lane or connector, and you answer it one of five ways:

| Decision | Meaning | Allowed on a blocker? |
| --- | --- | --- |
| `unresolved` | Not answered yet | **No** |
| `accepted` | The generated proposal stands | Yes |
| `overridden` | You supply a different value | Yes |
| `not_applicable` | The question doesn't apply here | Yes — and this is the only thing that softens a Stage 5 error |
| `ignored` | Parked to stop crowding the queue | **No** — warnings only |

When you're done, the page downloads `review.json`. A browser can't write to disk, so
this file is the hand-carried exchange between the page and the CLI.

### Stage 4 — apply the review

```bash
uv run osm-scenario apply-review \
  -w workspaces/junction-1 \
  --submission workspaces/junction-1/review.json \
  --config config/default.yaml
```

Checks the review still matches the model it was made against — workspace, source
checksum, generation fingerprint, and a per-finding evidence checksum — then
**regenerates** the map with your decisions folded in. It never patches the old model
in place, because changing a lane count renames every lane, connector and finding
downstream of it.

Writes `review/reviewed.osm` (source plus the tags your decisions materialised),
`review/applied-decisions.json` (the audit record), `lane-model/reviewed.json`, a
before/after comparison, and `inspection/stage-4-comparison.html`.

`source/map.osm` is never written — it's acquisition evidence, and Stage 4
re-checksums it afterwards to prove it didn't move.

### Stage 5 — validate

```bash
uv run osm-scenario validate-map -w workspaces/junction-1 --config config/default.yaml
```

Read-only. Asks one question: is the reviewed map geometrically and topologically
self-consistent? Writes `reports/map-validation.{json,md}` and
`inspection/stage-5-validation.html`, and **exits non-zero if it failed** — so a
pipeline can't read "wrote a report" as "the map is fit to convert".

### Stage 6 — convert

```bash
uv run osm-scenario convert -w workspaces/junction-1 --config config/default.yaml
```

Writes the ScenarioNet dataset into `<workspace>/scenarionet/`. Without a route this
is **map-only**: MetaDrive can load it and check it, but not drive it.

To make it drivable you have to choose a route, because `ScenarioEnv` has no
start-and-end setting — it navigates by replaying a recorded car's positions, so the
route has to be *in the file*:

```bash
# 1. pick routes: open inspection/stage-6-route-builder.html, click a start lane,
#    click an end lane, name it, add it, download routes.json
# 2. rebuild with the routes
uv run osm-scenario convert -w workspaces/junction-1 --config config/default.yaml \
  --routes workspaces/junction-1/routes/routes.json
```

Each named route becomes one scenario with a synthetic ego car driving it.

### Simulate

MetaDrive runs on Python 3.8 / numpy 1.24; this repo runs 3.10 / numpy 2.2. So the
runners are invoked with MetaDrive's own interpreter:

```bash
# load-and-check, no simulator needed
/home/keith/Desktop/work/wingfin/metadrive/.venv/bin/python \
  tools/check_dataset.py workspaces/junction-1/scenarionet

# actually drive it
/home/keith/Desktop/work/wingfin/metadrive/.venv/bin/python \
  tools/drive.py workspaces/junction-1/scenarionet --render 3D
```

Use `tools/drive.py`, not `python -m scenarionet.sim`. Both load the dataset
correctly, but in 3D `sim.py` shows a broken map — MetaDrive's terrain defaults are
sized for short Waymo clips, not for a road network — and none of the settings that
fix it are reachable from that entry point. `drive.py` measures each scenario and
picks a terrain size and texture resolution that fit. `--render` also accepts
`none`, `offscreen`, `2D` and `semantic`.

---

## How it works

### The workspace is the unit of state

```
<workspace>/
  source/
    map.osm                 the acquired OSM — written once, never again
    manifest.json           the ledger: stage_1b, stage_2, stage_4, stage_5, stage_6
  normalized/
    road-network.graphml    WGS84 (Stage 1A)
    road-network-local.*    projected into local metres (Stage 1B)
  lane-model/
    preliminary.json        Stage 2 output
    reviewed.json           Stage 4 output
  review/
    reviewed.osm            source + the tags your decisions materialised
    applied-decisions.json  what was applied, and the whole submission
  reports/                  every stage's JSON + Markdown report
  inspection/               one self-contained HTML page per stage
  routes/routes.json        hand-downloaded from the route builder
  scenarionet/              dataset_summary.pkl, dataset_mapping.pkl, sd_*.pkl
```

Each stage records its result and a sha256 into `source/manifest.json`, and the next
stage refuses to start if what it signed has moved since:

```
source/map.osm sha ──► preliminary.json + a generation fingerprint
                            │
                            ├─ Stage 3 binds review.json to that fingerprint,
                            │  and each decision to its own evidence checksum
                            ▼
     Stage 4 refuses a review whose fingerprint or evidence drifted
                            ├─ signs lane-model/reviewed.json into manifest.stage_4
                            ▼
     Stage 5 refuses a model whose sha moved since Stage 4 signed it
                            ├─ records pass/fail into manifest.stage_5
                            ▼
     Stage 6 refuses a model Stage 5 did not pass
```

Nothing downstream re-decides anything upstream. Stage 4 owns "what did the reviewer
conclude"; Stage 5 owns only "is the result self-consistent". Stage 5 cannot answer a
finding, and Stage 4 cannot declare a map valid.

### The two things Stage 2 produces

Everything is defined in `src/osm_scenario/lane_model.py`, all Pydantic models with
`extra="forbid"`.

A **lane** (`LaneFeature`) is one lane of one road segment: its source way IDs, its
index and direction, road class, width, speed limit, a centreline, a polygon, left
and right boundaries, its neighbours, its turn permissions, and its entry and exit
links.

A **connector** (`ConnectorFeature`) is one junction movement: from this lane, to
that lane, through this node, with a signed turn angle, a movement class
(`reverse` / `left` / `slight_left` / `through` / `slight_right` / `right`) and a
status of `active`, `forbidden`, or `review_required`.

The distinction that matters most:

- A road **carrying on through a node** is a *continuation*. No connector is created;
  the lane's `exit_lanes` simply names the next **lane**.
- A road **turning at a junction** is a *connector*. `exit_lanes` names the
  **connector**, and only when it's active.

So `entry_lanes` and `exit_lanes` hold a mix of two kinds of ID. In `junction-1`'s
reviewed model, 257 distinct lane IDs and 83 distinct connector IDs appear in those
lists — the 83 being exactly the active connectors, since forbidden and
review-required ones are never wired in. Any lookup that assumes one kind of ID fails
silently on the other, and Stage 6 has to resolve the connector IDs back to lanes
because ScenarioNet only understands lane IDs.

### Conventions that will bite you

**Lane indices run centre-out.** `idx0` hugs the centreline; `idx(n−1)` is kerbside.
`driving_side` is `left` for junction-1:

```
  way 776370584, 3 lanes, direction of travel ──────────────►

 ═══════════════════════════════════════════════════════ KERB ══
        idx2/3   nearside  (kerbside)
 ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─
        idx1/3   middle
 ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─
        idx0/3   offside   (against the centreline)
 ══════════════════════════════════════════════ CENTRELINE ══
```

- **`signed_turn_angle` is CCW-positive** — `+` is a left turn, `−` is a right turn.
- **`direction: forward|backward`** is relative to OSM way node order, *not* to
  oncoming-ness. A "backward" lane is not necessarily oncoming traffic.
- **OSM connectivity is via shared nodes.** Relations only carry turn restrictions —
  a missing connection is never a missing relation.

### What Stage 2 actually does

`build_lane_model` in `src/osm_scenario/generation.py` is pure — no filesystem — which
is why Stage 4 can re-run the identical function with your decisions applied. Roughly:

1. **Lanes, one graph edge at a time.** Lane count from tags or inference, width and
   speed from tags or config defaults, then geometry: offset the road centreline
   sideways per lane, buffer it to a polygon, derive left/right boundaries. IDs are
   content-addressed hashes, so the same input always gives the same ID.
2. **Way-level findings merged**, so a road split into five segments asks its lane-count
   question once rather than five times.
3. **Junctions.** For each node, group the outgoing lanes by carriageway.
4. **Allocate whole approaches before deciding individual lanes.** Which lane peels off
   at a junction is a question about the approach as a whole, not about each lane
   separately — so a diverge (`_balanced_approach_assignment`) and a merge
   (`_balanced_merge_assignment`) are resolved first. This is what stops a middle lane
   being fed by nothing while two approaches pile onto the same outer lane.
5. **Per-lane movements.** Classify continuation vs turn, pick the target lane, compute
   the turn angle, build a Bezier curve through the junction node, apply `turn:lanes`
   permissions, mark anything genuinely ambiguous.
6. **Turn restrictions** from OSM relations, including via-way chains.
7. **Emit connectors**, wire the active ones into the lanes, and raise a blocker for
   each one still marked `review_required`.
8. **Merge tapers, traffic signals, stop lines**, then package it all up with a
   generation fingerprint.

`src/osm_scenario/topology.py` holds the geometry and classification helpers this
leans on: `signed_turn_angle`, `classify_movement`, `movement_side`,
`side_lane_index`, `connector_curve`, and the turn-restriction resolvers.

### The standing rule: surveyed tags outrank inferred angles

`turn:lanes` is surveyed evidence of which movements are *permitted*. The movement
class is *inferred* by binning a turn angle against threshold constants. Where the two
disagree, the tag must never be the reason a lane loses its only exit — that would cut
the drivable network on the strength of a magic number. The generator keeps the
movement and raises a finding instead.

The corollary, which matters when you're tempted to make a warning go away: **never
fix a tag-versus-geometry conflict by making the finding stop being raised.** Fix the
mapping and keep the question.

### Findings, and what makes one a blocker

Nine rules can raise a finding: `lane_count_inference`, `lane_width_default`,
`speed_default`, `turn_permission_geometry_conflict`,
`lane_transition_count_mismatch`, `ambiguous_connector`, `signal_lane_association`,
`inferred_stop_line`, `restriction_effect_review`. Each is either a `warning` or a
`blocker`; blockers gate Stage 4.

Two of them can be answered by writing an OSM tag back (`lanes` and `turn:lanes`), so
the next generation run reads your decision straight out of the source. The rest stay
live as overrides in `applied-decisions.json`. Four rules that *would* need a tag are
**refused by name** rather than half-applied — a review that appears to have been
applied but wasn't is worse than a run that stops.

One thing that trips people up: **a reappearing finding is not open work.** Accepting
an inference leaves the map unchanged, so the same question comes back on every
regeneration. Only Stage 4's before/after comparison can tell a re-asked question
from a genuinely unresolved one, which is why Stage 5 reads its `findings_still_open`
field rather than re-deriving it.

### What Stage 5 checks

Geometry (non-finite, empty, self-intersecting, centreline outside its polygon),
references (dangling or non-reciprocal entry/exit and neighbour links), connectors
(endpoints that don't actually meet the lanes they join, active-but-unreachable,
inactive-but-drivable), restrictions, signals, and network boundary facts.

Two calibrations worth knowing about, both measured against `junction-1` rather than
picked:

- A lane's centreline lies *on* its own polygon boundary by construction, so the
  containment test needs a 1e-9 m epsilon — otherwise every lane in the map fails.
- Short connectors degenerate to a stub whose far end stays on the incoming lane, so
  the "does this connector meet its lane" threshold is 0.05 m, deliberately coupled to
  the same threshold in `connector_curve`. 32 of junction-1's 83 active connectors are
  stubs; an exact-endpoint assertion would fail every merge in the map.

Issues on a feature you marked `not_applicable` in Stage 3 are reported as **warnings**
naming the finding that dispositioned them, rather than errors. Stage 5 re-derives
conditions from the model, so it will happily re-detect something a human already
ruled out — re-raising it as an error would make the review pointless. There is no
suppression list; the only place to disposition an issue is Stage 3.

A lane that stops dead is usually just the edge of the extract. All 39 of junction-1's
no-entry/no-exit lanes end at a node that terminates every source way containing them.
A lane stopping at a node the road runs *through* is the real defect — reporting the
first as an error would bury the second under 39 false alarms, so extract-edge lanes
are reported as boundary facts.

### What Stage 6 writes

`map_features` is a flat dict: one `LANE_SURFACE_STREET` per lane (centreline
polyline, polygon, speed, width, entry/exit, neighbours) and one
`ROAD_EDGE_BOUNDARY` per lane boundary. Connector IDs in the entry/exit lists are
resolved to the lane on the other side.

With `--routes`, each route is re-planned in Python (Dijkstra over the lane graph,
splicing connector centrelines for junction hops), resampled at 10 Hz, and written as
a synthetic ego car at `tracks["ego"]["state"]["position"]`. **MetaDrive never reads
`routes.json`** — it reads the pickles, and the route *is* those positions.

The route builder page previews the same geometry in the browser; Python re-derives
it. The two agree to within 3.5 m over 1.1 km across 40 real routes, and both sides
are deliberately covered by the same test cases (`web/test/route/geometry.test.ts`
and `tests/unit/test_ego_route.py`) — if they ever diverge, the page would offer
drives the converter refuses.

Three pickles land in `scenarionet/`: one `sd_*.pkl` per scenario, plus
`dataset_summary.pkl` and `dataset_mapping.pkl`. They're written through a custom
pickler because numpy 2 stamps a reference to `numpy._core` into the stream, which
numpy 1.24 on Python 3.8 — the MetaDrive side — cannot resolve. Anything that changes
how arrays are written must keep the stream free of version-specific module names.
MetaDrive is deliberately not a dependency of this package; the schema is pinned by a
test that loads MetaDrive's real `ScenarioDescription` when the checkout is present.

### The browser pages

Every inspection page is a single self-contained HTML file with its data inlined as a
JSON payload — no server, no build step at view time. The two interactive ones (the
Stage 3 reviewer and the Stage 6 route builder) host TypeScript clients from `web/`,
compiled by esbuild into `src/osm_scenario/assets/*.js` and **committed**, so an
installed CLI never needs Node.

---

## Configuration

`config/default.yaml`, validated by `src/osm_scenario/config.py`:

| Key | Default | Controls |
| --- | --- | --- |
| `driving_side` | `null` | `left` / `right` |
| `coordinate_origin` | `null` | Local projection origin |
| `lane_width_defaults.vehicle` | `3.5` | Lane width when OSM has none |
| `default_speed_kph` | `50.0` | Fallback speed |
| `speed_defaults_kph` | per `highway` tag | motorway 110, service 30, … |
| `tag_inference.infer_missing_lane_count` | `true` | Infer lane counts when untagged |
| `lane_selection.side_movement_min_degrees` | `10.0` | When a turn counts as a side movement |
| `lane_selection.sharp_movement_review_degrees` | `130.0` | When a sharp movement gets flagged |
| `lane_geometry.merge_taper_length_m` | `30.0` | Merge taper geometry |

Unknown keys are rejected. Every command that takes `--config` falls back to built-in
defaults when the flag is absent — `config/default.yaml` is not loaded automatically.

---

## Repo layout

```
src/osm_scenario/
  cli.py              the six commands
  acquisition.py      Stage 1A       normalization.py     Stage 1B
  osm_source.py       raw OSM XML    stage1b_data_audit.py
  generation.py       Stage 2 — the generator
  lane_model.py       the data model
  topology.py         geometry, movement classification, restrictions
  review.py           Stage 3        apply_review.py      Stage 4
  validation.py       Stage 5        conversion.py        Stage 6
  ego_route.py        route planning + the synthetic ego car
  inspection.py, comparison_view.py, validation_view.py,
  reachability_view.py, route_builder_view.py     the HTML views
  assets/             committed compiled browser clients
web/                  TypeScript sources for those clients
tools/                drive.py, check_dataset.py   (run under MetaDrive's venv)
config/default.yaml
docs/policies/        road selection, Stage 2 algorithms, finding reference
docs/mapping-algo-changes/   a dated record of every corrected mapping mistake
guide/project-guide.md, "stage 3,4,5 guide.md"
```

---

## Development

```bash
uv run pytest
uv run ruff check
cd web && npm test        # the browser clients
```

`ruff format --check` fails on some pre-existing files and is not a gate.

`uv run pytest` tells you the code is correct; it does **not** tell you the dataset
loads. Both reference checkouts run Python 3.8 / numpy 1.24 while this repo runs
3.10 / numpy 2.2, so the interpreter the tests use is exactly the one where a version
fault is invisible. Check it from the other side with `tools/check_dataset.py`.

### Reference checkouts

Neither is a dependency; both are read-only references for "what does MetaDrive
actually do with this field".

- `/home/keith/Desktop/work/wingfin/metadrive/` — MetaDrive 0.4.3, the format this
  targets
- `/home/keith/Desktop/work/wingfin/scenarionet/` — ScenarioNet, plus the Waymo /
  nuPlan / nuScenes / Argoverse converters worth comparing our output against

`tests/unit/test_conversion.py` loads MetaDrive's real `ScenarioDescription` from the
first path and is `skipif`-marked on the directory being absent — so a moved or
renamed checkout **silently drops the schema gate** rather than failing.

See [`guide/project-guide.md`](guide/project-guide.md) for artifact ownership,
[`stage 3,4,5 guide.md`](stage%203,4,5%20guide.md) for the review stages in depth, and
[`docs/policies/`](docs/policies/) for the road-selection policy and the Stage 2
algorithms.
