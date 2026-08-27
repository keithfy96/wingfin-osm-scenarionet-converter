# Live traffic — placement here, driving by MetaDrive

How other cars are placed and routed, why they looked aimless, giving way where two routes
cross, and slowing for the corner a route turns through.

Split out of `CLAUDE.md` on 2026-08-27, where it was loaded into every session. The text
below is unchanged from that file — the measurements, dates and counts are the originals.
`CLAUDE.md` keeps a short block naming the traps in here and pointing back at this file.

---

### Other cars are placed by this repo and driven by MetaDrive (2026-08-22)

Stage 8. `osm-scenario traffic` writes `workspaces/<ws>/traffic/traffic.json`; `drive.py
--traffic live` reads it and puts cars on the road. **The only thing this repo supplies is
placement.** Every route is built by `ego_route.plan_route` and `ego_route.route_polyline`,
unchanged and uncopied - so traffic drives the *same* junction geometry the recorded car does,
cubics laid between the two lanes' own tangents rather than the connector marker - and every
car is a MetaDrive vehicle running `TrajectoryIDMPolicy`, which takes a `PointLane` and
nothing else (`idm_policy.py:442`).

```bash
uv run osm-scenario traffic -w workspaces/junction-1 --count 60 --seed 1
cd scripts && ./drive.sh junction-1 -- --traffic live --traffic-count 25 --render 3D
```

**It is a file because of the interpreter, not because of taste** - the same reason
`signal_control.py:9` gives for the live light manager. The planner is 3.10 and the manager
runs in MetaDrive's 3.8 venv, so `tools/traffic.py` reads the numbers rather than importing
`osm_scenario.traffic_routes`. `traffic.json` carries geometry and **no timing at all**, so
one file serves every rate a workspace holds, exactly as one `routes.json` does.

**`traffic_env` takes a class; `live_signal_env` returns one.** That asymmetry is deliberate
and load-bearing: both are whole `ScenarioEnv` subclasses, so composing them by assignment
would leave only the last one standing, and `--traffic live --lights live` would silently drop
the lights. A red light is a physics wall and the **only** thing separating conflicting
movements, because IDM has no give-way rule. `drive.py` therefore passes whatever `--lights`
chose into `traffic_env`.

Six things not to re-derive:

- **A lane with no feeder is not automatically a place a car may appear.** `junction-1` has 19
  (not the 21 `CLAUDE.md` used to read), and only **11** are roads the extract cut; the other 8
  are starved lanes inside junctions, where a car materialises on tarmac other traffic is
  crossing and nothing raises. The test is on the node - *does any other lane end where this one
  begins* - and it was checked before it was trusted: **all 11** sit at an OSM node **outside**
  `source/map.osm`'s own `<bounds>`, which is what a truncated way looks like, while most of the
  8 are inside it. `mosque` splits 16 into 9 and 7. Exit lanes get **no** node test, deliberately:
  nothing appears there, so a lane that leads nowhere is a fine place to stop however it got that
  way.
- **`POLYLINE_TOLERANCE_M` is 5 mm and the 30-degrees gate pins it, not the file size.**
  `route_polyline` samples finely enough for `speed_profile` to read curvature - 55,842 points
  over 60 `junction-1` routes - and every one becomes a `PointLane` vertex for every car on that
  route. Measured worst vertex turn: **18.3 degrees** undecimated, **18.5 at 5 mm** for 23.4% of
  the points, **34.3 at 2 cm** (over the gate) and **47.9 at 5 cm**. The next tolerance up is not
  a cheaper version of this one; it is a different road. The file goes 3.2 MB -> 761 KB.
- **The manager keeps its own generator, seeded once and advanced per episode.**
  `BaseEngine.seed` reseeds every manager from the scenario index at each reset, and
  `junction-1` holds exactly **one** scenario - so `global_random_seed` is 0 on every reset and a
  manager drawing from `self.np_random` places identical traffic forever. Found by measuring two
  resets, not by reading. Same trap, same fix, as `signal_control.LiveSignalManager`.
