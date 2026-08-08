// The review side panel: filters, the finding queue, and the structured controls
// that turn a finding into a decision.

import { controlFor, supportsBulk, type OverrideField } from "./controls.js";
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

function element<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  className?: string,
  text?: string,
): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
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
  onFocus(finding: Finding): void;
  onChanged(): void;
}

export class ReviewPanel {
  private filters: Filters = { search: "", rule: "", severity: "", status: "" };
  private selectedId: string | null = null;
  private readonly sorted: Finding[];
  private readonly laneIds: string[];

  constructor(
    private readonly root: HTMLElement,
    private readonly state: ReviewState,
    payload: ReviewPayload,
    private readonly hooks: PanelHooks,
  ) {
    this.sorted = sortFindings(payload.findings);
    this.laneIds = payload.lanes.map((lane) => lane.identifier);
    this.renderShell(payload);
    this.state.subscribe(() => this.renderQueue());
    this.renderQueue();
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
    search.placeholder = "Search rule, reason, lane, way or node id";
    search.addEventListener("input", () => {
      this.filters.search = search.value;
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

    const bulk = element("div", "bulk");
    bulk.id = "bulk";
    this.root.append(bulk);

    const queue = element("div", "queue");
    queue.id = "queue";
    this.root.append(queue);

    const detail = element("div", "detail");
    detail.id = "detail";
    this.root.append(detail);
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

  private renderQueue(): void {
    this.renderReadiness();
    const queue = this.root.querySelector<HTMLElement>("#queue");
    if (!queue) return;
    const visible = applyFilters(this.sorted, this.filters, (id) => this.state.statusOf(id));
    this.renderBulk(visible);

    queue.innerHTML = "";
    if (!visible.length) {
      queue.append(element("p", "muted", "No findings match these filters."));
      this.renderDetail();
      return;
    }
    for (const finding of visible.slice(0, 400)) {
      const status = this.state.statusOf(finding.identifier);
      const row = element("button", `row ${status} ${finding.severity}`);
      row.append(element("span", "row-rule", finding.rule));
      row.append(element("span", "row-status", STATUS_LABEL[status]));
      row.append(element("span", "row-reason muted", finding.reason));
      row.addEventListener("click", () => {
        this.selectedId = finding.identifier;
        this.hooks.onFocus(finding);
        this.renderDetail();
      });
      queue.append(row);
    }
    if (visible.length > 400) {
      queue.append(element("p", "muted", `${visible.length - 400} more; narrow the filters to see them.`));
    }
    this.renderDetail();
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
    const rows: [string, string][] = [
      ["Severity", finding.severity],
      ["Confidence", finding.confidence],
      ["Source", `${finding.source_type} ${finding.source_ids.join(", ")}`],
      ["Affects", finding.affected_feature_ids.join(", ")],
      ["Proposed", JSON.stringify(finding.proposed_value)],
      ["State", STATUS_LABEL[status]],
    ];
    for (const [label, value] of rows) {
      evidence.append(element("dt", undefined, label), element("dd", undefined, value));
    }
    node.append(evidence);

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
