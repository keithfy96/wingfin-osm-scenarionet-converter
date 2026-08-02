# Project Guide: From OpenStreetMap to a Lanelet2-Ready Road Network

This guide explains the data stored in an OpenStreetMap (`.osm`) source file,
how Stage 1 converts that source into a normalized road network, and which new
data points are introduced for later Lanelet2 generation.

The central distinction is that OSM generally describes **roads**, while
Lanelet2 needs **individual directed lanes with boundaries and connectivity**.
Stage 1 bridges those representations without generating the lanelets yet.

```text
Raw OSM
nodes + ways + relations + tags
        |
        v
Stage 1A - implemented
select roads, build directed graph, preserve evidence
        |
        +--> source/map.osm
        +--> source/manifest.json
        +--> normalized/road-network.graphml
        `--> normalized/road-network.gpkg
        |
        v
Stage 1B - planned
project coordinates into metres and run preflight checks
        |
        +--> normalized/road-network-local.graphml
        +--> normalized/road-network-local.gpkg
        +--> reports/acquisition.json
        `--> reports/acquisition.md
        |
        v
Stage 2 - planned
road segments -> lane centerlines -> lane boundaries -> Lanelet2
```

## 1. Data in the source OSM file

An `.osm` file is XML containing three primary geographic object types:

1. Nodes
2. Ways
3. Relations

Tags attach meaning to these objects. The repository's small reference source
is `tests/fixtures/osm/tiny.osm`.

### Nodes

A node is one geographic point:

```xml
<node id="2" lat="3.1501" lon="101.7001">
  <tag k="highway" v="traffic_signals" />
</node>
```

| Field | Meaning | Why it matters |
|---|---|---|
| `id` | Stable OSM identifier | Lets ways and relations refer to the point |
| `lat` | Latitude in WGS84 degrees | Supplies its geographic position |
| `lon` | Longitude in WGS84 degrees | Supplies its geographic position |
| Tags | Meaning attached to the point | Identifies signals, crossings, and other controls |

A node can be an ordinary geometry point, an intersection, a traffic signal,
or a crossing. For example:

```xml
<node id="4" lat="3.1503" lon="101.7003">
  <tag k="highway" v="crossing" />
  <tag k="crossing" v="marked" />
</node>
```

This identifies node `4` as a marked crossing.

### Ways

A way is an ordered list of node references:

```xml
<way id="10">
  <nd ref="1" />
  <nd ref="2" />
  <nd ref="3" />
  <nd ref="4" />

  <tag k="highway" v="residential" />
  <tag k="lanes" v="2" />
  <tag k="turn:lanes" v="through|right" />
  <tag k="maxspeed" v="50" />
  <tag k="width" v="7" />
  <tag k="access" v="yes" />
</way>
```

The ordered references describe the road geometry:

```text
node 1 -> node 2 -> node 3 -> node 4
```

The tags describe the road:

| Tag | Meaning | Lanelet2 relevance |
|---|---|---|
| `highway=residential` | Road classification | Determines whether the way is a usable driving road |
| `lanes=2` | Total vehicle-lane count | Determines how many lanelets may be required |
| `oneway=yes` | Travel is allowed in one direction | Determines directed lane orientation |
| `turn:lanes=through\|right` | Per-lane turn permissions | Helps connect lanes through intersections |
| `maxspeed=50` | Posted speed limit | Can become lane or regulatory metadata |
| `width=7` | Approximate total road width | Helps calculate lane and boundary offsets |
| `access=yes` | General access permission | Helps determine whether vehicles may use the road |
| `junction=roundabout` | Junction classification | Changes direction and connectivity handling |
| `surface=asphalt` | Surface material | Useful descriptive metadata |
| `bridge=yes` or `tunnel=yes` | Vertical separation | Helps avoid connecting roads that merely cross in plan view |

An OSM way normally represents a **road centerline**, not separate lane
centerlines or lane boundaries. For example, `lanes=2` and `width=7` say that
the road has two lanes across approximately seven metres, but they do not
provide two lane polygons. Those geometries must be constructed later.

### Relations

Relations describe relationships that cannot be represented by one node or
way. The fixture includes a turn restriction:

