// Declarative description of the structured control each finding rule offers.
//
// The reviewer selects or overrides an interpretation; they never draw geometry.
// Keeping this as data rather than per-rule DOM code means an unknown rule still
// gets accept / not-applicable rather than an unreviewable finding.

import type { Finding } from "./types.js";

export interface NumberField {
  kind: "number";
  key: string;
  label: string;
  unit?: string;
  min?: number;
  max?: number;
  step?: number;
}

export interface ChoiceField {
  kind: "choice";
  key: string;
  label: string;
  options: { value: string; label: string }[];
}

export interface TextField {
  kind: "text";
  key: string;
  label: string;
  placeholder?: string;
}

export interface LanePickerField {
  kind: "lanes";
  key: string;
  label: string;
}

export type OverrideField = NumberField | ChoiceField | TextField | LanePickerField;

export interface ControlSpec {
  /** Verb on the accept button, phrased for the rule. */
  acceptLabel: string;
  /** Verb on the override button. Null when the rule offers no override. */
  overrideLabel: string | null;
  /** Fields collected when overriding. Empty means the override is a bare flag. */
  fields: OverrideField[];
  /** One line telling the reviewer what they are actually judging. */
  question: string;
  /**
   * What accepting does to the reviewed map, in one sentence.
   *
   * These state the Stage 4 contract in `docs/implementation-plan/README.md`: which
   * decisions are written into `review/reviewed.osm` as tags and which stay non-OSM
   * overrides in `review/applied-decisions.json`.
   *
   * `apply-review` applies the non-OSM ones, plus `lane_count_inference` and
   * `turn_permission_geometry_conflict`, the two tag writes that have landed. It still
   * *refuses* any other override whose effect is an OSM tag, naming the rule. So the
   * remaining `overrideEffect` strings describe the intended contract rather than what
   * runs now — keep them in step with `_OSM_NATIVE_RULES` in
   * `src/osm_scenario/apply_review.py` as each is implemented.
   */
  acceptEffect: string;
  /** What overriding does. Null exactly when `overrideLabel` is null. */
  overrideEffect: string | null;
}

const TURN_OPTIONS = [
  { value: "through", label: "through" },
  { value: "left", label: "left" },
  { value: "right", label: "right" },
  { value: "slight_left", label: "slight left" },
  { value: "slight_right", label: "slight right" },
  { value: "reverse", label: "reverse / U-turn" },
];

