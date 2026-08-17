"""Drive a converted dataset in MetaDrive, with terrain settings that fit an OSM-sized map.

    <metadrive-checkout>/.venv/bin/python tools/drive.py <dataset> --render 3D

Like `check_dataset.py` this is not part of the package and imports nothing from it, because
it does not run on the same Python: the repo targets 3.10 and numpy 2, both MetaDrive
checkouts run 3.8 and numpy 1.24.

`python -m scenarionet.sim` exists and loads the same dataset, so why this. Because in 3D it
shows a map whose roads stop and an ego that sinks into the ground, and none of the four
settings that fix that can be reached from it. What it leaves at its defaults:

* **`map_region_size` (1024).** The terrain is one square of exactly this many metres, centred
  on the world origin - `base_engine.py:386` hard-codes `center_p = [0, 0]` - and the loader
  centralises the scenario on the ego's start, so the square is centred on wherever the drive
  begins. Outside it there is no ground and no flattened road at all. Rather than guess, this
  script measures each scenario and picks the smallest power of two that covers it.

* **The semantic texture, which has no config key at all.** MetaDrive builds the image that
  paints road surface and lane lines at `map_region_size x 22` pixels square - or x11 at 4096
  (`constants.py:499`): 22528 at 1024, 45056 at 4096. A GL context reports its own ceiling -
  measured 16384 on this machine's Intel iGPU and 32768 on its RTX 4050 - and past it the
  texture cannot be uploaded, which is what "the roads stop" looks like. There is no option for
  the 22, so it is patched below; see `_set_semantic_detail`. The ceiling itself is asked of a
  throwaway GL context rather than assumed, because it doubles between this machine's two GPUs
  and the whole resolution follows from it; see `_max_texture_dimension`.

* **Lane-line width, which also has no config key.** `terrain.py:625` passes literal pixel
  thicknesses to `BaseMap.get_semantic_map`, so a line is `thickness / pixels_per_meter` metres
  wide and its real width moves with the size of the map. `--line-width-m` asks in metres
  instead and works out the pixels; see `_set_line_width`.

* **A painted line is drawn short, and that is a fault rather than a setting.**
  `resample_polyline` steps with `np.arange(0, length, interval)` and never reaches the endpoint,
  so every line over 4 m loses up to a whole interval off its end - 554.7 m of paint across 585
  of `mosque`'s 690 painted lines. `_keep_line_ends` puts it back, unconditionally. The interval
  is `--line-interval-m`, because MetaDrive's 2 m also sags inside every curve.

* **`height_scale` (50).** `use_mesh_terrain` is false by default, so the car drives on a flat
  collision plane at z=0 while the *visible* ground is a noise heightfield around it. On
  `junction-1` at 50, the ground within 25 m of the drive reaches +10.4 m and 12% of it stands
  above the road - so the car is buried where it rises and floating where it dips. At 1 those
  become +0.2 m and 0%. The road itself is flattened either way; it is only the surroundings
  that need to come down to match, which is what `_ground_around` below measures.

* **`reactive_traffic`.** `sim.py` has the line commented out, so traffic there is always pure
  replay. Exposed here as `--reactive`, which matters once a scenario holds more than the ego.

It also stops at the end of the dataset. `sim.py` loops to 1,000,000 scenarios and finishes on
`AssertionError: Scenario Index ... out of range`, which reads like a fault in the data and is
not one.

Reports rather than asserts: every scenario prints what it did, so a partial failure says how
far the drive got.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

# Both ends of the range `base_env` accepts: it asserts the value is a power of two within
# these bounds before handing it to `TerrainProperty`.
MIN_REGION_M = 512
MAX_REGION_M = 4096

# The smallest ceiling any current GL context reports, used only when the real one cannot be
# had: `_max_texture_dimension` fails, or nothing is being rendered. Checked against the real
# number after the window exists either way.
FALLBACK_MAX_TEXTURE = 16384

# A real road marking is about 0.10-0.15 m wide. MetaDrive's own thicknesses are in *pixels*
# (2 for white, 3 for yellow, `terrain.py:625`), so what they draw depends on the size of the
# map: 2 px is 0.50 m on `mosque`'s 4096 m square at 4 px/m and 0.0625 m on `junction-1`'s
# 1024 m square at 32 - wrong in both directions from opposite ends. Asking in metres is the
# only form that is right on both. 0 restores MetaDrive's own pixel counts.
LINE_WIDTH_M = 0.15

# How finely a painted line is sampled before it is drawn into the semantic texture. MetaDrive's
# own default is 2 m (`base_map.py:194`, and `terrain.py:620` never passes anything else), which
# on a curve lays 2 m chords inside an arc whose road polygon is filled at full resolution - so
# the tarmac shows past its own line. At 0.25 m the paint is off the true edge by more than a
# texel over about a metre of `mosque`'s 23 km of painted line, against 52.7 m of bare road edge
# at 2 m.
#
# **It also moves the dashes, and that is the one thing here a reader will notice.** A broken line
# is drawn by skipping `floor(STRIPE_LENGTH * 2 / interval)` samples, so at 2 m it comes out 2 m
# on / 2 m off and at anything finer 3 m / 3 m. 3 m is what MetaDrive's own `STRIPE_LENGTH = 1.5`
# asks for and the 2 m is the flooring; `--line-interval-m 2.0` puts the old dashes back and still
# keeps the line ends, which is a separate fix - see `_keep_line_ends`.
LINE_INTERVAL_M = 0.25

# Ground either side of the flattened road, in metres. MetaDrive's 7 leaves our lanes as
# narrow ribbons because they are single carriageways rather than Waymo's full road surfaces.
DRIVABLE_AREA_EXTENSION_M = 10

# Not 0: at zero panda3d builds a singular transform for the terrain node and dies with
# "Tried to invert singular LMatrix4". 1 is the smallest value that keeps the ground within
# about a metre of the plane the car actually drives on.
HEIGHT_SCALE = 1

# MetaDrive's own value (`scenario_env.py:84`), repeated here so the flag that exposes it has a
# default that changes nothing. It is the width of the corridor the ego may drive in either side
# of its recorded route, and beyond it the episode ends as `out_of_road`. That is right for a
# replayed car, which is on the route by construction, and a real limit for anything that steers
# itself: the IDM ego reaches a measured 4.26 m, and a human takes a different turn on purpose.
MAX_LATERAL_DIST_M = 4.0


def _next_power_of_two(value: float) -> int:
    size = MIN_REGION_M
    while size < value and size < MAX_REGION_M:
        size *= 2
    return size


def _region_for(dataset: str) -> tuple[int, float, str]:
    """The smallest terrain square covering every scenario, and what forced that size.

    Measured after centralisation, because that is the state MetaDrive drives: the loader
    shifts everything so the ego's first position is the origin, and the terrain square is
    centred there. So what matters is the largest single-axis offset from the ego's start,
    which is exactly what `TerrainProperty.point_in_map` tests.
    """
    import numpy
    from metadrive.scenario.utils import read_dataset_summary, read_scenario_data

    _, lookup, mapping = read_dataset_summary(dataset)
    furthest = 0.0
    where = "no map features"
    for file_name in lookup:
        path = os.path.join(dataset, mapping[file_name], file_name)
        scenario = read_scenario_data(path, centralize=True)
        points = [
            numpy.asarray(feature["polyline"])[:, :2]
            for feature in scenario["map_features"].values()
            if "polyline" in feature
        ]
        if not points:
            continue
        reach = float(numpy.abs(numpy.concatenate(points)).max())
        if reach > furthest:
            furthest = reach
            where = scenario["id"]
    return _next_power_of_two(2 * furthest), furthest, where


def _longest_red(scenario) -> int:
    """How many steps the longest red in this scenario's plan lasts.

    The headroom a self-driving policy needs on top of the recording: a car that stops has to
    wait out a whole red in the worst case, and the recording is only as long as a drive that
    never stopped. Zero when the scenario carries no plan, which is most of them.
    """
    plan = (scenario.get("metadata") or {}).get("signals")
    if not plan:
        return 0
    cycle = float(plan["cycle_seconds"])
    step = float(plan["time_step_s"])
    longest = max(
        cycle - float(group["green_seconds"]) - float(group["yellow_seconds"])
        for group in plan["groups"]
    )
    return int(round(max(longest, 0.0) / step))


def _baked_stops(scenario) -> list[dict]:
    """The reds the recorded car was written to stop for, if any."""
    return list(((scenario.get("metadata") or {}).get("sdc_route") or {}).get("stops") or [])


def _metadrive_native_texture(region_m: int) -> int:
    """The texture MetaDrive would build for this region if nothing were patched.

    `TerrainProperty.get_semantic_map_pixel_per_meter` is `22 if map_region_size != 4096 else
    11`, so the 4096 m case is not `region * 22`. Only printed, to say what the patch below is
    standing in for.
    """
    return region_m * (11 if region_m == MAX_REGION_M else 22)


def _max_texture_dimension() -> int | None:
    """The GL ceiling the card really reports, or None if it could not be asked.

    Asked in a throwaway subprocess, and that is the whole difficulty: the ceiling is only
    knowable once a GL context exists, and by the time `env.engine.win` does, MetaDrive has
    already read `get_semantic_map_pixel_per_meter` and built the terrain from it. A separate
    process has a context of its own and depends on nothing about MetaDrive's reset ordering.

    It inherits this process's environment, so it sees whichever card the GLX loader was
    pointed at - which is how `scripts/drive.sh` setting `__NV_PRIME_RENDER_OFFLOAD` reaches
    this number. Measured on this machine: 16384 on the Intel iGPU, 32768 on the RTX 4050.
    """
    import subprocess

    probe = (
        "from panda3d.core import loadPrcFileData;"
        "loadPrcFileData('', 'window-type offscreen\\naudio-library-name null');"
        "from direct.showbase.ShowBase import ShowBase;"
        "print(ShowBase(windowType='offscreen').win.getGsg().getMaxTextureDimension())"
    )
    try:
        finished = subprocess.run(
            [sys.executable, "-c", probe],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    for line in reversed(finished.stdout.splitlines()):
        if line.strip().isdigit():
            return int(line.strip())
    return None


def _set_semantic_detail(pixels_per_meter: int) -> None:
    """Pin the semantic texture's resolution.

    One of this script's two monkeypatches - the other is `_set_line_width` - and for the same
    reason: `Terrain` reads this through `TerrainProperty.get_semantic_map_pixel_per_meter()`,
    whose body is `22 if map_region_size != 4096 else 11`, and there is no config key anywhere
    that reaches it, so a map big enough to need a 2048 m square cannot be textured without
    replacing the method. Writing to `TerrainProperty` is MetaDrive's own mechanism for the
    neighbouring value: `base_env.py:335` sets `TerrainProperty.map_region_size` the same way.

    Nothing in the MetaDrive checkout is edited; delete this call and the default returns.
    """
    from metadrive.constants import TerrainProperty

    TerrainProperty.get_semantic_map_pixel_per_meter = classmethod(lambda cls: pixels_per_meter)


def _set_line_width(thickness_px: int, sample_interval_m: float) -> None:
    """Pin how wide a painted lane line is, and how finely it is sampled before it is painted.

    The second monkeypatch, and the thickness has to *override* rather than re-default:
    `terrain.py:625` passes `white_line_thickness=2, yellow_line_thickness=3` explicitly, over
    `BaseMap.get_semantic_map`'s own defaults of 1 and 1. So the wrapper drops whatever the
    caller asked for. There is no config key between the two.

    White and yellow are given the same number. Our data has neither type of yellow line -
    `conversion.py` writes only `ROAD_EDGE_BOUNDARY` and `ROAD_LINE_BROKEN_SINGLE_WHITE` - so
    the yellow value is unreachable today, and a different one would be an unexplained
    difference on the first map that does have one.

    `line_sample_interval` is *added* rather than overridden - `terrain.py:620` never passes it,
    so its default of 2 m stands and there is nothing to drop. See `_keep_line_ends` for what
    that interval costs and `LINE_INTERVAL_M` for the dashes it moves.

    Nothing in the MetaDrive checkout is edited; delete this call and the defaults return.
    """
    from metadrive.component.map.base_map import BaseMap

    original = BaseMap.get_semantic_map

    def with_line_width(self, *arguments, **keywords):
        keywords["white_line_thickness"] = thickness_px
        keywords["yellow_line_thickness"] = thickness_px
        keywords["line_sample_interval"] = sample_interval_m
        return original(self, *arguments, **keywords)

    BaseMap.get_semantic_map = with_line_width


def _keep_line_ends() -> None:
    """Stop MetaDrive throwing away the last piece of every painted line.

    `metadrive/utils/math.py:269 resample_polyline` steps with `np.arange(0, length, interval)`,
    which **never includes the endpoint**, and `scenario_map.py:74/90` runs it over every line
    longer than `interval * 2` before the raster is painted. So every line over 4 m is drawn
    short by up to a whole interval: **554.7 m of paint discarded across 585 of `mosque`'s 690
    painted lines** and 448.2 m across 453 of `junction-1`'s 548, a mean 0.95 m off the end of
    each. It takes lane edges, dividers and junction kerbs alike, so a kerb butted 0.10 m into
    the line beside it has both ends chopped off and the join it was built to close reopens.

    What a reader sees is road edge with no thick line on it and only the shader's own hairline -
    Keith: *"some of the edges are still not containing proper thick lane edges."* Measured
    against the road outline at one texel: `mosque` 324.0 m of it, of which 185.0 m is purely
    this and 52.7 m more is the chord sag a finer interval removes; `junction-1` 420.8 m, 203.5 m
    and 101.4 m. What is left at both is **86.3 m and 115.9 m, which is the 39 and 38 road ends**
    `_junction_kerb_boundaries` leaves bare on purpose and nothing else - the same figure as if
    the resampling were removed altogether.

    The third and last monkeypatch. Both importers took the name rather than the module, so the
    two bindings are rebound separately and nothing else in MetaDrive imports it: `scenario_map`
    is the raster, `scenario_block` the collision ghosts, which must agree with what is drawn.
    """
    import numpy as np
    from metadrive.component.map import scenario_map
    from metadrive.component.scenario_block import scenario_block

    original = scenario_map.resample_polyline

    def to_the_end(points, target_distance):
        resampled = original(points, target_distance)
        last = np.asarray(points)[-1]
        if len(resampled) and np.allclose(resampled[-1], last):
            return resampled
        return np.vstack([resampled, last[: resampled.shape[1]]])

    scenario_map.resample_polyline = to_the_end
    scenario_block.resample_polyline = to_the_end


def _ground_around(engine, path, radius_m=25):
    """How high the visible ground stands beside the car, over the whole drive.

    This is the measurement the symptom needs. The car rides a flat collision plane, so its
    own height is ride height whatever the terrain does - probing directly under it proves
    nothing, and the road is flattened under it in any case. What is actually wrong in a bad
    render is the *landscape*: at MetaDrive's default `height_scale` the ground beside an OSM
    road stands tens of metres above it, so the car is inside a hillside rather than on a road.

    Read back from the texture the terrain actually uploaded, so it reports what is drawn
    rather than re-deriving MetaDrive's arithmetic and hoping the two agree.
    """
    import numpy

    terrain = engine.terrain
    if not getattr(terrain, "render", False) or terrain.heightfield_tex is None:
        return None
    image = terrain.heightfield_tex.getRamImage()
    if not image:
        return None
    field = numpy.frombuffer(image.getData(), dtype=numpy.uint16)
    size = terrain.heightfield_tex.getXSize()
    if field.size != size * size:
        return None
    field = field.reshape((size, size))

    # The same placement `base_map.get_height_map` rasterises with: one pixel per metre,
    # column from x and row from y, both about the centre of the square.
    metres = field.astype(numpy.float64) / 65536.0 * terrain._height_scale * 2
    ground = terrain.origin.getZ() + metres

    highest = -1e9
    above = 0
    total = 0
    for x, y in path:
        column = int(x + size / 2)
        row = int(y + size / 2)
        window = ground[
            max(0, row - radius_m) : row + radius_m, max(0, column - radius_m) : column + radius_m
        ]
        if window.size == 0:
            continue
        highest = max(highest, float(window.max()))
        above += int((window > 0.5).sum())
        total += int(window.size)
    if not total:
        return None
    return highest, above / total


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("dataset", help="Directory holding dataset_summary.pkl")
    parser.add_argument(
        "--render",
        default="none",
        choices=["none", "offscreen", "2D", "3D", "semantic"],
        help="`none` skips graphics entirely, which also means no terrain is built, so it "
        "checks the drive and not the view. `offscreen` builds the full 3D terrain into a "
        "buffer instead of a window - the only way to check the view without a display.",
    )
    parser.add_argument(
        "--scenario-index", type=int, default=None, help="Drive one scenario instead of all"
    )
    parser.add_argument(
        "--map-region-size",
        type=int,
        default=None,
        help="Terrain square in metres, a power of two in [512, 4096]. Measured from the "
        "dataset when not given.",
    )
    parser.add_argument(
        "--semantic-pixels-per-meter",
        type=int,
        default=None,
        help="Resolution of the road-surface texture. Chosen to fit the GL ceiling the card "
        f"reports when not given, or {FALLBACK_MAX_TEXTURE} px if it cannot be asked.",
    )
    parser.add_argument(
        "--line-width-m",
        type=float,
        default=LINE_WIDTH_M,
        help="How wide a painted lane line should be, in metres. MetaDrive's own thickness is "
        "in pixels, so its real width changes with the size of the map; this asks in metres "
        f"and works out the pixels. Default {LINE_WIDTH_M} m, about a real road marking. "
        "1 px is the floor, so a very large map cannot go as thin as a small one. 0 uses "
        "MetaDrive's own 2 px / 3 px.",
    )
    parser.add_argument(
        "--line-interval-m",
        type=float,
        default=LINE_INTERVAL_M,
        help="How finely a painted line is sampled before it is drawn, in metres. MetaDrive's "
        f"own value is 2.0, which sags inside every curve. Default {LINE_INTERVAL_M}. Anything "
        "under 1.5 also makes broken lines 3 m / 3 m instead of 2 m / 2 m; 2.0 restores both.",
    )
    parser.add_argument("--height-scale", type=int, default=HEIGHT_SCALE)
    parser.add_argument("--drivable-area-extension", type=int, default=DRIVABLE_AREA_EXTENSION_M)
    parser.add_argument(
        "--reactive",
        action="store_true",
        help="Give traffic behind the ego an IDM policy instead of replaying it",
    )
    parser.add_argument(
        "--agent-policy",
        default="replay",
        choices=["replay", "idm", "manual"],
        help="`replay` teleports the ego onto its recorded positions, so it passes through "
        "red lights - it has no dynamics to interrupt. `idm` follows the same recorded route "
        "as a reference line while braking for obstacles and for the wall a red light puts "
        "across the lane, so route completion stops being exact by construction. `manual` "
        "hands the wheel to the keyboard - WASD, and the arrow keys are not bound "
        "(`manual_controller.py:50-55`) - and requires --render 3D; the recorded route becomes "
        "the goal rather than the drive, and the run is not bounded by the recording's length.",
    )
    parser.add_argument(
        "--max-lateral-dist",
        type=float,
        default=MAX_LATERAL_DIST_M,
        help="How far sideways of the recorded route the ego may get before MetaDrive ends the "
        f"episode as `out_of_road`. MetaDrive's own value is {MAX_LATERAL_DIST_M} m "
        "(`scenario_env.py:84`) and is the default here, so nothing changes unless it is asked "
        "for. It is on the command line because the recorded route is a *reference line* rather "
        "than a set of walls: under --agent-policy manual a deliberate wrong turn ends the run "
        "within a second, and the same gate cuts off the idm ego at a measured 4.26 m.",
    )
    parser.add_argument(
        "--record",
        default=None,
        help="Write (observation, executed action) pairs to this .npz, for imitation learning. "
        "It reads `vehicle.current_action`, so it captures what the car actually did rather "
        "than what was asked of it - which is why a keyboard drive under --agent-policy manual "
        "produces a file the same shape as one recorded through env.step (see "
        "`examples/drive_with_a_policy.py`). Under --agent-policy replay every recorded action "
        "is [0, 0]: that policy sets the car's position directly and never acts.",
    )
    parser.add_argument(
        "--lights",
        default="tape",
        choices=["tape", "live"],
        help="`tape` replays `dynamic_map_states` - the same colour at the same step on every "
        "episode. `live` drives the same lights from `metadata.signals` and an offset drawn "
        "per episode, so the step number stops predicting the colour.",
    )
    parser.add_argument(
        "--light-seed",
        type=int,
        default=None,
        help="Seed for --lights live, so a run can be repeated.",
    )
    arguments = parser.parse_args()

    # Refused rather than worked around. `ManualControlPolicy.__init__` reads the keyboard
    # through panda3d only when `use_render` is on; without it, it falls back to opening a
    # *pygame* window purely to collect key strikes (`manual_control_policy.py:50-56`), so the
    # car would be driven from a blank window with no view of the map. The failure without this
    # check is a window that never appears, which says nothing about the cause.
    if arguments.agent_policy == "manual" and arguments.render != "3D":
        print(
            f"result       FAILED: --agent-policy manual needs --render 3D, not "
            f"--render {arguments.render}. Without a rendered window MetaDrive reads the "
            f"keyboard through a blank pygame window instead."
        )
        return 1

    import numpy

    print(f"interpreter  python {sys.version.split()[0]} / numpy {numpy.__version__}")

    dataset = os.path.abspath(arguments.dataset)

    if arguments.map_region_size is None:
        region, furthest, where = _region_for(dataset)
        print(
            f"map region   {region} m; the map reaches {furthest:.0f} m "
            f"from the ego's start in {where}"
        )
    else:
        region = arguments.map_region_size
        print(f"map region   {region} m, as asked")

    pixels_per_meter = arguments.semantic_pixels_per_meter
    if pixels_per_meter is None:
        # Only worth a whole process when something is going to be textured, and only when the
        # answer has not already been given on the command line.
        ceiling = _max_texture_dimension() if arguments.render != "none" else None
        if ceiling is None:
            ceiling = FALLBACK_MAX_TEXTURE
            source = f"assuming {FALLBACK_MAX_TEXTURE} px"
        else:
            source = f"the card reports {ceiling} px"
        pixels_per_meter = max(1, ceiling // region)
    else:
        source = "as asked"
    texture = region * pixels_per_meter
    print(
        f"road texture {texture} px square ({pixels_per_meter} px/m, {source}). MetaDrive's "
        f"own choice here would be {_metadrive_native_texture(region)} px, which is why this "
        f"is patched."
    )
    _set_semantic_detail(pixels_per_meter)

    # Metres in, pixels out, and the two do not meet exactly: `cv2.polylines` takes an integer
    # and cannot go below 1, so on a big map the floor decides the width rather than the
    # request does. Say which happened rather than rounding quietly.
    dashes = 2.0 if arguments.line_interval_m > 1.5 else 3.0
    if arguments.line_width_m > 0:
        thickness = max(1, round(arguments.line_width_m * pixels_per_meter))
        drawn = thickness / pixels_per_meter
        floored = "; 1 px is the floor at this resolution" if thickness == 1 else ""
        print(
            f"lane lines   {thickness} px = {drawn:.3f} m "
            f"(asked {arguments.line_width_m:.3f} m{floored}), sampled every "
            f"{arguments.line_interval_m:.2f} m, dashes {dashes:.0f} m on / {dashes:.0f} m off"
        )
        _set_line_width(thickness, arguments.line_interval_m)
    else:
        print("lane lines   MetaDrive's own 2 px / 3 px, as asked")

    # Unconditional, and not behind `--line-width-m`: a line drawn short is a fault in the
    # renderer rather than a preference about how it looks. See `_keep_line_ends`.
    _keep_line_ends()

    from metadrive.component.sensors.rgb_camera import RGBCamera
    from metadrive.envs.scenario_env import ScenarioEnv
    from metadrive.policy.env_input_policy import EnvInputPolicy
    from metadrive.policy.idm_policy import TrajectoryIDMPolicy
    from metadrive.policy.replay_policy import ReplayEgoCarPolicy
    from metadrive.scenario.utils import get_number_of_scenarios

    # `TrajectoryIDMPolicy` subclasses `IDMPolicy`, whose `lane_change_policy` checks whether
    # the object in front is a `BaseTrafficLight` - so it is the only ego policy here that
    # *reacts* to one. `ReplayEgoCarPolicy` sets the car's position directly each step and
    # would drive through a wall of any kind; it stops at a red only because the converter
    # wrote the stop into the positions, which is why the two lines below differ in where the
    # stopping comes from rather than in whether it happens.
    #
    # `manual` is not a third class here. `agent_manager.agent_policy` (`agent_manager.py:64-75`)
    # resolves in a fixed order - `TakeoverPolicy` first, then `manual_control`, then this value
    # - so setting `manual_control` below is what selects `ManualControlPolicy`, and the class
    # named here is not consulted for the ego at all. It is `EnvInputPolicy` rather than a
    # placeholder because that is the class `ManualControlPolicy` subclasses
    # (`manual_control_policy.py:37`): the two differ only in where `[steering, throttle]` comes
    # from, so the pair says what is really happening. And the value is not entirely dead -
    # `scenario_traffic_manager.py:65` reads it again to decide whether the ego is a replayed
    # car, which is what stops traffic spawning on top of a car that is driving itself. That
    # matters only once a scenario holds traffic, but naming `ReplayEgoCarPolicy` here would be
    # a wrong answer waiting for one.
    policy = {
        "replay": ReplayEgoCarPolicy,
        "idm": TrajectoryIDMPolicy,
        "manual": EnvInputPolicy,
    }[arguments.agent_policy]
    print(
        "ego policy   {} - {}".format(
            arguments.agent_policy,
            {
                "replay": "replayed positions; it stops only where the recording stops",
                "idm": "driven along the recorded route; it brakes for red lights itself",
                # `h` rather than a paraphrase: MetaDrive keeps the real list in
                # `constants.py:37` and shows it in the window. Naming `q` here anyway because
                # `b` is the one key that makes the car stop responding - the keyboard does not
                # steer in top-down view - and `b` does not toggle back out of it.
                "manual": "driven from the keyboard: WASD, `h` for MetaDrive's own key list, "
                "`q` back to the driving view from `b`'s top-down one",
            }[arguments.agent_policy],
        )
    )
    if arguments.agent_policy == "manual":
        # MetaDrive keeps the real list in `constants.py:37` and shows it on `h`; printed here
        # too because the first run is the one where nobody knows to press `h`. The focus line
        # is the failure this cannot check for us: panda3d reads the keyboard through the
        # window, so keys pressed while another window has focus reach nothing, and the ego -
        # which spawns at the *recorded* speed rather than at rest - drives off on its own.
        from metadrive.constants import HELP_MESSAGE

        print("             " + HELP_MESSAGE.replace("\n", "\n             ").rstrip())
        print(
            "             Click the window before driving: the keys go to whichever window has "
            "focus.\n             The on-screen `steering` and `throttle` are what the car is "
            "executing - if\n             they stay at 0 while you press W or A, the window is "
            "not getting the keys."
        )

    environment_class = ScenarioEnv
    if arguments.lights == "live":
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from signal_control import live_signal_env

        environment_class = live_signal_env(arguments.light_seed)

    count = get_number_of_scenarios(dataset)
    if arguments.scenario_index is not None and not 0 <= arguments.scenario_index < count:
        print(f"result       FAILED: --scenario-index must be below {count}")
        return 1

    # Terrain is only built when something is rendering it: `Terrain.reset` guards the whole
    # heightfield and texture path on `self.render or use_mesh_terrain`. So `--render none`
    # deliberately exercises none of what this script configures, and `offscreen` is how the
    # same work happens without a display.
    offscreen = {}
    if arguments.render == "offscreen":
        offscreen = {"image_observation": True, "sensors": {"rgb_camera": (RGBCamera, 320, 240)}}

    env = environment_class(
        {
            "data_directory": dataset,
            "num_scenarios": count,
            "use_render": arguments.render == "3D",
            "agent_policy": policy,
            **offscreen,
            # The one key that selects `ManualControlPolicy`; see the comment on `policy` above.
            "manual_control": arguments.agent_policy == "manual",
            "reactive_traffic": arguments.reactive,
            "map_region_size": region,
            "height_scale": arguments.height_scale,
            "max_lateral_dist": arguments.max_lateral_dist,
            "drivable_area_extension": arguments.drivable_area_extension,
            # Longer than any generated route; the loop below ends on the scenario's own
            # length rather than on a step budget.
            "horizon": 100000,
            "show_logo": False,
            "show_fps": False,
            "log_level": logging.WARNING,
        }
    )

    # Built here rather than imported at the top so that a run without --record does not depend
    # on `agent_env` at all, and so the only cost of the flag existing is this branch.
    recorder = None
    if arguments.record:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from agent_env import ActionRecorder

        recorder = ActionRecorder()

    indices = (
        [arguments.scenario_index] if arguments.scenario_index is not None else list(range(count))
    )
    reported_gpu = False
    failures = 0
    try:
        for index in indices:
            observation, _ = env.reset(seed=index)

            if not reported_gpu:
                reported_gpu = True
                window = env.engine.win
                limit = window.getGsg().getMaxTextureDimension() if window else None
                driver = window.getGsg().getDriverRenderer() if window else None
                if limit is None:
                    print("gpu          no graphics context; terrain is not built at all")
                elif texture > limit:
                    print(
                        f"gpu          {driver} reports a {limit} px limit, and the road "
                        f"texture is {texture} px. The road surface will not render. Re-run "
                        f"with --semantic-pixels-per-meter {max(1, limit // region)} or lower."
                    )
                    failures += 1
                else:
                    print(
                        f"gpu          {driver}, {limit} px limit; "
                        f"the {texture} px texture fits"
                    )

            length = env.engine.data_manager.current_scenario_length
            scenario = env.engine.data_manager.current_scenario
            scenario_id = scenario["id"]
            lights = getattr(env.engine, "light_manager", None)
            if recorder is not None:
                recorder.start_episode(scenario_id)

            # The recording's length is the right bound for a replayed car - there is nothing
            # after the last recorded position - and the wrong one for any policy that drives
            # itself. A car of its own that stops at a red needs more steps than the recording
            # has, and cutting it off there reports `did not arrive` for a car that was still
            # driving. MetaDrive itself does not impose this: `horizon` is 100000 above and
            # `ScenarioEnv`'s `allowed_more_steps` defaults to None.
            #
            # A human is bounded by neither. He may sit at the kerb before pulling away, take a
            # wrong turn and come back, or drive the route twice, and none of that is a fault to
            # be cut off after a fixed number of steps. So `manual` has no budget at all -
            # `None` - and the loop ends only when the episode terminates or the window closes.
            if arguments.agent_policy == "manual":
                allowance, budget = None, None
                # Not a detail: `ScenarioEnv` puts the ego on the tape's first position *with
                # the speed recorded there*, so the drive starts mid-traffic rather than at a
                # standstill, and a driver who is still reaching for the keys is already
                # moving. `p` pauses if that is not wanted.
                print(
                    f"             ego starts at {float(env.agent.speed) * 3.6:.0f} km/h - the "
                    "recorded speed, not a standstill; `p` pauses"
                )
            else:
                allowance = 0 if arguments.agent_policy == "replay" else _longest_red(scenario)
                budget = length + allowance

            # A baked stop is computed against the plan's written offsets, so it is right
            # under `--lights tape` and wrong under `--lights live`, which draws a fresh
            # offset every episode. The recorded car will stand still at a green, or drive
            # through a red, and neither is a fault in the data - it is the wrong pairing.
            stops = _baked_stops(scenario)
            if stops and arguments.lights == "live" and arguments.agent_policy == "replay":
                print(
                    f"             note: this track has {len(stops)} baked stop(s), written "
                    "against the plan's own offsets. --lights live redraws the offset, so the "
                    "car will wait at the wrong moment. Use --lights tape, or "
                    "--agent-policy idm."
                )
            # Transitions rather than a colour per step: 651 colours is not a report, and the
            # step a light turns green is the number that answers both questions here - did
            # the ego wait for it, and does that step move between episodes.
            changes = {}
            previous = {}
            heights = []
            speeds = []
            path = []
            info = {}
            steps = 0
            while budget is None or steps < budget:
                previous_observation = observation
                observation, _, terminated, truncated, info = env.step([0, 0])
                if recorder is not None:
                    # The observation that *produced* the action, paired with the action the
                    # car executed for it - so the one from before the step, not the one the
                    # step returned. An imitation learner is fitting the first to the second.
                    recorder.record(previous_observation, env.agent)
                heights.append(float(env.agent.origin.getZ()))
                speeds.append(float(env.agent.speed))
                if lights is not None:
                    # `engine.episode_step`, not the loop counter: the engine increments
                    # inside `env.step`, so the two differ by one and the whole point of this
                    # report is that the step number matches the plan's arithmetic.
                    now = env.engine.episode_step
                    for light in lights.spawned_objects.values():
                        was = previous.get(light.id)
                        if light.status != was:
                            if was is not None:
                                changes.setdefault(light.id, []).append((now, light.status))
                            previous[light.id] = light.status
                # Every tenth step is plenty: the windows overlap heavily at 0.1 s spacing.
                if steps % 10 == 0:
                    path.append(tuple(env.agent.position))
                steps += 1
                if arguments.render == "3D":
                    overlay = {"scenario": scenario_id}
                    if arguments.agent_policy == "manual":
                        # The action the car is *executing*, on screen, because the one thing
                        # that goes wrong here is invisible: panda3d reads the keyboard through
                        # the window's own focus, so a window that does not have it leaves the
                        # policy returning [0, 0] with nothing to say so. The ego also spawns at
                        # the recorded speed rather than at rest, so a car nobody is steering
                        # drives off on its own and looks exactly like a car being steered
                        # badly. These two numbers separate the cases: press W and watch
                        # `throttle` move; if it stays at 0 the window is not getting the keys.
                        #
                        # `controller` is a different question and answers the other half:
                        # `action_info["manual_control"]` (`manual_control_policy.py:87`) is the
                        # policy's own flag for whether it consulted the keyboard *at all* this
                        # step, which it does not in top-down view or when the camera is not
                        # tracking this car. False there, and `q` is what fixes it.
                        steering, throttle = (float(v) for v in env.agent.current_action)
                        consulted = env.engine.get_policy(env.agent.id).action_info.get(
                            "manual_control"
                        )
                        overlay.update(
                            {
                                "steering (A/D)": f"{steering:+.2f}",
                                "throttle (W/S)": f"{throttle:+.2f}",
                                "speed": f"{float(env.agent.speed) * 3.6:.0f} km/h",
                                "controller": "read" if consulted else "IGNORED - press q",
                            }
                        )
                    env.render(text=overlay)
                elif arguments.render in ("2D", "semantic"):
                    env.render(
                        mode="top_down",
                        film_size=(3000, 3000),
                        semantic_map=arguments.render == "semantic",
                        # `sim.py` passes `target_vehicle_heading_up`, which 0.4.3 deprecates
                        # in favour of this name.
                        target_agent_heading_up=False,
                    )
                if terminated or truncated:
                    break

            # The car rides on a flat collision plane, so its height should stay at ride
            # height for the whole drive. A z far from there is the terrain and the physics
            # disagreeing, which is the failure this script exists to make visible.
            print(
                "scenario {:<3} {}: {} of {} steps, arrive_dest={}, completion {:.3f}, "
                "vehicle z {:.3f}..{:.3f} m".format(
                    index,
                    scenario_id,
                    steps,
                    length,
                    bool(info.get("arrive_dest", False)),
                    float(info.get("route_completion", float("nan"))),
                    min(heights) if heights else float("nan"),
                    max(heights) if heights else float("nan"),
                )
            )
            if not info.get("arrive_dest", False):
                # `arrive_dest=False` on its own does not say whether the drive was wrong or
                # merely different. `out_of_road` under `--agent-policy idm`, for instance, is
                # the lateral controller losing the reference line, which says nothing about
                # the data. Naming the reason is the difference between the two.
                named = ("out_of_road", "crash", "crash_object", "crash_vehicle", "max_step")
                reasons = [name for name in named if info.get(name)]
                if allowance is None:
                    # There is no budget under `manual`, so the loop can only have been left by
                    # the episode ending. If none of the named reasons is set, MetaDrive ended
                    # it for one this script does not name rather than the steps running out.
                    ran_out = "the episode ended without arriving"
                elif allowance == 0:
                    ran_out = "ran out of recorded steps"
                else:
                    ran_out = (
                        f"ran out of steps ({budget}: the recording plus {allowance} for a red)"
                    )
                print(
                    "             did not arrive: {}{}".format(
                        ", ".join(reasons) or ran_out,
                        (
                            # `{:g}` so an untouched limit still prints as `4` rather than `4.0`:
                            # the value now arrives as a float from `--max-lateral-dist`, and a
                            # flag whose default changes nothing should change nothing here either.
                            "; lateral {:.2f} m against a {:g} m limit".format(
                                info["lateral_dist"], env.config["max_lateral_dist"]
                            )
                            if info.get("out_of_road") and "lateral_dist" in info
                            else ""
                        ),
                    )
                )
                # Under `manual` the driver is the variable, so not arriving says nothing about
                # the dataset - a wrong turn or a kerb is the human's, and reporting `FAILED`
                # for it would make the exit status mean something different in this mode than
                # in the other two. Printed either way; only counted for a policy that drives
                # the route the same way every time.
                if arguments.agent_policy != "manual":
                    failures += 1

            if lights is not None and lights.spawned_objects:
                offset = getattr(lights, "episode_offset_seconds", None)
                print(
                    "             {} light(s){}".format(
                        len(lights.spawned_objects),
                        ""
                        if offset is None
                        else f", phase offset {offset:.1f} s drawn for this episode",
                    )
                )
                for light_id, transitions in sorted(changes.items()):
                    greens = [step for step, status in transitions if status.endswith("GREEN")]
                    print(
                        "             {} turns green at step(s) {}".format(
                            light_id,
                            ", ".join(str(step) for step in greens[:6]) or "never in this run",
                        )
                    )
                # Only meaningful under `--agent-policy idm`: a replayed ego is placed on its
                # recorded positions, so its speed is the recording's and no light can change
                # it. Printed either way, because that is the fact worth seeing.
                stopped = sum(1 for speed in speeds if speed < 0.2)
                print(
                    "             ego was below 0.2 m/s for {} of {} steps (min {:.2f} m/s)".format(
                        stopped, len(speeds), min(speeds) if speeds else float("nan")
                    )
                )
            elif arguments.lights == "live":
                print("             no lights: this dataset was converted without --signals")

            beside = _ground_around(env.engine, path)
            if beside is not None:
                highest, share = beside
                print(
                    f"             ground within 25 m of the drive reaches {highest:+.1f} m; "
                    f"{share:.0%} of it stands above the road"
                )
    finally:
        env.close()

    if recorder is not None:
        # Said rather than left to be discovered: a replayed car's policy returns None every
        # step, so nothing is ever written to `current_action` and the file comes out as a
        # column of zeros - 352 of them on `junction-1`, measured. It is still saved, because a
        # recording of the observations is a real thing to want; it is simply not a drive.
        empty = recorder.all_zero()
        written = recorder.save(arguments.record)
        if written is None:
            print("recorded     nothing: the drive took no steps")
        else:
            observations, actions = written
            print(
                f"recorded     {observations[0]} steps, observations {observations}, "
                f"actions {actions} -> {arguments.record}"
                + (
                    "\n             every action is [0, 0]: --agent-policy replay sets the "
                    "car's position directly and never acts, so there is nothing here to "
                    "imitate. Use manual or idm."
                    if empty
                    else ""
                )
            )

    print("result       {}".format("FAILED" if failures else "OK"))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
