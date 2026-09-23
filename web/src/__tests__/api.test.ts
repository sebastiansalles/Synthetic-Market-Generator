import { afterEach, describe, expect, it, vi } from "vitest";
import { exportRun, horizon, ProfileSummary, RunSpec, simulate } from "../api";

const BTC = {
  periods_per_year: 365,
  annual_vol: 0.657,
} as ProfileSummary;

const EUR = {
  periods_per_year: 252,
  annual_vol: 0.09,
} as ProfileSummary;

describe("horizon", () => {
  // a bar count hides the calendar, which is what made BTC's dispersion surprising
  it("converts bars to years using the asset's own calendar", () => {
    expect(horizon(BTC, 1500).years).toBeCloseTo(4.11, 2);
    expect(horizon(EUR, 1500).years).toBeCloseTo(5.95, 2);
  });

  it("reports dispersion growing with the square root of the span", () => {
    const short = horizon(BTC, 365);
    const long = horizon(BTC, 365 * 4);
    expect(long.sdLogPrice / short.sdLogPrice).toBeCloseTo(2, 6);
  });

  it("brackets the terminal price symmetrically in log space", () => {
    const h = horizon(BTC, 1500);
    expect(h.lowMultiple * h.highMultiple).toBeCloseTo(1, 9);
    expect(h.highMultiple).toBeGreaterThan(8); // BTC really is this wide over 4 years
    expect(horizon(EUR, 1500).highMultiple).toBeLessThan(1.5); // and EUR/USD is not
  });
});

const RUN: RunSpec = {
  asset: "eurusd",
  nCandles: 500,
  seed: 2026,
  resolution: "weekly",
  preset: "flash_crash",
  startPrice: 1.1484,
};

function mockFetch(resp: Partial<Response> & { json?: () => Promise<unknown> }) {
  const fetchMock = vi.fn().mockResolvedValue({ ok: true, ...resp });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => vi.unstubAllGlobals());

describe("run spec serialization", () => {
  it("sends the whole workflow selection on /simulate", async () => {
    const fetchMock = mockFetch({ json: async () => ({ bars: [] }) });
    await simulate(RUN);
    const body = JSON.parse(fetchMock.mock.calls[0][1].body);
    expect(body).toEqual({
      asset: "eurusd",
      n_candles: 500,
      seed: 2026,
      resolution: "weekly",
      preset: "flash_crash",
      start_price: 1.1484,
      drift_annual: null, // null = the profile's calibrated drift
    });
  });

  it("sends an explicit zero drift rather than dropping it", async () => {
    const fetchMock = mockFetch({ json: async () => ({ bars: [] }) });
    await simulate({ ...RUN, driftAnnual: 0 });
    expect(JSON.parse(fetchMock.mock.calls[0][1].body).drift_annual).toBe(0);
  });

  it("sends the 'standard' preset as null — the API treats absence as unforced", async () => {
    const fetchMock = mockFetch({ json: async () => ({ bars: [] }) });
    await simulate({ ...RUN, preset: "standard" });
    expect(JSON.parse(fetchMock.mock.calls[0][1].body).preset).toBeNull();
  });

  it("surfaces the API's validation detail rather than a bare status code", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 422,
        json: async () => ({ detail: "jump_at 400 outside a 50-candle path" }),
      }),
    );
    await expect(simulate(RUN)).rejects.toThrow("jump_at 400 outside a 50-candle path");
  });
});

describe("exportRun", () => {
  it("downloads under the filename the server chose", async () => {
    const anchor = { href: "", download: "", click: vi.fn() };
    vi.stubGlobal("document", { createElement: () => anchor });
    vi.stubGlobal("URL", { createObjectURL: () => "blob:x", revokeObjectURL: vi.fn() });
    mockFetch({
      headers: {
        get: () => 'attachment; filename="eurusd_weekly_lean.zip"',
      } as unknown as Headers,
      blob: async () => new Blob(),
    });

    const name = await exportRun(RUN, "lean");
    expect(name).toBe("eurusd_weekly_lean.zip");
    expect(anchor.download).toBe("eurusd_weekly_lean.zip");
    expect(anchor.click).toHaveBeenCalled();
  });

  it("falls back to a sane name when the server sends no disposition", async () => {
    const anchor = { href: "", download: "", click: vi.fn() };
    vi.stubGlobal("document", { createElement: () => anchor });
    vi.stubGlobal("URL", { createObjectURL: () => "blob:x", revokeObjectURL: vi.fn() });
    mockFetch({
      headers: { get: () => null } as unknown as Headers,
      blob: async () => new Blob(),
    });

    expect(await exportRun(RUN, "csv")).toBe("eurusd.csv");
  });
});