const SPECS: Record<string, ControlSpec> = {
  speed_default: {
    question: "Is the configured default speed right for this road?",
    acceptLabel: "Accept default",
    overrideLabel: "Set speed",
    acceptEffect: "Leaves the way untagged; lanes keep the configured default speed.",
    overrideEffect: "Writes maxspeed onto the way in reviewed.osm and regenerates from it.",
    fields: [{ kind: "number", key: "maxspeed_kph", label: "Speed limit", unit: "km/h", min: 5, max: 140, step: 5 }],
  },
  lane_width_default: {
    question: "Is the configured default lane width right for this road?",
    acceptLabel: "Accept default",
    overrideLabel: "Set width",
    acceptEffect: "Leaves the way untagged; lanes keep the configured default width.",
    overrideEffect: "Writes width onto the way in reviewed.osm; lane polygons are redrawn.",
    fields: [{ kind: "number", key: "width_m", label: "Lane width", unit: "m", min: 1.5, max: 6, step: 0.1 }],
  },
  lane_count_inference: {
    question: "How many lanes does this way carry, in the direction this finding names?",
    acceptLabel: "Accept inferred count",
    overrideLabel: "Set lane count",
    acceptEffect: "Keeps the inferred count; the same lanes are generated for this way.",
    overrideEffect:
      "Writes lanes (one-way) or lanes:<direction> into reviewed.osm; the way is re-laned.",
    // One field, not three. The finding is already about a single direction — it carries
    // `proposed_value.direction` — so `lanes` / `lanes:forward` / `lanes:backward` offered
    // three ways to state one number and two ways to contradict both the other fields and
    // the finding itself. Stage 4 picks the tag from the way's own oneway-ness.
    fields: [{ kind: "number", key: "lane_count", label: "Lanes this direction", min: 1, max: 12, step: 1 }],
  },
  lane_transition_count_mismatch: {
    question: "Do these approach lanes really all feed the same destination lane?",
    acceptLabel: "Accept mapping",
    overrideLabel: "Set outgoing lanes",
    acceptEffect:
      "Keeps the mapping: every highlighted approach lane feeds the one destination lane.",
    overrideEffect: "Writes the outgoing lane count into reviewed.osm; the mapping is redone.",
    fields: [{ kind: "number", key: "outgoing_lanes", label: "Outgoing lanes", min: 1, max: 12, step: 1 }],
  },
  ambiguous_connector: {
    question:
      "Should this movement exist in the map — is the turn physically possible and legally allowed?",
    acceptLabel: "Keep this movement",
    overrideLabel: "Remove this movement",
    acceptEffect:
      "Keeps the movement as an allowed connection: a vehicle may make this turn.",
    overrideEffect:
      "Removes the movement: nothing connects these two lanes and no vehicle makes this turn.",
    fields: [],
  },
  turn_permission_geometry_conflict: {
    question: "The turn tag and the measured angle disagree — which one is right?",
    acceptLabel: "Keep restored movement",
    overrideLabel: "Set movement",
    acceptEffect: "Keeps the movement class the generator restored rather than the tag.",
    overrideEffect: "Writes the corrected turn:lanes into reviewed.osm; movements are reclassified.",
    fields: [{ kind: "choice", key: "movement", label: "Movement", options: TURN_OPTIONS }],
  },
  signal_lane_association: {
    // Not "approaching lanes": where the extract was cut at the signal there is no
    // approach in this map, and the generator associates the lanes it releases instead.
    question: "Which lanes does this signal govern?",
    acceptLabel: "Accept association",
    overrideLabel: "Choose lanes",
    acceptEffect: "Keeps the generated association; the reviewed model carries it.",
    overrideEffect: "Associates the signal with these lanes in the regenerated model.",
    fields: [{ kind: "lanes", key: "lane_ids", label: "Governed lanes" }],
  },
  inferred_stop_line: {
    question: "Is the inferred stop line in the right place?",
    acceptLabel: "Accept stop line",
    overrideLabel: null,
    acceptEffect: "Keeps the inferred placement; kept as an override in review.json.",
    overrideEffect: null,
    fields: [],
  },
  restriction_effect_review: {
    question: "Is this turn restriction correct as mapped?",
    acceptLabel: "Keep restriction",
    overrideLabel: "Correct or remove",
    acceptEffect: "Keeps the restriction as mapped; the movements it forbids stay forbidden.",
    overrideEffect: "Corrects or removes the restriction relation in reviewed.osm.",
    fields: [
      {
        kind: "choice",
        key: "disposition",
        label: "Action",
        options: [
          { value: "corrected", label: "Correct it" },
          { value: "removed", label: "Remove it" },
        ],
      },
      { kind: "text", key: "note", label: "What changes", placeholder: "e.g. applies to the kerbside lane only" },
    ],
  },
  restriction_enforced_leg: {
    question: "This restriction was applied by removing one movement of its route. Is that the right one?",
    acceptLabel: "Keep it",
    overrideLabel: null,
    acceptEffect: "Keeps the movement removed. Nothing else on the route changes.",
    overrideEffect: null,
    fields: [],
  },
  movement_served_by_link_bypass: {
    question: "An off-ramp before this junction already carries this turn, so the junction does not. Is that right?",
    acceptLabel: "Keep it removed",
    overrideLabel: null,
    acceptEffect: "Keeps the turn off the junction. Traffic making it uses the ramp instead.",
    overrideEffect: null,
    fields: [],
  },
};

const FALLBACK: ControlSpec = {
  question: "Is this proposal correct?",
  acceptLabel: "Accept proposal",
  overrideLabel: null,
  acceptEffect: "Keeps the generator's proposal for this finding.",
  overrideEffect: null,
  fields: [],
};

/** What the two rule-independent buttons do. Neither changes any geometry. */
export const NOT_APPLICABLE_EFFECT =
  "Says the finding itself is wrong, not the value. The reason is kept in the audit record.";
export const CLEAR_EFFECT = "Returns this finding to unresolved, which blocks export.";
/** Warnings only — a blocking finding cannot be set aside. */
export const IGNORE_EFFECT =
  "Sets it aside unjudged: the generator's proposal stands, and the record says " +
  "nobody decided it. Hidden from the queue until you filter for Ignored.";

export function controlFor(finding: Finding): ControlSpec {
  return SPECS[finding.rule] ?? FALLBACK;
}

/** Rules whose findings may be dispositioned in bulk across one road class. */
export function supportsBulk(rule: string): boolean {
  return rule === "speed_default" || rule === "lane_width_default";
}
