import { useState } from "react";
import { exportRun, horizon, ProfileSummary, RunSpec, WorkflowOptions } from "../api";
import { useStore } from "../store";

const pct = (x: number) => `${(x * 100).toFixed(0)}%`;
const mult = (x: number) => (x >= 10 ? `${x.toFixed(0)}x` : `${x.toFixed(2)}x`);

/** Phase 5's three-step workflow: pick a market, choose resolution and span, then
 *  decide whether it runs standard or under stress. The same run spec drives the
 *  chart and the export, so what is on screen is what lands on disk. */
export function WorkflowPanel({
  profiles,
  options,
  disabled,
}: {
  profiles: Record<string, ProfileSummary>;
  options: WorkflowOptions | null;
  disabled: boolean;
}) {
  const asset = useStore((s) => s.asset);
  const nCandles = useStore((s) => s.nCandles);
  const seed = useStore((s) => s.seed);
  const resolution = useStore((s) => s.resolution);
  const preset = useStore((s) => s.preset);
  const setAsset = useStore((s) => s.setAsset);
  const driftAnnual = useStore((s) => s.driftAnnual);
  const [busy, setBusy] = useState("");

  const summary = profiles[asset];
  const chosen = options?.presets.find((p) => p.key === preset);
  const span = summary ? horizon(summary, nCandles) : null;

  const download = async (fmt: string) => {
    setBusy(fmt);
    const run: RunSpec = {
      asset,
      nCandles,
      seed,
      resolution,
      preset,
      startPrice: summary?.last_close,
      driftAnnual,
    };
    try {
      const filename = await exportRun(run, fmt);
      useStore.getState().setStatus(`exported ${filename}`);
    } catch (e) {
      useStore.getState().setStatus(`export failed: ${(e as Error).message}`);
    } finally {
      setBusy("");
    }
  };

  return (
    <aside className="workflow" aria-label="simulation workflow">
      <section>
        <h2>
          <span className="step">1</span> Market
        </h2>
        <select value={asset} onChange={(e) => setAsset(e.target.value)} disabled={disabled}>
          {Object.entries(profiles).map(([k, p]) => (
            <option key={k} value={k}>
              {p.display_name}
            </option>
          ))}
        </select>
        {summary && (
          <p className="hint">
            calm/turbulent σ{" "}
            {summary.state_vol_ann.map((v) => `${(v * 100).toFixed(0)}%`).join(" / ")} · dwell{" "}
            {summary.expected_dwell_days.map((d) => `${d.toFixed(0)}d`).join(" / ")}
          </p>
        )}
      </section>

      <section>
        <h2>
          <span className="step">2</span> Resolution &amp; span
        </h2>
        <div className="seg">
          {(options?.resolutions ?? ["daily"]).map((r) => (
            <button
              key={r}
              className={r === resolution ? "on" : ""}
              disabled={disabled}
              onClick={() => useStore.setState({ resolution: r })}
            >
              {r}
            </button>
          ))}
        </div>
        <label className="field">
          <span>duration</span>
          <input
            type="number"
            min={50}
            max={options?.max_candles ?? 10000}
            step={50}
            value={nCandles}
            disabled={disabled}
            onChange={(e) => {
              const v = Number(e.target.value);
              if (Number.isFinite(v) && v >= 50) useStore.setState({ nCandles: v });
            }}
          />
        </label>
        {/* a bar count hides the calendar: 1500 bars is 4.1y of 365-day crypto but
            6.0y of business days, and dispersion grows with the root of that span */}
        {span && (
          <p className="hint">
            <strong>{span.years.toFixed(1)} years</strong> at {summary.periods_per_year} bars/yr
            {resolution !== "daily" && ` · aggregated to ${resolution}`}
            <br />
            at {pct(summary.annual_vol)} vol, ~90% of paths end between{" "}
            <strong>
              {mult(span.lowMultiple)} and {mult(span.highMultiple)}
            </strong>{" "}
            of the start — a lognormal guide only; fat tails and clustering run wider.
          </p>
        )}
      </section>

      <section>
        <h2>
          <span className="step">3</span> Scenario
        </h2>
        <div className="presets">
          {(options?.presets ?? []).map((p) => (
            <button
              key={p.key}
              className={p.key === preset ? "on" : ""}
              disabled={disabled}
              onClick={() => useStore.setState({ preset: p.key })}
            >
              {p.label}
            </button>
          ))}
        </div>
        {chosen && <p className="hint">{chosen.description}</p>}
      </section>

      <section>
        <h2>Drift</h2>
        <div className="seg">
          <button
            className={driftAnnual === null ? "on" : ""}
            disabled={disabled}
            onClick={() => useStore.setState({ driftAnnual: null })}
          >
            calibrated
          </button>
          <button
            className={driftAnnual === 0 ? "on" : ""}
            disabled={disabled}
            onClick={() => useStore.setState({ driftAnnual: 0 })}
          >
            zero
          </button>
        </div>
        {summary && (
          <p className="hint">
            {driftAnnual === null ? (
              <>
                Calibrated: <strong>{pct(summary.drift_ann)}/yr</strong> ± {pct(summary.drift_ann_se)}.
                {summary.drift_ann_se > Math.abs(summary.drift_ann) / 2 &&
                  " Barely distinguishable from zero, yet it compounds over the whole run."}
              </>
            ) : (
              <>Trend removed; volatility and regimes are untouched.</>
            )}
          </p>
        )}
      </section>

      <section>
        <h2>Reproducibility</h2>
        <label className="field">
          <span>seed</span>
          <input
            type="number"
            value={seed}
            disabled={disabled}
            onChange={(e) => {
              const v = Number(e.target.value);
              if (Number.isFinite(v)) useStore.setState({ seed: v });
            }}
          />
        </label>
        <p className="hint">Same seed, scenario and profile reproduce this run exactly.</p>
      </section>

      <section>
        <h2>Export</h2>
        <div className="seg">
          {(options?.formats ?? []).map((f) => (
            <button key={f} disabled={disabled || busy !== ""} onClick={() => download(f)}>
              {busy === f ? "…" : f}
            </button>
          ))}
        </div>
        <p className="hint">
          LEAN bundles arrive in QuantConnect&apos;s directory layout with a manifest
          recording the seed and scenario.
        </p>
      </section>
    </aside>
  );
}
