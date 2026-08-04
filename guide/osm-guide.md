# OpenStreetMap Guide: Nodes, Ways, Relations, and Tags

This guide explains the native data model used by an OpenStreetMap (`.osm`)
XML file. It focuses on the three OSM element types—**nodes**, **ways**, and
**relations**—and the parameters that give each element its identity, geometry,
and meaning.

For this project, the preserved source file is the authoritative record of OSM
elements and their original tags. Derived GraphML and GeoPackage files are
useful for processing and inspection, but they do not replace the source OSM.

## 1. The OSM data model at a glance

```text
Node
  one latitude/longitude point
       |
       | referenced by
       v
Way
  ordered list of node references
       |
       | referenced by
       v
Relation
  ordered list of node, way, or relation members with roles

Tags (`k` + `v`) can be attached to any of the three element types.
```

| Element | Geometry or structure | Typical road-network use |
|---|---|---|
| Node | One geographic point | Shape point, junction, signal, crossing, barrier |
| Way | Ordered node references | Road centerline, stop line, area boundary |
| Relation | Members plus member roles | Turn restriction, route, multipolygon |

An important distinction:

- **XML attributes** such as `id`, `lat`, `lon`, and `ref` define the OSM
  element or its references.
- **Tags** such as `highway=residential` describe what the mapped feature means.

## 2. Nodes

A node represents one WGS84 geographic coordinate. It may be only a geometry
point used to shape a way, or it may represent a feature in its own right.

```xml
<node id="102" lat="3.1501000" lon="101.7001000"
      version="4" changeset="987654" timestamp="2026-08-01T10:30:00Z">
  <tag k="highway" v="traffic_signals" />
  <tag k="traffic_signals:direction" v="forward" />
</node>
```

### Node attributes

| Parameter | Required in an OSM data file | Meaning |
|---|---:|---|
| `id` | Yes | Signed 64-bit OSM element identifier. Ways and relations refer to this value. |
| `lat` | Yes | Latitude in decimal WGS84 degrees. Valid geographic values run from south to north. |
| `lon` | Yes | Longitude in decimal WGS84 degrees. Valid geographic values run from west to east. |
| `version` | Common metadata | Revision number of this node. It increases when the element is edited. |
| `changeset` | Common metadata | ID of the changeset that produced this revision. |
| `timestamp` | Common metadata | UTC time at which this revision was saved. |
| `user` | Optional metadata | Display name of the contributing OSM user. |
| `uid` | Optional metadata | Numeric ID of the contributing OSM user. |
| `visible` | Optional metadata | Whether the element is visible in historical/API representations. |

The exact metadata included depends on how the file was exported. Conversion
logic should use `id`, `lat`, `lon`, and tags for map semantics, rather than
depending on contributor metadata being present.

### Driving-relevant node tags

OSM does not define a closed list of allowed tags. Any string key and value can
be attached to a node, and the community can introduce new tagging conventions.
Therefore, no static document can list literally every possible node tag. The
tables below cover the established tag families that can affect road selection,
legal vehicle movement, lane connectivity, controls, or driving-map review.
Unknown tags must still be preserved in the source evidence.

#### Road controls and road geometry

| Tag | Common values | Meaning |
|---|---|---|
| `highway=traffic_signals` | Fixed value | Traffic-control signal at the node |
| `highway=stop` | Fixed value | Stop sign or stopping control |
| `highway=give_way` | Fixed value | Give-way or yield control |
| `highway=crossing` | Fixed value | Pedestrian or cyclist road-crossing point |
| `highway=mini_roundabout` | Fixed value | Traversable central island with roundabout priority |
| `highway=motorway_junction` | Fixed value | Motorway junction or named/numbered exit point |
| `highway=turning_circle` | Fixed value | Enlarged terminal area where vehicles can turn |
| `highway=turning_loop` | Fixed value | Loop-shaped turning facility, normally with a central island |
| `highway=passing_place` | Fixed value | Widened point on a narrow road for vehicles to pass |
| `highway=speed_camera` | Fixed value | Fixed speed-enforcement camera location |
| `highway=toll_gantry` | Fixed value | Overhead toll collection or detection point |
| `highway=street_lamp` | Fixed value | Street-light position; useful context but not road topology |
| `highway=emergency_bay` | Fixed value | Roadside refuge intended for emergency stopping |
| `highway=milestone` | Fixed value | Distance marker beside a road |

