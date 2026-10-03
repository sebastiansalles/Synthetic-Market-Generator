import { useCallback, useEffect, useRef } from "react";
import { layoutCandles, volumeProfile } from "../engine/candles";
import { drawChart, THEMES } from "../engine/renderer";
import { followOffset, priceToY, wheelZoomFactor } from "../engine/viewport";
import { useStore } from "../store";

const AXIS_WIDTH = 64;
const VOLUME_PANE = 90;

/** Canvas candlestick chart: wheel zoom (anchored at cursor), drag pan, double-click
 *  reset. Redraws are coalesced to one per animation frame (rAF) and triggered by
 *  store changes OR canvas resizes (ResizeObserver). `?bench=1` runs a scripted
 *  pan/zoom loop with an fps readout to verify the 60fps gate. */
export function Chart() {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const dragRef = useRef<{ x: number } | null>(null);
  const rafRef = useRef(0);
  const fpsRef = useRef<HTMLDivElement>(null);
  const bench = new URLSearchParams(location.search).get("bench") === "1";

  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const { bars, viewport, live, theme } = useStore.getState();
    // live mode displays completed bars + the forming candle; static mode the fetched set
    const display =
      live !== null
        ? [
            ...live.bars,
            ...(live.forming
              ? [
                  {
                    timestamp: "",
                    open: live.forming.open,
                    high: live.forming.high,
                    low: live.forming.low,
                    close: live.forming.close,
                    volume: 0,
                  },
                ]
              : []),
          ]
        : bars;
    if (display.length === 0) return;
    const dpr = window.devicePixelRatio || 1;
    const cssW = canvas.clientWidth;
    const cssH = canvas.clientHeight;
    if (canvas.width !== Math.round(cssW * dpr) || canvas.height !== Math.round(cssH * dpr)) {
      canvas.width = Math.round(cssW * dpr);
      canvas.height = Math.round(cssH * dpr);
    }
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const priceHeight = cssH - VOLUME_PANE;
    // live: scale from COMPLETED bars so a forming wick can't jitter the whole axis
    const layout = layoutCandles(
      display,
      viewport,
      cssW - AXIS_WIDTH,
      priceHeight,
      live && live.bars.length >= 2 ? live.bars : undefined,
    );
    const { crosshair, marker } = useStore.getState();
    const crossInfo =
      crosshair && cssW - AXIS_WIDTH > 0
        ? {
            px: crosshair.px,
            py: crosshair.py,
            index: Math.min(
              display.length - 1,
              Math.max(0, Math.floor(viewport.offset + crosshair.px * viewport.candlesPerPixel)),
            ),
            price:
              layout.minPrice +
              (1 - crosshair.py / priceHeight) * (layout.maxPrice - layout.minPrice),
          }
        : undefined;
    const profile = volumeProfile(layout.visible)
      .map((b) => ({ ...b, y: priceToY(Math.exp(b.center), layout.minPrice, layout.maxPrice, priceHeight) }))
      .filter((b) => b.y >= 0 && b.y <= priceHeight);
    drawChart(
      ctx,
      layout,
      { width: cssW, height: cssH, priceHeight, axisWidth: AXIS_WIDTH },
      THEMES[theme],
      live ? { states: live.states, formingIndex: live.forming ? display.length - 1 : null } : undefined,
      profile,
      crossInfo,
      marker && marker.index < display.length ? marker : undefined,
    );
  }, []);

  const scheduleDraw = useCallback(() => {
    cancelAnimationFrame(rafRef.current);
    rafRef.current = requestAnimationFrame(draw);
  }, [draw]);

  useEffect(() => {
    const unsub = useStore.subscribe(scheduleDraw); // any state change -> one redraw/frame
    const canvas = canvasRef.current!;
    const ro = new ResizeObserver(scheduleDraw); // window resizes keep the canvas honest
    ro.observe(canvas);
    scheduleDraw();
    return () => {
      unsub();
      ro.disconnect();
      cancelAnimationFrame(rafRef.current);
    };
  }, [scheduleDraw]);

  // Wheel must be a NATIVE non-passive listener: React registers wheel handlers as
  // passive, so preventDefault() inside onWheel is silently ignored and the browser
  // zooms the whole page instead of the chart (ctrl+wheel especially, which is also
  // how trackpad pinch arrives).
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const rect = canvas.getBoundingClientRect();
      const w = canvas.clientWidth - AXIS_WIDTH;
      useStore
        .getState()
        .zoomAtPx(e.clientX - rect.left, wheelZoomFactor(e.deltaY, e.deltaMode, e.ctrlKey), w);
    };
    canvas.addEventListener("wheel", onWheel, { passive: false });
    return () => canvas.removeEventListener("wheel", onWheel);
  }, []);

  useEffect(() => {
    if (!bench) return;
    let frames = 0;
    let t0 = performance.now();
    let dir = 1;
    const id = setInterval(() => {
      const s = useStore.getState();
      const w = canvasRef.current?.clientWidth ?? 1200;
      if (frames % 120 === 0) dir *= -1;
      s.panByPx(6 * dir, w);
      if (frames % 60 === 0) s.zoomAtPx(w / 2, dir > 0 ? 1.05 : 0.95, w);
      frames += 1;
      const now = performance.now();
      if (now - t0 >= 1000) {
        fpsRef.current!.textContent = `${((frames * 1000) / (now - t0)).toFixed(0)} fps`;
        frames = 0;
        t0 = now;
      }
    }, 0);
    return () => clearInterval(id);
  }, [bench]);

  // live mode: follow the growing tail until the user takes over the viewport
  const live = useStore((s) => s.live);
  useEffect(() => {
    if (!live || live.bars.length === 0) return;
    const s = useStore.getState();
    if (!s.follow) return;
    const canvas = canvasRef.current;
    if (!canvas) return;
    const span = (canvas.clientWidth - AXIS_WIDTH) * s.viewport.candlesPerPixel;
    const target = followOffset(live.bars.length, span);
    if (Math.abs(s.viewport.offset - target) > 0.5) {
      useStore.setState({ viewport: { ...s.viewport, offset: target } });
    }
  }, [live]);

  return (
    <div className="chart-wrap">
      <canvas
        ref={canvasRef}
        className="chart-canvas"
        tabIndex={0}
        aria-label="candlestick chart: arrows pan, +/- zoom, wheel zooms, drag pans"
        onKeyDown={(e) => {
          const s = useStore.getState();
          const w = e.currentTarget.clientWidth - AXIS_WIDTH;
          if (e.key === "ArrowLeft") s.panByPx(w * 0.15, w);
          else if (e.key === "ArrowRight") s.panByPx(-w * 0.15, w);
          else if (e.key === "+" || e.key === "=") s.zoomAtPx(w / 2, 1 / 1.3, w);
          else if (e.key === "-") s.zoomAtPx(w / 2, 1.3, w);
          else if (e.key.toLowerCase() === "r") s.resetView();
          else return;
          e.preventDefault();
        }}
        onPointerDown={(e) => {
          e.currentTarget.setPointerCapture(e.pointerId);
          dragRef.current = { x: e.clientX };
        }}
        onPointerMove={(e) => {
          const rect = e.currentTarget.getBoundingClientRect();
          if (dragRef.current) {
            const dx = e.clientX - dragRef.current.x;
            dragRef.current.x = e.clientX;
            useStore.getState().panByPx(dx, e.currentTarget.clientWidth - AXIS_WIDTH);
          }
          useStore.getState().setCrosshair({ px: e.clientX - rect.left, py: e.clientY - rect.top });
        }}
        onPointerUp={() => (dragRef.current = null)}
        onPointerLeave={() => {
          dragRef.current = null;
          useStore.getState().setCrosshair(null);
        }}
        onDoubleClick={() => useStore.getState().resetView()}
      />
      {bench && <div ref={fpsRef} className="fps" />}
    </div>
  );
}
