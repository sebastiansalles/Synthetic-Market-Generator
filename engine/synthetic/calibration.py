"""Calibration data prep: returns, outlier scrubbing, gate statistics.

Raw and processed data stay untouched — scrubbing is a calibration-time view.
Two artifact classes in Yahoo EOD data are handled here:

1. Corrupt prints: a single bar at a level the market never traded, fully
   reverted the next bar (detected as a large robust-z spike whose successor
   reverses it). Both returns around the corrupt bar are polluted and dropped.
2. Gap returns: a close-to-close return spanning a multi-day data hole is a
   k-day return, not a daily one, and is dropped (GARCH/ACF assume regular
   spacing).

Large moves that do NOT revert (e.g. a genuine crash) are kept and reported.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd

DEFAULT_Z_THRESH = 10.0  # robust z (median/MAD based) above which a move is flagged
DEFAULT_MAX_GAP_DAYS = 6  # daily bars: beyond a long weekend => data hole
DEFAULT_REVERT_TOL = 0.5  # a revert undoes at least this fraction of the spike


def log_returns(df: pd.DataFrame) -> pd.Series:
    """Close-to-close log returns, indexed by the timestamp of the second bar."""
    close = df["close"].to_numpy(dtype=float)
    ts = df["timestamp"]
    ret = np.diff(np.log(close))
    return pd.Series(ret, index=pd.DatetimeIndex(ts.iloc[1:]), name="ret")


def robust_z(s: pd.Series) -> pd.Series:
    """Median/MAD based z-score; robust to the very outliers it is meant to flag."""
    mad = 1.4826 * (s - s.median()).abs().median()
    return (s - s.median()).abs() / mad


@dataclass
class ScrubReport:
    n_total: int
    n_spike_revert: int  # corrupt prints detected (the spike half of the pair)
    n_gap: int
    n_kept_spikes: int  # flagged large moves kept (no reversion — possibly real)
    dropped: pd.DataFrame  # timestamp, ret, reason
    kept_spikes: pd.DataFrame  # timestamp, ret, next_ret


def scrub_returns(
    r: pd.Series,
    z_thresh: float = DEFAULT_Z_THRESH,
    max_gap_days: int = DEFAULT_MAX_GAP_DAYS,
    revert_tol: float = DEFAULT_REVERT_TOL,
) -> tuple[pd.Series, ScrubReport]:
    """Drop corrupt-print pairs and gap returns; keep everything else."""
    r = r.dropna()
    idx = r.index
    vals = r.to_numpy(dtype=float)
    n = len(r)

    gap_mask = (idx.to_series().diff().dt.days >= max_gap_days).to_numpy()

    spike = (robust_z(r).to_numpy() > z_thresh) & ~gap_mask
    absv = np.abs(vals)
    sgn = np.sign(vals)
    nxt_abs = np.full(n, np.nan)
    nxt_sign = np.zeros(n)
    nxt_abs[:-1] = absv[1:]
    nxt_sign[:-1] = sgn[1:]
    reverts = (nxt_sign == -sgn) & (nxt_abs >= revert_tol * absv)
    corrupt = spike & reverts

    reasons = np.full(n, "", dtype=object)
    reasons[corrupt] = "spike_revert"
    partner = np.where(corrupt)[0] + 1
    partner = partner[partner < n]
    reasons[partner] = "revert_of_corrupt"
    reasons[(reasons == "") & gap_mask] = "gap"
    drop_pos = np.where(reasons != "")[0]

    spike_pos = np.where(spike)[0]
    kept_spike_pos = [p for p in spike_pos if reasons[p] == ""]

    dropped = pd.DataFrame(
        {"timestamp": idx[drop_pos], "ret": vals[drop_pos], "reason": reasons[drop_pos]}
    ).reset_index(drop=True)
    kept_spikes = pd.DataFrame(
        {
            "timestamp": idx[kept_spike_pos],
            "ret": vals[kept_spike_pos],
            "next_ret": [vals[p + 1] if p + 1 < n else np.nan for p in kept_spike_pos],
        }
    ).reset_index(drop=True)

    clean = r.drop(index=idx[drop_pos])
    report = ScrubReport(
        n_total=n,
        n_spike_revert=int(corrupt.sum()),
        n_gap=int(((reasons == "gap") | ((gap_mask) & (reasons == ""))).sum()),
        n_kept_spikes=len(kept_spike_pos),
        dropped=dropped,
        kept_spikes=kept_spikes,
    )
    return clean, report


def gate_stats(r: pd.Series, periods_per_year: int) -> pd.Series:
    """The Phase 1 gate quantities a fitted model must reproduce within tolerance."""
    return pd.Series(
        {
            "n": len(r),
            "mean_ret": r.mean(),
            "vol": r.std(),
            "vol_ann": r.std() * np.sqrt(periods_per_year),
            "kurtosis": r.kurt(),  # excess kurtosis; a normal distribution has 0
        }
    )


def acf_values(s: pd.Series, lags=range(1, 21)) -> pd.Series:
    """Pearson autocorrelation at each lag (pandas-native, no statsmodels)."""
    return pd.Series({lag: s.autocorr(lag) for lag in lags}, name="acf")


# ---------------------------------------------------------------------------
# Maximum-likelihood estimators (Phase 1)
#
# Conventions: `mu` and `sigma` are per-year parameters of the LOG-return
# process (r_t ≈ N(mu*dt, sigma^2*dt)); lambda is the jump intensity per year.
# Standard errors come from the observed information (inverse numerical Hessian
# of the negative log-likelihood at the optimum) — a parameter without a finite
# SE is a parameter the data cannot constrain.
# ---------------------------------------------------------------------------


@dataclass
class FitResult:
    model: str
    params: dict
    loglik: float
    n_params: int
    se: dict
    converged: bool

    @property
    def aic(self) -> float:
        return 2 * self.n_params - 2 * self.loglik


def _num_hessian(f, x, rel_step=1e-5) -> np.ndarray:
    """Central-difference Hessian of scalar f at x."""
    x = np.asarray(x, dtype=float)
    n = len(x)
    h = rel_step * np.maximum(np.abs(x), 0.1)
    H = np.zeros((n, n))
    f0 = f(x)
    for i in range(n):
        xi_p = x.copy(); xi_p[i] += h[i]
        xi_m = x.copy(); xi_m[i] -= h[i]
        H[i, i] = (f(xi_p) - 2 * f0 + f(xi_m)) / h[i] ** 2
        for j in range(i + 1, n):
            xpp = x.copy(); xpp[i] += h[i]; xpp[j] += h[j]
            xpm = x.copy(); xpm[i] += h[i]; xpm[j] -= h[j]
            xmp = x.copy(); xmp[i] -= h[i]; xmp[j] += h[j]
            xmm = x.copy(); xmm[i] -= h[i]; xmm[j] -= h[j]
            H[i, j] = H[j, i] = (f(xpp) - f(xpm) - f(xmp) + f(xmm)) / (4 * h[i] * h[j])
    return H


def _se_from_hessian(negloglik, theta_hat, log_params: set) -> dict:
    """Inverse-Hessian SEs; log_params are indices whose SE maps by |param|*se(log)."""
    theta_hat = np.asarray(theta_hat, dtype=float)
    H = _num_hessian(negloglik, theta_hat)
    try:
        cov = np.linalg.inv(H)
        if np.any(np.diag(cov) < 0):
            raise np.linalg.LinAlgError("non-positive-definite covariance")
        se_theta = np.sqrt(np.diag(cov))
    except np.linalg.LinAlgError:
        return {i: np.nan for i in range(len(theta_hat))}
    out = {}
    for i, t in enumerate(theta_hat):
        out[i] = abs(t) * se_theta[i] if i in log_params and np.isfinite(t) else se_theta[i]
    return out


def mle_gbm(r: pd.Series, periods_per_year: int = 252) -> FitResult:
    """Analytic MLE for constant-drift Brownian log returns."""
    x = np.asarray(r, dtype=float)
    n = len(x)
    mu_bar, var_bar = x.mean(), x.var()  # MLE variance (n divisor)
    loglik = -0.5 * n * (np.log(2 * np.pi * var_bar) + 1)
    dt = 1.0 / periods_per_year
    params = {"mu": mu_bar / dt, "sigma": np.sqrt(var_bar / dt)}
    se = {"mu": np.sqrt(var_bar / n) / dt, "sigma": np.sqrt(var_bar / (2 * n)) / np.sqrt(dt)}
    return FitResult("gbm", params, loglik, 2, se, True)


def mle_student_t(r: pd.Series, periods_per_year: int = 252, n_starts: int = 8) -> FitResult:
    """MLE for i.i.d. Student-t log returns (loc/scale/df).

    Scale bounds are wide on purpose: this estimator also fits *standardized*
    residuals (scale ~ 1), where log(scale) ≈ 0 — infeasible starts make
    L-BFGS-B clamp to a bound and return garbage. df is bounded below at >2 so
    the variance stays defined."""
    from scipy.optimize import minimize
    from scipy.stats import t as student_t

    x = np.asarray(r, dtype=float)
    dt = 1.0 / periods_per_year

    def negloglik(theta):
        df, loc, scale = np.exp(theta[0]), theta[1], np.exp(theta[2])
        return -student_t.logpdf(x, df, loc, scale).sum()

    bounds = [(np.log(2.05), 5.0), (-0.5, 0.5), (-10.0, 2.0)]
    best = None
    for df0 in np.geomspace(3.0, 40.0, n_starts):
        scale0 = x.std(ddof=1) * np.sqrt((df0 - 2) / df0)
        theta0 = [np.log(df0), x.mean(), np.log(scale0)]
        res = minimize(negloglik, theta0, method="L-BFGS-B", bounds=bounds)
        if best is None or res.fun < best.fun:
            best = res

    df, loc, scale = np.exp(best.x[0]), best.x[1], np.exp(best.x[2])
    se_map = _se_from_hessian(negloglik, best.x, log_params={0, 2})
    params = {
        "df": df,
        "mu": loc / dt,
        "sigma": scale * np.sqrt(df / (df - 2)) / np.sqrt(dt),
        "scale": scale,
    }
    se = {"df": se_map[0], "mu": se_map[1] / dt, "sigma": np.nan}
    return FitResult("student_t", params, -best.fun, 3, se, bool(best.success))


def mle_merton_jd(r: pd.Series, periods_per_year: int = 252, n_starts: int = 8) -> FitResult:
    """MLE for Merton jump-diffusion daily log returns (Poisson mixture of Gaussians).

    Parameters: mu, sigma (diffusion, per year), lambda (jumps/year), mu_j, sigma_j
    (jump size mean/std).
    """
    from scipy.optimize import minimize
    from scipy.special import logsumexp
    from scipy.stats import poisson

    x = np.asarray(r, dtype=float)
    dt = 1.0 / periods_per_year

    def negloglik(theta):
        mu, sigma = theta[0], np.exp(theta[1])
        lam, mu_j, sig_j = np.exp(theta[2]), theta[3], np.exp(theta[4])
        lam_dt = lam * dt
        k_max = max(6, int(poisson.ppf(0.9999999999, lam_dt)) + 1)
        ks = np.arange(k_max + 1)
        logw = poisson.logpmf(ks, lam_dt)[:, None]
        means = (mu * dt + ks * mu_j)[:, None]
        variances = (sigma**2 * dt + ks * sig_j**2)[:, None]
        z = (x[None, :] - means) / np.sqrt(variances)
        terms = logw - 0.5 * z**2 - 0.5 * np.log(2 * np.pi * variances)
        return -logsumexp(terms, axis=0).sum()

    gbm = mle_gbm(r, periods_per_year)
    mu0, sig0 = gbm.params["mu"], gbm.params["sigma"]
    bounds = [(-0.5, 0.5), (-8.0, -1.0), (-7.0, 5.0), (-0.3, 0.3), (-8.0, -2.0)]
    starts = [(lam0, sj0, mj0, sf) for lam0 in (1.0, 4.0, 12.0)
              for sj0, sf in ((0.01, 0.8), (0.03, 0.8)) for mj0 in (0.0,)]
    starts = starts[:n_starts] + [(4.0, 0.02, -0.02, 0.8)]

    best = None
    for lam0, sj0, mj0, sf in starts:
        theta0 = [mu0, np.log(sf * sig0), np.log(lam0), mj0, np.log(sj0)]
        res = minimize(negloglik, theta0, method="L-BFGS-B", bounds=bounds)
        if best is None or res.fun < best.fun:
            best = res

    mu = best.x[0]
    sigma, lam, mu_j, sig_j = np.exp(best.x[1]), np.exp(best.x[2]), best.x[3], np.exp(best.x[4])
    se_map = _se_from_hessian(negloglik, best.x, log_params={1, 2, 4})
    params = {"mu": mu, "sigma": sigma, "lambda": lam, "mu_j": mu_j, "sigma_j": sig_j}
    se = {"mu": se_map[0], "sigma": se_map[1], "lambda": se_map[2],
          "mu_j": se_map[3], "sigma_j": se_map[4]}
    return FitResult("merton_jd", params, -best.fun, 5, se, bool(best.success))


def implied_excess_kurtosis(fit: FitResult, periods_per_year: int = 252) -> float:
    """Analytic excess kurtosis of daily returns under the fitted model."""
    dt = 1.0 / periods_per_year
    if fit.model == "gbm":
        return 0.0
    if fit.model == "student_t":
        df = fit.params["df"]
        return 6.0 / (df - 4) if df > 4 else np.inf
    if fit.model == "merton_jd":
        p = fit.params
        lam_dt = p["lambda"] * dt
        k2 = p["sigma"] ** 2 * dt + lam_dt * (p["mu_j"] ** 2 + p["sigma_j"] ** 2)
        k4 = lam_dt * (p["mu_j"] ** 4 + 6 * p["mu_j"] ** 2 * p["sigma_j"] ** 2 + 3 * p["sigma_j"] ** 4)
        return k4 / k2**2
    raise ValueError(f"unknown model: {fit.model}")


# ---------------------------------------------------------------------------
# Regime detection (Hidden Markov Model) and the AssetProfile contract
# ---------------------------------------------------------------------------


@dataclass
class HMMRegimes:
    """2+ state Gaussian HMM on log returns; states canonically ordered calm -> turbulent."""

    n_states: int
    transition: np.ndarray
    start_probs: np.ndarray
    state_mean: np.ndarray  # per-bar mean log return
    state_var: np.ndarray  # per-bar variance
    expected_dwell: np.ndarray  # days
    loglik: float
    converged: bool
    model: object = None  # fitted hmmlearn object (kept for decoding); not in repr

    @property
    def state_vol(self) -> np.ndarray:
        return np.sqrt(self.state_var)

    def state_vol_ann(self, periods_per_year: int = 252) -> np.ndarray:
        return self.state_vol * np.sqrt(periods_per_year)

    @property
    def stationary(self) -> np.ndarray:
        """Long-run time share of each regime: the left eigenvector of the transition
        matrix for eigenvalue 1. This is the weighting under which per-state
        quantities combine into the chain's long-run average."""
        values, vectors = np.linalg.eig(self.transition.T)
        pi = np.real(vectors[:, np.argmin(np.abs(values - 1.0))])
        return pi / pi.sum()

    def state_path(self, r: pd.Series) -> np.ndarray:
        """Viterbi path, canonically ordered (0 = calmest state)."""
        raw = self.model.predict(np.asarray(r, dtype=float).reshape(-1, 1))
        order = np.argsort(self._fit_variances())
        remap = np.empty_like(order)
        remap[order] = np.arange(self.n_states)
        return remap[raw]

    def posterior(self, r: pd.Series) -> np.ndarray:
        """Smoothed state probabilities, columns ordered calm -> turbulent."""
        order = np.argsort(self._fit_variances())
        return self.model.predict_proba(np.asarray(r, dtype=float).reshape(-1, 1))[:, order]

    def _fit_variances(self) -> np.ndarray:
        return np.asarray(self.model.covars_).ravel()


