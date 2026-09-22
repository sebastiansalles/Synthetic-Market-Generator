import { create } from "zustand";
import { Candle } from "./engine/candles";
import { Viewport, MAX_CPP, panBy, zoomAt } from "./engine/viewport";

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

export const useStore = create<ChartState>((set, get) => ({
  asset: "eurusd",
  bars: [],
  seed: 2026,
  nCandles: 1500,
  viewport: { offset: 0, candlesPerPixel: 0.5 },
  status: "idle",
  setAsset: (asset) => set({ asset }),
  setBars: (bars, asset) =>
    set({ bars, asset, viewport: { offset: 0, candlesPerPixel: Math.max(0.2, bars.length / 1500) } }),
  setStatus: (status) => set({ status }),
  zoomAtPx: (anchorPx, factor) => set({ viewport: zoomAt(get().viewport, anchorPx, factor) }),
  panByPx: (dxPx, widthPx) =>
    set({ viewport: panBy(get().viewport, dxPx, widthPx, get().bars.length) }),
  resetView: () =>
    set({
      viewport: { offset: 0, candlesPerPixel: Math.max(MAX_CPP * 0.01, get().bars.length / 1500) },
    }),
}));
