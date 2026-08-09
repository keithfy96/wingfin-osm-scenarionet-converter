// Draft autosave used to live here. It is gone: the review UI now starts empty
// every time, and decisions only ever enter it by importing a review.json.
//
// What remains is the demolition. Earlier builds wrote drafts into localStorage
// keyed `osm-scenario.review.draft|<workspace>|<source>|<fingerprint>`, and those
// entries survive the code that wrote them. Nothing reads them any more, but
// "nothing is persisted" has to be true of the browser, not merely of the code
// path, so they are deleted on boot.
//
// This module can be dropped once no browser can still be holding one.

const PREFIX = "osm-scenario.review.draft";

/** Minimal slice of the Storage API, so tests can pass a plain object. */
export interface DraftStore {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
  /** Enumeration, for finding drafts left behind by an earlier build. */
  readonly length: number;
  key(index: number): string | null;
}

/**
 * Delete every draft an earlier build left in this browser, whatever workspace or
 * generation it belonged to. Returns how many were removed, so boot can say so
 * rather than cleaning up silently.
 */
export function purgeLegacyDrafts(store: DraftStore): number {
  // Collect first: removing during enumeration reindexes the store underneath us.
  const stale: string[] = [];
  for (let index = 0; index < store.length; index += 1) {
    const key = store.key(index);
    if (key !== null && key.startsWith(PREFIX)) stale.push(key);
  }
  for (const key of stale) store.removeItem(key);
  return stale.length;
}
