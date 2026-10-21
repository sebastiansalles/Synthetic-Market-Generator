import { useMemo } from "react";
import { regimeInk, regimeLabel, THEMES } from "../engine/renderer";
import { useStore } from "../store";

/** Key for the chart's regime heatmap, plus the run's realized regime mix — the
 *  readout that turns a coloured background into evidence ("this run spent 13% of
 *  its bars in the turbulent state, against a profile that expects ~18%").
 *
 *  Subscribes to the STATES ARRAY, not the live session. The live reducer returns a
 *  fresh states array only on bar_open/bar_close and keeps the reference across
 *  ticks, so this re-renders once per candle instead of once per tick — the
 *  re-render-at-tick-rate trap this project has already paid for once. */
export function RegimeLegend() {
  const theme = useStore((s) => s.theme);
  const states = useStore((s) => (s.live ? s.live.states : s.states));
  const resolution = useStore((s) => s.resolution);

  const shares = useMemo(() => {
    const present = states.filter((s) => s !== undefined && s !== null);
    // always key at least calm/turbulent, even if this run never left one of them
    const n = Math.max(2, ...present.map((s) => s + 1));
    const counts = new Array(n).fill(0);
    for (const s of present) counts[s] += 1;
    return counts.map((c) => (present.length > 0 ? c / present.length : 0));
  }, [states]);

  if (states.length === 0) return null;
  const palette = THEMES[theme];

  return (
    <div
      className="regime-legend"
      title={
        resolution === "daily"
          ? "Hidden Markov regime inferred per bar, from the same latent path that generated the candles"
          : `Each ${resolution} bar takes the modal regime of the daily bars inside it`
      }
    >
      <span className="regime-legend-title">regime</span>
      {shares.map((share, i) => (
        <span key={i} className="regime-chip">
          <i style={{ background: regimeInk(palette, i) }} />
          {regimeLabel(i)} <strong>{(share * 100).toFixed(0)}%</strong>
        </span>
      ))}
    </div>
  );
}
