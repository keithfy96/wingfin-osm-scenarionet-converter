# Stage 8 - Live traffic around the ego

## Status

**Built and measured on 2026-08-22**, on branch `stage-8-live-traffic`. Cars run on
both extracts. The design is as planned with two changes, both recorded below: the
manager reads a **file** rather than importing the package, and `--traffic live`
**composes onto** whatever `--lights` chose rather than replacing it.

**Second round, 2026-08-22, branch `stage-8-traffic-follows-lanes`.** Keith drove it
and reported cars driving on the grass, not keeping to lanes, and hitting each other.
Three faults, all in `tools/traffic.py`, none of them in the routes - `traffic.json`
did not change and did not need regenerating - plus a give-way rule, which the "Known
limits" section below had deliberately left unwritten until the collisions had been
measured without it. The full record is in `CLAUDE.md`; the short version:

1. **The plan was read in the file's coordinate frame, not the simulator's.**
   `centralize=True` moves every loaded scenario so the ego starts at the origin.
   Cars were placed **93.8 m** from the road on `junction-1`: 0 of 10 on the tarmac
   before, 10 of 10 after.
2. **`arrive_destination` is a 2 m circle around the last point**, so a car that
   arrived even slightly wide never arrived and drove straight for ever - 36 cars over
   three episodes, worst 245 m clear of any road. Retirement is now measured along the
   route, with the same margin.
3. **Spacing was per route, and 60 routes share 10 start points.** Two cars spawned
   **0.97 m** apart. It is now measured between the cars.
4. **Traffic drives its route's own speed profile** (`--traffic-speed`, on by
   default), because nothing in MetaDrive steers a traffic car by the road:
   `steering_control` is two PIDs chasing the polyline with a 1 m preview, and
   `NORMAL_SPEED` is a flat 40 km/h while **29.5%** of `junction-1`'s route distance
   allows less on curvature alone. Measured, 25 cars over 5 episodes, cars that left
   the tarmac: `junction-1` **41 -> 25** (worst 9.39 -> 3.80 m), `mosque` **56 -> 24**
   (9.51 -> 3.08 m). It costs pace - mean speed roughly halves - and that is the trade.
   `traffic_version` is 2; a version 1 plan is refused by name. A car more than 5 m off
   its route is taken off the map and counted as `cars_lost`, never as an arrival.
5. **Traffic gives way where two routes cross**, to other traffic and to the ego
   (`--traffic-give-way`, on by default). Collisions with 25 cars: `junction-1` over
   16 episodes **79 -> 60** with head-on **23 -> 4**, `mosque` over 12 episodes
   **24 -> 9**, the same number of cars completing routes either way, and about 3 ms a
   step at 25 cars. Sixteen episodes rather than five because one episode ranges from
   2 to 10 collisions; repeatable across separate processes, which took a second round
   to achieve, because the tie-break was on `vehicle.name` and MetaDrive names objects
   with a fresh `uuid.uuid4()` per process.

**One acceptance criterion is not measured and cannot be here.** Traffic stopping at
a red light needs a dataset built with `--signals`, and `workspaces/junction-1/signals/signals.json`
is bound to an older lane model - `convert` refuses it by fingerprint. Re-drawing the
phases in `inspection/stage-6-signal-builder.html` is a person's job by design; OSM
carries no signal timing. Everything else is measured, on both maps.

**Stage 8 does not depend on stage 7.** Traffic is watched under
`--agent-policy replay` with no agent involved at all, so the two stages can be
built in either order, and they compose when both exist. Stage 7 is
`stage-7-an-agent-at-the-wheel.md`.

## Summary

Stages 1-6 end with a ScenarioNet dataset holding exactly one moving object:
the ego, replayed from a tape `ego_route` builds. The roads are empty. Stage 8
puts other vehicles on them - **generated from the reviewed lane graph, driven
by MetaDrive's own IDM, and created at runtime rather than baked into the
dataset**.

```text
Completed Stage 6: a ScenarioNet dataset with a route in it
  -> Stage 8: live IDM traffic generated from the reviewed lane graph
```

The framing decision is Keith's: **use what MetaDrive already offers.** Nothing
here reimplements a car, a driver model, a sensor or a physics step. What is
ours is only the *placement* - which spawn points, and along which paths - and
the reviewed lane graph is a far better source for that than anything
MetaDrive could infer from an imported map.

### Nothing upstream changes

