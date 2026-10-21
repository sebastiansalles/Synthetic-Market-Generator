import { useCallback, useEffect, useRef } from "react";
import { ensembleWsUrl, ProfileSummary } from "../api";
import { EnsembleRisk } from "./EnsembleRisk";
import {
  EnsembleResult,
  EnsembleWsEvent,
  histogram,
  percentileBand,
  terminalStats,
} from "../engine/ensemble";
import { THEMES } from "../engine/renderer";
import { useStore } from "../store";

/** The distribution over N candles — BUILT LIVE: one path at a time streams in and
 *  sweeps across the canvas (~50 paths/sec), the percentile cone tightening as paths
 *  accumulate. The honest answer the engine gives, assembled in front of you. */

const QUANTILES = [0.05, 0.25, 0.5, 0.75, 0.95] as const;
const SWEEP_MS = 130; // how fast each individual path sweeps left-to-right
const BAND_RECOMPUTE_MS = 110; // the cone recomputes at most this often during the build

interface Sweep {
  path: number[];
  t0: number;
}

/** The percentile cone and the price extent, recomputed on a budget.
 *
 *  Both are O(paths x candles) — the cone sorts every path at every time step —
 *  and drawing runs at animation rate, so computing them per frame burned the
 *  whole frame on work whose answer barely moves between paths. `BAND_RECOMPUTE_MS`
 *  has always been declared for this; now it is honoured. A settled ensemble keeps
 *  the same `paths` array identity, so it recomputes exactly once. */
interface Derived {
  bands: number[][];
  lo: number;
  hi: number;
}

function deriveFan(paths: number[][], startPrice: number): Derived {
  const bands = QUANTILES.map((q) => percentileBand(paths, q));
  let lo = Infinity;
  let hi = -Infinity;
  // an explicit loop, not Math.min(...paths.flat()): at the permitted maximum of
  // 400 paths x 2000 candles that spread is 800k arguments, well past V8's
  // call-stack limit, and the view threw RangeError instead of rendering
  for (const path of paths) {
    for (const price of path) {
      if (price < lo) lo = price;
      if (price > hi) hi = price;
    }
  }
  if (!Number.isFinite(lo) || !Number.isFinite(hi)) {
    lo = startPrice * 0.9;
    hi = startPrice * 1.1;
  }
  return { bands, lo, hi };
}

