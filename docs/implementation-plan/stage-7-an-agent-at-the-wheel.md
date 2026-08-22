# Stage 7 - An agent at the wheel

## Status

**Stages 7a, 7b and 7c are all built**, and every figure under their `Verify`
blocks below is measured rather than read.

7a's open item - keys not reaching the window under Wayland - is closed: Keith's
commit `1a8489e`, "validated manual driving works", records the keyboard driving
the car. The reading in this document's history was that panda3d 1.10.16 has no
Wayland backend and the window was an X11 client under XWayland.

**7c** (2026-08-18) answers the question 7b left open once the socket existed:
*what does a model actually see, and where does it run?* Keith asked for camera,
lidar, IMU and GPS, and asked for a program that drives and dumps everything
MetaDrive produces so the input could be chosen from what is really there rather
than from a guess. Both halves are below.

**7c's own open item - "still the model" - is closed** (2026-08-22): wing-sim's
openpilot bridge drives the ego through that socket, and `--backend stub` proves
the path with no fork installed. See *Filled: a controller behind the socket*.

Stage 8, live traffic, is a separate document -
`stage-8-live-traffic.md`. The two do not depend on each other and can be
built in either order.

## Summary

Stages 1-6 end with a ScenarioNet dataset holding one moving object: the ego,
replayed from a tape `ego_route` builds. Nothing decides what that car does -
it is a recording being played back. Stage 7 is about **taking the wheel off
the tape**, and it happens in two halves:

```text
Completed Stage 6: a ScenarioNet dataset with a route in it
  -> Stage 7a: Keith drives it from the keyboard
  -> Stage 7b: a model's numbers reach the car through env.step()
```

**The two halves are the same code path.** `ManualControlPolicy` is a subclass
of `EnvInputPolicy` (`manual_control_policy.py:37`), and the only difference
between them is where `[steering, throttle]` comes from - a keyboard, or the
argument to `env.step()`. So 7a is not a warm-up exercise that gets thrown
away. It proves the environment, the physics, the render settings and the route
are all right **with no model, no gym loop and no new module**, and once it
works the only question left for 7b is where the two numbers originate.

Two decisions frame everything below, both Keith's:

- **The ego is driven by passing actions to `env.step()`**, with `agent_policy`
  left at MetaDrive's default. The model stays outside MetaDrive entirely.
- **Use what MetaDrive already offers.** Nothing here reimplements a car, a
  driver model, a sensor or a physics step.

### Nothing upstream changes

| | change |
|---|---|
| `src/osm_scenario/` | none |
| `src/osm_scenario/conversion.py` | none |
| `workspaces/*/scenarionet` | none - byte-identical, checked by hash |
| `generation_fingerprint`, Stage 3 reviews | none; nothing here is a config field |
| `tools/drive.py` | **one value on an existing enum, plus `--max-lateral-dist`, in 7a**; **`--record` in 7b** |
| existing tests | none - new tests only |

**That last row changed while 7b was being built, and the reason is worth keeping.**
The plan had `drive.py` untouched in 7b. It could not be: this document's own claim
that "the imitation-learning dataset comes out of stage 7a for free" is only true if
a keyboard drive can be recorded, and that needs a flag on the tool that does the
keyboard driving. `--record` is additive and defaults off, so the byte-identical
check on `replay` and `idm` still holds - re-run and still identical, four runs.

The one modified file is deliberate and was Keith's choice. `--agent-policy`
already means "what drives the ego", `replay` and `idm` are already two answers
to it, and `manual` is the third - so 7a is a value in an enum rather than a
new module standing beside the tool that already does all the render setup.

7b then adds `tools/agent_env.py`, which reuses `tools/drive.py` by
**importing** its helpers - they are all module-level functions - using the
cross-`tools/` import pattern `drive.py:511` already establishes for
`signal_control`. `drive.py` is not refactored onto it: a working file is not
worth rearranging to save one duplicated composition.

## Facts the design rests on

Read from the MetaDrive 0.4.3 checkout at
`/home/keith/Desktop/work/wingfin/metadrive/`, with file and line so each can be
re-checked. **None of these are measurements** - each is read from the source. The
measured figures are kept separately, in the `Verified` blocks under 7a and 7b.

