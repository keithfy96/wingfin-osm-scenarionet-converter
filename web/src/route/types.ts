// The payload the Stage 6 route builder is handed, and the file it exports.

/** One lane, as `lane_payload.build_lane_payload` writes it. */
export interface RouteLane {
  id: string;
  ways: string[];
  short: string;
  label: string;
  /** Leaflet order: [lat, lon]. */
  line: [number, number][];
  /** Junction movements out of this lane, already resolved from connector ids. */
  exits: string[];
  /** Lanes alongside this one that a car may move across into. */
  sideways: string[];
}

/** Binds a routes file to the lane model it was drawn on. */
export interface RouteIdentity {
  generation_fingerprint: string;
  reviewed_lane_model_sha256: string;
}

/** The path across one junction, as `ego_route` splices it into a route. */
export interface RouteConnector {
  from: string;
  to: string;
  line: [number, number][];
}

/** A step from one lane to another, `[from, to]`. */
export type LanePair = [string, string];

export interface RouteBuilderPayload {
  lanes: RouteLane[];
  connectors: RouteConnector[];
  /**
   * The steps that cross a junction, decided by `ego_route.junction_crossings` and carried
   * rather than worked out here. Having a connector is not the same question: a road
   * running straight through a junction is a plain continuation with no connector, and
   * most such cases turn on `source_edge`, which no lane entry carries. Optional only so a
   * page generated before this field existed still loads.
   */
  crossings?: LanePair[];
  ways: { id: string; lanes: number }[];
  center: [number, number];
  bounds: [[number, number], [number, number]] | null;
  identity: RouteIdentity;
  routes_version: number;
  /** Filename the CLI's `--routes` example names, so the two cannot disagree. */
  suggested_filename: string;
}

export interface ChosenRoute {
  name: string;
  start_lane: string;
  end_lane: string;
}

export interface RoutesFile {
  routes_version: number;
  identity: RouteIdentity;
  routes: ChosenRoute[];
}
