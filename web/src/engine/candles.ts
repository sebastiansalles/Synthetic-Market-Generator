/** Candle layout: bars -> pixel geometry for the visible window. Pure, tested. */
import { Viewport, priceToY, visibleRange } from "./viewport";

export interface Candle {
  timestamp: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface CandleGeom {
  index: number;
  x: number; // candle center, px
  bodyLeft: number;
  bodyWidth: number;
  bodyTop: number;
  bodyBottom: number;
  wickTop: number;
  wickBottom: number;
  up: boolean;
}

export interface ChartLayout {
  geoms: CandleGeom[];
  minPrice: number;
  maxPrice: number;
  volumeMax: number;
  visible: Candle[];
  pxPerCandle: number;
}

export const PRICE_PAD = 0.06; // 6% headroom above/below the visible extremes

export interface VolumeProfileBin {
  center: number; // log-price center of the bin
  volume: number;
  frac: number; // share of the max bin (0..1) — ready to draw as a horizontal bar
}

/** Volume-at-price over the visible window: where was business actually done? */
export function volumeProfile(bars: Candle[], bins = 24): VolumeProfileBin[] {
  if (bars.length === 0) return [];
  let lo = Infinity;
  let hi = -Infinity;
  let total = 0;
  for (const c of bars) {
    const l = Math.log(c.low);
    const h = Math.log(c.high);
    if (l < lo) lo = l;
    if (h > hi) hi = h;
    total += c.volume;
  }
  if (total === 0) return [];
  const width = (hi - lo) / bins || 1;
  const acc = new Array<number>(bins).fill(0);
  for (const c of bars) {
    const l = Math.log(c.low);
    const h = Math.log(c.high);
    const span = h - l || width;
    // spread the candle's volume across the log-range it traversed
    for (let b = 0; b < bins; b++) {
      const a0 = lo + b * width;
      const a1 = a0 + width;
      const overlap = Math.max(0, Math.min(h, a1) - Math.max(l, a0));
      if (overlap > 0) acc[b] += (c.volume * overlap) / span;
    }
  }
  const max = Math.max(...acc);
  return acc.map((v, b) => ({ center: lo + (b + 0.5) * width, volume: v, frac: v / max }));
}

export function layoutCandles(
  bars: Candle[],
  vp: Viewport,
  widthPx: number,
  priceHeightPx: number,
  autoscaleFrom?: Candle[], // e.g. completed bars only, so a forming candle can't jitter the axis
): ChartLayout {
  const { first, last } = visibleRange(vp, widthPx, bars.length);
  const visible = bars.slice(first, last + 1);
  const scaleSource = autoscaleFrom && autoscaleFrom.length > 0 ? autoscaleFrom : visible;
  let min = Infinity;
  let max = -Infinity;
  for (const c of scaleSource) {
    if (c.low < min) min = c.low;
    if (c.high > max) max = c.high;
  }
  let volumeMax = 0;
  for (const c of visible) {
    if (c.volume > volumeMax) volumeMax = c.volume;
  }
  if (!Number.isFinite(min)) {
    min = 0;
    max = 1;
  }
  const pad = (max - min) * PRICE_PAD || max * PRICE_PAD || 1;
  min -= pad;
  max += pad;

  const pxPerCandle = 1 / vp.candlesPerPixel;
  const bodyWidth = Math.max(1, Math.min(pxPerCandle * 0.7, 40));
  const geoms: CandleGeom[] = [];
  for (let i = 0; i < visible.length; i++) {
    const c = visible[i];
    const x = (first + i - vp.offset) * pxPerCandle + pxPerCandle / 2;
    const bodyTop = priceToY(Math.max(c.open, c.close), min, max, priceHeightPx);
    const bodyBottom = priceToY(Math.min(c.open, c.close), min, max, priceHeightPx);
    geoms.push({
      index: first + i,
      x,
      bodyLeft: x - bodyWidth / 2,
      bodyWidth,
      bodyTop,
      bodyBottom,
      wickTop: priceToY(c.high, min, max, priceHeightPx),
      wickBottom: priceToY(c.low, min, max, priceHeightPx),
      up: c.close >= c.open,
    });
  }
  return { geoms, minPrice: min, maxPrice: max, volumeMax, visible, pxPerCandle };
}