def fit_hmm_regimes(r: pd.Series, n_states: int = 2, n_init: int = 10, seed: int = 0) -> HMMRegimes:
    """Fit a Gaussian HMM on log returns; states sorted by variance (calm first).

    hmmlearn has no built-in multi-start in this version, so restarts are done
    here, keeping the best model by log-likelihood.
    """
    from hmmlearn.hmm import GaussianHMM

    logging.getLogger("hmmlearn").setLevel(logging.ERROR)  # silence restart chatter
    x = np.asarray(r, dtype=float).reshape(-1, 1)
    best = None
    for i in range(n_init):
        model = GaussianHMM(n_components=n_states, covariance_type="diag",
                            random_state=seed + i).fit(x)
        if best is None or model.score(x) > best.score(x):
            best = model
    model = best
    order = np.argsort(model.covars_.ravel())
    transition = model.transmat_[order][:, order]
    start = model.startprob_[order]
    means = model.means_.ravel()[order]
    variances = model.covars_.ravel()[order]
    return HMMRegimes(
        n_states=n_states,
        transition=transition,
        start_probs=start,
        state_mean=means,
        state_var=variances,
        expected_dwell=1.0 / (1.0 - np.diag(transition)),
        loglik=model.score(x),
        converged=bool(model.monitor_.converged),
        model=model,
    )