```xml
<relation id="20">
  <member type="way" ref="10" role="from" />
  <member type="node" ref="2" role="via" />
  <member type="way" ref="10" role="to" />

  <tag k="type" v="restriction" />
  <tag k="restriction" v="no_u_turn" />
</relation>
```

This means that travelling from way `10`, through node `2`, and back onto way
`10` as a U-turn is prohibited.

| Field | Meaning |
|---|---|
| Relation `id` | Identifier for the rule |
| Member `type` | Whether the member is a node, way, or relation |
| Member `ref` | Identifier of the referenced member |
| Member `role` | Its function, such as `from`, `via`, or `to` |
| `type=restriction` | Identifies a traffic restriction |
| `restriction=no_u_turn` | Identifies the prohibited maneuver |

Relations are one reason `source/map.osm` remains available. An OSMnx road
graph does not always represent every relation completely, so Stage 2 may need
to consult the preserved source evidence.

### Tags are source evidence

OSM tags are string key-value pairs:

```xml
<tag k="lanes" v="2" />
```

Even when a value looks numeric, its raw value is a string. Real data can also
contain values such as `lanes=2;3` or `maxspeed=50 mph`. Parsing a tag and
preserving its exact source value are therefore separate operations.

## 2. Information Lanelet2 needs that ordinary OSM may not provide

OSM might describe a road as:

```text
road centerline
lanes=2
oneway=no
width=7
```

Lanelet2 ultimately needs:

```text
Lanelet A
  centerline
  left boundary
  right boundary
  travel direction
  predecessor and successor connections

Lanelet B
  centerline
  left boundary
  right boundary
  travel direction
  predecessor and successor connections
```

The complete conversion must establish or infer:

- Which roads are usable by public vehicles
- Which direction each road segment can be travelled
- How many directed lanes exist
- Which side of the road each direction uses
- Lane widths in metres
- Lane centerlines and left/right boundaries
- Intersection connectivity and turn permissions
- Traffic-signal and crossing associations
- A consistent metric coordinate system

Stage 1 prepares this information, but intentionally stops before generating
lane geometry.

## 3. Stage 1A: accept and preserve one source

Stage 1A accepts exactly one source:

- A local `.osm` file
- A place query
- A longitude/latitude bounding box

It also requires an explicit workspace-wide driving side:

```text
left or right
```

For Malaysia, the expected value is normally `left`. Driving side is required
because it later determines the physical ordering of lanes, which half of a
two-way road belongs to each direction, and how lane offsets are applied.

The decision is recorded rather than hidden as an assumption:

```json
{
  "driving_side": "left",
  "driving_side_source": "explicit_cli"
}
```

For local input, the source is copied into or read from `source/map.osm` and
preserved. For place or bounding-box acquisition, OSMnx downloads the driving
network and saves an OSM graph export. The manifest distinguishes an original
local file from an OSMnx-generated export.

## 4. Stage 1A: select the public driving network

Raw OSM can contain public roads, private roads, footways, service roads,
building outlines, parking areas, and many other features. The converter
applies the versioned `public-driving-v1` selection policy.

It currently retains public classes such as motorways, trunks, primary,
secondary and tertiary roads, residential roads, living streets,
unclassified roads, and their corresponding link classes.

It excludes a way when, for example:

- It is a footway or another unsupported highway class
- It is a service road under the current policy
- It has `access=private` or `access=no`
- It represents an area rather than a road centerline

The fixture contains five ways:

| Way | Data | Result |
|---|---|---|
| `10` | Public residential road | Retained |
| `11` | One-way motorway with `toll=yes` | Retained |
| `12` | `highway=service` | Excluded |
| `13` | `access=private` | Excluded |
| `14` | `highway=footway` | Excluded |

The toll motorway is retained because a toll is not the same as prohibited
access.

Stage 1 records new audit data explaining the result:

```json
{
  "policy_id": "public-driving-v1",
  "source_way_count": 5,
  "selected_source_ways": 2,
  "excluded_source_ways": 3,
  "excluded_by_reason": {
    "access=private": 1,
    "highway=footway": 1,
    "highway=service": 1
  },
  "status": "passed"
}
```

## 5. Stage 1A: convert ways into a directed graph

OSM ways are converted into an unsimplified directed multigraph.

### Break ways into adjacent segments

Source way `10` contains:

