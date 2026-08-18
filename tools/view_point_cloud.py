"""Open a sensor-survey point cloud in an interactive 3-D viewer.

Reads the `point-cloud.npy` written by `tools/sensor_survey.py` and shows it in
Open3D. Unlike the rest of `tools/`, this runs under **this repo's** Python
(3.10) rather than MetaDrive's 3.8, because Open3D is not in MetaDrive's venv
and must not be added to it:

    uv run --with open3d python tools/view_point_cloud.py \
        workspaces/junction-1/sensor-survey/point-cloud.npy

The array is `(channels, rays, 3)` — x, y, z in metres in the car's own frame,
not a flat `(N, 3)` list — so it is reshaped before being handed over.

**A ray that hits nothing lands on the depth buffer's far plane**, which is why
the raw extent of the file runs to roughly +/-18 km. Those points are dropped at
`--max-range` (200 m by default, the sensor's own range) so the viewer autoscales
to the road rather than to the sky. `--max-range 0` keeps everything.

Controls: drag to orbit, scroll to zoom, shift-drag to pan, `h` in the window for
the rest.
"""

import argparse
import os
import sys

import numpy

DEFAULT_MAX_RANGE_M = 200.0


def _colours(points, mode):
    """Per-point RGB in [0, 1], as a blue -> cyan -> yellow -> red ramp."""
    if mode == "height":
        value = points[:, 2]
    elif mode == "range":
        value = numpy.linalg.norm(points, axis=1)
    else:  # "distance-ahead"
        value = points[:, 0]

    low, high = float(value.min()), float(value.max())
    if high - low < 1e-9:
        return numpy.tile(numpy.array([0.6, 0.6, 0.6]), (len(points), 1))
    t = (value - low) / (high - low)

    # Four control points, linearly interpolated.
    stops = numpy.array(
        [
            [0.10, 0.10, 0.60],  # blue
            [0.10, 0.75, 0.75],  # cyan
            [0.95, 0.90, 0.20],  # yellow
            [0.85, 0.15, 0.10],  # red
        ]
    )
    scaled = t * (len(stops) - 1)
    index = numpy.clip(scaled.astype(int), 0, len(stops) - 2)
    frac = (scaled - index)[:, None]
    return stops[index] * (1.0 - frac) + stops[index + 1] * frac


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("path", help="point-cloud.npy, or a workspace directory")
    parser.add_argument(
        "--max-range",
        type=float,
        default=DEFAULT_MAX_RANGE_M,
        help="drop points further than this from the car, in metres. "
        "0 keeps every point, including the far-plane misses (default: %(default)s)",
    )
    parser.add_argument(
        "--colour",
        choices=("range", "height", "distance-ahead"),
        default="range",
        help="what the colour ramp encodes. `height` is the usual choice for a "
        "lidar and is near-useless here: our scenarios hold one car and no "
        "buildings, so every return is the ground, within 0.1 m of z = -2 m "
        "(default: %(default)s)",
    )
    parser.add_argument(
        "--point-size", type=float, default=2.0, help="pixels (default: %(default)s)"
    )
    parser.add_argument(
        "--no-axes",
        action="store_true",
        help="hide the frame drawn at the car: red = x ahead, green = y left, blue = z up",
    )
    arguments = parser.parse_args()

    path = arguments.path
    if os.path.isdir(path):
        for candidate in (
            os.path.join(path, "point-cloud.npy"),
            os.path.join(path, "sensor-survey", "point-cloud.npy"),
        ):
            if os.path.exists(candidate):
                path = candidate
                break
        else:
            print(f"no point-cloud.npy under {arguments.path}", file=sys.stderr)
            return 1

    raw = numpy.load(path)
    points = raw.reshape(-1, raw.shape[-1])[:, :3].astype(numpy.float64)
    total = len(points)

    ranges = numpy.linalg.norm(points, axis=1)
    print(f"{path}  {raw.shape}  -> {total} points")
    print(
        f"raw range [{float(ranges.min()):.1f}, {float(ranges.max()):.1f}] m"
        " - a ray that hits nothing lands on the far plane"
    )

    if arguments.max_range > 0:
        keep = ranges < arguments.max_range
        points = points[keep]
        if not len(points):
            print(f"nothing within {arguments.max_range:.0f} m", file=sys.stderr)
            return 1
        kept = numpy.linalg.norm(points, axis=1)
        share = 100.0 * len(points) / total
        print(
            f"within {arguments.max_range:.0f} m: {len(points)} of {total} rays"
            f" ({share:.1f}%), spanning"
            f" [{float(kept.min()):.2f}, {float(kept.max()):.2f}] m"
        )
    else:
        print("keeping every point, including the far-plane misses")

    import open3d

    cloud = open3d.geometry.PointCloud()
    cloud.points = open3d.utility.Vector3dVector(points)
    cloud.colors = open3d.utility.Vector3dVector(_colours(points, arguments.colour))

    geometries = [cloud]
    if not arguments.no_axes:
        extent = float(numpy.abs(points).max())
        geometries.append(
            open3d.geometry.TriangleMesh.create_coordinate_frame(
                size=max(1.0, extent * 0.05)
            )
        )

    print("opening viewer - drag to orbit, scroll to zoom, 'h' for controls")
    viewer = open3d.visualization.Visualizer()
    if not viewer.create_window(window_name=os.path.basename(path)):
        print(
            "could not open a window. On Wayland, Open3D's GLFW picks the Wayland\n"
            "backend and then fails to initialise GLEW; run it through XWayland:\n"
            "  env -u WAYLAND_DISPLAY XDG_SESSION_TYPE=x11 <the same command>\n"
            "or use ./scripts/view-point-cloud.sh, which does that for you.",
            file=sys.stderr,
        )
        return 1
    for geometry in geometries:
        viewer.add_geometry(geometry)
    options = viewer.get_render_option()
    options.point_size = arguments.point_size
    options.background_color = numpy.array([0.05, 0.05, 0.08])
    viewer.run()
    viewer.destroy_window()
    return 0


if __name__ == "__main__":
    sys.exit(main())
