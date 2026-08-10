"""Open a converted dataset the way MetaDrive opens it, in MetaDrive's own interpreter.

    <metadrive-checkout>/.venv/bin/python tools/check_dataset.py workspaces/junction-1/scenarionet

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
import os
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("dataset", help="Directory holding dataset_summary.pkl")
    parser.add_argument(
        "--png",
        default=None,
        help="Where to write the map image. Defaults to stage-6-map.png in the dataset's "
        "parent directory - beside it rather than in it, because MetaDrive reads the "
        "dataset directory and it must hold nothing but the dataset.",
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
        print(
            "content      {} map features ({} lanes), {} tracks, length {}".format(
                len(scenario["map_features"]), lanes, len(scenario["tracks"]), scenario["length"]
            )
        )

        try:
            ScenarioDescription.sanity_check(scenario)
            print("sanity_check PASS")
        except AssertionError as error:
            print(f"sanity_check FAILED: {error}")
            failures += 1

        # Beside the dataset, never inside it: MetaDrive reads that directory, so it holds
        # the dataset and nothing else.
        png = arguments.png or os.path.join(os.path.dirname(dataset), "stage-6-map.png")
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
