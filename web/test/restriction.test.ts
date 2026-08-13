import { describe, expect, it } from "vitest";

import { restrictionNotes, type RestrictionContext } from "../src/restriction.js";
import type { RestrictionSummary } from "../src/types.js";
import { finding } from "./fixtures.js";

/**
 * mosque relation 18555951: a via-way `no_u_turn` the generator could not enforce,
 * holding the three right turns out of way 935525165 at node 1983979095. The same
 * mapper wrote 18555952 in the same changeset — a node-via `no_right_turn` naming
 * exactly those three movements, which enforced and removed them.
 */
const HELD = ["ac87ae8f1860a4ca", "b3ed36dbb7a2629f", "8e3e49baca54937f"];

function heldFinding(overrides: Record<string, unknown> = {}) {
  return finding({
    rule: "restriction_effect_review",
    severity: "blocker",
    source_type: "relation",
    source_ids: ["18555951"],
    proposed_value: {
      restriction: "no_u_turn",
      status: "review_required",
      from_way_ids: ["859423756"],
      via_member_ids: ["935525165"],
      to_way_ids: ["1173001827"],
      forbidden_connector_ids: [],
      held_connector_ids: HELD,
      ...overrides,
    },
  });
}

const NODE_RELATION: RestrictionSummary = {
  identifier: "restriction-1",
  source_relation_id: "18555952",
  restriction: "no_right_turn",
  status: "enforced",
  forbidden_connector_ids: HELD,
};

function context(overrides: Partial<RestrictionContext> = {}): RestrictionContext {
  return {
    statusOf: (id) => (HELD.includes(id) ? "forbidden" : undefined),
    isDrawn: () => true,
    restrictions: [NODE_RELATION],
    ...overrides,
  };
}

describe("explaining a restriction finding", () => {
  it("says nothing at all about any other rule", () => {
    expect(restrictionNotes(finding({ rule: "ambiguous_connector" }), context())).toEqual([]);
  });

  it("says the restriction forbade nothing, because an empty list does not", () => {
    // The whole misreading this exists to stop: `forbidden_connector_ids: []` inside the
    // Proposed blob looks like an omission, and the reviewer concludes that accepting is
    // what bans the turn.
    const notes = restrictionNotes(heldFinding(), context()).join(" ");
    expect(notes).toContain("removed no movements");
    expect(notes).toContain("Accepting does not forbid anything");
  });

  it("names the other relation that already forbade the movements", () => {
    const notes = restrictionNotes(heldFinding(), context()).join(" ");
    expect(notes).toContain("All 3 movements it names are already forbidden");
    expect(notes).toContain("relation 18555952 (no_right_turn)");
    expect(notes).toContain("no vehicle makes them");
  });

  it("never cites the finding's own relation as the one that forbade them", () => {
    // It is asking precisely because it forbade nothing; naming itself would be circular
    // and would read as "this is already handled" when nothing has handled it.
    const circular: RestrictionSummary = { ...NODE_RELATION, source_relation_id: "18555951" };
    const notes = restrictionNotes(heldFinding(), context({ restrictions: [circular] })).join(" ");
    expect(notes).toContain("already forbidden");
    expect(notes).not.toContain("relation 18555951");
  });

  it("still reports the movements are gone when no relation in the payload explains it", () => {
    // An older payload carries no restrictions. The state is still the decisive fact.
    const notes = restrictionNotes(heldFinding(), context({ restrictions: [] })).join(" ");
    expect(notes).toContain("already forbidden, so no vehicle makes them");
  });

  it("counts the movements that are still live separately from the ones that are gone", () => {
    const notes = restrictionNotes(
      heldFinding(),
      context({
        statusOf: (id) =>
          id === HELD[0] ? "forbidden" : id === HELD[1] ? "review_required" : "active",
      }),
    ).join(" ");
    expect(notes).toContain("1 of 3 movements it names is already forbidden");
    expect(notes).toContain("1 of the movements it names is held for review");
    expect(notes).toContain("1 of the movements it names is still active");
  });

  it("explains a relation whose route was never downloaded", () => {
    // mosque rel 15336555: via and to ways are outside the extract, so the map shows one
    // lone highlighted way and no movements — which looks like a broken page.
    const outside = finding({
      rule: "restriction_effect_review",
      severity: "blocker",
      source_type: "relation",
      source_ids: ["15336555"],
      proposed_value: {
        restriction: "no_u_turn",
        status: "review_required",
        from_way_ids: ["756118317"],
        via_member_ids: ["776369869"],
        to_way_ids: ["776369868"],
        forbidden_connector_ids: [],
      },
    });
    const notes = restrictionNotes(
      outside,
      context({ isDrawn: (id) => id === "756118317", restrictions: [] }),
    ).join(" ");
    expect(notes).toContain("Members 776369869, 776369868");
    expect(notes).toContain("not in this extract");
    expect(notes).toContain("nothing to remove");
    // The other reason a restriction forbids nothing must not be asserted here: this one
    // forbade nothing because its route is absent, and the finding's own reason — printed
    // directly above these notes — says exactly that.
    expect(notes).toContain("could not have: the route is not in this map");
    expect(notes).not.toContain("traffic the relation does not name");
  });

  it("says nothing about missing members when every member is on the map", () => {
    const notes = restrictionNotes(heldFinding(), context()).join(" ");
    expect(notes).not.toContain("not in this extract");
  });
});
