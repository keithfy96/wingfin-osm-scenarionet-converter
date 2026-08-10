# The dataset opens where it is meant to be used

- **Date:** 2026-08-11 02:53:01
- **Asked by:** Keith — "can we go ahead and try to get a workable scenario drive dataset
  that i can import?", then, on being shown the plan, "do i need the ego route at this
  instance? I'd rather just generate the map first and figure out the ego route later."
- **Files changed:** `src/osm_scenario/conversion.py`, `tools/check_dataset.py` (new),
  `tests/unit/test_conversion.py`, `CLAUDE.md`

No change to `generation.py`, `topology.py` or the lane model, so no
`docs/mapping-algo-changes/` entry.

## Symptom

`workspaces/junction-1/scenarionet/` was a valid ScenarioNet dataset that could not be
opened by either MetaDrive checkout on this machine:

```
ModuleNotFoundError: No module named 'numpy._core'
```

Nothing in this repo could see it. `uv run pytest` was green, including
`test_the_scenario_passes_metadrives_own_sanity_check`.

## Fundamental cause

**The one test that used MetaDrive's real code still ran in our interpreter.**

`_load_metadrive_schema` loads `scenario_description.py` from the checkout and executes it
here, under Python 3.10 and numpy 2.2. That is the right way to pin a *schema* without
taking a dependency, and it is why the field names are measured rather than assumed. But it
means the scenario was only ever unpickled by the numpy that pickled it, and numpy 2 reads
its own output happily. The check never crossed the boundary it was standing in for.

The boundary is real and does not close on its own. numpy 2 pickles an `ndarray` as a
reference to `numpy._core`, a module numpy 1 does not have. Both checkouts run Python 3.8 —
the MetaDrive dockerfile pins `uv venv --python 3.8` — and 3.8 cannot install numpy 2 at
all. So the format we target is, in practice, "readable by numpy 1", and nothing said so.

## Fix

`_PortablePickler` sends every array through `(np.array, (obj.tolist(), obj.dtype.str))`.
Both names are unchanged across numpy 1 and 2, so the stream carries no version-specific
module name and what arrives is a real `ndarray` with the original dtype and shape — not a
list, which matters because MetaDrive indexes this geometry with tuples
(`positions[:, :2]` in `parse_full_trajectory`).

The two tests pin the two halves separately, because either alone can be satisfied by a
broken shim: `pickletools.genops` finds no `numpy._core` global, and a round trip still
yields arrays. Both live under a comment saying why they are written that way rather than as
an ordinary round-trip assertion.

`tools/check_dataset.py` is the part that cannot be a test. It imports nothing from this
package, because it runs on the other interpreter, and reports rather than asserts so a
partial failure says how far the dataset got. It draws the map itself rather than through
MetaDrive's `draw_map`, which scatters polyline vertices: that suits Waymo's densely sampled
centrelines, but ours are OSM ways cut at their nodes, so a straight lane is two points and
the scatter comes out as an unrecognisable dot cloud.

## Not the ego route

The plan originally carried a generated ego route as well. Keith cut it — he wants the map
first. That turned out to remove most of the work rather than half of it, because the
missing ego track blocks only *driving*: loading, `sanity_check`, ScenarioNet's
`check_existence` and drawing all work without one. What it does block is recorded in
`CLAUDE.md`, together with the two facts that will be needed when the route work returns
(the track's required arrays, and `map_region_size=2048` — the default 1024 clips to ±512 m
about the origin and `junction-1` reaches y = 581 m).

## Verification

`uv run pytest` **235 passed** (was 233; 2 new, none skipped). `uv run ruff check` clean.

Re-converted `workspaces/junction-1`. The scenario's sha256 moved because the arrays are
pickled differently; **the content it decodes to did not**. Checked by normalising both
dicts — arrays to dtype, shape and values — and comparing: identical, across all three
pickles. The file grew 153,780 → 154,372 bytes, well under the increase expected, because
these polylines are two or three points each.

| | |
| --- | ---: |
| map features | 855 (285 lanes + 570 boundaries) |
| exit edges | 294 |
| `tracks` / `dynamic_map_states` / `length` | 0 / 0 / 1 |
| fingerprint vs `reviewed.json` | equal |

Then, in the environment the dataset is for — Python 3.8.20 / numpy 1.24.4:

```
$ metadrive/.venv/bin/python tools/check_dataset.py workspaces/junction-1/scenarionet
unpickle     ok, 154372 bytes on disk
geometry     855 polylines, held as ndarray, first row [-75.619328250879, 44.751608422282054]
content      855 map features (285 lanes), 0 tracks, length 1
sanity_check PASS
draw_map     accepted the features
result       OK
```

And ScenarioNet's own tools, from its own venv: `scenarionet.num` reports 1 scenario, and
`scenarionet.check_existence` reports "All scenarios can be loaded successfully!".

The rendered PNG shows the junction, the offramp and the side roads as a recognisable road
network, so the geometry survived the crossing as geometry and not merely as bytes.
