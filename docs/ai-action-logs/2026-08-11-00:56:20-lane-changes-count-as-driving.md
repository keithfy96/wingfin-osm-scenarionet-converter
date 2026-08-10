# Lane changes count as a way to get somewhere

- **Date:** 2026-08-11 00:56:20
- **Found by:** Keith — he pointed at way `334662874` between OSM nodes `474913266` and
  `1928630157` and asked why it cannot reach the road on the left, then asked the question
  that settled it: "does OSM specify allowance of lane change? I'd assumed that if a lane is
  going the same direction, a lane change is allowed unless otherwise specified."
- **Files changed:** `src/osm_scenario/conversion.py`,
  `src/osm_scenario/reachability_view.py`, `tests/unit/test_conversion.py`,
  `docs/implementation-plan/README.md`

No change to `generation.py`, `topology.py` or the lane model, so no
`docs/mapping-algo-changes/` entry — see *Not a mapping fix* below.

## Symptom

Northbound on Persiaran Kenanga, the offside lane `863caef770461b65` reached **5** lanes.
The lane immediately beside it reached **68**. The one lane that got the left turn onto way
`1013712948` was the kerbside one, and nothing let a car in the offside lane move across.

The whole reachability graph had **no lane-change edges at all**: 147 of 285 lanes record a
lane sitting beside them, and not one of those counted as somewhere a car could go.

## Fundamental cause

**The model was stricter than the survey, which is this project's rule inverted.**

OSM spells a lane-change ban with `change` / `change:lanes`; absence means the change is
permitted. `junction-1`'s source carries no `change` tag of any kind — the complete set of
lane-related keys in it is `lanes`, `oneway`, `lanes:backward`, `turn:lanes`,
`turn:lanes:forward`, `placement`. So the source *does* say lane changes are allowed, by
saying nothing, and `_reachability` was overriding that with a graph built only from
junction movements.

That is the same shape of mistake as an inferred angle overriding a `turn:lanes` tag, which
`_side_filtered_candidates` and `_stranded_permission_fallback` already guard against.

Keith's original guess was right. It was worth checking rather than accepting, because the
answer for the *other* direction is different: the southbound lanes cannot reach that road
either, but only because the junction is behind them and both U-turn connectors at the ends
of the segment are `forbidden`. No lane change would help there.

## Fix

`_lane_change_moves(model)` turns each recorded `left_neighbor` / `right_neighbor` into a
one-way move from the lane that records it. Symmetric data yields both directions on its
own; asymmetric data degrades instead of failing the conversion over bookkeeping. It
refuses, as `ConversionError`, a neighbour that is missing from the model or that is not on
the same way, same `source_edge` and same `direction` — a "neighbour" across the centreline
would be a drivable edge into oncoming traffic. All 178 links in `junction-1` pass.

`_reachability(neighbours, moves)` now reports both answers. The headline figures allow
lane changes, because that is what a person planning a route needs; `without_lane_changes`
keeps the junction-only figures beside them. Reporting either alone misleads — the same
lesson Stage 5's weak-versus-strong piece count already taught.

**`entry_lanes` / `exit_lanes` are untouched.** They mean "where this lane physically
leads", and a lane change is not that. The map MetaDrive loads has not moved.
`test_a_lane_change_never_becomes_an_exit_in_the_map_features` pins it.

The Stage 6 page gains a checkbox, on by default. Unticking it reproduces the junction-only
view exactly. That is not decoration: the difference is large enough that asserting it
without letting a reader see both sides would be asking for trust.

### Checked in MetaDrive's source, because it changes what the numbers are for

`ScenarioEnv` uses `TrajectoryNavigation`, which follows a recorded ego path — we ship none.
`EdgeNetworkNavigation`, the only consumer of the lane graph, is never configured as a
`navigation_module` anywhere in 0.4.3, and its `get_peer_lanes_from_index` reads
`left_n["id"]`, which matches neither our bare id strings nor Waymo's `feature_id` dicts. So
MetaDrive's own planner is dormant and the graph we ship is what any real planner will see.
Read, not run — no panda3d here.

Where MetaDrive *does* plan (`EdgeRoadNetwork.bfs_paths`) it seeds the queue with the start
lane's neighbours and then expands through `exit_lanes` only, so it allows one lane change
at the start and none after. Its navigation module handles the rest as lateral slack: at
each checkpoint the vehicle's corridor is that lane plus its neighbours. Keith chose to keep
`left_neighbor` / `right_neighbor` as bare id strings — the one shape that makes `bfs_paths`
work — over matching Waymo's dicts.

## Not a mapping fix

Giving the left turn to the kerbside lane is correct for left-hand traffic, and with lane
changes counted the offside lane now reaches it the way a real driver would: by moving
across first. Nothing in the source says which lane may turn, so widening the turn itself
would be inventing permission. If the offside lane should hold its own connector onto way
`1013712948`, that is a separate judgement about `generation.py` and Keith's to make.

## Verification

`uv run pytest` **233 passed** (was 226; 7 new, none skipped). `uv run ruff check` clean.

Re-converted `workspaces/junction-1`. The scenario pickle's sha256 changed, as intended —
`metadata.routing` is new content — and nothing else did:

| | |
| --- | ---: |
| map features | 855 (285 lanes + 570 boundaries) |
| exit edges | 294 |
| dangling references | 0 |
| lanes whose `exit_lanes` leaked a neighbour | 0 |
| `tracks` / `dynamic_map_states` / `length` | 0 / 0 / 1 |
| fingerprint vs `reviewed.json` | equal |
| `read_dataset_summary` + `sanity_check` | pass |

The measured effect:

| | junction only | with lane changes |
| --- | ---: | ---: |
| best lane reaches | 79 | **190** |
| typical lane reaches | 10 | **110** |
| journeys that exist | 6,556 (8%) | **22,217 (27%)** |
| lanes reaching nothing | 20 | **8** |
| pieces respecting direction | 274 | **185** |

The page's own JavaScript was run under Node with Leaflet and the DOM stubbed. With the box
ticked the best lane reaches 190 and its first search layers are `3 3 3 3 3 4 5` — a
network. Unticked, the same lane reaches 21 and its layers are all ones — the chain the
page showed before.

And the lane Keith asked about, `863caef770461b65`: **5 lanes on 1 way** without lane
changes, **126 lanes on 22 ways** with them, and way `1013712948` — the road on the left —
is in the second set and not the first.