- **A replacement enters at the *start* of a route, never partway along.** Every route in the
  pool begins at a lane the extract cut, so the start is the one place a car may arrive from off
  the map; dropped anywhere else it appears in the middle of a road other cars are on. Measured:
  across 24 episodes on both maps the road never once fell below the count asked for, under a
  replayed ego and under a slow `--agent-policy idm` one. **That is the fault that ruled out
  baking traffic into `tracks`** - a recorded track is as long as the episode, so the road empties
  around a slow agent - so it is the row that mattered.
- **Collisions are counted once per car per episode, not once per step.** A crash flag stays up
  while two cars are still touching, so a per-step count reports one collision as thirty and the
  number describes the frame rate.
- **Traffic is not in the dataset**, so a stock ScenarioNet consumer still sees an empty map - the
  same split as `--lights tape` against `--lights live`. And **it is what finally fills the lidar**:
  `Lidar.perceive` scans `physics_world.dynamic_world`, which is why the 120-laser block reads a
  constant 1.0 on a scenario holding one car.

### Three reasons the traffic looked like it was ignoring the road (2026-08-22, second round)

Keith: *"the cars look good, but they seem to be just driving around aimlessly on the grass,
i need them to follow lane and traffic rules and not bump into other vehicles."* Three separate
faults, none of them in the routes: `traffic.json` is unchanged and did not need regenerating,
and nothing in `src/osm_scenario/` moved, so no fingerprint moved either. All three are in
`tools/traffic.py`.

**1. The file's frame is not the simulator's, and everything written beside a pickle inherits
this.** `ScenarioDataManager` loads every scenario with `centralize=True`
(`scenario_data_manager.py:76`), which moves the whole world so the recorded car starts at the
origin, and records the move as `metadata.old_origin_in_current_coordinate`. `traffic.json` is
written in the *file's* frame, so a point handed to MetaDrive unshifted lands exactly that far
from the road it was computed for - **`[55.725, -75.469]` on `junction-1`, 93.8 m**. Measured
against the road surface itself rather than against a nearest-vertex distance, which is
meaningless when a lane feature carries only a polygon: **0 of 10 cars on the tarmac, a median
47.7 m clear of it, and 60 of 65 sampled route points off the road**; after, **10 of 10 and 0 of
65**. `_episode_shift` reads the field every reset, because the shift belongs to the *scenario*
and a dataset may hold several. `tools/geodesy.py:20` and `policy_client.py:160` already read the
same field for the same reason - traffic was the one thing written beside the pickle that did
not.

**2. `arrive_destination` is a circle around the last point, so a car that arrived wide never
arrived.** `TrajectoryIDMPolicy.arrive_destination` (`idm_policy.py:464`) is `DEST_REGION_RADIUS`
2 m from `traj.end` in the plane, and nothing else ends a car's run - `steering_control` asks
`heading_theta_at(long + 1)`, which clamps to the final segment, so a car that misses the circle
drives dead straight for ever through whatever is in front of it. Measured over three episodes of
25 cars: **36 cars ran past their last point and stayed**, against 27 retired by the circle, two
of them reaching **245 m and 131 m** clear of any road. `_past_the_end` measures the same margin
**along the route** instead. It is not a new constant - arriving is still `DEST_REGION_RADIUS`
from the end; it just stops asking the car to arrive laterally as well. Worst distance off the
road, same drive: **244.85 m -> 7.23 m**.

**3. `MIN_GAP_M` spaced cars along one route, and the pool has far more routes than the map has
ways in.** `junction-1`'s 60 routes start at **10 distinct points**, the busiest carrying 8, so
two routes are usually the same tarmac for their first hundred metres - and two cars on different
routes were spaced by nothing at all. Measured at reset: **closest pair 0.97 m**, four pairs under
5 m, and about half of every episode's collisions were the rear-end that followed. The rule is now
between the *cars* (`_free_at` takes a position, and `after_step` rebuilds the list from where the
cars actually are rather than from where their routes project them): **closest pair 15.19-20.11 m
over four resets, still 25 of 25 placed**. A car cannot see which route another car is following;
it can only see where it is.

### Traffic gives way where two routes cross, because IDM cannot see across its own lane

