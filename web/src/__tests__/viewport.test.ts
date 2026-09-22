import { describe, expect, it } from "vitest";
import {
  Viewport,
  clampCpp,
  MAX_CPP,
  MIN_CPP,
  niceTicks,
  panBy,
  priceToY,
  visibleRange,
  wheelZoomFactor,
  zoomAt,
} from "../engine/viewport";

describe("viewport", () => {
  it("clamps candles-per-pixel to sane zoom bounds", () => {
    expect(clampCpp(1e-9)).toBe(MIN_CPP);
    expect(clampCpp(1e9)).toBe(MAX_CPP);
    expect(clampCpp(0.5)).toBe(0.5);
  });

  it("zoomAt keeps the candle under the anchor pixel fixed", () => {
    const vp: Viewport = { offset: 100, candlesPerPixel: 0.5 };
    const anchorPx = 300;
    const anchorCandle = vp.offset + anchorPx * vp.candlesPerPixel;
    const zoomed = zoomAt(vp, anchorPx, 0.5); // zoom in 2x
    expect(zoomed.candlesPerPixel).toBeCloseTo(0.25);
    expect(zoomed.offset + anchorPx * zoomed.candlesPerPixel).toBeCloseTo(anchorCandle);
  });

  it("panBy moves the window and clamps at the edges", () => {
    const vp: Viewport = { offset: 10, candlesPerPixel: 1 };
    const left = panBy(vp, 100, 800, 1000); // drag right -> view moves left
    expect(left.offset).toBe(10 - 100);
    const far = panBy(vp, -1e9, 800, 1000);
    expect(far.offset).toBeLessThanOrEqual(999);
    expect(visibleRange(far, 800, 1000).last).toBe(999);
  });

  it("visibleRange intersects with [0, n-1]", () => {
    const vp: Viewport = { offset: -5, candlesPerPixel: 1 };
    const r = visibleRange(vp, 100, 500);
    expect(r.first).toBe(0);
    expect(r.last).toBe(95);
  });

  it("priceToY maps min to the bottom and max to the top", () => {
    expect(priceToY(0, 0, 100, 500)).toBe(500);
    expect(priceToY(100, 0, 100, 500)).toBe(0);
    expect(priceToY(50, 0, 100, 500)).toBe(250);
  });

  it("niceTicks produces readable steps inside the range", () => {
    const ticks = niceTicks(1.09, 1.21, 5);
    expect(ticks.length).toBeGreaterThan(1);
    for (const t of ticks) {
      expect(t).toBeGreaterThanOrEqual(1.09 - 1e-9);
      expect(t).toBeLessThanOrEqual(1.21 + 1e-9);
    }
    expect(new Set(ticks.map((t) => t.toPrecision(3))).size).toBe(ticks.length);
  });
});

import { followOffset } from "../engine/viewport";

describe("followOffset", () => {
  it("keeps the tail at 85% of the span and clamps at zero for short sessions", () => {
    expect(followOffset(1000, 100)).toBe(1000 - 85);
    expect(followOffset(50, 100)).toBe(0);
    expect(followOffset(85, 100)).toBe(0);
  });
});

describe("wheelZoomFactor", () => {
  // a fixed step per event is unusable on a trackpad, which fires a stream of
  // small deltas for a single gesture
  it("scales with how far the wheel actually moved", () => {
    const small = wheelZoomFactor(4);
    const big = wheelZoomFactor(100);
    expect(small).toBeGreaterThan(1);
    expect(big).toBeGreaterThan(small);
    expect(small).toBeLessThan(1.05); // a trackpad nudge barely moves the zoom
  });

  it("is symmetric: scrolling back undoes the zoom", () => {
    expect(wheelZoomFactor(100) * wheelZoomFactor(-100)).toBeCloseTo(1, 12);
  });

  it("normalizes line and page delta modes to pixels", () => {
    expect(wheelZoomFactor(1, 1)).toBeCloseTo(wheelZoomFactor(16, 0), 12);
    expect(wheelZoomFactor(1, 2)).toBeCloseTo(wheelZoomFactor(400, 0), 12);
  });

  it("gives pinch gestures their own sensitivity", () => {
    expect(wheelZoomFactor(10, 0, true)).toBeGreaterThan(wheelZoomFactor(10, 0, false));
  });

  it("never lets one event teleport the view", () => {
    expect(wheelZoomFactor(100000)).toBeLessThanOrEqual(3);
    expect(wheelZoomFactor(-100000)).toBeGreaterThanOrEqual(1 / 3);
  });
});

describe("zoomAt bounds", () => {
  it("keeps the window on the data when given the extent", () => {
    // zooming hard at the right edge used to strand the viewport past the data,
    // which then yanked the price autoscale
    const vp = { offset: 900, candlesPerPixel: 0.5 };
    const loose = zoomAt(vp, 700, 4);
    const bounded = zoomAt(vp, 700, 4, 800, 1000);
    expect(bounded.offset).toBeLessThanOrEqual(999);
    expect(bounded.offset).toBeGreaterThanOrEqual(-800 * bounded.candlesPerPixel + 1);
    expect(loose.candlesPerPixel).toBe(bounded.candlesPerPixel);
  });

  it("leaves the anchor fixed when no clamping is needed", () => {
    const vp = { offset: 100, candlesPerPixel: 0.5 };
    const out = zoomAt(vp, 400, 0.5, 800, 5000);
    expect(vp.offset + 400 * vp.candlesPerPixel).toBeCloseTo(
      out.offset + 400 * out.candlesPerPixel,
      9,
    );
  });
});