| fact | where |
|---|---|
| `env.step()` advances exactly 0.1 s (5 physics ticks of 0.02 s) and returns; nothing runs between calls, so the loop belongs to the caller | `base_env.py:190`, `:462` |
| The action reaches the ego as `engine.external_actions`, set at the top of `before_step` and read by the ego's policy | `base_engine.py:425`, `env_input_policy.py:27` |
| `EnvInputPolicy` is `BaseEnv`'s default, so passing an action *is* driving the ego | `base_env.py:55` |
| The action is `[steering, throttle_brake]`, both in [-1, 1]: `[0] x max_steering` degrees at the wheels, `[1] >= 0` engine force and `< 0` brakes | `base_vehicle.py:472-520` |
| `manual_control=True` replaces `agent_policy` outright - the configured value is not consulted at all in that branch | `agent_manager.py:68-74` |
| ...but `agent_policy` is read a second time, to decide whether the ego is a replayed car and so whether traffic may spawn on top of it | `scenario_traffic_manager.py:65`, `:195` |
| `ManualControlPolicy` **subclasses `EnvInputPolicy`**, so manual and model control are one code path differing only in where the two numbers come from | `manual_control_policy.py:37` |
| The keyboard silently stops steering when the ego is not `current_track_agent`, or the camera is in bird view (`b`) - control falls back to whatever `env.step` passed | `manual_control_policy.py:80-84` |
| Without `use_render`, manual control opens a **pygame** window to read keys instead | `manual_control_policy.py:52-56` |
| `t` hands over to MetaDrive's built-in expert, falling back to the keyboard and printing a message if the observation does not suit it | `manual_control_policy.py:66-74, 93-95` |
| The executed action lands on `vehicle.current_action` whatever produced it, so **one recorder serves both halves** | `base_vehicle.py:169, 226, 974` |
| `EnvInputPolicy` clips both dimensions to [-1, 1], and `action_check` is **off** by default - a malformed action is coerced, never refused | `env_input_policy.py:29-37`, `base_env.py:69` |
| A discrete action space is a config key rather than code: `discrete_action` gives `Discrete(25)`, `use_multi_discrete` gives `MultiDiscrete([5, 5])` | `env_input_policy.py:52-69`, `base_env.py:60-66` |
| `TakeoverPolicy` is the shared-control escape hatch - a model drives, a human grabs the wheel - and it outranks the `manual_control` override | `manual_control_policy.py:99-118`, `agent_manager.py:66` |
| The observation carries a 120-laser, 50 m lidar, so other traffic is visible to a policy with no extra work | `scenario_env.py:61` |
| The ego needs a recorded track whatever drives it - the tape is the navigation reference line and the destination | `scenario_map_manager.py:74`, `trajectory_navigation.py:80` |
| `max_lateral_dist=4` ends an episode as `out_of_road`; the IDM ego already reaches 4.26 m | `scenario_env.py:84`, `CLAUDE.md` |
| `drive.py`'s loop already calls `env.step([0, 0])`; replay and IDM ignore the constant, and it is the exact shape an agent loop takes | `drive.py:615-616` |
| `drive.py`'s helpers are module-level and importable; the cross-`tools/` import pattern already exists | `drive.py:112, 177, 212, 229, 261, 511` |
| `BasePolicy.engine` is a `get_engine()` property rather than an attribute set at construction, so **any policy can be built from outside the agent manager** once `env.reset()` has run - which is what makes an external IDM baseline free | `base_policy.py:78` |
| `ScenarioEnv` does **not** override `agent_policy`; it inherits `EnvInputPolicy` from `BaseEnv`, so an env built with no `agent_policy` at all is already one the action argument drives | `base_env.py:55`, `scenario_env.py:22-104` |
| **A second socket exists**: `ScenarioWaypointEnv` / `WaypointPolicy` takes `{"position": (horizon, 2)}` in the ego's local frame instead of pedals, and sets position, velocity and heading from it | `scenario_env.py:106-113, 442`, `waypoint_policy.py:47-96` |
| `BaseVehicle.before_step` appends to `last_current_action` **only when the policy returned an action**, so `current_action` is stale under any policy whose `act` returns `None` | `base_vehicle.py:225-226` |

### What "no watcher" means, since it is the thing most likely to be misread

MetaDrive is not a process ticking away that has to be interrupted, and there is
no queue, callback or listener waiting for input. It advances **only** when
`env.step()` is called, and `action` is a required argument - **the tick is the
call**. Between two calls nothing in the simulator moves; the program is sitting
in the caller's code.

So simulated time and wall-clock time are decoupled: a policy taking 3 s freezes
the simulator for 3 s and then advances 0.1 s; a policy taking 1 ms advances the
same 0.1 s, and the physics is identical. That decoupling is what makes the
environment usable for training. The only place MetaDrive deliberately spends
wall-clock time is `ForceFPS.real_time_simulation`
(`engine/core/force_fps.py:64`), which throttles the *display* for a human and is
off headless.