function drawEnsemble(
  ctx: CanvasRenderingContext2D,
  data: EnsembleResult,
  width: number,
  height: number,
  theme: (typeof THEMES)["dark" | "light"],
  sweeps: Sweep[],
  now: number,
  derived: Derived,
) {
  const histW = Math.min(190, Math.round(width * 0.22));
  const fanW = width - histW - 8;
  const axisH = 26;
  const fanH = height - axisH;

  ctx.fillStyle = theme.bg;
  ctx.fillRect(0, 0, width, height);

  const { bands } = derived;
  const complete = data.paths.length;
  let lo = derived.lo;
  let hi = derived.hi;
  for (const s of sweeps) {
    for (const price of s.path) {
      if (price < lo) lo = price;
      if (price > hi) hi = price;
    }
  }
  lo = Math.exp(Math.log(lo) - 0.04 * (Math.log(hi) - Math.log(lo)));
  hi = Math.exp(Math.log(hi) + 0.04 * (Math.log(hi) - Math.log(lo)));
  const yOf = (p: number) => ((Math.log(hi) - Math.log(p)) / (Math.log(hi) - Math.log(lo))) * fanH;
  const xOf = (t: number) => (t / (data.n_candles - 1)) * fanW;

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

  // fan from completed paths (widest first)
  if (complete > 1) {
    const fills: [number, number, number][] = [
      [0, 4, 0.35],
      [1, 3, 0.4],
    ];
    for (const [a, b, alpha] of fills) {
      ctx.fillStyle = theme.accent;
      ctx.globalAlpha = alpha;
      ctx.beginPath();
      ctx.moveTo(xOf(0), yOf(bands[a][0]));
      for (let t = 1; t < data.n_candles; t++) ctx.lineTo(xOf(t), yOf(bands[a][t]));
      for (let t = data.n_candles - 1; t >= 0; t--) ctx.lineTo(xOf(t), yOf(bands[b][t]));
      ctx.closePath();
      ctx.fill();
    }
    ctx.globalAlpha = 1;
    ctx.strokeStyle = theme.crosshair;
    ctx.lineWidth = 1.6;
    ctx.beginPath();
    ctx.moveTo(xOf(0), yOf(bands[2][0]));
    for (let t = 1; t < data.n_candles; t++) ctx.lineTo(xOf(t), yOf(bands[2][t]));
    ctx.stroke();
  }

  // start reference
  ctx.strokeStyle = theme.axisLine;
  ctx.setLineDash([3, 4]);
  ctx.beginPath();
  ctx.moveTo(0, Math.round(yOf(data.start_price)) + 0.5);
  ctx.lineTo(fanW, Math.round(yOf(data.start_price)) + 0.5);
  ctx.stroke();
  ctx.setLineDash([]);

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

  // active sweeps: each incoming path draws itself left-to-right, fast
  for (const s of sweeps) {
    const frac = Math.min(1, (now - s.t0) / SWEEP_MS);
    const upto = Math.max(1, Math.floor(frac * data.n_candles));
    ctx.strokeStyle = theme.bull;
    ctx.globalAlpha = 0.5 + 0.4 * (1 - frac);
    ctx.lineWidth = 1.2;
    ctx.beginPath();
    ctx.moveTo(xOf(0), yOf(s.path[0]));
    for (let t = 1; t < upto; t++) ctx.lineTo(xOf(t), yOf(s.path[t]));
    ctx.stroke();
  }
  ctx.globalAlpha = 1;
  ctx.lineWidth = 1;

  ctx.fillStyle = theme.axisText;
  ctx.textAlign = "center";
  for (let i = 0; i <= 4; i++) {
    const t = Math.round((i / 4) * (data.n_candles - 1));
    ctx.fillText(`+${t}`, xOf(t), fanH + 12);
  }

  // terminal histogram from completed paths (the bell shape emerges live)
  if (complete > 1) {
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
  }
  ctx.strokeStyle = theme.axisLine;
  ctx.beginPath();
  ctx.moveTo(width - histW - 4.5, 0);
  ctx.lineTo(width - histW - 4.5, fanH);
  ctx.stroke();
}

