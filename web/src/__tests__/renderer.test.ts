import { describe, expect, it } from "vitest";
import { Candle, layoutCandles } from "../engine/candles";
import { drawChart, formatPrice } from "../engine/renderer";
import { Viewport } from "../engine/viewport";

/** Recording stand-in for CanvasRenderingContext2D: enough surface for drawChart,
 *  and it keeps every fillText so tests can assert what actually reached the canvas. */
function recordingCtx() {
  const texts: string[] = [];
  const ctx = {
    fillStyle: "",
    strokeStyle: "",
    lineWidth: 1,
    font: "",
    textAlign: "left",
    textBaseline: "middle",
    globalAlpha: 1,
    fillRect: () => undefined,
    strokeRect: () => undefined,
    beginPath: () => undefined,
    moveTo: () => undefined,
    lineTo: () => undefined,
    stroke: () => undefined,
    setLineDash: () => undefined,
    measureText: (t: string) => ({ width: t.length * 6 }),
    fillText: (t: string) => texts.push(t),
  };
  return { ctx: ctx as unknown as CanvasRenderingContext2D, texts };
}

function bars(n: number): Candle[] {
  return Array.from({ length: n }, (_, i) => ({
    timestamp: new Date(Date.UTC(2026, 0, 1) + i * 86_400_000).toISOString(),
    open: 100 + i,
    high: 101 + i,
    low: 99 + i,
    close: 100.5 + i,
    volume: 10,
  }));
}

const OPTS = { width: 864, height: 490, priceHeight: 400, axisWidth: 64 };

describe("drawChart crosshair", () => {
  // `crosshair.index` is an ABSOLUTE bar index; the visible window starts at
  // geoms[0].index. Comparing it against visible.length made the crosshair vanish
  // once the user panned past the first screenful of data.
  it("draws the hovered-candle readout when the viewport is panned deep into the data", () => {
    const data = bars(1500);
    const vp: Viewport = { offset: 1200, candlesPerPixel: 1 };
    const layout = layoutCandles(data, vp, OPTS.width - OPTS.axisWidth, OPTS.priceHeight);
    const hovered = 1250;
    expect(hovered).toBeGreaterThan(layout.visible.length); // the case that used to fail

    const { ctx, texts } = recordingCtx();
    drawChart(ctx, layout, OPTS, undefined, undefined, undefined, {
      px: 50,
      py: 200,
      index: hovered,
      price: 1300,
    });
    const readout = texts.find((t) => t.startsWith("O "));
    expect(readout).toBeDefined();
    expect(readout).toContain(formatPrice(data[hovered].close)); // the hovered bar, not a neighbour
  });

  it("draws nothing for a crosshair outside the visible window", () => {
    const data = bars(200);
    const vp: Viewport = { offset: 0, candlesPerPixel: 1 };
    const layout = layoutCandles(data, vp, OPTS.width - OPTS.axisWidth, OPTS.priceHeight);
    const { ctx, texts } = recordingCtx();
    drawChart(ctx, layout, OPTS, undefined, undefined, undefined, {
      px: 50,
      py: 200,
      index: 5_000,
      price: 100,
    });
    expect(texts.some((t) => t.startsWith("O "))).toBe(false);
  });

  it("survives an empty layout", () => {
    const layout = layoutCandles([], { offset: 0, candlesPerPixel: 1 }, 800, 400);
    const { ctx } = recordingCtx();
    expect(() =>
      drawChart(ctx, layout, OPTS, undefined, undefined, undefined, {
        px: 10,
        py: 10,
        index: 0,
        price: 1,
      }),
    ).not.toThrow();
  });
});
