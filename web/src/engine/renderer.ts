/** Canvas 2D renderer — no charting libraries. One pass per redraw, no retained
 *  state, allocations only inside the visible window. Dark theme. */
import { ChartLayout, VolumeProfileBin } from "./candles";
import { niceTicks, priceToY } from "./viewport";

export const THEMES = {
  dark: {
    bg: "#0d1117",
    grid: "#1c2330",
    axisText: "#8b949e",
    axisLine: "#30363d",
    bull: "#26a69a",
    bear: "#ef5350",
    volume: "#39424f",
    crosshair: "#c9d1d9",
    accent: "#1f6feb",
    accentSoft: "rgba(88, 166, 255, 0.25)",
    panelFade: "rgba(1, 4, 9, 0.78)",
    // Regime colors, calm -> turbulent (HMM states are canonically ordered by
    // variance). These are the RIBBON inks, drawn at full opacity; the background
    // wash is the same hue at `regimeAlpha`, so the two can never disagree.
    // Validated for colour-vision deficiency at 2 states (the only case any shipped
    // profile produces): worst adjacent pair ΔE 26.7 protan / 35.2 normal vision.
    regimeInk: ["#3b82f6", "#ef4444", "#a855f7"],
    regimeAlpha: [0.06, 0.2, 0.3],
  },
  light: {
    bg: "#ffffff",
    grid: "#e8edf5",
    axisText: "#5b6472",
    axisLine: "#d0d7e2",
    bull: "#0e9f6e",
    bear: "#e02444",
    volume: "#c3cfe3",
    crosshair: "#1f2937",
    accent: "#0b5fff",
    accentSoft: "rgba(11, 95, 255, 0.20)",
    panelFade: "rgba(255, 255, 255, 0.85)",
    // darker inks and lighter washes: a light surface swallows the dark theme's values
    regimeInk: ["#2563eb", "#dc2626", "#9333ea"],
    regimeAlpha: [0.05, 0.16, 0.26],
  },
} as const;

export type ThemeName = keyof typeof THEMES;

/** Back-compat alias: the dark theme (charts used to be dark-only). */
export const THEME = THEMES.dark;

export interface DrawOptions {
  width: number;
  height: number;
  priceHeight: number; // px; remainder below is the volume pane
  axisWidth: number; // right-side price gutter
}

/** The HMM regime path behind the candles. Present for every run, not just live ones:
 *  this heatmap is the project's visual evidence that the regime model does something,
 *  and it was previously drawn only while a live session streamed. */
export interface RegimeOverlay {
  states: number[]; // regime per bar index, indexed in bars[] space
  formingIndex: number | null; // live only: bar currently forming (drawn with emphasis)
}

export const REGIME_LABELS = ["calm", "turbulent", "crisis"] as const;

/** Height of the solid regime ribbon along the top of the price pane, in px.
 *
 *  The ribbon exists because the background wash alone cannot carry the signal: a
 *  wash pale enough to sit behind candles without fighting them separates by only
 *  ΔE ~6 under protanopia, and this heatmap is the project's visual evidence that
 *  the regime model works. The ribbon is the primary encoding (full-opacity, 26.7
 *  ΔE protan) and the wash reinforces it. The legend labels both in words, so
 *  identity never rests on colour alone. */
export const RIBBON_H = 5;

const clampRegime = (theme: (typeof THEMES)[ThemeName], state: number): number =>
  Math.min(Math.max(state | 0, 0), theme.regimeInk.length - 1);

/** Solid regime colour, clamped — a 3-state profile must not fall off the palette. */
export function regimeInk(theme: (typeof THEMES)[ThemeName], state: number): string {
  return theme.regimeInk[clampRegime(theme, state)];
}

// Composed once per theme, not once per bar. drawChart calls regimeWash for every
// visible candle, so parsing the hex and building the rgba() string inline meant
// ~3 parseInt plus a string allocation per bar per frame — at 1500 candles and 60fps
// that is 90k throwaway strings a second, in the one module whose stated contract is
// "allocations only inside the visible window".
const washCache = new WeakMap<object, readonly string[]>();

function washes(theme: (typeof THEMES)[ThemeName]): readonly string[] {
  let cached = washCache.get(theme);
  if (!cached) {
    cached = theme.regimeInk.map((hex, i) => {
      const [r, g, b] = [1, 3, 5].map((k) => parseInt(hex.slice(k, k + 2), 16));
      return `rgba(${r}, ${g}, ${b}, ${theme.regimeAlpha[i]})`;
    });
    washCache.set(theme, cached);
  }
  return cached;
}

