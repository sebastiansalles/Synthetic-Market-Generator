/** Pure helpers for the validation-evidence view. No DOM, no canvas — the geometry
 *  and formatting are separated out so the parts that are easy to get subtly wrong
 *  (a band that renders off its rail, a tail bin that takes log(0)) are testable. */

export type MetricUnit = "ret" | "pct" | "num";

export interface ValidationMetric {
  key: string;
  label: string;
  family: string;
  unit: MetricUnit;
  real: number;
  sim_median: number;
  lo: number;
  hi: number;
  inside: boolean;
}

export interface ValidationFamily {
  key: string;
  label: string;
  blurb: string;
  passed: number;
  total: number;
}

export interface DensityBand {
  centers: number[];
  real: number[];
  lo: number[];
  median: number[];
  hi: number[];
  real_sd: number;
}

export interface ValidationReport {
  status: "ready";
  asset: string;
  n_bars: number;
  n_sims: number;
  seed: number;
  history_years: number | null;
  last_date: string | null;
  summary: { passed: number; total: number };
  families: ValidationFamily[];
  metrics: ValidationMetric[];
  density: DensityBand;
  cache?: { computed_at?: string };
}

export type ValidationState =
  | ValidationReport
  | { status: "computing"; asset: string; n_sims: number }
  | { status: "failed"; asset: string; message: string };

/** A metric's value in the units a reader thinks in. Returns and ranges are shares,
 *  so they read as percentages; correlations and kurtosis are bare numbers; volume
 *  is large enough to need thousands separators. */
export function formatMetric(value: number, unit: MetricUnit): string {
  if (!Number.isFinite(value)) return "—";
  if (unit === "ret") return `${(value * 100).toFixed(3)}%`;
  if (unit === "pct") return `${(value * 100).toFixed(1)}%`;
  if (Math.abs(value) >= 1000) return value.toLocaleString("en-US", { maximumFractionDigits: 0 });
  return value.toFixed(3);
}

export interface BandGeometry {
  bandLeft: number; // all four are percentages of the rail's width
  bandWidth: number;
  realPos: number;
  medianPos: number;
}

/** Where the simulated band and the real value sit on a row's rail.
 *
 *  The domain spans whichever is wider — the band or the real value — then pads, so
 *  a real value far outside the band is still ON the rail and visibly outside it.
 *  Clamping instead would park every failure at the same edge and lose the distinction
 *  between "just missed" and "nowhere near". */
export function bandGeometry(lo: number, hi: number, real: number, median: number): BandGeometry {
  const min = Math.min(lo, real);
  const max = Math.max(hi, real);
  const pad = (max - min) * 0.18 || Math.abs(max) * 0.1 || 1;
  const d0 = min - pad;
  const d1 = max + pad;
  const at = (x: number) => ((x - d0) / (d1 - d0)) * 100;
  return {
    bandLeft: at(lo),
    bandWidth: Math.max(at(hi) - at(lo), 0.6), // a zero-width band must still be visible
    realPos: at(real),
    medianPos: at(median),
  };
}

/** Smallest positive density across every series, halved — the floor a log axis uses
 *  in place of the empty tail bins, where log(0) would otherwise blow up the path. */
export function densityFloor(band: DensityBand): number {
  let min = Infinity;
  for (const series of [band.real, band.lo, band.median, band.hi]) {
    for (const v of series) if (v > 0 && v < min) min = v;
  }
  return Number.isFinite(min) ? min / 2 : 1e-6;
}

/** log10 of a density, floored. Densities span several orders of magnitude between
 *  the mode and the tails, which is exactly the part a fat-tail claim lives in, so
 *  the y axis is logarithmic and the floor keeps empty bins on the canvas. */
export function logDensity(value: number, floor: number): number {
  return Math.log10(Math.max(value, floor));
}

/** The share of bins where the real density falls inside the simulated 5-95% band.
 *  One number for "does the shape match", alongside the per-statistic verdicts. */
export function densityCoverage(band: DensityBand): number {
  let inside = 0;
  let counted = 0;
  for (let i = 0; i < band.real.length; i++) {
    // bins where nothing was ever observed or simulated say nothing either way
    if (band.real[i] === 0 && band.hi[i] === 0) continue;
    counted += 1;
    if (band.real[i] >= band.lo[i] && band.real[i] <= band.hi[i]) inside += 1;
  }
  return counted === 0 ? 0 : inside / counted;
}
