import { beforeEach, describe, expect, it } from "vitest";

import { buildOverlays } from "../src/overlays.js";
import type { GeoJsonFeature } from "../src/types.js";

// Leaflet is loaded from a script tag on the generated page, not bundled, so the
// module reaches it through the `L` global. The stub below is the smallest surface
// buildOverlays touches, with layer groups backed by a plain array so membership can
// be read back — that membership *is* the behaviour under test.

interface StubLayer {
  layers?: StubLayer[];
  addTo(target: unknown): StubLayer;
  addLayer?(layer: StubLayer): StubLayer;
  removeLayer?(layer: StubLayer): StubLayer;
  bindPopup?(): StubLayer;
  on?(): StubLayer;
}

let overlayHandlers: ((event: { type: string; name: string; layer: unknown }) => void)[] = [];
let overlayControl: Record<string, StubLayer> = {};
let onMap: Set<StubLayer>;

function layerGroup(): StubLayer {
  const layers: StubLayer[] = [];
  const group: StubLayer = {
    layers,
    addTo(_target: unknown) {
      onMap.add(group);
      return group;
    },
    addLayer(layer: StubLayer) {
      if (!layers.includes(layer)) layers.push(layer);
      return group;
    },
    removeLayer(layer: StubLayer) {
      const at = layers.indexOf(layer);
      if (at >= 0) layers.splice(at, 1);
      return group;
    },
  };
  return group;
}

const map = {
  setView: () => map,
  fitBounds: () => map,
  on(event: string, handler: (event: { type: string; name: string; layer: unknown }) => void) {
    // Leaflet accepts several event names in one string; the module passes two.
    for (const _name of event.split(" ")) overlayHandlers.push(handler);
    return map;
  },
};

beforeEach(() => {
  overlayHandlers = [];
  overlayControl = {};
  onMap = new Set();
  (globalThis as Record<string, unknown>).L = {
    layerGroup,
    geoJSON: (): StubLayer => ({
      addTo: (): StubLayer => ({}) as StubLayer,
      bindPopup(): StubLayer {
        return this as StubLayer;
      },
    }),
    circleMarker: (): StubLayer => ({ addTo: () => ({}) as StubLayer }),
    control: {
      layers: (_base: unknown, overlays: Record<string, StubLayer>) => {
        overlayControl = overlays;
        return { addTo: () => ({}) as StubLayer };
      },
    },
  };
});

/** Toggle a checkbox the way L.Control.Layers does: fire the event on the map. */
function toggle(label: string, on: boolean): void {
  const layer = overlayControl[label];
  const event = { type: on ? "overlayadd" : "overlayremove", name: label, layer };
  // Both event names share one handler, so firing it once is one checkbox change.
  const seen = new Set<unknown>();
  for (const handler of overlayHandlers) {
    if (seen.has(handler)) continue;
    seen.add(handler);
    handler(event);
  }
}

function connector(id: string, status: string): GeoJsonFeature[] {
  const properties = { id, kind: "connector", status };
  return [
    {
      type: "Feature",
      geometry: { type: "Polygon", coordinates: [[]] },
      properties: { ...properties, kind: "connector_polygon" },
    },
    {
      type: "Feature",
      geometry: { type: "LineString", coordinates: [] },
      properties,
    },
  ] as unknown as GeoJsonFeature[];
}

const FEATURES: GeoJsonFeature[] = [
  ...connector("c-active", "active"),
  ...connector("c-review", "review_required"),
  ...connector("c-forbidden", "forbidden"),
];

const popupFor = () => ({}) as HTMLElement;

function bandCount(index: ReturnType<typeof buildOverlays>): number {
  const group = index.groups.connector_polygon as unknown as StubLayer;
  return group.layers?.length ?? -1;
}

function holdsBandFor(index: ReturnType<typeof buildOverlays>, status: string): boolean {
  const group = index.groups.connector_polygon as unknown as StubLayer;
  const band = (index.bandsByStatus[status] ?? [])[0] as unknown as StubLayer;
  return Boolean(group.layers?.includes(band));
}

describe("movement bands follow their status checkbox", () => {
  it("starts with every band drawn, one per movement", () => {
    const index = buildOverlays(map as never, FEATURES, popupFor);
    expect(bandCount(index)).toBe(3);
    for (const status of ["active", "review_required", "forbidden"]) {
      expect(index.bandsByStatus[status], status).toHaveLength(1);
      expect(holdsBandFor(index, status), status).toBe(true);
    }
  });

  it("takes a category's band away with its centrelines, and leaves the others", () => {
    // The defect this pins: unchecking a category hid the hairlines and left the
    // lane-width bands over the map, opaque and still taking the clicks.
    const index = buildOverlays(map as never, FEATURES, popupFor);
    toggle("Movements needing review", false);
    expect(holdsBandFor(index, "review_required")).toBe(false);
    expect(holdsBandFor(index, "active")).toBe(true);
    expect(holdsBandFor(index, "forbidden")).toBe(true);
    expect(bandCount(index)).toBe(2);
  });

  it("brings the band back when the category is re-checked", () => {
    const index = buildOverlays(map as never, FEATURES, popupFor);
    toggle("Forbidden movements", false);
    toggle("Forbidden movements", true);
    expect(holdsBandFor(index, "forbidden")).toBe(true);
    expect(bandCount(index)).toBe(3);
  });

  it("does not resurrect a hidden category's band when the band layer is re-checked", () => {
    // Visibility is the intersection of the two boxes, so the order they are
    // toggled in cannot matter: re-ticking "Connector lanes" must not undo an
    // unticked category.
    const index = buildOverlays(map as never, FEATURES, popupFor);
    toggle("Allowed movements", false);
    toggle("Connector lanes", false);
    toggle("Connector lanes", true);
    expect(holdsBandFor(index, "active")).toBe(false);
    expect(bandCount(index)).toBe(2);
  });

  it("leaves layers that are not movement bands alone", () => {
    // The handler keys off the three status groups; toggling anything else must
    // not move a band. Lane polygons share the map with the bands and are the
    // reason the master switch exists.
    const lane = [
      {
        type: "Feature",
        geometry: { type: "Polygon", coordinates: [[]] },
        properties: { id: "lane-a", kind: "lane_polygon" },
      },
    ] as unknown as GeoJsonFeature[];
    const index = buildOverlays(map as never, [...FEATURES, ...lane], popupFor);
    toggle("Lane polygons", false);
    expect(bandCount(index)).toBe(3);
  });
});
