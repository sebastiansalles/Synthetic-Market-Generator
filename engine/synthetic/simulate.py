"""Phase 2 stochastic core: regime-switching GARCH(1,1)-t simulation -> OHLCV bars.

One code path: each candle's intrabar price path is a Brownian bridge conditioned
on the candle's close-to-close return (from the GARCH core) and its conditional
variance h_t; ticks are aggregated to OHLC. The same tick stream will drive
Phase 4's tick-by-tick animation.

    simulate(profile, n_candles, scenario?) -> OHLCV DataFrame (validated)
    validation_suite(profile, df_real)       -> Phase 2 gate table
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from synthetic.calibration import (
    AssetProfile,
    calibrated_drift,
    conditional_returns,
    log_returns,
    scrub_returns,
    shift_state_drift,
    simulate_regimes,
)
from synthetic.data import OHLCV_COLUMNS, validate_ohlcv
from synthetic.registry import REGISTRY
from synthetic.volume import simulate_volume


@dataclass(frozen=True)
class Scenario:
    """Stress-test controls, applied on top of the calibrated model.

    Regime forcing: `force_state` for `n_candles` candles from the start of the path,
    then the Markov chain resumes naturally (transitions continue from the forced
    state). `n_candles=0` forces nothing.

    Jump injection: a log-return shock of `jump_return` at candle `jump_at`. The
    2-state model provably cannot produce a crash of this size on its own — that is
    the documented BTC validation failure — so a crash is *injected* as a scenario
    rather than fitted. It enters the GARCH recursion, so the aftershock volatility
    is the model's, not a second piece of hand-tuning."""

    force_state: int = 1  # canonical: 1 = turbulent
    n_candles: int = 60
    jump_at: int | None = None
    jump_return: float = 0.0

    def jump_path(self, n: int) -> np.ndarray | None:
        """The per-candle shock vector this scenario applies, or None if it has no jump."""
        if self.jump_at is None or self.jump_return == 0.0:
            return None
        if not 0 <= self.jump_at < n:
            raise ValueError(f"jump_at {self.jump_at} outside a {n}-candle path")
        shocks = np.zeros(n)
        shocks[self.jump_at] = self.jump_return
        return shocks


@dataclass(frozen=True)
class ScenarioPreset:
    key: str
    label: str
    description: str


SCENARIO_PRESETS: tuple[ScenarioPreset, ...] = (
    ScenarioPreset("standard", "Standard",
                   "The calibrated model, unforced — the asset's own regime mix."),
    ScenarioPreset("turbulent", "Turbulent open",
                   "Opens with 60 forced turbulent candles, then the chain resumes."),
    ScenarioPreset("flash_crash", "Flash crash",
                   "A calm run, then an 8-sigma single-candle collapse the model "
                   "absorbs through its own GARCH aftershock."),
    ScenarioPreset("crisis", "Prolonged crisis",
                   "A sustained turbulent regime — a third of the path forced."),
)

FLASH_CRASH_SIGMA = 8.0  # turbulent-state daily sigmas; ~1-in-a-generation, not fitted


