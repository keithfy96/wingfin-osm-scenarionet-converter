# Traffic lights: placed at Stage 6, baked as a tape, driven live for training

- **Date:** 2026-08-11 17:08:45
- **Asked by:** Keith — "how to add traffic lights to junctions, can I do that?", then "could I
  possibly add the stop lights where I want them at stage 6? ... I want to avoid the symptom of
  it consistently playing at the same time and overtraining ... Remember that I care more about
  using the map to train agents than retracing the ego vehicle"
- **Files changed:** `src/osm_scenario/signal_plan.py` (new),
  `src/osm_scenario/signal_builder_view.py` (new), `web/src/signal/*` (new),
  `web/test/signal/*` (new), `src/osm_scenario/conversion.py`, `src/osm_scenario/cli.py`,
  `src/osm_scenario/lane_payload.py`, `web/build.mjs`, `tools/signal_control.py` (new),
  `tools/drive.py`, `tools/check_dataset.py`, `tests/unit/test_signal_plan.py` (new),
  `tests/unit/test_conversion.py`, `CLAUDE.md`

**No change to `generation.py`, `topology.py`, `ConverterConfig` or the lane model schema**, so
no `docs/mapping-algo-changes/` entry and the generation fingerprint does not move.

## Symptom

`conversion.py:470` wrote `"dynamic_map_states": {}` with a comment saying signal timing was
out of scope because "a fabricated phase plan would be indistinguishable from a surveyed one
once it is inside a pickle". So a converted dataset had no traffic lights at all, and the
`signals` list Stage 2 already extracts stopped at the pickle boundary.

## Fundamental cause

Three facts about MetaDrive 0.4.3, each measured from the checkout rather than assumed.

**1. There is no traffic-light controller.** `ScenarioLightManager.after_step` indexes
`state["object_state"]` by `episode_step` and calls `set_status`. That is the whole mechanism.
It is the only light manager in the package — `metadrive_env.py`, `pg_map.py` and
`traffic_manager.py` mention lights zero times, so procedurally generated maps have none
either. A light in a dataset is a tape, not a signal.

**2. A tape is identical on every episode**, which is exactly the overtraining Keith named. An
agent learns "step 340 is green" and looks like it is obeying signals when it is not.

**3. But lights can be driven live.** `metadrive/tests/vis_functionality/vis_traffic_light.py`
spawns `BaseTrafficLight` through `engine.spawn_object` and calls `set_green()`/`set_red()`
from a plain loop; `set_red` flips the collision mask on the invisible wall in real time. This
is what makes one scenario enough, instead of baking N copies of the same route with different
phase offsets.

And OSM cannot supply the plan. `highway=traffic_signals` carries presence and nothing else —
no cycle, no split, no offset. `junction-1` has one such node, `1927184932`, and it is not even
at a junction: node 0 of way `1173001826`, in no other way, 0 connectors, so Stage 2 filed
`signal_lane_association` (warning, medium) and bound it to the three lanes it *releases*.
Placement therefore has to be a person's judgement, like the choice of route.

## Fix

**`stage-6-signal-builder.html`**, a third Stage 6 page built from the same
`lane_payload.build_lane_payload` as the other two, so it cannot offer a lane or a movement the
dataset does not contain. Add a phase group, click the lanes it stops, set green / amber /
when-green-starts against a shared cycle, download `signals.json` stamped with the identity
block. A preview slider recolours every lane to the moment chosen, which is the only way to see
two arms at once. Surveyed signals are drawn dashed and never selected.

**Conflicts are reported, not solved.** Where two groups' movements **cross** or **merge into
one lane** at the same node, the page says so and how many seconds per cycle this plan runs
them green together. Deciding the phasing stays Keith's, as route choice is. `junction-1` has
21 junctions with more than one movement, 6 crossing pairs and 17 merging pairs, all at real
crossroads — node `1226982521` alone accounts for 3 crossings.

**`convert --signals`** writes both halves and both are needed:

- `dynamic_map_states`, one entry per signalled lane keyed on the lane id
  `ScenarioLightManager` looks up in `road_network.graph` — the **portable default**, so a
  stock ScenarioNet consumer gets working lights with no runtime component
- `metadata.signals`, the phase structure itself, marked `source: "synthesised"` — which is
  what answers the old comment's objection instead of avoiding it, and what a live controller
  reads

**`tools/signal_control.py`** subclasses `ScenarioLightManager` and replaces only where the
status comes from, keeping MetaDrive's own lane lookup and spawning. One offset is drawn per
episode and added to the **whole** plan: the gaps between groups are the plan, so randomising
each group separately would put crossing movements green at once. It draws from its own
generator rather than `self.np_random`, because `BaseEngine.seed` reseeds every manager from
the scenario index — a one-scenario dataset would otherwise get the same offset for the entire
training run, which is the fixed timing the manager exists to avoid.

