import { create } from "zustand";
import { Candle } from "./engine/candles";
import { Viewport, panBy, zoomAt } from "./engine/viewport";

interface ChartState {
  asset: string;
  bars: Candle[];
  seed: number;
  nCandles: number;
  viewport: Viewport;
  status: string;
  setAsset: (a: string) => void;
  setBars: (bars: Candle[], asset: string) => void;
  setStatus: (s: string) => void;
  zoomAtPx: (anchorPx: number, factor: number) => void;
  panByPx: (dxPx: number, widthPx: number) => void;
  resetView: () => void;
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
  viewport: { offset: 0, candlesPerPixel: 0.5 },
  status: "idle",
  // switching markets clears bars immediately: no stale-frame glitch while the
  // new simulation streams in (the UI shows the loading state instead)
  setAsset: (asset) => set((s) => (asset === s.asset ? {} : { asset, bars: [] })),
  // viewport is preserved on re-seeds (bars exist); a fresh load (after an asset
  // switch, or the first load) gets the overview zoom
  setBars: (bars, asset) =>
    set((s) => ({
      bars,
      asset,
      viewport: s.bars.length > 0 ? s.viewport : defaultViewport(bars.length),
    })),
  setStatus: (status) => set({ status }),
  zoomAtPx: (anchorPx, factor) => set({ viewport: zoomAt(get().viewport, anchorPx, factor) }),
  panByPx: (dxPx, widthPx) =>
    set({ viewport: panBy(get().viewport, dxPx, widthPx, get().bars.length) }),
  resetView: () => set({ viewport: defaultViewport(get().bars.length) }),
}));
