// Map overlays for the review view.
//
// Layer names and colours deliberately match the read-only Stage 2 audit so a
// reviewer moving between the two sees the same map, with decision state added.

import type {
  GeoJsonFeature,
  LeafletLayer,
  LeafletLayerGroup,
  LeafletMap,
  LeafletOverlayEvent,
} from "./types-dom.js";

const STATUS_COLOR: Record<string, string> = {
  active: "#2f9e44",
  review_required: "#e8590c",
  forbidden: "#c92a2a",
};

// Selection is yellow, as in the Stage 2 audit. Decision state is deliberately not
// a highlight colour: unresolved and review_required are both orange, so colouring
// the selection by state made the selected feature indistinguishable from its
// neighbours — which is exactly the thing a reviewer needs to see.
const GENERATED_HIGHLIGHT = {
  color: "#ffd43b",
  weight: 8,
  fillColor: "#ffd43b",
  fillOpacity: 0.4,
  opacity: 1,
};
const SOURCE_HIGHLIGHT = {
  color: "#ffd43b",
  weight: 7,
  fillColor: "#ffd43b",
  fillOpacity: 0.9,
  opacity: 1,
  radius: 9,
};

// The two ends of the selected movement, so "this lane, into that lane" can be read
// off the map rather than inferred from a hex id. Orange approaches, green receives.
// The green is a shade off the active-connector green so a highlighted lane is never
// mistaken for a movement band lying across it.
const ENTRY_HIGHLIGHT = {
  color: "#f76707",
  weight: 8,
  fillColor: "#f76707",
  fillOpacity: 0.35,
  opacity: 1,
};
const EXIT_HIGHLIGHT = {
  color: "#37b24d",
  weight: 8,
  fillColor: "#37b24d",
  fillOpacity: 0.35,
  opacity: 1,
};

export const LAYER_LABELS: Record<string, string> = {
  source_way: "Source OSM ways",
  source_node: "Source OSM nodes",
  lane_polygon: "Lane polygons",
  lane_centerline: "Lane centrelines",
  lane_direction: "Lane direction arrows",
  connector_polygon: "Connector lanes",
  active: "Allowed movements",
  review_required: "Movements needing review",
  forbidden: "Forbidden movements",
  stop_line: "Stop lines",
};

/** Layers shown before the reviewer touches the layer control. */
const DEFAULT_ON = new Set([
  "source_way",
  "lane_centerline",
  "lane_direction",
  "connector_polygon",
  "active",
  "review_required",
  "forbidden",
  "stop_line",
]);

function styleFor(properties: Record<string, unknown>): Record<string, unknown> {
  const kind = String(properties.kind ?? "");
  const status = String(properties.status ?? "");
  switch (kind) {
    case "source_way":
      return { color: "#868e96", weight: 1.5, opacity: 0.55, dashArray: "2 4" };
    case "source_node":
      return { color: "#868e96", weight: 1, radius: 4, fillColor: "#adb5bd", fillOpacity: 0.8 };
    case "lane_polygon":
      return { color: "#74c0fc", weight: 1, fillColor: "#74c0fc", fillOpacity: 0.08 };
    case "lane_centerline":
      return { color: "#277da1", weight: 2, opacity: 0.75 };
    case "lane_direction":
      return { color: "#0b4f7a", weight: 3, opacity: 0.95, lineCap: "butt" };
    case "connector_polygon":
      return {
        color: STATUS_COLOR[status] ?? "#868e96",
        weight: 1,
        fillColor: STATUS_COLOR[status] ?? "#868e96",
        fillOpacity: 0.22,
        opacity: 0.5,
      };
    case "stop_line":
      return { color: "#7048e8", weight: 6 };
    default:
      return {
        color: STATUS_COLOR[status] ?? "#868e96",
        weight: status === "review_required" ? 5 : 3,
        dashArray: status === "review_required" ? "7 5" : undefined,
        opacity: 0.9,
      };
  }
}

export interface OverlayIndex {
  /** Every layer drawn for a generated feature or OSM id, keyed by that id. */
  byId: Map<string, LeafletLayer[]>;
  /**
   * The style each layer was drawn with. Restoring from the layer's own feature
   * fails for a lane — a GeoJSON layer group carries no `feature` — and silently
   * repaints it as the default connector style, so the style is kept here.
   */
  baseStyle: Map<LeafletLayer, Record<string, unknown>>;
  groups: Record<string, LeafletLayerGroup>;
  /** Every movement band, by the status whose checkbox governs it. */
  bandsByStatus: Record<string, LeafletLayer[]>;
}

/** The statuses that have a checkbox of their own, and so a band group of their own. */
const BAND_STATUSES = ["active", "review_required", "forbidden"] as const;

