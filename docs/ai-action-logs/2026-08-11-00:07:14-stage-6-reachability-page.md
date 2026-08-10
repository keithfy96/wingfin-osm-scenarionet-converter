# A Stage 6 page for picking a route a car can actually drive

- **Date:** 2026-08-11 00:07:14
- **Requested by:** Keith — "could you create a stage 6 html, that contains all the possible
  lanes it can reach from 1351503426? i want to test what you just said, if you can please
  make it general, so it can handle the input from any lane"
- **Files changed:** `src/osm_scenario/reachability_view.py` (new),
  `src/osm_scenario/conversion.py`, `src/osm_scenario/cli.py`,
  `tests/unit/test_conversion.py`, `docs/implementation-plan/README.md`

No change to `generation.py`, `topology.py` or any lane mapping, so no
`docs/mapping-algo-changes/` entry.

## Why a page rather than a table

Stage 6's dataset already recorded one routing sentence: best starting lane
`60444583932ec866` reaches 79 of 285 lanes. Two things measured while building the page say
why that sentence, alone, misleads.

**Way `1351503426` is 42 separate lane segments, not a road.** Their reach runs from **0 to
79**. "Start on 1351503426" is not a well-formed instruction, and 42 hex ids in a table do
not make that visible.

**285 lanes are joined by 294 edges.** From the best lane the first *twelve* search steps
each find exactly one new lane — the layer sizes are `1 1 1 1 1 1 1 1 1 1 1 1 2 3 4 5 …`.
"Reaches 79" sounds like a network and is a thread. Drawn, and banded by distance, that is
one glance.

## What landed

`inspection/stage-6-reachability.html`, written by `convert` the way Stages 2, 4 and 5 each
write their page from their own command. Pick a lane from the map, from a way, or from a
`#lane=<id>` URL; the map colours everywhere reachable by how many lanes must be crossed,
greys the rest, and reports the layer sizes so a chain reads as a chain. A toggle runs the
same search on the reversed edges — *can be reached from* — which is the half of route
planning the metadata never carried.

**The search runs in the browser, over the graph the dataset was built from.** That is the
one design decision worth recording. `convert_scenario` calls `_lane_neighbours` once and
hands the identical object to `_map_features`, `_reachability` and the page, so the page
cannot draw a network the pickle does not contain — including the rule that a non-`active`
connector is not a drivable edge. `_scenario` returns `neighbours` for exactly this reason.
`test_the_page_carries_the_same_graph_the_scenario_does` parses `DATA` back out of the
rendered HTML and asserts the adjacency is equal.

There is no JavaScript test runner for this page — its script is inline, like Stage 4's and
Stage 5's, not part of the `web/` bundle. So the algorithm is pinned by keeping a Python
twin of the breadth-first search beside it in the test file and holding both to
`routing["best_start_reaches"]`.

## Verification

`uv run pytest` **226 passed** (was 222; 4 new). `uv run ruff check` clean.

Re-converted `workspaces/junction-1`. All three pickles are **byte-identical** — sha256
`5868a115…` for the scenario, unchanged — so the page is a side effect that moved nothing.

The client code was then executed under Node with Leaflet and the DOM stubbed, which
exercises `select()` end to end rather than merely parsing it:

```
verdict : can drive to 79 of 284 other lanes
facts   : lanes 79 · ways 15 · furthest 37 · leaving 1 · entering 0
layers  : 1 1 1 1 1 1 1 1 1 1 1 1 2 3 4 5 ...
waynote : 42 lane(s) on this way, reaching from 0 to 79 lanes
picker  : 41 ways
```

Every figure matches one measured independently from `reviewed.json` with `networkx`
beforehand. Two spot checks of the reverse direction agree between the page's JavaScript
and a `networkx` run: `4d02b6dead76fb66` is reachable from **77** lanes by both, and the
default start lane from **0** — it has nothing entering it, which is why it is the best
place to start.
