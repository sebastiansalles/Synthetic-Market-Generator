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
