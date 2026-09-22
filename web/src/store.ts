import { create } from "zustand";
import { Candle } from "./engine/candles";
import { initLive, LiveState, reduceEvent, WsEvent } from "./engine/live";
import { Viewport, panBy, zoomAt } from "./engine/viewport";

interface ChartState {
  asset: string;
  bars: Candle[];
  seed: number;
  nCandles: number;
  viewport: Viewport;
  status: string;
  live: LiveState | null;
  follow: boolean; // live mode: keep the tail in view until the user pans
  crosshair: { px: number; py: number } | null;
  setAsset: (a: string) => void;
  setBars: (bars: Candle[], asset: string) => void;
  setStatus: (s: string) => void;
  zoomAtPx: (anchorPx: number, factor: number) => void;
  panByPx: (dxPx: number, widthPx: number) => void;
  resetView: () => void;
  startLive: () => void;
  applyWsEvent: (ev: WsEvent) => void;
  stopLive: () => void;
  setCrosshair: (c: { px: number; py: number } | null) => void;
}

const defaultViewport = (nBars: number): Viewport => ({
  offset: 0,
  candlesPerPixel: Math.max(0.2, nBars / 1500),
});

export const useStore = create<ChartState>((set, get) => ({
  asset: "eurusd",
  bars: [],
  seed: 2026,
  nCandles: 1500,
  viewport: { offset: 0, candlesPerPixel: 1 }, // matches defaultViewport(1500): no snap on first data
  status: "idle",
  live: null,
  follow: true,
  crosshair: null,
  // switching markets clears bars immediately: no stale-frame glitch while the
  // new simulation streams in (the UI shows the loading state instead)
  setAsset: (asset) => set((s) => (asset === s.asset ? {} : { asset, bars: [] })),
  // stale responses are dropped: a late reply from a previously-selected asset
  // must not flip the chart back (the opening glitch)
  setBars: (bars, asset) =>
    set((s) => {
      if (asset !== s.asset) return {};
      return { bars, viewport: s.bars.length > 0 ? s.viewport : defaultViewport(bars.length) };
    }),
  setStatus: (status) => set({ status }),
  startLive: () => set({ live: initLive(), follow: true }),
  applyWsEvent: (ev) => set((s) => (s.live ? { live: reduceEvent(s.live, ev) } : {})),
  stopLive: () => set({ live: null, bars: [] }), // no stale flash while static refetches
  setCrosshair: (crosshair) => set({ crosshair }),
  // zooming never fights the live-follow; panning hands control to the user
  zoomAtPx: (anchorPx, factor) => set({ viewport: zoomAt(get().viewport, anchorPx, factor) }),
  panByPx: (dxPx, widthPx) =>
    set({ viewport: panBy(get().viewport, dxPx, widthPx, get().bars.length), follow: false }),
  resetView: () => set({ viewport: defaultViewport(get().bars.length), follow: true }),
}));
