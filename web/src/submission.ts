// Parsing and checksum-binding for an exported review.json.

import type { Decision, ReviewIdentity, ReviewSubmission } from "./types.js";

export class SubmissionError extends Error {}

const STATUSES = new Set(["unresolved", "accepted", "overridden", "not_applicable"]);

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
  assert(
    candidate.submission_version === 1,
    `Unsupported submission_version ${String(candidate.submission_version)}; expected 1.`,
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

export type IdentityRelation = "same" | "regenerated" | "foreign";

/**
 * How an imported submission relates to the map now open.
 *
 * `foreign` means a different workspace or a different source OSM — never
 * loadable. `regenerated` means the same source reviewed against an older
 * generation, which is the only case migration applies to.
 */
export function compareIdentity(mine: ReviewIdentity, theirs: ReviewIdentity): IdentityRelation {
  if (mine.workspace !== theirs.workspace) return "foreign";
  if (mine.source_checksum !== theirs.source_checksum) return "foreign";
  if (mine.generation_fingerprint !== theirs.generation_fingerprint) return "regenerated";
  return "same";
}

export function serializeSubmission(submission: ReviewSubmission): string {
  return `${JSON.stringify(submission, null, 2)}\n`;
}
