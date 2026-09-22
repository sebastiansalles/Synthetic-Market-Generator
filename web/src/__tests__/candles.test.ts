import { describe, expect, it } from "vitest";
import { Candle, layoutCandles } from "../engine/candles";
import { Viewport } from "../engine/viewport";

function mkCandles(n: number): Candle[] {
  let price = 100;
  return Array.from({ length: n }, (_, i) => {
    const drift = Math.sin(i / 7) * 0.8;
    const open = price;
    price += drift + 0.1;
    return {
      timestamp: `2026-01-01T00:00:0${i % 10}Z`,
      open,
      high: Math.max(open, price) + 0.5,
      low: Math.min(open, price) - 0.5,
      close: price,
      volume: 1000 + (i % 5) * 100,
    };
  });
}

describe("layoutCandles", () => {
  const bars = mkCandles(1000);
  const vp: Viewport = { offset: 0, candlesPerPixel: 1 }; // 800px -> candles 0..800

  it("lays out exactly the visible window", () => {
    const layout = layoutCandles(bars, vp, 800, 400);
    expect(layout.geoms.length).toBe(801);
    expect(layout.geoms[0].index).toBe(0);
    expect(layout.geoms.at(-1)!.index).toBe(800);
  });

  it("body and wick stay inside the price bounds of each candle", () => {
    const layout = layoutCandles(bars, vp, 800, 400);
    layout.geoms.forEach((g, i) => {
      const c = layout.visible[i];
      expect(g.wickTop).toBeLessThanOrEqual(g.bodyTop + 1e-9);
      expect(g.wickBottom).toBeGreaterThanOrEqual(g.bodyBottom - 1e-9);
      expect(g.up).toBe(c.close >= c.open);
      expect(g.x).toBeGreaterThanOrEqual(0);
    });
  });

  it("autoscale pads beyond the visible extremes", () => {
    const layout = layoutCandles(bars, vp, 800, 400);
    const visibleHighs = layout.visible.map((c) => c.high);
    const visibleLows = layout.visible.map((c) => c.low);
    expect(layout.maxPrice).toBeGreaterThan(Math.max(...visibleHighs));
    expect(layout.minPrice).toBeLessThan(Math.min(...visibleLows));
  });

  it("reports the visible volume max", () => {
    const layout = layoutCandles(bars, vp, 800, 400);
    expect(layout.volumeMax).toBe(Math.max(...layout.visible.map((c) => c.volume)));
  });
});