export function EnsembleView({ summary }: { summary: ProfileSummary | undefined }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const ensemble = useStore((s) => s.ensemble);
  const asset = useStore((s) => s.asset);
  const seed = useStore((s) => s.seed);
  const preset = useStore((s) => s.preset);
  const driftAnnual = useStore((s) => s.driftAnnual);
  const ensPaths = useStore((s) => s.ensPaths);
  const ensCandles = useStore((s) => s.ensCandles);
  const ensCondition = useStore((s) => s.ensCondition);
  const sweepsRef = useRef<Sweep[]>([]);
  const rafRef = useRef(0);
  const derivedRef = useRef<{ paths: number[][]; at: number; value: Derived } | null>(null);

  // stream the build: one path at a time over the websocket
  useEffect(() => {
    const s = useStore.getState();
    const ws = new WebSocket(
      ensembleWsUrl({
        asset,
        nCandles: ensCandles,
        nPaths: ensPaths,
        seed,
        preset,
        startPrice: summary?.last_close,
        driftAnnual,
        condition: ensCondition,
      }),
    );
    ws.onmessage = (e) => {
      const ev = JSON.parse(e.data) as EnsembleWsEvent;
      const st = useStore.getState();
      if (ev.type === "start") {
        st.beginEnsemble({
          n_paths: ev.n_paths,
          n_candles: ev.n_candles,
          start_price: ev.start_price,
          shock_at: ev.shock_at,
          preset: ev.preset,
          conditioned: ev.conditioned ?? false,
        });
        st.setStatus(`building distribution · 0/${ev.n_paths}`);
      } else if (ev.type === "path") {
        st.appendEnsemblePath(ev.path);
        sweepsRef.current.push({ path: ev.path, t0: performance.now() });
        st.setStatus(`building distribution · ${ev.index + 1}/${st.ensemble?.n_paths ?? "?"}`);
        ensureRaf();
      } else if (ev.type === "done") {
        st.finishEnsemble();
        st.setStatus(`distribution complete · ${st.ensemble?.n_paths ?? ""} paths`);
      } else if (ev.type === "error") {
        st.finishEnsemble();
        st.setStatus(`ensemble failed: ${ev.message}`);
      }
    };
    return () => {
      derivedRef.current = null; // a new ensemble must not reuse the old cone
      ws.close();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [asset, seed, preset, driftAnnual, ensPaths, ensCandles, ensCondition]);

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

    const now = performance.now();
    sweepsRef.current = sweepsRef.current.filter((s) => now - s.t0 < SWEEP_MS);

    // reuse the cone when nothing arrived (same array identity), or when the last
    // recompute is still inside the budget and this is a later state of the same run
    const cache = derivedRef.current;
    const fresh =
      cache &&
      (cache.paths === data.paths ||
        (now - cache.at < BAND_RECOMPUTE_MS && data.paths.length > cache.paths.length));
    const derived = fresh
      ? cache!.value
      : deriveFan(data.paths, data.start_price);
    if (!fresh) derivedRef.current = { paths: data.paths, at: now, value: derived };

    drawEnsemble(ctx, data, cssW, cssH, THEMES[useStore.getState().theme],
                 sweepsRef.current, now, derived);

    // keep animating while paths are sweeping in
    if (sweepsRef.current.length > 0 || useStore.getState().ensembleBusy) {
      rafRef.current = requestAnimationFrame(draw);
    }
  }, []);

  const ensureRaf = useCallback(() => {
    cancelAnimationFrame(rafRef.current);
    rafRef.current = requestAnimationFrame(draw);
  }, [draw]);

  useEffect(() => {
    const unsub = useStore.subscribe(draw);
    const canvas = canvasRef.current;
    const ro = new ResizeObserver(draw);
    if (canvas) ro.observe(canvas);
    draw();
    // `ensemble`: the empty state renders no canvas — re-run when data arrives
  }, [draw, ensemble]);

  // The risk panel stays mounted with no ensemble on purpose: switching the forecast
  // origin clears the old result, and a control that unmounts itself the moment you
  // click it is unusable. Its own controls read the profile, not the paths.
  if (!ensemble) {
    return (
      <div className="ensemble-wrap">
        <div className="ensemble-body">
          <div className="ensemble-empty">
            <p>
              The distribution view <strong>builds itself in front of you</strong> — hundreds
              of seeded paths sweep across the canvas, the cone tightening until the answer
              emerges.
            </p>
          </div>
          <EnsembleRisk summary={summary} />
        </div>
      </div>
    );
  }

  const building = ensemble.paths.length < ensemble.n_paths;
  const stats = terminalStats(ensemble.paths, ensemble.start_price);
  const fmt = (x: number) => (x >= 1000 ? x.toFixed(0) : x >= 10 ? x.toFixed(1) : x.toFixed(3));

  return (
    <div className="ensemble-wrap">
      <div className="ensemble-stats">
        <span className={building ? "building" : ""}>
          {building
            ? `building ${ensemble.paths.length}/${ensemble.n_paths} paths…`
            : `${ensemble.n_paths} paths × ${ensemble.n_candles} candles`}
          {summary ? ` · ${summary.display_name}` : ""}
          {ensemble.preset ? ` · ${ensemble.preset}` : ""}
        </span>
        {ensemble.conditioned && <span className="badge">from today</span>}
        {ensemble.paths.length > 1 && (
          <>
            <span>
              median end <strong>{fmt(stats.median)}</strong> (
              {(stats.median / ensemble.start_price).toFixed(2)}×)
            </span>
            <span>
              90% band{" "}
              <strong>
                {fmt(stats.p05)}–{fmt(stats.p95)}
              </strong>
            </span>
            <span>
              P(loss) <strong>{(stats.probLoss * 100).toFixed(0)}%</strong>
            </span>
            <span>
              P(≥2×) <strong>{(stats.probDoubling * 100).toFixed(0)}%</strong>
            </span>
          </>
        )}
      </div>
      <div className="ensemble-body">
        <canvas ref={canvasRef} className="ensemble-canvas" />
        <EnsembleRisk summary={summary} />
      </div>
    </div>
  );
}