```text
env.step(action)
 |- engine.before_step({"default_agent": action})
 |    engine.external_actions = {...}                      base_engine.py:425
 |    for every manager: manager.before_step()
 |       agent manager -> ego policy.act()
 |          EnvInputPolicy reads engine.external_actions   env_input_policy.py:27
 |          ManualControlPolicy reads the keyboard instead manual_control_policy.py:83
 |- engine.step(5)          5 x 0.02 s of physics
 |- engine.after_step()
 |- returns obs, reward, terminated, truncated, info
```

**Playback is the same loop**, and so is manual driving. Only the source of the
ego's motion differs: `ReplayEgoCarPolicy` ignores the action and sets position
from the tape, `ManualControlPolicy` reads a controller, `EnvInputPolicy` takes
the argument. That is why swapping between them is a config value and not a
different program, and why `drive.py` can grow a `manual` mode without its loop
changing at all - it already passes a constant that the ego's policy is free to
ignore.

### Which policy actually drives, when two settings disagree

`agent_manager.agent_policy` (`agent_manager.py:55-75`) resolves it in a fixed
order, and it matters because 7a sets one key while another still holds a class:

1. `TakeoverPolicy` / `TakeoverPolicyWithoutBrake`, if `agent_policy` is one of
   them, win over everything - including `manual_control`.
2. Otherwise, if `manual_control` is true, `ManualControlPolicy`. **`agent_policy`
   is not read at all in this branch.**
3. Otherwise, `agent_policy`.

So `--agent-policy manual` works by setting `manual_control=True`, and whatever
class sits in `agent_policy` beside it is dead **for choosing the ego's policy**.
The code says that where it happens rather than leaving a reader to work out
which of two settings is in charge.

**But it is not dead everywhere, which the plan had wrong.**
`scenario_traffic_manager.py:65` reads the same key a second time -
`is_ego_vehicle_replay = global_config["agent_policy"] == ReplayEgoCarPolicy` -
and uses it to decide whether traffic may be spawned on top of the ego
(`:195`). A car that is being driven, by a keyboard or by a model, is not a
replayed car, so leaving `ReplayEgoCarPolicy` in that slot would be a wrong
answer sitting there waiting for stage 8 to ask the question. `drive.py`
therefore pairs `manual_control=True` with `agent_policy=EnvInputPolicy`, which
is also the honest description: `ManualControlPolicy` subclasses it. **No
scenario in either extract holds traffic today, so nothing observable changes
now** - this costs nothing and removes a trap.

---

## Progress, outputs, and verification

