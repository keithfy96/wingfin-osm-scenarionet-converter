# Stage 3A Legend: Junction Connectors

A junction connector is a generated Lanelet2 lanelet describing how a vehicle travels from one road lane into another through an intersection.

```text
Incoming road lanelet
        \
         \ generated connector
          \
Outgoing road lanelet
```

## How Connectors Are Generated

### 1. Find intersection nodes

The generator finds graph nodes that have both:

- At least one lanelet ending at the node.
- At least one lanelet beginning at the node.

These become potential junctions. See [`src/osm_scenario/lanelet_generation.py`](../../src/osm_scenario/lanelet_generation.py), where connector generation begins with the intersection of the incoming-node and outgoing-node indexes.

### 2. Build possible incoming-to-outgoing pairs

For every incoming lane, the generator considers every outgoing lane at that node:

```text
Incoming lane A -> outgoing lane X
                -> outgoing lane Y
                -> outgoing lane Z
```

This can generate several connectors from one incoming lane.

### 3. Classify the turn using geometry

The angle between the end of the incoming lane and the beginning of the outgoing lane is classified as:

| Angle | Classification |
| --- | --- |
| Up to 35 degrees | Through |
| More than 35 and less than 70 degrees | Slight left/right |
| 70 to less than 145 degrees | Left/right |
| 145 degrees or more | Reverse |

The sign determines left versus right. See `_movement()` in [`src/osm_scenario/lanelet_generation.py`](../../src/osm_scenario/lanelet_generation.py).

### 4. Apply lane-turn tags

If OSM provides `turn:lanes`, `turn:lanes:forward`, or `turn:lanes:backward`, incompatible movements are discarded.

For example:

```text
turn:lanes=left|through;right
```

This prevents the left-turn lane from receiving a through connector.

If there is no usable turn-lane information, the generator initially assumes every geometrically possible movement is allowed.

### 5. Apply OSM turn restrictions

Node-based restriction relations are checked:

- `no_right_turn` removes the matching connector.
- `no_left_turn` removes the matching connector.
- `only_straight_on` removes every other outgoing connector.

In the current mosque workspace generation, this removed 87 candidate movements.

Restrictions using a `via` way are not currently applied automatically. Seven such restrictions in the current mosque workspace are placed in the correction queue because they require route context.

These counts describe the current mosque input and can change when its source OSM or conversion policy changes.

### 6. Handle continuing road lanes

When the incoming and outgoing segments belong to the same OSM way, the generator prefers to connect equal lane indexes:

```text
Incoming lane 0 -> outgoing lane 0
Incoming lane 1 -> outgoing lane 1
```

This avoids generating unnecessary lane changes along a continuing road. Different OSM ways do not currently receive equivalent lane-to-lane matching.

### 7. Draw the connector

The connector centerline is a five-point quadratic curve:

```text
incoming lane endpoint
        -> shared OSM junction node
        -> outgoing lane endpoint
```

Left and right boundaries are then offset from that curve using the configured default lane width. The result becomes a new Lanelet2 lanelet with:

- `from_lanelet_id`
- `to_lanelet_id`
- Source OSM junction node
- Turn classification
- Generated centerline
- Left and right boundaries

## Why Review Is Necessary

OSM normally describes road topology, not exact lane-level paths through an intersection. Therefore, the connector generator must make several assumptions.

Review is required particularly when:

- One incoming lane has several permitted outgoing movements.
- OSM does not provide usable `turn:lanes*` tags.
- Different OSM ways meet and cannot use same-way lane-index matching.
- A turn angle is close to a classification boundary.
- A restriction uses a `via` way and needs route-level interpretation.

The correction queue records these uncertain cases. It does not mean every listed connector is wrong; it means the source data is insufficient to confirm the generated lane-level movement automatically.
