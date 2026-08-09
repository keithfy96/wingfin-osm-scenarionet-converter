// Review decision store. Deliberately DOM-free so the rules below are testable
// without a browser; rendering subscribes to changes.

import type {
  Decision,
  DecisionStatus,
  Finding,
  MigrationSummary,
  Readiness,
  ReviewIdentity,
  ReviewPayload,
  ReviewSubmission,
} from "./types.js";

export class DecisionError extends Error {}

/** A decision is only complete when its status carries what that status requires. */
export function validateDecision(
  input: {
    status: DecisionStatus;
    value?: unknown;
    reason?: string;
  },
  finding?: Finding,
): void {
  if (input.status === "not_applicable") {
    const reason = (input.reason ?? "").trim();
    if (!reason) {
      throw new DecisionError("Marking a finding not applicable requires a reason.");
    }
  }
  if (input.status === "overridden" && input.value === undefined) {
    throw new DecisionError("An override must carry a replacement value.");
  }
  if (input.status === "ignored" && finding?.severity === "blocker") {
    // Ignoring is for findings that do not gate promotion. Allowing it here would
    // let a review reach Stage 4 with the very findings the gate exists for marked
    // as handled. No reason is required for a warning: friction defeats the point.
    throw new DecisionError(
      "A blocking finding cannot be ignored; decide it, override it, or mark it not applicable.",
    );
  }
}

export class ReviewState {
  readonly identity: ReviewIdentity;
  readonly findings: Finding[];

  private readonly byId = new Map<string, Finding>();
  private readonly decisions = new Map<string, Decision>();
  private readonly listeners = new Set<() => void>();
  private readonly clock: () => string;

  constructor(payload: ReviewPayload, clock: () => string = () => new Date().toISOString()) {
    this.identity = payload.identity;
    this.findings = payload.findings;
    this.clock = clock;
    for (const finding of payload.findings) {
      this.byId.set(finding.identifier, finding);
    }
  }

  subscribe(listener: () => void): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  private emit(): void {
    for (const listener of this.listeners) listener();
  }

  finding(findingId: string): Finding | undefined {
    return this.byId.get(findingId);
  }

  decision(findingId: string): Decision | undefined {
    return this.decisions.get(findingId);
  }

  statusOf(findingId: string): DecisionStatus {
    return this.decisions.get(findingId)?.status ?? "unresolved";
  }

  allDecisions(): Decision[] {
    // Sorted so an exported submission is byte-stable for the same decisions.
    return [...this.decisions.values()].sort((a, b) => a.finding_id.localeCompare(b.finding_id));
  }

  decide(
    findingId: string,
    input: { status: DecisionStatus; value?: unknown; reason?: string },
  ): void {
    const finding = this.byId.get(findingId);
    if (!finding) throw new DecisionError(`Unknown finding ${findingId}`);
    validateDecision(input, finding);

    if (input.status === "unresolved") {
      this.decisions.delete(findingId);
      this.emit();
      return;
    }
    const decision: Decision = {
      finding_id: findingId,
      rule: finding.rule,
      status: input.status,
      decided_at: this.clock(),
      evidence_checksum: finding.evidence_checksum,
      // Copied from the finding so an exported review can be lined up against a GPS
      // track on its own, without preliminary.json beside it.
      location: finding.location,
      source_type: finding.source_type,
      source_ids: finding.source_ids,
    };
    if (input.value !== undefined) decision.value = input.value;
    if (input.reason !== undefined && input.reason.trim()) decision.reason = input.reason.trim();
    this.decisions.set(findingId, decision);
    this.emit();
  }

  /**
   * Apply one disposition across a rule + road-class cohort.
   *
   * The plan allows bulk decisions only for findings sharing the same rule and
   * road class, and still requires each affected feature to be serialized
   * explicitly — so this writes individual decision records, not a group record.
   */
  decideBulk(
    scope: { rule: string; roadClass?: string | null },
    input: { status: DecisionStatus; value?: unknown; reason?: string },
  ): string[] {
    // Omitting roadClass means every road class — that is how a whole rule is set
    // aside at once. Passing `null` still means the unclassified ones only, so the
    // narrower road-class cohorts keep working.
    const anyRoadClass = !("roadClass" in scope);
    const targets = this.findings.filter(
      (finding) =>
        finding.rule === scope.rule &&
        (anyRoadClass || finding.road_class === scope.roadClass),
    );
    for (const finding of targets) validateDecision(input, finding);
    for (const finding of targets) {
      this.decide(finding.identifier, input);
    }
    return targets.map((finding) => finding.identifier);
  }

  readiness(): Readiness {
    const blockers = this.findings.filter((finding) => finding.severity === "blocker");
    const blockersUnresolved = blockers.filter(
      (finding) => this.statusOf(finding.identifier) === "unresolved",
    ).length;
    // Ignored is counted apart from resolved, so "decided" keeps meaning judged.
    // Reporting them together would overstate how much of the map has been reviewed.
    const statuses = this.findings.map((finding) => this.statusOf(finding.identifier));
    return {
      total: this.findings.length,
      resolved: statuses.filter((status) => status !== "unresolved" && status !== "ignored")
        .length,
      ignored: statuses.filter((status) => status === "ignored").length,
      blockers_total: blockers.length,
      blockers_unresolved: blockersUnresolved,
      ready: blockersUnresolved === 0,
    };
  }

  /**
   * The review as an exportable submission, finished or not.
   *
   * An unfinished review exports with `readiness.ready` false. Refusing to write the
   * file at all was a second gate on top of Stage 4, which is the authoritative
   * promotion gate and already rejects unresolved blockers; all the extra strictness
   * bought was stranding a reviewer's work in browser storage.
   */
  toSubmission(): ReviewSubmission {
    const readiness = this.readiness();
    return {
      submission_version: 3,
      exported_at: this.clock(),
      identity: this.identity,
      decisions: this.allDecisions(),
      readiness,
    };
  }

  /**
   * Load prior decisions.
   *
   * Never restores silently across a regeneration: a decision carries over only
   * when its finding still exists *and* its evidence checksum is unchanged.
   * Everything else returns to unresolved and is reported for the summary.
   */
  loadDecisions(decisions: Decision[]): MigrationSummary {
    const summary: MigrationSummary = {
      carried: 0,
      invalidated: 0,
      unknown: 0,
      invalidated_ids: [],
    };
    for (const decision of decisions) {
      const finding = this.byId.get(decision.finding_id);
      if (!finding) {
        summary.unknown += 1;
        continue;
      }
      if (finding.evidence_checksum !== decision.evidence_checksum) {
        summary.invalidated += 1;
        summary.invalidated_ids.push(decision.finding_id);
        continue;
      }
      if (decision.status === "ignored" && finding.severity === "blocker") {
        // The file is not the authority on what may be ignored. A hand-edited or
        // stale review must not be able to set a blocker aside by loading it, so it
        // is dropped here rather than at the parser, which has no severities.
        summary.invalidated += 1;
        summary.invalidated_ids.push(decision.finding_id);
        continue;
      }
      this.decisions.set(decision.finding_id, { ...decision, rule: finding.rule });
      summary.carried += 1;
    }
    this.emit();
    return summary;
  }

  reset(): void {
    this.decisions.clear();
    this.emit();
  }
}