`highway=*` is a tag expression: the key is `highway`, and the text after `=`
is its value. A node normally has only one `highway` value, with supplementary
properties expressed through additional keys.

#### Direction and control qualifiers

| Tag | Common values | Meaning |
|---|---|---|
| `direction` | `forward`, `backward`, compass bearing | Direction in which a point feature applies or faces; interpretation depends on the main feature tag |
| `traffic_signals:direction` | `forward`, `backward` | Signal applies along or opposite the referencing way's node order |
| `traffic_signals` | `signal`, other documented signal configurations | Further classification of a signalized node |
| `button_operated` | `yes`, `no` | Crossing signals can be requested with a push button |
| `flashing_lights` | `yes`, `no` | Control or crossing has flashing warning lights |
| `stop` | `all`, `minor` | At a stop node, indicates whether all approaches or only the minor road stop |
| `give_way` | `all`, `minor` | At a give-way node, indicates the affected approaches where mapped |

Directional tags are meaningful only when the node belongs to a way whose node
order establishes `forward` and `backward`. At complex junctions, separate
control nodes on the affected approaches are less ambiguous than one shared
node with an assumed direction.

#### Crossings and accessibility

These keys normally supplement `highway=crossing`:

| Tag | Common values | Meaning |
|---|---|---|
| `crossing` | `marked`, `unmarked`, `traffic_signals`, `uncontrolled`, `no` | Broad crossing type or control convention |
| `crossing:markings` | `yes`, `no`, `zebra`, and regional marking types | Road-surface crossing markings |
| `crossing:signals` | `yes`, `no` | Whether traffic signals control the crossing |
| `crossing:island` | `yes`, `no` | Presence of a pedestrian refuge island |
| `crossing:barrier` | `yes`, `no` | Presence of a crossing barrier |
| `bicycle` | `yes`, `no`, `designated`, `dismount` | Bicycle access or status at the crossing |
| `foot` | `yes`, `no`, `designated` | Pedestrian access or status |
| `horse` | `yes`, `no`, `designated` | Equestrian access or status |
| `kerb` | `flush`, `lowered`, `raised`, `rolled`, `no` | Kerb treatment at the crossing point |
| `tactile_paving` | `yes`, `no`, `incorrect` | Presence and status of tactile paving |
| `lit` | `yes`, `no` | Whether the feature is lit |
| `supervised` | `yes`, `no` | Whether a crossing is supervised at relevant times |

Some datasets use older or region-specific crossing values. Preserve them even
if the converter normalizes only a subset.

#### Physical barriers and access-control points

| Tag | Common values | Meaning |
|---|---|---|
| `barrier` | `gate`, `lift_gate`, `swing_gate`, `sliding_gate` | Movable gate across a route |
| `barrier` | `bollard`, `block`, `chain`, `jersey_barrier` | Physical obstruction or vehicle filter |
| `barrier` | `cycle_barrier`, `kissing_gate`, `stile` | Filter primarily affecting non-motorized users |
| `barrier` | `bus_trap` | Barrier designed to permit buses but block ordinary vehicles |
| `barrier` | `height_restrictor` | Physical overhead height restriction |
| `barrier` | `toll_booth`, `border_control`, `sally_port` | Controlled passage point |
| `access` | `yes`, `no`, `private`, `permissive`, `destination`, `customers` | Default access rule for all transport modes |
| `vehicle` | Same access vocabulary | Access rule for vehicles generally |
| `motor_vehicle` | Same access vocabulary | Access rule for motor vehicles |
| `motorcar` | Same access vocabulary | Access rule specifically for cars |
| `hgv` | Same access vocabulary | Access rule for heavy goods vehicles |
| `bus` | Same access vocabulary | Access rule for buses |
| `psv` | Same access vocabulary | Access rule for public-service vehicles |
| `bicycle` | Same access vocabulary | Access rule for bicycles |
| `foot` | Same access vocabulary | Access rule for pedestrians |
| `emergency` | Same access vocabulary | Access rule for emergency vehicles |
| `locked` | `yes`, `no` | Whether a gate or barrier is normally locked |
| `opening_hours` | OSM opening-hours expression | Times during which access or operation applies |
| `fee` | `yes`, `no`, or qualified value | Whether passage requires payment |