def simulate_regimes(regimes: HMMRegimes, n: int, rng,
                     start_probs: np.ndarray | None = None) -> np.ndarray:
    """Sample a state path of length n from the fitted Markov chain.

    The opening state is drawn from the chain's **stationary** distribution, not
    from `regimes.start_probs`. `start_probs` is the fitted distribution of the
    *sample's first bar* — a nuisance parameter of the HMM fit, pinned to whatever
    regime happened to prevail decades ago — and sampling it biased the opening of
    every simulated path. EUR/USD's is 83% turbulent against a 19% long-run share,
    so paths used to open in the turbulent state five times too often and needed
    ~60 bars to mix back; over a 250-bar forecast that is the first quarter spent
    reading an artifact of the fit. Long calibration runs were barely affected
    (thousands of bars swamp the opening), which is why the bug survived: it
    distorts exactly the short horizons a forecast cares about.

    `start_probs` overrides the opening distribution — this is how a conditional
    forecast opens from today's filtered regime probabilities instead of the
    long-run shares. See `synthetic.forecast.state_filter`.
    """
    p0 = regimes.stationary if start_probs is None else np.asarray(start_probs, dtype=float)
    # `stationary` comes out of np.linalg.eig, so a near-absorbing state can carry a
    # tiny negative component as float noise — and rng.choice rejects any negative
    # outright ("Probabilities are not non-negative"), which would crash every
    # simulation for that profile rather than degrade. Clip before normalizing; this
    # also sanitizes a caller-supplied override.
    p0 = np.clip(p0, 0.0, None)
    total = p0.sum()
    if not total > 0:
        p0 = np.full(regimes.n_states, 1.0 / regimes.n_states)
        total = 1.0
    states = np.empty(n, dtype=int)
    states[0] = rng.choice(regimes.n_states, p=p0 / total)
    cumulative = np.cumsum(regimes.transition, axis=1)
    draws = rng.random(n)
    for t in range(1, n):
        states[t] = np.searchsorted(cumulative[states[t - 1]], draws[t])
    return states


