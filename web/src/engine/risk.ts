/** Risk analytics over the ensemble path matrix — the questions a trader actually
 *  asks, which are not "where will price be" but "what happens to my position".
 *
 *  All of these are first-passage or tail quantities with no closed form under
 *  regime-switching GARCH-t. That is the whole reason the ensemble earns its keep:
 *  a barrier probability under plain GBM is a textbook formula, but add volatility
 *  clustering, Student-t innovations and a Markov regime and simulation is the only
 *  way to get the number. Pure functions, unit-tested; no rendering here.
 *
 *  ONE LIMITATION, AND IT MATTERS: the ensemble carries **close prices only** — no
 *  intrabar high/low. A barrier that a real bar pierced intrabar but closed back
 *  inside is therefore missed, so every hit probability here is a LOWER bound and
 *  every "neither" share an upper one. The bias grows as barriers tighten: a stop
 *  2% away on a 4%-daily-range asset is pierced far more often than the closes
 *  admit, while a 20% stop over 250 bars is barely affected. Quoting these numbers
 *  for tight stops without saying so would be dishonest. The fix is an ensemble of
 *  full OHLC paths (the tick bridge already builds them for /simulate) rather than
 *  anything in this file.
 */

/** Interpolated quantile of an already-sorted ascending array. Shared with
 *  `ensemble.ts`'s convention so a median here equals a median there. */
function quantileOfSorted(sorted: number[], q: number): number {
  if (sorted.length === 0) return NaN;
  const idx = Math.min(sorted.length - 1, Math.max(0, q * (sorted.length - 1)));
  const lo = Math.floor(idx);
  const frac = idx - lo;
  return sorted[lo] + (sorted[Math.min(sorted.length - 1, lo + 1)] - sorted[lo]) * frac;
}

export interface BarrierOutcome {
  pStop: number; // share of paths whose close breaches the stop first
  pTarget: number; // ...the target first
  pNeither: number; // still open at the horizon
  medianBarsToStop: number | null; // null when no path hit it
  medianBarsToTarget: number | null;
  /** Mean return of actually trading the rule: exit at whichever barrier breaks
   *  first, otherwise at the horizon. */
  expectancy: number;
  /** Mean return of the losing exits alone — what a stop really costs once
   *  gap-through is counted, which is strictly worse than the stop you set. */
  meanStopExit: number;
  nPaths: number;
}

/**
 * First-passage outcome of a stop/target pair, plus the expectancy of trading it.
 *
 * `stopPct` is negative and `targetPct` positive, both as fractions of the start
 * price (-0.08 and 0.15 for "8% stop, 15% target"). A long position is assumed;
 * for a short, mirror the barriers before calling.
 *
 * Exits are booked at the **close that breached the barrier**, not at the barrier
 * itself. That is deliberate and it is the difference between a backtest and a
 * fantasy: a path can gap straight through a stop and close well below it, so
 * booking the nominal -8% would quietly assume a fill nobody would get.
 * `meanStopExit` reports how much worse than the stop the average loss actually
 * is. Note that closes cannot straddle both barriers at once, so unlike an
 * OHLC-based test there are no ambiguous same-bar ties to adjudicate.
 */
export function barrierOutcome(
  paths: number[][],
  startPrice: number,
  stopPct: number,
  targetPct: number,
): BarrierOutcome {
  const empty: BarrierOutcome = {
    pStop: 0, pTarget: 0, pNeither: 1, medianBarsToStop: null,
    medianBarsToTarget: null, expectancy: 0, meanStopExit: 0, nPaths: 0,
  };
  if (paths.length === 0 || !(startPrice > 0)) return empty;
  if (!(stopPct < 0) || !(targetPct > 0)) return empty;

  const stopLevel = startPrice * (1 + stopPct);
  const targetLevel = startPrice * (1 + targetPct);
  const barsToStop: number[] = [];
  const barsToTarget: number[] = [];
  const stopExits: number[] = [];
  let neither = 0;
  let pnl = 0;

  for (const path of paths) {
    let hit = -1;
    let isStop = false;
    for (let t = 0; t < path.length; t++) {
      if (path[t] <= stopLevel) { hit = t; isStop = true; break; }
      if (path[t] >= targetLevel) { hit = t; break; }
    }
    if (hit < 0) {
      neither += 1;
      pnl += path.length > 0 ? path[path.length - 1] / startPrice - 1 : 0;
      continue;
    }
    const ret = path[hit] / startPrice - 1;
    pnl += ret;
    if (isStop) { barsToStop.push(hit); stopExits.push(ret); } else { barsToTarget.push(hit); }
  }

  const n = paths.length;
  barsToStop.sort((a, b) => a - b);
  barsToTarget.sort((a, b) => a - b);
  return {
    pStop: barsToStop.length / n,
    pTarget: barsToTarget.length / n,
    pNeither: neither / n,
    medianBarsToStop: barsToStop.length ? quantileOfSorted(barsToStop, 0.5) : null,
    medianBarsToTarget: barsToTarget.length ? quantileOfSorted(barsToTarget, 0.5) : null,
    expectancy: pnl / n,
    meanStopExit: stopExits.length
      ? stopExits.reduce((s, v) => s + v, 0) / stopExits.length
      : 0,
    nPaths: n,
  };
}

