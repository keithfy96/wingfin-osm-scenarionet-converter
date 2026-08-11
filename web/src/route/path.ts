// Finding the drive between two lanes, in the browser.
//
// This is a *preview*. The route that reaches the pickle is re-derived in Python at convert
// time, from the same lane model, because nothing that ends up in a dataset should come
// from code that only ran in a browser. What this has to get right is the answer, so the
// page never offers a route the converter would then refuse - `web/test/route/path.test.ts`
// and `tests/unit/test_ego_route.py` run the same fixture through both.
//
// The weights match `ego_route._graph` exactly: a junction movement costs the length of the
// lane it leads into, and a lane change costs half that plus a fixed penalty, because a
// change lands mid-way along the neighbour rather than at its start.

import type { RouteLane } from "./types.js";

/** Matches `ego_route.LANE_CHANGE_COST_M`. */
export const LANE_CHANGE_COST_M = 5;

export interface Step {
  to: string;
  weight: number;
  change: boolean;
}

export interface FoundRoute {
  lanes: string[];
  /** Indices into `lanes` reached by moving sideways rather than through a junction. */
  laneChanges: number[];
}

const EARTH_RADIUS_M = 6_371_000;

/** Great-circle distance, which is what the payload's [lat, lon] pairs support.
 *
 * The lane model holds metres in a local projection, but the page is handed the WGS84
 * projection of it for Leaflet. Over a network 800 m across the difference between the two
 * is far below the lane-change penalty, so it cannot change which route wins - and the
 * distance actually reported to the user comes from Python anyway.
 */
export function metresBetween(a: [number, number], b: [number, number]): number {
  const toRad = Math.PI / 180;
  const meanLat = ((a[0] + b[0]) / 2) * toRad;
  const dLat = (b[0] - a[0]) * toRad;
  const dLon = (b[1] - a[1]) * toRad * Math.cos(meanLat);
  return Math.hypot(dLat, dLon) * EARTH_RADIUS_M;
}

/** One key for a junction, so the two places that build it cannot disagree.
 *
 * They already did once: a stray byte in one of two hand-written template literals made the
 * lookup silently miss, and every junction on a route was measured as zero.
 */
export function crossingKey(from: string, to: string): string {
  return `${from}->${to}`;
}

export function lineLength(line: [number, number][]): number {
  let total = 0;
  for (let index = 1; index < line.length; index += 1) {
    total += metresBetween(line[index - 1]!, line[index]!);
  }
  return total;
}

export function laneLength(lane: RouteLane): number {
  return lineLength(lane.line);
}

export class RouteGraph {
  private readonly steps = new Map<string, Step[]>();
  readonly lanes = new Map<string, RouteLane>();

  constructor(lanes: RouteLane[]) {
    for (const lane of lanes) this.lanes.set(lane.id, lane);
    const lengths = new Map<string, number>();
    for (const lane of lanes) lengths.set(lane.id, laneLength(lane));

    for (const lane of lanes) {
      const out: Step[] = [];
      const taken = new Set<string>();
      for (const target of lane.exits) {
        if (!this.lanes.has(target) || taken.has(target)) continue;
        taken.add(target);
        out.push({ to: target, weight: lengths.get(target) ?? 0, change: false });
      }
      for (const target of lane.sideways) {
        // A junction movement to the same lane already exists and is the cheaper, plainer
        // way to get there; Python resolves the collision the same way.
        if (!this.lanes.has(target) || taken.has(target)) continue;
        taken.add(target);
        out.push({
          to: target,
          weight: (lengths.get(target) ?? 0) / 2 + LANE_CHANGE_COST_M,
          change: true,
        });
      }
      this.steps.set(lane.id, out);
    }
  }

  stepsFrom(laneId: string): Step[] {
    return this.steps.get(laneId) ?? [];
  }

  /** The cheapest drive from `start` to `end`, or null when there is none.
   *
   * Most pairs have none - the map is one-way in most places - so an empty answer is the
   * common case and has to read as an answer rather than a failure.
   */
  find(start: string, end: string): FoundRoute | null {
    if (!this.lanes.has(start) || !this.lanes.has(end) || start === end) return null;

    const best = new Map<string, number>([[start, 0]]);
    const cameFrom = new Map<string, { from: string; change: boolean }>();
    // A binary heap would be faster; with 285 lanes the linear scan is imperceptible and
    // it keeps the tie-breaking obvious, which matters more than speed here.
    const open = new Set<string>([start]);

    while (open.size > 0) {
      let current: string | null = null;
      let currentCost = Infinity;
      for (const candidate of open) {
        const cost = best.get(candidate) ?? Infinity;
        // Ties break on the id, so the same pair always yields the same route however the
        // payload happens to be ordered.
        if (cost < currentCost || (cost === currentCost && current !== null && candidate < current)) {
          current = candidate;
          currentCost = cost;
        }
      }
      if (current === null) break;
      if (current === end) break;
      open.delete(current);

      for (const step of this.stepsFrom(current)) {
        const cost = currentCost + step.weight;
        if (cost < (best.get(step.to) ?? Infinity)) {
          best.set(step.to, cost);
          cameFrom.set(step.to, { from: current, change: step.change });
          open.add(step.to);
        }
      }
    }

    if (!best.has(end)) return null;

    const lanes: string[] = [end];
    const changes: number[] = [];
    let cursor = end;
    while (cursor !== start) {
      const previous = cameFrom.get(cursor);
      if (!previous) return null;
      // `cursor` is the lane the step arrived at, and it sits at the end of the
      // still-reversed list - so its position is the last index, not the length.
      if (previous.change) changes.push(lanes.length - 1);
      lanes.push(previous.from);
      cursor = previous.from;
    }
    lanes.reverse();
    // Positions were counted from the end while walking backwards.
    const laneChanges = changes.map((index) => lanes.length - 1 - index).sort((a, b) => a - b);

    // No distance here. Estimating it from lane lengths was wrong twice - once too low,
    // once too high - because a route's length depends on geometry this class does not
    // hold. `geometry.routeGeometry` builds the drive the way the converter does and
    // measures that.
    return { lanes, laneChanges };
  }

  /** Every lane reachable from `start`, so unreachable ends can be shown as such. */
  reachableFrom(start: string): Set<string> {
    const seen = new Set<string>([start]);
    const queue = [start];
    while (queue.length > 0) {
      const lane = queue.shift()!;
      for (const step of this.stepsFrom(lane)) {
        if (seen.has(step.to)) continue;
        seen.add(step.to);
        queue.push(step.to);
      }
    }
    return seen;
  }
}
