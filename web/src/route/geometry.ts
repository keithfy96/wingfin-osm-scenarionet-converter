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
import type { LanePair } from "./types.js";

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
export const COINCIDENT_M = 1e-3;
export const MAX_CURVE_TURN_DEG = 20;

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

/** A cubic through four control points, sampled at `TURN_SAMPLE_M`. See `_bezier`. */
function bezier(start: Metres, p1: Metres, p2: Metres, end: Metres): Metres[] {
  const bound = span(start, p1) + span(p1, p2) + span(p2, end);
  const count = Math.max(8, Math.ceil(bound / TURN_SAMPLE_M));
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

/** The sharpest change of heading at any vertex, in degrees. See `_worst_turn_deg`. */
export function worstTurnDeg(raw: Metres[]): number {
  const line = dropRepeats(raw);
  if (line.length < 3) return 0;
  let previous = Math.atan2(line[1]![1] - line[0]![1], line[1]![0] - line[0]![0]);
  let worst = 0;
  for (let i = 2; i < line.length; i += 1) {
    const heading = Math.atan2(line[i]![1] - line[i - 1]![1], line[i]![0] - line[i - 1]![0]);
    let turned = (heading - previous) % (2 * Math.PI);
    if (turned > Math.PI) turned -= 2 * Math.PI;
    if (turned < -Math.PI) turned += 2 * Math.PI;
    worst = Math.max(worst, (Math.abs(turned) * 180) / Math.PI);
    previous = heading;
  }
  return worst;
}

/** A cubic Bezier leaving `start` along one direction and reaching `end` along another.
 *
 * Shortening the handle straightens the curve towards the chord, so the search terminates: at
 * zero it *is* the chord. See `_turn_curve` for why tangency is the right thing to trade.
 */
function turnCurve(
  start: Metres,
  startDirection: Metres,
  end: Metres,
  endDirection: Metres,
  turned: number,
): Metres[] {
  const chord = span(start, end);
  if (chord < COINCIDENT_M) return [];
  let handle = handleFraction(turned) * chord;
  for (let attempt = 0; attempt < 8; attempt += 1) {
    const p1: Metres = [start[0] + startDirection[0] * handle, start[1] + startDirection[1] * handle];
    const p2: Metres = [end[0] - endDirection[0] * handle, end[1] - endDirection[1] * handle];
    const curve = bezier(start, p1, p2, end);
    if (worstTurnDeg(curve) <= MAX_CURVE_TURN_DEG) return curve;
    handle /= 2;
  }
  return bezier(start, start, end, end);
}

interface Join {
  head: Metres[];
  curve: Metres[];
  tail: Metres[];
}

/** How much of `joining` the junction turn after it needs left over. See `_turn_reserve`. */
function turnReserve(joining: Metres[], following: Metres[], what: string): number {
  const end = joining[joining.length - 1]!;
  const directionIn = unit(joining[joining.length - 2]!, end, what);
  const directionOut = unit(following[0]!, following[1]!, what);
  const across: Metres = [following[0]![0] - end[0], following[0]![1] - end[1]];
  const sideways = Math.abs(across[0] * -directionIn[1] + across[1] * directionIn[0]);
  const wanted = wantedTrim(turnBetween(directionIn, directionOut), sideways);
  return Math.min(wanted, spare(totalLength(following))) + MIN_TANGENT_M;
}

/** How far back into each lane a turn wants to cut. See `_wanted_trim`. */
function wantedTrim(angle: number, sideways: number): number {
  const trim = TURN_RADIUS_M * Math.tan(Math.min(Math.abs(angle), (170 * Math.PI) / 180) / 2);
  if (Math.abs((angle * 180) / Math.PI) < SMOOTHING_MAX_DEG) return Math.max(trim, smoothingSpan(sideways));
  return trim;
}

/** Join two lanes: the trimmed approach, the turn between them, the trimmed exit. */
function turn(arriving: Metres[], leaving: Metres[], what: string, crossing: boolean, reserve = 0): Join {
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

  // Cut independently rather than by the smaller of the two - see `_turn` in `ego_route`.
  const wanted = wantedTrim(angle, sideways);
  const aheadLength = totalLength(ahead);
  const head = trimEnd(arriving, Math.min(wanted, spare(totalLength(arriving))));
  const tail = trimStart(ahead, Math.min(wanted, spare(aheadLength), Math.max(0, aheadLength - reserve)));
  const curve = turnCurve(head[head.length - 1]!, directionIn, tail[0]!, directionOut, angle);
  return { head, curve: curve.slice(1, -1), tail };
}

/** Cross into a lane alongside: the run-up, the crossing, the run-out. See `_lane_change`. */
function laneChange(leaving: Metres[], joining: Metres[], what: string, reserve = 0): Join {
  const entry = projectAlong(joining, leaving[0]!);
  const exit = projectAlong(joining, leaving[leaving.length - 1]!);
  if (exit - entry < 1e-6) return { head: leaving, curve: [], tail: joining };
  const spanJoining = totalLength(joining);
  let middle = (entry + exit) / 2;
  const abreast = cutMetres(joining, true, middle / spanJoining).slice(-1)[0]!;
  const sideways = span(abreast, leaving[leaving.length - 1]!);
  let reach = Math.min(smoothingSpan(sideways), CHANGE_MAX_FRACTION * (exit - entry));

  // The reserve yields first, and can yield to nothing - see `_lane_change` in `ego_route`.
  const held = Math.min(reserve, Math.max(0, spanJoining - entry - 2 * MIN_TANGENT_M));
  const latest = spanJoining - held;
  reach = Math.min(reach, (latest - entry) / 2);
  middle = Math.min(Math.max(middle, entry + reach), latest - reach);

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
    if (span(kept[kept.length - 1]!, point) > COINCIDENT_M) kept.push(point);
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
  crossingPairs: LanePair[],
): RouteGeometry {
  // Which steps cross a junction, and so are judged against `MAX_CROSSING_M` rather than
  // `MAX_JOIN_M`. Handed over by `ego_route.junction_crossings` in the payload, not worked
  // out here. This used to be read off the connectors, and a connector is not the same
  // question: a road running straight through a junction is a plain continuation with no
  // connector, and once generation started cutting lanes back to the junction edge those
  // steps had a whole junction to span. Judged as plain joins they were refused - 77% of
  // the destinations this page painted blue on `mosque` could not be drawn, while the
  // converter built them without complaint.
  const crossings = new Set(crossingPairs.map(([from, to]) => crossingKey(from, to)));
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
    // What comes next decides how much of this lane the manoeuvre may use. A lane change
    // after this one needs nothing held back: it places itself in the window the two lanes
    // share rather than at the far end. See `route_polyline`.
    const after = graph.lanes.get(routeLanes[position + 1] ?? "");
    const reserve =
      after && after.line.length >= 2 && !changing.has(position + 1)
        ? turnReserve(centre, after.line.map((p) => frame.to(p as Point)), `lane ${laneId}`)
        : 0;
    const join: Join = isChange
      ? laneChange(current, centre, `lane ${laneId}`, reserve)
      : turn(
          current,
          centre,
          `lane ${laneId}`,
          crossings.has(crossingKey(routeLanes[position - 1]!, laneId)),
          reserve,
        );
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
