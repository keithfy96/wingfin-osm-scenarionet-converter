"""Open a converted dataset the way MetaDrive opens it, in MetaDrive's own interpreter.

    <metadrive-checkout>/.venv/bin/python tools/check_dataset.py \
        workspaces/junction-1/scenarionet-10hz

This is not part of the package and imports nothing from it, because it does not run on the
same Python. The repo targets 3.10 and numpy 2; both MetaDrive checkouts run 3.8 and numpy
1.24, and 3.8 cannot have numpy 2 at all. A pickle that opens perfectly under pytest can
still be unopenable where it is meant to be used, and 3.10 is exactly the interpreter where
that fault is invisible - so the check has to be run from the other side.

`inspection/stage-6-reachability.html` draws the same map, but from the lane model and by
our own code. This draws the pickle, by MetaDrive's code. Only the second can show that the
file survived the crossing.

Reports rather than asserts: every step prints what it found so a partial failure says how
far the dataset got.
"""

from __future__ import annotations

import argparse
import collections
import os
import sys


def _polyline_length(polyline) -> float:
    """Metres along a polyline. Written out rather than imported: numpy is the only thing
    this script may lean on, and it has to behave the same under numpy 1.24."""
    total = 0.0
    for index in range(len(polyline) - 1):
        step = polyline[index + 1][:2] - polyline[index][:2]
        total += float((step[0] ** 2 + step[1] ** 2) ** 0.5)
    return total


# The window every per-step drivability figure is measured over, in seconds. It is a fixed
# span rather than one step on purpose: a step is whatever the dataset was written at, and a
# 100 Hz recording of a drive that passes at 10 Hz would fail a per-step rule for no reason
# but its own density. Turning is concentrated at the polyline's vertices, so once a step is
# shorter than the vertex spacing one step absorbs a whole vertex's turn whatever the step
# is - the identical drive reads 24.7 deg/step at 10 Hz and 12.3 at 100 Hz. Measured over a
# fixed 0.1 s the answer is the same at either rate, and at 10 Hz the window is one step, so
# every number this script has ever printed is unchanged.
GATE_WINDOW_S = 0.1
GATE_TURN_DEG = 30.0


def _step_seconds(metadata) -> float:
    """How long one recorded step of this scenario lasts.

    `metadata.ts` spacing *is* the rate - it is built by multiplying an integer step index by
    the interval, so there is no separate `dt` key and none is wanted. A map-only scenario has
    one timestamp and no spacing, and a plan carries the rate it was baked at, so both are
    read in turn before falling back to MetaDrive's default.
    """
    stamps = metadata.get("ts")
    if stamps is not None and len(stamps) > 1:
        step = float(stamps[1]) - float(stamps[0])
        if step > 0.0:
            return step
    plan = metadata.get("signals") or {}
    step = plan.get("time_step_s")
    if step:
        return float(step)
    return 0.1


