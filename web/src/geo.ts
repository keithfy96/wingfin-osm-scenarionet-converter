// The one [lat, lon] -> metres approximation the clients share.
//
// It lived in `route/path.ts` until the signal builder needed it too, to say how far a light
// has moved since a plan was drawn on an earlier generation of the map. `build.mjs` keeps the
// three bundles from carrying each other's code, so it moved out here rather than being
// imported across, and rather than being copied - a distance formula in two places is the
// kind of thing that drifts by a byte and is never noticed.

export const EARTH_RADIUS_M = 6_371_000;

/** Great-circle distance, which is what the payload's [lat, lon] pairs support.
 *
 * The lane model holds metres in a local projection, but the pages are handed the WGS84
 * projection of it for Leaflet. Over a network 800 m across the difference between the two
 * is far below anything either caller decides on.
 */
export function metresBetween(a: [number, number], b: [number, number]): number {
  const toRad = Math.PI / 180;
  const meanLat = ((a[0] + b[0]) / 2) * toRad;
  const dLat = (b[0] - a[0]) * toRad;
  const dLon = (b[1] - a[1]) * toRad * Math.cos(meanLat);
  return Math.hypot(dLat, dLon) * EARTH_RADIUS_M;
}
