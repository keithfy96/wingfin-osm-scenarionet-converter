// The review side panel: filters, the finding queue, and the structured controls
// that turn a finding into a decision.

import { controlFor, supportsBulk, type OverrideField } from "./controls.js";
import type { FeatureIndex } from "./details.js";
import { chip, definitionRow, element } from "./dom.js";
import { DecisionError, type ReviewState } from "./state.js";
import type { DecisionStatus, Finding, ReviewPayload } from "./types.js";

/** Order the queue puts findings in: hardest judgement first, defaults last. */
const REVIEW_PRIORITY: Record<string, number> = {
  turn_permission_geometry_conflict: 0,
  ambiguous_connector: 1,
  restriction_effect_review: 2,
  signal_lane_association: 3,
  lane_transition_count_mismatch: 4,
  inferred_stop_line: 5,
  lane_count_inference: 6,
  lane_width_default: 7,
  speed_default: 8,
};

const STATUS_LABEL: Record<DecisionStatus, string> = {
  unresolved: "Unresolved",
  accepted: "Accepted",
  overridden: "Overridden",
  not_applicable: "Not applicable",
};

export function sortFindings(findings: Finding[]): Finding[] {
  return [...findings].sort(
    (a, b) =>
      (REVIEW_PRIORITY[a.rule] ?? 99) - (REVIEW_PRIORITY[b.rule] ?? 99) ||
      a.rule.localeCompare(b.rule) ||
      a.identifier.localeCompare(b.identifier),
  );
}

export interface Filters {
  search: string;
  rule: string;
  severity: string;
  status: string;
}

export function applyFilters(findings: Finding[], filters: Filters, statusOf: (id: string) => DecisionStatus): Finding[] {
  const needle = filters.search.trim().toLowerCase();
  return findings.filter((finding) => {
    if (filters.rule && finding.rule !== filters.rule) return false;
    if (filters.severity && finding.severity !== filters.severity) return false;
    if (filters.status && statusOf(finding.identifier) !== filters.status) return false;
    if (!needle) return true;
    const haystack = [
      finding.rule,
      finding.reason,
      finding.identifier,
      ...finding.source_ids,
      ...finding.affected_feature_ids,
    ]
      .join(" ")
      .toLowerCase();
    return haystack.includes(needle);
  });
}

function fieldInput(field: OverrideField, laneOptions: string[]): HTMLElement {
  if (field.kind === "number") {
    const input = element("input");
    input.type = "number";
    input.dataset.key = field.key;
    if (field.min !== undefined) input.min = String(field.min);
    if (field.max !== undefined) input.max = String(field.max);
    if (field.step !== undefined) input.step = String(field.step);
    return input;
  }
  if (field.kind === "choice") {
    const select = element("select");
    select.dataset.key = field.key;
    for (const option of field.options) {
      const node = element("option", undefined, option.label);
      node.value = option.value;
      select.append(node);
    }
    return select;
  }
  if (field.kind === "lanes") {
    const select = element("select");
    select.dataset.key = field.key;
    select.multiple = true;
    select.size = Math.min(6, Math.max(3, laneOptions.length));
    for (const laneId of laneOptions) {
      const node = element("option", undefined, laneId);
      node.value = laneId;
      select.append(node);
    }
    return select;
  }
  const input = element("input");
  input.type = "text";
  input.dataset.key = field.key;
  if (field.placeholder) input.placeholder = field.placeholder;
  return input;
}

function readFields(container: HTMLElement, fields: OverrideField[]): Record<string, unknown> {
  const value: Record<string, unknown> = {};
  for (const field of fields) {
    const node = container.querySelector<HTMLInputElement | HTMLSelectElement>(
      `[data-key="${field.key}"]`,
    );
    if (!node) continue;
    if (field.kind === "number") {
      const raw = (node as HTMLInputElement).value.trim();
      if (raw !== "") value[field.key] = Number(raw);
      continue;
    }
    if (field.kind === "lanes") {
      const selected = [...(node as HTMLSelectElement).selectedOptions].map((option) => option.value);
      if (selected.length) value[field.key] = selected;
      continue;
    }
    const raw = node.value.trim();
    if (raw !== "") value[field.key] = raw;
  }
  return value;
}

export interface PanelHooks {
  /** Light up everything a finding names and pan to it. Returns layers painted. */
  onFocus(finding: Finding): number;
  /** Light up one feature the reviewer named by id or clicked in a chip. */
  onFocusFeature(identifier: string): void;
  onChanged(): void;
}