- [x] **Stage 7a - Keith at the wheel** (built 2026-08-17)
  - [x] `tools/drive.py` gains `manual` on the existing `--agent-policy` enum.
  - Outputs, as built:
    - `--agent-policy replay|idm|manual`. `manual` sets
      `"manual_control": True` in the config dict, with a comment recording that
      `agent_policy` beside it does not choose the ego's policy
      (`agent_manager.py:68-74`) - and that it is still read by
      `scenario_traffic_manager.py:65`, which is why the value beside it is
      `EnvInputPolicy` rather than a placeholder. See the section above.
    - **The step budget is lifted.** `budget` is `None` under `manual` and the
      loop reads `while budget is None or steps < budget`, so the run ends only
      when the episode terminates or the window closes. For `replay` and `idm`
      the arithmetic is untouched.
    - **`--render 3D` is required**, refused before anything is built, in the
      same `result       FAILED:` form the tool already uses for a bad
      `--scenario-index`. Without a rendered window MetaDrive falls back to a
      pygame key-reader (`manual_control_policy.py:50-56`).
    - **Not arriving is not counted as a failure under `manual`.** The driver is
      the variable, so a kerb or a wrong turn is the human's, and letting it set
      the exit status would make `result FAILED` mean something different in
      this mode than in the other two. It is still printed.
    - **`--max-lateral-dist` was added, and was not in the plan.** The first
      manual drive ended itself: MetaDrive stops the episode 4 m sideways of the
      recorded route, and with nobody steering the coasting ego crossed that at
      **4.28 m after 189 of 370 steps**. A deliberate wrong turn crosses it in
      about a second, so with the gate closed the mode cannot be used for what
      it exists for. The flag defaults to MetaDrive's own 4.0
      (`scenario_env.py:84`), so nothing changes unless it is asked for.
    - The existing report - `arrive_dest`, route completion, vehicle z, light
      transitions - is printed unchanged at the end of a manual drive.
    - **An on-screen readout of the executed action**, added after Keith's first
      real drive went straight off the road. Two facts made that unreadable: the
      ego spawns at the **recorded** speed, measured at 50 km/h on `junction-1`,
      so a car receiving no input drives away by itself and looks like a car
      being steered badly; and panda3d reads the keyboard through the focused
      window, so keys pressed elsewhere reach nothing silently. `steering`,
      `throttle`, `speed` and whether the policy consulted the controller at all
      are now on screen, and MetaDrive's own key list is printed at startup.
      **The path itself was measured sound before any of this was written**:
      forcing the controller's inputs gives steering 0.801 and +66.8 deg of
      heading in 2 s, and braking takes the car from 7.63 m/s to 0.01.
  - Verified (measured 2026-08-17 unless marked):
    1. `./scripts/drive.sh junction-1 -- --render 3D --agent-policy manual`
       builds the window on the RTX 4050, reports
       `ego policy   manual`, and drives. **Whether WASD steers is Keith's
       check** - it needs someone at the keyboard, and it is the only item here
       that cannot be measured from a script.
    2. `--agent-policy replay` and `--agent-policy idm` print **byte-identical**
       output to the committed version on both extracts, compared against
       `git show HEAD:tools/drive.py` run through the same interpreter - four
       runs, four identical. The `{:g}` on the lateral limit is there for this
       reason: `--max-lateral-dist` makes the value a float, and `4.0` where it
       used to say `4` would have broken it.
    3. The budget really is lifted: under `manual` with the lateral gate opened
       the loop was **still running after 75 s of rendered stepping**, against a
       370-step recording that reaches its bound in about 37 s. Every bounded
       policy prints its report; this one never did.
    4. `--render none` and `--render offscreen` are both refused with exit 1 and
       the reason named.
    5. `--max-lateral-dist` reaches the config, checked where the effect is
       visible: `--agent-policy idm --max-lateral-dist 30` on `junction-1` turns
       `out_of_road at -5.44 m after 291 of 370 steps, completion 0.774` into
       **370 of 370 steps and completion 0.891**, the run ending on the
       recording's length instead of on the gate.
    6. `uv run pytest` - 408 passed, 1 failed, the known
       `test_no_route_on_the_real_map_turns_more_than_the_gate_allows`, which
       `CLAUDE.md` already records as failing at **3 of 396**. Unchanged.
       `uv run ruff check` passes.
    7. `sha256sum` over `workspaces/*/scenarionet/*` unchanged on both extracts;
       `git status` shows `tools/drive.py` and this document.
  - Left for Keith, because they need someone at the keyboard:
    - Pressing `b` into top-down view should stop the keyboard steering, and
      **`q`** should restore it - MetaDrive's own behaviour
      (`manual_control_policy.py:80-84`, `base_env.py:777-778`), documented
      rather than fixed. `b` does not toggle back out; an earlier version of
      this line said it did.
    - Pressing `t` either hands over to MetaDrive's built-in expert or prints
      its observation-mismatch message. **Which of the two happens on our data
      is still unmeasured** - the expert was trained on MetaDrive's own
      observation, and ours may or may not match it.