**`get_find_front_back_objs_single_lane` keeps only objects whose bounding box is on the
follower's own lane** (`idm_policy.py:161-164`, `lane.point_on_lane`). That is geometric, not by
lane identity, so a car ahead on the same tarmac *is* seen whatever route object it is following -
which is why rear-end collisions were a placement fault and not a driving one. But a car entering
from the side is on no part of that lane at any distance, so it is not an obstacle at all.
MetaDrive's own traffic manager never meets this: it replays recorded tracks driven by people who
did give way.

`_yielders` runs once per `before_step` and is the only thing in this repo that decides how a car
drives. It looks `YIELD_LOOKAHEAD_M` (40 m, about 3 s at 50 km/h) ahead along each car's own
route, finds the first place two look-aheads pass within `CONFLICT_WIDTH_M` (4 m) **at an angle**,
and holds one of the two back. `--traffic-give-way off` measures what it is worth.

Measured 25 cars, unsignalled, ego replayed. **Sixteen episodes on `junction-1` and twelve on
`mosque`, and the length is not padding**: a single `junction-1` episode ranges from 2 to 10
collisions with the rule off, so a five-episode window moves by more than the rule is worth and
an earlier version of this table read the difference backwards. The runs are exactly repeatable
across separate processes - the same episode list came back from two independent 8- and
16-episode runs of each column - which is the point of the tie-break below.

| | give way off | give way on |
|---|---|---|
| `junction-1`, collisions over 16 episodes | 79 (0.34 /car-min) | **60 (0.26)** |
| ... of which head-on, traffic only | 23 | **4** |
| ... of which crossing, traffic only | 47 | **38** |
| ... of which rear-end, traffic only | 0 | 12 |
| ... of which with the ego | 9 | **6** |
| `mosque`, collisions over 12 episodes | 24 (0.12 /car-min) | **9 (0.04)** |

**The head-on column is where the rule pays**, and it is not the column it was aimed at: a
give-way rule declines to act above `CROSSING_MAX_DEG`, so it never brakes for a head-on. What it
removes is the *crossing* collision upstream that knocks a car into the oncoming carriageway in
the first place. 23 to 4.

**The rear-end count goes up and that is the rule's own doing**, not noise: a car that brakes for
a crossing is a car the one behind it has to brake for, and `do_speed_control` runs a fifth of
the cars per step (`IDM_ACT_BATCH_SIZE`), so a follower can be up to 0.5 s late noticing. It is
still a net 19 fewer, and the twelve it costs are the shunt rather than the T-bone.

Cars retired per episode is unchanged either way, which is the check that matters against a rule
that brakes: nothing is gridlocked, the same traffic completes the same routes.

**It costs 3.1 ms a step at 25 cars** - 11.3 ms against 14.4 on `junction-1` headless - and the
first version cost **6.9**. Two prunings halved it, and neither was the obvious one: `_look_ahead`
was vectorised with `searchsorted` (`_pose_at` walks a ~900-vertex route from the start, and it
was being called 525 times a step) for 1 ms, and `_conflict` gained a bounding-box rejection and
stopped computing the crossing angle over the whole 21x21 grid rather than over the samples that
are actually close, for 2.8 ms. The arrays it reads are built once a reset in `_localised_routes`.

Five things not to re-derive:

- **The angle band is what makes it safe to run at all.** Below `CROSSING_MIN_DEG` (30 deg) two
  paths are running together - the same lane, or a merge - and treating that as a conflict would
  have a follower and its leader each waiting for the other for ever. IDM already owns that case,
  per `point_on_lane` above. Above `CROSSING_MAX_DEG` (150 deg) is head-on, which a give-way rule
  cannot fix and a correct one-way lane model does not produce.
- **The nearer car goes, decided on distance and not on time.** A car that has stopped has an
  infinite time to arrive, so a time-based priority makes it give way to everything for ever -
  including to the car that is waiting for it.