def fit_garch_t(r: pd.Series, periods_per_year: int = 252) -> dict:
    """GARCH(1,1) with Student-t innovations via `arch`; params in per-bar decimal units."""
    from arch import arch_model

    res = arch_model(np.asarray(r, dtype=float), vol="GARCH", p=1, q=1, dist="t",
                     rescale=True).fit(disp="off")
    p, se = res.params, res.std_err
    s = res.scale  # rescale=True optimizes internally scaled data; convert back
    return {
        "mu": p["mu"] / s,
        "omega": p["omega"] / s**2,
        "alpha": p["alpha[1]"],
        "beta": p["beta[1]"],
        "persistence": p["alpha[1]"] + p["beta[1]"],
        "nu": p["nu"],
        "se_nu": se["nu"],
        "uncond_var": float(np.asarray(r, dtype=float).var()),  # level anchor for near-unit-root fits
    }


def garch_unconditional_var(g: dict) -> float:
    """Unconditional variance: analytic when covariance-stationary, sample variance
    when persistence >= 0.999 (IGARCH territory — omega/(1-a-b) would explode)."""
    if g["persistence"] < 0.999:
        return g["omega"] / (1.0 - g["alpha"] - g["beta"])
    return g["uncond_var"]


@dataclass
class AssetProfile:
    """The Phase 1/2 contract: everything the simulator and validation consume."""

    asset: str
    periods_per_year: int
    n_bars: int
    targets: dict  # mean / vol / kurt of clean per-bar log returns
    gbm: FitResult
    student_t: FitResult
    merton_jd: FitResult  # exploratory: daily JD is not identified (see notebook §7)
    garch: dict
    state_garch: list  # per-state GARCH(1,1)-t dicts (Viterbi-assigned days)
    hmm: HMMRegimes
    state_innov_df: np.ndarray  # per-state Student-t df on state-standardized residuals
    scrub: ScrubReport
    volume: object = None  # VolumeModel; None/False-y means "emit zeros"
    last_close: float = 100.0  # anchor: simulations start from the last real close
    history_years: float | None = None  # era window the profile was fitted on (None = full)
    last_date: str | None = None  # anchor: simulations continue forward from this bar


