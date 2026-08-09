import { describe, expect, it } from "vitest";

import { applyFilters, sortFindings, type Filters } from "../src/panel.js";
import type { DecisionStatus } from "../src/types.js";
import { finding } from "./fixtures.js";

const NONE: Filters = { search: "", rule: "", severity: "", status: "" };

function statuses(map: Record<string, DecisionStatus>) {
  return (id: string): DecisionStatus => map[id] ?? "unresolved";
}

describe("filtering the queue", () => {
  const findings = [
    finding({ identifier: "kept", severity: "warning" }),
    finding({ identifier: "set-aside", severity: "warning" }),
  ];
  const statusOf = statuses({ "set-aside": "ignored" });

  it("hides ignored findings, which is the point of ignoring them", () => {
    const visible = applyFilters(findings, NONE, statusOf);
    expect(visible.map((item) => item.identifier)).toEqual(["kept"]);
  });

  it("brings every one of them back when the Ignored state is selected", () => {
    // Hidden, never gone: a reviewer must be able to see what they set aside.
    const visible = applyFilters(findings, { ...NONE, status: "ignored" }, statusOf);
    expect(visible.map((item) => item.identifier)).toEqual(["set-aside"]);
  });

  it("keeps an ignored finding hidden even when another filter would match it", () => {
    // Searching for it by rule must not resurrect it; only the state filter does.
    const visible = applyFilters(findings, { ...NONE, rule: "speed_default" }, statusOf);
    expect(visible.map((item) => item.identifier)).toEqual(["kept"]);
  });

  it("orders the queue by how hard the judgement is, not alphabetically", () => {
    const ordered = sortFindings([
      finding({ identifier: "a", rule: "speed_default" }),
      finding({ identifier: "b", rule: "ambiguous_connector" }),
    ]);
    expect(ordered.map((item) => item.rule)).toEqual(["ambiguous_connector", "speed_default"]);
  });
});
