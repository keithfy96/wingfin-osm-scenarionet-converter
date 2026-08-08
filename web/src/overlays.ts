// Map overlays for the review view.
//
// Layer names and colours deliberately match the read-only Stage 2 audit so a
// reviewer moving between the two sees the same map, with decision state added.

import type { GeoJsonFeature, LeafletLayer, LeafletLayerGroup, LeafletMap } from "./types-dom.js";

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
}

export function buildOverlays(
  map: LeafletMap,
  features: GeoJsonFeature[],
  popupFor: (properties: Record<string, unknown>) => HTMLElement,
): OverlayIndex {
  const groups: Record<string, LeafletLayerGroup> = {};
  const byId = new Map<string, LeafletLayer[]>();
  const baseStyle = new Map<LeafletLayer, Record<string, unknown>>();

  for (const name of Object.keys(LAYER_LABELS)) {
    groups[name] = L.layerGroup();
  }

  for (const feature of features) {
    const properties = feature.properties ?? {};
    const kind = String(properties.kind ?? "");
    // Connectors carry their status as the group so the three movement layers
    // can be toggled independently of the band that shows their footprint.
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
  }

  const overlayControl: Record<string, LeafletLayer> = {};
  for (const [name, group] of Object.entries(groups)) {
    overlayControl[LAYER_LABELS[name] ?? name] = group;
    if (DEFAULT_ON.has(name)) group.addTo(map);
  }
  L.control.layers(null, overlayControl, { collapsed: true }).addTo(map);

  return { byId, baseStyle, groups };
}

/**
 * Paint the features a finding names yellow and pan to them.
 *
 * Source OSM geometry gets its own weight so a way and the lanes generated from it
 * stay distinguishable while both are lit. Returns how many layers were actually
 * found, so the panel can say plainly when a finding maps to no geometry rather
 * than leaving the reviewer looking for a highlight that was never drawn.
 */
export function focusFeatures(
  map: LeafletMap,
  index: OverlayIndex,
  generated: string[],
  source: string[],
): number {
  let bounds: ReturnType<typeof L.latLngBounds> | null = null;
  let painted = 0;
  for (const [identifiers, style] of [
    [source, SOURCE_HIGHLIGHT],
    [generated, GENERATED_HIGHLIGHT],
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
