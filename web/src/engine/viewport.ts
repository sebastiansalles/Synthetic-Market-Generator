/** Viewport math for the candlestick renderer — pure functions, unit-tested.
 *  X axis is in "candle index" space (0..n-1, floats allowed); Y is price. */

export interface Viewport {
  /** index of the first (partially) visible candle */
  offset: number;
  /** zoom: candles per horizontal pixel */
  candlesPerPixel: number;
}

// candlesPerPixel: SMALL = zoomed in (few candles spread over many pixels).
export const MIN_CPP = 0.005; // furthest zoom IN: 200 px per candle
export const MAX_CPP = 4; // furthest zoom OUT: 4 candles per pixel

export function clampCpp(cpp: number): number {
  return Math.min(MAX_CPP, Math.max(MIN_CPP, cpp));
}

/** Keep the window overlapping the data: at least one candle stays on screen. */
export function clampOffset(offset: number, spanCandles: number, n: number): number {
  if (n <= 0) return offset; // nothing to anchor against yet
  return Math.max(-spanCandles + 1, Math.min(offset, n - 1));
}

/** Zoom by `factor` (>1 zooms out) keeping the candle under `anchorPx` fixed.
 *  Pass widthPx/n to keep the result anchored to the data — without them a zoom
 *  can strand the viewport off the end, which then yanks the price autoscale. */
export function zoomAt(vp: Viewport, anchorPx: number, factor: number,
                       widthPx?: number, n?: number): Viewport {
  const cpp = clampCpp(vp.candlesPerPixel * factor);
  const anchorCandle = vp.offset + anchorPx * vp.candlesPerPixel;
  const offset = anchorCandle - anchorPx * cpp;
  if (widthPx === undefined || n === undefined) return { candlesPerPixel: cpp, offset };
  return { candlesPerPixel: cpp, offset: clampOffset(offset, widthPx * cpp, n) };
}

/** Pan by a pixel delta; clamps so the visible window stays within [0, n-1]. */
export function panBy(vp: Viewport, dxPx: number, widthPx: number, n: number): Viewport {
  const span = widthPx * vp.candlesPerPixel;
  const offset = vp.offset - dxPx * vp.candlesPerPixel;
  return { ...vp, offset: clampOffset(offset, span, n) };
}

const LINE_HEIGHT_PX = 16; // deltaMode 1 reports lines
const PAGE_HEIGHT_PX = 400; // deltaMode 2 reports pages

/** Zoom factor for one wheel event, proportional to how far the wheel actually moved.
 *  A fixed step per event is unusable on a trackpad, which fires a stream of small
 *  deltas for one gesture. Pinch gestures arrive as ctrl+wheel with small deltas of
 *  their own, so they get their own sensitivity. */
export function wheelZoomFactor(deltaY: number, deltaMode = 0, pinch = false): number {
  const px = deltaY * (deltaMode === 1 ? LINE_HEIGHT_PX : deltaMode === 2 ? PAGE_HEIGHT_PX : 1);
  const factor = Math.exp(px * (pinch ? 0.01 : 0.0022));
  return Math.min(3, Math.max(1 / 3, factor)); // no single event may teleport the view
}

/** Live follow: offset that keeps the session tail at `fill` of the visible span. */
export function followOffset(n: number, spanCandles: number, fill = 0.85): number {
  return Math.max(0, n - spanCandles * fill);
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
