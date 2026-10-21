import { useEffect, useMemo, useState } from "react";
import { ConditionalState, getConditional, ProfileSummary } from "../api";
import { barrierOutcome, drawdownDistribution, terminalRisk } from "../engine/risk";
import { regimeLabel } from "../engine/renderer";
import { useStore } from "../store";

/** The distribution, turned into the questions a position actually poses: where
 *  does this forecast start, what happens to a stop/target pair, and how bad is
 *  the bad case. Everything here is a first-passage or tail quantity with no
 *  closed form under regime-switching GARCH-t — which is exactly why an ensemble
 *  is the instrument rather than a formula. */

const pct = (x: number, dp = 1) => `${(x * 100).toFixed(dp)}%`;

function RegimeMix({ probs }: { probs: number[] }) {
  const top = probs
    .map((p, i) => ({ p, i }))
    .sort((a, b) => b.p - a.p)
    .filter(({ p }) => p >= 0.005);
  return (
    <>
      {top.map(({ p, i }, k) => (
        <span key={i}>
          {k > 0 ? " · " : ""}
          <strong>{pct(p, 0)}</strong> {regimeLabel(i)}
        </span>
      ))}
    </>
  );
}

function Origin({ asset }: { asset: string }) {
  const conditioned = useStore((s) => s.ensCondition);
  const setCondition = useStore((s) => s.setEnsCondition);
  const [state, setState] = useState<ConditionalState | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let live = true;
    setState(null);
    setFailed(false);
    getConditional(asset)
      .then((s) => live && setState(s))
      .catch(() => live && setFailed(true));
    return () => {
      live = false;
    };
  }, [asset]);

  return (
    <section className="risk-block">
      <h3>Forecast opens from</h3>
      <div className="seg">
        <button className={conditioned ? "" : "on"} onClick={() => setCondition(false)}>
          long-run
        </button>
        <button className={conditioned ? "on" : ""} onClick={() => setCondition(true)}>
          today
        </button>
      </div>
      {failed && <p className="hint">could not read today&rsquo;s state</p>}
      {state && (
        <>
          <dl className="risk-rows">
            <div>
              <dt>today</dt>
              <dd>
                <RegimeMix probs={state.state_probs} />
              </dd>
            </div>
            <div>
              <dt>today&rsquo;s vol</dt>
              <dd>
                <strong>{pct(state.ann_vol, 1)}</strong> annualized
              </dd>
            </div>
            <div>
              <dt>long-run vol</dt>
              <dd>
                {pct(state.uncond_ann_vol, 1)} ({state.vol_ratio.toFixed(2)}×)
              </dd>
            </div>
          </dl>
          <p className="hint">
            {conditioned ? (
              <>
                Paths open at today&rsquo;s filtered regime and variance, as of{" "}
                {state.last_date ?? "the last bar"}. The conditioning decays at the
                GARCH persistence and is discarded by the first regime switch — it
                shapes the near bars, not the far ones.
              </>
            ) : (
              <>
                Paths open from the chain&rsquo;s long-run regime shares — the
                asset &ldquo;in general&rdquo;, which is the right question for
                calibration and the wrong one for a forecast from here.
              </>
            )}
          </p>
        </>
      )}
    </section>
  );
}