Mode-specific access tags override broader tags for that mode. For example,
`access=no` with `bus=yes` describes a point closed generally but passable by
buses.

#### Physical clearance and legal limits

These keys are especially important on a barrier, restriction point, or portal:

| Tag | Example values | Meaning |
|---|---|---|
| `maxheight` | `4.2`, `13'6\"` | Maximum permitted or physical vehicle height |
| `maxwidth` | `2.5` | Maximum permitted or physical vehicle width |
| `maxlength` | `12` | Maximum permitted vehicle length |
| `maxweight` | `7.5` | Maximum permitted gross vehicle weight |
| `maxaxleload` | `5` | Maximum permitted axle load |
| `maxspeed` | `30`, `20 mph` | Speed limit beginning or enforced at the point, when point mapping is appropriate |
| `minspeed` | `30` | Minimum legal speed where applicable |
| `traffic_sign` | `MY:R1`, `maxspeed`, or a direction-qualified value | Sign present at the node; exact syntax follows the traffic-sign convention |

Bare numeric values use the default unit defined by the key and local OSM
convention. Explicit units must be parsed rather than discarded.

#### Traffic-calming features

| Tag | Common values | Meaning |
|---|---|---|
| `traffic_calming` | `bump` | Short raised bump |
| `traffic_calming` | `hump` | Longer rounded hump |
| `traffic_calming` | `table` | Flat-topped raised table |
| `traffic_calming` | `cushion` | Speed cushion designed around vehicle wheel tracks |
| `traffic_calming` | `chicane` | Lateral deflection of the vehicle path |
| `traffic_calming` | `choker` | Road narrowing at one point |
| `traffic_calming` | `island` | Traffic-calming island |
| `traffic_calming` | `rumble_strip` | Textured strip producing noise or vibration |
| `traffic_calming` | `dip` | Deliberate depression used to reduce speed |
| `traffic_calming` | `yes` | Unspecified traffic-calming feature; review is needed before deriving geometry |

Supplementary keys may include `maxspeed`, `width`, `direction`, and `surface`.

#### Railway interactions

| Tag | Common values | Meaning |
|---|---|---|
| `railway=level_crossing` | Fixed value | Road and railway cross at the same level |
| `railway=crossing` | Fixed value | Path or non-road route crosses a railway at grade |
| `crossing:barrier` | `yes`, `no`, `half`, `full` | Barrier provision at the rail crossing |
| `crossing:light` | `yes`, `no` | Warning lights are present |
| `crossing:bell` | `yes`, `no` | Audible warning is present |
| `crossing:on_demand` | `yes`, `no` | Crossing operates on demand |
| `railway=tram_stop` | Fixed value | Tram stop position |
| `railway=halt` | Fixed value | Small railway stopping point |

Railway tags can be relevant to driving simulation even when rail objects are
not converted into road lanelets, because the crossing changes road controls
and possible vehicle motion.

#### Public transport, services, and road access points

