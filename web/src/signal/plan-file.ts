// Building and validating the signals.json this page exports.
//
// MetaDrive never reads this file. It is an exchange between the browser and
// `osm-scenario convert --signals`, which turns a set of lane ids and three numbers into
// the traffic lights in the pickle - the same arrangement the route builder uses for
// routes.json, and Stage 3 for review.json.
//
// Every check here is also made in `signal_plan.read_signal_plan`. That duplication is
// deliberate: the converter must refuse a bad plan whatever wrote it, and the page must be
// able to say why while the plan is still on screen.

import { metresBetween } from "../geo.js";
import type {
  IdentityProblem,
  PhaseGroup,
  SignalIdentity,
  SignalsFile,
  SignalsInspection,
} from "./types.js";

export class SignalsFileError extends Error {}

/** Below this a light has not meaningfully moved: it is the projection wobble between the
 *  lane model's local metres and the WGS84 the page is handed, not a lane in a new place. */
export const MOVED_M = 0.5;

/** Matches `signal_plan._GROUP_NAME`. */
export const GROUP_NAME = /^[A-Za-z0-9][A-Za-z0-9-]{0,39}$/;

export function nameProblem(name: string, taken: Iterable<string>): string | null {
  if (!GROUP_NAME.test(name)) {
    return "Use 1-40 letters, digits or hyphens.";
  }
  for (const existing of taken) {
    if (existing === name) return `There is already a phase group called ${name}.`;
  }
  return null;
}

/** Why this group's timing does not fit the cycle, or null.
 *
 * Red is the remainder rather than a number of its own, so green plus yellow is the only
 * thing that can overrun. An all-red gap between two arms is expressed by giving both a
 * green shorter than their share - which is how a real plan expresses it too.
 */
export function timingProblem(
  group: Pick<PhaseGroup, "green_seconds" | "yellow_seconds" | "offset_seconds">,
  cycleSeconds: number,
): string | null {
  const values = [group.green_seconds, group.yellow_seconds, group.offset_seconds];
  if (values.some((value) => !Number.isFinite(value))) return "Green, yellow and offset must be numbers.";
  if (group.green_seconds < 0 || group.yellow_seconds < 0 || group.offset_seconds < 0) {
    return "Green, yellow and offset cannot be negative.";
  }
  if (group.green_seconds + group.yellow_seconds > cycleSeconds) {
    return `Green plus yellow is ${(group.green_seconds + group.yellow_seconds).toFixed(
      1,
    )} s, longer than the ${cycleSeconds.toFixed(1)} s cycle.`;
  }
  return null;
}

export function serializeSignals(
  identity: SignalIdentity,
  cycleSeconds: number,
  groups: PhaseGroup[],
  version: number,
  drawnAt: Record<string, [number, number]>,
): string {
  const file: SignalsFile = {
    signals_version: version,
    identity,
    cycle_seconds: cycleSeconds,
    groups,
    drawn_at: drawnAt,
  };
  return `${JSON.stringify(file, null, 2)}\n`;
}

/** Everything except the identity check.
 *
 * Split out so the page can look inside a plan drawn on another generation of the map and say
 * what is wrong with it, instead of only that something is. What is checked here is fatal on
 * *any* map and so throws either way: a file that is not JSON, a version this page does not
 * read, a cycle that is not a number, a group with no name, two greens on one lane. Only a
 * lane the map does not have is a question of *which* map, and only that is collectable.
 */
function readPlan(
  raw: string,
  version: number,
  knownLanes: ReadonlySet<string>,
  strict: boolean,
): { cycleSeconds: number; groups: PhaseGroup[]; missing: { group: string; lane: string }[] } {
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    throw new SignalsFileError("File is not valid JSON.");
  }
  const candidate = parsed as Partial<SignalsFile>;
  if (!candidate || typeof candidate !== "object") {
    throw new SignalsFileError("File is not a signal plan.");
  }
  if (candidate.signals_version !== version) {
    throw new SignalsFileError(
      `Unsupported signals_version ${String(candidate.signals_version)}; this page reads ${version}.`,
    );
  }
  const cycleSeconds = candidate.cycle_seconds;
  if (typeof cycleSeconds !== "number" || !Number.isFinite(cycleSeconds) || cycleSeconds <= 0) {
    throw new SignalsFileError("File has no usable cycle_seconds.");
  }
  if (!Array.isArray(candidate.groups) || candidate.groups.length === 0) {
    throw new SignalsFileError("File has no phase groups.");
  }

  const groups: PhaseGroup[] = [];
  const missing: { group: string; lane: string }[] = [];
  const names = new Set<string>();
  const claimed = new Map<string, string>();
  for (const [index, entry] of candidate.groups.entries()) {
    const group = entry as Partial<PhaseGroup>;
    if (typeof group.name !== "string" || !Array.isArray(group.lanes)) {
      throw new SignalsFileError(`Phase group ${index} is missing a name or its lanes.`);
    }
    const named = nameProblem(group.name, names);
    if (named) throw new SignalsFileError(`Phase group ${index}: ${named}`);
    names.add(group.name);

    if (group.lanes.length === 0) {
      throw new SignalsFileError(`Phase group ${group.name} signals no lanes.`);
    }
    const lanes: string[] = [];
    for (const lane of group.lanes) {
      if (typeof lane !== "string" || !knownLanes.has(lane)) {
        if (strict) {
          throw new SignalsFileError(
            `Phase group ${group.name} signals ${String(lane)}, which is not a lane on this map.`,
          );
        }
        missing.push({ group: group.name, lane: String(lane) });
        continue;
      }
      // Two greens on one lane is not a plan any controller could carry out, and it would
      // reach MetaDrive as two lights on one key with the second silently winning. Fatal on
      // every map, so it throws even when the rest is only being inspected.
      const owner = claimed.get(lane);
      if (owner !== undefined) {
        throw new SignalsFileError(
          `Lane ${lane} is in both ${owner} and ${group.name}; a lane can only show one colour at a time.`,
        );
      }
      claimed.set(lane, group.name);
      lanes.push(lane);
    }

    const timing = {
      green_seconds: Number(group.green_seconds),
      yellow_seconds: Number(group.yellow_seconds),
      offset_seconds: Number(group.offset_seconds ?? 0),
    };
    const problem = timingProblem(timing, cycleSeconds);
    if (problem) throw new SignalsFileError(`Phase group ${group.name}: ${problem}`);

    groups.push({ name: group.name, lanes, ...timing });
  }
  return { cycleSeconds, groups, missing };
}

