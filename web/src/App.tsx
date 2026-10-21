import { useEffect, useRef, useState } from "react";
import {
  getHealth,
  getWorkflow,
  listProfiles,
  ProfileSummary,
  simulate,
  WorkflowOptions,
} from "./api";
import { Chart } from "./components/Chart";
import { EnsembleView } from "./components/EnsembleView";
import { EvidencePanel } from "./components/EvidencePanel";
import { LiveCounter, LIVE_TOTAL } from "./components/LiveCounter";
import { WorkflowPanel } from "./components/WorkflowPanel";
import { WsEvent } from "./engine/live";
import { useStore } from "./store";

export function App() {
  const [profiles, setProfiles] = useState<Record<string, ProfileSummary>>({});
  const [apiVersion, setApiVersion] = useState<string>("");
  const [options, setOptions] = useState<WorkflowOptions | null>(null);
  // per-field selectors, not the whole store: a live session replaces `live` on every
  // tick, and subscribing wholesale would re-render App (and re-run effects) at tick rate
  const asset = useStore((s) => s.asset);
  const seed = useStore((s) => s.seed);
  const nCandles = useStore((s) => s.nCandles);
  const resolution = useStore((s) => s.resolution);
  const preset = useStore((s) => s.preset);
  const driftAnnual = useStore((s) => s.driftAnnual);
  const status = useStore((s) => s.status);
  const theme = useStore((s) => s.theme);
  const mode = useStore((s) => s.mode);
  const ensPaths = useStore((s) => s.ensPaths);
  const ensCandles = useStore((s) => s.ensCandles);
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
    getWorkflow().then(setOptions).catch(() => undefined);
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

  // runKey: unchanged settings never refetch. Also what lets a finished live session
  // stay on screen — promoteLive keeps bars + runKey, so flipping out of live mode
  // does not snap back to a fresh static chart.
  const runKey = [asset, nCandles, seed, resolution, preset, driftAnnual].join("|");
  useEffect(() => {
    if (!asset || isLive || mode !== "single") return;
    if (hasBars && useStore.getState().runKey === runKey) return;
    setStatus(`simulating ${asset}…`);
    simulate({
      asset,
      nCandles,
      seed,
      resolution,
      preset,
      startPrice: profilesRef.current[asset]?.last_close,
      driftAnnual,
    })
      .then(({ bars, states, shockAt, preset: usedPreset }) => {
        setBars(bars, asset, states);
        useStore.getState().setRunKey(runKey);
        if (shockAt !== null) {
          // flag the injected shock and bring it on screen — a one-candle event is
          // invisible at overview zoom, which is why scenarios "didn't seem to work"
          useStore.getState().setMarker({ index: shockAt, label: usedPreset ?? "shock" });
          useStore.setState({
            viewport: { offset: Math.max(0, shockAt - 300), candlesPerPixel: 0.5 },
            follow: false,
          });
        } else {
          useStore.getState().setMarker(null);
        }
        const scope = preset === "standard" ? "" : ` · ${preset}`;
        setStatus(`${bars.length} ${resolution} candles · seed ${seed}${scope}`);
      })
      .catch((e) => setStatus(`simulation failed: ${e.message}`));
  }, [asset, nCandles, seed, resolution, preset, driftAnnual, isLive, mode, hasBars, runKey, setBars, setStatus]);

  const startLive = () => {
    const s = useStore.getState();
    s.startLive();
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(
      `${proto}://${location.host}/ws/simulate?asset=${asset}&seed=${seed}` +
        `&n_candles=${LIVE_TOTAL}&ticks_per_candle=24&ticks_per_sec=250`,
    );
    ws.onmessage = (e) => {
      const ev = JSON.parse(e.data) as WsEvent & { n_candles?: number };
      s.applyWsEvent(ev);
      if (ev.type === "generating") setStatus(`generating ${ev.n_candles}-candle session…`);
      else if (ev.type === "done") setStatus(`session complete · ${LIVE_TOTAL} candles`);
      else if (ev.type === "error") setStatus(`live error: ${ev.message}`);
    };
    ws.onclose = () => {
      // keep whatever the session produced on screen — completing a live run must
      // not snap the chart back to a fresh static simulation
      useStore.getState().promoteLive();
    };
    wsRef.current = ws;
  };

  const stopLive = () => wsRef.current?.close(); // onclose promotes the session

  return (
    <div className="app">
      <header className="toolbar">
        <span className="brand">Synthetic Market Generator</span>
        <div className="seg">
          <button
            className={mode === "single" ? "on" : ""}
            onClick={() => useStore.getState().setMode("single")}
            title="one simulated path, candle by candle"
          >
            single
          </button>
          <button
            className={mode === "ensemble" ? "on" : ""}
            onClick={() => useStore.getState().setMode("ensemble")}
            title="hundreds of paths: the distribution the model implies"
          >
            distribution
          </button>
          <button
            className={mode === "evidence" ? "on" : ""}
            onClick={() => useStore.getState().setMode("evidence")}
            title="the statistical validation suite: does this model reproduce the real asset?"
          >
            evidence
          </button>
        </div>
        {mode === "ensemble" && (
          <>
            <label className="field inline">
              <span>paths</span>
              <input
                type="number"
                min={10}
                max={400}
                value={ensPaths}
                onChange={(e) =>
                  useStore.getState().setEnsParams(
                    Math.max(10, Math.min(400, Number(e.target.value) || 10)),
                    ensCandles,
                  )
                }
              />
            </label>
            <label className="field inline">
              <span>candles</span>
              <input
                type="number"
                min={10}
                max={2000}
                value={ensCandles}
                onChange={(e) =>
                  useStore.getState().setEnsParams(
                    ensPaths,
                    Math.max(10, Math.min(2000, Number(e.target.value) || 10)),
                  )
                }
              />
            </label>
          </>
        )}
        {/* the evidence view has its own fixed seed: a re-seed would change nothing
            there, and a control that does nothing is worse than no control */}
        {mode !== "evidence" && (
          <button onClick={() => useStore.setState({ seed: seed + 1 })} disabled={isLive}>
            ↻ new seed
          </button>
        )}
        {mode === "single" && <button onClick={resetView}>reset view</button>}
        <button
          onClick={() => useStore.getState().setTheme(theme === "dark" ? "light" : "dark")}
          title="toggle light / dark theme"
        >
          {theme === "dark" ? "☀ light" : "☾ dark"}
        </button>
        {mode === "single" &&
          (!isLive ? (
            <button className="live-btn" onClick={startLive}>
              ▶ live
            </button>
          ) : (
            <button className="live-btn" onClick={stopLive}>
              ■ stop
            </button>
          ))}
        {isLive && <LiveCounter />}
        <span className="status">{status}</span>
        <span className="meta">
          {summary ? `${summary.volume_mode} · ` : ""}engine {apiVersion}
        </span>
      </header>
      <div className="workspace">
        {mode !== "ensemble" && (
          <WorkflowPanel
            profiles={profiles}
            options={options}
            disabled={isLive}
            scope={mode === "evidence" ? "market" : "full"}
          />
        )}
        {mode === "ensemble" ? (
          <EnsembleView summary={summary} />
        ) : mode === "evidence" ? (
          <EvidencePanel summary={summary} />
        ) : hasBars || isLive ? (
          <Chart />
        ) : (
          <div className="empty">loading…</div>
        )}
      </div>
    </div>
  );
}
