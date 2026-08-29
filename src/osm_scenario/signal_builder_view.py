# ruff: noqa: E501
"""The Stage 6 signal builder: place the traffic lights, and time them.

MetaDrive 0.4.3 has no traffic-light controller and its procedurally generated maps have no
lights at all. A light in a scenario is a *tape* of colours indexed by step, and the only
thing that decides the tape is a phase plan - which OSM does not supply. `highway=traffic_signals`
says a signal exists and carries no cycle, no split and no offset.

So the plan is a judgement about the junction, like the choice of route, and it belongs to a
person rather than to a heuristic in the converter. This page is where that judgement is made.

Placement is on **lanes**, not junctions, because a light stops the traffic leaving one lane
and the wall goes at that lane's downstream end. The junction is where the *conflict* is, and
the page reports those - two groups whose movements cross or merge at the same node, and how
long this plan runs them green together - without trying to solve the phasing.

Surveyed signals are drawn and never selected. `junction-1` makes the reason plain: its one
`highway=traffic_signals` node sits at the edge of the extract, associated with the lanes it
*releases*, so treating it as a placement would put a light where nobody chose one.

The exchange file is `signals.json`, downloaded here and read by `convert --signals` - the
same arrangement the route builder uses for `routes.json`. **MetaDrive never sees it.** It
reads the pickles the converter writes and nothing else.
"""

from __future__ import annotations

from collections.abc import Mapping
from importlib import resources
from typing import Any

from osm_scenario.lane_model import PreliminaryLaneModel
from osm_scenario.lane_payload import build_lane_payload, embed

CLIENT_ASSET = "signal-client.js"

SIGNALS_FILENAME = "signals.json"

# A starting point, not a recommendation. 60 s with 27 s of green and 3 s of amber is an
# ordinary two-phase urban cycle; the page's conflict panel is what says whether the plan
# built from it works, and nothing here is surveyed.
DEFAULT_CYCLE_S = 60.0
DEFAULT_GREEN_S = 27.0
DEFAULT_YELLOW_S = 3.0

