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

export function layoutCandles(
  bars: Candle[],
  vp: Viewport,
  widthPx: number,
  priceHeightPx: number,
): ChartLayout {
  const { first, last } = visibleRange(vp, widthPx, bars.length);
  const visible = bars.slice(first, last + 1);
  let min = Infinity;
  let max = -Infinity;
  let volumeMax = 0;
  for (const c of visible) {
    if (c.low < min) min = c.low;
    if (c.high > max) max = c.high;
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
