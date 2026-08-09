import type { DraftStore } from "../src/persistence.js";
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
    geometry_ids: ["lane-a"],
    source_geometry_ids: ["way:776021091"],
    proposed_value: { maxspeed_kph: 50 },
    evidence_checksum: "evidence-1",
    road_class: "secondary",
    location: {
      coordinate_system: "EPSG:4326",
      lat: 3.1856,
      lon: 101.6116,
      bbox: [101.6114, 3.1855, 101.6118, 3.1858],
      sources: [
        {
          ref: "way:776021091",
          coordinates: [
            { lat: 3.1855, lon: 101.6114 },
            { lat: 3.1858, lon: 101.6118 },
          ],
        },
      ],
    },
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

/** In-memory stand-in for window.localStorage, enumeration included. */
export function memoryStore(): DraftStore {
  const data = new Map<string, string>();
  return {
    getItem: (key) => data.get(key) ?? null,
    setItem: (key, value) => void data.set(key, value),
    removeItem: (key) => void data.delete(key),
    get length() {
      return data.size;
    },
    key: (index) => [...data.keys()][index] ?? null,
  };
}
