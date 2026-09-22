import { useEffect, useState } from "react";
import { listProfiles, ProfileSummary, simulate } from "./api";
import { Chart } from "./components/Chart";
import { useStore } from "./store";

export function App() {
  const [profiles, setProfiles] = useState<Record<string, ProfileSummary>>({});
  const { asset, bars, seed, nCandles, status, setAsset, setBars, setStatus, resetView } =
    useStore();

  useEffect(() => {
    listProfiles()
      .then((p) => {
        setProfiles(p);
        const first = Object.keys(p)[0];
        if (first) setAsset(first);
      })
      .catch((e) => setStatus(`API unreachable: ${e.message}`));
  }, [setAsset, setStatus]);

  useEffect(() => {
    if (!asset) return;
    setStatus(`simulating ${asset}…`);
    simulate(asset, nCandles, seed)
      .then((b) => {
        setBars(b, asset);
        setStatus(`${b.length} candles · seed ${seed}`);
      })
      .catch((e) => setStatus(`simulation failed: ${e.message}`));
  }, [asset, nCandles, seed, setBars, setStatus]);

  const summary = profiles[asset];

  return (
    <div className="app">
      <header className="toolbar">
        <span className="brand">Synthetic Market Generator</span>
        <select value={asset} onChange={(e) => setAsset(e.target.value)}>
          {Object.entries(profiles).map(([k, p]) => (
            <option key={k} value={k}>
              {p.display_name}
            </option>
          ))}
        </select>
        <button onClick={() => useStore.setState({ seed: seed + 1 })}>↻ new seed</button>
        <button onClick={resetView}>reset view</button>
        <span className="status">{status}</span>
        {summary && (
          <span className="meta">
            σ {summary.state_vol_ann.map((v) => (v * 100).toFixed(0) + "%").join(" / ")} · dwell{" "}
            {summary.expected_dwell_days.map((d) => d.toFixed(0) + "d").join(" / ")} ·{" "}
            {summary.volume_mode}
          </span>
        )}
      </header>
      {bars.length > 0 ? <Chart /> : <div className="empty">loading…</div>}
    </div>
  );
}
