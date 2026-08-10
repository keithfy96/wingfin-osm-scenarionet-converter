# The converted scenario now passes MetaDrive's own sanity check

- **Date:** 2026-08-10 23:44:31
- **Prompted by:** Keith — "can i just import them into metadrive?" — then, decisively,
  "you can actually check i have metadrive and scenario net installed in wingfin/wingfin-metadrive"
- **Files changed:** `src/osm_scenario/conversion.py`, `tests/unit/test_conversion.py`,
  `docs/implementation-plan/README.md`
- **Supersedes:** the closing section of
  `2026-08-10-23:14:34-stage-6-convert.md`, which recorded these field names as unverifiable

## Symptom

The answer to "can I just import them" was **no**. `ScenarioDescription.sanity_check()`
failed on `workspaces/junction-1/scenarionet/scenario.pkl`, and the dataset would not have
survived `read_dataset_summary` either.

The earlier Stage 6 work shipped a `_UNVERIFIED_FIELDS` table on the honest but wrong
premise that ScenarioNet could not be checked on this machine. It could: MetaDrive and
ScenarioNet are checked out at `wingfin/metadrive` and `wingfin/scenarionet`. That premise
went unchecked because the question asked was "is it a dependency of *this* repo", and the
answer to that was correctly no.

## Fundamental cause

Three defects, each one a place where the format is stricter than a reading of it suggests.

**1. `metadata` lacked `metadrive_processed`.** `METADATA_KEYS = {metadrive_processed,
coordinate, ts}` and `sanity_check` asserts the whole set. This was the first stop.

**2. `metadata["ts"]` was a Python list.** The last line of `sanity_check` is
`assert scenario[METADATA][TIMESTEP].shape == (scenario_length,)`. A list has no `.shape`,
so this is an `AttributeError` in a function whose other failures are readable assertions.

**3. The filename `scenario.pkl` is rejected by the loader.** This is the one no amount of
care about *content* would have found — the check is on the *name*:

```python
return os.path.basename(file_name)[:3] == "sd_" or all(char.isdigit() for char in file_name)
```

`read_dataset_summary` asserts `is_scenario_file` for every entry in the summary. A dataset
whose scenario is perfectly formed but readably named loads nowhere.

Underneath all three: the scenario dict was written against the format's *documentation*
rather than against its *validator*. The two disagree, and only one of them runs.

## Fix

`metadrive_processed: False` (what every ScenarioNet converter that is not MetaDrive
records), `ts` as `np.zeros(1)`, `coordinate` as `"metadrive"`, neighbours as lists rather
than bare ids, and `scenario_file_name()` building the name the way MetaDrive's own
`get_export_file_name` builds it. Both index files key on that one computed string, so they
cannot drift apart. The scenario id carries a 16-hex-digit fingerprint prefix — the full 64
stay in `metadata.provenance`, and the id ends up in the filename and in MetaDrive's logs.

`_UNVERIFIED_FIELDS` is **deleted**, from the module, the scenario metadata and the
conversion report. It existed to record doubt; the doubt is resolved, and a table of
"things we are unsure about" that is no longer true is worse than no table.

In its place, `test_the_scenario_passes_metadrives_own_sanity_check` runs MetaDrive's real
validator. It loads `scenario_description.py` by file path, registering bare `metadrive`,
`metadrive.scenario` and `metadrive.utils` package modules and supplying
`metadrive.utils.math.norm`, because `metadrive/__init__.py` imports panda3d and checking a
data structure needs no renderer. It skips where no checkout exists. So MetaDrive stays out
of this converter's dependencies — as the Stage 6 spec requires — while the schema stops
being a matter of opinion.

## Verification

`uv run pytest` **222 passed** (was 217; 5 new, none skipped on this machine).
`uv run ruff check` clean.

Against the real workspace, replicating `read_dataset_summary`'s assertions and then
`sanity_check`:

```
read_dataset_summary checks PASS -> ['sd_osm-scenario_v1_junction-1-ce2efbed720afb5e.pkl']
ScenarioDescription.sanity_check PASS
```

And the map itself is untouched by the schema fix — every count measured before the change
still holds:

| | |
| --- | ---: |
| lane features | 285 |
| total map features | 855 |
| exit edges | 294 |
| dangling references | 0 |
| `tracks` / `dynamic_map_states` | 0 / 0 |
| fingerprint vs `reviewed.json` | equal |

## Checked and deliberately left alone

Recorded because each looks wrong beside `scenarionet/converted_waymo_test_data` and is not:

- **Two-point polylines load.** `_sample_topology` skips a lane only at
  `len(polyline) <= 1`. All 281 are fine. An earlier note calling this a risk was wrong.
- **`ROAD_EDGE_BOUNDARY` is correct** — it is exactly `MetaDriveType.BOUNDARY_LINE`.
  `MetaDriveType.has_type()` returns False for it, but that function covers *object* types
  and map features never reach it.
- **`width` as a scalar stays.** Waymo stores an `(N, 2)` array; `ScenarioLane` ignores the
  field entirely and uses `VIS_LANE_WIDTH`. Ours is the surveyed width, under the format's
  own name for it.
- **Entry/exit ids resolving is stricter than the reference.** Waymo's `entry_lanes` hold
  ints that match none of its string `map_features` keys. Ours all resolve.
- **`coordinate` is a label, not a transform.** Nothing in MetaDrive 0.4.3 branches on it.

## Still not done

Nothing has been loaded by MetaDrive itself, rendered, or driven. That needs panda3d and a
GPU and belongs in the isolated environment, which remains the open half of Stage 6.
