// Where a light is drawn, and how.
//
// The page had no answer to "where on this lane does the light go". It recoloured the whole
// polyline, which says a lane is signalled and never says where the wall lands - and the wall
// is the thing the plan is judged on. `signal_plan.stop_points` puts it at `centerline[-1]`
// and warns that being a metre out is the difference between stopping at the line and
// stopping in the junction.
//
// `lane_payload.build_lane_payload` writes `line` as that same centreline transformed
// vertex-for-vertex, with no decimation and no reordering, so the last vertex here *is* the
// point MetaDrive builds the wall at rather than an approximation of it.
// `web/test/signal/stop-marker.test.ts` pins the end it takes, because a lane read backwards
// is the one error that would still look plausible on screen.

import type { SignalLane } from "./types.js";

/** The larger radius marks the group being edited, matching the lane's heavier weight. */
export const STOP_RADIUS_ACTIVE = 7;
export const STOP_RADIUS_OTHER = 5;

/** The downstream end of the lane: where the traffic it releases is stopped. */
export function stopPoint(lane: Pick<SignalLane, "line">): [number, number] | null {
  const last = lane.line[lane.line.length - 1];
  return last ? [last[0], last[1]] : null;
}

/** Filled, so it reads as a lamp rather than another piece of line, inside a white ring so it
 *  stays visible sitting on a lane drawn in the same colour. */
export function stopMarkerStyle(colour: string, isActive: boolean): Record<string, unknown> {
  return {
    radius: isActive ? STOP_RADIUS_ACTIVE : STOP_RADIUS_OTHER,
    color: "#ffffff",
    weight: 2,
    opacity: 1,
    fillColor: colour,
    fillOpacity: 1,
  };
}
