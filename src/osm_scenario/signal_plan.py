"""Stage 6 - turn a chosen signal plan into the lights MetaDrive drives.

MetaDrive 0.4.3 has no traffic-light controller. `ScenarioLightManager.after_step` does one
thing - index `state["object_state"]` by `episode_step` and call `set_status` - and it is the
only light manager in the package; procedurally generated maps have no lights at all. So a
light stored in a dataset is a *tape*: a colour spelled out for every step, identical on every
episode.

Three consequences shape this module.

* **The phase plan is not in the source.** `highway=traffic_signals` says a signal exists and
  nothing else - no cycle, no split, no offset. Everything here is therefore synthesised, and
  `plan_metadata` records it as such. A fabricated plan that could not be told apart from a
  surveyed one is the failure this stage has to avoid, not the fabrication itself.

* **Which lanes are signalled is a judgement about the map**, like the choice of route, so it
  comes from a person through `signals.json` rather than from a heuristic here. `junction-1`
  makes the point: OSM gives it one signal node, at the edge of the extract, associated with
  the lanes it *releases* rather than any it stops.

* **The tape alone would train an agent to read the clock.** Same scenario, same colour at the
  same step, forever. `tools/signal_control.py` reads `plan_metadata`'s output and drives the
  same lights live from a per-episode offset instead; the tape stays as the portable default
  so a stock ScenarioNet consumer still sees working lights.

One cycle is shared by every group, and each group has its own green, yellow and offset within
it. That is what makes a junction coherent - two arms are safe together because their offsets
differ by a known amount, so any randomisation has to move the whole plan rather than each
group separately.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from osm_scenario.ego_route import TIME_STEP_S
from osm_scenario.lane_model import PreliminaryLaneModel

# The version of `signals.json` this converter reads. The signal builder writes the same
# constant, so a page and a CLI that have drifted apart say so instead of half working.
SIGNALS_VERSION = 1

# Group names are shown on the page and written into metadata, never into a filename - so
# this is narrower than it has to be, deliberately matching `_ROUTE_NAME` so a person does
# not have to remember two rules for two files in the same stage.
_GROUP_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,39}")

# `MetaDriveType.LIGHT_*`. Spelled out rather than imported: MetaDrive is deliberately not a
# dependency, and `test_signal_plan` pins these against the real enum where a checkout exists,
# the same arrangement `conversion` uses for the rest of the schema.
LIGHT_GREEN = "TRAFFIC_LIGHT_GREEN"
LIGHT_YELLOW = "TRAFFIC_LIGHT_YELLOW"
LIGHT_RED = "TRAFFIC_LIGHT_RED"

_LIGHT_TYPE = "TRAFFIC_LIGHT"


class SignalPlanError(RuntimeError):
    """Raised when a signal plan cannot be read or does not fit this map."""


@dataclass(frozen=True)
class PhaseGroup:
    """Lanes that go green together, and when in the cycle they do it."""

    name: str
    lanes: tuple[str, ...]
    green_seconds: float
    yellow_seconds: float
    offset_seconds: float


@dataclass(frozen=True)
class SignalPlan:
    """Every group on one shared cycle. See the module docstring for why it is shared."""

    cycle_seconds: float
    groups: tuple[PhaseGroup, ...]

    @property
    def lanes(self) -> tuple[str, ...]:
        return tuple(lane for group in self.groups for lane in group.lanes)


def colour_at(
    *,
    seconds: float,
    cycle_seconds: float,
    green_seconds: float,
    yellow_seconds: float,
    offset_seconds: float,
) -> str:
    """The colour a group shows `seconds` into the episode.

    The offset is **when green starts**, measured from the top of the cycle - so a group with
    `offset_seconds=30` turns green 30 s in. Green runs from there, then yellow, then red
    fills the rest. Red is the remainder rather than a configured number so the three can
    never fail to add up; an all-red gap between two arms is expressed by giving both a green
    shorter than their share, which is how a real plan expresses it too.

    `web/src/signal/conflicts.ts` reads the offset the same way when it measures how long two
    groups are green together, and `web/test/signal/phase.test.ts` pins the two against each
    other - a plan whose page and converter disagreed about the sign would put a red light in
    the pickle where the page drew a green one.
    """
    phase = (seconds - offset_seconds) % cycle_seconds
    if phase < green_seconds:
        return LIGHT_GREEN
    if phase < green_seconds + yellow_seconds:
        return LIGHT_YELLOW
    return LIGHT_RED


def _positive(value: Any, *, label: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise SignalPlanError(f"{label} must be a number, not {value!r}")
    number = float(value)
    if not math.isfinite(number):
        raise SignalPlanError(f"{label} must be a finite number, not {value!r}")
    return number


def _read_group(
    entry: Any, *, position: int, cycle_seconds: float, lane_ids: Mapping[str, Any]
) -> PhaseGroup:
    if not isinstance(entry, dict):
        raise SignalPlanError(f"phase group {position} is not an object")
    name = entry.get("name")
    if not isinstance(name, str) or not _GROUP_NAME.fullmatch(name):
        raise SignalPlanError(
            f"phase group {position} has name {name!r}; a name must be 1-40 characters of "
            "letters, digits or hyphens"
        )

    lanes = entry.get("lanes")
    if not isinstance(lanes, list) or not lanes:
        raise SignalPlanError(f"phase group {name!r} signals no lanes")
    seen: set[str] = set()
    members: list[str] = []
    for lane in lanes:
        if not isinstance(lane, str):
            raise SignalPlanError(f"phase group {name!r} has a lane id that is not a string")
        if lane not in lane_ids:
            raise SignalPlanError(
                f"phase group {name!r} signals lane {lane}, which is not a lane in this "
                "model. Re-open the signal builder from this workspace and place the "
                "lights again"
            )
        if lane in seen:
            raise SignalPlanError(f"phase group {name!r} lists lane {lane} twice")
        seen.add(lane)
        members.append(lane)

    green = _positive(entry.get("green_seconds"), label=f"phase group {name!r} green_seconds")
    yellow = _positive(entry.get("yellow_seconds"), label=f"phase group {name!r} yellow_seconds")
    offset = _positive(
        entry.get("offset_seconds", 0.0), label=f"phase group {name!r} offset_seconds"
    )
    if green < 0 or yellow < 0:
        raise SignalPlanError(f"phase group {name!r} has a negative green or yellow")
    if green + yellow > cycle_seconds:
        raise SignalPlanError(
            f"phase group {name!r} is green for {green:g} s and yellow for {yellow:g} s, "
            f"which is longer than the {cycle_seconds:g} s cycle"
        )

    return PhaseGroup(
        name=name,
        lanes=tuple(members),
        green_seconds=green,
        yellow_seconds=yellow,
        # Normalised so two plans that differ only by a whole cycle produce the same tape,
        # and so the live controller can add its own offset without compounding.
        offset_seconds=offset % cycle_seconds,
    )


def read_signal_plan(
    raw: Any, *, model: PreliminaryLaneModel, model_sha256: str, source: Path | str
) -> SignalPlan:
    """The plan drawn in the signal builder, refused unless it was drawn on this map.

    The identity block does the same job it does for `routes.json`: lane ids are content
    addressed, so a plan drawn on one generation and applied to another does not fail loudly -
    it either names lanes that no longer exist, or names lanes that now sit somewhere else and
    puts a red light across the wrong road.
    """
    if not isinstance(raw, dict):
        raise SignalPlanError(f"{source} is not a signal plan")
    version = raw.get("signals_version")
    if version != SIGNALS_VERSION:
        raise SignalPlanError(
            f"unsupported signals_version {version!r}; this converter writes and reads "
            f"{SIGNALS_VERSION}"
        )

    identity = raw.get("identity")
    if not isinstance(identity, dict):
        raise SignalPlanError(f"{source} has no identity block, so it cannot be checked")
    expected = {
        "generation_fingerprint": model.metadata.generation_fingerprint,
        "reviewed_lane_model_sha256": model_sha256,
    }
    for key, value in expected.items():
        if identity.get(key) != value:
            raise SignalPlanError(
                f"{source} was drawn on a different lane model ({key} does not match). "
                "Re-open the signal builder from this workspace and place the lights again"
            )

    cycle = _positive(raw.get("cycle_seconds"), label="cycle_seconds")
    if cycle <= 0:
        raise SignalPlanError(f"cycle_seconds must be greater than zero, not {cycle:g}")

    entries = raw.get("groups")
    if not isinstance(entries, list) or not entries:
        raise SignalPlanError(f"{source} contains no phase groups")

    lane_ids = {lane.identifier: lane for lane in model.lanes}
    groups: list[PhaseGroup] = []
    names: set[str] = set()
    claimed: dict[str, str] = {}
    for position, entry in enumerate(entries):
        group = _read_group(entry, position=position, cycle_seconds=cycle, lane_ids=lane_ids)
        if group.name in names:
            raise SignalPlanError(f"{source} uses the phase group name {group.name!r} twice")
        names.add(group.name)
        for lane in group.lanes:
            # Two greens on one lane is not a plan a controller could carry out, and it would
            # reach MetaDrive as two lights on one key with the second silently winning.
            if lane in claimed:
                raise SignalPlanError(
                    f"lane {lane} is in both phase group {claimed[lane]!r} and {group.name!r}; "
                    "a lane can only show one colour at a time"
                )
            claimed[lane] = group.name
        groups.append(group)

    return SignalPlan(cycle_seconds=cycle, groups=tuple(groups))


def _stop_points(
    plan: SignalPlan, model: PreliminaryLaneModel
) -> dict[str, tuple[float, float, float]]:
    """Where each wall goes: the downstream end of the signalled lane.

    A light placed on a lane stops the traffic *leaving* it, so the stop line is where that
    lane ends. MetaDrive builds a 0.25 m invisible wall across the lane at this point and
    flips its collision mask with the colour, so being a metre out is the difference between
    stopping at the line and stopping in the junction.
    """
    wanted = set(plan.lanes)
    points = {
        lane.identifier: (float(lane.centerline[-1].x), float(lane.centerline[-1].y), 0.0)
        for lane in model.lanes
        if lane.identifier in wanted
    }
    missing = sorted(wanted - set(points))
    if missing:
        raise SignalPlanError(f"lane {missing[0]} is not in this model")
    return points


def light_states(
    plan: SignalPlan, *, model: PreliminaryLaneModel, steps: int
) -> dict[str, dict[str, Any]]:
    """`dynamic_map_states`, keyed by lane id, in the shape MetaDrive's own converters write.

    Two details are load-bearing and neither is obvious from the schema docstring.

    `stop_point` sits at the **top level** of the entry, not inside `state`. Everything inside
    `state` is length-checked against the scenario length by `_check_object_state_dict`, so a
    3-element position there fails on any scenario that is not 3 steps long;
    `ScenarioLightManager._get_episode_light_data` reads the top-level one and treats an
    in-`state` one as the old Waymo format with shape [T, 2].

    The key must be a lane id that exists in `map_features`, because
    `ScenarioLightManager.after_reset` looks it up in `road_network.graph`. MetaDrive's
    `skip_missing_light` defaults to **True**, so a wrong key is skipped with a log line and
    no light - which is why `tools/check_dataset.py` checks the keys resolve.
    """
    points = _stop_points(plan, model)
    states: dict[str, dict[str, Any]] = {}
    for group in plan.groups:
        tape = [
            colour_at(
                seconds=step * TIME_STEP_S,
                cycle_seconds=plan.cycle_seconds,
                green_seconds=group.green_seconds,
                yellow_seconds=group.yellow_seconds,
                offset_seconds=group.offset_seconds,
            )
            for step in range(steps)
        ]
        for lane_id in group.lanes:
            states[lane_id] = {
                "type": _LIGHT_TYPE,
                # A fresh list per lane: MetaDrive deep-copies these on reset, but a shared
                # list would still make two lanes one object in the pickle and in any reader
                # that mutates it.
                "state": {"object_state": list(tape)},
                "lane": lane_id,
                "stop_point": np.array(points[lane_id], dtype=np.float32),
                "metadata": {
                    "track_length": steps,
                    "type": _LIGHT_TYPE,
                    "object_id": lane_id,
                    "dataset": "osm-scenario",
                    "phase_group": group.name,
                },
            }
    return states


def plan_metadata(plan: SignalPlan, *, model: PreliminaryLaneModel) -> dict[str, Any]:
    """The phase structure itself, for anything that wants to drive the lights rather than
    replay them.

    This is what separates a synthesised plan from a surveyed one inside a pickle, which is
    the risk this stage was previously avoiding by converting no signals at all. `source` says
    where the timing came from - never OSM, because OSM has none - and the numbers are here in
    full so a reader can regenerate the tape rather than infer it from the colours.

    Plain floats and lists rather than arrays: this travels in `dataset_summary.pkl` and into
    the conversion report, and `tools/signal_control.py` reads it under MetaDrive's numpy 1.
    """
    points = _stop_points(plan, model)
    return {
        "source": "synthesised",
        "signals_version": SIGNALS_VERSION,
        "note": (
            "OSM records only that a signal exists; it carries no cycle, split or offset. "
            "Every number here was chosen in the Stage 6 signal builder, not surveyed."
        ),
        "cycle_seconds": plan.cycle_seconds,
        "time_step_s": TIME_STEP_S,
        "groups": [
            {
                "name": group.name,
                "green_seconds": group.green_seconds,
                "yellow_seconds": group.yellow_seconds,
                "offset_seconds": group.offset_seconds,
                "red_seconds": plan.cycle_seconds - group.green_seconds - group.yellow_seconds,
                "lanes": [
                    {"lane_id": lane_id, "stop_point": list(points[lane_id])}
                    for lane_id in group.lanes
                ],
            }
            for group in plan.groups
        ],
    }