**Three constraints that shaped this, none of them obvious:**

- **Timing cannot live in `config/default.yaml`.** `configuration_checksum` is an input to
  `generation_fingerprint` (`generation.py:2212`), so any field on `ConverterConfig` moves the
  fingerprint at the next `generate-map` and invalidates the live review. Same for
  `LANE_MODEL_SCHEMA_VERSION`, so `SignalAssociation` gains nothing either. It is a
  `convert`-time file, like `--routes`.
- **`stop_point` goes at the top level of a light entry, not inside `state`.** Everything in
  `state` is length-checked against the scenario length, so a 3-element position there passes
  only on a 3-step scenario, and `_get_episode_light_data` would read it as the old Waymo
  `[T, 2]` format besides.
- **The live controller cannot live in `src/osm_scenario/`.** MetaDrive imports it at runtime
  under Python 3.8 / numpy 1.24; the package targets 3.10 / numpy 2.

## Verification

`uv run pytest` **289 passed** (was 250). `uv run ruff check` clean. In `web/`:
`npm run typecheck` clean, `npm run test` **129 passed** (was 84), `npm run build` regenerates
all three bundles.

**MetaDrive's own schema accepts the lights.** `sanity_check` runs `_check_object_state_dict`
over `dynamic_map_states` too, and a new test converts a scenario with a light and puts it
through the real `ScenarioDescription`. Another pins our three colour strings against
`MetaDriveType` and through `simplify_light_status` — a misspelt red is not an error there, it
becomes `LIGHT_UNKNOWN`, whose collision mask is `AllOff`, so it is a light nothing stops for.

**The page works in a browser**, not only in tests. Loaded over a local server: lanes clickable
(popup names way `935525164`, lane 0/3, id `fc8a8a6a78b25e19`), the group card's phase strip
draws green 0–45%, amber 45–50%, red 50–100% of the cycle, and dragging the preview from 0.0 s
to 47.0 s turns the selected lane from green to red. The file the browser actually produced was
fed back through `convert --signals` and `check_dataset.py`: 1 light, `sanity_check PASS`, and
a colour tally of **green 321 / amber 30 / red 300** over 651 steps — exactly what a 60 s cycle
of 27/3/30 predicts, including the 51 steps that wrap past the end of the first cycle.

**The lights fire on the numbers.** With a three-group test plan (one light on the ego's route,
two on the crossroads at node `1226982521`), `tools/drive.py` reports the route arm turning
green at **step 500** for an authored start of 50.0 s, and the other two at step 600 for 60.0 s.

**The ego stops for a red light.** Under `--agent-policy idm`:

| | steps | arrive_dest | completion | below 0.2 m/s | min speed |
| --- | ---: | --- | ---: | ---: | ---: |
| `--agent-policy replay` (unchanged baseline) | 619 / 651 | True | 0.952 | 0 | 13.89 m/s |
| `--agent-policy idm`, light red on arrival | 528 / 651 | False | 0.549 | **34** | **0.09 m/s** |

The stop is unambiguously the light: at its first stop, step 466, the ego is **5.7 m** from the
red light on its own route lane, while the other two lights are 238 and 239 m away. It moves
off at step 500, when that light goes green.

The IDM run ends early with **`out_of_road`, lateral 4.26 m against a 4 m limit** — the lateral
controller losing the reference line, not the lights and not the data. `drive.py` now names the
termination reason for exactly this case, because `arrive_dest=False` alone does not
distinguish a wrong drive from a different one. An earlier attempt also *failed* to stop: the
plan had been timed against the replay speed, and the IDM ego reaches the same stop line at
43 s rather than 36 s, by which point the light was already green.

**Live lights vary per episode.** Three seeds gave phase offsets 25.0 s, 26.2 s and 33.0 s,
moving the green steps to 251/251/151, 162/262/262 and 331/231/331 respectively. The gaps
between groups are preserved in each.

**Nothing regressed.** Without `--signals`: 855 map features, one scenario, `main-route` still
903 m over 18 junction movements and 1 lane change, `dynamic_map_states == {}`,
`sanity_check PASS`. The offscreen 3D path still reports ground **+0.2 m, 0% above the road**
and `619/651, arrive_dest=True, completion 0.952` with the lights present, so the terrain work
from earlier today is untouched.

**The workspace was left without a signal plan.** The plans used above live in the session
scratchpad, not in `workspaces/junction-1/`. Placing the real lights is what the page exists
for, and it is Keith's call which junctions get them.
