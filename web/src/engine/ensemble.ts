/** Ensemble math — pure functions over the path matrix from POST /ensemble.
 *  The distribution is the product: percentile fan over time, terminal stats,
 *  terminal histogram. Unit-tested; no rendering here. */

export interface EnsembleResult {
  n_paths: number;
  n_candles: number;
  start_price: number;
  shock_at: number | null;
  preset: string | null;
  /** True when the paths opened from today's filtered regime + variance rather
   *  than the chain's long-run shares. */
  conditioned?: boolean;
  paths: number[][]; // [path][candle] close prices
}

/** q-th percentile per time step across paths (q in 0..1). */
export function percentileBand(paths: number[][], q: number): number[] {
  const n = paths[0]?.length ?? 0;
  const out = new Array<number>(n);
  const col: number[] = [];
  for (let t = 0; t < n; t++) {
    col.length = 0;
    for (let i = 0; i < paths.length; i++) col.push(paths[i][t]);
    col.sort((a, b) => a - b);
    const idx = Math.min(col.length - 1, Math.max(0, q * (col.length - 1)));
    const lo = Math.floor(idx);
    const frac = idx - lo;
    out[t] = col[lo] + (col[Math.min(col.length - 1, lo + 1)] - col[lo]) * frac;
  }
  return out;
}

export interface TerminalStats {
  median: number;
  p05: number;
  p95: number;
  mean: number;
  probLoss: number; // share of paths ending below the start price
  probDoubling: number;
}

export function terminalStats(paths: number[][], startPrice: number): TerminalStats {
  const last = paths.map((p) => p[p.length - 1]).sort((a, b) => a - b);
  const pick = (q: number) => {
    const idx = q * (last.length - 1); // interpolated, same convention as percentileBand
    const lo = Math.floor(idx);
    const frac = idx - lo;
    return last[lo] + (last[Math.min(last.length - 1, lo + 1)] - last[lo]) * frac;
  };
  const below = last.filter((v) => v < startPrice).length;
  return {
    median: pick(0.5),
    p05: pick(0.05),
    p95: pick(0.95),
    mean: last.reduce((s, v) => s + v, 0) / last.length,
    probLoss: below / last.length,
    probDoubling: last.filter((v) => v >= 2 * startPrice).length / last.length,
  };
}

export interface Histogram {
  lo: number;
  hi: number;
  counts: number[];
  centers: number[];
}

export function histogram(values: number[], bins = 28): Histogram {
  if (values.length === 0) return { lo: 0, hi: 1, counts: [], centers: [] };
  const lo = Math.min(...values);
  const hi = Math.max(...values);
  const width = (hi - lo) / bins || 1;
  const counts = new Array<number>(bins).fill(0);
  for (const v of values) {
    const b = Math.min(bins - 1, Math.floor((v - lo) / width));
    counts[b] += 1;
  }
  return { lo, hi, counts, centers: counts.map((_, b) => lo + (b + 0.5) * width) };
}

/** Messages from /ws/ensemble — the distribution built in front of you. */
export type EnsembleWsEvent =
  | { type: "start"; n_paths: number; n_candles: number; start_price: number; shock_at: number | null; preset: string | null; conditioned?: boolean }
  | { type: "path"; index: number; path: number[] }
  | { type: "done" }
  | { type: "error"; message: string };