def build_scenario(preset: str, n_candles: int, profile: AssetProfile) -> Scenario | None:
    """Turn a preset key into a concrete Scenario for this asset and path length.

    Crash magnitude is scaled by the asset's own turbulent-state volatility rather
    than fixed in return units, so 'flash crash' means the same *severity* for
    EUR/USD as for Bitcoin instead of the same percentage."""
    known = {p.key for p in SCENARIO_PRESETS}
    if preset not in known:
        raise ValueError(f"unknown scenario preset '{preset}' (have: {sorted(known)})")
    if preset == "standard":
        return None
    if preset == "turbulent":
        return Scenario(force_state=1, n_candles=min(60, n_candles))
    if preset == "crisis":
        return Scenario(force_state=1, n_candles=max(1, n_candles // 3))

    # state_vol is already per-period (daily); state_vol_ann is the annualized view
    turbulent_daily_sigma = float(profile.hmm.state_vol[-1])
    crash_at = int(n_candles * 0.6)
    return Scenario(
        # the calm regime is FORCED until the crash — the preset promises "a calm
        # run, then a collapse"; without this the natural chain opens turbulent
        # ~20% of the time and the calm run the user came to see never appears
        force_state=0,
        n_candles=max(1, crash_at),
        jump_at=crash_at,
        jump_return=-FLASH_CRASH_SIGMA * turbulent_daily_sigma,
    )


def _drift_override(profile: AssetProfile, drift_annual: float | None) -> np.ndarray | None:
    """Per-state mu for a requested long-run annual drift; None keeps the calibrated one."""
    if drift_annual is None:
        return None
    return shift_state_drift(profile, drift_annual / profile.periods_per_year)


def scenario_states(profile: AssetProfile, n: int, scenario: Scenario | None, rng) -> np.ndarray:
    """Regime path, with optional forced stretch at the beginning."""
    states = simulate_regimes(profile.hmm, n, rng)
    if scenario is None:
        return states
    n_forced = min(scenario.n_candles, n)
    states[:n_forced] = scenario.force_state
    cumulative = np.cumsum(profile.hmm.transition, axis=1)
    for t in range(n_forced, n):
        states[t] = np.searchsorted(cumulative[states[t - 1]], rng.random())
    return states


def _daily_timestamps(profile: AssetProfile, n: int,
                      end_date: str | pd.Timestamp | None = None,
                      start_date: str | pd.Timestamp | None = None) -> pd.DatetimeIndex:
    """Timestamps for a simulated path. `start_date` anchors the run forward from a
    date; otherwise it is anchored to END at `end_date` (default: today). Passing
    `start_date` wins.

    The two anchors serve genuinely different purposes and neither is right for both:

    - Forward (`start_date`, typically the bar after the profile's last real one):
      the path reads as a possible *future*, which is what the simulator is for.
      Overlaying synthetic prices on dates that really happened invites the chart to
      be misread as history.
    - Backward (`end_date`): required for export. Backtesters refuse to replay dates
      that have not happened, and they do it silently — a future-dated 500-bar LEAN
      export replayed only the 227 bars before today and skipped the injected crash
      with no error at all."""
    spec = REGISTRY.get(profile.asset)
    freq_daily = bool(spec and spec.calendar == "crypto")
    if start_date is not None:
        start = pd.Timestamp(start_date)
        start = start.tz_localize(None) if start.tz is not None else start
        if freq_daily:
            return pd.date_range(start=start, periods=n, freq="D")
        return pd.bdate_range(start=start, periods=n)
    end = pd.Timestamp(end_date) if end_date is not None else pd.Timestamp.now("UTC").normalize()
    end = end.tz_localize(None) if end.tz is not None else end
    if freq_daily:
        return pd.date_range(end=end, periods=n, freq="D")
    return pd.bdate_range(end=end, periods=n)


def simulate_ticks(profile: AssetProfile, n_candles: int, seed: int = 0,
                   scenario: Scenario | None = None, ticks_per_candle: int = 24,
                   start_price: float = 100.0, end_date: str | None = None,
                   start_date: str | None = None, drift_annual: float | None = None,
                   return_ticks: bool = True) -> tuple[pd.DataFrame, pd.DataFrame | None]:
    """Simulate a full path. Returns (bars, ticks): bars is a validated OHLCV frame
    (one row per candle); ticks — None unless return_ticks — carries the intra-candle
    tick stream (bar_id, tick_in_bar, price) for Phase 4's animation. Mass-simulation
    callers (validation_suite) pass return_ticks=False for ~10x speed."""
    rng = np.random.default_rng(seed)
    states = scenario_states(profile, n_candles, scenario, rng)
    jumps = scenario.jump_path(n_candles) if scenario else None
    mu_states = _drift_override(profile, drift_annual)
    returns, h = conditional_returns(profile, states, rng, jumps=jumps, mu_states=mu_states)
    volumes = simulate_volume(profile.volume, returns, states, profile.hmm.state_vol, rng)
    timestamps = _daily_timestamps(profile, n_candles, end_date, start_date)
    # Intrabar increments use the SAME per-state Student-t innovation family as the
    # daily GARCH core (nu <= 2.1 falls back to Gaussian) — Phase 1's central
    # finding was fat-tailed innovations; Gaussian ticks would contradict it.
    nu = np.array([g["nu"] for g in profile.state_garch])

    def tick_increments(t: int) -> np.ndarray:
        n = ticks_per_candle
        if nu[states[t]] > 2.1:
            z = rng.standard_t(nu[states[t]], n)
            z = z / np.sqrt(nu[states[t]] / (nu[states[t]] - 2.0))  # unit variance
        else:
            z = rng.standard_normal(n)
        return z * np.sqrt(h[t] / n)

    prev_close = start_price
    bar_rows = []
    tick_ids, tick_idx, tick_prices = [], [], []
    for t in range(n_candles):
        e = tick_increments(t)
        delta = returns[t] / ticks_per_candle + (e - e.mean())  # bridge: increments sum to r_t
        path = prev_close * np.exp(np.cumsum(delta))
        bar_rows.append(
            {
                "timestamp": timestamps[t],
                "open": prev_close,
                "high": float(max(path.max(), prev_close)),
                "low": float(min(path.min(), prev_close)),
                "close": float(path[-1]),
                "volume": float(volumes[t]),
            }
        )
        if return_ticks:
            tick_ids.append(np.full(ticks_per_candle, t))
            tick_idx.append(np.arange(ticks_per_candle))
            tick_prices.append(path)
        prev_close = float(path[-1])

    bars = validate_ohlcv(pd.DataFrame(bar_rows)[OHLCV_COLUMNS])
    if not return_ticks:
        return bars, None
    ticks = pd.DataFrame(
        {"bar_id": np.concatenate(tick_ids),
         "tick_in_bar": np.concatenate(tick_idx),
         "price": np.concatenate(tick_prices)}
    )
    if tick_ids:
        ticks["state"] = states[np.concatenate(tick_ids)]
    return bars, ticks


def simulate(profile: AssetProfile, n_candles: int, scenario: Scenario | None = None,
             seed: int = 0, ticks_per_candle: int = 24, start_price: float = 100.0,
             end_date: str | None = None, start_date: str | None = None,
             drift_annual: float | None = None, debug: bool = False):
    """simulate(profile, n_candles, scenario?) -> OHLCV[] (validated). Seeded and
    reproducible; pass debug=True to also get the latent state/return/vol paths."""
    bars, _ = simulate_ticks(profile, n_candles, seed=seed, scenario=scenario,
                             ticks_per_candle=ticks_per_candle, start_price=start_price,
                             end_date=end_date, start_date=start_date,
                             drift_annual=drift_annual, return_ticks=False)
    if not debug:
        return bars
    rng = np.random.default_rng(seed)
    states = scenario_states(profile, n_candles, scenario, rng)
    jumps = scenario.jump_path(n_candles) if scenario else None
    returns, h = conditional_returns(profile, states, rng, jumps=jumps,
                                     mu_states=_drift_override(profile, drift_annual))
    return bars, {"states": states, "returns": returns, "h": h}


# ---------------------------------------------------------------------------
# Phase 2 gate: statistical validation suite, synthetic vs. real
# ---------------------------------------------------------------------------


def _ohlc_shape_metrics(bars: pd.DataFrame) -> dict:
    """Candle-internal structure: the suite validates not just closes but the
    bars themselves (range, close location, body share)."""
    o, h, l, c = (np.log(bars[k].to_numpy(dtype=float)) for k in ("open", "high", "low", "close"))
    log_range = h - l
    eps = 1e-12
    return {
        "range_mean": float(log_range.mean()),
        "body_ratio": float(np.mean(np.abs(c - o) / (log_range + eps))),
        "close_loc": float(np.mean((c - l) / (h - l + eps))),
        "up_bar_share": float(np.mean(c > o)),
    }


def _real_metrics(profile: AssetProfile, df_real: pd.DataFrame) -> dict:
    from synthetic.registry import REGISTRY as REG

    spec = REG.get(profile.asset)
    max_gap = spec.scrub_max_gap_days if spec else 6.0
    r, _ = scrub_returns(log_returns(df_real), max_gap_days=max_gap)
    out = {
        "mean": r.mean(), "vol": r.std(), "kurt": r.kurt(),
        "max_abs": r.abs().max(),
        "turb_share": float(pd.Series(profile.hmm.posterior(r)[:, 1], index=r.index).mean()),
    }
    for lag in (1, 2, 5, 10):
        out[f"r2_acf_{lag}"] = (r**2).autocorr(lag)
    out.update(_ohlc_shape_metrics(df_real))
    v = pd.Series(df_real["volume"].to_numpy(dtype=float),
                  index=pd.DatetimeIndex(df_real["timestamp"])).iloc[1:].reindex(r.index)
    if profile.volume is not None and profile.volume.enabled and np.nanmax(v) > 0:
        out["volu_mean"] = float(v.mean())
        out["volu_corr_absr"] = float(np.corrcoef(np.log1p(v), np.abs(r))[0, 1])
        out["volu_acf1"] = float(pd.Series(np.log1p(v)).autocorr(1))
    return out


def _sim_metrics(bars: pd.DataFrame, states: np.ndarray | None = None) -> dict:
    r = np.log(bars["close"]).diff().dropna()
    out = {"mean": r.mean(), "vol": r.std(), "kurt": r.kurt(), "max_abs": r.abs().max(),
           "turb_share": float(np.mean(states == 1)) if states is not None else np.nan}
    for lag in (1, 2, 5, 10):
        out[f"r2_acf_{lag}"] = (r**2).autocorr(lag)
    out.update(_ohlc_shape_metrics(bars))
    if bars["volume"].max() > 0:
        out["volu_mean"] = float(bars["volume"].mean())
        out["volu_corr_absr"] = float(np.corrcoef(np.log1p(bars["volume"].iloc[1:]), np.abs(r))[0, 1])
        out["volu_acf1"] = float(pd.Series(np.log1p(bars["volume"])).autocorr(1))
    return out


def validation_suite(profile: AssetProfile, df_real: pd.DataFrame,
                     n_sims: int = 200, seed: int = 101) -> pd.DataFrame:
    """Phase 2 gate: simulated paths must reproduce the real data's distributional
    and time-series properties (returns, squared-return ACF, extremes, candle
    internals, regime mix, volume)."""
    real = _real_metrics(profile, df_real)
    n = len(df_real) - 1
    sims = []
    for i in range(n_sims):
        bars, dbg = simulate(profile, n, seed=seed + i, debug=True)
        sims.append(_sim_metrics(bars, states=dbg["states"]))

    rows = []
    for metric, target in real.items():
        s = pd.Series([m.get(metric, np.nan) for m in sims]).dropna()
        if len(s) == 0:
            continue
        lo, hi = s.quantile([0.05, 0.95])
        inside = bool(lo <= target <= hi)
        rows.append({"metric": metric, "real": round(target, 5), "sim_median": round(s.median(), 5),
                     "sim 5-95%": f"[{lo:.5f}, {hi:.5f}]", "inside": inside,
                     "verdict": "PASS" if inside else "CHECK"})
    return pd.DataFrame(rows)
