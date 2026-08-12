# Junctions had no interior, so the turns had nowhere to go

## Symptom

Keith: "the junctions are also very problematic, lanes extend out into junctions, and the
routing is extremely weird."

All three were one defect. Measured on `junction-1` before the change:

- 281 of 285 lane centrelines were two-point straight lines running node to node, and the node
  is the middle of the intersection.
- Active connectors were **1.7–6.0 m long, median 3.0 m**, with 3 or 5 points each.
- 55% of lane-to-connector joins turned more than 20°, 19% more than 90°, worst **179.3°**.
- In the written dataset the turns were absent entirely: only `LANE_SURFACE_STREET`,
  `ROAD_EDGE_BOUNDARY` and `ROAD_LINE_BROKEN_SINGLE_WHITE` features existed.

```
 node 1226982521 · 4 arms, every approach idx0/1 · left-hand traffic
 + = left turn · − = right turn · indices centre-out, idx0 hugs the centreline
 every figure re-derived from the lane model by script, before and after

 BEFORE                                      AFTER
 ─────────────────────────────────           ─────────────────────────────────

  ┌ ─ junction interior, ~11 m ─ ┐            ┌ ─ junction interior, ~11 m ─ ┐
  │                              │            │                              │
  │      ╔══════════════╗        │            │   ╲          │          ╱    │
 ─┼─ 52a2899a ────────► ║        │           ─┼─ 52a2899a ──►╲   ╱─────►      │
  │      ║ all 4 approaches and  │            │      ╲        ╲ ╱        ╱    │
 ─┼─ 5587a011 ────────► ║        │           ─┼─ 5587a011 ───► ╳ ◄────────    │
  │      ║ all 4 exits stop      │            │      ╱        ╱ ╲        ╲    │
 ─┼─ 7ecc28d2 ────────► ║        │           ─┼─ 7ecc28d2 ──►╱   ╲─────►      │
  │      ║ inside this box       │            │   ╱          │          ╲    │
 ─┼─ b9f571a6 ────────► ║        │           ─┼─ b9f571a6 ──►             ►   │
  │      ╚══════════════╝        │            │                              │
  └ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┘            └ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┘
     lane-end cloud 3.5 × 3.5 m                  lane ends 9.8 × 10.2 m apart
     = one lane width, all piled on the          = they stop at the junction edge,
       node at the centre                          and the turns cross between them

  the seven turns at this node, measured:

  connector   movement      BEFORE                         AFTER
  0aa81f1c    through +7.6°  1.8 m,  5 pts, in 97.6°       9.7 m, 22 pts, in 1.6° out 1.2°
  4c146380    right  −87.3°  2.8 m,  5 pts, in 81.8°      10.2 m, 32 pts, in 1.8° out 2.1°
  7b6e0e62    left   +87.3°  2.8 m,  5 pts, in 98.2°       5.0 m, 16 pts, in 4.4° out 3.5°
  87d23c6f    right  −85.0°  1.7 m,  5 pts, out 90.0°      9.3 m, 29 pts, in 1.4° out 2.7°
  e17c5a20    right  −94.3°  2.9 m,  5 pts, in 82.0°      10.0 m, 32 pts, in 2.3° out 2.1°
  f00364cb    left   +94.3°  2.9 m,  5 pts, in 98.0°       4.4 m, 15 pts, in 4.2° out 5.3°
  fb100d3a    left   +93.3°  1.8 m,  5 pts, in 176.7°      6.2 m, 20 pts, in 4.7° out 2.0°

  ✗ BEFORE: a 93° LEFT TURN STORED AS A 1.8 m STRAIGHT LINE POINTING BACKWARDS ✗
```

## Fundamental cause

OSM puts one node at the centre of an intersection and every way runs to it. Lane geometry was
generated over the whole graph edge, so every lane ended in the middle of the junction — and so
did every other lane at that node. There was no junction interior for a turn to cross.

`topology.connector_curve` then built a quadratic Bézier from `incoming.coords[-1]` to
`outgoing.coords[0]` with the junction node as its only control point. Given lanes that all end
on that node, start, end and control were within one lane width of each other and the curve
degenerated. What survived was the lateral offset between two overlapping lane centrelines,
pointing wherever that offset happened to point — hence a left turn whose geometry leaves the
approach at 176.7°.

The arithmetic was never wrong. It was asked to interpolate three points that were the same
point, because the road model had no concept of where a junction begins.

Separately, `conversion._lane_neighbours` resolved every connector reference to the lane beyond
it, so whatever geometry the turn had was discarded before the dataset was written. MetaDrive
builds its road network only from lane features (`ScenarioBlock._sample_topology`), so a turn
that is not a feature is not road: no surface to localise on, nothing to paint, and a hole
exactly where the ego drives. Waymo's own data, in `metadrive/assets/waymo/`, writes the turn as
an ordinary `LANE_SURFACE_STREET` — one of them turns 181.6° over 26.6 m with 55 points — and
chains `entry_lanes`/`exit_lanes` through it with a 0.000 m gap at every join.

## Fix

Four changes, all keeping lane and connector ids — and therefore the review — untouched.

