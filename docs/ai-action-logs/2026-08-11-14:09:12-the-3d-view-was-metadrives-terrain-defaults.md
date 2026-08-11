# The 3D view was MetaDrive's terrain defaults, not our data

- **Date:** 2026-08-11 14:09:12
- **Asked by:** Keith — "when i run the metadrive simulation, the 2d model looks fine, but the
  3d model doesn't work because the topology 'height of the ground' messes it up, the roads
  stop half way and the ego vehicle looks like its driving through the ground and flying in
  other areas"
- **Files changed:** `tools/drive.py` (new), `tools/check_dataset.py`, `CLAUDE.md`

**No change to `src/osm_scenario/`**, so no `docs/mapping-algo-changes/` entry and the
generation fingerprint does not move. The converted dataset was already correct.

## Symptom

`python -m scenarionet.sim --render 3D`: roads that stop part-way across the map, an ego that
sinks into the ground in some places and floats in others. The same dataset renders correctly
in 2D.

## What was ruled out first

The obvious reading is that our geometry is wrong. Measured against the current dataset before
looking anywhere else:

- all 285 lanes carry a `polygon`, which is what the terrain rasterises — `polyline` alone
  would have left the drivable region empty
- every lane's centreline lies **inside its own polygon**, worst deviation **0.00 m**
- **100%** of the ego's 651 steps land on drivable pixels of that rasterised region
- the whole map lies within ±512 m of this route's start (furthest single-axis offset 449 m)

So nothing the converter writes is at fault, and the cause is downstream.

## Fundamental cause

Three independent MetaDrive terrain defaults, none of them reachable from `scenarionet.sim`,
all of them tuned for a Waymo-shaped clip rather than a road network.

**1. `height_scale=50` is the sinking and the flying.** `use_mesh_terrain` is false by default,
so the collision surface is a flat plane at z≈0 and the car is pinned to it. What is *drawn* is
a separate shader mesh built from a noise heightfield, with drivable pixels flattened and
everything else scaled by `height_scale × terrain_size / 2048`. Measured on this drive: at 50
the ground within 25 m of the route reaches **+10.4 m** and **12%** of it stands above the road;
at 1 it reaches **+0.2 m** and **0%** does. The car is inside a hillside, not on a road.

Our drivable area is only ~2–7% of the terrain square, because our roads are thin ribbons over
a wide network rather than a few hundred metres of dense road surface. So the landscape
dominates the frame in a way it never does for the datasets this was tuned on.

**2. The road-surface texture is larger than the GPU accepts, and has no config key.**
`Terrain.get_terrain_semantics` builds it at `map_region_size × 22` px square — **22528** at
1024, **45056** at 2048. The GL context reports its own ceiling: **16384** measured here (it
came up on the Intel iGPU half of the hybrid graphics), 32768 on a discrete card. Past the
ceiling the texture cannot be uploaded, and that is what "the roads stop" looks like. The 22 is
hard-coded in `TerrainProperty.get_semantic_map_pixel_per_meter`, which nothing configures.

**3. `map_region_size` is a square, and 2048 was the wrong blanket answer.** The terrain is one
square of that many metres centred on the world origin — `base_engine.py:386` hard-codes
`center_p = [0, 0]` — and the disk loader centralises the scenario on the ego's start
(`scenario_data_manager.py:76,126` pass `centralize=True`). Outside it there is no ground and
no flattened road. **A previous version of `CLAUDE.md`, written by me, said to set 2048**; that
demands a 45056 px texture no GPU can hold. The right value is the smallest power of two that
covers the map from that particular ego start — 1024 for `main-route`, which reaches 449 m.

## Fix

`tools/drive.py`, a runner beside `tools/check_dataset.py` and following its conventions:
imports nothing from the package, runs under MetaDrive's own 3.8 venv, reports rather than
asserts.

- **measures `map_region_size` from the dataset** instead of guessing, and reports what forced
  the size
- **checks the texture against the real GL limit** after the context exists, and if it does not
  fit, names the driver, the limit and the flag value that would work. The failure it prevents
  is silent.
- sets `height_scale=1` (**not 0** — panda3d builds a singular transform there and dies with
  `Tried to invert singular LMatrix4`) and `drivable_area_extension=10`
- **`--render offscreen`**, which builds the full 3D terrain into a buffer. `--render none`
  builds no terrain at all — `Terrain.reset` guards the whole heightfield and texture path on
  `self.render or use_mesh_terrain` — so it checks the drive and not the view, and saying so
  matters more than the flag.
- reports **how high the ground stands beside the drive**, read back from the heightfield
  texture the terrain actually uploaded rather than by re-deriving MetaDrive's arithmetic. The
  car's own z is ride height whatever the terrain does, so probing under it proves nothing;
  the landscape around it is the thing that is wrong.
- stops at the end of the dataset, rather than `sim.py`'s loop to 1,000,000 and its trailing
  `AssertionError: Scenario Index ... out of range`
- `--reactive`, which `sim.py` has commented out

**One monkeypatch, and it is named as one.** `_set_semantic_detail` replaces
`TerrainProperty.get_semantic_map_pixel_per_meter` at runtime, because no config key reaches
it. It rides the seam MetaDrive already uses for the neighbouring value — `base_env.py:335`
assigns `TerrainProperty.map_region_size` the same way. Confirmed by `git status` in the
MetaDrive checkout: **no modified tracked files** (HEAD `85e5dadc`).

`tools/check_dataset.py` gains one line: how far the map reaches from the ego's start, the
`map_region_size` that covers it, and whether `scenarionet.sim`'s 1024 is enough. Nothing
reported that before, and it is the number that decides whether 3D can show the whole map.

## Verification

`uv run pytest` **250 passed**, `uv run ruff check` clean — neither tool is in the package, so
that is a no-regression check rather than a test of the change. The change is verified by
running it, headless, from MetaDrive's venv.

| run | road texture | ground within 25 m of the drive | result |
| --- | --- | --- | --- |
| `--render offscreen` (this script's defaults) | 16384 px, fits the 16384 px limit | **+0.2 m, 0% above the road** | OK |
| `--render offscreen --height-scale 50 --semantic-pixels-per-meter 22` (MetaDrive's) | 22528 px, **over** the limit | **+10.4 m, 12% above the road** | FAILED, with the flag value to use |

Both drove the whole route — 619 of 651 steps, `arrive_dest=True`, completion 0.952, vehicle z
0.014–0.537 m — which is the point: the drive was never broken, only the view.

`--render none` and `--render 2D` both run and report OK. **`--render 3D` opens a window and I
have not run it**; the numbers above come from the offscreen path, which builds the same
terrain.
