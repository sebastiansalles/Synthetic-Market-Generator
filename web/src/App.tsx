import { useEffect, useRef, useState } from "react";
import { getHealth, listProfiles, ProfileSummary, simulate } from "./api";
import { Chart } from "./components/Chart";
import { WsEvent } from "./engine/live";
import { useStore } from "./store";

const LIVE_CANDLES = 400;

export function App() {
  const [profiles, setProfiles] = useState<Record<string, ProfileSummary>>({});
  const [apiVersion, setApiVersion] = useState<string>("");
  // per-field selectors, not the whole store: a live session replaces `live` on every
  // tick, and subscribing wholesale would re-render App (and re-run effects) at tick rate
  const asset = useStore((s) => s.asset);
  const seed = useStore((s) => s.seed);
  const nCandles = useStore((s) => s.nCandles);
  const status = useStore((s) => s.status);
  const hasBars = useStore((s) => s.bars.length > 0);
  const isLive = useStore((s) => s.live !== null);
  const setAsset = useStore((s) => s.setAsset);
  const setBars = useStore((s) => s.setBars);
  const setStatus = useStore((s) => s.setStatus);
  const resetView = useStore((s) => s.resetView);
  const wsRef = useRef<WebSocket | null>(null);
  const profilesRef = useRef<Record<string, ProfileSummary>>({});
  profilesRef.current = profiles;

  useEffect(() => {
    getHealth()
      .then((h) => setApiVersion(h.version))
      .catch(() => setApiVersion("unreachable"));
    listProfiles()
      .then((p) => {
        setProfiles(p);
        const first = Object.keys(p)[0];
        if (first) setAsset(first);
      })
      .catch((e) => setStatus(`API unreachable: ${e.message}`));
    return () => wsRef.current?.close();
  }, [setAsset, setStatus]);

  const summary = profiles[asset];

  // deps exclude `summary`: profile arrival must not re-run the simulation (a ref
  // supplies the anchor), otherwise the page simulates twice on open
  useEffect(() => {
    if (!asset || isLive) return;
    setStatus(`simulating ${asset}…`);
    simulate(asset, nCandles, seed, profilesRef.current[asset]?.last_close)
      .then((b) => {
        setBars(b, asset);
        setStatus(`${b.length} candles · seed ${seed}`);
      })
      .catch((e) => setStatus(`simulation failed: ${e.message}`));
  }, [asset, nCandles, seed, isLive, setBars, setStatus]);

  const startLive = () => {
    const s = useStore.getState();
    s.startLive();
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(
      `${proto}://${location.host}/ws/simulate?asset=${asset}&seed=${seed}` +
        `&n_candles=${LIVE_CANDLES}&ticks_per_candle=24&ticks_per_sec=250`,
    );
    ws.onmessage = (e) => {
      const ev = JSON.parse(e.data) as WsEvent & { n_candles?: number };
      s.applyWsEvent(ev);
      if (ev.type === "generating") setStatus(`generating ${ev.n_candles}-candle session…`);
      if (ev.type === "bar_close") setStatus(`live · bar ${ev.bar_id + 1}/${LIVE_CANDLES}`);
      else if (ev.type === "done") setStatus(`session complete · ${LIVE_CANDLES} candles`);
      else if (ev.type === "error") setStatus(`live error: ${ev.message}`);
    };
    ws.onclose = () => {
      if (useStore.getState().live !== null && !useStore.getState().live?.done) {
        setStatus("live disconnected");
      }
      useStore.getState().stopLive();
    };
    wsRef.current = ws;
  };

  // clearing `live` re-runs the simulate effect above, which refetches the static set —
  // calling simulate() here too would fire the same request twice
  const stopLive = () => {
    wsRef.current?.close();
    wsRef.current = null;
    useStore.getState().stopLive();
  };

  return (
    <div className="app">
      <header className="toolbar">
        <span className="brand">Synthetic Market Generator</span>
        <select value={asset} onChange={(e) => setAsset(e.target.value)} disabled={isLive}>
          {Object.entries(profiles).map(([k, p]) => (
            <option key={k} value={k}>
              {p.display_name}
            </option>
          ))}
        </select>
        <button onClick={() => useStore.setState({ seed: seed + 1 })} disabled={isLive}>
          ↻ new seed
        </button>
        <button onClick={resetView}>reset view</button>
        {!isLive ? (
          <button className="live-btn" onClick={startLive}>
            ▶ live
          </button>
        ) : (
          <button className="live-btn" onClick={stopLive}>
            ■ stop
          </button>
        )}
        <span className="status">{status}</span>
        {summary && (
          <span className="meta">
            σ {summary.state_vol_ann.map((v) => (v * 100).toFixed(0) + "%").join(" / ")} · dwell{" "}
            {summary.expected_dwell_days.map((d) => d.toFixed(0) + "d").join(" / ")} ·{" "}
            {summary.volume_mode} · engine {apiVersion}
          </span>
        )}
      </header>
      {hasBars || isLive ? <Chart /> : <div className="empty">loading…</div>}
    </div>
  );
}
