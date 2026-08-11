// The path a car actually drives along a chosen route.
//
// A port of `ego_route.route_polyline`, and deliberately a port rather than an
// approximation. Two earlier attempts to *estimate* the distance from lane lengths were both
// wrong, in opposite directions: the first left out the first lane and every junction, the
// second counted both lanes of a change in full when together they span one lane's worth of
// road. An estimate has no way to be checked; this can be, and
// `test_the_browser_and_python_agree_on_the_geometry` does check it.
//
// The page draws what this returns, so the line on screen is the line that gets built.

import { crossingKey, lineLength, type RouteGraph } from "./path.js";
import type { RouteConnector } from "./types.js";

/** Matches `ego_route._CHANGE_HALF_SPAN`. */
export const CHANGE_HALF_SPAN = 0.15;

type Point = [number, number];

/** Distance in the payload's own units, for splitting a line proportionally.
 *
 * Degrees, not metres - which is fine because it is only ever used as a ratio along one
 * short line, and a lane spans far too little latitude for the two to differ meaningfully.
 */
function span(a: Point, b: Point): number {
  return Math.hypot(b[0] - a[0], b[1] - a[1]);
}

/** A line split at `at`, a fraction of its length; the head of it or the tail. */
export function cut(line: Point[], keepHead: boolean, at: number): Point[] {
  if (line.length < 2) return line;
  const travelled: number[] = [0];
  for (let i = 1; i < line.length; i += 1) {
    travelled.push(travelled[i - 1]! + span(line[i - 1]!, line[i]!));
  }
  const target = travelled[travelled.length - 1]! * at;
  let index = travelled.findIndex((d) => d >= target);
  index = Math.max(1, Math.min(index === -1 ? line.length - 1 : index, line.length - 1));
  const width = travelled[index]! - travelled[index - 1]!;
  const ratio = width === 0 ? 0 : (target - travelled[index - 1]!) / width;
  const a = line[index - 1]!;
  const b = line[index]!;
  const split: Point = [a[0] + (b[0] - a[0]) * ratio, a[1] + (b[1] - a[1]) * ratio];
  return keepHead ? [...line.slice(0, index), split] : [split, ...line.slice(index)];
}

/** The drive itself, split into the stretches drawn as road and those drawn as a change. */
export interface RouteGeometry {
  /** Every piece in order, so the page can draw the route as one line. */
  line: Point[];
  /** The pieces that are a lane change, drawn differently because they are one. */
  changes: Point[][];
  distanceM: number;
}

export function routeGeometry(
  graph: RouteGraph,
  routeLanes: string[],
  laneChanges: number[],
  connectors: RouteConnector[],
): RouteGeometry {
  const crossings = new Map<string, Point[]>();
  for (const connector of connectors) {
    const key = crossingKey(connector.from, connector.to);
    if (!crossings.has(key)) crossings.set(key, connector.line);
  }
  const changing = new Set(laneChanges);
  const pieces: Point[][] = [];
  const changePieces: Point[][] = [];

  for (const [position, laneId] of routeLanes.entries()) {
    const lane = graph.lanes.get(laneId);
    if (!lane) continue;
    let centre = lane.line as Point[];
    const arrivingByChange = changing.has(position);
    const leavingByChange = changing.has(position + 1);

    if (arrivingByChange) {
      centre = cut(centre, false, 0.5 + CHANGE_HALF_SPAN);
      // The diagonal between where the last piece stopped and where this one starts. It is
      // part of the drive, so it counts towards the distance and is drawn as the change.
      const previous = pieces[pieces.length - 1];
      if (previous) changePieces.push([previous[previous.length - 1]!, centre[0]!]);
    } else if (position > 0) {
      const crossing = crossings.get(crossingKey(routeLanes[position - 1]!, laneId));
      if (crossing && crossing.length >= 2) pieces.push(crossing as Point[]);
    }
    if (leavingByChange) centre = cut(centre, true, 0.5 - CHANGE_HALF_SPAN);
    pieces.push(centre);
  }

  // Measured over the joined line rather than piece by piece, exactly as Python measures
  // `np.vstack(pieces)`: the diagonal of a lane change is the gap between two pieces, so
  // summing the pieces alone would leave the manoeuvre itself out of the distance.
  const line = pieces.flat();
  return { line, changes: changePieces, distanceM: lineLength(line) };
}
