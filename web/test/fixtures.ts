import type { Finding, ReviewIdentity, ReviewPayload } from "../src/types.js";

export const IDENTITY: ReviewIdentity = {
  workspace: "junction-1",
  source_checksum: "source-aaa",
  generation_fingerprint: "fingerprint-aaa",
  generator_version: "direct-osm-stage2-v12",
  lane_model_schema_version: 2,
  configuration_checksum: "config-aaa",
  generated_at: "2026-08-08T00:00:00+00:00",
};

export function finding(overrides: Partial<Finding> = {}): Finding {
  return {
    identifier: "f1",
    rule: "speed_default",
    severity: "warning",
    confidence: "medium",
    reason: "no explicit maxspeed",
    source_type: "way",
    source_ids: ["776021091"],
    affected_feature_ids: ["lane-a"],
    proposed_value: { maxspeed_kph: 50 },
    evidence_checksum: "evidence-1",
    road_class: "secondary",
    ...overrides,
  };
}

export function payload(findings: Finding[]): ReviewPayload {
  return {
    payload_version: 1,
    identity: IDENTITY,
    center: [101.6, 3.18],
    features: [],
    findings,
    lanes: [],
    connectors: [],
    counts: {},
  };
}

/** In-memory stand-in for window.localStorage. */
export function memoryStore(): {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
} {
  const data = new Map<string, string>();
  return {
    getItem: (key) => data.get(key) ?? null,
    setItem: (key, value) => void data.set(key, value),
    removeItem: (key) => void data.delete(key),
  };
}
