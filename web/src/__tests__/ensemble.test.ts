import { describe, expect, it } from "vitest";
import { histogram, percentileBand, terminalStats } from "../engine/ensemble";

// 4 paths × 3 candles, terminal values 8, 4, 2, 1
const paths = [
  [1, 2, 8],
  [1, 2, 4],
  [1, 1, 2],
  [1, 1, 1],
];

describe("ensemble math", () => {
  it("percentileBand interpolates across paths per time step", () => {
    const med = percentileBand(paths, 0.5);
    expect(med[0]).toBeCloseTo(1, 9);
    expect(med[1]).toBeCloseTo(1.5, 9);
    expect(med[2]).toBeCloseTo(3, 9); // median of {8,4,2,1} = (2+4)/2
    const p05 = percentileBand(paths, 0.05);
    expect(p05[2]).toBeGreaterThanOrEqual(1);
    expect(p05[2]).toBeLessThan(p05[0] === p05[2] ? 2 : 8);
  });

  it("terminalStats reports the distribution and loss probability", () => {
    const s = terminalStats(paths, 2);
    expect(s.median).toBeCloseTo(3, 9);
    expect(s.p05).toBeCloseTo(1.15, 9); // interpolated: 1 + 0.15·(2−1)
    expect(s.p95).toBeCloseTo(7.4, 9); // interpolated: 4 + 0.85·(8−4)
    expect(s.probLoss).toBeCloseTo(0.25, 9); // only the ×1 path ends below the start of 2
    expect(s.probDoubling).toBeCloseTo(0.5, 9); // 2 of 4 end at >= 4
  });

  it("histogram bins the terminal values and conserves count", () => {
    const h = histogram(paths.map((p) => p[2]), 4);
    expect(h.counts.reduce((a, b) => a + b, 0)).toBe(4);
    expect(Math.max(...h.counts)).toBeGreaterThan(0);
    expect(h.centers).toHaveLength(4);
  });
});