- **Ties break on the spawn ordinal, and `vehicle.name` will not do**, which cost a full round of
  measurement to find. `nameable.py:12` is `self.name = str(uuid.uuid4())` - a fresh random id
  every process - so a tie broken on it sends a different car first on every run, and the physics
  amplifies that from there. Measured: with the rule **off** the same five episodes gave **26
  collisions four times over**, and with it **on** they gave **13, 19, 20 and 22**. It was the
  give-way column being the only unrepeatable one that gave it away. On the ordinal, three runs
  of each now give 26 and 18 exactly. Anything in this repo that breaks a tie between two
  MetaDrive objects has the same trap waiting in it.
- **Giving way can only ever slow a car down.** `before_step` takes `min(idm_acc, brake)`;
  steering, following distance and everything else is still MetaDrive's. `_yield_brake` sizes the
  brake from the room left and the car's own speed, so a stationary car asks for nothing and is
  held by the throttle cap alone.
- **Traffic gives way to the ego as well, and the ego is never the one braked.** The ego is not
  in the plan, so nothing in the look-ahead could see it: 9 of `junction-1`'s 79 collisions were
  with it. `_ego_look_ahead` extrapolates a straight line from where it is going rather than
  reading its recorded track - the tape is the ego's future only under `--agent-policy replay`,
  and `idm`, `manual` and `remote` all drive it somewhere else, so a straight line is right
  enough for all four over the second that decides a give-way and wrong in the same way for all
  four. Worth **67 to 60** over 16 episodes, measured on its own by disabling that one method.
  The ego never receives a brake: under replay it is a tape and cannot yield, and under any other
  policy it brakes for its own reasons.
- **It is measured by counting collisions, not by counting yields.** Over a five-episode run the
  rule holds a car back on a few hundred car-steps out of 8,800; genuine crossings are rare on a
  map this size, and a third of the collisions going for that handful of interventions is the
  result, not a sign it is not firing.
- **Traffic stops at a red without any of this**, and that was checked rather than assumed:
  `TrajectoryIDMPolicy.act` has no light logic, but a MetaDrive light is a physical object on the
  lane, so `get_find_front_back_objs_single_lane` returns it as the front object and
  `acceleration()` brakes for it. It is the same path that already stops the ego 5.7 m short of a
  red under `--agent-policy idm`.

### Nothing steers a traffic car by the road, so it has to be slowed for the corner (2026-08-23)

Keith, after the three fixes above: *"although the vehicles keep within the lanes, there are
still some instances of them going onto the grass, why does this happen?"*

**No part of MetaDrive is keeping a traffic car on the road, and there is nothing to
misconfigure.** `TrajectoryIDMPolicy.act` returns two numbers: an acceleration from IDM's
car-following, and a steering angle from `steering_control` (`idm_policy.py:463`), which is a
heading PID aimed at `heading_theta_at(long + 1)` - a **fixed 1 m preview** - plus a lateral
PID on the projection error. Road edges, lane lines and the drivable surface are not inputs to
it. So "the lane is clear" cannot help: it is tracking a polyline, not reading a road. And
nothing notices when it fails - `out_of_road` termination is the **ego's**, and a traffic car
has no road constraint at all.

Measured, `junction-1`, three episodes of 25 cars, before this change:

- **26 cars left the tarmac and 26 of 26 were touching nothing at the time.** Not collisions.
- Tracking is excellent until it is not: **median lateral error 0.08 m**, p90 2.16 m, worst 16 m.
- **`NORMAL_SPEED` is 40 km/h, flat, everywhere** - while these are the same routes the ego
  drives under `speed_profile`. **29.5% of `junction-1`'s 51.8 km of route distance allows less
  than 40 km/h on curvature alone**; every one of the 60 routes has a point allowing under
  20 km/h and 29 of 60 go under 10 (slowest point: median 10.2 km/h).
- At the moment of departure **17 of 26** were faster than the corner allows - median **29.8
  km/h into a corner allowing 19.5**. The other candidate, a backward jump in
  `PointLane.local_coordinates`, accounted for 2 of 26.

