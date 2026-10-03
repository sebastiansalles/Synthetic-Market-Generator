import { create } from "zustand";
import { Candle } from "./engine/candles";
import { EnsembleResult } from "./engine/ensemble";
import { initLive, LiveState, reduceEvent, WsEvent } from "./engine/live";
import { Viewport, panBy, zoomAt } from "./engine/viewport";

export type Theme = "dark" | "light";
export type AppMode = "single" | "ensemble";

const storedTheme = (): Theme =>
  typeof localStorage !== "undefined" && localStorage.getItem("smg-theme") === "light"
    ? "light"
    : "dark";

interface ChartState {
  asset: string;
  bars: Candle[];
  seed: number;
  nCandles: number;
  resolution: string;
  preset: string;
  driftAnnual: number | null; // null = the profile's calibrated drift
  viewport: Viewport;
  status: string;
  live: LiveState | null;
  follow: boolean; // live mode: keep the tail in view until the user pans
  crosshair: { px: number; py: number } | null;
  theme: Theme;
  mode: AppMode;
  ensemble: EnsembleResult | null;
  ensembleBusy: boolean;
  marker: { index: number; label: string } | null; // scenario shock annotation
  runKey: string; // settings the current bars belong to; unchanged settings = no refetch
  setAsset: (a: string) => void;
  setBars: (bars: Candle[], asset: string) => void;
  setStatus: (s: string) => void;
  zoomAtPx: (anchorPx: number, factor: number, widthPx: number) => void;
  panByPx: (dxPx: number, widthPx: number) => void;
  resetView: () => void;
  startLive: () => void;
  applyWsEvent: (ev: WsEvent) => void;
  stopLive: () => void;
  /** Live session ends (done or user stop): keep the generated bars on screen
   *  instead of snapping back to a fresh static chart. */
  promoteLive: () => void;
  setCrosshair: (c: { px: number; py: number } | null) => void;
  setTheme: (t: Theme) => void;
  setMode: (m: AppMode) => void;
  setEnsemble: (e: EnsembleResult | null, busy: boolean) => void;
  setMarker: (m: { index: number; label: string } | null) => void;
  setRunKey: (k: string) => void;
}

const defaultViewport = (nBars: number): Viewport => ({
  offset: 0,
  candlesPerPixel: Math.max(0.2, nBars / 1500),
});

/** ~8 px per candle: wide enough to read a body and wick while still showing a
 *  meaningful stretch of the session on a normal window. */
export const LIVE_VIEWPORT: Viewport = { offset: 0, candlesPerPixel: 0.125 };

/** Bars currently on screen. In live mode the session lives in `live`, not `bars` —
 *  clamping navigation against `bars.length` (0 while live) pushed the view off the
 *  data the moment the user panned. */
const displayCount = (s: ChartState): number =>
  s.live ? s.live.bars.length + (s.live.forming ? 1 : 0) : s.bars.length;

export const useStore = create<ChartState>((set, get) => ({
  asset: "eurusd",
  bars: [],
  seed: 2026,
  nCandles: 1500,
  resolution: "daily",
  preset: "standard",
  driftAnnual: null,
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
  // A live session starts with a handful of bars but inherits the static view's zoom
  // (1500 candles squeezed to ~1 px each), so candles rendered as hairlines. Reset to
  // a zoom sized for a forming session; `follow` then keeps the tail in view.
  startLive: () => set({ live: initLive(), follow: true, viewport: LIVE_VIEWPORT }),
  applyWsEvent: (ev) => set((s) => (s.live ? { live: reduceEvent(s.live, ev) } : {})),
  stopLive: () => set({ live: null, bars: [] }), // no stale flash while static refetches
  promoteLive: () =>
    set((s) => (s.live ? { bars: s.live.bars, live: null } : {})),
  setCrosshair: (crosshair) => set({ crosshair }),
  theme: storedTheme(),
  setTheme: (theme) => {
    if (typeof localStorage !== "undefined") localStorage.setItem("smg-theme", theme);
    set({ theme });
  },
  mode: "single",
  ensemble: null,
  ensembleBusy: false,
  marker: null,
  runKey: "",
  setMode: (mode) => set({ mode }),
  setEnsemble: (ensemble, ensembleBusy) => set({ ensemble, ensembleBusy }),
  setMarker: (marker) => set({ marker }),
  setRunKey: (runKey) => set({ runKey }),
  // zooming never fights the live-follow; panning hands control to the user
  zoomAtPx: (anchorPx, factor, widthPx) =>
    set({ viewport: zoomAt(get().viewport, anchorPx, factor, widthPx, displayCount(get())) }),
  panByPx: (dxPx, widthPx) =>
    set({ viewport: panBy(get().viewport, dxPx, widthPx, displayCount(get())), follow: false }),
  resetView: () => set({ viewport: defaultViewport(displayCount(get())), follow: true }),
}));
