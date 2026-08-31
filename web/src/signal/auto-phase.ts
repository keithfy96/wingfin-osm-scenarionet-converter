// Re-time the phase groups a person drew so that no two conflicting ones are ever green
// together.
//
// This is the one thing on this page that decides something, and it is worth being exact
// about how little it decides. It does **not** touch which lanes belong to which group -
// that is the judgement about the junction, and it stays with the person. It takes the
// groups as drawn, reads the list of places their traffic meets, and works out when each
// one may run.
//
// The conflict list is already the whole input. `findConflicts` returns every meeting
// whatever its current overlap, and computes `overlapSeconds` only *after* a pair is found -
// so the list is a plain graph over group names, independent of the timings being replaced.
// Colour that graph and every colour is a set of groups that can run together; give each
// colour its own slice of the shared cycle and nothing that meets is ever green at once.

import type { Conflict } from "./conflicts.js";
import type { PhaseGroup } from "./types.js";

export class AutoPhaseError extends Error {}

export interface Stage {
  /** Group names, sorted, so a note reads the same twice. */
  members: string[];
  offsetSeconds: number;
  lengthSeconds: number;
}

export interface AutoPhase {
  /** The re-timed groups, in the order they were handed in. */
  groups: PhaseGroup[];
  stages: Stage[];
  /** Every green that had to give way to its stage, so the page can say which and by how much. */
  shortened: { name: string; was: number; now: number }[];
}

/** Offsets and greens land on a tenth of a second, which is what the panel shows. */
function tenth(value: number): number {
  return Math.round(value * 10) / 10;
}

/** When each group may be green, so that no two that meet ever are.
 *
 * Rewrites offsets, and shortens a green only where it cannot fit its stage. Yellows and the
 * cycle are never touched, and **a green is never made longer** - one deliberately shortened
 * stays short even when its stage would allow more.
 *
 * Throws `AutoPhaseError` rather than returning a half-answer: with nothing to resolve, or
 * with a yellow that fills a whole stage on its own, there is no plan to hand back and
 * quietly returning the input would read as success.
 */
export function autoPhase(
  groups: readonly PhaseGroup[],
  cycleSeconds: number,
  conflicts: readonly Conflict[],
): AutoPhase {
  if (groups.length < 2) {
    throw new AutoPhaseError("There is nothing to re-time until there are two phase groups.");
  }
  const known = new Set(groups.map((group) => group.name));
  const meets = new Map<string, Set<string>>(groups.map((group) => [group.name, new Set()]));
  let edges = 0;
  for (const conflict of conflicts) {
    if (!known.has(conflict.a) || !known.has(conflict.b)) continue;
    if (!meets.get(conflict.a)!.has(conflict.b)) edges += 1;
    meets.get(conflict.a)!.add(conflict.b);
    meets.get(conflict.b)!.add(conflict.a);
  }
  if (edges === 0) {
    throw new AutoPhaseError("No two groups' traffic meets, so any timing is already safe.");
  }

  // Welsh-Powell: hardest first, then the lowest colour no neighbour already holds. The name
  // tie-break is not cosmetic - the same plan has to give the same answer twice, or undoing
  // and clicking again lands somewhere else.
  const order = [...groups].sort(
    (left, right) =>
      meets.get(right.name)!.size - meets.get(left.name)!.size ||
      left.name.localeCompare(right.name),
  );
  const colour = new Map<string, number>();
  for (const group of order) {
    const taken = new Set<number>();
    for (const neighbour of meets.get(group.name)!) {
      const had = colour.get(neighbour);
      if (had !== undefined) taken.add(had);
    }
    let pick = 0;
    while (taken.has(pick)) pick += 1;
    colour.set(group.name, pick);
  }

  const lanesIn = new Map(groups.map((group) => [group.name, group.lanes.length]));
  const byColour = new Map<number, string[]>();
  for (const [name, index] of colour) {
    const found = byColour.get(index);
    if (found) found.push(name);
    else byColour.set(index, [name]);
  }
  // The busiest set of movements leads the cycle, which is what a real controller does and
  // what puts a dual carriageway ahead of the side road it is crossed by.
  const ordered = [...byColour.entries()].sort(
    (left, right) =>
      right[1].reduce((sum, name) => sum + lanesIn.get(name)!, 0) -
        left[1].reduce((sum, name) => sum + lanesIn.get(name)!, 0) || left[0] - right[0],
  );

  const count = ordered.length;
  const starts = ordered.map((_, index) => tenth((index * cycleSeconds) / count));
  // Each stage ends where the next begins. Deriving the greens from the *rounded* starts and
  // not from `cycleSeconds / count` is what keeps a seven-stage split from rounding its way
  // back into the overlap this exists to remove.
  const ends = starts.slice(1).concat(cycleSeconds);

  const stageOf = new Map<string, number>();
  const stages: Stage[] = ordered.map(([, members], index) => {
    for (const name of members) stageOf.set(name, index);
    return {
      members: [...members].sort((left, right) => left.localeCompare(right)),
      offsetSeconds: starts[index]!,
      lengthSeconds: tenth(ends[index]! - starts[index]!),
    };
  });

  const shortened: { name: string; was: number; now: number }[] = [];
  const timed = groups.map((group) => {
    const index = stageOf.get(group.name)!;
    const room = tenth(ends[index]! - starts[index]! - group.yellow_seconds);
    if (room <= 0) {
      throw new AutoPhaseError(
        `${group.name}'s ${group.yellow_seconds.toFixed(1)} s yellow fills a ` +
          `${tenth(ends[index]! - starts[index]!).toFixed(1)} s stage on its own. Lengthen the ` +
          "cycle, or shorten the yellow.",
      );
    }
    const green = Math.min(group.green_seconds, room);
    if (green < group.green_seconds) {
      shortened.push({ name: group.name, was: group.green_seconds, now: green });
    }
    return { ...group, green_seconds: green, offset_seconds: starts[index]! };
  });

  return { groups: timed, stages, shortened };
}