**And the routes are partly at fault, which is worth separating from the controller.** They
are on the road - only **95.1 m of 51,766 m** (0.18%) lies off the drivable surface, worst
1.97 m - but they are not all drivable. Measured over a window the length of the car, against
its own minimum turning radius of **2.94 m** (wheelbase 2.47 m at 40 deg of lock): routes with
a lane change have a tightest radius of **2.00 m median**, 28 of 45 tighter than the car can
physically turn; routes without measure **6.02 m**, 2 of 15. That is `ego_route._lane_change`
fitting a 3.5 m shift inside one pair of 5.8-7 m lanes, already recorded in
`docs/reference/ego-route-and-signals.md` as why the ego
crawls to 10 km/h on a lane change. It is **not** the main trigger, and that was checked rather
than assumed: 78% of cars drove lane-change routes and 69% of departures were on them, so they
are slightly *under*-represented, and only 10 of 26 departures were within 20 m of a kink
tighter than the car can turn.

`traffic.json` gained a **speed profile** per route (`traffic_version` 2 - a version 1 file is
refused by name, because it would drive every corner at 40 km/h), min-pooled to
`SPEED_STEP_M` 2 m from `ego_route.speed_profile`'s own 0.1 m, and `tools/traffic.py` writes it
to `policy.target_speed` each step. `--traffic-speed flat` measures what it is worth.

| `junction-1`, 25 cars, 5 episodes | before | after |
|---|---|---|
| cars that left the tarmac | 41 | **25** |
| worst distance off it | 9.39 m | **3.80 m** |
| collisions | 20 | **17** |
| routes completed | 48 | 35 |
| mean speed | 29.2 km/h | 17.6 km/h |

| `mosque`, same | before | after |
|---|---|---|
| cars that left the tarmac | 56 | **24** |
| worst distance off it | 9.51 m | **3.08 m** |
| collisions | 5 | **0** |
| routes completed | 44 | 24 |
| mean speed | 33.4 km/h | 18.0 km/h |

Six things not to re-derive:

- **`TRAFFIC_LATERAL_ACCEL_MPS2` is 4.0 against `ego_route`'s 8.5, and the sweep is monotonic
  and steep.** Same five episodes, cars off the tarmac and the worst distance: 8.5 gives **54
  and 45.22 m**, 6.0 gives **38 and 12.36 m**, 4.0 gives **27 and 3.76 m**. 8.5 is not a comfort
  figure - it is pinned to the ego's 30-degrees-per-step gate - and it works for the ego because
  the ego's positions are **replayed**, so nothing has to steer to them.
- **The pace and the throughput are what it costs, and the cost is real**: mean speed roughly
  halves and routes completed per five episodes fall from 48 to 35. That is the trade for
  keeping cars on the road with a controller that has a 1 m preview. It is one constant and a
  `osm-scenario traffic` rebuild away if a future map wants it different.
- **Min-pooled, never sampled.** A sample can land either side of the one tight vertex in a
  junction and report the speed of the straight beside it. The raw profile is 517,750 samples
  over 60 routes and would be most of the file; pooled at 2 m it is 25,887, and the file goes
  761 KB to 1.17 MB.
- **`_allowed_mps` reads the pool the car is *in*, and must not interpolate toward the next
  one** - on the approach to a corner that hands back a speed the corner does not allow.
- **`target_speed` is set before `policy.act` reads it.** `act` computes an acceleration on one
  step in five (`IDM_ACT_BATCH_SIZE`), so a step late is up to half a second late into a corner.
  Pinned by a test that walks the AST.
- **A car more than `LOST_LATERAL_M` (5 m) off its own route is taken off the map**, and
  replaced at a route start like an arrival - but counted as `cars_lost`, never as a completed
  route. With the profile in force the lateral error is 0.11 m at the median and 1.80 m at p90,
  with **7 excursions past 5 m against 11 past 3 m**, so 3 m would pick up cars still going
  round a junction. **It confounds any collision measurement taken with it on**: it culls
  exactly the cars that were about to crash, and the first sweep of the profile read 5
  collisions against 17 purely because the flat column had 33 cars removed and the profile
  column 4. Isolate it before comparing anything.

**Traffic stopping at a red is the one thing not measured**, and it cannot be here:
`workspaces/junction-1/signals/signals.json` is bound to an older lane model and `convert`
refuses it by fingerprint. Re-draw the phases in `inspection/stage-6-signal-builder.html` and
rebuild with `--signals` - choosing signal timing is a person's job because OSM supplies none.
