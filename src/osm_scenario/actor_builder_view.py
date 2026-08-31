# ruff: noqa: E501
"""The Stage 6 actor builder: place the pedestrians, cyclists and street furniture.

MetaDrive replays non-vehicle actors already, and has done all along. `PEDESTRIAN` and
`CYCLIST` are first-class ScenarioNet track types and `ScenarioTrafficManager` spawns them
straight from `tracks`, on a pure replay policy that wants no lane, no route and no map
feature. That manager is registered in every drive this repo runs. It has simply never had
anything but the recorded car to spawn.

**What is missing is not code, it is the paths.** Stage 1 drops footways, and the extracts are
bare regardless: across `junction-1` and `mosque` together the source OSM holds four
`highway=footway` ways, one `steps`, two `path`, and **not one `highway=crossing` node or
`crossing=*` tag of any kind**. There is nothing surveyed to convert. So where a person walks
is a judgement about the place, like the choice of route and the timing of the lights, and it
is made here rather than by a heuristic in the converter.

Placement is by **clicking the map**, not by picking a lane, which is what makes this page
unlike the other two. An actor walks where no lane is, so there is nothing to snap to and
nothing content-addressed to name. The file therefore carries geometry - `[lat, lon]`, the
order every page here speaks - and `osm_scenario.actors` projects it into the model's own
metric CRS on the way in. The identity block is the only thing standing between a stale file
and a pedestrian placed silently in a live carriageway, which is why the page checks it too.

A **crossing is painted only where you ask for one**, per actor. A `CROSSWALK` map feature is
paint and a semantic-camera label and nothing else - `collision_callback` skips it, and no
policy in MetaDrive yields at one - so painting a zebra under every walker would be inventing
infrastructure that neither the survey nor the simulator knows about.

The exchange file is `actors.json`, downloaded here and read by `convert --actors`, the same
arrangement `routes.json` and `signals.json` use. **MetaDrive never sees it.**
"""

from __future__ import annotations

from collections.abc import Mapping
from importlib import resources

from osm_scenario.lane_model import PreliminaryLaneModel
from osm_scenario.lane_payload import build_lane_payload, embed

CLIENT_ASSET = "actor-client.js"

ACTORS_FILENAME = "actors.json"

# Starting points, not recommendations, and the page says so. 1.3 m/s is the figure the
# pedestrian-crossing literature uses for an unhurried adult; MetaDrive's own `Pedestrian`
# carries `SPEED_LIST = [0.4, 1.2]`, which chooses the walk *animation* and not the speed.
# 5.0 m/s is 18 km/h, an ordinary urban cyclist. 4 m is a common zebra width.
DEFAULT_PEDESTRIAN_MPS = 1.3
DEFAULT_CYCLIST_MPS = 5.0
DEFAULT_CROSSING_WIDTH_M = 4.0

