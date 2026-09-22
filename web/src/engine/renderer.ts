/** Canvas 2D renderer — no charting libraries. One pass per redraw, no retained
 *  state, allocations only inside the visible window. Dark theme. */
import { ChartLayout } from "./candles";
import { niceTicks, priceToY } from "./viewport";

export const THEME = {
  bg: "#0d1117",
  grid: "#1c2330",
  axisText: "#8b949e",
  axisLine: "#30363d",
  bull: "#26a69a",
  bear: "#ef5350",
  volume: "#39424f",
  crosshair: "#c9d1d9",
} as const;

export interface DrawOptions {
  width: number;
  height: number;
  priceHeight: number; // px; remainder below is the volume pane
  axisWidth: number; // right-side price gutter
}

export function drawChart(
  ctx: CanvasRenderingContext2D,
  layout: ChartLayout,
  opts: DrawOptions,
  theme: typeof THEME = THEME,
): void {
  const { width, height, priceHeight, axisWidth } = opts;
  const plotW = width - axisWidth;

  ctx.fillStyle = theme.bg;
  ctx.fillRect(0, 0, width, height);

  // grid + price axis labels
  ctx.strokeStyle = theme.grid;
  ctx.fillStyle = theme.axisText;
  ctx.lineWidth = 1;
  ctx.font = "11px ui-monospace, monospace";
  ctx.textAlign = "left";
  ctx.textBaseline = "middle";
  const ticks = niceTicks(layout.minPrice, layout.maxPrice, 6);
  for (const t of ticks) {
    const y = Math.round(priceToY(t, layout.minPrice, layout.maxPrice, priceHeight)) + 0.5;
    ctx.beginPath();
    ctx.moveTo(0, y);
    ctx.lineTo(plotW, y);
    ctx.stroke();
    ctx.fillText(formatPrice(t), plotW + 6, y);
  }

  // date labels along the bottom
  const n = layout.geoms.length;
  if (n > 1) {
    const labelEvery = Math.max(1, Math.ceil(n / 8));
    ctx.textAlign = "center";
    for (let i = 0; i < n; i += labelEvery) {
      const g = layout.geoms[i];
      const d = new Date(layout.visible[i].timestamp);
      ctx.fillText(
        `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, "0")}-${String(
          d.getUTCDate(),
        ).padStart(2, "0")}`,
        Math.min(Math.max(g.x, 40), plotW - 40),
        priceHeight + 12,
      );
    }
  }

  // volume pane
  if (layout.volumeMax > 0) {
    const volTop = priceHeight + 24;
    const volH = height - volTop - 4;
    ctx.fillStyle = theme.volume;
    for (let i = 0; i < layout.geoms.length; i++) {
      const v = layout.visible[i].volume;
      const h = (v / layout.volumeMax) * volH;
      ctx.fillRect(layout.geoms[i].bodyLeft, volTop + (volH - h), layout.geoms[i].bodyWidth, h);
    }
  }

  // candles
  for (const g of layout.geoms) {
    const color = g.up ? theme.bull : theme.bear;
    ctx.strokeStyle = color;
    ctx.fillStyle = color;
    ctx.beginPath();
    ctx.moveTo(Math.round(g.x) + 0.5, g.wickTop);
    ctx.lineTo(Math.round(g.x) + 0.5, g.wickBottom);
    ctx.stroke();
    const top = Math.min(g.bodyTop, g.bodyBottom);
    const h = Math.max(1, Math.abs(g.bodyBottom - g.bodyTop));
    ctx.fillRect(g.bodyLeft, top, g.bodyWidth, h);
  }

  // plot frame
  ctx.strokeStyle = theme.axisLine;
  ctx.strokeRect(0.5, 0.5, plotW - 1, height - 1);
  ctx.beginPath();
  ctx.moveTo(0, priceHeight + 24.5);
  ctx.lineTo(width, priceHeight + 24.5);
  ctx.stroke();
}

export function formatPrice(p: number): string {
  const digits = Math.abs(p) >= 1000 ? 2 : Math.abs(p) >= 1 ? 3 : 5;
  return p.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}