| Tag | Common values | Meaning |
|---|---|---|
| `highway=bus_stop` | Fixed value | Bus stopping position or roadside bus-stop feature |
| `public_transport=stop_position` | Fixed value | Position on the route where a vehicle stops |
| `public_transport=platform` | Fixed value | Passenger boarding/alighting location when mapped as a node |
| `amenity=parking_entrance` | Fixed value | Entrance or exit of a parking facility |
| `amenity=fuel` | Fixed value | Fuel station represented as a point |
| `amenity=charging_station` | Fixed value | Vehicle charging facility represented as a point |
| `highway=services` | Fixed value | Motorway or major-road service area represented as a point |
| `highway=rest_area` | Fixed value | Roadside rest area represented as a point |
| `emergency=phone` | Fixed value | Emergency telephone |
| `emergency=fire_hydrant` | Fixed value | Fire hydrant; useful roadside context, not road connectivity |

These features may be useful as destinations or context without changing the
drivable graph directly.

#### Identification, provenance, and review tags

The following generic keys can appear on nodes as well as ways and relations:

| Tag | Example values | Meaning |
|---|---|---|
| `name` | `Jalan Example Junction` | Human-readable feature name |
| `ref` | `Exit 210`, `A12` | Feature reference; unrelated to XML member/node `ref` attributes |
| `operator` | Organization name | Operator of a signal, gate, facility, or control |
| `network` | Network identifier | Network to which the feature belongs |
| `source` | `survey`, imagery name | Declared information source |
| `source:position` | Survey or imagery reference | Source used specifically for the position |
| `survey:date` | `2026-08-01` | Date on which the feature was surveyed |
| `start_date` | Year or date | Date the feature began operating or existing |
| `description` | Free text | Mapper-facing or user-facing description |
| `note` | Free text | Information for other mappers; not a traffic rule |
| `fixme` | Free text | Known uncertainty or requested mapping follow-up |
| `check_date` | `2026-08-01` | Date the feature or property was last checked |

This catalogue is intentionally comprehensive for driving-map work, but it is
not an allowlist. A node can also represent any other point feature—for example
a shop, tree, address, or utility asset—with tags unrelated to road conversion.
The current meanings and usage frequency of an unfamiliar key should be checked
in the OSM Wiki and Taginfo before conversion behavior is added.

### Geometry and identity rules

- Two nodes at the same coordinates remain two different objects if their IDs
  differ.
- A node shared by two ways expresses exact topological connectivity between
  those ways. Merely placing separate nodes close together does not.
- A node's tags apply to that point, not automatically to every way that uses
  it.
- The order in which a way references nodes supplies direction; a node by
  itself has no forward or backward direction.

## 3. Ways

A way is an **ordered** list of node references. Consecutive references form
line segments. The same structure can describe an open line or, when the first
and last node references are the same, a closed ring.

```xml
<way id="501" version="7" changeset="987654">
  <nd ref="101" />
  <nd ref="102" />
  <nd ref="103" />

  <tag k="highway" v="primary" />
  <tag k="name" v="Example Road" />
  <tag k="oneway" v="yes" />
  <tag k="lanes" v="2" />
  <tag k="turn:lanes" v="through|right" />
  <tag k="maxspeed" v="50" />
  <tag k="width" v="7" />
  <tag k="access" v="yes" />
</way>
```

### Way attributes and child elements

| Parameter | Where it appears | Meaning |
|---|---|---|
| `id` | `<way id="…">` | Unique identifier for the way |
| `version` | `<way>` metadata | Revision number |
| `changeset` | `<way>` metadata | Changeset that produced the revision |
| `timestamp` | `<way>` metadata | UTC revision time |
| `user`, `uid`, `visible` | `<way>` metadata | Optional contributor/history metadata |
| `ref` | `<nd ref="…" />` | ID of a node used by the way |
| `k`, `v` | `<tag k="…" v="…" />` | Tag key and string value |

The `<nd>` sequence above means:

```text
node 101 -> node 102 -> node 103
```

Changing that order changes the way's forward direction. This affects tags
such as `oneway`, `lanes:forward`, `turn:lanes`, and directional traffic
controls.

