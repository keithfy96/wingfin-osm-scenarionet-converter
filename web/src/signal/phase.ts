// The colour a phase group shows at a given moment, and the strip that draws a whole cycle.
//
// This is the browser half of `signal_plan.colour_at`. The two must agree exactly: the page
// is where the plan is judged and the converter is what writes it, so a disagreement about
// the sign of the offset would put a red light in the pickle where the page drew a green
// one. `web/test/signal/phase.test.ts` pins the cases that would catch it.

import type { PhaseGroup } from "./types.js";

export const GREEN = "#2f9e44";
export const YELLOW = "#f59f00";
export const RED = "#c92a2a";

/** The offset is when green starts, measured from the top of the cycle. */
export function colourAt(
  seconds: number,
  group: Pick<PhaseGroup, "green_seconds" | "yellow_seconds" | "offset_seconds">,
  cycleSeconds: number,
): string {
  const phase = (((seconds - group.offset_seconds) % cycleSeconds) + cycleSeconds) % cycleSeconds;
  if (phase < group.green_seconds) return GREEN;
  if (phase < group.green_seconds + group.yellow_seconds) return YELLOW;
  return RED;
}

/** One cycle as a CSS gradient with hard stops - the plan, at a glance.
 *
 * Built from boundaries rather than by sampling, so a two-second yellow is drawn at its real
 * width instead of being lost between samples.
 */
export function phaseStripCss(group: PhaseGroup, cycleSeconds: number): string {
  const edges = [0, cycleSeconds];
  for (const boundary of [
    group.offset_seconds,
    group.offset_seconds + group.green_seconds,
    group.offset_seconds + group.green_seconds + group.yellow_seconds,
  ]) {
    edges.push(((boundary % cycleSeconds) + cycleSeconds) % cycleSeconds);
  }
  const sorted = [...new Set(edges)].sort((a, b) => a - b);

  const parts: string[] = [];
  let from: number | undefined;
  for (const to of sorted) {
    if (from !== undefined && to > from) {
      // Sampled at the middle of each band, which cannot land on a boundary.
      const colour = colourAt((from + to) / 2, group, cycleSeconds);
      const start = ((from / cycleSeconds) * 100).toFixed(3);
      const end = ((to / cycleSeconds) * 100).toFixed(3);
      parts.push(`${colour} ${start}% ${end}%`);
    }
    from = to;
  }
  return `linear-gradient(to right, ${parts.join(", ")})`;
}
