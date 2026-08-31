// Generating a starting scene of actors, at a chosen density, along a chosen route.
//
// Placing actors one click at a time is exact and slow, and the first attempt on
// `junction-1` showed the failure it invites: the pedestrian went down 137 m from the ego's
// route, was valid for 68 of the scenario's 960 frames, and the drive never went near it. So
// this module places them *against the drive* - every actor sits on or beside a lane the
// imported route actually uses, and every walker is timed to be standing there when the car
// is due.
//
// Nothing here touches the DOM or Leaflet, so `web/test/actor/randomise.test.ts` tests the
// placement itself rather than a page that happens to contain it. What comes out is
// `DrawnActor[]` in exactly the shape the panel's own list holds, which is what makes the
// result editable: a generated actor is not a special kind of actor, it is the same entry an
// "Add actor" click produces, and it is written out by the same `serializeActors`.

import { EARTH_RADIUS_M, metresBetween } from "../geo.js";
import type { ActorKind, DrawnActor } from "./types.js";

export type Density = "none" | "low" | "medium" | "dense";

export const DENSITIES: readonly Density[] = ["none", "low", "medium", "dense"];

/** Actors per kilometre of corridor.
 *
 * Read off what a scene should feel like rather than off a survey - there is no surveyed
 * pedestrian network on either map to read it off (across `junction-1` and `mosque` the OSM
 * carries four footways and not one `highway=crossing` node). "medium" everywhere on
 * `junction-1`'s 1089 m route 5 is about 19 actors, which is a populated junction rather
 * than a crowd, and every one of them is editable afterwards.
 */
export const PER_KM: Record<ActorKind, Record<Exclude<Density, "none">, number>> = {
  pedestrian: { low: 1, medium: 4, dense: 10 },
  cyclist: { low: 1, medium: 3, dense: 8 },
  cone: { low: 2, medium: 8, dense: 20 },
  barrier: { low: 1, medium: 3, dense: 8 },
};

/** Ceiling on a whole-map press.
 *
 * Without a route the rates run over every lane in the model, which on `mosque` is 405 of
 * them - "dense" there is thousands of actors and a file nobody can edit, which is the one
 * outcome this feature exists to avoid.
 */
export const WHOLE_MAP_CAP = 150;

/** How long a walker stands at the kerb before stepping into the road.
 *
 * It is also the lead: the walk begins at the estimated arrival, so the actor appears this
 * long before it. Shortened, never the start delayed, for a walker so near the start of the
 * route that the car gets there sooner - see `place`.
 */
export const PEDESTRIAN_DWELL_S = 20;

/** A cyclist is put on the road a few seconds ahead of the car rather than made to wait.
 * It rides away from the sample point, so by the time the ego is there it is the object in
 * front rather than an obstacle in the road. */
export const CYCLIST_LEAD_S = 5;

/** How far a rider rides before its track ends. */
export const CYCLIST_PATH_MIN_M = 40;
export const CYCLIST_PATH_MAX_M = 120;

/** No generated actor waits longer than this share of the estimated drive.
 *
 * The assumed pace is a guess and the arrival it produces inherits the error, which lands
 * hardest on the far end of the route: on `junction-1`'s route-1 the default 30 km/h against
 * an actual 37 km/h stretched a 38 s drive to an estimated 47 s, and the cyclist a whole-route
 * arrival put at 35 s appeared at step 350 of 379 - on the map, and all but over. An actor
 * that shows up after the recording ends is an actor that is not there, so the delay is
 * clamped rather than trusted.
 */
export const MAX_DELAY_FRACTION = 0.6;

/** Clear of the kerb, and clear of the centreline, so a crossing starts and ends off the
 * carriageway rather than on the edge of it. */
export const VERGE_M = 2;

/** How far outside the kerb edge a cone or a barrier stands. */
export const KERB_CLEARANCE_M = 0.4;

/** Enough of a lane to place something on. Shorter ones are junction stubs. */
export const MIN_LANE_M = 6;

/** Only what placement reads. `ActorLane` satisfies it; so does anything else with the
 * geometry, which is what keeps the tests free of the rest of the payload. */
export interface PlacementLane {
  id: string;
  /** Leaflet order: [lat, lon]. */
  line: [number, number][];
  width_m: number;
  /** Centre-out: 0 hugs the centreline, `count - 1` is kerbside. */
  index: number;
  count: number;
}

