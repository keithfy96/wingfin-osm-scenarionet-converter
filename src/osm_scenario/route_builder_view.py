# ruff: noqa: E501
"""The Stage 6 route builder: pick two lanes, get a drive.

`ScenarioEnv` has no start and end setting. It is wired to `TrajectoryNavigation`, whose
whole input is a recorded car's positions, so the only way to say "drive from here to there"
is to put a car in the file that already did. Choosing that route is a judgement about the
map - which turn, which direction, whether a lane change is wanted - and so it belongs to a
person rather than to a heuristic in the converter.

The page is a selector. It previews the drive so the choice is informed, but the route that
reaches the pickle is re-derived in Python by `ego_route.plan_route`, over the same lane
model. Nothing that ends up in a dataset comes from code that only ran in a browser.

Both Stage 6 pages are built from `lane_payload.build_lane_payload`, which is handed the
`neighbours` and `moves` the scenario itself was built from. So the route builder cannot
offer a move the reachability page says does not exist, and neither can offer one the
dataset does not contain.

The exchange file is `routes.json`, downloaded here and read by `convert --routes` - the
same arrangement Stage 3 uses for `review.json`, and for the same reason: a browser cannot
write to disk. **MetaDrive never sees it.** It reads the pickles this produces and nothing
else.
"""

from __future__ import annotations

from collections.abc import Mapping
from importlib import resources

from osm_scenario.lane_model import PreliminaryLaneModel
from osm_scenario.lane_payload import build_lane_payload, embed

CLIENT_ASSET = "route-client.js"

ROUTES_FILENAME = "routes.json"

_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Stage 6 - route builder</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
  html,body{margin:0;height:100%%;font:13px/1.5 system-ui,sans-serif;color:#212529}
  #wrap{display:flex;height:100%%}
  #map{flex:1;min-width:0}
  #side{width:380px;overflow-y:auto;padding:16px;border-left:1px solid #dee2e6;background:#f8f9fa}
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
  input[type=text]{flex:1;min-width:0;padding:6px;font:inherit;border:1px solid #ced4da;border-radius:3px}
  .namerow{display:flex;gap:6px;margin-bottom:4px}
  /* Beats `button.primary{width:100%%}`, which would otherwise push the text field to
     nothing and run the button off the edge of the panel. */
  .namerow button.primary{flex:none;width:auto;margin-bottom:0}
  .routes{border:1px solid #e9ecef;border-radius:3px;background:#fff;margin-bottom:6px}
  .rrow{display:flex;justify-content:space-between;align-items:center;gap:8px;padding:4px 6px;cursor:pointer;border-bottom:1px solid #f1f3f5}
  .rrow:last-child{border-bottom:0}
  .rrow:hover{background:#e9ecef}
  .rrow span.n{font-variant-numeric:tabular-nums;color:#868e96;font-size:11px}
  .loadbtn{display:block;font-size:11px;color:#1c7ed6;cursor:pointer;text-decoration:underline}
  .loadbtn input{display:none}
</style>
</head>
<body>
<div id="wrap"><div id="map"></div><div id="side">
<h1>Stage 6 - route builder</h1>
<p class='caption'>Click a lane to start from, then click where to finish. Every route you add
becomes one scenario in the dataset, with a car recorded driving it - which is the only way
MetaDrive can be given a route at all.</p>
<div id="panel"></div>
<h2>Colours</h2>
<div class='key'><span class='sw' style='border-top-color:#2f9e44'></span> start</div>
<div class='key'><span class='sw' style='border-top-color:#c92a2a'></span> end</div>
<div class='key'><span class='sw' style='border-top-color:#7048e8'></span> the drive between them</div>
<div class='key'><span class='sw' style='border-top-color:#f59f00'></span> a lane change on the way</div>
<div class='key'><span class='sw' style='border-top-color:#1c7ed6'></span> reachable from the start</div>
<div class='key'><span class='sw' style='border-top-color:#ced4da'></span> not reachable</div>
<div class='key'><span class='sw' style='border-top-color:#343a40'></span> a lane, before you pick a start</div>
<h2>Then run</h2>
<pre>osm-scenario convert -w %(workspace)s \\
  --config config/default.yaml \\
  --routes %(routes_hint)s</pre>
<p class='caption'>MetaDrive never reads <code>routes.json</code>. It is passed to the converter,
which re-derives each route from this same map and writes the scenario pickles - and those are
all MetaDrive loads.</p>
<h2>Drawn on</h2>
<p class='caption'>Generation <code>%(fingerprint)s</code>. A routes file is stamped with this, and
the converter refuses one drawn on a different map rather than quietly routing through lanes that
have since moved.</p>
</div></div>
<script>window.__ROUTE_PAYLOAD__=%(data)s;</script>
<script>%(client)s</script>
</body></html>
"""


def client_source() -> str:
    """The compiled route client, or a clear error when it was never built."""
    try:
        return resources.files("osm_scenario.assets").joinpath(CLIENT_ASSET).read_text("utf-8")
    except (FileNotFoundError, ModuleNotFoundError) as error:  # pragma: no cover - packaging fault
        raise RuntimeError(
            f"The route builder client bundle is missing ({CLIENT_ASSET}). "
            "Run `npm install && npm run build` in web/ to rebuild it."
        ) from error


def render_route_builder_html(
    *,
    model: PreliminaryLaneModel,
    neighbours: Mapping[str, tuple[list[str], list[str]]],
    moves: Mapping[str, list[str]],
    workspace_name: str,
    model_sha256: str,
    routes_version: int,
) -> str:
    """Draw the reviewed map so a person can pick the drives to build.

    `neighbours` and `moves` are passed in rather than recomputed, for the reason the
    reachability page gives: they must be the objects the scenario was built from, or the
    page and the dataset can disagree about the network.
    """
    payload = build_lane_payload(model=model, neighbours=neighbours, moves=moves)
    routes_hint = f"workspaces/{workspace_name}/routes/{ROUTES_FILENAME}"
    data = embed(
        {
            **payload,
            "identity": {
                "generation_fingerprint": model.metadata.generation_fingerprint,
                "reviewed_lane_model_sha256": model_sha256,
            },
            "routes_version": routes_version,
            "suggested_filename": ROUTES_FILENAME,
        }
    )
    return _TEMPLATE % {
        "data": data,
        "client": client_source(),
        "workspace": f"workspaces/{workspace_name}",
        "routes_hint": routes_hint,
        "fingerprint": model.metadata.generation_fingerprint[:16],
    }