### Important road-way tags

| Tag | Example values | What it controls or communicates |
|---|---|---|
| `highway` | `motorway`, `primary`, `residential`, `service` | Road classification and basic feature type |
| `name` | `Example Road` | Human-readable road name |
| `ref` | `E35` | Signed or administrative route reference; this is a tag, not an `<nd ref>` |
| `oneway` | `yes`, `no`, `-1` | Whether travel follows the node order, is bidirectional, or runs opposite the node order |
| `lanes` | `2` | Total marked motor-vehicle lanes, stored as a string |
| `lanes:forward` | `2` | Lanes travelling in the way's forward direction |
| `lanes:backward` | `1` | Lanes travelling opposite the way's forward direction |
| `turn:lanes` | `left|through;right` | Lane-by-lane turn indications, separated by `|`; multiple indications use `;` |
| `turn:lanes:forward` | `through|right` | Turn indications only for forward lanes |
| `turn:lanes:backward` | `left|through` | Turn indications only for backward lanes |
| `maxspeed` | `50`, `30 mph` | Legal speed limit; a bare number normally uses the local default unit |
| `width` | `7`, `7 m` | Approximate total feature width, not necessarily an individual-lane width |
| `access` | `yes`, `no`, `private`, `destination` | General access rule |
| `motor_vehicle` | `yes`, `no`, `private` | Access rule specifically for motor vehicles |
| `junction` | `roundabout` | Junction behavior; roundabouts usually imply one-way travel |
| `bridge` | `yes` | The way is carried by a bridge |
| `tunnel` | `yes` | The way runs through a tunnel |
| `layer` | `1`, `0`, `-1` | Relative vertical ordering; it does not by itself create or remove connectivity |
| `surface` | `asphalt`, `concrete`, `gravel` | Physical surface material |

All tag values are strings. Software must parse numeric-looking values and
units deliberately rather than assuming an XML number type.

### Open and closed ways

- An **open way** has different first and last node references. Roads are
  commonly represented this way.
- A **closed way** repeats its first node reference at the end. Depending on its
  tags, it may represent an area boundary or a closed linear feature.
- Being closed does not automatically mean that a way is an area. Tags and OSM
  conventions determine its interpretation.

### Connectivity rules

- Consecutive nodes create the way's geometry.
- Ways connect topologically when they share a node at the connection point.
- A line crossing in the drawing is not necessarily an intersection. Separate
  node IDs, together with bridge/tunnel/layer information, often describe a
  grade-separated crossing.
- One physical road may be split into several ways whenever tags, direction,
  lane counts, speed limits, or other properties change.

## 4. Relations

A relation groups nodes, ways, and even other relations. Each member has a
role whose meaning is defined by the relation's `type` and tagging convention.
Member order can also be meaningful.

```xml
<relation id="9001" version="3" changeset="987654">
  <member type="way" ref="500" role="from" />
  <member type="node" ref="102" role="via" />
  <member type="way" ref="501" role="to" />

  <tag k="type" v="restriction" />
  <tag k="restriction" v="no_right_turn" />
</relation>
```

### Relation attributes and member parameters

| Parameter | Where it appears | Meaning |
|---|---|---|
| `id` | `<relation id="…">` | Unique identifier for the relation |
| `version` | `<relation>` metadata | Revision number |
| `changeset` | `<relation>` metadata | Changeset that produced the revision |
| `timestamp` | `<relation>` metadata | UTC revision time |
| `user`, `uid`, `visible` | `<relation>` metadata | Optional contributor/history metadata |
| `type` | `<member type="…">` | Member element type: `node`, `way`, or `relation` |
| `ref` | `<member ref="…">` | ID of the referenced member within that element type |
| `role` | `<member role="…">` | The member's function in this relation; it may be an empty string |
| `k`, `v` | `<tag k="…" v="…" />` | Relation tag key and string value |

`type="way" ref="501"` means OSM way 501. IDs are scoped by element type, so
a node and a way may legitimately have the same numeric ID.

