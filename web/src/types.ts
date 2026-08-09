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

/** A WGS84 position. Named keys so a GPS join cannot silently transpose them. */
export interface GeoPoint {
  lat: number;
  lon: number;
}

/** One OSM feature a finding came from, with its geometry copied in. */
export interface FindingSource {
  /** `way:<id>` or `node:<id>`. */
  ref: string;
  coordinates: GeoPoint[];
}

/** Where a finding is, for matching against externally recorded positions. */
export interface FindingLocation {
  coordinate_system: "EPSG:4326";
  lat: number;
  lon: number;
  /** [min_lon, min_lat, max_lon, max_lat] — a way-scoped finding has real extent. */
  bbox: number[];
  sources: FindingSource[];
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
  /** Generated features this finding maps to, resolved to ids actually drawn. */
  geometry_ids: string[];
  /** Source OSM geometry, as `way:<id>` / `node:<id>` keys that are drawn. */
  source_geometry_ids: string[];
  proposed_value: Record<string, unknown>;
  evidence_checksum: string;
  /** Road class of the first affected lane, for bulk scoping. Null when unknown. */
  road_class: string | null;
  /** Null when the source is a graph edge, which has no OSM geometry to resolve. */
  location: FindingLocation | null;
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
  /**
   * Copied from the finding so an exported review stands on its own against a GPS
   * track, without needing preliminary.json beside it. Absent in files exported
   * before submission_version 2.
   */
  location?: FindingLocation | null;
  source_type?: string;
  source_ids?: string[];
}

export interface Readiness {
  total: number;
  resolved: number;
  blockers_total: number;
  blockers_unresolved: number;
  ready: boolean;
}

export interface ReviewSubmission {
  /** 2 adds per-decision location; a version 1 file is one without it. */
  submission_version: 1 | 2;
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
