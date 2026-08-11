"""The lane data both Stage 6 pages draw from.

There are two pages over the same network: one answers "where can I get to from here", the
other "give me the drive between these two lanes". They must agree about which lanes join,
or a route the builder happily draws would be one the reachability page says is impossible -
and there would be no way to tell which page was lying.

So the payload is built once, here, from the `neighbours` and `moves` the scenario itself
was built from. Neither page recomputes the network.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from pyproj import Transformer

from osm_scenario.comparison_view import _lonlat
from osm_scenario.lane_model import PreliminaryLaneModel


def edge_name(source_edge: list[str]) -> str:
    """The stretch of road a lane sits on, as the two OSM nodes it runs between.

    `source_edge` is the OSMnx multigraph key - two node ids and a counter that separates
    parallel edges between the same pair. The counter is 0 for all 285 lanes in
    `junction-1`, so it is shown only when it is doing work; printed always it is noise on
    every row of the lane list.
    """
    if len(source_edge) >= 3 and source_edge[2] != "0":
        return f"{source_edge[0]} → {source_edge[1]} #{source_edge[2]}"
    return f"{source_edge[0]} → {source_edge[1]}" if len(source_edge) >= 2 else "?"


def build_lane_payload(
    *,
    model: PreliminaryLaneModel,
    neighbours: Mapping[str, tuple[list[str], list[str]]],
    moves: Mapping[str, list[str]],
) -> dict[str, Any]:
    """Every lane's geometry, labels and outgoing moves, plus where to put the map.

    `exits` and `sideways` are kept apart rather than merged. A page that wants them
    together can concatenate; one that has only the union can never separate them again,
    and the difference between a junction movement and a lane change is the thing both
    pages exist to show.
    """
    transformer = Transformer.from_crs(
        model.metadata.coordinate_system_wkt, "EPSG:4326", always_xy=True
    )

    lanes: list[dict[str, Any]] = []
    for lane in sorted(model.lanes, key=lambda item: item.identifier):
        ways = list(lane.source_way_ids)
        edge = edge_name(lane.source_edge)
        lanes.append(
            {
                "id": lane.identifier,
                "ways": ways,
                # `lane_index`/`lane_count` alone do not tell two segments of the same way
                # apart - most of `junction-1` is idx0/1 - so the pair of OSM nodes the
                # segment runs between is what actually identifies it on the page.
                "short": f"idx{lane.lane_index}/{lane.lane_count} {lane.direction} · {edge}",
                "label": (
                    f"way {', '.join(ways)} · lane {lane.lane_index}/{lane.lane_count} · "
                    f"{lane.direction} · between OSM nodes {edge}"
                ),
                "line": _lonlat(lane.centerline, transformer),
                "exits": list(neighbours[lane.identifier][1]),
                "sideways": list(moves.get(lane.identifier, ())),
            }
        )

    way_counts: dict[str, int] = {}
    for entry in lanes:
        for way in entry["ways"]:
            way_counts[way] = way_counts.get(way, 0) + 1
    ways = [
        {"id": way, "lanes": count}
        for way, count in sorted(way_counts.items(), key=lambda item: (-item[1], item[0]))
    ]

    points = [point for entry in lanes for point in entry["line"]]
    if points:
        lats = [point[0] for point in points]
        lons = [point[1] for point in points]
        center = [(min(lats) + max(lats)) / 2, (min(lons) + max(lons)) / 2]
        bounds = [[min(lats), min(lons)], [max(lats), max(lons)]]
    else:
        center, bounds = [0.0, 0.0], None

    # The path across each junction, keyed by the pair of lanes it joins. Carried so the
    # route builder can measure a drive the way `ego_route.route_polyline` builds it: a
    # junction movement follows the connector, and leaving those out understated the
    # distance by more than a third on a route with eleven of them.
    #
    # `junction` is here for the signal builder, which needs to know which movements meet at
    # the same node before it can say two phase groups conflict. It costs the route builder
    # nothing and keeps both pages reading one description of the junctions.
    connectors = [
        {
            "from": connector.from_lane_id,
            "to": connector.to_lane_id,
            "junction": connector.junction_node_id,
            "line": _lonlat(connector.centerline, transformer),
        }
        for connector in sorted(model.connectors, key=lambda item: item.identifier)
        if connector.status == "active"
    ]

    return {
        "lanes": lanes,
        "connectors": connectors,
        "ways": ways,
        "center": center,
        "bounds": bounds,
    }


def embed(payload: dict[str, Any]) -> str:
    """JSON for a `<script>` block, with the one sequence that could end it early escaped."""
    return json.dumps(payload, separators=(",", ":")).replace("</", "<\\/")
