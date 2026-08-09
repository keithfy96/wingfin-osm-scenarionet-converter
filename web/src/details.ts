// Reading a feature off the map: which lane it is, what it links to, and which
// findings name it.
//
// A reviewer's first question about highlighted geometry is "which lane is this?".
// The Stage 2 audit answers that in a popup; Stage 3 needs the same answer plus the
// decision state, so the description logic lives here and the popup, the queue and
// the detail pane all read from it rather than each inventing a label.

import { chip, definitionRow, element, pill } from "./dom.js";
import type { DecisionStatus, Finding, GeoJsonFeature, MovementRole } from "./types.js";

type Properties = Record<string, unknown>;

export interface LinkRow {
  /** The lane at the far end of the link. */
  id: string;
  movement: string;
  status: string;
  /**
   * The connector this link was generated as, when there is one. A finding about a
   * movement names the connector, never the lane, so without this the reviewer has
   * no route from a link that needs review to the blocker asking about it.
   * Continuations carry no connector and so carry no id here.
   */
  connectorId?: string;
}

/**
 * Which side of the carriageway a lane index sits on. Indices run centre-out:
 * idx0 hugs the centreline, idx(n-1) is the kerbside lane.
 */
export function laneSide(index: number, count: number): string {
  if (count <= 1) return "single lane";
  if (index === 0) return "offside";
  if (index === count - 1) return "nearside";
  return "middle";
}

/** ` way 756118314` for a lane that names one, and nothing at all otherwise. */
function wayOf(properties: Properties): string {
  const ways = properties.source_way_ids;
  const first = Array.isArray(ways) ? ways[0] : undefined;
  return typeof first === "string" && first ? ` way ${first}` : "";
}

function laneNumbers(properties: Properties): { index: number; count: number } | null {
  const index = properties.lane_index;
  const count = properties.lane_count;
  if (typeof index !== "number" || typeof count !== "number") return null;
  return { index, count };
}

function isConnector(properties: Properties): boolean {
  const kind = String(properties.kind ?? "");
  return kind === "connector" || kind === "connector_polygon";
}

export class FeatureIndex {
  private readonly properties = new Map<string, Properties>();
  private readonly connectors: Properties[] = [];
  private readonly findingsByFeature = new Map<string, Finding[]>();

  constructor(features: GeoJsonFeature[], findings: Finding[]) {
    for (const feature of features) {
      const properties = feature.properties ?? {};
      const identifier = properties.id;
      if (typeof identifier !== "string" || !identifier) continue;
      // A lane draws a polygon, a centreline and an arrow under one id, and all
      // three carry identical properties, so last-wins keeps one entry per thing.
      this.properties.set(identifier, properties);
      if (properties.kind === "connector") this.connectors.push(properties);
    }
    for (const finding of findings) {
      const named = new Set([
        ...finding.affected_feature_ids,
        ...finding.geometry_ids,
        ...finding.source_geometry_ids,
      ]);
      for (const identifier of named) {
        const existing = this.findingsByFeature.get(identifier);
        if (existing) existing.push(finding);
        else this.findingsByFeature.set(identifier, [finding]);
      }
    }
  }

  has(identifier: string): boolean {
    return this.properties.has(identifier);
  }

  propertiesOf(identifier: string): Properties | undefined {
    return this.properties.get(identifier);
  }

  findingsFor(identifier: string): Finding[] {
    return this.findingsByFeature.get(identifier) ?? [];
  }

  /** `abc123 · way 756118314 lane 2/3 middle` — how a reviewer reads a lane off the map. */
  label(identifier: string): string {
    const properties = this.properties.get(identifier);
    if (!properties) return identifier;
    const lane = laneNumbers(properties);
    if (lane) {
      // Two lanes at one node are routinely both "lane 1/2 offside" — on different ways.
      // Without the way they are indistinguishable in a list of chips.
      return `${identifier} ·${wayOf(properties)} lane ${lane.index + 1}/${lane.count} ${laneSide(lane.index, lane.count)}`;
    }
    if (isConnector(properties)) {
      return `${identifier} · ${String(properties.movement ?? "movement")}`;
    }
    return identifier;
  }

  /** The same lane, short enough to sit either side of an arrow. */
  shortLabel(identifier: string): string {
    const properties = this.properties.get(identifier);
    const lane = properties ? laneNumbers(properties) : null;
    if (!lane || !properties) return identifier;
    return `${identifier}${wayOf(properties)} lane ${lane.index + 1}/${lane.count}`;
  }

  /**
   * A connector as the movement it is: from which lane to which, how, and where.
   * Two connector findings of one rule are otherwise indistinguishable in a queue.
   */
  describeConnector(identifier: string): string | null {
    const properties = this.properties.get(identifier);
    if (!properties || !isConnector(properties)) return null;
    const angle = properties.turn_angle_degrees;
    const turn =
      typeof angle === "number" ? ` ${angle > 0 ? "+" : ""}${angle.toFixed(1)}°` : "";
    const from = this.shortLabel(String(properties.from_lane_id ?? ""));
    const to = this.shortLabel(String(properties.to_lane_id ?? ""));
    const node = String(properties.junction_node_id ?? "");
    return `${from} → ${to} · ${String(properties.movement ?? "")}${turn}${node ? ` at node ${node}` : ""}`;
  }

