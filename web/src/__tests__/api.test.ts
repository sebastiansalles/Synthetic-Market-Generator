import { afterEach, describe, expect, it, vi } from "vitest";
import { exportRun, RunSpec, simulate } from "../api";

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
    });
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
