// Minimal ambient surface for the Leaflet global.
//
// The page loads Leaflet from the same CDN the Stage 1 and Stage 2 views already
// use, so it is not bundled; pulling @types/leaflet would add a dependency for a
// handful of call sites.

export type { GeoJsonFeature } from "./types.js";

export interface LatLngBoundsLike {
  isValid(): boolean;
  extend(other: unknown): LatLngBoundsLike;
  pad(ratio: number): LatLngBoundsLike;
}

export interface LeafletLayer {
  addTo(target: unknown): LeafletLayer;
  on(event: string, handler: (event: unknown) => void): LeafletLayer;
  setStyle?(style: Record<string, unknown>): LeafletLayer;
  getBounds?(): LatLngBoundsLike;
  bringToFront?(): LeafletLayer;
}

export interface LeafletLayerGroup extends LeafletLayer {
  addLayer(layer: LeafletLayer): LeafletLayerGroup;
  clearLayers(): LeafletLayerGroup;
}

export interface LeafletMap {
  setView(center: [number, number], zoom: number): LeafletMap;
  fitBounds(bounds: LatLngBoundsLike, options?: Record<string, unknown>): LeafletMap;
}

export interface LeafletStatic {
  map(element: string | HTMLElement, options?: Record<string, unknown>): LeafletMap;
  tileLayer(url: string, options?: Record<string, unknown>): LeafletLayer;
  layerGroup(): LeafletLayerGroup;
  geoJSON(data?: unknown, options?: Record<string, unknown>): LeafletLayer;
  circleMarker(latlng: unknown, options?: Record<string, unknown>): LeafletLayer;
  latLngBounds(corners: [number, number][]): LatLngBoundsLike;
  control: {
    layers(
      base: unknown,
      overlays: Record<string, LeafletLayer>,
      options?: Record<string, unknown>,
    ): LeafletLayer;
  };
}

declare global {
  // Provided by the Leaflet script tag in the generated HTML.
  const L: LeafletStatic;
}