_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Stage 6 - actor builder</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
  html,body{margin:0;height:100%%;font:13px/1.5 system-ui,sans-serif;color:#212529}
  #wrap{display:flex;height:100%%}
  #map{flex:1;min-width:0;cursor:crosshair}
  #side{width:400px;overflow-y:auto;padding:16px;border-left:1px solid #dee2e6;background:#f8f9fa}
  h1{font-size:16px;margin:0 0 4px}
  h2{font-size:13px;margin:18px 0 6px;text-transform:uppercase;letter-spacing:.04em;color:#495057}
  .muted{color:#868e96}
  code{font-size:11px;background:#e9ecef;padding:1px 4px;border-radius:3px;word-break:break-all}
  pre{font-size:11px;background:#e9ecef;padding:8px;border-radius:3px;overflow-x:auto}
  .caption{margin:-2px 0 10px;font-size:11px;color:#868e96}
  .verdict{font-size:15px;font-weight:600;padding:6px 10px;border-radius:3px;margin:0 0 4px;background:#d0ebff;color:#1864ab}
  .key{display:flex;align-items:center;gap:8px;margin:3px 0}
  .sw{width:22px;height:0;border-top-width:4px;border-top-style:solid;flex:none}
  button{padding:6px 10px;font:inherit;cursor:pointer;border:1px solid #ced4da;background:#fff;color:#495057;border-radius:3px}
  button.primary{background:#1c7ed6;border-color:#1c7ed6;color:#fff;width:100%%;margin-bottom:6px}
  button:disabled{opacity:.45;cursor:not-allowed}
  button.link{border:0;background:none;color:#c92a2a;padding:0 4px;text-decoration:underline}
  button.danger{border-color:#ffc9c9;background:#fff5f5;color:#c92a2a}
  input[type=text],input[type=number],select{min-width:0;padding:5px;font:inherit;border:1px solid #ced4da;border-radius:3px}
  input[type=number]{font-variant-numeric:tabular-nums}
  input[type=text]{flex:1}
  .row{display:flex;gap:6px;align-items:center;margin-bottom:6px;flex-wrap:wrap}
  .row label{color:#868e96;font-size:11px}
  .row[hidden]{display:none}
  .arow,.wrow{display:flex;justify-content:space-between;gap:8px;align-items:center;padding:5px 6px;border-bottom:1px solid #f1f3f5;font-size:12px;cursor:pointer}
  .arow:last-child,.wrow:last-child{border-bottom:0}
  .arow.on{background:#fff4e6;box-shadow:inset 3px 0 0 #e8590c}
  .arow .n{font-variant-numeric:tabular-nums;color:#868e96;white-space:nowrap}
  .waits{margin-bottom:8px}
  .loadbtn{display:block;text-align:center;padding:6px 10px;font:inherit;cursor:pointer;border:1px solid #1c7ed6;background:#fff;color:#1c7ed6;border-radius:3px}
  .loadbtn input{display:none}
</style>
</head>
<body>
<div id="wrap"><div id="map"></div><div id="side">
<h1>Stage 6 - actor builder</h1>
<p class='caption'>Pick a kind, then click the map where the actor goes - each click adds a
corner, in the order it is walked. Name it, add it, and download <code>actors.json</code>.
Or start from a whole scene with <em>Randomise</em> and edit it down.</p>
<div id="panel"></div>
<h2>Colours</h2>
<div class='key'><span class='sw' style='border-top-color:#adb5bd'></span> a lane, drawn for context</div>
<div class='key'><span class='sw' style='border-top-color:#7048e8;border-top-style:dashed'></span> the actor you are laying out now</div>
<div class='key'><span class='sw' style='border-top-color:#0ca678'></span> an actor already added</div>
<div class='key'><span class='sw' style='border-top-color:#e8590c;border-top-style:dashed'></span> the one you are editing, picked on the map or in the list</div>
<div class='key'><span class='sw' style='border-top-color:#1c7ed6'></span> the loaded route, which is what actors are placed along</div>
<h2>Changing one you have already placed</h2>
<p class='caption'>Click an actor - either its row in the list or the actor itself on the map -
and the form at the top becomes an editor for it. Its kind, name, speed, delay, waits, heading
and crossing all fill in, and its path or position turns dashed orange so you can see which one
you have. <em>Delete&nbsp;&lt;name&gt;</em> appears under the button and throws that one away
without going back to the list to find it. <strong>There is no Save.</strong> Every change lands as you make it, so picking a
different actor or clicking away never strands a half-finished edit. Press <em>Done editing</em>
when you have finished, or click the same row again.</p>
<p class='caption'>The geometry is edited in the same buffer a new actor is drawn in: clicking
the map adds a corner at the end of the path, <em>Undo last point</em> takes one off, and
<em>Clear</em> starts the shape again - the actor keeps the shape it had until you have clicked
enough points for a new one. The same goes for the name: while it is blank or already taken, the
actor simply keeps its old one. Changing a walker into a cone puts it on the first corner of its
own path, and changing it back brings the rest of the path with it.</p>
<p class='caption'>Editing an actor that came from <em>Generate</em> does not make it yours -
it is still one of the ones the button placed, so the next press replaces it along with the
rest. The panel says so when you select one. Draw it by hand if it needs to survive.</p>
<h2>Randomising a starting scene</h2>
<p class='caption'>Set a density per kind and press <em>Generate</em>. The rates are per
kilometre - pedestrians 1, 4 or 10; cyclists 1, 3 or 8; cones 2, 8 or 20; barriers 1, 3 or 8 -
so what you get depends on how long the road is. Everything it places is an ordinary entry in
the list below: select it and change it, remove it, or edit the downloaded file. Pressing Generate again
replaces what it placed last time and leaves anything you drew by hand alone.</p>
<h2>The seed moves them; it does not add any</h2>
<p class='caption'>The seed decides <em>where</em> each actor goes and nothing else. How many
there are is the density table times the length of road, so a new seed gives a different
arrangement of exactly the same number - which is why pressing <em>Generate</em> twice at the
same settings gives the same file byte for byte. <em>new seed</em> rolls a fresh one into the
box and regenerates, so a different scene is one click, and because the number that produced
it stays on screen you can type it back to get that scene again.</p>
<p class='caption'>To change how many, use <em>exactly N objects</em>. It is a target, not a
ceiling - it scales the densities in both directions, so a number above what they asked for
places more rather than being ignored, and you get exactly the number you typed. The mix
stays whatever the densities say: ask for half as many and you get half of each kind, not all
the cones and no pedestrians. The line under the box says what the densities come to on their
own, and 0 places that many. On <code>junction-1</code>'s whole map - 9.3&nbsp;km of usable
lane - that is 168 at <em>medium</em> and 430 at <em>dense</em>.</p>
<p class='caption'>The seed and the number are written into the downloaded
<code>actors.json</code>, and shown again when you load one back:
<em>Loaded file seed = 835819, no of objects = 430</em>. It is <strong>reported, never
applied</strong> - the boxes above are what the next press will do, so loading a file never
overwrites what you were setting up. The converter ignores the block, so a file carrying it
opens anywhere.</p>
<h2>Cones and barriers close a lane</h2>
<p class='caption'>One on its own reads as litter, so they arrive as lines: a cone run
tapering from just inside the kerb across to the middle of the nearside lane, a barrier line
squared and laid down the middle of it. They are <em>in</em> the lane, not on the kerb beside
it, and that is the point - MetaDrive counts anything overlapping a lane as the car in front,
and a barrier never drives off, so whatever is driving that lane stops behind it and stays
there. Measured: one barrier on the recorded car's own line took it from finishing the route
to 35%% of it.</p>
<p class='caption'>Which lane gets closed follows from the geometry, with no special case for
the recorded car. Where the route has an inner lane the run closes the nearside one and the
car goes past on the inside; where the route is already in the nearside lane - and on any
single-lane road - it closes the car's own lane and stops it. Drag one out to the kerb if
you want roadworks that nothing has to drive around.</p></p>
<h2>Load a route first</h2>
<p class='caption'>Without one, actors are scattered over the whole map - as many as
<em>exactly N objects</em> says, which starts at 150 because every lane at <em>dense</em> runs
to hundreds of them - and most will be nowhere near the car.
Load the same <code>routes.json</code> you convert with and the route is drawn in blue, every
actor is placed on or beside a lane it actually drives, and each walker is timed to be
standing at the kerb as the car arrives. That timing is an <em>estimate</em>: the page works
it out from the distance along the route and the average speed in the box, which is why a
walker waits twenty seconds either side rather than stepping out on a stopwatch. Edit any
delay you want to be exact.</p>
<h2>What MetaDrive does with these</h2>
<p class='caption'>Each actor becomes a <code>tracks</code> entry that MetaDrive's own
<code>ScenarioTrafficManager</code> spawns and replays. Nothing at drive time has to be told
about them, and no flag turns them on. A pedestrian and a cyclist are both solid: the ego's
lidar detects them, IDM brakes for one standing in its lane, and hitting one registers as
<code>crash_human</code>.</p>
<h2>They are a tape, not a crowd</h2>
<p class='caption'>An actor walks exactly the path you draw, at the speed you set, whatever
else is happening. It will not wait for a car and a car will not be waved across by it. Give
it a wait where it should stand still, and a start delay to meet the traffic where you want
it - those are the only two controls over its timing, because there is no pedestrian policy
in MetaDrive to give it more.</p>
<h2>Crossings are paint</h2>
<p class='caption'>Ticking <em>paint a crossing</em> emits a <code>CROSSWALK</code> polygon for
the part of the path that lies on the carriageway - stripes on the road surface and a label
for the semantic camera. It changes nothing about behaviour: nothing routes a pedestrian onto
it and no policy yields at one. Leave it off for a walker on the pavement, or for a jaywalker.
The source carries no surveyed crossing anywhere on this map, so every zebra here is one you
chose.</p>
<h2>Then run</h2>
<pre>osm-scenario convert -w %(workspace)s \\
  --config config/default.yaml \\
  --routes %(routes_hint)s \\
  --actors %(actors_hint)s</pre>
<p class='caption'><code>--actors</code> needs <code>--routes</code>: a map-only dataset is one
frame long and holds no tracks, so there would be nowhere for an actor to walk. Each actor is
written into every scenario the routes produce, cut to that scenario's length.</p>
<h2>Reusing a plan you already drew</h2>
<p class='caption'>Load one with the button above. It is refused unless it was drawn on this
generation of the map - and that check matters more here than anywhere else in Stage 6. A stale
route names lane ids that can be found missing; a stale actor names nothing at all, so it would
simply put a pedestrian somewhere else, quite possibly in a live carriageway, with nothing
downstream noticing. The fingerprint moves on <em>every</em> Stage 1 rerun, even over an
unchanged <code>map.osm</code>.</p>
<h2>Drawn on</h2>
<p class='caption'>Generation <code>%(fingerprint)s</code>. Points are stored as
<code>[lat, lon]</code> and projected by the converter, so the page and the dataset cannot
disagree about which of the pair is which.</p>
</div></div>
<script>window.__ACTOR_PAYLOAD__=%(data)s;</script>
<script>%(client)s</script>
</body></html>
"""


def client_source() -> str:
    """The compiled actor client, or a clear error when it was never built."""
    try:
        return resources.files("osm_scenario.assets").joinpath(CLIENT_ASSET).read_text("utf-8")
    except (FileNotFoundError, ModuleNotFoundError) as error:  # pragma: no cover - packaging fault
        raise RuntimeError(
            f"The actor builder client bundle is missing ({CLIENT_ASSET}). "
            "Run `npm install && npm run build` in web/ to rebuild it."
        ) from error


def render_actor_builder_html(
    *,
    model: PreliminaryLaneModel,
    neighbours: Mapping[str, tuple[list[str], list[str]]],
    moves: Mapping[str, list[str]],
    workspace_name: str,
    model_sha256: str,
    actors_version: int,
    routes_version: int,
) -> str:
    """Draw the reviewed map so a person can place the actors on it.

    `neighbours` and `moves` are passed in rather than recomputed, for the reason the other
    two Stage 6 pages give: the page must be drawn from the objects the scenario was built
    from. Here that is load-bearing rather than a formality - the page resolves an imported
    `routes.json` through the same lane graph the converter plans routes on, so a page built
    from different neighbours would offer a corridor the drive never takes.

    `exits` and `sideways` were dropped from this payload while the page only ever drew lanes
    for context. The randomiser reads them, through `route/path.ts`'s `RouteGraph`, because
    `routes.json` names only a route's two ends. `width_m`, `index` and `count` come with them
    and are added here rather than in `build_lane_payload`: the route and signal pages have no
    use for them, and the payload is inlined into the HTML - on `mosque` that is 405 lanes'
    worth. The connectors stay out, because the corridor is drawn from lane centrelines.
    """
    payload = build_lane_payload(model=model, neighbours=neighbours, moves=moves)
    # Centre-out lane geometry, which is what says where the kerb is on a lane the page has
    # only a centreline for. Keyed by identifier because `build_lane_payload` sorts its own.
    geometry = {
        lane.identifier: {
            "width_m": lane.width_m,
            "index": lane.lane_index,
            "count": lane.lane_count,
        }
        for lane in model.lanes
    }
    lanes = [
        {
            **{key: lane[key] for key in ("id", "short", "label", "line", "exits", "sideways")},
            **geometry[lane["id"]],
        }
        for lane in payload["lanes"]
    ]
    data = embed(
        {
            "lanes": lanes,
            "center": payload["center"],
            "bounds": payload["bounds"],
            "identity": {
                "generation_fingerprint": model.metadata.generation_fingerprint,
                "reviewed_lane_model_sha256": model_sha256,
            },
            "actors_version": actors_version,
            "routes_version": routes_version,
            "suggested_filename": ACTORS_FILENAME,
            "defaults": {
                "pedestrian_mps": DEFAULT_PEDESTRIAN_MPS,
                "cyclist_mps": DEFAULT_CYCLIST_MPS,
                "crossing_width_m": DEFAULT_CROSSING_WIDTH_M,
            },
        }
    )
    return _TEMPLATE % {
        "data": data,
        "client": client_source(),
        "workspace": f"workspaces/{workspace_name}",
        "routes_hint": f"workspaces/{workspace_name}/routes/routes.json",
        "actors_hint": f"workspaces/{workspace_name}/actors/{ACTORS_FILENAME}",
        "fingerprint": model.metadata.generation_fingerprint[:16],
    }