### Common relation types

| Relation tag | Typical member roles | Purpose |
|---|---|---|
| `type=restriction` | `from`, `via`, `to` | Prohibits or exclusively permits a driving maneuver |
| `type=route` | Often empty, `forward`, `backward`, `stop`, `platform` | Groups a signed, road, bicycle, or public-transport route |
| `type=multipolygon` | `outer`, `inner` | Builds an area from one or more boundary ways |
| `type=boundary` | `outer`, `inner`, sometimes administrative roles | Represents an administrative or other boundary |

Roles are relation-specific. For example, `outer` is meaningful in a
multipolygon but is not a substitute for `from` in a turn restriction.

### Turn-restriction relations

A standard road turn restriction contains:

| Member or tag | Expected meaning |
|---|---|
| `from` way | Road segment on which the maneuver begins |
| `via` node | Junction node through which a simple maneuver passes |
| `via` way or ordered ways | Intermediate road segment or chain for a more complex maneuver |
| `to` way | Road segment on which the maneuver ends |
| `type=restriction` | Declares the relation as a restriction |
| `restriction=no_*` | Forbids the named maneuver, such as `no_left_turn` or `no_u_turn` |
| `restriction=only_*` | Allows only the named exit, such as `only_straight_on` |
| `except` | Lists transport modes exempt from the restriction |

For a via-node restriction, the `via` node should connect the `from` and `to`
ways. For a via-way restriction, the member sequence and topology must form the
legal traversal from `from`, through every `via`, to `to`. Missing members or a
broken chain make the source relation incomplete and require review; geometry
alone is not enough to reconstruct the intended legal rule safely.

## 5. Tags

A tag is a string key-value pair attached as a child of a node, way, or
relation:

```xml
<tag k="highway" v="residential" />
```

| Parameter | Meaning |
|---|---|
| `k` | Key that names the property |
| `v` | String value assigned to that property |

### Difference between `k` and `v`

`k` means **key**. It is the name of the question or property being recorded.
`v` means **value**. It is the answer stored for that property.

```xml
<tag k="highway" v="traffic_signals" />
```

Read this as:

```text
property: highway
value:    traffic_signals
meaning:  this element is a traffic signal
```

The two parts are not interchangeable:

| XML tag | Key (`k`) asks or identifies | Value (`v`) answers |
|---|---|---|
| `<tag k="highway" v="traffic_signals" />` | What kind of transport feature is this? | It is a traffic signal. |
| `<tag k="highway" v="stop" />` | What kind of transport feature is this? | It is a stop control. |
| `<tag k="lanes" v="2" />` | How many lanes are recorded? | Two lanes. |
| `<tag k="maxspeed" v="50" />` | What is the maximum speed? | `50` in the locally applicable/default unit. |
| `<tag k="oneway" v="yes" />` | Is travel one-way? | Yes. |
| `<tag k="name" v="Example Road" />` | What is the feature's name? | Example Road. |

One OSM element can have many tags because it can have many different keys:

```xml
<way id="501">
  <tag k="highway" v="residential" />
  <tag k="name" v="Example Road" />
  <tag k="lanes" v="2" />
  <tag k="oneway" v="yes" />
</way>
```

Here, `highway`, `name`, `lanes`, and `oneway` are four property names. Their
respective values are `residential`, `Example Road`, `2`, and `yes`.

The `k` and `v` letters are XML attribute names used inside `<tag>`. They are
not separate tags by themselves. Also, every value is stored as text—even
`v="2"`—so conversion software must parse numbers, units, lists, and Boolean-like
values according to the specific key's rules.

Tag keys commonly use colons for a namespace or qualifier:

```text
lanes
lanes:forward
turn:lanes:forward
traffic_signals:direction
```

Semicolons often separate multiple values within one tag, while vertical bars
have a special lane-separator role in lane tags. These separators are tagging
conventions, not general XML syntax, so each key must be interpreted according
to its own schema.