/** Deepest peak-to-trough decline within one path, as a positive fraction. */
export function pathMaxDrawdown(path: number[]): number {
  let peak = -Infinity;
  let worst = 0;
  for (const p of path) {
    if (p > peak) peak = p;
    if (peak > 0) {
      const dd = 1 - p / peak;
      if (dd > worst) worst = dd;
    }
  }
  return worst;
}

export interface DrawdownStats {
  quantiles: number[]; // the q's asked for
  values: number[]; // max drawdown at each q, positive fractions
  mean: number;
  worst: number;
}

/**
 * Distribution of max drawdown across the ensemble.
 *
 * The number people mean by "the drawdown" is a quantile of this, never the mean:
 * drawdown distributions are strongly right-skewed, so the average path's worst
 * loss badly understates what a bad path does. The 95th percentile is the one
 * worth sizing against.
 *
 * Drawdown runs from each path's own running peak, including peaks above the
 * start price — a path that doubles and halves back drew down 50%, which is what
 * the position would have felt even though it ended level.
 */
export function drawdownDistribution(
  paths: number[][],
  quantiles: number[] = [0.5, 0.75, 0.95, 0.99],
): DrawdownStats {
  if (paths.length === 0) {
    return { quantiles, values: quantiles.map(() => 0), mean: 0, worst: 0 };
  }
  const dds = paths.map(pathMaxDrawdown).sort((a, b) => a - b);
  return {
    quantiles,
    values: quantiles.map((q) => quantileOfSorted(dds, q)),
    mean: dds.reduce((s, v) => s + v, 0) / dds.length,
    worst: dds[dds.length - 1],
  };
}

export interface TailRisk {
  alpha: number;
  /** Value at Risk: the loss the terminal return does not exceed with probability
   *  1 - alpha, as a positive fraction (0.22 means "down 22%"). */
  var: number;
  /** Expected shortfall: the MEAN loss given that VaR is breached. Always at least
   *  VaR, and the honest number of the two — VaR says where the cliff starts, ES
   *  says how far down it goes, and only ES notices that this model has fat tails
   *  at all. */
  es: number;
}

/**
 * VaR and expected shortfall of the terminal return, at each alpha.
 *
 * Returns losses as positive fractions. With `alpha = 0.05`, `var` is the 5th
 * percentile terminal loss and `es` the average of the worst 5% — the quantity a
 * fat-tailed model exists to produce, and the one a lognormal guide gets wrong.
 */
export function terminalRisk(
  paths: number[][],
  startPrice: number,
  alphas: number[] = [0.05, 0.01],
): TailRisk[] {
  if (paths.length === 0 || !(startPrice > 0)) {
    return alphas.map((alpha) => ({ alpha, var: 0, es: 0 }));
  }
  const rets = paths
    .map((p) => (p.length ? p[p.length - 1] / startPrice - 1 : 0))
    .sort((a, b) => a - b);
  return alphas.map((alpha) => {
    const v = quantileOfSorted(rets, alpha);
    // the worst ceil(alpha*n) observations; at least one, so ES is always defined
    const k = Math.max(1, Math.ceil(alpha * rets.length));
    const tail = rets.slice(0, k);
    return {
      alpha,
      var: Math.max(0, -v),
      es: Math.max(0, -(tail.reduce((s, x) => s + x, 0) / tail.length)),
    };
  });
}
