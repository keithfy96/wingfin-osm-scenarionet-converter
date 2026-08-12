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
//
// Two things the Python side explains at length and this file only summarises. A junction
// turn is *built* here rather than read off the connector: `topology.connector_curve` makes
// a marker for the inspection map, not a driving line, and splicing it in put a 180° flip at
// 55 of `junction-1`'s 83 movements. And the geometry runs in metres, in a local frame,
// because a radius in degrees is a different radius north-south than east-west.

import { crossingKey, lineLength, type RouteGraph } from "./path.js";
import type { RouteConnector } from "./types.js";

/** All of these mirror the constant of the same name in `ego_route`. */
export const TURN_RADIUS_M = 9;
export const MAX_TURN_TRIM_FRACTION = 0.4;
export const MIN_TANGENT_M = 1;
export const CHANGE_MAX_FRACTION = 0.45;
export const SMOOTHING_RADIUS_M = 110;
export const SMOOTHING_MAX_DEG = 20;
export const TURN_SAMPLE_M = 0.25;
export const STRAIGHT_DEG = 1;
export const MAX_VERTEX_TURN_DEG = 150;
export const MAX_JOIN_M = 5;
export const MAX_CROSSING_M = 20;

const EARTH_RADIUS_M = 6_371_000;

/** Leaflet order: [lat, lon]. */
type Point = [number, number];
/** Local metres: [east, north]. */
type Metres = [number, number];

/** Raised for a route that cannot be built, matching `ego_route.RouteError`. */
export class RouteGeometryError extends Error {}

/** Flat metres about a fixed origin - the same approximation `metresBetween` makes. */
function projector(origin: Point) {
  const toRad = Math.PI / 180;
  const perLat = EARTH_RADIUS_M * toRad;
  const perLon = perLat * Math.cos(origin[0] * toRad);
  return {
    to: (p: Point): Metres => [(p[1] - origin[1]) * perLon, (p[0] - origin[0]) * perLat],
    from: (m: Metres): Point => [origin[0] + m[1] / perLat, origin[1] + m[0] / perLon],
  };
}

function span(a: Metres, b: Metres): number {
  return Math.hypot(b[0] - a[0], b[1] - a[1]);
}

function totalLength(line: Metres[]): number {
  let total = 0;
  for (let i = 1; i < line.length; i += 1) total += span(line[i - 1]!, line[i]!);
  return total;
}

/** A line split at `at`, a fraction of its length; the head of it or the tail.
 *
 * Kept on [lat, lon] pairs because the page and `routes-file` both use it that way; over one
 * short line the two axes' scales differ far too little to move a proportional split.
 */
