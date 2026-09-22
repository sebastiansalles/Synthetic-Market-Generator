import { describe, expect, it } from "vitest";
import { heatColor, initLive, reduceEvent, WsEvent } from "../engine/live";

function session(events: WsEvent[]) {
  let s = initLive();
  for (const ev of events) s = reduceEvent(s, ev);
  return s;
}

const open = (id: number, state = 0): WsEvent => ({ type: "bar_open", bar_id: id, open: 100, state });
const tick = (id: number, price: number): WsEvent => ({ type: "tick", bar_id: id, price });
const close = (id: number, state = 0): WsEvent => ({
  type: "bar_close", bar_id: id, high: 101, low: 99, close: 100.5, volume: 10, state,
});

describe("live reducer", () => {
  it("bar_open initializes the forming candle and records the regime", () => {
    const s = session([open(0, 1)]);
    expect(s.forming).toEqual({ open: 100, high: 100, low: 100, close: 100, ticks: 0 });
    expect(s.states[0]).toBe(1);
  });

  it("ticks extend the forming candle's wick and close; late ticks are ignored", () => {
    let s = session([open(0), tick(0, 100.4), tick(0, 99.6), tick(0, 100.9)]);
    expect(s.forming).toMatchObject({ high: 100.9, low: 99.6, close: 100.9, ticks: 3 });
    s = reduceEvent(s, tick(1, 50)); // wrong bar_id: no effect
    expect(s.forming?.close).toBe(100.9);
  });

  it("bar_close commits the candle (envelope covers the forming wick) and clears forming", () => {
    const s = session([open(0), tick(0, 100.8), tick(0, 99.2), close(0)]);
    expect(s.bars).toHaveLength(1);
    expect(s.bars[0].high).toBeGreaterThanOrEqual(100.8);
    expect(s.bars[0].low).toBeLessThanOrEqual(99.2);
    expect(s.forming).toBeNull();
    expect(s.states[0]).toBe(0);
  });

  it("done terminates the session; errors surface", () => {
    expect(session([open(0), close(0), { type: "done", n_candles: 1 }]).done).toBe(true);
    const bad = reduceEvent(initLive(), { type: "error", message: "boom" });
    expect(bad.done).toBe(true);
    expect(bad.error).toBe("boom");
  });

  it("heatmap colors distinguish calm from turbulent", () => {
    expect(heatColor(0)).not.toBe(heatColor(1));
    expect(heatColor(1)).toContain("248, 81, 73");
  });
});
