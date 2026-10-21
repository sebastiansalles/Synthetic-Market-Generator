import { describe, expect, it } from "vitest";
import {
  bandGeometry,
  DensityBand,
  densityCoverage,
  densityFloor,
  formatMetric,
  logDensity,
} from "../engine/evidence";

describe("formatMetric", () => {
  it("renders returns, shares and bare numbers in the units a reader thinks in", () => {
    expect(formatMetric(0.00566, "ret")).toBe("0.566%");
    expect(formatMetric(0.1554, "pct")).toBe("15.5%");
    expect(formatMetric(2.5532, "num")).toBe("2.553");
    expect(formatMetric(148231.7, "num")).toBe("148,232");
    expect(formatMetric(NaN, "num")).toBe("—");
  });
});

describe("bandGeometry", () => {
  it("places the real value inside the rail when it is inside the band", () => {
    const g = bandGeometry(0.0055, 0.0062, 0.00566, 0.00573);
    expect(g.realPos).toBeGreaterThan(g.bandLeft);
    expect(g.realPos).toBeLessThan(g.bandLeft + g.bandWidth);
    expect(g.bandLeft).toBeGreaterThan(0);
    expect(g.bandLeft + g.bandWidth).toBeLessThan(100);
  });

  // A failure that clamps to the rail's edge loses the difference between "just
  // missed the band" and "nowhere near it" — the whole point of showing the band.
  it("keeps a real value far outside the band on the rail, and visibly outside", () => {
    const g = bandGeometry(0.518, 0.529, 0.155, 0.522);
    expect(g.realPos).toBeGreaterThanOrEqual(0);
    expect(g.realPos).toBeLessThan(g.bandLeft);
    expect(g.bandLeft + g.bandWidth).toBeLessThanOrEqual(100);
  });

  it("gives a degenerate band a visible width instead of a zero-width sliver", () => {
    const g = bandGeometry(0.5, 0.5, 0.5, 0.5);
    expect(g.bandWidth).toBeGreaterThan(0);
    expect(Number.isFinite(g.bandLeft)).toBe(true);
    expect(Number.isFinite(g.realPos)).toBe(true);
  });

  it("survives a band of exact zeros without producing NaN", () => {
    const g = bandGeometry(0, 0, 0, 0);
    for (const v of [g.bandLeft, g.bandWidth, g.realPos, g.medianPos]) {
      expect(Number.isFinite(v)).toBe(true);
    }
  });
});

const band = (over: Partial<DensityBand> = {}): DensityBand => ({
  centers: [-0.02, -0.01, 0, 0.01, 0.02],
  real: [0, 2, 40, 2, 0],
  lo: [0, 1, 35, 1, 0],
  median: [0, 2, 40, 2, 0],
  hi: [0, 3, 45, 3, 0],
  real_sd: 0.0057,
  ...over,
});

describe("density band", () => {
  it("floors the log axis below the smallest positive density, never at zero", () => {
    const floor = densityFloor(band());
    expect(floor).toBeGreaterThan(0);
    expect(floor).toBeLessThan(1);
    expect(Number.isFinite(logDensity(0, floor))).toBe(true);
    expect(logDensity(0, floor)).toBe(Math.log10(floor));
  });

  it("falls back to a positive floor when every bin is empty", () => {
    const floor = densityFloor(band({ real: [0, 0], lo: [0, 0], median: [0, 0], hi: [0, 0] }));
    expect(floor).toBeGreaterThan(0);
  });

  it("scores coverage over the bins that carry information", () => {
    expect(densityCoverage(band())).toBe(1); // the all-zero tail bins are not counted
    const miss = band({ real: [0, 9, 40, 9, 0] }); // two bins outside the band
    expect(densityCoverage(miss)).toBeCloseTo(1 / 3, 5);
  });

  it("reports zero coverage rather than NaN for an empty band", () => {
    expect(densityCoverage(band({ real: [], lo: [], median: [], hi: [], centers: [] }))).toBe(0);
  });
});