| | change |
|---|---|
| `src/osm_scenario/` | one new file; **no existing file edited** |
| `src/osm_scenario/conversion.py` | none |
| `workspaces/*/scenarionet` | none - byte-identical, checked by hash |
| `generation_fingerprint`, Stage 3 reviews | none; nothing here is a config field |
| `tools/drive.py` | one optional flag, defaulting to off |
| existing tests | none - new tests only |

## Facts the design rests on

Read from the MetaDrive 0.4.3 checkout at
`/home/keith/Desktop/work/wingfin/metadrive/`, with file and line so each can be
re-checked. **None of these are measurements** - no code has been run for this
plan.

| fact | where |
|---|---|
| `TrajectoryIDMPolicy` takes only a `PointLane` - no road network, no navigation module, no scenario file | `idm_policy.py:436-503` |
| ...and finds the car in front **geometrically** against that polyline (`point_on_lane`, 20 m, bounding-box overlap), so it sees crossing traffic and the ego | `idm_policy.py:136` |
| ...and brakes at red with no light logic at all: `set_red` turns a 0.25 m air wall on for the lidar, `set_green` sets `AllOff`, and IDM brakes for stationary objects | `base_traffic_light.py:105`, `idm_policy.py:304` |
| A manager's `before_step()` is called once per `env.step()`. That is the only reason to be a manager | `base_engine.py:426` |
| Vehicles are created and destroyed with `spawn_object` / `clear_objects`, and a policy is attached with `add_policy` | `base_manager.py:76-114` |
| `ScenarioTrafficManager` staggers IDM across `IDM_ACT_BATCH_SIZE` steps, because IDM is the expensive part | `scenario_traffic_manager.py:71-80` |
| Vehicle size class is chosen by length: <=4 m S, <=5.2 M, <=6.2 L, else XL | `scenario_traffic_manager.py:389` |
| Replay traffic is deleted once `episode_step >= scenario_length` - the reason traffic here is live rather than baked into `tracks` | `scenario_traffic_manager.py:135` |
| The PG `TrafficManager` places cars by asking the map's blocks about themselves, which an imported map cannot answer | `traffic_manager.py:245-293`, `scenario_map.py:49` |
| The observation already carries a 120-laser, 50 m lidar, so traffic is visible to a policy with no extra work | `scenario_env.py:61` |

### Why not the PG traffic manager

Not because anything is locked. MetaDrive has two kinds of map: one *built* from
prefab blocks that can answer questions about themselves, and one *imported* from
a file, which is a single placeholder `ScenarioBlock` over an `EdgeRoadNetwork`.
The PG `TrafficManager` places cars by asking blocks those questions, and on an
imported map they have no answer.

**And its method is not wanted here anyway.** What it contributes is a way of
choosing spawn points and routes; the reviewed lane graph is a far better one,
with real turn restrictions and real junction geometry. The car, the driver model
and the physics are all shared and all available - only the placement policy is
ours.

### Why the traffic is live rather than written into the dataset

`ScenarioTrafficManager` spawns only what is in `scenario["tracks"]` and deletes
every replayed object once the episode passes the recorded length
(`scenario_traffic_manager.py:135`). So baked traffic brings three things with
it: an episode window, padding every track out to it, and converter changes with
everything those imply for the fingerprint - and even then **the road empties
while a slow agent is still driving**, because the recording ran out.

`TrajectoryIDMPolicy` needs none of that. It takes a plain polyline
(`idm_policy.py:436-503`), so a manager of our own can create cars whenever it
likes and hand each one a path. The episode-length problem disappears rather
than being managed.

---

## Progress, outputs, and verification

