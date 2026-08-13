// Explaining a `restriction_effect_review` blocker in words a reviewer can act on.
//
// This rule only ever fires when the generator *could not* apply a turn restriction:
// no single movement on the route carries only the prohibited traffic, so removing one
// would have stopped drivers the relation never named. The finding therefore forbids
// nothing, and the panel used to show that as an empty `forbidden_connector_ids` inside
// a raw JSON blob — which reads exactly like the opposite of what it means.
//
// Two facts decide it, and neither is legible without help:
//
//   * whether the movements it names are already gone. A mapper who writes a via-way
//     `no_u_turn` usually also writes the shorter node-via relation covering the same
//     turn, and that one enforces exactly. When it has, the answer is simply "the turn
//     is already forbidden by relation N" and there is nothing to weigh.
//   * whether the relation's route exists here at all. A relation whose via or to way
//     was never downloaded leaves one lone member way highlighted on the map and no
//     movements anywhere, which looks like a page fault rather than an answer.

import type { Finding, RestrictionSummary } from "./types.js";

export interface RestrictionContext {
  /** Current status of a generated movement, or undefined when it is not one. */
  statusOf(connectorId: string): string | undefined;
  /** Whether an OSM way or node id resolves to geometry drawn on this map. */
  isDrawn(osmId: string): boolean;
  restrictions: RestrictionSummary[];
}

function stringList(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

function plural(count: number, one: string, many: string): string {
  return count === 1 ? one : many;
}

/**
 * Which other relations forbade these movements.
 *
 * Excluding the finding's own relation is the point: it is asking precisely because it
 * forbade nothing, so finding it here would be circular.
 */
function forbiddenBy(
  connectorIds: string[],
  context: RestrictionContext,
  ownRelationIds: string[],
): string[] {
  const own = new Set(ownRelationIds);
  const named = new Set(connectorIds);
  const relations = context.restrictions
    .filter(
      (restriction) =>
        !own.has(restriction.source_relation_id) &&
        restriction.forbidden_connector_ids.some((id) => named.has(id)),
    )
    .map((restriction) => `${restriction.source_relation_id} (${restriction.restriction})`);
  return [...new Set(relations)].sort();
}

/**
 * Plain-English notes for a restriction finding, most decisive first. Empty for every
 * other rule, and empty when the finding says nothing beyond what the panel already shows.
 */
export function restrictionNotes(finding: Finding, context: RestrictionContext): string[] {
  if (finding.rule !== "restriction_effect_review") return [];
  const proposed = finding.proposed_value;
  const notes: string[] = [];

  // Members of the relation that were never downloaded. Checked against what is drawn
  // rather than inferred from the reason string: a way outside the extract produces no
  // feature at all, so this is a lookup, not a guess.
  const members = [
    ...stringList(proposed.from_way_ids),
    ...stringList(proposed.via_member_ids),
    ...stringList(proposed.to_way_ids),
  ];
  const missing = members.filter((id) => !context.isDrawn(id));
  if (missing.length) {
    notes.push(
      `${plural(missing.length, "Member", "Members")} ${missing.join(", ")} ` +
        `${plural(missing.length, "is", "are")} named by this relation but ` +
        `${plural(missing.length, "is", "are")} not in this extract, so the route it ` +
        "forbids does not exist here. There is nothing to remove, and the highlighted " +
        "geometry is the one member that was downloaded.",
    );
  }

  if (!stringList(proposed.forbidden_connector_ids).length) {
    notes.push(
      // Two different reasons a restriction forbids nothing, and only one of them is
      // "no movement carries only the prohibited traffic". Stating that cause over a
      // relation whose route was never downloaded contradicts the finding's own reason,
      // printed directly above this in the panel.
      missing.length
        ? "This restriction removed no movements, and could not have: the route is not " +
          "in this map. Accepting records that the relation is mapped correctly and " +
          "forbids nothing."
        : "This restriction removed no movements. The generator could not apply it " +
          "without also stopping traffic the relation does not name, so it left every " +
          "movement in place and asked you instead. Accepting does not forbid anything.",
    );
  }

  const held = stringList(proposed.held_connector_ids);
  if (held.length) {
    const byStatus = (wanted: string): string[] =>
      held.filter((id) => context.statusOf(id) === wanted);
    const gone = byStatus("forbidden");
    const waiting = byStatus("review_required");
    const active = byStatus("active");
    const of = held.length === gone.length ? `All ${held.length}` : `${gone.length} of ${held.length}`;

    if (gone.length) {
      const relations = forbiddenBy(gone, context, finding.source_ids);
      const by = relations.length ? ` by relation ${relations.join(", ")}` : "";
      notes.push(
        `${of} ${plural(held.length, "movement", "movements")} it names ` +
          `${plural(gone.length, "is", "are")} already forbidden${by}, so no vehicle makes ` +
          `${plural(gone.length, "it", "them")} whichever way you decide this.`,
      );
    }
    if (waiting.length) {
      notes.push(
        `${waiting.length} of the movements it names ${plural(waiting.length, "is", "are")} ` +
          "held for review by another finding, so they stay out of the drivable network " +
          "until that one is decided.",
      );
    }
    if (active.length) {
      notes.push(
        `${active.length} of the movements it names ${plural(active.length, "is", "are")} ` +
          `still active: a vehicle may make ${plural(active.length, "it", "them")}. Nothing ` +
          "here removes them.",
      );
    }
  }

  return notes;
}
