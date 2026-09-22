/** Viewport math for the candlestick renderer — pure functions, unit-tested.
 *  X axis is in "candle index" space (0..n-1, floats allowed); Y is price. */

export interface Viewport {
  /** index of the first (partially) visible candle */
  offset: number;
  /** zoom: candles per horizontal pixel */
  candlesPerPixel: number;
}

export const MIN_CPP = 0.005; // max zoom out: 200 px per candle
export const MAX_CPP = 4; // max zoom in: quarter-candle per pixel

export function clampCpp(cpp: number): number {
  return Math.min(MAX_CPP, Math.max(MIN_CPP, cpp));
}

/** Zoom by `factor` (>1 zooms out) keeping the candle under `anchorPx` fixed. */
export function zoomAt(vp: Viewport, anchorPx: number, factor: number): Viewport {
  const cpp = clampCpp(vp.candlesPerPixel * factor);
  const anchorCandle = vp.offset + anchorPx * vp.candlesPerPixel;
  return { candlesPerPixel: cpp, offset: anchorCandle - anchorPx * cpp };
}

/** Pan by a pixel delta; clamps so the visible window stays within [0, n-1]. */
export function panBy(vp: Viewport, dxPx: number, widthPx: number, n: number): Viewport {
  const span = widthPx * vp.candlesPerPixel;
  let offset = vp.offset - dxPx * vp.candlesPerPixel;
  offset = Math.min(offset, n - 1);
  offset = Math.max(offset, -span + 1); // keep at least one candle visible
  return { ...vp, offset: offset };
}

export interface VisibleRange {
  first: number;
  last: number;
}

/** Integer index range [first, last] intersected with [0, n-1]. */
export function visibleRange(vp: Viewport, widthPx: number, n: number): VisibleRange {
  const first = Math.max(0, Math.floor(vp.offset));
  const last = Math.min(n - 1, Math.ceil(vp.offset + widthPx * vp.candlesPerPixel));
  return { first, last: Math.max(first, last) };
}

/** Linear price→pixel mapping (y grows downward on canvas). */
export function priceToY(price: number, min: number, max: number, height: number): number {
  return ((max - price) / (max - min || 1)) * height;
}

/** "Nice" evenly spaced axis ticks covering [min, max]. */
export function niceTicks(min: number, max: number, count = 5): number[] {
  const span = max - min || Math.abs(max) || 1;
  const step0 = span / count;
  const mag = Math.pow(10, Math.floor(Math.log10(step0)));
  const norm = step0 / mag;
  const step = (norm >= 5 ? 10 : norm >= 2 ? 5 : norm >= 1 ? 2 : 1) * mag;
  const lo = Math.ceil(min / step) * step;
  const ticks: number[] = [];
  for (let v = lo; v <= max + 1e-12; v += step) ticks.push(Number(v.toPrecision(12)));
  return ticks;
}
