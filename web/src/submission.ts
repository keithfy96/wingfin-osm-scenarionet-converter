// Parsing and checksum-binding for an exported review.json.

import type { Decision, ReviewIdentity, ReviewSubmission } from "./types.js";

export class SubmissionError extends Error {}

const STATUSES = new Set([
  "unresolved",
  "accepted",
  "overridden",
  "not_applicable",
  "ignored",
]);

function assert(condition: unknown, message: string): asserts condition {
  if (!condition) throw new SubmissionError(message);
}

export function parseSubmission(raw: string): ReviewSubmission {
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    throw new SubmissionError("File is not valid JSON.");
  }
  const candidate = parsed as Partial<ReviewSubmission>;
  assert(candidate && typeof candidate === "object", "File is not a review submission.");
  // A version 1 file is a version 2 file without per-decision locations, a version 2
  // file is a version 3 file that cannot contain `ignored`, and a version 3 file is a
  // version 4 file whose decisions do not say what value they approved. Older reviews
  // still load; the version is what stops a newer one being read by older rules.
  assert(
    [1, 2, 3, 4].includes(candidate.submission_version as number),
    `Unsupported submission_version ${String(candidate.submission_version)}; expected 1, 2, 3 or 4.`,
  );
  assert(candidate.identity && typeof candidate.identity === "object", "Submission has no identity.");
  assert(Array.isArray(candidate.decisions), "Submission has no decisions array.");

  for (const [index, entry] of candidate.decisions.entries()) {
    const decision = entry as Partial<Decision>;
    assert(typeof decision.finding_id === "string", `Decision ${index} has no finding_id.`);
    assert(
      typeof decision.status === "string" && STATUSES.has(decision.status),
      `Decision ${index} has an unsupported status ${String(decision.status)}.`,
    );
    assert(
      typeof decision.evidence_checksum === "string",
      `Decision ${index} has no evidence_checksum, so it cannot be checksum-bound.`,
    );
    if (decision.status === "not_applicable") {
      assert(
        typeof decision.reason === "string" && decision.reason.trim().length > 0,
        `Decision ${index} is not_applicable without a reason.`,
      );
    }
  }
  return candidate as ReviewSubmission;
}

export type IdentityRelation = "same" | "regenerated" | "relocated" | "other-map";

/**
 * How an imported submission relates to the map now open.
 *
 * Nothing here refuses a load. What makes a decision safe to carry is its
 * evidence checksum, enforced one layer down in `ReviewState.loadDecisions`:
 * a decision lands only when this model asks a byte-identical question. A
 * finding id is built from the rule plus OSM ids plus content-hashed feature
 * ids and carries no workspace name, so the same junction mapped in two
 * extracts produces the same id — which is what makes a cross-map load useful
 * rather than merely permitted.
 *
 * The relation exists to tell the reader which of the four they are looking
 * at, because the confidence differs: `same` is their own work restored,
 * `regenerated` the same map at a moved fingerprint, `relocated` the same
 * source OSM in another workspace, `other-map` a genuinely different extract.
 */
export function compareIdentity(mine: ReviewIdentity, theirs: ReviewIdentity): IdentityRelation {
  if (mine.source_checksum !== theirs.source_checksum) return "other-map";
  if (mine.workspace !== theirs.workspace) return "relocated";
  if (mine.generation_fingerprint !== theirs.generation_fingerprint) return "regenerated";
  return "same";
}

export function serializeSubmission(submission: ReviewSubmission): string {
  return `${JSON.stringify(submission, null, 2)}\n`;
}
