# A road running straight through a junction still crosses it

## Symptom

Keith, with two screenshots: "i have selected 334662874 and from the image you can see it
says i can drive to 182502377, but in the route builder, it says no access between these 2
lanes." Then: "it should be reachable anyways, you said it was reachable and its physically
possible, so is there a bug in the route planner?"

There was. The lane was reachable, the drive was there, and the converter built it — the
page refused it and said nothing about why.

```
 node 1239566959 · mosque · 3 arms · left-hand traffic · every figure re-derived by script
 + = left turn · − = right turn · indices centre-out, idx0 hugs the centreline

                       way 859432210, 2 lanes                     ARM 3
                    idx0/1 forward, idx1/2 forward             (starts here,
                              │      │                          0 exits back)
                              ▼      ▼
 ══════════════════════════════════════════════════════════════════════ KERB ══

   way 182502377                   ┊ ← 10.00 m →  ┊                way 182502377
   20eb81c1fa88e980   ──────────►  ┊              ┊  ──────────►   1d1a46ad99d21ea3
   idx0/1 backward                 ┊              ┊                idx0/1 backward
   heading out 6.6°                ┊   junction   ┊                heading in 6.6°
                                   ┊   interior   ┊
 ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┊  (setback,   ┊ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─
                                   ┊  5 m each    ┊
   way 182502377                   ┊   side)      ┊                way 182502377
   ca3994b0061e7e4a   ◄──────────  ┊              ┊  ◄──────────   4fc809a668d18410
   idx0/1 forward                  ┊              ┊                idx0/1 forward

 ══════════════════════════════════════════════════════════════ CENTRELINE ══

   bend across the join: 0.0° — the road does not turn here at all
   connector between 20eb81c1 and 1d1a46ad: NONE. Nothing turns; the road goes on.

   ✗ BEFORE: the page saw no connector, called it a plain join, allowed MAX_JOIN_M
             (5 m), measured 10.00 m and threw. 46,570 of the 60,235 destinations it
             had just painted blue could not be drawn.
   ✓ AFTER:  `junction_crossings` names the pair, the page allows MAX_CROSSING_M
             (20 m), and the drive Keith asked for builds: 945 m over 16 lanes.
```

## Fundamental cause

`b1ab34d` gave junctions an interior by cutting every lane back from the node — half the
widest carriageway plus a corner allowance. That is what a turn needs in order to have
somewhere to go, and it applies to *every* lane at the node, including the ones that are
not turning.

So a road that runs straight on across a junction was parted too: 10.00 m at this node,
5 m of setback from each side. Topologically nothing happens there — lane names lane in
`exit_lanes`, no connector is generated, because no movement is being asserted. Both sides
of the join carry the same OSM way id, and the bend is 0.0°. Nothing about the *step* says
"junction" except the node it happens at.

`ego_route.route_polyline` was taught to look at the node. `web/src/route/geometry.ts` was
not — mirroring it was left undone when that merge landed — and it still decided `crossing`
from connector presence alone. Two implementations of one rule, and only one of them was
updated.

The deeper fault is that there were two at all. The geometry has to be written twice,
because the browser cannot run Python; **which joins cross a junction does not** — it is a
property of the map, not of the drive, and it can travel in the payload. The browser could
not have reproduced it anyway: 21 of `junction-1`'s 26 cases turn on `source_edge`, which
no lane entry carries.

And the refusal was silent. `refresh()` called `routeGeometry` before writing any panel
text, with no `try`/`catch` anywhere in `main.ts`, so the throw escaped the click handler
and left the previous message, the previous button state and the previous drawing in place.
A refusal read as whatever the page last said.

## Fix

1. **`ego_route.junction_crossings(model)`** — the `crossings` set lifted out of
   `route_polyline` to module level, with `_junction_nodes` and `_bend_deg`. One authority,
   unchanged behaviour.
2. **`lane_payload.build_lane_payload`** — ships it as `crossings: [[from, to], …]`. Nothing
   here touches the lane model, so `generation_fingerprint` cannot move.
3. **`web/src/route/geometry.ts`** — `routeGeometry`'s fourth parameter is the crossing
   pairs, not `RouteConnector[]`; its only use of connectors was `from`/`to`. `main.ts`
   passes `payload.crossings` with a fallback to the old connector-derived set, so a page
   generated before this change degrades rather than breaks.
4. **`web/src/route/main.ts`** — `routeGeometry` is wrapped, and a `RouteGeometryError` now
   prints its own sentence ("the route leaves a 10 m gap before lane …") under *"That drive
   cannot be built."* The Add button keys on `geometry` rather than `found`, so a route the
   page cannot draw cannot be added either.

## Verification

Measured on both workspaces, before and after, by flooding the lane graph twice — once over
every edge, once over only the edges the browser would accept — and counting the difference.

| | mosque | junction-1 |
|---|---|---|
| steps with no connector and a gap over `MAX_JOIN_M` | 25 (widest 14.2 m) | 26 (widest 17.3 m) |
| destinations painted blue | 60,235 | 22,217 |
| undrawable, **before** | **46,570 (77.3%)** | **12,167 (54.8%)** |
| undrawable, **after** | **0** | **0** |
| widest step now called a crossing | 17.63 m | — |

Nothing was waved through: the widest step classified as a crossing is 17.63 m, inside
`MAX_CROSSING_M` (20 m), and `test_every_step_too_wide_for_a_plain_join_is_named_as_a_junction`
asserts both directions — every wide step is named, and no named step exceeds the span.

Keith's own pair, end to end in the browser on the regenerated `mosque` page: start on a
backward lane of way `334662874`, end on `bfe61a87f9340033` — **946 m over 16 lanes, 15
junction movements**, drawn. Python re-derives the same pair at **945 m over 16 lanes**; the
1 m is rounding on a 945 m route.

`uv run pytest` 341 passed (up from 326: two new in `test_ego_route.py`, four in the new
`test_lane_payload.py`). `npm run test` 139 passed. `uv run ruff check` clean, `tsc` clean.
From MetaDrive's interpreter, both datasets: `sanity_check PASS`, `draw_map accepted`,
`result OK`; `mosque` worst turn 2.3° per step, 0 steps over 30°.

Regenerating both workspaces moved five files each — the three Stage 6 pages, the conversion
report and the manifest. **The scenario pickles are byte-identical**, which is the right
outcome: this changed what the browser is told, not what the converter builds. Both
`generation_fingerprint`s held (`junction-1` `ce2efbed`, `mosque` `b8ac8a2e`).

## Still open

From a *forward* lane of way `334662874`, `bfe61a87f9340033` is genuinely unreachable — it
is the other carriageway, and the two directions sit 3.5 m apart, about one pixel at the
zoom in the screenshot. The road reads blue while the lane under the click is not. Not a
mapping fault (the network flips direction freely elsewhere) and not addressed here.
