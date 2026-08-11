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
  paints road surface and lane lines at `map_region_size x 22` pixels square: 22528 at 1024,
  45056 at 2048. A GL context reports its own ceiling - 16384 on an Intel iGPU, 32768 on a
  discrete card - and past it the texture cannot be uploaded, which is what "the roads stop"
  looks like. There is no option for the 22, so it is patched below; see `_set_semantic_detail`.

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

# The smallest ceiling any current GL context reports, and so the one to size the semantic
# texture against when there is no context yet to ask. Checked against the real number after
# the window exists.
ASSUMED_MAX_TEXTURE = 16384

# Ground either side of the flattened road, in metres. MetaDrive's 7 leaves our lanes as
# narrow ribbons because they are single carriageways rather than Waymo's full road surfaces.
DRIVABLE_AREA_EXTENSION_M = 10

# Not 0: at zero panda3d builds a singular transform for the terrain node and dies with
# "Tried to invert singular LMatrix4". 1 is the smallest value that keeps the ground within
# about a metre of the plane the car actually drives on.
HEIGHT_SCALE = 1


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


def _set_semantic_detail(pixels_per_meter: int) -> None:
    """Pin the semantic texture's resolution.

    A monkeypatch, and deliberately the only one. `Terrain` reads this through
    `TerrainProperty.get_semantic_map_pixel_per_meter()`, whose body is
    `22 if map_region_size != 4096 else 11` - there is no config key anywhere that reaches it,
    so a map big enough to need a 2048 m square cannot be textured without replacing the
    method. Writing to `TerrainProperty` is MetaDrive's own mechanism for the neighbouring
    value: `base_env.py:335` sets `TerrainProperty.map_region_size` the same way.

    Nothing in the MetaDrive checkout is edited; delete this call and the default returns.
    """
    from metadrive.constants import TerrainProperty

    TerrainProperty.get_semantic_map_pixel_per_meter = classmethod(lambda cls: pixels_per_meter)


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
        help="Resolution of the road-surface texture. Chosen to fit "
        f"{ASSUMED_MAX_TEXTURE} px when not given.",
    )
    parser.add_argument("--height-scale", type=int, default=HEIGHT_SCALE)
    parser.add_argument("--drivable-area-extension", type=int, default=DRIVABLE_AREA_EXTENSION_M)
    parser.add_argument(
        "--reactive",
        action="store_true",
        help="Give traffic behind the ego an IDM policy instead of replaying it",
    )
    arguments = parser.parse_args()

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
        pixels_per_meter = max(1, ASSUMED_MAX_TEXTURE // region)
    texture = region * pixels_per_meter
    print(
        f"road texture {texture} px square ({pixels_per_meter} px/m). MetaDrive's own "
        f"choice here would be {region * 22} px, which is why this is patched."
    )
    _set_semantic_detail(pixels_per_meter)

    from metadrive.component.sensors.rgb_camera import RGBCamera
    from metadrive.envs.scenario_env import ScenarioEnv
    from metadrive.policy.replay_policy import ReplayEgoCarPolicy
    from metadrive.scenario.utils import get_number_of_scenarios

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

    env = ScenarioEnv(
        {
            "data_directory": dataset,
            "num_scenarios": count,
            "use_render": arguments.render == "3D",
            "agent_policy": ReplayEgoCarPolicy,
            **offscreen,
            "manual_control": False,
            "reactive_traffic": arguments.reactive,
            "map_region_size": region,
            "height_scale": arguments.height_scale,
            "drivable_area_extension": arguments.drivable_area_extension,
            # Longer than any generated route; the loop below ends on the scenario's own
            # length rather than on a step budget.
            "horizon": 100000,
            "show_logo": False,
            "show_fps": False,
            "log_level": logging.WARNING,
        }
    )

    indices = (
        [arguments.scenario_index] if arguments.scenario_index is not None else list(range(count))
    )
    reported_gpu = False
    failures = 0
    try:
        for index in indices:
            env.reset(seed=index)

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
            scenario_id = env.engine.data_manager.current_scenario["id"]
            heights = []
            path = []
            info = {}
            steps = 0
            while steps < length:
                _, _, terminated, truncated, info = env.step([0, 0])
                heights.append(float(env.agent.origin.getZ()))
                # Every tenth step is plenty: the windows overlap heavily at 0.1 s spacing.
                if steps % 10 == 0:
                    path.append(tuple(env.agent.position))
                steps += 1
                if arguments.render == "3D":
                    env.render(text={"scenario": scenario_id})
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
                failures += 1

            beside = _ground_around(env.engine, path)
            if beside is not None:
                highest, share = beside
                print(
                    f"             ground within 25 m of the drive reaches {highest:+.1f} m; "
                    f"{share:.0%} of it stands above the road"
                )
    finally:
        env.close()

    print("result       {}".format("FAILED" if failures else "OK"))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
