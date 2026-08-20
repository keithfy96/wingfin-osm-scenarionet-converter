"""Drive a converted dataset's traffic lights live, instead of replaying a fixed tape.

    from signal_control import live_signal_env
    env = live_signal_env(seed=0)({...the usual ScenarioEnv config...})

Like `drive.py` and `check_dataset.py` this is not part of the package and imports nothing
from it, because it does not run on the same Python: the repo targets 3.10 and numpy 2, both
MetaDrive checkouts run 3.8 and numpy 1.24. It is imported *by MetaDrive's* interpreter, at
runtime, which is exactly why it cannot live in `src/osm_scenario/`.

**The problem it solves.** `dynamic_map_states` is a tape. `ScenarioLightManager.after_step`
does one thing - index `state["object_state"]` by `episode_step` and call `set_status` - so a
light in a dataset shows the same colour at the same step on every episode, forever. An agent
trained against that learns the step number rather than the light, and will look like it is
obeying signals when it is not. MetaDrive has nothing else to offer here: `ScenarioLightManager`
is the only light manager in 0.4.3, and its procedurally generated maps carry no lights at all.

**What this does instead.** It reads `metadata.signals` - the phase structure
`osm_scenario.signal_plan.plan_metadata` writes, which is the numbers rather than the colours -
and evaluates each group's colour from the episode clock at every step. The tape is ignored.

**One offset, drawn per episode, applied to every group.** This is the part that has to be
right. The gaps *between* groups are the plan: two arms are safe together because their offsets
differ by a known amount, and the Stage 6 signal builder checks exactly that. Randomising each
group separately would destroy it and put crossing movements green at once. So a single delta
is drawn from the episode seed and added to the whole plan, which slides the cycle under the
drive without changing its shape.

It is also not bounded by the scenario length, which the tape is: `after_step` returns early
once `episode_step` passes it, freezing the tape's last colour. A training episode longer than
the recorded route keeps live lights.
"""

from __future__ import annotations

GREEN = "TRAFFIC_LIGHT_GREEN"
YELLOW = "TRAFFIC_LIGHT_YELLOW"
RED = "TRAFFIC_LIGHT_RED"


def colour_at(seconds, group):
    """The colour a group shows `seconds` into the episode.

    The third implementation of one clock - `signal_plan.colour_at` writes the tape and
    `web/src/signal/phase.ts` draws the page - and it must agree with both. The offset is when
    green *starts*, measured from the top of the cycle.
    """
    cycle = group["cycle_seconds"]
    phase = (seconds - group["offset_seconds"]) % cycle
    if phase < group["green_seconds"]:
        return GREEN
    if phase < group["green_seconds"] + group["yellow_seconds"]:
        return YELLOW
    return RED


def _plan_of(scenario):
    """`metadata.signals`, or None when the dataset was built without `--signals`."""
    return (scenario.get("metadata") or {}).get("signals")