OSM tagging is open-ended. Unknown tags should generally be preserved as source
evidence even when the current converter does not use them.

## 6. Complete road example

```xml
<?xml version="1.0" encoding="UTF-8"?>
<osm version="0.6" generator="example">
  <node id="101" lat="3.1500" lon="101.7000" />
  <node id="102" lat="3.1501" lon="101.7001">
    <tag k="highway" v="traffic_signals" />
  </node>
  <node id="103" lat="3.1502" lon="101.7002" />
  <node id="104" lat="3.1501" lon="101.7003" />

  <way id="500">
    <nd ref="101" />
    <nd ref="102" />
    <tag k="highway" v="residential" />
    <tag k="oneway" v="yes" />
    <tag k="lanes" v="1" />
  </way>

  <way id="501">
    <nd ref="102" />
    <nd ref="103" />
    <tag k="highway" v="residential" />
    <tag k="oneway" v="yes" />
    <tag k="lanes" v="1" />
  </way>

  <way id="502">
    <nd ref="102" />
    <nd ref="104" />
    <tag k="highway" v="residential" />
    <tag k="oneway" v="yes" />
    <tag k="lanes" v="1" />
  </way>

  <relation id="9001">
    <member type="way" ref="500" role="from" />
    <member type="node" ref="102" role="via" />
    <member type="way" ref="502" role="to" />
    <tag k="type" v="restriction" />
    <tag k="restriction" v="no_right_turn" />
  </relation>
</osm>
```

This fragment says:

1. Four nodes supply coordinates, and node `102` is also a traffic signal.
2. Three one-way road ways share node `102`, making it a topological junction.
3. Relation `9001` prohibits travel from way `500` through node `102` onto way
   `502`.
4. The alternative movement from way `500` onto way `501` is not prohibited by
   this relation.

## 7. What the converter should preserve and derive

| Data | Source or derived? | Handling |
|---|---|---|
| Node IDs and coordinates | Source | Preserve as evidence and use for geometry/topology |
| Way node order | Source | Preserve; it defines forward direction |
| Tags | Source | Preserve original strings, then parse only where required |
| Relation members, order, and roles | Source | Preserve; validate referenced members before applying rules |
| Projected metre coordinates | Derived | Generate from latitude/longitude for geometry calculations |
| Directed graph edges | Derived | Generate from way order plus direction/access tags |
| Lane centerlines and boundaries | Derived | Generate later from road and lane evidence |
| Lanelet connectivity | Derived | Generate and validate against source topology and restrictions |

Do not edit a normalized GraphML or GeoPackage to correct an OSM fact. Correct
the source `.osm` data and rerun the source-dependent stages. Use generated
Lanelet2 edits only for one-off geometry corrections that are not source facts.

## 8. Practical inspection checklist

When inspecting an OSM road feature, check in this order:

1. **Node identity and position:** Are referenced node IDs present, and are
   their latitude/longitude values plausible?
2. **Way order:** Does the node sequence follow the expected forward direction?
3. **Topology:** Do roads that should connect share the same junction node?
4. **Road tags:** Are `highway`, access, direction, lane count, turns, speed,
   and width consistent with the mapped road?
5. **Relation completeness:** Are all members present with the correct element
   type, reference, role, and order?
6. **Restriction legality:** Does each `from`/`via`/`to` chain describe a real
   connected maneuver?
7. **Source boundary:** Is a suspected issue genuinely in the source OSM, or
   was it introduced in a derived graph, projected layer, or generated map?

## Further reading

- [Project conversion guide](project-guide.md)
- [Files created by each pipeline step](created-files-from-steps.md)
- [OpenStreetMap elements](https://wiki.openstreetmap.org/wiki/Elements)
- [OSM XML file format](https://wiki.openstreetmap.org/wiki/OSM_XML)
- [Turn-restriction relations](https://wiki.openstreetmap.org/wiki/Relation:restriction)