export class ReviewPanel {
  private filters: Filters = { search: "", rule: "", severity: "", status: "" };
  private selectedId: string | null = null;
  private focusedId: string | null = null;
  private mappedLayers = 0;
  private readonly sorted: Finding[];
  private readonly laneIds: string[];

  constructor(
    private readonly root: HTMLElement,
    private readonly state: ReviewState,
    payload: ReviewPayload,
    private readonly index: FeatureIndex,
    private readonly hooks: PanelHooks,
  ) {
    this.sorted = sortFindings(payload.findings);
    this.laneIds = payload.lanes.map((lane) => lane.identifier);
    this.renderShell(payload);
    this.state.subscribe(() => this.renderQueue());
    this.renderQueue();
  }

  /** Open a finding from outside the queue — a map popup naming it, say. */
  select(findingId: string): void {
    const finding = this.state.finding(findingId);
    if (!finding) return;
    this.selectedId = findingId;
    this.mappedLayers = this.hooks.onFocus(finding);
    this.renderQueue();
    this.root.querySelector("#detail")?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  private renderShell(payload: ReviewPayload): void {
    this.root.innerHTML = "";
    const header = element("header");
    header.append(element("h1", undefined, "Stage 3 review"));
    header.append(
      element(
        "p",
        "muted",
        `${payload.identity.generator_version} · fingerprint ${payload.identity.generation_fingerprint.slice(0, 12)}`,
      ),
    );
    this.root.append(header);

    const readiness = element("div", "readiness");
    readiness.id = "readiness";
    this.root.append(readiness);

    const filters = element("div", "filters");
    const search = element("input");
    search.id = "filter-search";
    search.placeholder = "Search rule or reason; paste a lane, way or node id to focus it";
    search.addEventListener("input", () => {
      this.filters.search = search.value;
      // A pasted id is a request to look at that thing, not to filter by its text.
      // Both happen: the queue narrows and the map jumps to what was named.
      const hit = this.index.resolve(search.value);
      if (hit && hit !== this.focusedId) {
        this.focusedId = hit;
        this.hooks.onFocusFeature(hit);
      } else if (!hit) {
        this.focusedId = null;
      }
      this.renderQueue();
    });

    const rules = [...new Set(payload.findings.map((finding) => finding.rule))].sort();
    const ruleSelect = element("select");
    ruleSelect.id = "filter-rule";
    ruleSelect.append(new Option("All rules", ""));
    for (const rule of rules) ruleSelect.append(new Option(rule, rule));
    ruleSelect.addEventListener("change", () => {
      this.filters.rule = ruleSelect.value;
      this.renderQueue();
    });

    const severity = element("select");
    severity.append(new Option("All severities", ""), new Option("Blocker", "blocker"), new Option("Warning", "warning"));
    severity.addEventListener("change", () => {
      this.filters.severity = severity.value;
      this.renderQueue();
    });

    const status = element("select");
    status.append(
      new Option("Any state", ""),
      new Option("Unresolved", "unresolved"),
      new Option("Accepted", "accepted"),
      new Option("Overridden", "overridden"),
      new Option("Not applicable", "not_applicable"),
    );
    status.addEventListener("change", () => {
      this.filters.status = status.value;
      this.renderQueue();
    });

    filters.append(search, ruleSelect, severity, status);
    this.root.append(filters);

    const note = element("p", "queue-note muted");
    note.id = "queue-note";
    this.root.append(note);

    const bulk = element("div", "bulk");
    bulk.id = "bulk";
    this.root.append(bulk);

    const queue = element("div", "queue");
    queue.id = "queue";
    this.root.append(queue);

    const detail = element("div", "detail");
    detail.id = "detail";
    this.root.append(detail);

    this.root.append(this.renderLegend());
  }

  /** What the map colours mean. Without it every band is just a coloured line. */
  private renderLegend(): HTMLElement {
    const box = element("details", "legend-box");
    box.append(element("summary", undefined, "Legend"));
    const legend = element("div", "legend");
    const entries: [string, string][] = [
      ["swatch selected", "Selected or searched geometry"],
      ["swatch lane", "Lane centreline"],
      ["swatch lane-direction", "Direction of travel (arrow points downstream)"],
      ["swatch connector-band", "Connector band — the lane-width path a movement takes"],
      ["swatch active", "Allowed movement"],
      ["swatch review", "Movement needing review"],
      ["swatch forbidden", "Forbidden movement"],
      ["swatch stop-line", "Inferred stop line"],
      ["swatch source", "Source OSM way or node (dashed)"],
    ];
    for (const [className, label] of entries) {
      legend.append(element("span", className), element("span", undefined, label));
    }
    box.append(legend);
    box.append(
      element(
        "p",
        "muted",
        "Queue rows are edged by decision state: orange unresolved, green accepted, " +
          "blue overridden, grey not applicable.",
      ),
    );
    return box;
  }

  private renderReadiness(): void {
    const node = this.root.querySelector<HTMLElement>("#readiness");
    if (!node) return;
    const readiness = this.state.readiness();
    node.className = `readiness ${readiness.ready ? "ready" : "blocked"}`;
    node.innerHTML = "";
    node.append(
      element("strong", undefined, readiness.ready ? "Ready to export" : "Export blocked"),
      element(
        "span",
        "muted",
        `${readiness.resolved}/${readiness.total} findings decided · ` +
          `${readiness.blockers_unresolved} of ${readiness.blockers_total} blockers unresolved`,
      ),
    );
  }

  private renderBulk(visible: Finding[]): void {
    const node = this.root.querySelector<HTMLElement>("#bulk");
    if (!node) return;
    node.innerHTML = "";
    // Bulk is offered only when the visible set is already one rule and one road
    // class, so a reviewer cannot sweep a decision across unlike features.
    const rules = new Set(visible.map((finding) => finding.rule));
    const classes = new Set(visible.map((finding) => finding.road_class));
    if (visible.length < 2 || rules.size !== 1 || classes.size !== 1) return;
    const rule = [...rules][0] as string;
    if (!supportsBulk(rule)) return;
    const roadClass = [...classes][0] ?? null;

    node.append(
      element("span", "muted", `${visible.length} × ${rule} · ${roadClass ?? "unclassified"}`),
    );
    const accept = element("button", "primary", "Accept all shown");
    accept.addEventListener("click", () => {
      this.state.decideBulk({ rule, roadClass }, { status: "accepted" });
      this.hooks.onChanged();
    });
    node.append(accept);
  }

  /** Where a finding is, in the words a reviewer uses: which lane, at which node. */
  private whereLabel(finding: Finding): string {
    const drawn = [...new Set([...finding.affected_feature_ids, ...finding.geometry_ids])].filter(
      (id) => this.index.has(id),
    );
    const primary = drawn[0];
    if (!primary) {
      return `${finding.source_type} ${finding.source_ids.join(", ") || "unlocated"}`;
    }
    const others = drawn.length - 1;
    const more = others > 0 ? ` +${others} more` : "";
    // A movement already names the node it happens at, so the usual suffix would
    // repeat it. Everything else needs telling where it is.
    const movement = this.index.describeConnector(primary);
    if (movement) return `${movement}${more}`;
    const at = finding.source_ids.length
      ? ` at ${finding.source_type} ${finding.source_ids.join(", ")}`
      : "";
    return `${this.index.describe(primary)}${more}${at}`;
  }

  private renderNote(visible: number): void {
    const node = this.root.querySelector<HTMLElement>("#queue-note");
    if (!node) return;
    const focused = this.focusedId
      ? `${this.index.describe(this.focusedId)} focused on the map. `
      : "";
    node.textContent = `${focused}${visible} finding(s) match these filters.`;
  }

  private renderQueue(): void {
    this.renderReadiness();
    const queue = this.root.querySelector<HTMLElement>("#queue");
    if (!queue) return;
    const visible = applyFilters(this.sorted, this.filters, (id) => this.state.statusOf(id));
    this.renderBulk(visible);
    this.renderNote(visible.length);

    queue.innerHTML = "";
    if (!visible.length) {
      queue.append(element("p", "muted", "No findings match these filters."));
      this.renderDetail();
      return;
    }
    for (const finding of visible.slice(0, 400)) {
      const status = this.state.statusOf(finding.identifier);
      const selected = finding.identifier === this.selectedId ? " selected" : "";
      const row = element("button", `row ${status} ${finding.severity}${selected}`);
      row.append(element("span", "row-rule", finding.rule));
      row.append(element("span", "row-status", STATUS_LABEL[status]));
      // Findings of the same rule share a reason verbatim, so the row has to name
      // the lane it is about or the queue reads as a list of duplicates.
      row.append(element("span", "row-where", this.whereLabel(finding)));
      row.append(element("span", "row-reason muted", finding.reason));
      row.addEventListener("click", () => {
        this.selectedId = finding.identifier;
        this.mappedLayers = this.hooks.onFocus(finding);
        this.renderQueue();
      });
      queue.append(row);
    }
    if (visible.length > 400) {
      queue.append(element("p", "muted", `${visible.length - 400} more; narrow the filters to see them.`));
    }
    this.renderDetail();
  }

  /**
   * A row of ids the reviewer can click to put on the map. `resolve` is how a raw
   * id in the finding maps to a drawn feature — an OSM way id is `way:<id>` there.
   */
  private chipRow(identifiers: string[], resolve?: (raw: string) => string | null): HTMLElement {
    const row = element("span", "chip-row");
    if (!identifiers.length) {
      row.append(element("span", "muted", "none"));
      return row;
    }
    for (const identifier of identifiers) {
      const key = resolve ? resolve(identifier) : identifier;
      const drawn = key !== null && this.index.has(key);
      const label = drawn && key !== null ? this.index.label(key) : identifier;
      row.append(
        chip(label, drawn && key !== null ? () => this.hooks.onFocusFeature(key) : undefined),
      );
    }
    return row;
  }

  private renderDetail(): void {
    const node = this.root.querySelector<HTMLElement>("#detail");
    if (!node) return;
    node.innerHTML = "";
    if (!this.selectedId) {
      node.append(element("p", "muted", "Select a finding to see its evidence and record a decision."));
      return;
    }
    const finding = this.state.finding(this.selectedId);
    if (!finding) return;
    const spec = controlFor(finding);
    const status = this.state.statusOf(finding.identifier);

    node.append(element("h2", undefined, finding.rule));
    node.append(element("p", "question", spec.question));
    node.append(element("p", "muted", finding.reason));

    const evidence = element("dl", "evidence");
    definitionRow(evidence, "Severity", finding.severity);
    definitionRow(evidence, "Confidence", finding.confidence);
    definitionRow(evidence, "Source", this.chipRow(finding.source_ids, (raw) => this.index.resolve(raw)));
    definitionRow(
      evidence,
      "Affects",
      this.chipRow([...new Set([...finding.affected_feature_ids, ...finding.geometry_ids])]),
    );
    definitionRow(evidence, "Proposed", JSON.stringify(finding.proposed_value));
    definitionRow(evidence, "State", STATUS_LABEL[status]);
    node.append(evidence);

    if (!this.mappedLayers) {
      // Silence here would read as "nothing is wrong on the map"; it means the
      // opposite — the reviewer is being asked about geometry that was not drawn.
      node.append(
        element(
          "p",
          "warning",
          "No geometry on the map could be matched to this finding — decide it from the evidence above.",
        ),
      );
    }

    const form = element("div", "controls");
    const fieldWrap = element("div", "fields");
    for (const field of spec.fields) {
      const label = element("label");
      const unit = field.kind === "number" ? field.unit : undefined;
      label.append(element("span", undefined, unit ? `${field.label} (${unit})` : field.label));
      label.append(fieldInput(field, this.laneIds));
      fieldWrap.append(label);
    }
    if (spec.fields.length) form.append(fieldWrap);

    const error = element("p", "error");
    const decide = (input: { status: DecisionStatus; value?: unknown; reason?: string }): void => {
      try {
        error.textContent = "";
        this.state.decide(finding.identifier, input);
        this.hooks.onChanged();
      } catch (caught) {
        error.textContent = caught instanceof DecisionError ? caught.message : String(caught);
      }
    };

    const accept = element("button", "primary", spec.acceptLabel);
    accept.addEventListener("click", () => decide({ status: "accepted" }));
    form.append(accept);

    if (spec.overrideLabel) {
      const override = element("button", undefined, spec.overrideLabel);
      override.addEventListener("click", () => {
        const value = spec.fields.length ? readFields(fieldWrap, spec.fields) : { accepted: false };
        decide({ status: "overridden", value });
      });
      form.append(override);
    }

    const reason = element("input");
    reason.type = "text";
    reason.placeholder = "Reason (required to mark not applicable)";
    const notApplicable = element("button", undefined, "Not applicable");
    notApplicable.addEventListener("click", () =>
      decide({ status: "not_applicable", reason: reason.value }),
    );

    const clear = element("button", "ghost", "Clear decision");
    clear.addEventListener("click", () => decide({ status: "unresolved" }));

    form.append(reason, notApplicable, clear, error);
    node.append(form);
  }
}