def fit_state_innovation_df(r: pd.Series, hmm: HMMRegimes, min_days: int = 30) -> np.ndarray:
    """Per-state Student-t df on state-standardized residuals: within-regime fatness
    with the regime mixture's contribution to the marginal kurtosis removed.
    inf => Gaussian innovations (small or near-normal state)."""
    path = hmm.state_path(r)
    z = np.asarray(r, dtype=float)
    dfs = np.full(hmm.n_states, np.inf)
    for s in range(hmm.n_states):
        mask = path == s
        if mask.sum() < min_days:
            continue
        zs = (z[mask] - hmm.state_mean[s]) / hmm.state_vol[s]
        df = mle_student_t(pd.Series(zs)).params["df"]
        dfs[s] = df if df >= 2.5 else np.inf
    return dfs


def era_window(df: pd.DataFrame, max_history_years: float | None) -> pd.DataFrame:
    """The trailing-era slice a windowed fit is calibrated on; the whole frame if None.

    Defined once and shared: anything that re-measures a windowed profile (the
    validation report, a holdout gate) has to use the same slice the parameters were
    fitted on, or it compares the model against data it never saw."""
    if max_history_years is None:
        return df
    cutoff = df["timestamp"].max() - pd.Timedelta(days=365.25 * max_history_years)
    return df[df["timestamp"] >= cutoff].reset_index(drop=True)