- [x] **Stage 8 - Live traffic generated from the reviewed lane graph**
  - [x] `src/osm_scenario/traffic_routes.py` - entry lanes, routes, polylines.
  - [x] `osm-scenario traffic` - the CLI step that writes `traffic/traffic.json`.
  - [x] `tools/traffic.py` - the manager.
  - [x] `tools/drive.py --traffic live`.
  - [x] `tests/unit/test_traffic_routes.py`, `tests/unit/test_traffic_manager.py`
        and a `CLAUDE.md` section.
  - Outputs:
    - `src/osm_scenario/traffic_routes.py`, which reads the reviewed lane model
      and **edits nothing in the package**:

      ```text
      sources(model)          -> lanes with no feeder, filtered to those whose
                                 upstream node is on the edge of the extract
      routes(model, n, seed)  -> walk the lane graph forward, choosing at each
                                 junction, until the route leaves the map
      polyline(route)         -> plan_route(...) then route_polyline(...)
                                 both existing, called and not changed
      ```

      `CLAUDE.md` records 21 fed-by-nothing lanes on `junction-1`. That figure is
      **read, not measured**, and it mixes network-boundary lanes with genuine
      starved-lane defects - the same list the "starved middle lanes" section
      says has never been fully diagnosed. So the spawn set is derived by
      **testing for a map-edge node**, not by trusting the count, and spawning a
      car on a starved lane would be putting traffic somewhere the map says
      nothing feeds.

      Because the polylines come from `route_polyline`, traffic drives **the
      same junction geometry the ego does** - `_turn` curves built from the two
      lanes' own tangents, not connector markers - so it stays in lane through
      turns for free, and inherits the 30-degrees-per-step gate that work is
      held to.
    - **Changed in implementation:** `traffic_routes.py` does not hand its
      polylines to `tools/traffic.py` directly. It cannot - `pyproject.toml` is
      `>=3.10,<3.11` and `scripts/drive.sh` runs MetaDrive's 3.8.20 venv, where
      this package is not installed. It would import in the container and fail on
      the host, which is worse than not trying. `tools/signal_control.py:9` had
      already settled the same question for lights: read the numbers, do not
      import the planner. So a CLI step writes `workspaces/<ws>/traffic/traffic.json`
      and the manager reads it, the same shape as `routes.json` and `signals.json`.
    - `tools/traffic.py`, a `BaseManager` subclass registered into the env. **It
      asks the map nothing**; it reads `traffic.json`. Being a manager buys exactly one thing - `before_step()`
      called once per `env.step()` (`base_engine.py:426`).
      - `after_reset` seeds from the episode and scatters cars along the
        polylines.
      - `before_step` runs each car's `p.act(do_speed_control)` and hands the
        action to the vehicle, copying `ScenarioTrafficManager.before_step:71-80`
        including its `IDM_ACT_BATCH_SIZE` stagger.
      - Spawning is `spawn_object(vehicle_class, position, heading, ...)` then
        `add_policy(v.name, TrajectoryIDMPolicy, v, seed, PointLane(polyline),
        index)`.
      - A car despawns on `arrive_destination` and a replacement is released,
        spaced so cars on one polyline cannot spawn inside each other.
      - It never spawns on top of the ego - the check at
        `ScenarioTrafficManager:195`.
      - `length` varies across the S/M/L/XL bands
        (`scenario_traffic_manager.py:389`).
      - The layout is drawn from **this manager's own generator, advanced once
        per episode and never reseeded** - not from `self.np_random`. `BaseEngine.seed`
        reseeds every manager from the scenario index at each reset, and
        `junction-1` holds exactly one scenario, so `global_random_seed` is 0 on
        every reset and a manager drawing from it would place identical traffic
        for a whole training run. Caught by measurement, not by reading; the same
        trap `signal_control.LiveSignalManager` documents.
    - **Changed in implementation:** `signal_control.live_signal_env` returns a
      whole `ScenarioEnv` subclass rather than a mixin, and `drive.py` assigns it.
      A traffic env written the same way would replace it, so
      `--traffic live --lights live` would silently drop the lights - and a red
      light is the only thing separating conflicting movements, IDM having no
      give-way rule. `traffic.traffic_env` therefore **takes** the class
      `drive.py` has already chosen and subclasses that. Verified: both managers
      register and both run.
    - `tools/drive.py` gains `--traffic live|none` **defaulting to `none`**, plus
      `--traffic-count`, `--traffic-seed` and `--traffic-file`, so traffic can be
      watched in 3D.
      Additive in exactly the way `--reactive`, `--lights` and `--line-width-m`
      were. `--reactive` is left alone: it governs the *recorded* traffic path,
      which is a different mechanism.
    - `tests/unit/test_traffic_routes.py` - sources are map-edge lanes, every
      generated route is reachable, and every polyline passes the same
      30-degrees-per-step gate `ego_route` is held to.
    - A `CLAUDE.md` section on where traffic routes come from, why the manager
      asks the map nothing, and that traffic is not in the dataset.
  - Measured, 2026-08-22:
    1. **Nothing existing moved.** `git diff --stat` shows `tools/drive.py` as the
       only modified file under `tools/`; `./scripts/drive.sh junction-1` with no
       new flag drives 352 of 370 steps, `arrive_dest=True`, completion 0.953 -
       identical to before.
    2. **Every generated polyline passes the 30-degrees-per-step gate.** Worst
       vertex turn over `junction-1`'s 60 routes: **18.3 degrees** as
       `route_polyline` builds it, **18.5** after simplification. Pinned by
       `test_no_traffic_route_on_the_real_map_turns_more_than_the_gate_allows`,
       which also asserts the pool is still 60 - a version that reached zero by
       refusing routes the map permits would be worse than what it replaced.
    3. **Collisions per episode, unsignalled.** Three episodes per row, the ego
       driving, cars on the road held at the count asked for. **Superseded by the
       second round above** - this table was measured with the coordinate-frame
       fault in place, so the cars were driving the right geometry in the wrong
       place, and with the per-route spacing that let two of them spawn inside each
       other. It is kept because it is the number the give-way rule was judged
       against:

       | map | ego | cars | steps | collisions | per car-minute |
       |---|---|---|---|---|---|
       | `junction-1` | replay | 10 | 352 | 4, 2, 0 | 0.34 |
       | `junction-1` | replay | 25 | 352 | 7, 4, 2 | 0.30 |
       | `junction-1` | idm | 10 | 392-1200 | 4, 3, 4 | 0.33 |
       | `junction-1` | idm | 25 | 392-409 | 8, 3, 5 | 0.32 |
       | `mosque` | replay | 10 | 400 | 0, 0, 0 | 0.00 |
       | `mosque` | replay | 25 | 400 | 4, 4, 2 | 0.20 |
       | `mosque` | idm | 10 | 439 | 0, 0, 0 | 0.00 |
       | `mosque` | idm | 25 | 439 | 4, 4, 2 | 0.18 |

       The rate per car-minute is flat across car counts and across which policy
       drives the ego, which says it is a property of the junctions rather than of
       the density or of the ego. `mosque` at 10 cars never collides at all.
    4. **The road stays occupied**, including under a slow ego. Every row above
       ended with exactly the number of cars asked for, and the emptiest the road
       ever got was also that number - the replacement releases before the count
       can drop. This is the fault that ruled out baked traffic, so it is the row
       that mattered most.
    5. **Two resets of the same scenario differ**, and the same seed reproduces:
       a fresh env at seed 0 rebuilds episode 1's layout exactly, and the second
       reset of a running env does not.
    6. `uv run ruff check` passes and `uv run pytest` stays at its one known
       failure, `test_no_route_on_the_real_map_turns_more_than_the_gate_allows`.
  - Not measured:
    7. **Traffic stopping at red.** Needs a dataset converted with `--signals`,
       and this workspace's `signals.json` is bound to an older lane model, so
       `convert` refuses it. Re-draw the phases in
       `inspection/stage-6-signal-builder.html`, rebuild with `--signals`, and the
       measurement is `--traffic live --lights live` with distance from the wall at
       standstill against the ego's measured 5.7 m. Left as it is deliberately:
       choosing signal timing is a person's job, because OSM supplies none.

