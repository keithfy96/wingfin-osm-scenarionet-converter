// Where the page puts the light.
//
// The one assertion worth having here is which end of the lane it takes. A lane drawn from
// the wrong end still looks like a lane, and a circle at its wrong end still looks like a
// light - it is only wrong once the converter has built a wall in the middle of a junction.
// `tests/unit/test_lane_payload.py` holds the other half of this: that the vertex taken here
// is the one `signal_plan.stop_points` hands MetaDrive.

import { describe, expect, it } from "vitest";
import { GREEN, RED } from "../../src/signal/phase.js";
import {
  STOP_RADIUS_ACTIVE,
  STOP_RADIUS_OTHER,
  stopMarkerStyle,
  stopPoint,
} from "../../src/signal/stop-marker.js";

const LINE: [number, number][] = [
  [1.3, 103.8],
  [1.31, 103.81],
  [1.32, 103.82],
];

describe("stopPoint", () => {
  it("is the downstream end, not the upstream one", () => {
    expect(stopPoint({ line: LINE })).toEqual([1.32, 103.82]);
  });

  it("follows the lane rather than the compass", () => {
    // The same geometry travelled the other way stops at the other end. Nothing about the
    // coordinates decides this; the order `lane_payload` wrote them in does.
    expect(stopPoint({ line: [...LINE].reverse() })).toEqual([1.3, 103.8]);
  });

  it("is the single point when a lane has only one", () => {
    expect(stopPoint({ line: [[1.3, 103.8]] })).toEqual([1.3, 103.8]);
  });

  it("is null rather than a throw when a lane has no geometry", () => {
    // Never expected from the payload, and a page that draws no dot beats one that fails to
    // boot over a lane it could have drawn everything else about.
    expect(stopPoint({ line: [] })).toBeNull();
  });
});

describe("stopMarkerStyle", () => {
  it("fills with the light's colour, not the group's hue", () => {
    expect(stopMarkerStyle(RED, false).fillColor).toBe(RED);
    expect(stopMarkerStyle(GREEN, false).fillColor).toBe(GREEN);
  });

  it("is filled and ringed in white, so it reads on a lane of the same colour", () => {
    const style = stopMarkerStyle(GREEN, false);
    expect(style.fillOpacity).toBe(1);
    expect(style.color).toBe("#ffffff");
  });

  it("draws the group being edited larger", () => {
    expect(stopMarkerStyle(GREEN, true).radius).toBe(STOP_RADIUS_ACTIVE);
    expect(stopMarkerStyle(GREEN, false).radius).toBe(STOP_RADIUS_OTHER);
    expect(STOP_RADIUS_ACTIVE).toBeGreaterThan(STOP_RADIUS_OTHER);
  });
});
