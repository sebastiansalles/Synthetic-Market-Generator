"""Conditioning the simulator on today.

Everything upstream of this module is *unconditional*: draw a regime path from the
fitted chain, open GARCH at the state's unconditional variance, let it run. That
answers "what does this asset's distribution look like in general" — the right
question for calibration gates and scenario design, and the wrong one for a
forecast, which asks "what does it look like *from here*". The two diverge most
exactly when the answer matters: right after a shock, or deep into a calm stretch.

This module filters the real history to recover the two numbers a forecast must
start from — the regime distribution for the next bar and the conditional variance
for the next bar.

Both passes are **causal**: bar `t` sees only bars before it. That is not merely
tidy, it is the whole point. `HMMRegimes.state_path` (Viterbi) and
`HMMRegimes.posterior` (forward–backward) both condition on the *entire* series,
so a variance path driven by either leaks the future into the middle of the sample
and flatters any walk-forward backtest built on it. At the final bar there is no
future to leak and the two agree closely; everywhere else the smoothed version is
cheating, and the cheat is large. Forecasting the next 20 bars' log realized
variance (Mincer–Zarnowitz R², EUR/USD):

    Viterbi-driven            0.596     <- hindsight; do not believe this one
    causal, carried h         0.557     <- what this module computes
    trailing-20 realized var  0.497     <- the trivial baseline to beat
    causal, re-init h         0.407

Two things worth knowing before building on this. The hindsight premium is real
and large — roughly the entire apparent edge over the trivial baseline. And the
engine's filter beats a 20-bar realized variance only narrowly (EUR/USD 0.557 vs
0.497, gold 0.319 vs 0.307) and *loses* on BTC (0.137 vs 0.196). The regime-GARCH
machinery earns its keep in generating honest distributions, not in out-forecasting
a moving average of squared returns.

**Shelf life.** Conditioning information is discarded by the first regime switch,
so how long it shapes a forecast is the asset's dwell time — and that varies by an
order of magnitude across this registry:

    asset           dwell (d)   median bars to 1st switch
    eurusd          104 / 24    56
    xauusd           59 / 17    31
    btcusd           11 /  4     6
    btcusd_last3y     5 /  2     3

So a conditioned EUR/USD forecast differs from an unconditional one across a whole
quarter, while a conditioned BTC forecast has converged to the unconditional one
within a week. Measured on the opening cross-sectional spread of 300 paths,
EUR/USD narrows from 0.0071 to 0.0040 over the first 5 bars and `btcusd_last3y`
does not move at all, despite having a comparable conditional/unconditional vol
ratio. This is not a bug to fix: a fast-switching chain genuinely forgets, and a
forecast that kept insisting on today's variance would be the dishonest one. It
does mean the feature is worth far more on FX and gold than on crypto.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from synthetic.calibration import (
    AssetProfile,
    HMMRegimes,
    _sim_safe_garch,
    garch_unconditional_var,
)


def state_filter(regimes: HMMRegimes, r: pd.Series | np.ndarray) -> np.ndarray:
    """Causal one-step-ahead regime probabilities (the Hamilton filter).

    Returns an `(n + 1, n_states)` array in canonical (calm → turbulent) order.
    Row `t` is ``P(state_t | r_0 .. r_{t-1})`` — what was knowable *before* bar t —
    and the extra final row is ``P(state_n | r_0 .. r_{n-1})``, the distribution a
    forecast continuing after the last observed bar opens from. That final row is
    the one callers usually want; the rest exist so a backtest can ask the same
    question at every historical bar without re-running the filter.

    `regimes.start_probs` seeds row 0 because there it is exactly right: it *is*
    the fitted distribution of the sample's first bar. Its influence decays at the
    chain's mixing rate and is gone long before the final row.
    """
    rv = np.asarray(r, dtype=float).ravel()
    mu = np.asarray(regimes.state_mean, dtype=float)
    sd = np.sqrt(np.asarray(regimes.state_var, dtype=float))
    transition = np.asarray(regimes.transition, dtype=float)
    stationary = regimes.stationary

    out = np.empty((len(rv) + 1, regimes.n_states))
    ahead = np.asarray(regimes.start_probs, dtype=float).copy()
    for t, x in enumerate(rv):
        out[t] = ahead
        # Gaussian likelihood up to a constant factor — the 1/sqrt(2*pi) cancels in
        # the normalization below, so it is not worth computing.
        lik = np.exp(-0.5 * ((x - mu) / sd) ** 2) / sd
        post = ahead * lik
        total = post.sum()
        # A return far enough into the tail underflows every state's likelihood to
        # zero. Falling back to the stationary distribution is the one choice that
        # cannot invent information: it says "this bar told us nothing".
        post = post / total if total > 0 else stationary
        ahead = post @ transition
    out[len(rv)] = ahead
    return out


def variance_filter(
    profile: AssetProfile,
    r: pd.Series | np.ndarray,
    states: np.ndarray,
    reinit: bool = False,
) -> np.ndarray:
    """Conditional variance under the GARCH recursion, per state.

    Returns `n + 1` variances: row `t` is the variance for bar `t`, known before it,
    and the final row is what a forecast starting after the last bar opens at.

    `reinit` chooses whether variance resets to the new state's unconditional level
    on a regime switch, as `conditional_returns` does when it *generates* paths.
    The default is False — deliberately unlike the generator — and the asymmetry is
    the point: the generator knows its own regime path exactly, by construction,
    so resetting there is free. A filter only *estimates* the path, and a reset on
    a misclassified switch throws away every shock the recursion had accumulated.
    The causal MAP path disagrees with Viterbi on 8–18% of bars, which is enough to
    make resetting actively harmful: R² 0.557 carried against 0.407 re-initialized
    on EUR/USD, forecasting the next 20 bars' realized variance.

    Pass `reinit=True` to reproduce the generator's own recursion exactly, which is
    what a test comparing filter to generator on a *known* state path should do.
    """
    rv = np.asarray(r, dtype=float).ravel()
    path = np.asarray(states, dtype=int).ravel()
    if len(path) != len(rv):
        raise ValueError(f"states has length {len(path)}, expected {len(rv)} to align")

    g = [_sim_safe_garch(x) for x in profile.state_garch]
    uncond = np.array([garch_unconditional_var(x) for x in g])

    out = np.empty(len(rv) + 1)
    h = uncond[path[0]]
    prev = path[0]
    for t, x in enumerate(rv):
        s = path[t]
        if reinit and s != prev:
            h = uncond[s]
        prev = s
        out[t] = h
        eps = x - g[s]["mu"]
        h = g[s]["omega"] + g[s]["alpha"] * eps**2 + g[s]["beta"] * h
    out[len(rv)] = h
    return out


@dataclass
class ConditionalState:
    """Where a forecast should start, given everything the asset has done so far."""

    asset: str
    n_bars: int
    last_date: str | None
    state_probs: np.ndarray  # P(next state | history), canonical calm -> turbulent
    stationary: np.ndarray  # the long-run shares, for comparison
    h: float  # conditional variance for the next bar
    ann_vol: float  # that variance, annualized
    uncond_ann_vol: float  # the unconditional level the old ensemble opened at
    periods_per_year: int

    @property
    def vol_ratio(self) -> float:
        """How far today sits from the unconditional level. 1.0 means they agree —
        which happens, and is exactly why the number is worth showing rather than
        assuming the conditioning always bites."""
        return self.ann_vol / self.uncond_ann_vol if self.uncond_ann_vol > 0 else 1.0

    def as_dict(self) -> dict:
        return {
            "asset": self.asset,
            "n_bars": self.n_bars,
            "last_date": self.last_date,
            "state_probs": [float(x) for x in self.state_probs],
            "stationary": [float(x) for x in self.stationary],
            "h": float(self.h),
            "ann_vol": float(self.ann_vol),
            "uncond_ann_vol": float(self.uncond_ann_vol),
            "vol_ratio": float(self.vol_ratio),
            "periods_per_year": self.periods_per_year,
        }


def conditional_state(profile: AssetProfile, df_real: pd.DataFrame) -> ConditionalState:
    """Filter the real history and report where a forecast should open.

    The regime sequence driving the variance filter is the causal MAP state —
    `argmax` of each row of `state_filter` — and not Viterbi, for the hindsight
    reason in this module's docstring, and the variance carries across switches for
    the reason in `variance_filter`'s. The unconditional comparison is the
    stationary-weighted variance, i.e. what a correctly-seeded unconditional
    ensemble opens at, so `vol_ratio` measures the conditioning alone and not the
    `start_probs` bug it replaced.
    """
    from synthetic.simulate import real_returns

    r = real_returns(profile, df_real)
    ahead = state_filter(profile.hmm, r)
    states = ahead[: len(r)].argmax(axis=1)
    h = variance_filter(profile, r, states)

    g = [_sim_safe_garch(x) for x in profile.state_garch]
    uncond = np.array([garch_unconditional_var(x) for x in g])
    ppy = profile.periods_per_year
    last = r.index[-1] if isinstance(r, pd.Series) and len(r) else None

    return ConditionalState(
        asset=profile.asset,
        n_bars=len(r),
        last_date=str(last)[:10] if last is not None else None,
        state_probs=ahead[-1],
        stationary=profile.hmm.stationary,
        h=float(h[-1]),
        ann_vol=float(np.sqrt(h[-1] * ppy)),
        uncond_ann_vol=float(np.sqrt(float(uncond @ profile.hmm.stationary) * ppy)),
        periods_per_year=ppy,
    )
