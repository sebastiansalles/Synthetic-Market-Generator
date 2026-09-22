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