```text
1 -> 2 -> 3 -> 4
```

Stage 1 splits it into adjacent directed segments:

```text
1 -> 2
2 -> 3
3 -> 4
```

Because way `10` is not one-way, reverse travel is also created:

```text
2 -> 1
3 -> 2
4 -> 3
```

One source way with four nodes therefore produces six directed graph edges.
Fixture way `11` is one-way, so its two nodes produce only one directed edge.
The retained fixture roads consequently produce seven graph edges.

### Keep the graph unsimplified

Intermediate geometry nodes are retained. If a curved road described as
`A -> B -> C -> D` were collapsed into only `A -> D`, later lane boundaries
could cut across the curve instead of following it.

### Allow parallel edges

The graph is a multigraph, so multiple road edges can connect the same pair of
nodes. Every edge is identified by `(u, v, key)`:

| Field | Meaning |
|---|---|
| `u` | Starting node |
| `v` | Ending node |
| `key` | Distinguishes parallel edges with the same `u` and `v` |

This supports divided roads, ramps, parallel carriageways, and other cases
where one node pair is not enough to identify a road segment.

## 6. New graph data points introduced by Stage 1A

Stage 1A introduces structural and derived attributes that are not direct XML
fields.

### Node representation

The source node:

```xml
<node id="2" lat="3.1501" lon="101.7001">
  <tag k="highway" v="traffic_signals" />
</node>
```

becomes approximately:

```json
{
  "node_id": 2,
  "x": 101.7001,
  "y": 3.1501,
  "highway": "traffic_signals",
  "osm_tags_json": {
    "highway": "traffic_signals"
  }
}
```

| New field | Meaning | Why needed |
|---|---|---|
| `x` | Longitude in the WGS84 graph | Provides a consistent graph/geospatial convention |
| `y` | Latitude in the WGS84 graph | Provides a consistent graph/geospatial convention |
| `osm_tags_json` | Exact source tags | Supports traceability and parity checks |
| Point geometry in GPKG | GIS representation of `x` and `y` | Enables mapping and spatial inspection |

At Stage 1A, `x` and `y` still represent degrees, not metres.

### Edge representation

The first segment of way `10` becomes approximately:

```json
{
  "u": 1,
  "v": 2,
  "key": 0,
  "osmid": 10,
  "highway": "residential",
  "lanes": "2",
  "turn:lanes": "through|right",
  "maxspeed": "50",
  "width": "7",
  "access": "yes",
  "oneway": false,
  "reversed": false,
  "length": 15.713,
  "osm_tags_json": {
    "10": {
      "highway": "residential",
      "lanes": "2",
      "turn:lanes": "through|right",
      "maxspeed": "50",
      "width": "7",
      "access": "yes"
    }
  }
}
```

| Field | Source or derived? | Why needed |
|---|---|---|
| `u` | Derived from node order | Identifies the start of directed travel |
| `v` | Derived from node order | Identifies the end of directed travel |
| `key` | Graph-generated | Distinguishes parallel edges |
| `osmid` | Preserved | Links the edge to its source way |
| `oneway` | Interpreted | Determines whether reverse edges exist |
| `reversed` | Derived | Shows reverse travel relative to source node order |
| `length` | Calculated | Supplies road-segment length |
| `geometry` | Constructed or preserved | Supplies a drawable road line |
| `osm_tags_json` | Added | Preserves the complete original tag evidence |

Values such as `lanes`, `maxspeed`, and `width` are preserved OSM evidence
attached to a more useful graph edge rather than newly invented road facts.

## 7. Stage 1A source-parity audit

The converter checks that normalization has not corrupted the source meaning.
It verifies that:

- Every selected OSM way is represented
- No unexpected way remains
- Node coordinates match the source
- Preserved tags match the source
- Directed edges agree with `oneway` and roundabout rules

The audit adds fields such as:

```text
missing_way_ids
extra_way_ids
coordinate_mismatch_node_ids
tag_mismatch_way_ids
direction_mismatches
represented_way_ids
output_nodes
output_directed_edges
status
errors
```

Successfully writing a GraphML file only proves that serialization worked. The
audit provides evidence that the produced graph still agrees with the source.

## 8. Stage 1A artifacts

### `source/map.osm`