def calibrate(df: pd.DataFrame, asset: str = "unknown", periods_per_year: int | None = None,
              n_states: int = 2, seed: int = 0,
              max_history_years: float | None = None) -> AssetProfile:
    """Full calibration pipeline: OHLCV frame -> scrubbed returns -> AssetProfile.

    `periods_per_year` and scrub gap tolerance default to the asset's registry spec
    (plug-and-play): calibrate(df, asset="btcusd") uses a 365-day calendar.

    `max_history_years` fits on the most recent era only — non-stationary assets
    (e.g. BTC's 1000x growth) carry risk parameters from eras that no longer
    describe them; the window is recorded on the profile for transparency."""

    from synthetic.registry import REGISTRY
    from synthetic.volume import fit_volume

    spec = REGISTRY.get(asset)
    if periods_per_year is None:
        periods_per_year = spec.ppy_daily if spec else 252
    max_gap_days = spec.scrub_max_gap_days if spec else 6.0

    df = era_window(df, max_history_years)

    r = log_returns(df)
    clean, report = scrub_returns(r, max_gap_days=max_gap_days)
    hmm = fit_hmm_regimes(clean, n_states=n_states, seed=seed)
    return AssetProfile(
        asset=asset,
        periods_per_year=periods_per_year,
        n_bars=len(clean),
        targets={"mean": clean.mean(), "vol": clean.std(), "kurt": clean.kurt()},
        gbm=mle_gbm(clean, periods_per_year),
        student_t=mle_student_t(clean, periods_per_year),
        merton_jd=mle_merton_jd(clean, periods_per_year),
        garch=fit_garch_t(clean, periods_per_year),
        state_garch=fit_state_garch(clean, hmm, periods_per_year),
        hmm=hmm,
        state_innov_df=fit_state_innovation_df(clean, hmm),
        scrub=report,
        volume=fit_volume(df, clean, hmm),
        last_close=float(df["close"].iloc[-1]),
        last_date=str(pd.Timestamp(df["timestamp"].iloc[-1]).date()),
        history_years=max_history_years,
    )


def simulate_profile_daily(profile: AssetProfile, n: int, rng) -> np.ndarray:
    """Daily log returns from the profile's MARGINAL model: regime path + per-state
    unit-variance Student-t innovations scaled by per-state volatility. Used for the
    Phase 1 gate; for the conditional (per-state GARCH) model see
    `simulate_profile_conditional`.

    Unit-variance t via normal/chi-square: t = z * sqrt((df-2)/chi2_df)."""
    states = simulate_regimes(profile.hmm, n, rng)
    df = profile.state_innov_df[states]
    df_safe = np.where(np.isinf(df), 3.0, df)  # placeholder for Gaussian states
    z = rng.standard_normal(n)
    chi2 = rng.chisquare(df_safe, n)
    t_unit = np.where(np.isinf(df), z, z * np.sqrt((df_safe - 2) / chi2))
    return profile.hmm.state_mean[states] + profile.hmm.state_vol[states] * t_unit