1. **`generation._node_setbacks` / `_trimmed_edge`** — cut every lane back before the junction
   node by half the widest carriageway meeting there plus a corner allowance, so a junction
   interior exists. A node counts as a junction on having more than two distinct neighbours, not
   on edge count: a two-way road already puts four directed edges on every node along it. Short
   links clamp to `MIN_TRIMMED_LANE_M` and are counted in `trim_clamped_edges`. The same rule
   fillets sharp bends at through nodes, where two ways meet at an angle and the real road
   curves.
2. **`topology.connector_curve`** — a cubic Bézier with its control points on the two tangents
   at a third of the chord, so the curve leaves the approach in the approach's direction and
   arrives in the exit's by construction. Sampled at 0.5 m, matching Waymo, but never finer than
   `CONNECTOR_MIN_SAMPLE_METRES` — forcing eight segments onto a 0.1 m curve puts points 12 mm
   apart, where coordinate noise dominates the direction and the curve wanders hundreds of
   degrees.
3. **`conversion._exported_links` / `_connector_feature` / `_bridge_feature`** — write the turns
   into the dataset as lane features and chain through them. Movements the model records as
   plain continuations get a bridging junction lane when trimming has parted their ends.
   Guarded by `_gap_ahead`: a gap measured *along* the approach, because trimming both ends
   independently can leave the next lane starting behind this one, and bridging an overlap
   produces a cusp.
4. **`ego_route.route_polyline`** — widen what counts as crossing a junction. It decided that
   from connector presence alone, so a road running *straight through* a junction was a plain
   continuation and got `MAX_JOIN_M` (5 m) rather than `MAX_CROSSING_M` (20 m). That was right
   while lanes met at the node and wrong the moment they were trimmed apart: 26 of `junction-1`'s
   211 continuations now open past 5 m, the widest at 17.3 m, and every route through one was
   refused. A join is now also a crossing where the node is a junction, or where the bend
   exceeds `BEND_FILLET_MIN_DEGREES` — the two cases where `_node_setbacks` parts the lanes.

   Told apart by what generation did, not by the gap. A threshold that promoted any join wide
   enough would swallow the hole the guard exists to catch. `BEND_FILLET_MIN_DEGREES` moved to
   `topology` so generation and the route builder read one number rather than two that drift.

   **The rest of what was written here is dropped.** The original change also stopped
   `route_polyline` splicing the collinear marker — but `ff0e53b` had already found and fixed
   that, in far more depth, in
   `docs/ai-action-logs/2026-08-12-02:05:13-the-ego-spun-at-every-junction-because-a-marker-was-driven-as-a-road.md`.
   Their version is kept.

Not changed, after trying it and reverting: normalising `signed_turn_angle` at the ±180 branch
cut. The sign there is a floating-point accident, but it reaches U-turn target selection, so
pinning it to either value re-identifies findings and invalidates settled decisions. Documented
in place as a known instability rather than silently altered.

## Verification

Measured on `main` at `fcb000e`, before and after, with the same script pointed at
`metadrive/assets/waymo/` for the third column. The workspace was regenerated through Stages 2,
4, 5 and 6, with `--routes` and `--signals`.

| measure | before | after | Waymo |
|---|---|---|---|
| lane→exit gap, max | 5.578 m | **0.693 m** | 0.000 m |
| join angle, median | 5.70° | **0.77°** | 0.03° |
| join angle, p99 | 94.56° | **18.76°** | 0.91° |
| join angle, max | 98.79° | **25.18°** | 2.12° |
| joins over 5° | 53.4% | **9.7%** | 0.0% |
| joins over 20° | 19.0% | **1.1%** | 0.0% |
| junction turns as lane features | 0 | **151** | all |
| lane features total | 285 | **436** | 182 |
| ego yaw, worst step | 8.51° | 10.25° | — |

The ego-yaw row is the one that does not improve, and it should not: `ff0e53b` had already
fixed the drive line, so the recorded track was smooth before this and is smooth after. Neither
run has a single step above 15°. What changed is that the car now has road underneath it.

**The review survived untouched.** All 140 finding ids, all 116 connector ids and all 285 lane
ids are unchanged, the statuses are identical (80 active / 31 review_required / 5 forbidden),
and `generation_fingerprint` is unchanged — it is built from the generator version, schema
version and input checksums, none of which geometry touches. Stage 4 was re-run against the
submission recorded in `review/applied-decisions.json` with **no edits at all** and passed;
Stage 5 passed. Only `routes.json` and `signals.json` need re-exporting, because their identity
blocks carry `reviewed_lane_model_sha256` and the reviewed model's bytes change with the
geometry.

`uv run pytest` 310 passed, including the 1500-route sweep over the real map that asserts no
route turns more than 30° at a vertex. `uv run ruff check` clean. From MetaDrive's own
interpreter: `sanity_check PASS`, `draw_map` accepted 921 features, and `tools/drive.py` reaches
`arrive_dest=True` under replay, stopping once for 9 s at a red light.

Not fixed, and not made worse: `--agent-policy idm` still ends in `out_of_road`. Before this
change it reached 49.8% and −4.03 m lateral; after, 49.4% and −4.25 m. Same failure in the same
place — the IDM's lateral controller, which is a separate defect from the map.

Still outstanding: 5 lane-to-lane joins remain between 20° and 25.18°, at nodes below the
bend-fillet threshold or where the trim clamped. Not diagnosed.
