# Stage 7 - An agent at the wheel

## Status

**Nothing in this document is built.** Every checkbox is unchecked and every
`Verify` block is written as "after implementation". It is a plan, recorded so
that the reading behind it does not have to be done twice.

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
| `tools/drive.py` | **one value added to an existing enum in 7a**; untouched in 7b |
| existing tests | none - new tests only |

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
re-checked. **None of these are measurements** - no code has been run for this
plan.

| fact | where |
|---|---|
| `env.step()` advances exactly 0.1 s (5 physics ticks of 0.02 s) and returns; nothing runs between calls, so the loop belongs to the caller | `base_env.py:190`, `:462` |
| The action reaches the ego as `engine.external_actions`, set at the top of `before_step` and read by the ego's policy | `base_engine.py:425`, `env_input_policy.py:27` |
| `EnvInputPolicy` is `BaseEnv`'s default, so passing an action *is* driving the ego | `base_env.py:55` |
| The action is `[steering, throttle_brake]`, both in [-1, 1]: `[0] x max_steering` degrees at the wheels, `[1] >= 0` engine force and `< 0` brakes | `base_vehicle.py:472-520` |
| `manual_control=True` replaces `agent_policy` outright - the configured value is not consulted at all in that branch | `agent_manager.py:68-74` |
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
class sits in `agent_policy` beside it is simply dead. The code must say that
where it happens rather than leaving a reader to work out which of two settings
is in charge.

---

## Progress, outputs, and verification

- [ ] **Stage 7a - Keith at the wheel**
  - [ ] `tools/drive.py` gains `manual` on the existing `--agent-policy` enum.
  - Outputs:
    - `--agent-policy replay|idm|manual` (`drive.py:405`). `manual` sets
      `"manual_control": True` in the config dict at `drive.py:534`, with a
      comment recording that `agent_policy` beside it is then dead
      (`agent_manager.py:68-74`).
    - **The step budget is lifted.** `drive.py:590` computes
      `budget = length + allowance` - the recording's length for a replayed car,
      and the recording plus the longest red for IDM. A human is bounded by
      neither, so `manual` runs until the episode terminates or the window
      closes. This is the one piece of real logic in 7a, and the reason it is
      not a one-line change.
    - **`--render 3D` is required**, with a clear refusal otherwise. Without it
      MetaDrive falls back to a pygame key-reader
      (`manual_control_policy.py:52-56`), which is not what anyone means by
      driving it, and the failure would otherwise be a window that never
      appears.
    - The existing report - `arrive_dest`, route completion, vehicle z, light
      transitions - is printed unchanged at the end of a manual drive, so the
      same run that lets him feel the map also checks it.
  - Verify after implementation:
    1. `./scripts/drive.sh junction-1 -- --agent-policy manual` opens a window,
       and WASD steers, accelerates and brakes the car.
    2. `--agent-policy replay` and `--agent-policy idm` print **byte-identical**
       output to today on both extracts. That is the additive-change test, and
       it is the whole reason this was allowed to touch a working file.
    3. Pressing `b` into bird view stops the keyboard steering and pressing it
       back restores it - MetaDrive's own behaviour
       (`manual_control_policy.py:80-84`), documented rather than fixed.
    4. Pressing `t` either hands over to MetaDrive's built-in expert or prints
       its observation-mismatch message. **Which of the two happens on our data
       is measured, not assumed** - the expert was trained on MetaDrive's own
       observation, and ours may or may not match it.
    5. `git diff --stat` shows `tools/drive.py` and nothing else, and
       `sha256sum` over `workspaces/*/scenarionet/*` matches the pre-change
       values on both extracts.
    6. `uv run pytest` and `uv run ruff check` unchanged.

- [ ] **Stage 7b - A model's numbers reaching the car**
  - [ ] `tools/agent_env.py` - the env builder.
  - [ ] A recorder for `(obs, action)` pairs.
  - [ ] `examples/drive_with_a_policy.py` - the loop, runnable.
  - [ ] `CLAUDE.md` section on the gym contract.
  - Outputs:
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
      exist.
    - A recorder writing `(obs, action)` pairs, reading `vehicle.current_action`
      (`base_vehicle.py:974`) so that it captures the action the car **actually
      executed, whatever produced it**. That is what makes it worth building
      once rather than inside the example: pointed at 7a it records human
      demonstrations, pointed at 7b it records the model's. **The
      imitation-learning dataset therefore comes out of stage 7a for free**,
      which is the concrete reason the manual half comes first.
    - `examples/drive_with_a_policy.py` - executable documentation, with a stub
      controller:

      ```python
      env = make_env(sys.argv[1])
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
      goal for an agent-driven ego, and the `max_lateral_dist` gate.
  - Verify after implementation:
    1. `git diff --stat` shows **no modified file** for 7b - only new ones - and
       `sha256sum` over `workspaces/*/scenarionet/*` still matches on both
       extracts.
    2. `examples/drive_with_a_policy.py workspaces/mosque/scenarionet` reaches a
       terminal state and reports the same `arrive_dest` and completion fields
       `drive.py` reports.
    3. `env.action_space` is `Box(-1, 1, (2,))`, and flipping `discrete_action`
       to `True` through `**overrides` turns it into `Discrete(25)` with no code
       change - the check that the passthrough is real rather than a curated
       subset.
    4. A recording made under `--agent-policy manual` and one made through
       `env.step` produce the **same array shapes** - `(N, 2)` inside [-1, 1].
       That is the check that the two halves really are one interface, and it
       only means anything because 7a exists.
    5. `uv run pytest` and `uv run ruff check` unchanged.

---

## Known limits, stated rather than hidden

**An agent-driven ego will end episodes almost immediately, early in training.**
MetaDrive stops an episode when the car is more than 4 m sideways off its
recorded route - `max_lateral_dist`, `scenario_env.py:84`, MetaDrive's rule and
not ours. Already **measured** and recorded in `CLAUDE.md`: MetaDrive's own IDM
policy driving our ego reaches 4.26 m and is cut off. A model that has not
learned to steer will cross 4 m within a second or two. This is written down so
it is not chased as a fault in the map or the dataset, and the limit is exposed
as an argument rather than quietly retuned.

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

**An empty map is what a model meets here.** Until stage 8, the only vehicle in
the scenario is the ego, so a policy trained against this alone has never seen
another car. That is the whole reason stage 8 exists, and why the lidar row in
the facts table is worth keeping: the observation is already shaped to carry
traffic the moment there is any.