def profile_gate_check(profile: AssetProfile, n_sims: int = 500, seed: int = 1,
                       targets: dict | None = None, n: int | None = None) -> pd.DataFrame:
    """Phase 1 gate: does simulating from the profile reproduce mean/vol/kurtosis
    within model-implied (parametric-bootstrap) tolerance? Pass `targets` to judge
    against out-of-sample statistics instead of the in-sample ones."""
    from scipy.stats import kurtosis as stats_kurtosis

    n = n or profile.n_bars
    rng = np.random.default_rng(seed)
    sims = np.array([simulate_profile_daily(profile, n, rng) for _ in range(n_sims)])
    stats = {
        "mean": sims.mean(axis=1),
        "vol": sims.std(axis=1),
        "kurt": stats_kurtosis(sims, axis=1, bias=False),
    }
    rows = []
    for name, target in (targets or profile.targets).items():
        s = stats[name]
        lo, hi = np.percentile(s, [5, 95])
        pct = float((s < target).mean())
        if name == "mean":
            tol_pp = 2.0 / profile.periods_per_year  # drift is unidentified: ±2pp/yr acceptance
            verdict = "PASS" if (lo <= target <= hi or abs(s.mean() - target) < tol_pp) else "MARGINAL"
        else:
            verdict = "PASS" if lo <= target <= hi else ("MARGINAL" if 0.01 < pct < 0.99 else "FAIL")
        rows.append({"stat": name, "target": round(target, 4), "sim_mean": round(s.mean(), 4),
                     "sim 5-95%": f"[{lo:.4f}, {hi:.4f}]", "pct_of_target": pct, "verdict": verdict})
    return pd.DataFrame(rows)


