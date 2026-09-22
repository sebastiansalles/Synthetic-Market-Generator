/** Canvas 2D renderer — no charting libraries. One pass per redraw, no retained
 *  state, allocations only inside the visible window. Dark theme. */
import { ChartLayout, VolumeProfileBin } from "./candles";
import { heatColor } from "./live";
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

export interface LiveOverlay {
  states: number[]; // regime per bar index (HMM heatmap)
  formingIndex: number | null; // bar currently forming (drawn with emphasis)
}

export interface CrosshairInfo {
  px: number;
  py: number;
  index: number; // hovered candle index (in bars[] space)
  price: number;
}

export interface ProfileDraw extends VolumeProfileBin {
  y: number; // pixel row for the bin center
}

export function drawChart(
  ctx: CanvasRenderingContext2D,
  layout: ChartLayout,
  opts: DrawOptions,
  theme: typeof THEME = THEME,
  overlay?: LiveOverlay,
  profile?: ProfileDraw[],
  crosshair?: CrosshairInfo,
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

  // regime heatmap: background column per bar, colored by inferred HMM state
  if (overlay) {
    for (let i = 0; i < layout.geoms.length; i++) {
      const g = layout.geoms[i];
      const st = overlay.states[g.index];
      if (st === undefined) continue;
      ctx.fillStyle = heatColor(st);
      ctx.fillRect(g.x - layout.pxPerCandle / 2, 0, layout.pxPerCandle, priceHeight);
    }
  }

  // volume profile: horizontal histogram of volume-at-price (right-aligned strip)
  if (profile && profile.length > 0) {
    const strip = 56;
    ctx.fillStyle = "rgba(88, 166, 255, 0.25)";
    for (const bin of profile) {
      const h = Math.max(1, (priceHeight / profile.length) * 0.8);
      ctx.fillRect(plotW - strip * bin.frac, bin.y - h / 2, strip * bin.frac, h);
    }
  }

  // candles
  for (const g of layout.geoms) {
    const color = g.up ? theme.bull : theme.bear;
    ctx.strokeStyle = color;
    ctx.fillStyle = color;
    ctx.globalAlpha = overlay && overlay.formingIndex === g.index ? 0.7 : 1;
    ctx.beginPath();
    ctx.moveTo(Math.round(g.x) + 0.5, g.wickTop);
    ctx.lineTo(Math.round(g.x) + 0.5, g.wickBottom);
    ctx.stroke();
    const top = Math.min(g.bodyTop, g.bodyBottom);
    const h = Math.max(1, Math.abs(g.bodyBottom - g.bodyTop));
    ctx.fillRect(g.bodyLeft, top, g.bodyWidth, h);
    ctx.globalAlpha = 1;
  }

  // plot frame
  ctx.strokeStyle = theme.axisLine;
  ctx.strokeRect(0.5, 0.5, plotW - 1, height - 1);
  ctx.beginPath();
  ctx.moveTo(0, priceHeight + 24.5);
  ctx.lineTo(width, priceHeight + 24.5);
  ctx.stroke();

  // crosshair: dashed lines, axis price tag, hovered-candle OHLC readout.
  // `index` is an absolute bar index; the visible window starts at geoms[0].index.
  const rel = crosshair && layout.geoms.length > 0 ? crosshair.index - layout.geoms[0].index : -1;
  if (crosshair && rel >= 0 && rel < layout.geoms.length) {
    const g = layout.geoms[rel];
    ctx.strokeStyle = theme.crosshair;
    ctx.setLineDash([4, 4]);
    ctx.globalAlpha = 0.6;
    ctx.beginPath();
    ctx.moveTo(Math.round(crosshair.px) + 0.5, 0);
    ctx.lineTo(Math.round(crosshair.px) + 0.5, priceHeight);
    ctx.moveTo(0, Math.round(crosshair.py) + 0.5);
    ctx.lineTo(plotW, Math.round(crosshair.py) + 0.5);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.globalAlpha = 1;
    // price tag at the cursor's row
    const tag = formatPrice(crosshair.price);
    ctx.font = "11px ui-monospace, monospace";
    const tw = ctx.measureText(tag).width + 8;
    ctx.fillStyle = "#1f6feb";
    ctx.fillRect(plotW + 1, crosshair.py - 9, Math.max(tw, axisWidth - 2), 18);
    ctx.fillStyle = "#ffffff";
    ctx.textAlign = "left";
    ctx.fillText(tag, plotW + 5, crosshair.py + 0.5);
    // OHLC readout for the hovered candle
    const c = layout.visible[rel];
    if (c && g) {
      const txt = `O ${formatPrice(c.open)}  H ${formatPrice(c.high)}  L ${formatPrice(c.low)}  C ${formatPrice(c.close)}`;
      ctx.fillStyle = "rgba(1, 4, 9, 0.75)";
      ctx.fillRect(6, 6, ctx.measureText(txt).width + 12, 18);
      ctx.fillStyle = theme.crosshair;
      ctx.fillText(txt, 12, 15.5);
    }
  }
}

export function formatPrice(p: number): string {
  const digits = Math.abs(p) >= 1000 ? 2 : Math.abs(p) >= 1 ? 3 : 5;
  return p.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}
