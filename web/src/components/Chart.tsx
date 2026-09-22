import { useCallback, useEffect, useRef } from "react";
import { layoutCandles } from "../engine/candles";
import { drawChart } from "../engine/renderer";
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
    const { bars, viewport } = useStore.getState();
    if (bars.length === 0) return;
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
    const layout = layoutCandles(bars, viewport, cssW - AXIS_WIDTH, priceHeight);
    drawChart(ctx, layout, { width: cssW, height: cssH, priceHeight, axisWidth: AXIS_WIDTH });
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
      if (frames % 60 === 0) s.zoomAtPx(w / 2, dir > 0 ? 1.05 : 0.95);
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
          else if (e.key === "+" || e.key === "=") s.zoomAtPx(w / 2, 1 / 1.3);
          else if (e.key === "-") s.zoomAtPx(w / 2, 1.3);
          else if (e.key.toLowerCase() === "r") s.resetView();
          else return;
          e.preventDefault();
        }}
        onWheel={(e) => {
          e.preventDefault();
          const rect = e.currentTarget.getBoundingClientRect();
          useStore.getState().zoomAtPx(e.clientX - rect.left, e.deltaY > 0 ? 1.15 : 1 / 1.15);
        }}
        onPointerDown={(e) => {
          e.currentTarget.setPointerCapture(e.pointerId);
          dragRef.current = { x: e.clientX };
        }}
        onPointerMove={(e) => {
          if (!dragRef.current) return;
          const dx = e.clientX - dragRef.current.x;
          dragRef.current.x = e.clientX;
          useStore.getState().panByPx(dx, e.currentTarget.clientWidth - AXIS_WIDTH);
        }}
        onPointerUp={() => (dragRef.current = null)}
        onDoubleClick={() => useStore.getState().resetView()}
      />
      {bench && <div ref={fpsRef} className="fps" />}
    </div>
  );
}
