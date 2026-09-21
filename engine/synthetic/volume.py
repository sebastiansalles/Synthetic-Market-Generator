"""Daily volume modeling.

Volume is not decoration in the OHLCV contract: the validation suite checks its
stylized facts (contemporaneous volume-volatility correlation, persistence).
Model: per-state log-volume driven by the return surprise |r|/sigma_state, plus
AR(1) noise. Assets with no real tape (spot FX) get enabled=False and the
simulator emits zeros (LEAN's forex convention).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class VolumeModel:
    enabled: bool
    state_intercept: np.ndarray  # E[log1p V] at zero return-surprise, per state
    state_slope: np.ndarray  # response to |standardized return|, per state
    ar1: float  # lag-1 autocorrelation of residuals (clamped to [-0.9, 0.9]:
    # real volume is near-unit-root persistent, and an unclamped AR(1) explodes
    # over 5k-bar simulations)
    innov_std: float  # std of AR(1) innovations
    logv_lo: float = 0.0  # simulated log-volume is clipped to the real observed range
    logv_hi: float = 0.0
    z_cap: float = np.inf  # winsorized return-surprise cap (same transform as the fit)


def fit_volume(df: pd.DataFrame, r_clean: pd.Series, hmm, min_days: int = 30) -> VolumeModel:
    """Fit the per-state log-volume model; disabled when the asset has no real tape."""
    v = pd.Series(df["volume"].to_numpy(dtype=float), index=pd.DatetimeIndex(df["timestamp"])).iloc[1:]
    v = v.reindex(r_clean.index).to_numpy(dtype=float)
    k = hmm.n_states
    if not np.isfinite(v).all() or np.nanmax(v) <= 0:
        return VolumeModel(False, np.zeros(k), np.zeros(k), 0.0, 0.0)

    path = hmm.state_path(r_clean)
    z_full = np.abs(np.asarray(r_clean, dtype=float)) / hmm.state_vol[path]
    # Winsorize the return surprise: real volume response saturates, and a handful
    # of crash days (z ~ 10) otherwise dominate OLS and blow up the slope.
    z_cap = float(np.quantile(z_full, 0.95))
    z = np.minimum(z_full, z_cap)
    logv = np.log1p(v)
    intercept = np.zeros(k)
    slope = np.zeros(k)
    for s in range(k):
        mask = path == s
        if mask.sum() < min_days:
            continue
        slope[s], intercept[s] = np.polyfit(z[mask], logv[mask], 1)  # returns [p1, p0]
    resid = logv - (intercept[path] + slope[path] * z)
    var_resid = float(resid.var())
    ar1_raw = float(np.corrcoef(resid[:-1], resid[1:])[0, 1])
    ar1 = float(np.clip(ar1_raw, -0.9, 0.9))
    # Preserve the stationary noise scale across the clamp: with var(e) held at the
    # real residual variance, the simulated volume level and its ACF both match.
    innov_std = float(np.sqrt(var_resid * (1.0 - ar1**2)))
    # Per-state mean anchoring: pick intercepts so the simulated arithmetic mean
    # matches the real per-state mean (exact under lognormality, robust to the
    # heavy right skew a global lognormal correction would over-correct).
    for s in range(k):
        mask = path == s
        if mask.sum() < min_days:
            continue
        var_s = var_resid + slope[s] ** 2 * float(np.var(z[mask]))
        intercept[s] = float(np.log1p(np.mean(v[mask]))) - 0.5 * var_s
    return VolumeModel(
        True, intercept, slope, ar1, innov_std,
        logv_lo=float(np.quantile(logv, 0.005)), logv_hi=float(np.quantile(logv, 0.995)),
        z_cap=z_cap,
    )


def simulate_volume(model: VolumeModel, r: np.ndarray, states: np.ndarray,
                    state_vol: np.ndarray, rng) -> np.ndarray:
    """Generate a volume path for simulated returns; zeros when the model is disabled."""
    n = len(r)
    if not model.enabled:
        return np.zeros(n)
    z = np.minimum(np.abs(r) / state_vol[states], model.z_cap)
    e = np.empty(n)
    e[0] = rng.normal(0.0, model.innov_std)
    for t in range(1, n):
        e[t] = model.ar1 * e[t - 1] + rng.normal(0.0, model.innov_std)
    logv = model.state_intercept[states] + model.state_slope[states] * z + e
    return np.clip(np.expm1(logv), 0.0, None)