- [x] **Stage 7b - A model's numbers reaching the car** (built 2026-08-17)
  - [x] `tools/agent_env.py` - the env builder.
  - [x] A recorder for `(obs, action)` pairs - `ActionRecorder`, in the same module.
  - [x] `IdmDriver` - the baseline behind the socket, and **not in the plan**. See below.
  - [x] `tools/drive.py --record`, also not in the plan; see the table above.
  - [x] `examples/drive_with_a_policy.py` - the loop, runnable.
  - [x] `CLAUDE.md` section on the gym contract.
  - **The question 7b actually turned on was not how to connect the inputs.** That
    was already settled and already proven: `env.step([steering, throttle_brake])`,
    two floats in [-1, 1], the same socket 7a's keyboard uses because
    `ManualControlPolicy` subclasses `EnvInputPolicy`. Keith asked whether to start
    with a small model or with an external IDM that could be replaced, and those are
    not two ways of connecting - they are two things to put behind one socket. The
    answer is the IDM, **taken from MetaDrive rather than written**, for one reason
    nothing learned can supply: it is the only way to test the plumbing. See
    `IdmDriver` below.
  - Outputs, as built:
    - `tools/agent_env.py` exposing
      `make_env(dataset_dir, *, render=None, **overrides) -> ScenarioEnv`. It
      exists because `drive.py:351-545` works the map settings out inline from
      argparse, and an external training loop should not have to reproduce
      eighty lines of that to get a correctly rendered map. It **imports** the
      pieces rather than duplicating or refactoring them: `_region_for` for
      `map_region_size` (the smallest power of two covering the drive),
      `_max_texture_dimension` into `_set_semantic_detail`, `_set_line_width`,
      `_keep_line_ends`, `height_scale=1`, and `num_scenarios` from the dataset
      summary. **Every other key is MetaDrive's own, passed straight through**,
      so `agent_policy`, `discrete_action`, `action_check`, `manual_control` and
      `max_lateral_dist` are all reachable without this module knowing they
      exist. **`agent_policy` is deliberately never set**: `ScenarioEnv` inherits
      `EnvInputPolicy` from `base_env.py:55`, and that inherited default is what
      makes the action argument reach the car.

      Two keys had to be named parameters rather than ride in `**overrides` -
      `line_width_m` and `line_interval_m` - because they are **not MetaDrive
      config keys at all**. There is no key that reaches either, which is why
      `drive.py` patches them, and MetaDrive rejects a config key it does not know.
    - `IdmDriver`, the baseline the model replaces. It is MetaDrive's own
      `TrajectoryIDMPolicy` **constructed from outside the agent manager** - legal
      because `BasePolicy.engine` is a `get_engine()` property (`base_policy.py:78`)
      - given the same three arguments `agent_manager.py:48-50` passes, and called
      each step for its `[steering, acc]`.

      **It exists for the plumbing test, not for the driving.** `drive.py
      --agent-policy idm` already runs that class *inside* the engine, where the
      action passed to `env.step` is ignored. Running the same class from outside,
      over an env whose policy is `EnvInputPolicy`, must produce the same drive - and
      if it does, the numbers being passed really are what moves the car. Measured,
      and it is exact on both extracts: see item 1 under Verified.

      It reads the engine rather than the observation (`idm_policy.py:239-297`), so
      it is not a `policy(obs)` in the gym sense. The `obs` argument is accepted and
      ignored, so the loop's signature is the one a model will use and the swap is
      one line.
    - A recorder writing `(obs, action)` pairs, reading `vehicle.current_action`
      (`base_vehicle.py:974`) so that it captures the action the car **actually
      executed, whatever produced it**. That is what makes it worth building
      once rather than inside the example: pointed at 7a it records human
      demonstrations, pointed at 7b it records the model's. **The
      imitation-learning dataset therefore comes out of stage 7a for free**,
      which is the concrete reason the manual half comes first.

      **The pair is the observation from *before* the step**, with the action the
      car executed during it. Recording the returned observation instead is off by
      one and is the wrong thing to fit.

      **A replay recording is a column of zeros, and it is written rather than
      refused.** `ReplayEgoCarPolicy.act` returns `None`, and `before_step` appends
      to `last_current_action` only when it got an action (`base_vehicle.py:225-226`),
      so `current_action` never leaves its initial `(0.0, 0.0)`. Measured: a 352-step
      `junction-1` replay records 352 actions and **every one is `[0, 0]`**. Both
      `ActionRecorder.all_zero` and the line `drive.py` prints say so, because the
      file is otherwise indistinguishable from a real one. The same is true of
      `WaypointPolicy` (`waypoint_policy.py:96`), for the same reason.
    - `examples/drive_with_a_policy.py` - executable documentation, with a stub
      controller:

      ```python
      env = make_env(sys.argv[1])
      policy = IdmDriver(env)          # <- the one line a model replaces
      obs, info = env.reset()
      while True:
          obs, r, terminated, truncated, info = env.step(policy(obs))
          if terminated or truncated:
              obs, info = env.reset()
      ```

      This is deliberately **not** a policy-loading harness. An earlier draft
      had `--policy PKG:CALLABLE` and it inverted control for nothing: the env
      is the API, and the loop belongs to whoever is training. The file shows
      the shape and nothing more.
    - A `CLAUDE.md` section covering the step semantics, the action contract,
      the silent clipping and `action_check=False`, that a discrete action space
      is a config key rather than code, that `routes.json` still chooses the
      goal for an agent-driven ego, the `max_lateral_dist` gate, and the waypoint
      socket's two costs.
  - Verified (measured 2026-08-17):
    1. **The plumbing test.** `IdmDriver` through `env.step` against
       `--agent-policy idm`, which runs the same class inside the engine:
       `junction-1` **291 steps, completion 0.774** both ways; `mosque` **1180
       steps, completion 0.723** both ways. Not merely close - the two runs'
       recorded arrays are **bit-identical**, observations and actions alike
       (`np.array_equal` True on both). So the action argument is what moves the
       car, and any later difference is the model rather than the wiring.
    2. `--agent-policy replay` and `--agent-policy idm` print **byte-identical**
       output to `git show HEAD:tools/drive.py` on both extracts - four runs, four
       identical - so `--record` really is additive.
    3. `env.action_space` is `Box(-1.0, 1.0, (2,), float32)`, and
       `discrete_action=True` through `**overrides` turns it into `Discrete(25)`
       with no code change. `action_check=True` and `max_lateral_dist=30.0` arrive
       in `env.config` the same way, and `env.config["agent_policy"]` reads
       `EnvInputPolicy` without this module ever setting it.
    4. A recording made through `drive.py` and one made through the example loop
       are the **same shape** - `(291, 161)` observations and `(291, 2)` actions,
       inside [-1, 1] - which is what says the two halves are one interface.
       **The manual half of that check is Keith's**, and is blocked on the same
       keyboard fault as 7a: `drive.py --agent-policy manual --record` runs the
       identical recorder call, but a drive nobody can steer is not a
       demonstration.
    5. `uv run pytest` - 408 passed, 1 failed, the known
       `test_no_route_on_the_real_map_turns_more_than_the_gate_allows` at **3 of
       396**. Unchanged. `uv run ruff check` passes, and covers both new files.
    6. `workspaces/*/scenarionet/*` untouched - last written before this work
       began - and `git status` shows `tools/drive.py`, `tools/agent_env.py`,
       `examples/`, this document and `CLAUDE.md`.
  - Left for Keith:
    - **The model.** The slot is `policy = IdmDriver(env)` and nothing here fills
      it. The env, the recorder and a baseline to beat exist so that the first one
      has data, a harness and a number to be compared against.
    - **A model is premature today for two reasons rather than one**, and both are
      worth knowing before any training starts: there is nothing to imitate yet -
      the recorder is 7b's own output and 7a is what fills it, which is blocked on
      the keyboard - and until stage 8 the map holds no other vehicle, so the
      120-laser block of the 161-number observation is constant.