export interface RandomiseOptions {
  /** The lanes to place against, in travel order when they came from a route. */
  corridor: PlacementLane[];
  densities: Record<ActorKind, Density>;
  seed: number;
  /** Names already spoken for, so a generated actor never collides with a drawn one. */
  taken: Iterable<string>;
  speeds: { pedestrian_mps: number; cyclist_mps: number };
  /**
   * Metres per second the ego is assumed to average, or null when there is no route and so
   * no arrival to aim at.
   */
  egoMps: number | null;
  /** Paint a zebra for each generated pedestrian. */
  crossings: boolean;
  crossingWidthM: number;
  cap?: number;
}

/** mulberry32. Eight lines of arithmetic rather than a dependency, and deterministic, which
 * is the whole point of showing the seed: the same seed and settings give the same scene. */
export function rng(seed: number): () => number {
  let state = seed >>> 0;
  return () => {
    state = (state + 0x6d2b79f5) >>> 0;
    let t = state;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** `point` moved `east` and `north` metres.
 *
 * Built on `geo.EARTH_RADIUS_M` rather than a radius of its own: this is the inverse of the
 * approximation `metresBetween` makes, and a second constant is how the two would come to
 * disagree about the size of the planet.
 */
export function offsetMetres(
  point: [number, number],
  east: number,
  north: number,
): [number, number] {
  const toDeg = 180 / Math.PI;
  const latitude = point[0] + (north / EARTH_RADIUS_M) * toDeg;
  const scale = Math.cos((point[0] * Math.PI) / 180) || 1e-9;
  const longitude = point[1] + (east / (EARTH_RADIUS_M * scale)) * toDeg;
  return [latitude, longitude];
}

interface Sample {
  point: [number, number];
  /** Unit vector along travel, in metres-space. */
  east: number;
  north: number;
  lane: PlacementLane;
}

/** Every lane laid end to end, with where each one starts. */
class Corridor {
  readonly lanes: PlacementLane[];
  /** Arc length at the start of each lane, plus the total at the end. */
  private readonly starts: number[] = [0];
  readonly totalM: number;

  constructor(lanes: PlacementLane[]) {
    // Junction stubs are metres long and a crossing drawn across one is a crossing of
    // nothing. Dropping them here rather than in each placement keeps the arc length and
    // the sampling talking about the same road.
    this.lanes = lanes.filter((lane) => lane.line.length >= 2 && lineLengthM(lane.line) >= MIN_LANE_M);
    let total = 0;
    for (const lane of this.lanes) {
      total += lineLengthM(lane.line);
      this.starts.push(total);
    }
    this.totalM = total;
  }

  /** The point `distance` metres along, and which way travel points there. */
  at(distance: number): Sample | null {
    if (this.lanes.length === 0) return null;
    const wanted = Math.min(Math.max(distance, 0), this.totalM);
    let index = 0;
    while (index < this.lanes.length - 1 && (this.starts[index + 1] ?? 0) <= wanted) index += 1;
    const lane = this.lanes[index];
    const start = this.starts[index];
    if (!lane || start === undefined) return null;
    return along(lane, wanted - start);
  }
}

export function lineLengthM(line: readonly [number, number][]): number {
  let total = 0;
  for (let index = 1; index < line.length; index += 1) {
    const previous = line[index - 1];
    const current = line[index];
    if (previous && current) total += metresBetween(previous, current);
  }
  return total;
}

/** `distance` metres along one lane's centreline, with the unit tangent there. */
function along(lane: PlacementLane, distance: number): Sample | null {
  let travelled = 0;
  for (let index = 1; index < lane.line.length; index += 1) {
    const a = lane.line[index - 1];
    const b = lane.line[index];
    if (!a || !b) continue;
    const segment = metresBetween(a, b);
    if (segment <= 0) continue;
    if (travelled + segment >= distance || index === lane.line.length - 1) {
      const fraction = Math.min(Math.max((distance - travelled) / segment, 0), 1);
      const point: [number, number] = [
        a[0] + (b[0] - a[0]) * fraction,
        a[1] + (b[1] - a[1]) * fraction,
      ];
      const { east, north } = unitBetween(a, b);
      return { point, east, north, lane };
    }
    travelled += segment;
  }
  return null;
}

/** The unit vector from `a` to `b`, in metres-space. */
function unitBetween(a: [number, number], b: [number, number]): { east: number; north: number } {
  const meanLat = (((a[0] + b[0]) / 2) * Math.PI) / 180;
  const east = (b[1] - a[1]) * Math.cos(meanLat);
  const north = b[0] - a[0];
  const length = Math.hypot(east, north) || 1;
  return { east: east / length, north: north / length };
}

/** Metres from this lane's centre out to the kerb, and in to the centreline.
 *
 * Indices run centre-out - `index` 0 hugs the centreline, `count - 1` is kerbside - so the
 * two are not the same number unless the lane is the only one on its carriageway.
 */
export function halfLane(lane: PlacementLane): number {
  return (lane.width_m > 0 ? lane.width_m : 3.5) / 2;
}

export function halfWidths(lane: PlacementLane): { toKerb: number; toCentre: number } {
  const width = lane.width_m > 0 ? lane.width_m : 3.5;
  const count = Math.max(lane.count, 1);
  const index = Math.min(Math.max(lane.index, 0), count - 1);
  return {
    toKerb: (count - index - 0.5) * width,
    toCentre: (index + 0.5) * width,
  };
}

/** `metres` to the left of travel, which on this map is the kerb.
 *
 * `driving_side` is `left` and lane indices run centre-out, so the nearside - the kerb - is
 * to the left of a car going forwards, and the centreline is to its right. A sign error here
 * puts every crossing on the wrong side of the road and nothing downstream would object.
 */
function toKerbside(sample: Sample, metres: number): [number, number] {
  return offsetMetres(sample.point, -sample.north * metres, sample.east * metres);
}

/** How many of a kind to place over `metres` of corridor.
 *
 * At least one whenever a density was asked for: "low" on a 300 m route rounds to zero
 * actors, and a button that quietly does nothing is worse than one that places a single
 * pedestrian.
 */
export function countFor(kind: ActorKind, density: Density, metres: number): number {
  if (density === "none") return 0;
  const perKm = PER_KM[kind][density];
  return Math.max(1, Math.round((perKm * metres) / 1000));
}

/** When the ego is estimated to reach `distance` along the corridor.
 *
 * An estimate, and deliberately used as one. `route/path.ts` says plainly that a route's
 * length cannot be summed from its lane lengths - it was wrong twice, once low and once high
 * - and this page does not pull in `geometry.ts` to fix that. So the walkers are given a
 * twenty-second dwell centred on this number rather than being timed to the second, and the
 * page says the delay is a starting point to edit.
 */
export function arrivalSeconds(distance: number, egoMps: number | null): number | null {
  if (egoMps === null || !(egoMps > 0)) return null;
  return distance / egoMps;
}

function namer(taken: Iterable<string>): (stem: string) => string {
  const used = new Set(taken);
  return (stem: string) => {
    let index = 1;
    let candidate = `${stem}-${index}`;
    while (used.has(candidate)) {
      index += 1;
      candidate = `${stem}-${index}`;
    }
    used.add(candidate);
    return candidate;
  };
}

const STEM: Record<ActorKind, string> = {
  pedestrian: "ped",
  cyclist: "bike",
  cone: "cone",
  barrier: "barrier",
};

/** Evenly spaced along the corridor, jittered inside each slot, clear of both ends. */
function positions(count: number, totalM: number, random: () => number): number[] {
  const out: number[] = [];
  if (count <= 0 || totalM <= 0) return out;
  const slot = totalM / count;
  for (let index = 0; index < count; index += 1) {
    const at = (index + 0.15 + random() * 0.7) * slot;
    out.push(Math.min(Math.max(at, 1), Math.max(totalM - 1, 1)));
  }
  return out;
}

/**
 * A scene of actors along `corridor`, ready to drop into the panel's list.
 *
 * Order is fixed - kinds in `KINDS` order, each spaced along the corridor - so the same seed
 * and the same settings give the same file, byte for byte.
 */
export function generateActors(options: RandomiseOptions): DrawnActor[] {
  const corridor = new Corridor(options.corridor);
  if (corridor.totalM <= 0) return [];

  const cap = options.cap ?? Infinity;
  const wanted: { kind: ActorKind; count: number }[] = [];
  let total = 0;
  for (const kind of ["pedestrian", "cyclist", "cone", "barrier"] as ActorKind[]) {
    const count = countFor(kind, options.densities[kind], corridor.totalM);
    total += count;
    wanted.push({ kind, count });
  }
  // Scaled rather than truncated, so a capped press keeps the mix that was asked for
  // instead of spending the whole budget on whichever kind is enumerated first.
  const scale = total > cap ? cap / total : 1;

  const random = rng(options.seed);
  const name = namer(options.taken);
  const out: DrawnActor[] = [];

  for (const { kind, count } of wanted) {
    const scaled = scale < 1 ? Math.max(count > 0 ? 1 : 0, Math.floor(count * scale)) : count;
    for (const distance of positions(scaled, corridor.totalM, random)) {
      const sample = corridor.at(distance);
      if (!sample) continue;
      const actor = place(kind, sample, distance, name(STEM[kind]), random, options, corridor.totalM);
      if (actor) out.push(actor);
    }
  }
  return out;
}

function place(
  kind: ActorKind,
  sample: Sample,
  distance: number,
  name: string,
  random: () => number,
  options: RandomiseOptions,
  totalM: number,
): DrawnActor | null {
  const { toKerb, toCentre } = halfWidths(sample.lane);
  const arrival = arrivalSeconds(distance, options.egoMps);
  const latest = arrivalSeconds(totalM * MAX_DELAY_FRACTION, options.egoMps) ?? Infinity;

  /** When this actor starts, clamped so it is never given a delay past the end of the drive. */
  const delay = (lead: number): number =>
    round1(arrival === null ? random() * 30 : Math.min(Math.max(arrival - lead, 0), latest));

  if (kind === "pedestrian") {
    // Kerb to centreline, clearing both, so the walk starts and finishes off the tarmac and
    // the crossing spans the carriageway rather than half of it.
    const path: [number, number][] = [
      toKerbside(sample, toKerb + VERGE_M),
      toKerbside(sample, -(toCentre + VERGE_M)),
    ];
    // Standing at the kerb, then out in front of the car.
    //
    // Two corrections live in this arithmetic, both of them measured, and both invisible
    // from the file afterwards:
    //
    // 1. **The walk is aimed at the lane, not at the kerb.** Starting it at the arrival puts
    //    the pedestrian a step off the pavement as the car goes by - it passed 3.8 m away
    //    and drove the route in exactly the 412 steps it takes with no actors at all. So the
    //    walk begins earlier by however long it takes to reach the **near edge of the ego's
    //    lane**, and the pedestrian is entering the road when the car gets there. The near
    //    edge and not the centreline: aiming at the centreline works, and works too well -
    //    the recorded tape then passes 0.3 m from the walker, which is a strike for anything
    //    driving it back under `--agent-policy replay`.
    // 2. **The dwell is shortened rather than the start pushed back.** Holding it at twenty
    //    seconds and flooring the start at zero looks the same and is not: a walker thirty
    //    metres in has a four-second arrival, so it would stand at the kerb until t=20 and
    //    cross long after the car had gone.
    //
    // The dwell also does the second job the timing has: a moving actor is valid only while
    // it is walking and MetaDrive despawns it on the first invalid frame, which is how a
    // hand-drawn pedestrian came to exist for 68 of 960 steps. Waiting is what keeps it on
    // screen for the approach.
    const speed = options.speeds.pedestrian_mps;
    const toLaneEdge = Math.max(toKerb + VERGE_M - halfLane(sample.lane), 0);
    const intoTheRoad = arrival === null ? null : Math.max(arrival - toLaneEdge / speed, 0);
    const dwell =
      intoTheRoad === null ? PEDESTRIAN_DWELL_S : Math.min(PEDESTRIAN_DWELL_S, intoTheRoad);
    const actor: DrawnActor = {
      name,
      kind,
      path,
      speed_mps: speed,
      start_delay_s: delay(toLaneEdge / speed + dwell),
      waits: [{ at_m: 0, seconds: round1(dwell) }],
    };
    if (options.crossings) actor.crossing_width_m = options.crossingWidthM;
    return actor;
  }

  if (kind === "cyclist") {
    const length =
      CYCLIST_PATH_MIN_M + random() * (CYCLIST_PATH_MAX_M - CYCLIST_PATH_MIN_M);
    // Along the lane, a little kerbward of its centre, in the direction of travel.
    const offset = Math.max(toKerb - 0.8, 0.3);
    const start = toKerbside(sample, offset);
    const end = offsetMetres(start, sample.east * length, sample.north * length);
    return {
      name,
      kind,
      path: [start, end],
      speed_mps: options.speeds.cyclist_mps,
      start_delay_s: delay(CYCLIST_LEAD_S),
    };
  }

  // A cone or a barrier: just outside the kerb edge, squared to the road. Static tracks are
  // valid for the whole episode, so there is nothing to time.
  return {
    name,
    kind,
    position: toKerbside(sample, toKerb + KERB_CLEARANCE_M),
    heading_rad: round4(Math.atan2(sample.north, sample.east)),
  };
}

function round1(value: number): number {
  return Math.round(value * 10) / 10;
}

function round4(value: number): number {
  return Math.round(value * 10000) / 10000;
}
