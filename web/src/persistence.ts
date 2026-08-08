// Draft autosave. Browser-local drafts are explicitly non-authoritative: only an
// exported review.json may be passed to Stage 4. This exists so an accidental tab
// close does not lose in-progress work.

import type { Decision, ReviewIdentity } from "./types.js";

const PREFIX = "osm-scenario.review.draft";

/** Minimal slice of the Storage API, so tests can pass a plain object. */
export interface DraftStore {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

export interface Draft {
  saved_at: string;
  identity: ReviewIdentity;
  decisions: Decision[];
}

/**
 * Key on all three identity components. A different source checksum or
 * generation fingerprint is a different map, and its draft must not be offered
 * against this one.
 */
export function draftKey(identity: ReviewIdentity): string {
  return [
    PREFIX,
    identity.workspace,
    identity.source_checksum,
    identity.generation_fingerprint,
  ].join("|");
}

export function saveDraft(
  store: DraftStore,
  identity: ReviewIdentity,
  decisions: Decision[],
  now: () => string = () => new Date().toISOString(),
): Draft {
  const draft: Draft = { saved_at: now(), identity, decisions };
  store.setItem(draftKey(identity), JSON.stringify(draft));
  return draft;
}

export function loadDraft(store: DraftStore, identity: ReviewIdentity): Draft | null {
  const raw = store.getItem(draftKey(identity));
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as Draft;
    if (!parsed || !Array.isArray(parsed.decisions)) return null;
    return parsed;
  } catch {
    // A corrupt draft must not block the review; drop it and start clean.
    return null;
  }
}

export function clearDraft(store: DraftStore, identity: ReviewIdentity): void {
  store.removeItem(draftKey(identity));
}

/** Coalesce rapid decision changes into one write. */
export function debounce<T extends (...args: never[]) => void>(fn: T, ms: number): T {
  let handle: ReturnType<typeof setTimeout> | undefined;
  return ((...args: never[]) => {
    if (handle !== undefined) clearTimeout(handle);
    handle = setTimeout(() => fn(...args), ms);
  }) as T;
}
