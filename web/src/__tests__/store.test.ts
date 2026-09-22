import { beforeEach, describe, expect, it } from "vitest";
import { useStore } from "../store";

const candle = {
  timestamp: "2026-01-01T00:00:00Z",
  open: 1, high: 1.1, low: 0.9, close: 1.05, volume: 0,
};

describe("store", () => {
  beforeEach(() => {
    useStore.setState({ asset: "eurusd", bars: [], viewport: { offset: 0, candlesPerPixel: 0.5 } });
  });

  it("keeps the viewport across re-seeds of the same asset", () => {
    const s = useStore.getState();
    s.setBars([candle, candle], "eurusd");
    useStore.setState({ viewport: { offset: 42, candlesPerPixel: 0.3 } });
    useStore.getState().setBars([candle, candle, candle], "eurusd"); // new seed arrives
    expect(useStore.getState().viewport.offset).toBe(42);
    expect(useStore.getState().viewport.candlesPerPixel).toBe(0.3);
  });

  it("clears bars on asset switch and resets the viewport for the new asset", () => {
    const s = useStore.getState();
    s.setBars([candle, candle], "eurusd");
    useStore.setState({ viewport: { offset: 42, candlesPerPixel: 0.3 } });
    useStore.getState().setAsset("xauusd");
    expect(useStore.getState().bars).toEqual([]);
    useStore.getState().setBars([candle, candle, candle], "xauusd");
    expect(useStore.getState().viewport.offset).toBe(0);
  });
});

describe("store hardening", () => {
  beforeEach(() => {
    useStore.setState({ asset: "eurusd", bars: [], live: null, follow: true });
  });

  it("drops stale setBars from a previously selected asset (opening glitch)", () => {
    const s = useStore.getState();
    s.setAsset("xauusd");
    useStore.getState().setBars([candle, candle], "eurusd"); // late eurusd response
    expect(useStore.getState().asset).toBe("xauusd");
    expect(useStore.getState().bars).toEqual([]);
  });

  it("stopLive clears the session so the static refetch has nothing stale to flash", () => {
    const s = useStore.getState();
    s.startLive();
    useStore.getState().applyWsEvent({ type: "bar_open", bar_id: 0, open: 1, state: 0 });
    useStore.getState().stopLive();
    expect(useStore.getState().live).toBeNull();
    expect(useStore.getState().bars).toEqual([]);
  });

  it("zoom keeps live-follow; pan and reset hand control back", () => {
    const s = useStore.getState();
    s.startLive();
    useStore.getState().zoomAtPx(100, 1.2);
    expect(useStore.getState().follow).toBe(true);
    useStore.getState().panByPx(10, 800);
    expect(useStore.getState().follow).toBe(false);
    useStore.getState().resetView();
    expect(useStore.getState().follow).toBe(true);
    useStore.getState().stopLive();
  });
});
