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

  it("clamps live-mode panning against the streamed bars, not the empty static set", () => {
    // `bars` stays [] during a live session; clamping against it pinned the offset
    // to <= -1, so the first pan threw the view off the data entirely
    const s = useStore.getState();
    s.startLive();
    for (let i = 0; i < 40; i++) {
      useStore.getState().applyWsEvent({ type: "bar_open", bar_id: i, open: 1, state: 0 });
      useStore.getState().applyWsEvent({
        type: "bar_close", bar_id: i, high: 1.1, low: 0.9, close: 1.05, volume: 0, state: 0,
      });
    }
    useStore.setState({ viewport: { offset: 10, candlesPerPixel: 0.05 } });
    useStore.getState().panByPx(-200, 800); // drag left, towards the newest bars
    expect(useStore.getState().viewport.offset).toBeGreaterThan(0);
    expect(useStore.getState().viewport.offset).toBeLessThanOrEqual(39);
    useStore.getState().stopLive();
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
    useStore.getState().zoomAtPx(100, 1.2, 800);
    expect(useStore.getState().follow).toBe(true);
    useStore.getState().panByPx(10, 800);
    expect(useStore.getState().follow).toBe(false);
    useStore.getState().resetView();
    expect(useStore.getState().follow).toBe(true);
    useStore.getState().stopLive();
  });
});

describe("regime path in the store", () => {
  beforeEach(() => {
    useStore.setState({ asset: "eurusd", bars: [], states: [], live: null });
  });

  it("carries the regime path alongside the bars it belongs to", () => {
    useStore.getState().setBars([candle, candle, candle], "eurusd", [0, 1, 1]);
    expect(useStore.getState().states).toEqual([0, 1, 1]);
  });

  it("drops a stale run's states with its bars", () => {
    useStore.getState().setAsset("xauusd");
    useStore.getState().setBars([candle], "eurusd", [1]); // late eurusd response
    expect(useStore.getState().states).toEqual([]);
  });

  it("clears states on asset switch so the old market's regimes never colour the new one", () => {
    useStore.getState().setBars([candle, candle], "eurusd", [0, 1]);
    useStore.getState().setAsset("btcusd");
    expect(useStore.getState().states).toEqual([]);
  });

  // The heatmap the user just watched build used to vanish the instant the stream
  // ended: promoteLive kept `bars` but dropped the session, and the overlay was
  // live-only. The regime path has to survive the handover.
  it("promoteLive keeps the finished session's regimes on screen", () => {
    const s = useStore.getState();
    s.startLive();
    for (let i = 0; i < 3; i++) {
      useStore.getState().applyWsEvent({ type: "bar_open", bar_id: i, open: 1, state: i === 1 ? 1 : 0 });
      useStore.getState().applyWsEvent({
        type: "bar_close", bar_id: i, high: 1.1, low: 0.9, close: 1.05, volume: 0,
        state: i === 1 ? 1 : 0,
      });
    }
    useStore.getState().promoteLive();
    expect(useStore.getState().live).toBeNull();
    expect(useStore.getState().bars).toHaveLength(3);
    expect(useStore.getState().states).toEqual([0, 1, 0]);
  });

  // A bar_open for the NEXT candle arrives before its bar_close, so states can be
  // one longer than bars at the moment a session is stopped mid-candle. Trimming
  // keeps the heatmap indexed in the same space as the promoted bars.
  it("promoteLive trims a half-formed candle's state", () => {
    const s = useStore.getState();
    s.startLive();
    useStore.getState().applyWsEvent({ type: "bar_open", bar_id: 0, open: 1, state: 0 });
    useStore.getState().applyWsEvent({
      type: "bar_close", bar_id: 0, high: 1.1, low: 0.9, close: 1.05, volume: 0, state: 0,
    });
    useStore.getState().applyWsEvent({ type: "bar_open", bar_id: 1, open: 1, state: 1 });
    useStore.getState().promoteLive();
    expect(useStore.getState().bars).toHaveLength(1);
    expect(useStore.getState().states).toEqual([0]);
  });
});
