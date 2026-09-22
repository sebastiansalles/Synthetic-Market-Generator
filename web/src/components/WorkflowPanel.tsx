import { useState } from "react";
import { exportRun, ProfileSummary, RunSpec, WorkflowOptions } from "../api";
import { useStore } from "../store";

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
  const [busy, setBusy] = useState("");

  const summary = profiles[asset];
  const chosen = options?.presets.find((p) => p.key === preset);

  const download = async (fmt: string) => {
    setBusy(fmt);
    const run: RunSpec = {
      asset,
      nCandles,
      seed,
      resolution,
      preset,
      startPrice: summary?.last_close,
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
        <p className="hint">
          daily bars simulated; {resolution === "daily" ? "shown as-is" : `aggregated to ${resolution}`}
        </p>
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
