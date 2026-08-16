# Stage 8 - Live traffic around the ego

## Status

**Nothing in this document is built.** Every checkbox is unchecked and every
`Verify` block is written as "after implementation". It is a plan, recorded so
that the reading behind it does not have to be done twice.

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

- [ ] **Stage 8 - Live traffic generated from the reviewed lane graph**
  - [ ] `src/osm_scenario/traffic_routes.py` - sources, routes, polylines.
  - [ ] `tools/traffic.py` - the manager.
  - [ ] `tools/drive.py --traffic live`.
  - [ ] `tests/unit/test_traffic_routes.py` and a `CLAUDE.md` section.
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
    - `tools/traffic.py`, a `BaseManager` subclass registered into the env. **It
      asks the map nothing**; it carries its own spawn points and polylines from
      the module above. Being a manager buys exactly one thing - `before_step()`
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
      - It is **re-seeded on every reset**, so each episode is a different
        situation on the same map, for the reason `--lights live` exists: an
        agent that meets identical cars at identical times learns the step
        number.
    - `tools/drive.py` gains `--traffic live|none` **defaulting to `none`**, plus
      `--traffic-count` and `--traffic-seed`, so traffic can be watched in 3D.
      Additive in exactly the way `--reactive`, `--lights` and `--line-width-m`
      were. `--reactive` is left alone: it governs the *recorded* traffic path,
      which is a different mechanism.
    - `tests/unit/test_traffic_routes.py` - sources are map-edge lanes, every
      generated route is reachable, and every polyline passes the same
      30-degrees-per-step gate `ego_route` is held to.
    - A `CLAUDE.md` section on where traffic routes come from, why the manager
      asks the map nothing, and that traffic is not in the dataset.
  - Verify after implementation:
    1. `./scripts/drive.sh mosque` with no new flag is still identical to today,
       and `git diff --stat` shows `tools/drive.py` as the only modified file.
    2. `./scripts/drive.sh junction-1 -- --traffic live --traffic-count 25
       --lights live` shows traffic that stays in lane through junctions.
    3. Every generated polyline passes the 30-degrees-per-step gate, reported as
       a count over the whole generated set - the same standard the ego's drive
       line meets.
    4. Traffic stops at red and moves off at green, **measured** as distance from
       the wall at standstill, against the ego's measured 5.7 m.
    5. Collisions per episode is a **measured number**, reported for signalled
       and unsignalled junctions separately. This is the number that says whether
       stage 8 is finished, and it is the one thing here that cannot be
       predicted from reading MetaDrive.
    6. The road is still occupied when the episode ends under a slow ego
       (`--agent-policy idm`) - the fault that ruled out baked traffic, so it is
       measured rather than assumed.
    7. Two resets of the same scenario produce different traffic.
    8. `uv run pytest` stays at its one known failure,
       `test_no_route_on_the_real_map_turns_more_than_the_gate_allows`, and
       `uv run ruff check` passes.

---

## Known limits, stated rather than hidden

- **IDM has no give-way, and this plan does not add one.** It brakes only for
  what is within 20 m and geometrically on its own polyline
  (`idm_policy.py:136`), so two cars approaching an unsignalled junction on
  crossing paths may see each other late. What separates conflicting movements
  is the **signal plan**, and it costs nothing here because a red light is a
  physics wall - so training runs should use `--signals`. Collisions per episode
  is measured **before** any give-way rule is written. MetaDrive's own IDM has
  no give-way either, so this is not a shortfall against an existing baseline.
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