export function buildOverlays(
  map: LeafletMap,
  features: GeoJsonFeature[],
  popupFor: (properties: Record<string, unknown>) => HTMLElement,
): OverlayIndex {
  const groups: Record<string, LeafletLayerGroup> = {};
  const byId = new Map<string, LeafletLayer[]>();
  const baseStyle = new Map<LeafletLayer, Record<string, unknown>>();
  const bandsByStatus: Record<string, LeafletLayer[]> = {};

  for (const name of Object.keys(LAYER_LABELS)) {
    groups[name] = L.layerGroup();
  }
  for (const status of BAND_STATUSES) {
    bandsByStatus[status] = [];
  }

  for (const feature of features) {
    const properties = feature.properties ?? {};
    const kind = String(properties.kind ?? "");
    // A movement's centreline is filed under its status, so the three movement
    // layers toggle separately. Its band goes to connector_polygon and is governed
    // by both boxes — see the overlay handler below.
    const groupName = kind === "connector" ? String(properties.status ?? "active") : kind;
    const group = groups[groupName];
    if (!group) continue;

    const layer = L.geoJSON(feature, {
      style: () => styleFor(properties),
      pointToLayer: (_point: unknown, latlng: unknown) =>
        (L as unknown as { circleMarker(ll: unknown, o: unknown): LeafletLayer }).circleMarker(
          latlng,
          styleFor(properties),
        ),
    });

    const identifiers = [
      properties.id,
      properties.osm_id,
      properties.identifier,
      properties.junction_node_id,
    ]
      .filter((value): value is string => typeof value === "string" && value.length > 0);

    layer.bindPopup?.(() => popupFor(properties), { maxWidth: 420, maxHeight: 380 });
    baseStyle.set(layer, styleFor(properties));
    for (const identifier of identifiers) {
      const existing = byId.get(identifier);
      if (existing) existing.push(layer);
      else byId.set(identifier, [layer]);
    }
    group.addLayer(layer);
    const bands = kind === "connector_polygon" ? bandsByStatus[String(properties.status)] : undefined;
    if (bands) bands.push(layer);
  }

  const overlayControl: Record<string, LeafletLayer> = {};
  for (const [name, group] of Object.entries(groups)) {
    overlayControl[LAYER_LABELS[name] ?? name] = group;
    if (DEFAULT_ON.has(name)) group.addTo(map);
  }
  L.control.layers(null, overlayControl, { collapsed: true }).addTo(map);
  linkBandsToStatus(map, groups, bandsByStatus);

  return { byId, baseStyle, groups, bandsByStatus };
}

/**
 * Make a movement band visible only when its own status is ticked as well as the
 * band layer itself.
 *
 * A Leaflet overlay is one checkbox to one layer group, so a band cannot belong to
 * both its status and "Connector lanes". Filing it under its status alone would work
 * but costs the master switch that clears every band at once — which is what makes
 * the lane polygons underneath readable. So the band stays in connector_polygon and
 * joins or leaves that group as its status is toggled. Left unlinked, unchecking a
 * category hid its centrelines and left its bands lying over the map, opaque and
 * still taking the clicks meant for the movements still showing.
 *
 * Only the layer control fires these events; the `addTo` calls that set the opening
 * state do not, so every band starts in the group and nothing has to be replayed.
 */
function linkBandsToStatus(
  map: LeafletMap,
  groups: Record<string, LeafletLayerGroup>,
  bandsByStatus: Record<string, LeafletLayer[]>,
): void {
  const bandOwner = new Map<LeafletLayer, string>();
  for (const status of BAND_STATUSES) {
    const group = groups[status];
    if (group) bandOwner.set(group, status);
  }
  const bandGroup = groups.connector_polygon;
  if (!bandGroup) return;

  map.on("overlayadd overlayremove", (event: LeafletOverlayEvent) => {
    const status = bandOwner.get(event.layer);
    if (!status) return;
    for (const band of bandsByStatus[status] ?? []) {
      if (event.type === "overlayadd") bandGroup.addLayer(band);
      else bandGroup.removeLayer(band);
    }
  });
}

export interface FocusPlan {
  /** Generated features the finding names. Painted yellow. */
  generated: string[];
  /** Source OSM geometry, `way:<id>` / `node:<id>`. Painted yellow, own weight. */
  source: string[];
  /** Lanes the selected movement leaves. Painted orange. */
  entry?: string[];
  /** Lanes the selected movement enters. Painted green. */
  exit?: string[];
}

/**
 * Paint everything a finding names and pan to it.
 *
 * Source OSM geometry gets its own weight so a way and the lanes generated from it
 * stay distinguishable while both are lit, and the two ends of a movement are painted
 * last so their roles win over the plain selection colour. Returns how many layers
 * were found, so the panel can say plainly when a finding maps to no geometry rather
 * than leaving the reviewer looking for a highlight that was never drawn.
 */
export function focusFeatures(map: LeafletMap, index: OverlayIndex, plan: FocusPlan): number {
  let bounds: ReturnType<typeof L.latLngBounds> | null = null;
  let painted = 0;
  for (const [identifiers, style] of [
    [plan.source, SOURCE_HIGHLIGHT],
    [plan.generated, GENERATED_HIGHLIGHT],
    [plan.entry ?? [], ENTRY_HIGHLIGHT],
    [plan.exit ?? [], EXIT_HIGHLIGHT],
  ] as const) {
    for (const identifier of identifiers) {
      for (const layer of index.byId.get(identifier) ?? []) {
        layer.setStyle?.(style);
        layer.bringToFront?.();
        painted += 1;
        const layerBounds = layer.getBounds?.();
        if (layerBounds?.isValid()) {
          bounds = bounds ? bounds.extend(layerBounds) : layerBounds;
        }
      }
    }
  }
  if (bounds?.isValid()) {
    map.fitBounds(bounds.pad(0.45), { maxZoom: 19 });
  }
  return painted;
}

/** Undo a previous focus so the map returns to its status colouring. */
export function clearFocus(index: OverlayIndex, identifiers: string[]): void {
  for (const identifier of identifiers) {
    for (const layer of index.byId.get(identifier) ?? []) {
      const style = index.baseStyle.get(layer);
      if (style) layer.setStyle?.(style);
    }
  }
}