/** Read a signal plan back, refusing one drawn on a different lane model.
 *
 * Lane ids are content addressed, so applying a stale plan does not fail loudly: it either
 * names lanes that no longer exist or, worse, names lanes that now sit somewhere else and
 * puts a red light across the wrong road. `inspectSignals` is the path that lets a person
 * look at one of those and decide; this one is the flat refusal, and stays that way.
 */
export function parseSignals(
  raw: string,
  identity: SignalIdentity,
  version: number,
  knownLanes: ReadonlySet<string>,
): { cycleSeconds: number; groups: PhaseGroup[] } {
  for (const problem of identityProblems(raw, identity)) {
    throw new SignalsFileError(problem.message);
  }
  const { cycleSeconds, groups } = readPlan(raw, version, knownLanes, true);
  return { cycleSeconds, groups };
}

/** How the file's identity differs from this page's, if it can be read at all. */
function identityProblems(raw: string, identity: SignalIdentity): IdentityProblem[] {
  let found: Partial<SignalIdentity> | undefined;
  try {
    found = (JSON.parse(raw) as Partial<SignalsFile>)?.identity;
  } catch {
    return [];  // `readPlan` gives the better message for unreadable JSON.
  }
  if (!found || typeof found !== "object") {
    return [
      {
        field: "identity",
        was: "absent",
        now: "required",
        message: "File has no identity block, so it cannot be checked.",
      },
    ];
  }
  const problems: IdentityProblem[] = [];
  if (found.generation_fingerprint !== identity.generation_fingerprint) {
    problems.push({
      field: "generation",
      was: String(found.generation_fingerprint),
      now: identity.generation_fingerprint,
      message:
        "This plan was drawn on a different generation of the map. Place the lights again on this one.",
    });
  }
  if (found.reviewed_lane_model_sha256 !== identity.reviewed_lane_model_sha256) {
    problems.push({
      field: "lane model",
      was: String(found.reviewed_lane_model_sha256),
      now: identity.reviewed_lane_model_sha256,
      message:
        "This plan was drawn on a lane model that has since been re-reviewed. Place the lights again.",
    });
  }
  return problems;
}

/** Look inside a plan without demanding it belong to this map.
 *
 * The fingerprint is noisier than it looks: a full Stage 1 rerun mints a new one even over a
 * byte-identical `map.osm`, because osmnx stamps a build timestamp into the graphml it writes
 * and that checksum feeds `generation_fingerprint`. So a plan is refused far more often than
 * the map has actually changed, and re-placing every light by hand is the cost.
 *
 * What the refusal is protecting is still real. A lane id is
 * `deterministic_id("lane", *way_ids, u, v, key, lane_index)` and carries **no `lane_count`
 * and no geometry**, so a re-review that turns a two-lane road into three keeps `idx0` and
 * `idx1` under the same ids while moving them across the carriageway. That is why this
 * reports rather than waves through, and why `drawn_at` exists: with it, "the map changed"
 * becomes "this light has moved 4.2 m", which is a thing a person can judge.
 */
export function inspectSignals(
  raw: string,
  identity: SignalIdentity,
  version: number,
  lanes: ReadonlyMap<string, [number, number] | null>,
): SignalsInspection {
  const known = new Set(lanes.keys());
  const { cycleSeconds, groups, missing } = readPlan(raw, version, known, false);
  const kept = groups.filter((group) => group.lanes.length > 0);
  const droppedGroups = groups.filter((group) => group.lanes.length === 0).map((g) => g.name);

  const recorded = (JSON.parse(raw) as Partial<SignalsFile>).drawn_at;
  const drawnAt = recorded ?? {};
  const movedLanes: { lane: string; metres: number }[] = [];
  for (const group of kept) {
    for (const lane of group.lanes) {
      const before = drawnAt[lane];
      const now = lanes.get(lane);
      if (!before || !now) continue;
      const metres = metresBetween(before, now);
      if (metres > MOVED_M) movedLanes.push({ lane, metres });
    }
  }
  movedLanes.sort((a, b) => b.metres - a.metres);

  return {
    plan: { cycleSeconds, groups: kept },
    identityProblems: identityProblems(raw, identity),
    missingLanes: missing,
    movedLanes,
    droppedGroups,
    records: recorded !== undefined,
  };
}
