/** Live-session state machine — pure reducer over WebSocket events. Unit-tested.
 *  The reducer is the single authority on what a tick/bar event DOES to the
 *  displayed session; rendering just draws the resulting state. */
import { Candle } from "./candles";

export interface FormingCandle {
  open: number;
  high: number;
  low: number;
  close: number;
  ticks: number;
}

export interface LiveState {
  bars: Candle[];
  forming: FormingCandle | null;
  states: number[]; // regime per completed+forming bar (HMM heatmap)
  done: boolean;
  error: string | null;
}

export type WsEvent =
  | { type: "generating"; n_candles: number }
  | { type: "bar_open"; bar_id: number; open: number; state: number }
  | { type: "tick"; bar_id: number; price: number }
  | { type: "bar_close"; bar_id: number; high: number; low: number; close: number; volume: number; state: number }
  | { type: "done"; n_candles: number }
  | { type: "error"; message: string };

export function initLive(): LiveState {
  return { bars: [], forming: null, states: [], done: false, error: null };
}

function isoDay(barId: number): string {
  return new Date(Date.UTC(2026, 0, 1) + barId * 86_400_000).toISOString();
}

export function reduceEvent(s: LiveState, ev: WsEvent): LiveState {
  switch (ev.type) {
    case "generating": // informational: the session is being built off-loop
      return s;
    case "bar_open": {
      const states = [...s.states];
      states[ev.bar_id] = ev.state;
      return {
        ...s,
        states,
        forming: { open: ev.open, high: ev.open, low: ev.open, close: ev.open, ticks: 0 },
      };
    }
    case "tick": {
      // only the currently-forming bar accepts ticks
      if (!s.forming || ev.bar_id !== s.bars.length) return s;
      const f = s.forming;
      return {
        ...s,
        forming: {
          ...f,
          close: ev.price,
          high: Math.max(f.high, ev.price),
          low: Math.min(f.low, ev.price),
          ticks: f.ticks + 1,
        },
      };
    }
    case "bar_close": {
      if (!s.forming || ev.bar_id !== s.bars.length) return s;
      const f = s.forming;
      const bar: Candle = {
        timestamp: isoDay(ev.bar_id),
        open: f.open,
        high: Math.max(ev.high, f.high),
        low: Math.min(ev.low, f.low),
        close: ev.close,
        volume: ev.volume,
      };
      const states = [...s.states];
      states[ev.bar_id] = ev.state;
      return { ...s, bars: [...s.bars, bar], forming: null, states };
    }
    case "done":
      return { ...s, done: true, forming: null };
    case "error":
      return { ...s, error: ev.message, done: true };
    default:
      return s;
  }
}