def build_manager(seed=None):
    """`ScenarioLightManager` with the tape swapped for a clock.

    Built inside a function rather than at module scope so importing this file does not import
    MetaDrive - `check_dataset.py` and `drive.py` both print their interpreter version before
    touching it, and a module-level import would make a missing MetaDrive an import error
    instead of a sentence.

    Only the two places a status is read are overridden. Everything else - looking the lane id
    up in `road_network.graph`, spawning the object, honouring `skip_missing_light` - is
    MetaDrive's own code, because a light this manager placed differently from the stock one
    would not be testing the same thing.

    `seed` seeds this manager's own generator, and **not** MetaDrive's. `BaseEngine.seed`
    reseeds every manager from the scenario index at each reset, so a manager drawing from
    `self.np_random` would produce the same offset every time a given scenario came round -
    which for a one-scenario dataset means the same offset for the whole training run, and
    the fixed timing this manager exists to avoid. An independent generator, advanced once per
    episode, is what makes the offset vary; passing a seed makes the sequence repeatable.
    """
    import numpy
    from metadrive.manager.scenario_light_manager import ScenarioLightManager

    class LiveSignalManager(ScenarioLightManager):
        """Lights driven from `metadata.signals` and a per-episode offset."""

        def __init__(self):
            super().__init__()
            self._groups = []
            self._lane_to_group = {}
            self._episode_offset = 0.0
            # Replaced from the engine in `before_reset`; only a placeholder for the window
            # before the first reset, when there is no engine config to read.
            self._time_step = 0.1
            self._rng = numpy.random.RandomState(seed)

        def before_reset(self):
            super().before_reset()
            plan = _plan_of(self.current_scenario)
            self._groups = []
            self._lane_to_group = {}
            # From the **engine**, not from the plan. `colour_at` below is handed
            # `episode_step * self._time_step`, and `episode_step` counts `env.step`s - so the
            # denominator has to be how long an `env.step` lasts in *this* run, which is
            # `physics_world_step_size x decision_repeat` and nothing else
            # (`waypoint_policy.py:61-65` derives it the same way). `plan["time_step_s"]` is
            # the rate the *tape* was baked at, which is a different clock and equal to this
            # one only when the dataset was converted at the rate it is being driven at. These
            # lights are live precisely because the tape is not being used, so reading the
            # tape's rate here was right only by coincidence. Do not put it back.
            config = self.engine.global_config
            self._time_step = float(config["physics_world_step_size"]) * int(
                config["decision_repeat"]
            )
            if not plan:
                return
            cycle = plan["cycle_seconds"]
            # One delta for the whole plan; see the module docstring for why it must not be
            # per group.
            self._episode_offset = float(self._rng.uniform(0.0, cycle))
            for group in plan["groups"]:
                record = {
                    "cycle_seconds": cycle,
                    "green_seconds": group["green_seconds"],
                    "yellow_seconds": group["yellow_seconds"],
                    "offset_seconds": group["offset_seconds"] + self._episode_offset,
                }
                self._groups.append(record)
                for lane in group["lanes"]:
                    self._lane_to_group[lane["lane_id"]] = record

        @property
        def episode_offset_seconds(self):
            """What this episode drew. Reported rather than hidden: an episode that cannot be
            explained afterwards is not reproducible in any useful sense."""
            return self._episode_offset

        def _live_status(self, scenario_lane_id):
            group = self._lane_to_group.get(scenario_lane_id)
            if group is None:
                return None
            return colour_at(self.episode_step * self._time_step, group)

        def after_reset(self):
            super().after_reset()
            # The parent sets each light from the tape's first frame. Re-set them here rather
            # than duplicating the spawn logic: the lights exist by now and this is one call.
            for scenario_lane_id, light_id in self._scenario_id_to_obj_id.items():
                status = self._live_status(scenario_lane_id)
                if status is not None:
                    self.spawned_objects[light_id].set_status(status)

        def after_step(self, *args, **kwargs):
            if not self._groups:
                # No plan in this scenario, so fall back to whatever tape it carries. A mixed
                # dataset is a real possibility and silently unlighting half of it would not
                # be an improvement.
                return super().after_step(*args, **kwargs)
            for scenario_lane_id, light_id in self._scenario_id_to_obj_id.items():
                status = self._live_status(scenario_lane_id)
                if status is not None:
                    self.spawned_objects[light_id].set_status(status)

    return LiveSignalManager


def live_signal_env(seed=None):
    """`ScenarioEnv` that registers the live manager instead of the stock one.

    `setup_engine` is the seam MetaDrive provides for exactly this, and it is the only place
    where the timing is right: the engine exists, and no episode has begun. Registering after
    a `reset()` would leave that episode's lights unmanaged, because `before_reset` has
    already run for every manager the engine knows about.

    `no_light` is set in the config so the stock manager is never registered -
    `register_manager` asserts a name is not already taken, so the two cannot both exist.
    """
    from metadrive.envs.scenario_env import ScenarioEnv

    manager_class = build_manager(seed)

    class LiveSignalScenarioEnv(ScenarioEnv):
        def __init__(self, config=None):
            merged = dict(config or {})
            merged["no_light"] = True
            super().__init__(merged)

        def setup_engine(self):
            super().setup_engine()
            self.engine.register_manager("light_manager", manager_class())

    return LiveSignalScenarioEnv
