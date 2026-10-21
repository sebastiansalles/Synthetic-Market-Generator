import { useCallback, useEffect, useRef, useState } from "react";
import { DensityBand, densityFloor, logDensity } from "../engine/evidence";
import { THEMES } from "../engine/renderer";
import { useStore } from "../store";

/** Real return density against the band of densities the model produces.
 *
 *  Form: a magnitude-over-continuous-x comparison with an uncertainty envelope, so
 *  it is a band plus a reference line — not a bar chart of two series. The y axis is
 *  logarithmic because the claim being tested lives in the tails, which sit three or
 *  four orders of magnitude below the mode; on a linear axis they are a flat line on
 *  the floor and the chart only shows that both distributions have a peak.
 *
 *  Two entities, so a legend is mandatory and lives in the DOM above the canvas. */

const PAD = { top: 14, right: 16, bottom: 30, left: 50 };

interface Hover {
  bin: number;
  px: number;
}

function draw(
  ctx: CanvasRenderingContext2D,
  band: DensityBand,
  w: number,
  h: number,
  theme: (typeof THEMES)["dark" | "light"],
  hover: Hover | null,
): void {
  ctx.fillStyle = theme.bg;
  ctx.fillRect(0, 0, w, h);
  const n = band.centers.length;
  if (n < 2) return;

  const plotW = w - PAD.left - PAD.right;
  const plotH = h - PAD.top - PAD.bottom;
  const floor = densityFloor(band);
  const yMin = Math.log10(floor);
  // guard the peak itself, not `peak-or-floor`: an all-empty band is > 0 once the
  // floor is folded in, and log10(0) would then set yMax to -Infinity and blank the
  // canvas rather than draw an empty chart
  const peak = Math.max(...band.real, ...band.hi);
  const yMax = peak > 0 ? Math.log10(peak) + 0.15 : yMin + 1;

  const x0 = band.centers[0];
  const x1 = band.centers[n - 1];
  const xOf = (v: number) => PAD.left + ((v - x0) / (x1 - x0)) * plotW;
  const yOf = (d: number) =>
    PAD.top + plotH - ((logDensity(d, floor) - yMin) / (yMax - yMin)) * plotH;

  // recessive grid: one line per decade of density
  ctx.strokeStyle = theme.grid;
  ctx.fillStyle = theme.axisText;
  ctx.lineWidth = 1;
  ctx.font = "10px ui-monospace, monospace";
  ctx.textAlign = "right";
  ctx.textBaseline = "middle";
  for (let e = Math.ceil(yMin); e <= Math.floor(yMax); e++) {
    const y = Math.round(yOf(10 ** e)) + 0.5;
    ctx.beginPath();
    ctx.moveTo(PAD.left, y);
    ctx.lineTo(PAD.left + plotW, y);
    ctx.stroke();
    ctx.fillText(`1e${e}`, PAD.left - 7, y);
  }

  // x axis in standard deviations of the real series: comparable across assets
  ctx.textAlign = "center";
  ctx.textBaseline = "top";
  for (let sd = -8; sd <= 8; sd += 2) {
    const v = sd * band.real_sd;
    if (v < x0 || v > x1) continue;
    const x = Math.round(xOf(v)) + 0.5;
    ctx.strokeStyle = sd === 0 ? theme.axisLine : theme.grid;
    ctx.beginPath();
    ctx.moveTo(x, PAD.top);
    ctx.lineTo(x, PAD.top + plotH);
    ctx.stroke();
    ctx.fillStyle = theme.axisText;
    ctx.fillText(sd === 0 ? "0" : `${sd}σ`, x, PAD.top + plotH + 7);
  }

  // the simulated 5-95% envelope
  ctx.fillStyle = theme.accentSoft;
  ctx.beginPath();
  ctx.moveTo(xOf(band.centers[0]), yOf(band.hi[0]));
  for (let i = 1; i < n; i++) ctx.lineTo(xOf(band.centers[i]), yOf(band.hi[i]));
  for (let i = n - 1; i >= 0; i--) ctx.lineTo(xOf(band.centers[i]), yOf(band.lo[i]));
  ctx.closePath();
  ctx.fill();

  // the model's median density
  ctx.strokeStyle = theme.accent;
  ctx.lineWidth = 1.5;
  ctx.setLineDash([4, 3]);
  ctx.beginPath();
  band.median.forEach((d, i) => (i ? ctx.lineTo(xOf(band.centers[i]), yOf(d)) : ctx.moveTo(xOf(band.centers[i]), yOf(d))));
  ctx.stroke();
  ctx.setLineDash([]);

  // reality, on top and solid: the series every other mark is being judged against
  ctx.strokeStyle = theme.crosshair;
  ctx.lineWidth = 2;
  ctx.beginPath();
  band.real.forEach((d, i) => (i ? ctx.lineTo(xOf(band.centers[i]), yOf(d)) : ctx.moveTo(xOf(band.centers[i]), yOf(d))));
  ctx.stroke();
  ctx.lineWidth = 1;

  if (hover && hover.bin >= 0 && hover.bin < n) {
    const x = Math.round(xOf(band.centers[hover.bin])) + 0.5;
    ctx.strokeStyle = theme.crosshair;
    ctx.globalAlpha = 0.5;
    ctx.setLineDash([3, 3]);
    ctx.beginPath();
    ctx.moveTo(x, PAD.top);
    ctx.lineTo(x, PAD.top + plotH);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.globalAlpha = 1;

    const b = hover.bin;
    const inside = band.real[b] >= band.lo[b] && band.real[b] <= band.hi[b];
    const lines = [
      `${(band.centers[b] * 100).toFixed(2)}%  (${(band.centers[b] / band.real_sd).toFixed(1)}σ)`,
      `real ${band.real[b].toExponential(2)}`,
      `model ${band.lo[b].toExponential(2)} – ${band.hi[b].toExponential(2)}`,
      inside ? "inside the band" : "outside the band",
    ];
    ctx.font = "11px ui-monospace, monospace";
    const tw = Math.max(...lines.map((l) => ctx.measureText(l).width)) + 16;
    const th = lines.length * 14 + 10;
    const bx = Math.min(x + 10, PAD.left + plotW - tw);
    ctx.fillStyle = theme.panelFade;
    ctx.fillRect(bx, PAD.top + 6, tw, th);
    ctx.strokeStyle = theme.axisLine;
    ctx.strokeRect(bx + 0.5, PAD.top + 6.5, tw, th);
    ctx.textAlign = "left";
    ctx.textBaseline = "middle";
    lines.forEach((l, i) => {
      ctx.fillStyle = i === 3 ? (inside ? theme.bull : theme.bear) : theme.crosshair;
      ctx.fillText(l, bx + 8, PAD.top + 20 + i * 14);
    });
  }
}

export function DensityChart({ band }: { band: DensityBand }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const theme = useStore((s) => s.theme);
  const [hover, setHover] = useState<Hover | null>(null);

  const render = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
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
    draw(ctx, band, cssW, cssH, THEMES[theme], hover);
  }, [band, theme, hover]);

  // the canvas only exists once there is a band to draw, so the effect has to
  // re-run when `band` arrives — a draw effect that misses its own mount is the
  // bug class that made the distribution view "need a theme toggle to appear"
  useEffect(() => {
    render();
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ro = new ResizeObserver(render);
    ro.observe(canvas);
    return () => ro.disconnect();
  }, [render]);

  return (
    <canvas
      ref={canvasRef}
      className="density-canvas"
      aria-label="real return density against the simulated 5 to 95 percent band"
      onPointerMove={(e) => {
        const rect = e.currentTarget.getBoundingClientRect();
        const px = e.clientX - rect.left;
        const plotW = e.currentTarget.clientWidth - PAD.left - PAD.right;
        const frac = (px - PAD.left) / plotW;
        const bin = Math.round(frac * (band.centers.length - 1));
        setHover(bin >= 0 && bin < band.centers.length ? { bin, px } : null);
      }}
      onPointerLeave={() => setHover(null)}
    />
  );
}
