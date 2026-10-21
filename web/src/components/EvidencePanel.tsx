import { useEffect, useRef, useState } from "react";
import { getValidation, ProfileSummary } from "../api";
import {
  bandGeometry,
  densityCoverage,
  formatMetric,
  ValidationMetric,
  ValidationState,
} from "../engine/evidence";
import { useStore } from "../store";
import { DensityChart } from "./DensityChart";

/** The validation suite, on screen.
 *
 *  This is the project's whole claim to rigor and it previously existed only in
 *  notebooks and PROGRESS.md, so the app looked like a charting toy with no evidence
 *  behind it. Every number here is a parametric bootstrap: 200 simulated histories of
 *  the same length as the real one, and the question is whether the real statistic
 *  falls inside the range THIS model generates — never whether a single path matched.
 *
 *  Which is why a failing row is informative rather than hidden. It says the real data
 *  sits outside what the model can produce, and names exactly where. */

const POLL_MS = 3000;

function MetricRow({ m }: { m: ValidationMetric }) {
  const g = bandGeometry(m.lo, m.hi, m.real, m.sim_median);
  return (
    <tr className={m.inside ? "" : "outside"}>
      <th scope="row">{m.label}</th>
      <td className="num">{formatMetric(m.real, m.unit)}</td>
      <td className="rail-cell">
        <span
          className="rail"
          title={`model 5–95%: ${formatMetric(m.lo, m.unit)} to ${formatMetric(m.hi, m.unit)} · median ${formatMetric(m.sim_median, m.unit)}`}
        >
          <i className="rail-band" style={{ left: `${g.bandLeft}%`, width: `${g.bandWidth}%` }} />
          <i className="rail-median" style={{ left: `${g.medianPos}%` }} />
          <i
            className={`rail-real ${m.inside ? "in" : "out"}`}
            style={{ left: `${g.realPos}%` }}
          />
        </span>
      </td>
      <td className="num muted">
        {formatMetric(m.lo, m.unit)} – {formatMetric(m.hi, m.unit)}
      </td>
      <td className={`verdict ${m.inside ? "pass" : "check"}`}>{m.inside ? "inside" : "outside"}</td>
    </tr>
  );
}

export function EvidencePanel({ summary }: { summary: ProfileSummary | undefined }) {
  const asset = useStore((s) => s.asset);
  const [state, setState] = useState<ValidationState | null>(null);
  const [nonce, setNonce] = useState(0); // bumping it re-runs the effect
  const timer = useRef<number>(0);
  // A one-shot flag, not `nonce > 0`: a sticky refresh would force a fresh 30-second
  // run on every later market switch, and — because the poll loop would inherit it —
  // would restart the job on every poll and never finish at all.
  const refreshOnce = useRef(false);

  useEffect(() => {
    let cancelled = false;
    const wantRefresh = refreshOnce.current;
    refreshOnce.current = false;
    setState(null);
    const poll = (refresh: boolean) => {
      getValidation(asset, refresh)
        .then((next) => {
          if (cancelled) return;
          setState(next);
          // the server computes in a background thread; `computing` means come back —
          // and come back WITHOUT refresh, or each poll would restart the job
          if (next.status === "computing") {
            timer.current = window.setTimeout(() => poll(false), POLL_MS);
          }
        })
        .catch((e) => {
          if (!cancelled) setState({ status: "failed", asset, message: (e as Error).message });
        });
    };
    poll(wantRefresh);
    return () => {
      cancelled = true;
      window.clearTimeout(timer.current);
    };
  }, [asset, nonce]);

  const recompute = () => {
    refreshOnce.current = true;
    setNonce((n) => n + 1);
  };

  if (state === null) {
    return <div className="evidence-empty">loading the validation report…</div>;
  }

  if (state.status === "computing") {
    return (
      <div className="evidence-empty">
        <h2>Running the validation suite</h2>
        <p>
          Simulating <strong>{state.n_sims} full histories</strong> of{" "}
          {summary?.display_name ?? asset} and measuring each one against the real series.
          This takes about half a minute the first time; the result is then cached
          against the profile.
        </p>
        <p className="muted">
          It is recomputed automatically whenever the profile is recalibrated — evidence
          must never outlive the parameters it was measured on.
        </p>
      </div>
    );
  }

  if (state.status === "failed") {
    // a failed job stays failed on the server until something asks for a refresh, so
    // without this button the error is a dead end the user cannot get out of
    return (
      <div className="evidence-empty">
        <h2>The validation suite could not run</h2>
        <p className="muted">{state.message}</p>
        <p>
          <button className="link-btn" onClick={recompute}>
            try again
          </button>
        </p>
      </div>
    );
  }

  const { summary: tally, families, metrics, density } = state;
  const coverage = densityCoverage(density);
  const span = summary ? state.n_bars / summary.periods_per_year : null;

  return (
    <div className="evidence">
      <header className="evidence-head">
        <div className="evidence-hero">
          <strong>
            {tally.passed}
            <span className="of">/{tally.total}</span>
          </strong>
          <span>
            statistics of {summary?.display_name ?? state.asset} reproduced
            <br />
            <span className="muted">inside the model&rsquo;s own 5–95% band</span>
          </span>
        </div>
        <p className="evidence-meta">
          {state.n_bars.toLocaleString("en-US")} real bars
          {span ? ` · ${span.toFixed(1)} years` : ""}
          {state.history_years != null ? ` · fitted on the last ${state.history_years}y` : ""}
          {state.last_date ? ` · through ${state.last_date}` : ""}
          <br />
          {state.n_sims} simulated histories · seed {state.seed}
          {state.cache?.computed_at ? ` · computed ${state.cache.computed_at.slice(0, 10)}` : ""}
          <br />
          <button className="link-btn" onClick={recompute}>
            recompute
          </button>
        </p>
      </header>

      <section className="evidence-density">
        <div className="evidence-section-head">
          <h2>Return distribution, measured</h2>
          <div className="chart-legend">
            <span className="lg-real">
              <i /> real returns
            </span>
            <span className="lg-band">
              <i /> model 5–95%
            </span>
            <span className="lg-median">
              <i /> model median
            </span>
          </div>
        </div>
        <p className="evidence-blurb">
          Every bin of the real return histogram against the range of histograms{" "}
          {state.n_sims} simulated histories produce. The real curve inside the band
          means the model generates that part of the shape; outside means it does not.
          The axis is logarithmic because the fat-tail claim lives three orders of
          magnitude below the peak — <strong>{(coverage * 100).toFixed(0)}% of bins
          fall inside the band</strong>.
        </p>
        <DensityChart band={density} />
      </section>

      {families.map((f) => (
        <section key={f.key} className="evidence-family">
          <div className="evidence-section-head">
            <h2>{f.label}</h2>
            <span className={`family-tally ${f.passed === f.total ? "pass" : "check"}`}>
              {f.passed}/{f.total} inside
            </span>
          </div>
          <p className="evidence-blurb">{f.blurb}</p>
          <table className="evidence-table">
            <thead>
              <tr>
                <th scope="col">Statistic</th>
                <th scope="col" className="num">
                  Real
                </th>
                <th scope="col">Where it falls in the model&rsquo;s range</th>
                <th scope="col" className="num">
                  Model 5–95%
                </th>
                <th scope="col">Verdict</th>
              </tr>
            </thead>
            <tbody>
              {metrics
                .filter((m) => m.family === f.key)
                .map((m) => (
                  <MetricRow key={m.key} m={m} />
                ))}
            </tbody>
          </table>
        </section>
      ))}
    </div>
  );
}
