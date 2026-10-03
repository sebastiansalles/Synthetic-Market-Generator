import { useCallback, useEffect, useRef } from "react";
import { ProfileSummary } from "../api";
import { EnsembleResult, histogram, percentileBand, terminalStats } from "../engine/ensemble";
import { THEMES } from "../engine/renderer";
import { useStore } from "../store";

/** The distribution over N candles: percentile fan of hundreds of seeded paths,
 *  a terminal-price histogram, and the headline risk stats. This is the honest
 *  answer the engine gives — not one path, the whole cone. */

const QUANTILES = [0.05, 0.25, 0.5, 0.75, 0.95] as const;

function drawEnsemble(
  ctx: CanvasRenderingContext2D,
  data: EnsembleResult,
  width: number,
  height: number,
  theme: (typeof THEMES)["dark" | "light"],
) {
  const histW = Math.min(190, Math.round(width * 0.22));
  const fanW = width - histW - 8;
  const axisH = 26;
  const fanH = height - axisH;

  ctx.fillStyle = theme.bg;
  ctx.fillRect(0, 0, width, height);

  const bands = QUANTILES.map((q) => percentileBand(data.paths, q));
  const all = data.paths.flat();
  let lo = Math.min(...all);
  let hi = Math.max(...all);
  // log scale fits multiplicative dispersion; pad so edges breathe
  lo = Math.exp(Math.log(lo) - 0.04 * (Math.log(hi) - Math.log(lo)));
  hi = Math.exp(Math.log(hi) + 0.04 * (Math.log(hi) - Math.log(lo)));
  const yOf = (p: number) => ((Math.log(hi) - Math.log(p)) / (Math.log(hi) - Math.log(lo))) * fanH;
  const xOf = (t: number) => (t / (data.n_candles - 1)) * fanW;

  // grid + price axis
  ctx.strokeStyle = theme.grid;
  ctx.fillStyle = theme.axisText;
  ctx.lineWidth = 1;
  ctx.font = "11px ui-monospace, monospace";
  ctx.textAlign = "left";
  ctx.textBaseline = "middle";
  for (let i = 1; i <= 4; i++) {
    const p = Math.exp(Math.log(lo) + (i / 5) * (Math.log(hi) - Math.log(lo)));
    const y = Math.round(yOf(p)) + 0.5;
    ctx.beginPath();
    ctx.moveTo(0, y);
    ctx.lineTo(fanW, y);
    ctx.stroke();
    ctx.fillText(p >= 100 ? p.toFixed(0) : p.toFixed(p >= 10 ? 1 : 3), fanW + 6, y);
  }

  // fan bands, widest first
  const fills: [number, number, string][] = [
    [0, 4, theme.accentSoft],
    [1, 3, theme.accent],
  ];
  for (const [a, b, color] of fills) {
    ctx.fillStyle = color;
    ctx.globalAlpha = a === 0 ? 0.35 : 0.4;
    ctx.beginPath();
    ctx.moveTo(xOf(0), yOf(bands[a][0]));
    for (let t = 1; t < data.n_candles; t++) ctx.lineTo(xOf(t), yOf(bands[a][t]));
    for (let t = data.n_candles - 1; t >= 0; t--) ctx.lineTo(xOf(t), yOf(bands[b][t]));
    ctx.closePath();
    ctx.fill();
  }
  ctx.globalAlpha = 1;

  // median
  ctx.strokeStyle = theme.crosshair;
  ctx.lineWidth = 1.6;
  ctx.beginPath();
  ctx.moveTo(xOf(0), yOf(bands[2][0]));
  for (let t = 1; t < data.n_candles; t++) ctx.lineTo(xOf(t), yOf(bands[2][t]));
  ctx.stroke();

  // start reference
  ctx.strokeStyle = theme.axisLine;
  ctx.setLineDash([3, 4]);
  ctx.beginPath();
  ctx.moveTo(0, Math.round(yOf(data.start_price)) + 0.5);
  ctx.lineTo(fanW, Math.round(yOf(data.start_price)) + 0.5);
  ctx.stroke();
  ctx.setLineDash([]);

  // shock marker
  if (data.shock_at !== null) {
    const x = Math.round(xOf(data.shock_at)) + 0.5;
    ctx.strokeStyle = theme.bear;
    ctx.setLineDash([6, 4]);
    ctx.beginPath();
    ctx.moveTo(x, 0);
    ctx.lineTo(x, fanH);
    ctx.stroke();
    ctx.setLineDash([]);
  }

  // time axis labels
  ctx.fillStyle = theme.axisText;
  ctx.textAlign = "center";
  for (let i = 0; i <= 4; i++) {
    const t = Math.round((i / 4) * (data.n_candles - 1));
    ctx.fillText(`+${t}`, xOf(t), fanH + 12);
  }

  // terminal histogram (right strip), horizontal bars
  const terms = data.paths.map((p) => p[p.length - 1]);
  const hist = histogram(terms, 26);
  const maxCount = Math.max(...hist.counts, 1);
  const rowH = fanH / hist.counts.length;
  ctx.fillStyle = theme.accent;
  ctx.globalAlpha = 0.75;
  hist.counts.forEach((c, b) => {
    const w = (c / maxCount) * (histW - 10);
    const y = yOf(hist.centers[b]);
    ctx.fillRect(width - w, y - rowH * 0.4, w, rowH * 0.8);
  });
  ctx.globalAlpha = 1;
  ctx.strokeStyle = theme.axisLine;
  ctx.beginPath();
  ctx.moveTo(width - histW - 4.5, 0);
  ctx.lineTo(width - histW - 4.5, fanH);
  ctx.stroke();
}