This is the preserved source evidence. It retains the original nodes, ways,
relations, identifiers, tags, and coordinates. Relations such as turn
restrictions remain here even when they are not fully represented in GraphML.

### `normalized/road-network.graphml`

GraphML stores the directed topology used by later converter stages:

```text
nodes + directed edges + connectivity + retained road attributes
```

It is the machine-oriented normalized road graph. It does not contain
individual generated lanes or Lanelet2 boundaries.

### `normalized/road-network.gpkg`

The GeoPackage exposes the graph as GIS feature layers:

```text
nodes -> Point features
edges -> LineString features
```

It is intended for QGIS, GeoPandas, filtering, visualization, and operator
review. It is not an additional conversion stage or the source of truth for
lane generation.

## 9. New provenance data introduced by Stage 1A

The generated `source/manifest.json` contains operational data not present in
the OSM source:

| New data | Why it is required |
|---|---|
| Acquisition timestamp | Identifies when the workspace was created |
| Source type | Distinguishes a local file, place query, and bounding box |
| Source checksum | Detects later modification of the input |
| Artifact checksums | Detects modification of GraphML and GPKG outputs |
| Artifact paths and sizes | Locates and verifies generated outputs |
| Driving side and provenance | Records a critical operator decision |
| Node and edge counts | Provides basic graph validation |
| Geographic bounds | Describes the network's spatial coverage |
| Directed and simplified flags | Records graph semantics |
| Road-selection audit | Explains retained and excluded roads |
| Tool versions | Supports reproducible conversion |

These fields do not describe individual roads, but they make the conversion
traceable, verifiable, and reproducible.

## 10. Stage 1B: convert degrees into metres

Stage 1B is planned but not implemented yet.

The Stage 1A graph uses WGS84 coordinates:

```text
x = longitude in degrees
y = latitude in degrees
```

Degrees are unsuitable for lane-width and boundary-offset calculations. Stage
1B will create a local azimuthal-equidistant coordinate frame:

```text
local x = east, in metres
local y = north, in metres
```

The origin is an explicitly configured longitude and latitude when supplied;
otherwise, the planned default is the centroid of the retained road network.

Stage 1B needs to introduce and record:

| New data | Why required |
|---|---|
| Source CRS | Identifies WGS84 as the input coordinate system |
| Local CRS definition | Makes local coordinates meaningful and reproducible |
| Origin longitude and latitude | Defines where local `(0, 0)` is located |
| Axis order | Prevents longitude/latitude reversal |
| Units | Confirms that geometry calculations use metres |
| Forward transformation | Converts WGS84 into local East-North coordinates |
| Inverse transformation | Converts generated geometry back to WGS84 |
| `pyproj` version | Records which projection implementation was used |
| Maximum round-trip error | Verifies that forward and inverse conversion are accurate |

## 11. Stage 1B preflight data

Before lane construction, Stage 1B is intended to check and report:

- Empty road networks
- Invalid or non-finite geometry
- Disconnected components
- Conflicting direction tags
- Missing lane counts
- Failed coordinate transformations
- Features excluded from conversion
- Values inferred during normalization

Blocking conditions include no usable roads, unusable geometry, and failed
coordinate transformation. Missing lane counts are normally warnings because
Stage 2 can apply explicit inference rules.

The versioned configuration currently allows missing-lane-count inference and
defines a default vehicle lane width of `3.5` metres. These values are intended
for later lane generation rather than Stage 1A.

## 12. What Stage 1 does not create

Stage 1 does not yet create:

- Lanelet IDs
- Individual lane centerlines
- Left and right lane boundaries
- Lanelet polygons
- Predecessor and successor lanelet links
- Adjacent-lane relationships
- Intersection lane connectors
- Lanelet2 regulatory elements

That is the boundary between Stage 1 and Stage 2:

```text
Stage 1 output:
This directed road segment goes from A to B, comes from OSM way 10,
has two lanes, is seven metres wide, and is approximately 15.7 metres long.

Stage 2 output:
This road segment produces these individual lanelets, with these centerlines,
boundaries, travel directions, neighbors, and intersection connections.
```

Stage 2 is expected to use the normalized graph for road topology and the
original OSM file for source evidence, especially relation information. The
GeoPackage remains an inspection view rather than an input to Lanelet2
generation.
