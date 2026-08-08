// Contract with the Python renderer (src/osm_scenario/review.py). Any change here
// must change the payload builder and its test in the same commit.

/** Identity a review is bound to. A review is only valid against one of these. */
export interface ReviewIdentity {
  workspace: string;
  source_checksum: string;
  generation_fingerprint: string;
  generator_version: string;
  lane_model_schema_version: number;
  configuration_checksum: string;
  generated_at: string;
}

export interface Finding {
  identifier: string;
  rule: string;
  severity: "blocker" | "warning";
  confidence: string;
  reason: string;
  source_type: string;
  source_ids: string[];
  affected_feature_ids: string[];
  proposed_value: Record<string, unknown>;
  evidence_checksum: string;
  /** Road class of the first affected lane, for bulk scoping. Null when unknown. */
  road_class: string | null;
}

export interface LaneSummary {
  identifier: string;
  source_way_ids: string[];
  lane_index: number;
  lane_count: number;
  direction: string;
  road_class: string;
  width_m: number;
  speed_limit_kph: number | null;
  turn_permissions: string[];
  entry_lanes: string[];
  exit_lanes: string[];
}

export interface ConnectorSummary {
  identifier: string;
  junction_node_id: string;
  from_lane_id: string;
  to_lane_id: string;
  from_way_id: string;
  to_way_id: string;
  movement: string;
  turn_angle_degrees: number;
  status: "active" | "review_required" | "forbidden";
}

export interface GeoJsonFeature {
  type: "Feature";
  geometry: { type: string; coordinates: unknown };
  properties: Record<string, unknown>;
}

export interface ReviewPayload {
  payload_version: 1;
  identity: ReviewIdentity;
  center: [number, number];
  features: GeoJsonFeature[];
  findings: Finding[];
  lanes: LaneSummary[];
  connectors: ConnectorSummary[];
  counts: Record<string, number>;
}

/**
 * Terminal states the plan allows for a blocking finding. `unresolved` prevents
 * export; every blocker must reach one of the other three.
 */
export type DecisionStatus = "unresolved" | "accepted" | "overridden" | "not_applicable";

export interface Decision {
  finding_id: string;
  rule: string;
  status: DecisionStatus;
  /** Present only when status is `overridden`. Shape depends on the rule. */
  value?: unknown;
  /** Required when status is `not_applicable`. */
  reason?: string;
  decided_at: string;
  /**
   * The evidence checksum of the finding this decision was made against. A
   * regenerated model whose checksum differs invalidates the decision rather
   * than silently reusing it.
   */
  evidence_checksum: string;
}

export interface Readiness {
  total: number;
  resolved: number;
  blockers_total: number;
  blockers_unresolved: number;
  ready: boolean;
}

export interface ReviewSubmission {
  submission_version: 1;
  exported_at: string;
  identity: ReviewIdentity;
  decisions: Decision[];
  readiness: Readiness;
}

export interface MigrationSummary {
  carried: number;
  invalidated: number;
  unknown: number;
  /** Finding IDs whose evidence moved, so their decision was dropped. */
  invalidated_ids: string[];
}