_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Stage 6 - signal builder</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
  html,body{margin:0;height:100%%;font:13px/1.5 system-ui,sans-serif;color:#212529}
  #wrap{display:flex;height:100%%}
  #map{flex:1;min-width:0}
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
  .dot{width:14px;height:14px;border-radius:50%%;border:2px solid #fff;box-shadow:0 0 0 1px #adb5bd;flex:none;margin-left:4px}
  button{padding:6px 10px;font:inherit;cursor:pointer;border:1px solid #ced4da;background:#fff;color:#495057;border-radius:3px}
  button.primary{background:#1c7ed6;border-color:#1c7ed6;color:#fff;width:100%%;margin-bottom:6px}
  button:disabled{opacity:.45;cursor:not-allowed}
  button.link{border:0;background:none;color:#c92a2a;padding:0 4px;text-decoration:underline}
  input[type=text],input[type=number]{min-width:0;padding:5px;font:inherit;border:1px solid #ced4da;border-radius:3px}
  input[type=number]{width:64px;font-variant-numeric:tabular-nums}
  input[type=range]{flex:1;min-width:0}
  .row{display:flex;gap:6px;align-items:center;margin-bottom:6px;flex-wrap:wrap}
  .row label{color:#868e96;font-size:11px}
  .gcard{border:1px solid #e9ecef;border-left-width:5px;border-radius:3px;background:#fff;padding:8px;margin-bottom:6px;cursor:pointer}
  .gcard.active{border-color:#1c7ed6;border-left-color:inherit;box-shadow:0 0 0 2px #d0ebff}
  .ghead{display:flex;gap:6px;align-items:center;margin-bottom:6px}
  .ghead input{flex:1}
  /* The cycle at a glance. A hard-stop gradient rather than segments, so a two-second
     amber is drawn at its real width instead of rounding away. */
  .strip{height:14px;border-radius:2px;margin:4px 0;border:1px solid #dee2e6}
  .conflicts{margin-bottom:10px}
  .crow{display:flex;justify-content:space-between;gap:8px;padding:4px 6px;border-bottom:1px solid #f1f3f5;font-size:11px}
  .crow:last-child{border-bottom:0}
  .crow .n{font-variant-numeric:tabular-nums;color:#868e96;white-space:nowrap}
  .crow.bad{background:#fff5f5;color:#c92a2a}
  .crow.bad .n{color:#c92a2a}
  p.ok{color:#2b8a3e;font-weight:600;margin:0 0 4px}
  p.bad{color:#c92a2a;font-weight:600;margin:0 0 4px}
  .loadbtn{display:block;font-size:11px;color:#1c7ed6;cursor:pointer;text-decoration:underline}
  .loadbtn input{display:none}
</style>
</head>
<body>
<div id="wrap"><div id="map"></div><div id="side">
<h1>Stage 6 - signal builder</h1>
<p class='caption'>Add a phase group, then click the lanes it stops. A light stops the traffic
<em>leaving</em> a lane, so it sits at that lane's far end. Drag the preview to see every light
at one moment in the cycle.</p>
<div id="panel"></div>
<h2>Colours</h2>
<div class='key'><span class='sw' style='border-top-color:#2f9e44'></span> green at the previewed moment</div>
<div class='key'><span class='sw' style='border-top-color:#f59f00'></span> amber</div>
<div class='key'><span class='sw' style='border-top-color:#c92a2a'></span> red</div>
<div class='key'><span class='sw' style='border-top-color:#7048e8;border-top-style:dashed'></span> a signal OSM records, with no timing</div>
<div class='key'><span class='sw' style='border-top-color:#343a40'></span> an unsignalled lane</div>
<div class='key'><span class='dot' style='background:#2f9e44'></span> where the light is - the
stop line at the lane's downstream end, which is where the converter puts the wall</div>
<h2>Then run</h2>
<pre>osm-scenario convert -w %(workspace)s \\
  --config config/default.yaml \\
  --routes %(routes_hint)s \\
  --signals %(signals_hint)s</pre>
<p class='caption'>MetaDrive never reads <code>signals.json</code>. The converter expands it into
one colour per 0.1 s step per lane, which is the only form MetaDrive understands - it has no
traffic-light controller of its own.</p>
<h2>The timing is yours, and says so</h2>
<p class='caption'>OSM records that a signal exists and nothing about how it runs. Every number
here is one you chose, and the dataset marks the plan <code>synthesised</code> so it can never be
mistaken for survey.</p>
<h2>Same colours every episode</h2>
<p class='caption'>What the converter writes is a fixed tape, so an agent trained on it can learn
the step number rather than the light. <code>tools/signal_control.py</code> drives these same
lights live from a per-episode offset instead, keeping the gaps between groups intact.</p>
<h2>Drawn on</h2>
<p class='caption'>Generation <code>%(fingerprint)s</code>. A signal plan is stamped with this, and
the converter refuses one drawn on a different map rather than putting a red light across a road
that has since moved.</p>
</div></div>
<script>window.__SIGNAL_PAYLOAD__=%(data)s;</script>
<script>%(client)s</script>
</body></html>
"""


def client_source() -> str:
    """The compiled signal client, or a clear error when it was never built."""
    try:
        return resources.files("osm_scenario.assets").joinpath(CLIENT_ASSET).read_text("utf-8")
    except (FileNotFoundError, ModuleNotFoundError) as error:  # pragma: no cover - packaging fault
        raise RuntimeError(
            f"The signal builder client bundle is missing ({CLIENT_ASSET}). "
            "Run `npm install && npm run build` in web/ to rebuild it."
        ) from error


def surveyed_signals(model: PreliminaryLaneModel) -> list[dict[str, Any]]:
    """The `highway=traffic_signals` nodes Stage 2 associated with lanes, for reference only.

    Kept separate from the plan on purpose. A surveyed signal is evidence that a junction is
    controlled; it is not evidence of where a stop line is, and in `junction-1` the single
    association points at the lanes a signal *releases* because the junction itself falls
    outside the extract.
    """
    known = {lane.identifier for lane in model.lanes}
    return [
        {
            "node": signal.source_node_id,
            "lanes": [lane_id for lane_id in signal.lane_ids if lane_id in known],
            "status": signal.status,
        }
        for signal in sorted(model.signals, key=lambda item: item.identifier)
    ]


def render_signal_builder_html(
    *,
    model: PreliminaryLaneModel,
    neighbours: Mapping[str, tuple[list[str], list[str]]],
    moves: Mapping[str, list[str]],
    workspace_name: str,
    model_sha256: str,
    signals_version: int,
) -> str:
    """Draw the reviewed map so a person can place and time the lights.

    `neighbours` and `moves` are passed in rather than recomputed, for the reason both other
    Stage 6 pages give: they must be the objects the scenario was built from, or the page and
    the dataset can disagree about the network. Here that matters twice over - the conflict
    check reads the connectors out of the same payload, so a movement the dataset does not
    contain cannot be reported as a conflict, and one it does contain cannot be missed.
    """
    payload = build_lane_payload(model=model, neighbours=neighbours, moves=moves)
    data = embed(
        {
            **payload,
            "identity": {
                "generation_fingerprint": model.metadata.generation_fingerprint,
                "reviewed_lane_model_sha256": model_sha256,
            },
            "signals_version": signals_version,
            "suggested_filename": SIGNALS_FILENAME,
            "surveyed": surveyed_signals(model),
            "defaults": {
                "cycle_seconds": DEFAULT_CYCLE_S,
                "green_seconds": DEFAULT_GREEN_S,
                "yellow_seconds": DEFAULT_YELLOW_S,
            },
        }
    )
    return _TEMPLATE % {
        "data": data,
        "client": client_source(),
        "workspace": f"workspaces/{workspace_name}",
        "routes_hint": f"workspaces/{workspace_name}/routes/routes.json",
        "signals_hint": f"workspaces/{workspace_name}/signals/{SIGNALS_FILENAME}",
        "fingerprint": model.metadata.generation_fingerprint[:16],
    }
