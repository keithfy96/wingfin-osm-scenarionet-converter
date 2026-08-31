// The payload the Stage 6 actor builder is handed, and the file it exports.

/** One lane, as `lane_payload.build_lane_payload` writes it. Drawn for context only: an
 * actor is placed by clicking the map, not by picking a lane, because it walks where no
 * lane is. */
export interface ActorLane {
  id: string;
  short: string;
  label: string;
  /** Leaflet order: [lat, lon]. */
  line: [number, number][];
}

/** Binds an actors file to the lane model it was drawn on. */
export interface ActorIdentity {
  generation_fingerprint: string;
  reviewed_lane_model_sha256: string;
}

/** The four things MetaDrive will replay. `pedestrian` and `cyclist` walk a path;
 * `cone` and `barrier` stand still. */
export type ActorKind = "pedestrian" | "cyclist" | "cone" | "barrier";

export interface ActorWait {
  at_m: number;
  seconds: number;
}

/**
 * One drawn actor, in the shape `actors.json` carries it.
 *
 * `path` and `position` are `[lat, lon]`, the order every Stage 6 page speaks, because this
 * page is a Leaflet map and the browser has no projection. `osm_scenario.actors` projects
 * them into the model's own metric CRS on the way in; nothing here has to know that CRS.
 */
export interface DrawnActor {
  name: string;
  kind: ActorKind;
  path?: [number, number][];
  speed_mps?: number;
  start_delay_s?: number;
  waits?: ActorWait[];
  crossing_width_m?: number;
  position?: [number, number];
  heading_rad?: number;
}

export interface ActorBuilderPayload {
  lanes: ActorLane[];
  center: [number, number];
  bounds: [[number, number], [number, number]] | null;
  identity: ActorIdentity;
  actors_version: number;
  /** Filename the CLI's `--actors` example names, so the two cannot disagree. */
  suggested_filename: string;
  /** Walking and riding speeds the panel starts from. Not survey - see the page copy. */
  defaults: {
    pedestrian_mps: number;
    cyclist_mps: number;
    crossing_width_m: number;
  };
}

export interface ActorsFile {
  actors_version: number;
  identity: ActorIdentity;
  actors: DrawnActor[];
}