def time_split(df: pd.DataFrame, split_date: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split an OHLCV frame into (train, test) at a date boundary."""
    cut = pd.Timestamp(split_date)
    train = df[df["timestamp"] <= cut].reset_index(drop=True)
    test = df[df["timestamp"] > cut].reset_index(drop=True)
    return train, test


def holdout_check(profile: AssetProfile, df_test: pd.DataFrame,
                  n_sims: int = 500, seed: int = 11) -> tuple[pd.DataFrame, dict]:
    """Out-of-sample gate: judge the (train-fitted) profile against statistics of
    the test window. Returns (gate table, test targets)."""
    r_test, _ = scrub_returns(log_returns(df_test))
    targets = {"mean": r_test.mean(), "vol": r_test.std(), "kurt": r_test.kurt()}
    table = profile_gate_check(profile, n_sims=n_sims, seed=seed, targets=targets, n=len(r_test))
    return table, targets


def fit_state_garch(r: pd.Series, hmm: HMMRegimes,
                    periods_per_year: int = 252) -> list[dict]:
    """GARCH(1,1)-t fitted within each HMM state (days assigned by Viterbi path).

    Approximation: state subsets are non-contiguous, so vol persistence across
    state switches is not modeled — a joint Markov-switching GARCH MLE is the
    strict alternative. Documented in the notebook."""
    path = hmm.state_path(r)
    x = np.asarray(r, dtype=float)
    out = []
    for s in range(hmm.n_states):
        xs = x[path == s]
        fit = fit_garch_t(pd.Series(xs), periods_per_year)
        fit["n_days"] = int(len(xs))
        out.append(fit)
    return out


def simulate_garch_t(params: dict, n: int, rng) -> np.ndarray:
    """GARCH(1,1)-t return path started from the unconditional variance."""
    from scipy.stats import t as student_t

    params = _sim_safe_garch(params)
    uncond = garch_unconditional_var(params)
    z = student_t.rvs(params["nu"], size=n, random_state=rng)
    z = z / np.sqrt(params["nu"] / (params["nu"] - 2))  # unit variance
    h = np.empty(n)
    eps = np.empty(n)
    h[0] = uncond
    eps[0] = np.sqrt(h[0]) * z[0]
    for t in range(1, n):
        h[t] = params["omega"] + params["alpha"] * eps[t - 1] ** 2 + params["beta"] * h[t - 1]
        eps[t] = np.sqrt(h[t]) * z[t]
    return params["mu"] + eps


def calibrated_drift(profile: AssetProfile) -> float:
    """The chain's long-run per-period drift: per-state mu weighted by how much time
    the regime chain spends in each state."""
    mu = np.array([_sim_safe_garch(x)["mu"] for x in profile.state_garch])
    return float(np.dot(profile.hmm.stationary, mu))


def shift_state_drift(profile: AssetProfile, target_per_period: float) -> np.ndarray:
    """Per-state mu shifted so the chain's long-run drift equals `target_per_period`.

    Shifts rather than overwrites, so the calibrated *spread* between regimes survives
    — turbulent states keep their characteristically negative mean relative to calm
    ones. Drift is the least identified parameter in the whole model (BTC: 45.7%/yr
    with a standard error near 19pp) yet it compounds over multi-year horizons, so
    being able to neutralize it is a modelling control, not a convenience."""
    mu = np.array([_sim_safe_garch(x)["mu"] for x in profile.state_garch])
    return mu - float(np.dot(profile.hmm.stationary, mu)) + target_per_period


def conditional_returns(profile: AssetProfile, states: np.ndarray, rng,
                        jumps: np.ndarray | None = None,
                        mu_states: np.ndarray | None = None,
                        h0: float | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Per-state GARCH(1,1)-t returns for a GIVEN regime path; also returns the
    conditional variance path h (the OHLC bridge needs it). Variance re-initializes
    at the new state's unconditional level on every regime switch.

    `jumps` (log-return shocks, one per candle, mostly zero) enters the innovation
    BEFORE the variance update, so a shock propagates into the GARCH recursion and
    the model produces its own aftershock volatility. Adding the shock to the
    returned series afterwards would give a crash with an implausibly calm sequel.

    `h0` opens the recursion at a given variance instead of the first state's
    unconditional level — how a conditional forecast starts from today's filtered
    variance. It decays out of the path at the GARCH persistence and is discarded
    outright by the first regime switch, which is the honest behaviour: conditioning
    information has a shelf life and the model should not pretend otherwise."""
    n = len(states)
    g = [_sim_safe_garch(x) for x in profile.state_garch]
    by_state = np.array([x["mu"] for x in g]) if mu_states is None else np.asarray(mu_states, float)
    mu = by_state[states]
    omega = np.array([x["omega"] for x in g])[states]
    alpha = np.array([x["alpha"] for x in g])[states]
    beta = np.array([x["beta"] for x in g])[states]
    nu = np.array([x["nu"] for x in g])[states]
    uncond = np.array([garch_unconditional_var(x) for x in g])[states]

    z = rng.standard_t(nu, n) / np.sqrt(nu / (nu - 2))  # unit-variance t, per-state df
    shock = np.zeros(n) if jumps is None else np.asarray(jumps, dtype=float)
    if len(shock) != n:
        raise ValueError(f"jumps has length {len(shock)}, expected {n}")
    r = np.empty(n)
    h_path = np.empty(n)
    h = uncond[0] if h0 is None else float(h0)
    prev = states[0]
    for t in range(n):
        if states[t] != prev:
            h = uncond[t]
            prev = states[t]
        eps = np.sqrt(h) * z[t] + shock[t]
        r[t] = mu[t] + eps
        h_path[t] = h
        h = omega[t] + alpha[t] * eps**2 + beta[t] * h
    return r, h_path


def simulate_profile_conditional(profile: AssetProfile, n: int, rng) -> np.ndarray:
    """Regime-switching GARCH(1,1)-t with the regime path drawn from the fitted
    Markov chain. This is the Phase 2 simulator's daily core; `simulate()` in
    synthetic.simulate builds OHLCV bars on top of it."""
    states = simulate_regimes(profile.hmm, n, rng)
    r, _ = conditional_returns(profile, states, rng)
    return r


def hmm_bic(loglik: float, n: int, n_states: int) -> float:
    """BIC for a diag-covariance Gaussian HMM: k = transitions + start + means + covars."""
    k = n_states**2 + 2 * n_states - 1
    return -2.0 * loglik + k * np.log(n)


def _sim_safe_garch(g: dict) -> dict:
    """Normalize a fitted GARCH dict for simulation: clamp persistence to <=0.999
    and renormalize omega so the recursion's attractor equals the data's sample
    variance. Level comes from data, dynamics (alpha/beta) from the fit — this
    removes the systematic ~3-6% vol bias of near-unit-root fits whose analytic
    omega/(1-a-b) drifts from the sample level."""
    a, b = min(g["alpha"], 0.5), g["beta"]
    if a + b >= 0.999:
        b = 0.999 - a
    return {**g, "alpha": a, "beta": b, "omega": g["uncond_var"] * (1.0 - a - b)}
