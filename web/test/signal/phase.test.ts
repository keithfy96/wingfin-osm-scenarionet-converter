// The colour a group shows, and the strip that draws it.
//
// These are the browser half of `signal_plan.colour_at`, and the cases below are the ones
// `tests/unit/test_signal_plan.py` asserts on the Python side with the same numbers. Two
// implementations of one clock is a thing worth pinning: the page is where a plan is judged
// and the converter is what writes it, so a disagreement about the sign of the offset would
// draw a green light on screen and put a red one in the pickle.

import { describe, expect, it } from "vitest";
import { colourAt, phaseStripCss, GREEN, RED, YELLOW } from "../../src/signal/phase.js";

const GROUP = { green_seconds: 27, yellow_seconds: 3, offset_seconds: 0 };

describe("colourAt", () => {
  it.each([
    [0, GREEN],
    [26.9, GREEN],
    [27, YELLOW],
    [29.9, YELLOW],
    [30, RED],
    [59.9, RED],
  ])("at %o s shows %s", (seconds, expected) => {
    expect(colourAt(seconds, GROUP, 60)).toBe(expected);
  });

  it("wraps at the cycle", () => {
    expect(colourAt(60, GROUP, 60)).toBe(GREEN);
    expect(colourAt(87, GROUP, 60)).toBe(YELLOW);
  });

  // The offset is when green *starts*. Getting this backwards is the one error that would
  // still look plausible on screen, so it is stated as a test rather than only a comment.
  it("starts green at the offset", () => {
    const offset = { ...GROUP, offset_seconds: 30 };
    expect(colourAt(29.9, offset, 60)).toBe(RED);
    expect(colourAt(30, offset, 60)).toBe(GREEN);
    expect(colourAt(57, offset, 60)).toBe(YELLOW);
  });

  it("is green for the whole cycle when green fills it", () => {
    const always = { green_seconds: 60, yellow_seconds: 0, offset_seconds: 0 };
    for (const seconds of [0, 15, 30, 45, 59.9]) {
      expect(colourAt(seconds, always, 60)).toBe(GREEN);
    }
  });

  it("is red for the whole cycle when nothing is green", () => {
    const never = { green_seconds: 0, yellow_seconds: 0, offset_seconds: 0 };
    expect(colourAt(0, never, 60)).toBe(RED);
    expect(colourAt(30, never, 60)).toBe(RED);
  });
});

describe("phaseStripCss", () => {
  it("draws every band at its real width, amber included", () => {
    const css = phaseStripCss({ name: "a", lanes: [], ...GROUP }, 60);
    expect(css).toContain(`${GREEN} 0.000% 45.000%`);
    expect(css).toContain(`${YELLOW} 45.000% 50.000%`);
    expect(css).toContain(`${RED} 50.000% 100.000%`);
  });

  it("splits the strip in two when the phase wraps past the end of the cycle", () => {
    const css = phaseStripCss({ name: "a", lanes: [], ...GROUP, offset_seconds: 45 }, 60);
    // Green runs 45 s -> 60 s and then 0 s -> 12 s, so the strip opens green and closes green.
    expect(css.startsWith(`linear-gradient(to right, ${GREEN} 0.000%`)).toBe(true);
    expect(css.endsWith(`${GREEN} 75.000% 100.000%)`)).toBe(true);
  });
});