  /** What kind of thing an id names, in prose a reviewer can read in a sentence. */
  describe(identifier: string): string {
    const properties = this.properties.get(identifier);
    if (!properties) return identifier;
    const kind = String(properties.kind ?? "feature");
    if (kind === "source_way") return `OSM way ${String(properties.osm_way_id ?? "")}`;
    if (kind === "source_node") return `OSM node ${String(properties.osm_node_id ?? "")}`;
    if (laneNumbers(properties)) return `Lane ${this.label(identifier)}`;
    if (isConnector(properties)) return `Movement ${this.describeConnector(identifier)}`;
    return `${kind.replaceAll("_", " ")} ${identifier}`;
  }

  /**
   * Resolve an id the reviewer typed or clicked. A bare OSM id is ambiguous
   * between a way and a node, so try both namespaces before giving up.
   */
  resolve(raw: string): string | null {
    const query = raw.trim();
    if (!query) return null;
    for (const key of [query, `way:${query}`, `node:${query}`]) {
      if (this.properties.has(key)) return key;
    }
    return null;
  }

  /**
   * The lanes a set of features enters from and leaves to.
   *
   * A finding about a movement names only the connector, so "which lanes is this
   * about" has to be read off the connector's own ends. Ids that are not connectors
   * contribute nothing rather than guessing a direction for them — which is why a
   * finding that names lanes directly carries `movement_roles`, worked out by the
   * generator from the links at the node. Prefer those: they are a statement, not an
   * inference, and they cover a continuation, which has no connector to read.
   */
  movementEnds(
    identifiers: string[],
    roles?: Record<string, MovementRole>,
  ): { entry: string[]; exit: string[] } {
    if (roles) {
      const named = new Set(identifiers);
      const of = (role: MovementRole): string[] =>
        Object.entries(roles)
          .filter(([identifier, value]) => value === role && named.has(identifier))
          .map(([identifier]) => identifier);
      return { entry: of("approach"), exit: of("destination") };
    }
    const entry = new Set<string>();
    const exit = new Set<string>();
    for (const identifier of identifiers) {
      const properties = this.properties.get(identifier);
      if (!properties || !isConnector(properties)) continue;
      const from = String(properties.from_lane_id ?? "");
      const to = String(properties.to_lane_id ?? "");
      if (this.properties.has(from)) entry.add(from);
      if (this.properties.has(to)) exit.add(to);
    }
    return { entry: [...entry], exit: [...exit] };
  }

  /**
   * Every link a lane has, one row each. A direct continuation has no connector,
   * so it is named in entry_lanes/exit_lanes and has to be folded in separately —
   * without it a lane that simply carries on looks like a dead end.
   */
  links(properties: Properties, incoming: boolean): LinkRow[] {
    const own = incoming ? "to_lane_id" : "from_lane_id";
    const far = incoming ? "from_lane_id" : "to_lane_id";
    const rows: LinkRow[] = [];
    for (const connector of this.connectors) {
      if (connector[own] !== properties.id) continue;
      rows.push({
        id: String(connector[far] ?? ""),
        movement: String(connector.movement ?? "movement"),
        status: String(connector.status ?? "active"),
        connectorId: String(connector.id ?? ""),
      });
    }
    const continuations = properties[incoming ? "entry_lanes" : "exit_lanes"];
    for (const identifier of Array.isArray(continuations) ? continuations : []) {
      // entry_lanes/exit_lanes mix lane ids with connector ids; only the lane ids
      // are continuations, and a lane is what carries a lane count.
      const linked = this.properties.get(String(identifier));
      if (linked && laneNumbers(linked)) {
        rows.push({ id: String(identifier), movement: "continuation", status: "active" });
      }
    }
    const seen = new Set<string>();
    const unique: LinkRow[] = [];
    for (const row of rows) {
      if (!row.id || seen.has(row.id)) continue;
      seen.add(row.id);
      unique.push(row);
    }
    return unique.sort(
      (a, b) => a.movement.localeCompare(b.movement) || a.id.localeCompare(b.id),
    );
  }
}

export interface PopupHooks {
  /** Put a feature on the map, as if the reviewer had searched for its id. */
  focus(identifier: string): void;
  /** Open a finding in the side panel so it can be decided. */
  openFinding(finding: Finding): void;
  statusOf(findingId: string): DecisionStatus;
}

/**
 * The status of one link, as something the reviewer can act on.
 *
 * A movement generated as a connector is clickable: to its blocker if one names it,
 * otherwise to the movement on the map. A continuation has no connector and nothing
 * to decide, so it stays inert text rather than a button that does nothing.
 */
