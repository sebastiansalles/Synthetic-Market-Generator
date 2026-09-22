import { describe, expect, it } from "vitest";
import { Candle, layoutCandles, PRICE_PAD, volumeProfile } from "../engine/candles";
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

describe("volumeProfile", () => {
  it("spreads candle volume across its price range and conserves the total", () => {
    const bars = [
      { timestamp: "t", open: 100, high: 110, low: 90, close: 105, volume: 100 },
      { timestamp: "t", open: 105, high: 108, low: 102, close: 103, volume: 50 },
    ];
    const bins = volumeProfile(bars, 10);
    expect(bins).toHaveLength(10);
    const total = bins.reduce((s, b) => s + b.volume, 0);
    expect(total).toBeCloseTo(150, 6);
    for (const b of bins) {
      expect(b.frac).toBeLessThanOrEqual(1);
      expect(b.volume).toBeGreaterThanOrEqual(0);
    }
    expect(Math.max(...bins.map((b) => b.frac))).toBeCloseTo(1, 9);
  });

  it("returns empty for zero-volume input", () => {
    const bars = [
      { timestamp: "t", open: 100, high: 101, low: 99, close: 100, volume: 0 },
    ];
    expect(volumeProfile(bars, 8)).toEqual([]);
  });
});

describe("autoscaleFrom (live forming-candle jitter fix)", () => {
  const vpLocal: Viewport = { offset: 0, candlesPerPixel: 1 };

  it("a forming-candle spike does not move the price scale", () => {
    const calm = Array.from({ length: 50 }, () => ({
      timestamp: "t", open: 100, high: 101, low: 99, close: 100.5, volume: 10,
    }));
    const spike = { timestamp: "t", open: 100, high: 500, low: 99, close: 450, volume: 10 };
    const without = layoutCandles([...calm, spike], vpLocal, 800, 400);
    const withFix = layoutCandles([...calm, spike], vpLocal, 800, 400, calm);
    expect(withFix.maxPrice).toBeLessThan(without.maxPrice);
    // scaled off the calm bars alone: [99, 101] plus PRICE_PAD headroom
    expect(withFix.maxPrice).toBeCloseTo(101 + 2 * PRICE_PAD, 6);
    expect(withFix.minPrice).toBeCloseTo(99 - 2 * PRICE_PAD, 6);
    // without the fix the spike drags the whole axis up with it
    expect(without.maxPrice).toBeCloseTo(500 + (500 - 99) * PRICE_PAD, 6);
    // the spike still renders (its wick may exceed the plot; that's intended)
    const g = withFix.geoms.at(-1)!;
    expect(g.up).toBe(true);
  });
});
