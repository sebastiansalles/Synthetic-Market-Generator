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
    """MLE for i.i.d. Student-t log returns (loc/scale/df)."""
    from scipy.optimize import minimize
    from scipy.stats import t as student_t

    x = np.asarray(r, dtype=float)
    dt = 1.0 / periods_per_year

    def negloglik(theta):
        df, loc, scale = np.exp(theta[0]), theta[1], np.exp(theta[2])
        return -student_t.logpdf(x, df, loc, scale).sum()

    bounds = [(-2.0, 5.0), (-0.05, 0.05), (-8.0, -2.0)]
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
