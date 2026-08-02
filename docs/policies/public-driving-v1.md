# Public Driving Policy V1

## Purpose

`public-driving-v1` determines which OSM ways enter the converter's playable
motor-vehicle graph. It provides the same explicit selection rule for local
`.osm`, place-query, and bounding-box workflows.

This policy only selects roads. It does not determine lane count, lane width,
turn connectivity, traffic-light phases, Lanelet2 geometry, or ScenarioNet
output.

## Source Code

The implementation is in
[`src/osm_scenario/osm_source.py`](../../src/osm_scenario/osm_source.py):

| Code element | Responsibility |
| --- | --- |
| `ROAD_SELECTION_POLICY_ID` | Records the identifier `public-driving-v1` |
| `PUBLIC_DRIVING_HIGHWAYS` | Declares eligible `highway=*` values |
| `PROHIBITED_ACCESS` | Declares the blocking values `no` and `private` |
| `road_exclusion_reason()` | Applies the selection rules to one OSM way |
| `select_public_driving_graph()` | Filters the OSMnx graph and produces the source audit |

The policy tests are in
[`tests/unit/test_inspection.py`](../../tests/unit/test_inspection.py).

## Included Highway Classes

An OSM way is eligible when its `highway` tag has one of these exact values:

```text
motorway          motorway_link
trunk             trunk_link
primary           primary_link
secondary         secondary_link
tertiary          tertiary_link
residential
living_street
unclassified
road
```

Tolling does not affect selection. For example, a `highway=motorway` way with
`toll=yes` remains included unless another exclusion rule applies.

## Exclusion Rules

An OSM way is excluded when any of the following is true:

1. It has no `highway` tag. It is counted as a non-highway way rather than an
   excluded highway.
2. Its `highway` value is not in the included list. This excludes values such as
   `service`, `footway`, `path`, `steps`, `track`, `construction`, and
   `rest_area`.
3. It has `area=yes`.
4. Any of these access tags has the value `no` or `private`:

```text
access
vehicle
motor_vehicle
motorcar
```

The first applicable reason is recorded for visual inspection and reporting.
Excluded ways remain in the preserved `source/map.osm`; the policy does not
delete or modify the source file.

## Preserved Evidence And Audit

For every selected way, the converter retains its complete source tag dictionary
as `osm_tags_json`. The Stage 1 source audit checks:

- every selected source way is represented in the graph;
- no unselected way remains in the selected graph;
- preserved tags exactly match the source XML;
- graph-node coordinates match source-node longitude and latitude;
- graph edges follow adjacent node references from the source way;
- one-way, reversed one-way, two-way, and roundabout directions are represented
  consistently.

Missing ways, unexpected ways, tag differences, coordinate differences, and
direction differences are blocking errors. Missing lane counts remain warnings
because lane inference occurs later and does not affect road selection.

## Workspace Records

The generated `source/manifest.json` records:

```json
{
  "road_selection": {
    "policy_id": "public-driving-v1",
    "status": "passed"
  }
}
```

It also records selected, excluded, ignored, and represented-way counts;
exclusion counts grouped by reason; and all parity failures. The same audit is
included in `reports/acquisition.json`.

The Stage 1 inspection displays selected roads in green and excluded source
highways in grey. Clicking a feature shows its OSM ID, source tags, and exclusion
reason:

```bash
uv run osm-scenario inspect \
  --workspace workspaces/<map-id> \
  --view source
```

## Mosque Workspace Example

For the current `workspaces/mosque/source/map.osm` snapshot, the policy records:

```text
Selected driving ways:      227
Excluded highway ways:      323
Ignored non-highway ways:   862
```

The 323 excluded highways are grouped as follows:

```text
access=private       164
highway=service      120
highway=footway       28
highway=path           5
highway=steps          2
access=no              1
highway=construction   1
highway=rest_area      1
highway=track          1
```

These counts describe that specific OSM snapshot and will change if the source
file changes.

## Known Boundaries

- V1 intentionally excludes all `highway=service` ways, including publicly
  accessible service roads.
- V1 does not evaluate conditional restrictions such as
  `access:conditional=*`.
- Selection proves consistency with the preserved OSM snapshot, not that the OSM
  data perfectly represents current real-world roads.
- Lane-count and geometry corrections are handled during preliminary Lanelet2
  generation and manual review, not by this policy.

Changing any of these behavioral boundaries requires a new policy version.