/** The same hue as the ribbon, at wash alpha: one source of truth for both. */
export function regimeWash(theme: (typeof THEMES)[ThemeName], state: number): string {
  return washes(theme)[clampRegime(theme, state)];
}

export function regimeLabel(state: number): string {
  return REGIME_LABELS[state] ?? `state ${state}`;
}

export interface CrosshairInfo {
  px: number;
  py: number;
  index: number; // hovered candle index (in bars[] space)
  price: number;
}

export interface MarkerInfo {
  index: number; // candle index to flag
  label: string;
}

export interface ProfileDraw extends VolumeProfileBin {
  y: number; // pixel row for the bin center
}

export function drawChart(
  ctx: CanvasRenderingContext2D,
  layout: ChartLayout,
  opts: DrawOptions,
  theme: (typeof THEMES)[ThemeName] = THEMES.dark,
  overlay?: RegimeOverlay,
  profile?: ProfileDraw[],
  crosshair?: CrosshairInfo,
  marker?: MarkerInfo,
): void {
  const { width, height, priceHeight, axisWidth } = opts;
  const plotW = width - axisWidth;

  ctx.fillStyle = theme.bg;
  ctx.fillRect(0, 0, width, height);

  // regime heatmap: one background column per bar, colored by its HMM state. Drawn
  // FIRST so it reads as background — painted after the grid it tinted the gridlines.
  // Columns are drawn edge-to-edge (ceil of the span) so neighbouring bars in the
  // same regime form one continuous block instead of a picket fence at low zoom.
  const ribbonH = overlay ? RIBBON_H : 0;
  if (overlay) {
    for (let i = 0; i < layout.geoms.length; i++) {
      const g = layout.geoms[i];
      const st = overlay.states[g.index];
      if (st === undefined) continue;
      const x0 = Math.floor(g.x - layout.pxPerCandle / 2);
      const w = Math.ceil(layout.pxPerCandle) + 1;
      ctx.fillStyle = regimeWash(theme, st);
      ctx.fillRect(x0, 0, w, priceHeight);
      ctx.fillStyle = regimeInk(theme, st);
      ctx.fillRect(x0, 0, w, ribbonH);
    }
  }

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

  // volume profile: horizontal histogram of volume-at-price (right-aligned strip)
  if (profile && profile.length > 0) {
    const strip = 56;
    ctx.fillStyle = theme.accentSoft;
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

  // scenario marker: flag the injected shock so a one-candle event is unmissable
  if (marker && layout.geoms.length > 0) {
    const g0 = layout.geoms[0].index;
    const gi = marker.index - g0;
    if (gi >= 0 && gi < layout.geoms.length) {
      const x = Math.round(layout.geoms[gi].x) + 0.5;
      ctx.strokeStyle = theme.bear;
      ctx.setLineDash([6, 4]);
      ctx.beginPath();
      ctx.moveTo(x, ribbonH);
      ctx.lineTo(x, priceHeight);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.font = "11px ui-monospace, monospace";
      const tw = ctx.measureText(marker.label).width + 12;
      const lx = Math.min(Math.max(x + 6, 6), plotW - tw - 6);
      ctx.fillStyle = theme.bear;
      ctx.fillRect(lx, 6 + ribbonH, tw, 18);
      ctx.fillStyle = "#ffffff";
      ctx.textAlign = "left";
      ctx.fillText(marker.label, lx + 6, 15.5 + ribbonH);
    }
  }

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
    ctx.fillStyle = theme.accent;
    ctx.fillRect(plotW + 1, crosshair.py - 9, Math.max(tw, axisWidth - 2), 18);
    ctx.fillStyle = "#ffffff";
    ctx.textAlign = "left";
    ctx.fillText(tag, plotW + 5, crosshair.py + 0.5);
    // OHLC readout for the hovered candle
    const c = layout.visible[rel];
    if (c && g) {
      const txt = `O ${formatPrice(c.open)}  H ${formatPrice(c.high)}  L ${formatPrice(c.low)}  C ${formatPrice(c.close)}`;
      ctx.fillStyle = theme.panelFade;
      ctx.fillRect(6, 6 + ribbonH, ctx.measureText(txt).width + 12, 18);
      ctx.fillStyle = theme.crosshair;
      ctx.fillText(txt, 12, 15.5 + ribbonH);
    }
  }
}

export function formatPrice(p: number): string {
  const digits = Math.abs(p) >= 1000 ? 2 : Math.abs(p) >= 1 ? 3 : 5;
  return p.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}