def _paint_on_tarmac(map_features) -> tuple[float, str]:
    """The longest run of a painted line lying inside a lane surface it does not belong to.

    A line MetaDrive draws also gets a ghost body, and a solid one sets
    `on_white_continuous_line`, so paint on drivable road reads to an agent as a road boundary.
    `conversion._uncovered_boundaries` cuts those back; this is the check that it did.

    Three things are meant to be touched and are excluded, and all three can be read off the
    dataset without the lane model: the line's own lane, the lanes it names as neighbours - which
    is how two lanes of one road meet exactly on the edge they share - and the junction turns the
    lane is an end of. Leans on shapely, which MetaDrive itself requires; without it there is
    nothing to report rather than a failure.
    """
    try:
        from shapely.geometry import LineString, Polygon
    except ImportError:  # pragma: no cover - MetaDrive pulls shapely in
        return 0.0, ""

    surfaces = {}
    junction_ends = {}
    neighbours = {}
    for identifier, feature in map_features.items():
        if feature.get("type") != "LANE_SURFACE_STREET" or len(feature.get("polygon", [])) < 4:
            continue
        surfaces[identifier] = Polygon(feature["polygon"][:, :2]).buffer(0)
        neighbours[identifier] = set(feature.get("left_neighbor") or ()) | set(
            feature.get("right_neighbor") or ()
        )
        for end in (*feature.get("entry_lanes", ()), *feature.get("exit_lanes", ())):
            junction_ends.setdefault(end, set()).add(identifier)

    worst, culprit = 0.0, ""
    for identifier, feature in map_features.items():
        owner = feature.get("lane_id")
        if owner is None or len(feature.get("polyline", [])) < 2:
            continue
        line = LineString(feature["polyline"][:, :2])
        for other, shape in surfaces.items():
            if other == owner or other in neighbours.get(owner, ()):
                continue
            # A junction turn leaving this lane, or the lane the turn leaves - both meet it.
            if other in junction_ends.get(owner, ()) or owner in junction_ends.get(other, ()):
                continue
            inside = line.intersection(shape.buffer(-0.05))
            for part in getattr(inside, "geoms", [inside]):
                if part.length > worst:
                    worst, culprit = part.length, identifier
    return worst, culprit


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("dataset", help="Directory holding dataset_summary.pkl")
    parser.add_argument(
        "--png",
        default=None,
        help="Where to write the map image. Defaults to stage-6-map-<rate>hz.png in the "
        "dataset's parent directory - beside it rather than in it, because MetaDrive reads "
        "the dataset directory and it must hold nothing but the dataset, and named by rate "
        "because a workspace holds one dataset per rate.",
    )
    arguments = parser.parse_args()

    import numpy

    print(f"interpreter  python {sys.version.split()[0]} / numpy {numpy.__version__}")

    dataset = os.path.abspath(arguments.dataset)

    # Imported here rather than at module scope so the version line above is printed even
    # when MetaDrive is missing, which is itself the likeliest reason this script fails.
    from metadrive.scenario.scenario_description import ScenarioDescription
    from metadrive.scenario.utils import draw_map, read_dataset_summary, read_scenario_data

    summary, lookup, mapping = read_dataset_summary(dataset)
    print("summary      {} scenario(s): {}".format(len(summary), ", ".join(lookup)))

    failures = 0
    scenario = None
    for file_name in lookup:
        path = os.path.join(dataset, mapping[file_name], file_name)
        try:
            scenario = read_scenario_data(path)
        except Exception as error:  # noqa: BLE001 - the point is to name it, not to handle it
            print(f"unpickle     FAILED for {file_name}: {type(error).__name__}: {error}")
            failures += 1
            continue
        print(f"unpickle     ok, {os.path.getsize(path)} bytes on disk")

        # The one array MetaDrive reaches into with a tuple index. A compatibility shim that
        # quietly turned arrays into lists would pass an eyeball check and fail here.
        polylines = [
            feature["polyline"]
            for feature in scenario["map_features"].values()
            if "polyline" in feature
        ]
        kinds = sorted({type(item).__name__ for item in polylines})
        print(
            "geometry     {} polylines, held as {}, first row {}".format(
                len(polylines), "/".join(kinds), polylines[0][0, :2].tolist()
            )
        )

        lanes = sum(
            1
            for feature in scenario["map_features"].values()
            if feature.get("type") == "LANE_SURFACE_STREET"
        )
        # The rate this scenario was written at, which every per-step figure below is measured
        # against. It also decides what `tools/drive.py --step-hz` has to be given: a replayed
        # track is consumed one frame per env.step with no interpolation, so a mismatch drives
        # the route at the wrong speed rather than failing.
        step_s = _step_seconds(scenario["metadata"])
        print(
            "content      {} map features ({} lanes), {} tracks, length {}, "
            "step {:g} s ({:g} Hz)".format(
                len(scenario["map_features"]),
                lanes,
                len(scenario["tracks"]),
                scenario["length"],
                step_s,
                1.0 / step_s,
            )
        )

        # A divider has to satisfy MetaDrive's `is_broken_line` to be drawn dashed, and a
        # boundary type never does however it is spelled - `ScenarioBlock` sends every
        # `is_road_boundary_line` to `_construct_continuous_line`. So a typo here does not
        # fail, it silently draws solid, which is also what a car feels: the ghost body takes
        # its name from the type and only a solid one sets `on_white_continuous_line`.
        # A dash also needs the line to be over 4 m, or `resample_polyline` never runs on it.
        markings = scenario["metadata"].get("lane_markings")
        styles = collections.Counter(
            feature.get("type")
            for feature in scenario["map_features"].values()
            if feature.get("type") != "LANE_SURFACE_STREET"
        )
        short = sum(
            1
            for feature in scenario["map_features"].values()
            if feature.get("type") == "ROAD_LINE_BROKEN_SINGLE_WHITE"
            and _polyline_length(feature["polyline"]) <= 4.0
        )
        stubs = (markings or {}).get("junction_stubs", 0)
        kerbs = (markings or {}).get("junction_kerbs", 0)
        ends = (markings or {}).get("road_ends_unpainted", 0)
        sealed = (markings or {}).get("surfaces_sealed", 0)
        covered = (markings or {}).get("covered_paint", 0)
        covered_m = (markings or {}).get("covered_paint_m", 0)
        print(
            "markings     {}{}{}{}{}{}{}{}".format(
                ", ".join(f"{count} {name}" for name, count in sorted(styles.items()))
                or "no boundaries",
                f" · source {markings['source']}, {markings['merged']} second copies merged"
                if markings
                else " · no lane_markings metadata",
                # Not a fault, and worth saying out loud: these are the markings of lanes the
                # junction trim clamped, which sit inside a junction. Left in, a car turning
                # through the box crosses them and the ghost bodies read as line violations.
                f" · {stubs} left unpainted on junction stubs" if stubs else "",
                # The other half of that: a junction's interior stays bare, but the edge of it
                # facing open ground is painted, so the kerb line does not stop at the mouth.
                # Counted separately from `edges` because these belong to no lane.
                f" · {kerbs} kerb line(s) round the junctions" if kerbs else "",
                # A road that simply stops leaves a bar of bare edge across the carriageway.
                # Never painted - a line there draws as a stop line - and counted so the blank
                # reads as a decision rather than as something nobody looked at.
                f" · {ends} road end(s) left bare" if ends else "",
                # Not a marking, but the road the markings are drawn on: lane surfaces grown to
                # swallow a hole in the tarmac beside them, which would otherwise render as a
                # white line nothing drew. See `conversion._sealed_surfaces`.
                f" · {sealed} surface(s) sealed" if sealed else "",
                # Lines cut back where a merging or turning lane's edge lay inside the lane
                # beside it. Solid paint on drivable road is a ghost body an agent reads as a
                # boundary, which is exactly why this one is worth naming in the report.
                f" · {covered} line(s) cut back off {covered_m:.1f} m of tarmac"
                if covered
                else "",
                f" · {short} divider(s) under 4 m, too short to dash" if short else "",
            )
        )

        # And the check that says the cutting back actually worked. A line drawn through a lane
        # is a ghost body sitting on road a car drives along, so an agent reads a boundary where
        # there is none - the defect `conversion._uncovered_boundaries` exists to remove. Judged
        # from the dataset alone: a lane's own surface, the lanes it names as neighbours (which
        # is how two lanes of one road meet exactly on their shared edge) and the junction turns
        # it is an end of are all meant to be touched.
        worst_run, worst_line = _paint_on_tarmac(scenario["map_features"])
        if worst_run >= 0.5:
            print(
                f"             FAILED: {worst_run:.2f} m of line {worst_line} runs through "
                "another lane's driving surface"
            )
            failures += 1
        else:
            print(f"             longest line inside another lane {worst_run:.2f} m")

        # What 3D needs, and nothing else reports. MetaDrive builds one terrain square of
        # `map_region_size` metres centred on the ego's start - `base_engine` hard-codes the
        # centre at the origin and the loader shifts the scenario so the ego begins there -
        # and outside that square there is no ground and no flattened road. The number that
        # decides whether the whole map can be shown is the largest single-axis offset from
        # the ego's start, because the region is a square and not a circle.
        sdc_id = scenario["metadata"].get("sdc_id")
        if sdc_id is not None and polylines:
            origin = scenario["tracks"][sdc_id]["state"]["position"][0][:2]
            reach = float(numpy.abs(numpy.concatenate(polylines)[:, :2] - origin).max())
            region = 512
            while region < 2 * reach and region < 4096:
                region *= 2
            verdict = (
                "which scenarionet.sim's default of 1024 covers"
                if region <= 1024
                else "and scenarionet.sim's default of 1024 does NOT cover it; the far end of "
                "the map will have no terrain"
            )
            print(
                f"map region   the map reaches {reach:.0f} m from the ego's start, so 3D needs "
                f"map_region_size={region}, {verdict}"
            )

        # The one place a bad traffic light shows up. MetaDrive's `skip_missing_light`
        # defaults to True, so `ScenarioLightManager.after_reset` drops a light whose key is
        # not in `road_network.graph` with a log line and carries on - the dataset loads, the
        # drive runs, and the light simply is not there. Checked here against `map_features`,
        # which is what that graph is built from.
        lights = scenario["dynamic_map_states"]
        if lights:
            missing = sorted(set(lights) - set(scenario["map_features"]))
            plan = scenario["metadata"].get("signals") or {}
            colours = {}
            for light in lights.values():
                for colour in light["state"]["object_state"]:
                    colours[colour] = colours.get(colour, 0) + 1
            print(
                "lights       {} light(s) in {} phase group(s), {} s cycle, {}".format(
                    len(lights),
                    len(plan.get("groups", ())),
                    plan.get("cycle_seconds", "?"),
                    plan.get("source", "no plan in metadata"),
                )
            )
            tally = ", ".join(
                "{} {}".format(name.split("_")[-1].lower(), count)
                for name, count in sorted(colours.items())
            )
            print(f"             colours over the scenario: {tally}")
            if missing:
                print(
                    "             FAILED: {} light(s) keyed on a lane that is not a map "
                    "feature, which MetaDrive would skip silently: {}".format(
                        len(missing), ", ".join(missing[:5])
                    )
                )
                failures += 1
            else:
                print("             every light is keyed on a lane in this map")
        else:
            print(
                "lights       none. OSM carries no signal timing, so a plan is drawn in "
                "inspection/stage-6-signal-builder.html and passed to convert --signals."
            )

        route = scenario["metadata"].get("sdc_route")
        if route:
            print(
                "route        {}: {:.0f} m over {} lanes in {:.0f} s, {} junction "
                "movement(s), {} lane change(s) - {}".format(
                    route["name"],
                    route["distance_m"],
                    len(route["lanes"]),
                    route["duration_s"],
                    route["junction_movements"],
                    route["lane_changes"],
                    route["source"],
                )
            )

        # What the recorded car is actually asked to do. `ReplayEgoCarPolicy` sets both
        # position and heading from these arrays every step, so a 180 degree flip in them is
        # a car that visibly spins on the spot - which is what a junction marker spliced in
        # as a driving line produced, at 13 steps of 575 on one route. Nothing else looks:
        # `sanity_check` checks shapes and lengths, never whether the drive is drivable.
        sdc_id = scenario["metadata"].get("sdc_id")
        if sdc_id is not None and sdc_id in scenario["tracks"]:
            state = scenario["tracks"][sdc_id]["state"]
            heading = numpy.asarray(state["heading"]).reshape(-1)
            speed = numpy.linalg.norm(numpy.asarray(state["velocity"])[:, :2], axis=1)
            # How many recorded steps make up the fixed window. 1 at MetaDrive's own rate.
            window = max(1, int(round(GATE_WINDOW_S / step_s)))
            span = window * step_s
            swung = heading[window:] - heading[:-window] if len(heading) > window else heading[:0]
            turn = numpy.abs((swung + numpy.pi) % (2 * numpy.pi) - numpy.pi)
            snaps = int((numpy.degrees(turn) > GATE_TURN_DEG).sum())
            lateral = float((turn / span * speed[: len(turn)]).max()) if len(turn) else 0.0
            worst_turn = float(numpy.degrees(turn).max()) if len(turn) else 0.0
            slowest, fastest = speed.min() * 3.6, speed.max() * 3.6
            # The radius the drive line actually turns through, which the heading change alone
            # hides: at walking pace a car can turn through anything, so a track can pass the
            # 30 degree rule and still describe a corner no car could take at any speed. A
            # small car needs about 5 m.
            track = numpy.asarray(state["position"])[:, :2]
            # The arc that turn is spread along. A heading is a property of a step, so the
            # swing from step i to step i+w happens between the middle of one and the middle
            # of the other - hence the half-step correction at each end. At window 1 this is
            # exactly `(moved[i] + moved[i+1]) / 2`, which is what it has always been.
            moved = numpy.linalg.norm(numpy.diff(track, axis=0), axis=1)
            along = numpy.concatenate([[0.0], numpy.cumsum(moved)])
            if len(moved) > window:
                spans = (along[window:-1] - along[: -window - 1]) + (
                    moved[window:] - moved[:-window]
                ) / 2.0
            else:
                spans = numpy.zeros(0)
            spans = spans[: len(turn)]
            turning = turn[: len(spans)] > numpy.radians(0.05)
            radius = spans[turning] / turn[: len(spans)][turning] if turning.any() else None
            print(
                f"drivability  worst turn {worst_turn:.1f} deg per {span:g} s window "
                f"({window} step(s) of {step_s:g} s), {snaps} window(s) over "
                f"{GATE_TURN_DEG:.0f} deg; speed {slowest:.0f}-{fastest:.0f} km/h; "
                f"peak lateral {lateral:.1f} m/s^2"
            )
            if radius is not None and len(radius):
                tight = int((radius < 5.0).sum())
                print(
                    f"             tightest radius {float(radius.min()):.1f} m; {tight} "
                    "step(s) under 5 m, which is tighter than a small car turns"
                )
            if snaps:
                print(
                    f"             FAILED: the recorded car turns more than "
                    f"{GATE_TURN_DEG:.0f} deg in {span:g} s {snaps} time(s). Replayed, it "
                    f"spins on the spot."
                )
                failures += 1

            # A baked stop and the tape are two derivations of one clock, so they can drift.
            # A car standing still through a green, or pulling away on a red, is a dataset
            # that contradicts itself - and it would look like a simulator fault, not a
            # converter one. Read straight off the tape MetaDrive will play.
            for stop in (route or {}).get("stops", ()):
                tape = lights.get(stop["lane_id"], {}).get("state", {}).get("object_state")
                if not tape:
                    print(
                        "             FAILED: the car is recorded stopping for "
                        f"{stop['lane_id']}, which carries no light in this scenario"
                    )
                    failures += 1
                    continue
                arrived = int(round(stop["arrived_s"] / step_s))
                left = int(round((stop["arrived_s"] + stop["waited_s"]) / step_s))
                held, moved = tape[min(arrived, len(tape) - 1)], tape[min(left, len(tape) - 1)]
                print(
                    "             stops {:.0f} s at {} for {:.1f} s; the tape reads {} on "
                    "arrival and {} on moving off".format(
                        stop["arrived_s"],
                        stop["lane_id"],
                        stop["waited_s"],
                        held.split("_")[-1].lower(),
                        moved.split("_")[-1].lower(),
                    )
                )
                if not held.endswith("RED") or not moved.endswith("GREEN"):
                    print(
                        "             FAILED: the baked stop and the tape disagree. The car "
                        "waits when the light is not red, or moves off before it is green."
                    )
                    failures += 1

        try:
            ScenarioDescription.sanity_check(scenario)
            print("sanity_check PASS")
        except AssertionError as error:
            print(f"sanity_check FAILED: {error}")
            failures += 1

        # Beside the dataset, never inside it: MetaDrive reads that directory, so it holds
        # the dataset and nothing else. Named by the rate this scenario was written at,
        # because a workspace holds one dataset per rate and a single name would leave the
        # 10 Hz build's map image showing the 100 Hz one.
        png = arguments.png or os.path.join(
            os.path.dirname(dataset), f"stage-6-map-{1.0 / step_s:g}hz.png"
        )
        import matplotlib

        # No display in a container or over ssh. This is a file, not a window.
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        # Drawn here rather than with MetaDrive's `draw_map`, which scatters polyline
        # vertices. That suits Waymo's densely sampled centrelines; ours are OSM ways cut at
        # their nodes, so a straight lane is two points and the scatter comes out as a dot
        # cloud you cannot recognise as a road. Same data, joined up.
        figure, axes = plt.subplots(figsize=(12, 12))
        for feature in scenario["map_features"].values():
            line = feature.get("polyline")
            if line is None:
                continue
            if feature.get("type") == "LANE_SURFACE_STREET":
                axes.plot(line[:, 0], line[:, 1], linewidth=0.9, color="#1f77b4")
            else:
                axes.plot(line[:, 0], line[:, 1], linewidth=0.4, color="#999999")
        axes.set_aspect("equal")
        axes.set_title(f"{scenario['id']} - {lanes} lanes")
        figure.savefig(png, dpi=200, bbox_inches="tight")
        plt.close(figure)
        print(f"drawn        {png}")

        # Still exercised, because it is MetaDrive's own reader and a structure it cannot
        # parse is worth finding here. The figure is discarded.
        draw_map(scenario["map_features"])
        plt.close("all")
        print("draw_map     accepted the features")

    if scenario is None:
        print("driving      nothing loaded")
    elif scenario["tracks"]:
        print("driving      tracks present; scenarionet.sim should run")
    else:
        print(
            "driving      no ego track, so this is a map-only dataset. "
            "scenarionet.num and scenarionet.check_existence work; "
            "scenarionet.sim and check_simulation do not - ScenarioEnv reads the ego's "
            "recorded path to build its route and has no start/end config."
        )

    print("result       {}".format("FAILED" if failures else "OK"))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
