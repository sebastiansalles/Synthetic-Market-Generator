import { describe, expect, it } from "vitest";
import {
  barrierOutcome,
  drawdownDistribution,
  pathMaxDrawdown,
  terminalRisk,
} from "../engine/risk";

/** Deterministic PRNG so the Monte Carlo checks below are reproducible. */
function mulberry32(seed: number) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** Driftless random walk in LOG price — the one process whose first-passage
 *  probabilities have a closed form to check against. */
function gbmPaths(nPaths: number, nSteps: number, sigma: number, seed: number): number[][] {
  const rnd = mulberry32(seed);
  const paths: number[][] = [];
  for (let i = 0; i < nPaths; i++) {
    const path = new Array<number>(nSteps);
    let logp = 0;
    for (let t = 0; t < nSteps; t++) {
      // Box-Muller, one normal per step
      const u = Math.max(1e-12, rnd());
      const z = Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * rnd());
      logp += sigma * z;
      path[t] = 100 * Math.exp(logp);
    }
    paths.push(path);
  }
  return paths;
}

describe("barrierOutcome", () => {
  it("matches the gambler's-ruin formula for driftless log-price", () => {
    // For Brownian motion started at 0 with absorbing barriers a<0<b, the chance of
    // reaching b first is |a|/(b-a) and it does not depend on sigma at all.
    const stopPct = -0.05;
    const targetPct = 0.15;
    const a = Math.log(1 + stopPct);
    const b = Math.log(1 + targetPct);
    const pTargetExact = -a / (b - a);

    const paths = gbmPaths(3000, 1200, 0.01, 12345);
    const out = barrierOutcome(paths, 100, stopPct, targetPct);

    // nearly every path resolves over this horizon, so the two hit probabilities
    // carry the whole mass and can be compared to the continuous-time answer
    expect(out.pNeither).toBeLessThan(0.02);
    expect(out.pTarget).toBeCloseTo(pTargetExact, 1);
    expect(Math.abs(out.pTarget - pTargetExact)).toBeLessThan(0.035);
    expect(out.pStop + out.pTarget + out.pNeither).toBeCloseTo(1, 10);
  });

  it("is symmetric for symmetric barriers in log space", () => {
    const up = 0.1;
    const down = 1 / (1 + up) - 1; // same log distance below
    const paths = gbmPaths(2000, 1500, 0.01, 999);
    const out = barrierOutcome(paths, 100, down, up);
    expect(out.pNeither).toBeLessThan(0.02);
    expect(Math.abs(out.pStop - out.pTarget)).toBeLessThan(0.05);
  });

  it("books the exit at the breaching close, not the nominal barrier", () => {
    // one path gaps straight through a -10% stop and closes 25% down
    const out = barrierOutcome([[100, 75]], 100, -0.1, 0.5);
    expect(out.pStop).toBe(1);
    expect(out.meanStopExit).toBeCloseTo(-0.25, 10); // not -0.10
    expect(out.expectancy).toBeCloseTo(-0.25, 10);
  });

  it("takes whichever barrier the path reaches first", () => {
    const toTarget = [100, 105, 112]; // target at +10% before any stop
    const toStop = [100, 97, 91]; // stop at -8% first
    const out = barrierOutcome([toTarget, toStop], 100, -0.08, 0.1);
    expect(out.pTarget).toBe(0.5);
    expect(out.pStop).toBe(0.5);
    expect(out.medianBarsToTarget).toBe(2);
    expect(out.medianBarsToStop).toBe(2);
    expect(out.pNeither).toBe(0);
  });

  it("counts an unresolved path at its terminal value", () => {
    const out = barrierOutcome([[100, 101, 103]], 100, -0.5, 0.5);
    expect(out.pNeither).toBe(1);
    expect(out.expectancy).toBeCloseTo(0.03, 10);
    expect(out.medianBarsToStop).toBeNull();
    expect(out.medianBarsToTarget).toBeNull();
  });

  it("refuses barriers on the wrong side of the start", () => {
    const paths = [[100, 90, 120]];
    expect(barrierOutcome(paths, 100, 0.05, 0.1).nPaths).toBe(0); // stop above start
    expect(barrierOutcome(paths, 100, -0.05, -0.1).nPaths).toBe(0); // target below
    expect(barrierOutcome([], 100, -0.05, 0.1).nPaths).toBe(0);
  });
});

describe("pathMaxDrawdown", () => {
  it("measures from the running peak, including peaks above the start", () => {
    // doubles then halves back to the start: felt like -50% even though it ended level
    expect(pathMaxDrawdown([100, 200, 100])).toBeCloseTo(0.5, 10);
  });

  it("is zero for a monotonically rising path", () => {
    expect(pathMaxDrawdown([100, 110, 120, 130])).toBe(0);
  });

  it("keeps the deepest trough, not the last one", () => {
    expect(pathMaxDrawdown([100, 50, 100, 80])).toBeCloseTo(0.5, 10);
  });
});

describe("drawdownDistribution", () => {
  it("orders quantiles and brackets them with mean and worst", () => {
    const paths = [
      [100, 95, 100],
      [100, 80, 100],
      [100, 50, 100],
      [100, 99, 100],
    ];
    const d = drawdownDistribution(paths, [0.5, 0.95]);
    expect(d.values[0]).toBeLessThanOrEqual(d.values[1]);
    expect(d.worst).toBeCloseTo(0.5, 10);
    expect(d.mean).toBeGreaterThan(0);
    expect(d.mean).toBeLessThanOrEqual(d.worst);
  });

  it("survives an empty ensemble", () => {
    const d = drawdownDistribution([], [0.5, 0.95]);
    expect(d.values).toEqual([0, 0]);
    expect(d.worst).toBe(0);
  });
});

describe("terminalRisk", () => {
  it("computes VaR and ES from the terminal tail", () => {
    // terminal returns -0.50 .. +0.49 in 0.01 steps, 100 paths
    const paths = Array.from({ length: 100 }, (_, i) => [100, 100 * (1 + (i - 50) / 100)]);
    const [a05] = terminalRisk(paths, 100, [0.05]);
    // worst 5 returns are -0.50..-0.46, mean -0.48
    expect(a05.es).toBeCloseTo(0.48, 10);
    expect(a05.var).toBeCloseTo(0.4505, 3);
  });

  it("always reports ES at least as large as VaR", () => {
    const paths = gbmPaths(500, 100, 0.02, 7);
    for (const r of terminalRisk(paths, 100, [0.01, 0.05, 0.1])) {
      expect(r.es).toBeGreaterThanOrEqual(r.var - 1e-12);
    }
  });

  it("reports no loss when every path gains", () => {
    const paths = [
      [100, 120],
      [100, 130],
    ];
    const [r] = terminalRisk(paths, 100, [0.05]);
    expect(r.var).toBe(0);
    expect(r.es).toBe(0);
  });
});
