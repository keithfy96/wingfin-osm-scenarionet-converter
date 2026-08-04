# Public Driving Policy v1

`public-driving-v1` selects OSM ways suitable for the Stage 1 public
motor-vehicle road graph. Its implementation is
[`src/osm_scenario/osm_source.py`](../../src/osm_scenario/osm_source.py).

## Included roads

Ways must have a supported `highway=*` value and must not be explicitly private
or otherwise unavailable to ordinary motor traffic. The policy preserves each
selected way's complete OSM tag dictionary for auditing.

## Excluded roads

The selector excludes non-driving classes such as pedestrian paths and rejects
ways whose access tags clearly prohibit public motor vehicles. Every excluded
source way is recorded with a reason so coverage can be inspected rather than
silently discarded.

## Directionality

One-way tags and road-class conventions determine directed graph edges. The
graph remains unsimplified to preserve source nodes, way identifiers, and
intersection evidence.

## Audit boundary

Selection decides which ways enter the Stage 1 graph. It does not establish
that lane counts, connectivity, restrictions, signals, widths, or stop lines
are complete. Those properties are reported by the Stage 1B data audit.

```bash
uv run osm-scenario inspect --workspace workspaces/example --view source
uv run osm-scenario inspect --workspace workspaces/example --view audit
```
