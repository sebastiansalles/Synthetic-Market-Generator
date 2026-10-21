import { describe, expect, it } from "vitest";
import { Candle, layoutCandles } from "../engine/candles";
import {
  drawChart,
  formatPrice,
  regimeInk,
  regimeLabel,
  regimeWash,
  RIBBON_H,
  THEMES,
} from "../engine/renderer";
import { Viewport } from "../engine/viewport";

/** Recording stand-in for CanvasRenderingContext2D: enough surface for drawChart,
 *  and it keeps every fillText so tests can assert what actually reached the canvas. */
function recordingCtx() {
  const texts: string[] = [];
  const rects: { x: number; w: number; fill: string }[] = [];
  const ctx = {
    fillStyle: "",
    strokeStyle: "",
    lineWidth: 1,
    font: "",
    textAlign: "left",
    textBaseline: "middle",
    globalAlpha: 1,
    fillRect(x: number, _y: number, w: number) {
      rects.push({ x, w, fill: this.fillStyle });
    },
    strokeRect: () => undefined,
    beginPath: () => undefined,
    moveTo: () => undefined,
    lineTo: () => undefined,
    stroke: () => undefined,
    setLineDash: () => undefined,
    measureText: (t: string) => ({ width: t.length * 6 }),
    fillText: (t: string) => texts.push(t),
  };
  return { ctx: ctx as unknown as CanvasRenderingContext2D, texts, rects };
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

describe("regime heatmap", () => {
  // The heatmap is the project's visual proof that the HMM does something, and it
  // used to be drawn only while a live WebSocket session streamed: static runs showed
  // nothing, and a completed live session lost its colours the moment it ended.
  const data = bars(60);
  const vp: Viewport = { offset: 0, candlesPerPixel: 0.2 };
  const layout = layoutCandles(data, vp, OPTS.width - OPTS.axisWidth, OPTS.priceHeight);
  const states = data.map((_, i) => (i % 10 < 3 ? 1 : 0));

  it("paints the wash for a static run, with calm and turbulent distinguished", () => {
    const { ctx, rects } = recordingCtx();
    drawChart(ctx, layout, OPTS, THEMES.dark, { states, formingIndex: null });
    const calm = rects.filter((r) => r.fill === regimeWash(THEMES.dark, 0));
    const turb = rects.filter((r) => r.fill === regimeWash(THEMES.dark, 1));
    expect(calm.length + turb.length).toBe(layout.geoms.length);
    expect(turb.length).toBe(states.filter((x) => x === 1).length);
    expect(regimeWash(THEMES.dark, 0)).not.toBe(regimeWash(THEMES.dark, 1));
  });

  it("paints no wash at all without an overlay", () => {
    const { ctx, rects } = recordingCtx();
    drawChart(ctx, layout, OPTS, THEMES.dark);
    for (let i = 0; i < THEMES.dark.regimeInk.length; i++) {
      expect(rects.some((r) => r.fill === regimeWash(THEMES.dark, i))).toBe(false);
    }
  });

  // Painted after the grid, the translucent wash tinted every gridline. It belongs
  // directly on top of the background fill and under everything else.
  it("paints the wash as background — immediately after the page fill", () => {
    const { ctx, rects } = recordingCtx();
    drawChart(ctx, layout, OPTS, THEMES.dark, { states, formingIndex: null });
    expect(rects[0].fill).toBe(THEMES.dark.bg);
    expect(rects[1].fill).toBe(regimeWash(THEMES.dark, states[0]));
  });

  it("covers the full bar span so neighbouring bars form one block", () => {
    const { ctx, rects } = recordingCtx();
    drawChart(ctx, layout, OPTS, THEMES.dark, { states, formingIndex: null });
    const wash = rects.filter((r) => r.fill === regimeWash(THEMES.dark, 0))[0];
    expect(wash.w).toBeGreaterThanOrEqual(layout.pxPerCandle);
  });

  it("uses a different wash per theme — a dark alpha is invisible on white", () => {
    expect(regimeWash(THEMES.light, 1)).not.toBe(regimeWash(THEMES.dark, 1));
  });

  it("clamps a regime index past the palette instead of drawing undefined", () => {
    const last = THEMES.dark.regimeInk.length - 1;
    expect(regimeInk(THEMES.dark, 99)).toBe(THEMES.dark.regimeInk[last]);
    expect(regimeInk(THEMES.dark, -1)).toBe(THEMES.dark.regimeInk[0]);
    expect(regimeWash(THEMES.dark, 99)).toBe(regimeWash(THEMES.dark, last));
    expect(regimeLabel(0)).toBe("calm");
    expect(regimeLabel(1)).toBe("turbulent");
    expect(regimeLabel(42)).toContain("42");
  });

  // The wash is the ribbon's hue at wash alpha. Defining them separately is how a
  // chart ends up with a blue ribbon over a red wash after one of them is edited.
  it("derives the wash from the ribbon ink, so the two can never disagree", () => {
    for (const name of ["dark", "light"] as const) {
      const theme = THEMES[name];
      theme.regimeInk.forEach((hex, i) => {
        const [r, g, b] = [1, 3, 5].map((k) => parseInt(hex.slice(k, k + 2), 16));
        expect(regimeWash(theme, i)).toBe(`rgba(${r}, ${g}, ${b}, ${theme.regimeAlpha[i]})`);
      });
    }
  });

  // A wash pale enough to sit behind candles separates by only ~6 dE under
  // protanopia, so the solid ribbon is the primary encoding, not decoration.
  it("draws a solid ribbon per bar on top of the wash", () => {
    const { ctx, rects } = recordingCtx();
    drawChart(ctx, layout, OPTS, THEMES.dark, { states, formingIndex: null });
    const ribbon = rects.filter((r) => r.fill === regimeInk(THEMES.dark, 1));
    expect(ribbon.length).toBe(states.filter((x) => x === 1).length);
    expect(RIBBON_H).toBeGreaterThan(0);
  });

  it("draws no ribbon when there is no regime path to show", () => {
    const { ctx, rects } = recordingCtx();
    drawChart(ctx, layout, OPTS, THEMES.dark);
    for (let i = 0; i < THEMES.dark.regimeInk.length; i++) {
      expect(rects.some((r) => r.fill === regimeInk(THEMES.dark, i))).toBe(false);
    }
  });
});