function statusCell(index: FeatureIndex, row: LinkRow, hooks: PopupHooks): HTMLElement {
  const cell = element("td");
  const label = row.status === "review_required" ? "review" : row.status;
  if (!row.connectorId) {
    cell.append(pill(row.status, label));
    return cell;
  }
  const connectorId = row.connectorId;
  const finding = index.findingsFor(connectorId)[0];
  const button = element("button", `pill ${row.status} actionable`, label);
  button.type = "button";
  button.title = finding
    ? `Open the ${finding.rule} finding for this movement`
    : "Show this movement on the map";
  button.addEventListener("click", () =>
    finding ? hooks.openFinding(finding) : hooks.focus(connectorId),
  );
  cell.append(button);
  return cell;
}

function linkTable(index: FeatureIndex, rows: LinkRow[], hooks: PopupHooks): HTMLElement {
  if (!rows.length) return element("span", "muted", "none");
  const table = element("table", "link-table");
  for (const row of rows) {
    const line = element("tr");
    const target = element("td");
    target.append(
      chip(index.label(row.id), index.has(row.id) ? () => hooks.focus(row.id) : undefined),
    );
    line.append(target);
    line.append(element("td", "muted", row.movement));
    line.append(statusCell(index, row, hooks));
    table.append(line);
  }
  return table;
}

function propertyTable(properties: Properties): HTMLElement {
  const box = element("details", "raw");
  box.append(element("summary", undefined, "All properties"));
  const table = element("table", "popup-table");
  for (const [key, value] of Object.entries(properties)) {
    const row = element("tr");
    row.append(element("td", undefined, key));
    row.append(element("td", undefined, Array.isArray(value) ? value.join(", ") : String(value)));
    table.append(row);
  }
  box.append(table);
  return box;
}

/**
 * The popup for a clicked feature. Lanes and connectors get the question they are
 * actually asked — what feeds this, where does it go — above the raw properties,
 * and any finding that names the feature is one click from being decided.
 */
export function buildPopup(
  index: FeatureIndex,
  properties: Properties,
  hooks: PopupHooks,
): HTMLElement {
  const identifier = String(properties.id ?? "");
  const root = element("div", "popup");
  root.append(element("strong", undefined, index.describe(identifier)));

  const list = element("dl", "evidence");
  if (isConnector(properties)) {
    const from = String(properties.from_lane_id ?? "");
    const to = String(properties.to_lane_id ?? "");
    // Same colours the two ends are painted on the map, so the chip and the lane it
    // names are recognisably the same thing.
    const entry = chip(index.label(from), () => hooks.focus(from));
    entry.classList.add("entry");
    const exit = chip(index.label(to), () => hooks.focus(to));
    exit.classList.add("exit");
    definitionRow(list, "Entry lane", entry);
    definitionRow(list, "Exit lane", exit);
    definitionRow(list, "Movement", String(properties.movement ?? ""));
    definitionRow(list, "Turn angle", `${String(properties.turn_angle_degrees ?? "")}°`);
    definitionRow(list, "Status", pill(String(properties.status ?? "active")));
  } else if (laneNumbers(properties)) {
    const incoming = index.links(properties, true);
    const outgoing = index.links(properties, false);
    // The status pills alone left a reviewer reading colours. Say it in words: these
    // movements are blockers, and the tag beside each one opens it.
    const held = [...incoming, ...outgoing].filter((row) => row.status === "review_required");
    if (held.length) {
      root.append(
        element(
          "p",
          "notice",
          `${held.length} movement(s) at this lane need review — click a review tag to open the blocker.`,
        ),
      );
    }
    definitionRow(list, "Entered from", linkTable(index, incoming, hooks));
    definitionRow(list, "Leaves to", linkTable(index, outgoing, hooks));
    const permissions = properties.turn_permissions;
    definitionRow(
      list,
      "turn:lanes",
      Array.isArray(permissions) && permissions.length ? permissions.join(", ") : "not tagged",
    );
  }
  if (list.childElementCount) root.append(list);

  const findings = index.findingsFor(identifier);
  if (findings.length) {
    root.append(element("h3", undefined, `${findings.length} finding(s) here`));
    const queue = element("div", "popup-findings");
    for (const finding of findings.slice(0, 8)) {
      const status = hooks.statusOf(finding.identifier);
      const button = element("button", `row ${status} ${finding.severity}`);
      button.type = "button";
      button.append(element("span", "row-rule", finding.rule));
      button.append(element("span", "row-status", status.replaceAll("_", " ")));
      button.addEventListener("click", () => hooks.openFinding(finding));
      queue.append(button);
    }
    if (findings.length > 8) {
      queue.append(element("p", "muted", `${findings.length - 8} more in the queue.`));
    }
    root.append(queue);
  }

  root.append(propertyTable(properties));
  return root;
}