export function EnsembleView({ summary }: { summary: ProfileSummary | undefined }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const ensemble = useStore((s) => s.ensemble);
  const theme = useStore((s) => s.theme);

  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    const data = useStore.getState().ensemble;
    if (!canvas || !data) return;
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
    drawEnsemble(ctx, data, cssW, cssH, THEMES[useStore.getState().theme]);
  }, []);

  useEffect(() => {
    const unsub = useStore.subscribe(draw);
    const canvas = canvasRef.current;
    const ro = new ResizeObserver(draw);
    if (canvas) ro.observe(canvas);
    draw();
    return () => {
      unsub();
      ro.disconnect();
    };
    // `ensemble`: the empty state renders no canvas at all, so this effect must
    // re-run when data arrives and the canvas mounts — otherwise the first draw
    // happens only on some unrelated store change (e.g. a theme toggle)
  }, [draw, ensemble]);

  if (!ensemble) {
    return (
      <div className="ensemble-empty">
        <p>
          The distribution view simulates <strong>hundreds of seeded paths</strong> over the
          chosen span and draws the cone they form — the engine&rsquo;s honest answer.
        </p>
        <p className="hint">Set paths and candles, then press run.</p>
      </div>
    );
  }

  const stats = terminalStats(ensemble.paths, ensemble.start_price);
  const fmt = (x: number) =>
    x >= 1000 ? x.toFixed(0) : x >= 10 ? x.toFixed(1) : x.toFixed(3);

  return (
    <div className="ensemble-wrap">
      <div className="ensemble-stats">
        <span>
          <strong>{ensemble.n_paths}</strong> paths × <strong>{ensemble.n_candles}</strong> candles
          {summary ? ` · ${summary.display_name}` : ""}
          {ensemble.preset ? ` · ${ensemble.preset}` : ""}
        </span>
        <span>
          median end <strong>{fmt(stats.median)}</strong> ({(stats.median / ensemble.start_price).toFixed(2)}×)
        </span>
        <span>
          90% band <strong>{fmt(stats.p05)}–{fmt(stats.p95)}</strong>
        </span>
        <span>
          P(loss) <strong>{(stats.probLoss * 100).toFixed(0)}%</strong>
        </span>
        <span>
          P(≥2×) <strong>{(stats.probDoubling * 100).toFixed(0)}%</strong>
        </span>
      </div>
      <canvas ref={canvasRef} className="ensemble-canvas" />
    </div>
  );
}
