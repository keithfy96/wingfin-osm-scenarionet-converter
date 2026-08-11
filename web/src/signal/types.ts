// The payload the Stage 6 signal builder is handed, and the file it exports.

/** One lane, as `lane_payload.build_lane_payload` writes it. */
export interface SignalLane {
  id: string;
  ways: string[];
  short: string;
  label: string;
  /** Leaflet order: [lat, lon]. */
  line: [number, number][];
  exits: string[];
  sideways: string[];
}

/** The path across one junction. `junction` is the OSM node the movement passes through. */
export interface SignalConnector {
  from: string;
  to: string;
  junction: string;
  line: [number, number][];
}

/** Binds a signal plan to the lane model it was drawn on. */
export interface SignalIdentity {
  generation_fingerprint: string;
  reviewed_lane_model_sha256: string;
}

/** A `highway=traffic_signals` node the survey does have, drawn for reference.
 *
 * Never pre-selected. OSM records that a signal exists and no timing at all, and in
 * `junction-1` the one node it has sits at the edge of the extract - associated with the
 * lanes it releases rather than any it stops - so treating it as a placement would put a
 * light somewhere nobody chose.
 */
export interface SurveyedSignal {
  node: string;
  lanes: string[];
  status: string;
}

export interface SignalBuilderPayload {
  lanes: SignalLane[];
  connectors: SignalConnector[];
  ways: { id: string; lanes: number }[];
  center: [number, number];
  bounds: [[number, number], [number, number]] | null;
  identity: SignalIdentity;
  signals_version: number;
  suggested_filename: string;
  surveyed: SurveyedSignal[];
  defaults: { cycle_seconds: number; green_seconds: number; yellow_seconds: number };
}

export interface PhaseGroup {
  name: string;
  lanes: string[];
  green_seconds: number;
  yellow_seconds: number;
  offset_seconds: number;
}

export interface SignalsFile {
  signals_version: number;
  identity: SignalIdentity;
  cycle_seconds: number;
  groups: PhaseGroup[];
}