---

## Stage 7c - real sensors out, a hosted model's steering back (2026-08-18)

Built, and every number below measured on `junction-1`'s `test` route.

### Why the survey came first

Keith's question was "how do I get a model to see the lidar and reply", and his
own suggested alternative - a program that drives and dumps everything - was the
right first step, because **what goes on the wire cannot be chosen until it is
known what exists**. It also corrected a premise this document had carried since
7b: the 161-float array the loop passes around is not sensor data at all. It is
`LidarStateObservation`, a normalised RL summary, and **39 of its 161 numbers
move**. `tools/sensor_survey.py` reports the layout, the ranges and what varied,
and writes samples: a PNG per camera, the point cloud and the observation as
`.npy`, and `track.csv` with position, latitude/longitude, IMU and action per
step. `scripts/sensor-survey.sh` wraps it for the same reason `drive.sh` exists -
the cameras need a render context and the PRIME-offload pair in front of the
command.

The full measured tables live in `CLAUDE.md`; the three findings that changed the
design are here.

- **The 120-laser lidar block is constant 1.0.** `Lidar.perceive` scans the
  *dynamic* world and our scenarios hold one car. Not a misconfiguration, and it
  fixes itself at stage 8 - which is why the tool measures it every run instead of
  quoting a number. The road is seen by the 12-laser side detector; the route by
  the 22-number navigation block.
- **A camera can only be read with `image_observation=True`, and that replaces the
  observation** with `{"image", "state"}` whose state is 41 numbers and no lidar at
  all. The two are welded together offscreen. The survey builds
  `LidarStateObservation(env.config)` itself so it can report both honestly.
- **GPS is exact rather than approximate**, and needed nothing invented: the
  dataset carries its projection and MetaDrive records the shift it applied when it
  re-centred the scenario. `tools/geodesy.py` inverts it with Vincenty's direct
  solution because `pyproj` is not in MetaDrive's 3.8 venv - agreeing with `pyproj`
  to **0.000000 m** over 25 points spanning +/-900 m.

### The socket

`tools/policy_client.py` (`RemotePolicy`, `SensorPack`, `sensor_config`),
`examples/policy_server.py` (stdlib only, one `act()` to edit, four stand-in
backends), `--policy-url` on the 7b example, and `--agent-policy remote` on
`drive.py` so a hosted model drives with the 3D view, the terrain, the lights and
`--record` for free.

`remote` maps to `EnvInputPolicy` - **the same class as `manual`** - which is the
7b finding made concrete: a keyboard drive and a model drive are one code path.
It follows `manual` wherever `drive.py` special-cases a self-driving policy: no
episode budget, and excluded from `failures`, so the exit status keeps meaning
"the dataset is drivable" rather than "the model drove it".

### Verified