export function EnsembleRisk({ summary }: { summary: ProfileSummary | undefined }) {
  const ensemble = useStore((s) => s.ensemble);
  const asset = useStore((s) => s.asset);
  const [stopPct, setStopPct] = useState(-8);
  const [targetPct, setTargetPct] = useState(15);

  const paths = ensemble?.paths ?? [];
  const startPrice = ensemble?.start_price ?? 0;

  // Recomputed as paths stream in, deliberately: watching the probabilities settle
  // is how you see how many paths the answer actually needs. All three scans are
  // O(paths x candles) with early exits, far cheaper than the percentile cone.
  const barrier = useMemo(
    () => barrierOutcome(paths, startPrice, stopPct / 100, targetPct / 100),
    [paths, startPrice, stopPct, targetPct],
  );
  const dd = useMemo(() => drawdownDistribution(paths, [0.5, 0.95]), [paths]);
  const tail = useMemo(() => terminalRisk(paths, startPrice, [0.05, 0.01]), [paths, startPrice]);

  const years = summary ? paths[0]?.length / summary.periods_per_year : null;
  const bars = (n: number | null) => (n === null ? "—" : `${Math.round(n)} bars`);

  return (
    <aside className="risk-panel" aria-label="risk analytics">
      <Origin asset={asset} />

      <section className="risk-block">
        <h3>Stop &amp; target</h3>
        <div className="risk-inputs">
          <label className="field">
            <span>stop %</span>
            <input
              type="number"
              max={-0.1}
              step={0.5}
              value={stopPct}
              onChange={(e) => {
                const v = Number(e.target.value);
                if (Number.isFinite(v) && v < 0) setStopPct(v);
              }}
            />
          </label>
          <label className="field">
            <span>target %</span>
            <input
              type="number"
              min={0.1}
              step={0.5}
              value={targetPct}
              onChange={(e) => {
                const v = Number(e.target.value);
                if (Number.isFinite(v) && v > 0) setTargetPct(v);
              }}
            />
          </label>
        </div>
        {paths.length === 0 ? (
          <p className="hint">waiting for paths…</p>
        ) : (
          <>
            <div className="risk-split" role="img"
                 aria-label={`stop first ${pct(barrier.pStop, 0)}, target first ${pct(barrier.pTarget, 0)}, neither ${pct(barrier.pNeither, 0)}`}>
              <span className="risk-seg stop" style={{ flexGrow: Math.max(barrier.pStop, 0.001) }} />
              <span className="risk-seg none" style={{ flexGrow: Math.max(barrier.pNeither, 0.001) }} />
              <span className="risk-seg target" style={{ flexGrow: Math.max(barrier.pTarget, 0.001) }} />
            </div>
            <dl className="risk-rows">
              <div>
                <dt className="stop">stop first</dt>
                <dd>
                  <strong>{pct(barrier.pStop, 0)}</strong> · {bars(barrier.medianBarsToStop)}
                </dd>
              </div>
              <div>
                <dt className="target">target first</dt>
                <dd>
                  <strong>{pct(barrier.pTarget, 0)}</strong> · {bars(barrier.medianBarsToTarget)}
                </dd>
              </div>
              <div>
                <dt>neither</dt>
                <dd>{pct(barrier.pNeither, 0)}</dd>
              </div>
              <div>
                <dt>expectancy</dt>
                <dd className={barrier.expectancy >= 0 ? "pos" : "neg"}>
                  <strong>{pct(barrier.expectancy, 2)}</strong> / trade
                </dd>
              </div>
              <div>
                <dt>mean loss</dt>
                <dd>{pct(barrier.meanStopExit, 2)} vs {pct(stopPct / 100, 1)} set</dd>
              </div>
            </dl>
            <p className="hint">
              Exits are booked at the close that breached the level, not the level
              itself — a path can gap through a stop, which is why the mean loss is
              worse than the stop you set. <strong>These are closes only:</strong> a
              level pierced intrabar but closed back inside is missed, so both hit
              probabilities are lower bounds, increasingly so as the levels tighten.
            </p>
          </>
        )}
      </section>

      <section className="risk-block">
        <h3>Bad case</h3>
        {paths.length === 0 ? (
          <p className="hint">waiting for paths…</p>
        ) : (
          <>
            <dl className="risk-rows">
              <div>
                <dt>median drawdown</dt>
                <dd>{pct(dd.values[0], 1)}</dd>
              </div>
              <div>
                <dt>95th drawdown</dt>
                <dd>
                  <strong>{pct(dd.values[1], 1)}</strong>
                </dd>
              </div>
              <div>
                <dt>worst seen</dt>
                <dd>{pct(dd.worst, 1)}</dd>
              </div>
              {tail.map((t) => (
                <div key={t.alpha}>
                  <dt>
                    ES {pct(t.alpha, 0)}
                  </dt>
                  <dd>
                    <strong>{pct(t.es, 1)}</strong> (VaR {pct(t.var, 1)})
                  </dd>
                </div>
              ))}
            </dl>
            <p className="hint">
              Drawdown runs from each path&rsquo;s own peak, so a path that doubles
              and halves back drew down 50% even though it ended level. Expected
              shortfall is the <em>mean</em> loss once VaR is breached
              {years ? ` over ${years.toFixed(1)} years` : ""} — the number that
              notices fat tails, which is why it is quoted ahead of VaR.
            </p>
          </>
        )}
      </section>
    </aside>
  );
}