---

## Known limits, stated rather than hidden

- **IDM has no give-way. The second round adds one, outside the policy.** IDM
  brakes only for what is within 20 m and geometrically on its own lane
  (`idm_policy.py:161-164`), so a car crossing from the side is not an obstacle to
  it at any distance. `_yielders` looks 40 m ahead along each car's own route,
  finds the first place two routes pass within 4 m *at an angle*, and holds the
  car with further to run - taking `min(idm_acc, brake)`, so it can only ever slow
  a car down. It takes a third off `junction-1` and two thirds off `mosque`, and
  removes every head-on.
  What separates conflicting movements *properly* is still the **signal plan**,
  and it costs nothing here because a red light is a physics wall - so training
  runs should still use `--signals`. The collision rate was measured **before** the
  rule was written, and `--traffic-give-way off` keeps that comparison available.
- **Live traffic is not in the dataset.** A stock ScenarioNet consumer opening
  `workspaces/mosque/scenarionet` still sees an empty map. This is the same split
  the repo already made for signals - `--lights tape` portable, `--lights live`
  redrawn per episode - and for the same reason.
- **Baking traffic into `tracks` was considered and rejected.** It works, and it
  is the portable option, but it drags in three things this plan avoids: an
  episode window, padding every track to it, and converter changes with
  everything those imply for the fingerprint. If a self-contained dataset is ever
  wanted, the route-generation half of this stage is shared and most of the work
  is already paid for.
- **The spawn set rests on an undiagnosed count.** The 21 fed-by-nothing lanes on
  `junction-1` have never been split into boundary lanes and defects. The
  map-edge test above is what makes that safe, but it is a filter over a list
  nobody has audited, and the first run should report which lanes it kept and
  which it dropped rather than only a total.