1. **The wire is lossless.** A local drive and a remote drive fed the *same*
   float32 actions: **291 steps, completion 0.774126 both**, observations and
   actions **bit-identical**.
2. **The observation arrives whole.** Every one of the 291 the server received was
   bit-identical to the one the car had.
3. **The reply steers, with the right sign.** `--steering 1.0` leaves the road in
   13 steps at -4.59 m lateral; `-1.0` in 12 at +4.00 m; `[0, 0]` coasts 188.
4. **Cost.** 0.880 ms per step headless against `env.step`'s own 0.954 ms, rising
   to 49.0 ms with every sensor on. `TCP_NODELAY` is worth **41.0 -> 0.126 ms**.
5. **GPS lands on the map.** All 291 points inside `source/map.osm`'s bounds.
6. **Additive.** `replay` and `idm` byte-identical to `git show HEAD:tools/drive.py`
   on both extracts, and `manual`'s refusal path unchanged. Datasets untouched.
   `uv run ruff check` clean; `uv run pytest` 408 passed with its one known failure.

### Two things that were nearly written down wrong

- **A recording is float32; `IdmDriver` returns float64.** Replaying a recording
  against the float64 original diverges to 1.9e-3 by step 6 - chaotic amplification
  of a 1e-8 action difference, and **nothing to do with the wire**. The test only
  says anything because it holds the dtype fixed on both sides. Counting that as a
  transport fault would have been the 7b "counting refusals is not counting faults"
  mistake in a new place.
- **`--render offscreen` costs 3.6 MB a step with no sensors requested**, because it
  forces the observation into a 3-frame camera stack. `none` and `3D` keep it at 161
  floats, so the 3D row *with* a camera is cheaper than the offscreen row without
  one. `drive.py` prints KB/step rather than leaving that to be discovered.

### Filled: a controller behind the socket (2026-08-22)

7c left the slot empty on purpose - `act()` in `examples/policy_server.py` on the
model's side, `policy = RemotePolicy(url)` on this one. Keith cloned `wing-sim`
and asked whether the `openpilot` folder in it could go there. It can, for the
control half.

**What that folder is.** `bridge/zapeta/server.py` fronts openpilot's real
`plannerd` + `controlsd`. Per tick it takes a *predicted path* and three ego
scalars and returns `steer` / `throttle` / `brake`. It never sees an image. So it
is the steering and pedal stack, not a driver, and something has to hand it a
trajectory.

**What was built.** `tools/openpilot_policy.py` is the translation - the bridge's
length-prefixed JSON on one side, our `/spec` `/episode` `/act` on the other -
and `examples/openpilot_server.py` is the HTTP front, so `--policy-url` and
`step-timing.sh --rows 3` reach it unchanged. `tools/policy_client.py` gained a
`route` sensor, because only the simulator has the route and the observation's own
navigation block is normalised and clipped at 30 m.

**Three readings that made it fit, each off the bridge rather than assumed:**

- **`carla_steer_curvature_gain: 0.0` selects a geometric branch whose output is
  already MetaDrive's.** `server.py:788` emits `-road_wheel_deg / max_steer_angle`,
  and `action[0] x max_steering` *is* the road-wheel angle in degrees. Send
  MetaDrive's own `max_steering` and nothing is left to convert. The default path
  inverts an empirical CARLA curvature gain measured on Town10HD.
- **Both ends negate**, because MetaDrive is left-positive and CARLA is
  right-positive: the waypoints' `y` and the reply's `steer`.
- **The waypoints need no model.** wing-sim ships `route_gt.py` for exactly this,
  and `waypoints_from_route` rebuilds it against the `route` sensor.

**Four things that bite, and are written down rather than left to be met:**

- **`target_speed` defaults to 0**, which is a stop (`server.py:614`). It is sent
  every tick.
- **`steer_ratio` in `init` is stored and never used** - the bridge divides by its
  own `CP.steerRatio` both ways, so the two cancel when they match and a mismatch
  mis-reports the wheel angle rather than changing the output scale.
- **The bridge is written for 20 Hz** (`_DT_MDL = 0.05`). The server prints a note
  at any other rate; `convert --step-hz 20` is the matching dataset, off the same
  `routes.json`.
- **`accel_map.py` is CARLA pedal calibration**, so speed tracking is poor until it
  is re-measured here. Steering is unaffected.

**Verified.** `--backend stub` - a real socket speaking the real protocol with a
pure-pursuit law - drives `junction-1` to **380 steps, arrive_dest=True,
completion 0.951** and `mosque` to **435 / 0.951**, at 0.5 ms and 2.5 KB a step,
against `--backend constant --steering 1.0` leaving the road in 13. Row 3 of the
sweep runs under `--policy-sensors imu,route`. 22 unit tests, every one of them
about a sign or a frame. `uv run ruff check` clean; `uv run pytest` 482 passed
with its one known `ego_route` failure.