export function cut(line: Point[], keepHead: boolean, at: number): Point[] {
  if (line.length < 2) return line;
  const travelled: number[] = [0];
  for (let i = 1; i < line.length; i += 1) {
    travelled.push(travelled[i - 1]! + Math.hypot(line[i]![0] - line[i - 1]![0], line[i]![1] - line[i - 1]![1]));
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

/** The same split, in metres, which is the frame every turn is built in. */
function cutMetres(line: Metres[], keepHead: boolean, at: number): Metres[] {
  if (line.length < 2) return line;
  const travelled: number[] = [0];
  for (let i = 1; i < line.length; i += 1) travelled.push(travelled[i - 1]! + span(line[i - 1]!, line[i]!));
  const target = travelled[travelled.length - 1]! * at;
  let index = travelled.findIndex((d) => d >= target);
  index = Math.max(1, Math.min(index === -1 ? line.length - 1 : index, line.length - 1));
  const width = travelled[index]! - travelled[index - 1]!;
  const ratio = width === 0 ? 0 : (target - travelled[index - 1]!) / width;
  const a = line[index - 1]!;
  const b = line[index]!;
  const split: Metres = [a[0] + (b[0] - a[0]) * ratio, a[1] + (b[1] - a[1]) * ratio];
  return keepHead ? [...line.slice(0, index), split] : [split, ...line.slice(index)];
}

function trimEnd(line: Metres[], distance: number): Metres[] {
  const total = totalLength(line);
  if (distance <= 0 || total <= distance) return line;
  return cutMetres(line, true, (total - distance) / total);
}

function trimStart(line: Metres[], distance: number): Metres[] {
  const total = totalLength(line);
  if (distance <= 0 || total <= distance) return line;
  return cutMetres(line, false, distance / total);
}

function unit(from: Metres, to: Metres, what: string): Metres {
  const length = span(from, to);
  if (length < 1e-9) throw new RouteGeometryError(`${what} has no direction`);
  return [(to[0] - from[0]) / length, (to[1] - from[1]) / length];
}

/** Signed angle from one direction to another, in radians. */
function turnBetween(before: Metres, after: Metres): number {
  return Math.atan2(before[0] * after[1] - before[1] * after[0], before[0] * after[0] + before[1] * after[1]);
}

/** `line` with any head at or behind `origin` along `direction` removed. See `_advance_past`. */
function advancePast(line: Metres[], origin: Metres, direction: Metres, what: string): Metres[] {
  const reach = line.map((p) => (p[0] - origin[0]) * direction[0] + (p[1] - origin[1]) * direction[1]);
  if (reach[reach.length - 1]! <= 0) {
    throw new RouteGeometryError(`${what} lies entirely behind the piece before it`);
  }
  if (reach[0]! > 0) return line;
  const index = reach.findIndex((d) => d > 0);
  const before = reach[index - 1]!;
  const ratio = (0 - before) / (reach[index]! - before);
  const a = line[index - 1]!;
  const b = line[index]!;
  const foot: Metres = [a[0] + (b[0] - a[0]) * ratio, a[1] + (b[1] - a[1]) * ratio];
  return [foot, ...line.slice(index)];
}

/** The Bezier's handles as a fraction of the chord. See `_handle_fraction` in `ego_route`. */
function handleFraction(turn: number): number {
  const angle = Math.abs(turn);
  if (angle < 1e-6) return 1 / 3;
  return ((2 / 3) * Math.tan(angle / 4)) / Math.sin(angle / 2);
}

/** Half the distance a sideways step needs to be spread over. See `_smoothing_span`. */
function smoothingSpan(sideways: number): number {
  return Math.sqrt(6 * Math.max(sideways, 0) * SMOOTHING_RADIUS_M) / 2;
}

/** How much of a lane of this length one turn may use. See `_spare`. */
function spare(length: number): number {
  return Math.max(MAX_TURN_TRIM_FRACTION * length, length - MIN_TANGENT_M);
}

/** How far along `line` the nearest point to `point` lies. See `_project_along`. */
function projectAlong(line: Metres[], point: Metres): number {
  let travelled = 0;
  let best = Infinity;
  let bestAt = 0;
  for (let i = 0; i + 1 < line.length; i += 1) {
    const a = line[i]!;
    const b = line[i + 1]!;
    const length = span(a, b);
    if (length < 1e-9) continue;
    const raw = ((point[0] - a[0]) * (b[0] - a[0]) + (point[1] - a[1]) * (b[1] - a[1])) / length;
    const along = Math.min(Math.max(raw, 0), length);
    const foot: Metres = [a[0] + ((b[0] - a[0]) / length) * along, a[1] + ((b[1] - a[1]) / length) * along];
    const distance = span(foot, point);
    if (distance < best) {
      best = distance;
      bestAt = travelled + along;
    }
    travelled += length;
  }
  return bestAt;
}

/** A cubic Bezier leaving `start` along one direction and reaching `end` along another. */
function turnCurve(
  start: Metres,
  startDirection: Metres,
  end: Metres,
  endDirection: Metres,
  turned: number,
): Metres[] {
  const chord = span(start, end);
  if (chord < 1e-6) return [];
  const handle = handleFraction(turned) * chord;
  const p1: Metres = [start[0] + startDirection[0] * handle, start[1] + startDirection[1] * handle];
  const p2: Metres = [end[0] - endDirection[0] * handle, end[1] - endDirection[1] * handle];
  const count = Math.max(8, Math.ceil((chord + 2 * handle) / TURN_SAMPLE_M));
  const points: Metres[] = [];
  for (let i = 0; i <= count; i += 1) {
    const t = i / count;
    const u = 1 - t;
    points.push([
      u * u * u * start[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t * t * t * end[0],
      u * u * u * start[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t * t * t * end[1],
    ]);
  }
  return points;
}

interface Join {
  head: Metres[];
  curve: Metres[];
  tail: Metres[];
}

/** Join two lanes: the trimmed approach, the turn between them, the trimmed exit. */
function turn(arriving: Metres[], leaving: Metres[], what: string, crossing: boolean): Join {
  const allowed = crossing ? MAX_CROSSING_M : MAX_JOIN_M;
  const gap = span(arriving[arriving.length - 1]!, leaving[0]!);
  if (gap > allowed) {
    throw new RouteGeometryError(
      `the route leaves a ${gap.toFixed(0)} m gap before ${what}, more than a ` +
        `${crossing ? "junction" : "join"} spans`,
    );
  }
  const end = arriving[arriving.length - 1]!;
  const directionIn = unit(arriving[arriving.length - 2]!, end, `the lane before ${what}`);
  let directionOut = unit(leaving[0]!, leaving[1]!, what);
  let angle = turnBetween(directionIn, directionOut);

  // Only a shallow join can have an overlap worth trimming - see `_turn` in `ego_route`.
  let ahead = leaving;
  if (Math.abs((angle * 180) / Math.PI) < SMOOTHING_MAX_DEG) {
    ahead = advancePast(leaving, end, directionIn, what);
    if (ahead.length < 2) throw new RouteGeometryError(`${what} has nothing left of it`);
    directionOut = unit(ahead[0]!, ahead[1]!, what);
    angle = turnBetween(directionIn, directionOut);
  }

  const across: Metres = [ahead[0]![0] - end[0], ahead[0]![1] - end[1]];
  const sideways = Math.abs(across[0] * -directionIn[1] + across[1] * directionIn[0]);
  if (Math.abs((angle * 180) / Math.PI) < STRAIGHT_DEG && sideways < 0.05) {
    return { head: arriving, curve: [], tail: ahead };
  }

  let trim = TURN_RADIUS_M * Math.tan(Math.min(Math.abs(angle), (170 * Math.PI) / 180) / 2);
  if (Math.abs((angle * 180) / Math.PI) < SMOOTHING_MAX_DEG) {
    trim = Math.max(trim, smoothingSpan(sideways));
  }
  trim = Math.min(trim, spare(totalLength(arriving)), spare(totalLength(ahead)));
  const head = trimEnd(arriving, trim);
  const tail = trimStart(ahead, trim);
  const curve = turnCurve(head[head.length - 1]!, directionIn, tail[0]!, directionOut, angle);
  return { head, curve: curve.slice(1, -1), tail };
}

/** Cross into a lane alongside: the run-up, the crossing, the run-out. See `_lane_change`. */
function laneChange(leaving: Metres[], joining: Metres[], what: string): Join {
  const entry = projectAlong(joining, leaving[0]!);
  const exit = projectAlong(joining, leaving[leaving.length - 1]!);
  if (exit - entry < 1e-6) return { head: leaving, curve: [], tail: joining };
  const spanJoining = totalLength(joining);
  const middle = (entry + exit) / 2;
  const abreast = cutMetres(joining, true, middle / spanJoining).slice(-1)[0]!;
  const sideways = span(abreast, leaving[leaving.length - 1]!);
  const reach = Math.min(smoothingSpan(sideways), CHANGE_MAX_FRACTION * (exit - entry));

  const at = (middle - reach - entry) / (exit - entry);
  const head = cutMetres(leaving, true, Math.min(Math.max(at, 1e-6), 1));
  const tail = trimStart(joining, middle + reach);
  if (head.length < 2 || tail.length < 2) return { head, curve: [], tail };
  const directionIn = unit(head[head.length - 2]!, head[head.length - 1]!, `the lane left before ${what}`);
  const directionOut = unit(tail[0]!, tail[1]!, what);
  const curve = turnCurve(head[head.length - 1]!, directionIn, tail[0]!, directionOut, 0);
  return { head, curve: curve.slice(1, -1), tail };
}

/** Collapse coincident consecutive points; matches `_drop_repeats`.
 *
 * Lanes meet where one ends and the next begins, so a join routinely produces a duplicate. A
 * zero-length step has no direction, and the heading read off it is whatever `atan2(0, 0)`
 * returns - which then looks like a 169° reversal to the check below.
 */
function dropRepeats(line: Metres[]): Metres[] {
  if (line.length < 2) return line;
  const kept: Metres[] = [line[0]!];
  for (const point of line.slice(1)) {
    if (span(kept[kept.length - 1]!, point) > 1e-6) kept.push(point);
  }
  return kept;
}

/** Refuse a drive that snaps round, the mirror of the hole check. See `_refuse_reversals`. */
function refuseReversals(line: Metres[]): void {
  if (line.length < 3) return;
  let previous = Math.atan2(line[1]![1] - line[0]![1], line[1]![0] - line[0]![0]);
  for (let i = 2; i < line.length; i += 1) {
    const heading = Math.atan2(line[i]![1] - line[i - 1]![1], line[i]![0] - line[i - 1]![0]);
    const turned = Math.abs((((heading - previous + Math.PI) % (2 * Math.PI)) + 2 * Math.PI) % (2 * Math.PI) - Math.PI);
    if ((turned * 180) / Math.PI > MAX_VERTEX_TURN_DEG) {
      throw new RouteGeometryError(`the route turns ${((turned * 180) / Math.PI).toFixed(0)}° in one step`);
    }
    previous = heading;
  }
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
  // The connectors say *which* steps cross a junction. Their geometry is not used - see the
  // note at the top of this file, and `ego_route`'s module docstring for the measurements.
  const crossings = new Set(connectors.map((c) => crossingKey(c.from, c.to)));
  const changing = new Set(laneChanges);

  const first = graph.lanes.get(routeLanes[0] ?? "");
  if (!first || first.line.length < 2) return { line: [], changes: [], distanceM: 0 };
  const frame = projector(first.line[0]!);

  const finished: Metres[][] = [];
  const changePieces: Metres[][] = [];
  let current: Metres[] | null = null;

  for (let position = 0; position < routeLanes.length; position += 1) {
    let laneId = routeLanes[position]!;
    let lane = graph.lanes.get(laneId);
    if (!lane || lane.line.length < 2) continue;
    if (current === null) {
      current = lane.line.map((p) => frame.to(p as Point));
      continue;
    }
    const isChange = changing.has(position);
    if (isChange) {
      // A run of changes is one manoeuvre, not several - see `route_polyline` in Python.
      let last = position;
      while (last + 1 < routeLanes.length && changing.has(last + 1)) last += 1;
      laneId = routeLanes[last]!;
      lane = graph.lanes.get(laneId);
      if (!lane || lane.line.length < 2) continue;
      position = last;
    }
    const centre = lane.line.map((p) => frame.to(p as Point));
    const join: Join = isChange
      ? laneChange(current, centre, `lane ${laneId}`)
      : turn(current, centre, `lane ${laneId}`, crossings.has(crossingKey(routeLanes[position - 1]!, laneId)));
    finished.push(join.head);
    if (join.curve.length > 0) {
      finished.push(join.curve);
      if (isChange) changePieces.push(join.curve);
    }
    current = join.tail;
  }
  if (current === null) return { line: [], changes: [], distanceM: 0 };
  finished.push(current);

  const metres = dropRepeats(finished.flat());
  refuseReversals(metres);
  const line = metres.map(frame.from);
  return {
    line,
    changes: changePieces.map((piece) => piece.map(frame.from)),
    // Measured over the joined line rather than piece by piece, and in the page's own
    // [lat, lon] frame, so it is the same measurement `lineLength` makes everywhere else.
    distanceM: lineLength(line),
  };
}