### Left for Keith

**The perception half, and the fork.** The thing that turns cameras into waypoints
is a separate AV3 model under `evaluation/src/inference_models/`, CARLA-camera
shaped, and is not in `wing-sim/openpilot/` at all. The openpilot fork itself is
private - `pull.sh` clones `zapetaai/openpilot` at a pinned SHA with private
submodules - so `--backend bridge` needs access the stub does not. And
`accel_map.py` wants a MetaDrive sweep before anyone judges the longitudinal
tracking. All three are decisions rather than omissions.

---

## Known limits, stated rather than hidden

**An agent-driven ego will end episodes almost immediately, early in training.**
MetaDrive stops an episode when the car is more than 4 m sideways off its
recorded route - `max_lateral_dist`, `scenario_env.py:84`, MetaDrive's rule and
not ours. Already **measured** and recorded in `CLAUDE.md`: MetaDrive's own IDM
policy driving our ego reaches 4.26 m and is cut off. A model that has not
learned to steer will cross 4 m within a second or two. This is written down so
it is not chased as a fault in the map or the dataset.

**7a exposed it rather than retuning it**: `drive.py --max-lateral-dist`,
defaulting to MetaDrive's own 4.0, and reachable from 7b through `**overrides`
as an ordinary config key. It had to exist for manual driving to mean anything -
the gate is measured from the *recorded* route, so taking a different turn on
purpose ends the episode - and the same measurement is what shows the gate is
the only thing stopping the idm ego on `junction-1`: opened to 30 m, it drives
to 0.891 completion instead of being cut off at 0.774.

**A wrong action range fails silently, and not in the direction you would
guess.** `action_check` is off by default (`base_env.py:69`), so nothing
verifies the action is in range; `EnvInputPolicy` simply clips it
(`env_input_policy.py:36`, with `clip` being `min(max(a, low), high)` at
`utils/math.py:53`). Three consequences, each of which reads as a driving
problem rather than a plumbing one:

- output in **[0, 1]** instead of [-1, 1] - `throttle_brake` is never negative,
  and `_apply_throttle_brake` only brakes below zero (`base_vehicle.py:494`), so
  **the car cannot brake at all**.
- output **far outside** the range, a forgotten `tanh` say - every step
  saturates to ±1, so steering behaves like an on/off switch at full lock and
  full throttle.
- **`NaN` passes straight through unclipped** - `min(max(nan, -1), 1)` returns
  `nan` in Python - and reaches `setSteeringValue(nan * max_steering)`.

The remedy is a config key rather than code: pass `action_check=True` through
`**overrides` while developing. The `CLAUDE.md` section must say this, because
the symptom never points at the cause.

**The route is still chosen by hand.** An agent-driven ego does not free the
dataset from needing a recorded track: `ScenarioMapManager.reset` calls
`get_sdc_track()` unconditionally, and `TrajectoryNavigation`'s reference line
*is* that tape (`trajectory_navigation.py:80`). So `routes.json` and
`convert --routes` remain exactly as they are, and the tape becomes the goal
rather than the drive.

**There is a second socket, and a model's output shape should be chosen knowing
it.** If the model predicts a *path* rather than pedals - the natural shape for one
trained on our own recorded routes - MetaDrive already takes that:
`ScenarioWaypointEnv` / `WaypointPolicy` (`scenario_env.py:106-113, 442`), where the
action is `{"position": (horizon, 2)}` in the ego's local frame and MetaDrive sets
position, velocity, heading and angular velocity from it. Reachable from `make_env`
as ordinary overrides, since it is a config key and an env class rather than code.
Two costs, and neither is small:

- **the ego becomes kinematic.** `set_static=True` is asserted
  (`scenario_env.py:471`), so no physics acts on it - a model can put the car
  somewhere a car could not get to, and nothing in the run will say so.
- **the recorder captures nothing.** `WaypointPolicy.act` returns `None`
  (`waypoint_policy.py:96`), so `current_action` is never written, exactly as under
  replay. A waypoint model's own output is the thing to record, and this recorder is
  not the tool for it.

Steering and throttle is therefore the default here and the recorder's format.

**An empty map is what a model meets here.** Until stage 8, the only vehicle in
the scenario is the ego, so a policy trained against this alone has never seen
another car. That is the whole reason stage 8 exists, and why the lidar row in
the facts table is worth keeping: the observation is already shaped to carry
traffic the moment there is any.
