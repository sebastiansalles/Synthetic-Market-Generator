import json

import numpy as np
import pandas as pd
import pytest

from synthetic.calibration import (
    _sim_safe_garch,
    conditional_returns,
    garch_unconditional_var,
    simulate_regimes,
)
from synthetic.data import DEFAULT_PROCESSED_DIR
from synthetic.forecast import conditional_state, state_filter, variance_filter
from synthetic.profile_store import DEFAULT_PROFILE_DIR, load_profile


def _profile(stem="eurusd"):
    return load_profile(DEFAULT_PROFILE_DIR / f"{stem}.pkl")


def _returns(n=400, seed=3):
    rng = np.random.default_rng(seed)
    return pd.Series(0.006 * rng.standard_normal(n),
                     index=pd.date_range("2020-01-01", periods=n, freq="B"))


def test_state_filter_is_a_distribution_per_bar():
    profile = _profile()
    r = _returns()
    ahead = state_filter(profile.hmm, r)
    assert ahead.shape == (len(r) + 1, profile.hmm.n_states)
    assert np.all(ahead >= 0)
    np.testing.assert_allclose(ahead.sum(axis=1), 1.0, atol=1e-12)


def test_state_filter_row_zero_is_the_fit_start_probs():
    """Row 0 is the only place start_probs is the right answer: it IS the fitted
    distribution of the first bar, before any return has been seen."""
    profile = _profile()
    ahead = state_filter(profile.hmm, _returns())
    np.testing.assert_allclose(ahead[0], profile.hmm.start_probs, atol=1e-12)


def test_state_filter_is_causal():
    """Row t may not depend on bar t or anything after it. This is the property that
    separates this filter from `posterior` (forward-backward) and `state_path`
    (Viterbi), both of which condition on the whole series and would leak the
    future into a walk-forward backtest."""
    profile = _profile()
    r = _returns(n=300)
    full = state_filter(profile.hmm, r)
    truncated = state_filter(profile.hmm, r.iloc[:120])
    # the truncated filter's 121 rows must match the full filter's first 121
    np.testing.assert_allclose(truncated, full[:121], atol=1e-12)


def test_variance_filter_reproduces_the_generator_exactly():
    """With `reinit=True` and the generator's own state path, the filter must
    recover the generator's variance path to machine precision — they are the same
    recursion read in opposite directions. If this drifts, a conditional forecast
    is seeded with a variance the simulator would never have produced."""
    profile = _profile()
    rng = np.random.default_rng(11)
    states = simulate_regimes(profile.hmm, 600, rng)
    r, h_path = conditional_returns(profile, states, rng)
    recovered = variance_filter(profile, r, states, reinit=True)
    np.testing.assert_allclose(recovered[: len(r)], h_path, rtol=0, atol=1e-18)


def test_variance_filter_carried_differs_from_reinit():
    """The default carries h across switches; the generator resets. They must not
    silently coincide, or the measured forecast gap between them is a fiction."""
    profile = _profile()
    rng = np.random.default_rng(5)
    states = simulate_regimes(profile.hmm, 800, rng)
    assert len(np.unique(states)) > 1, "need at least one regime switch to compare"
    r, _ = conditional_returns(profile, states, rng)
    carried = variance_filter(profile, r, states, reinit=False)
    reset = variance_filter(profile, r, states, reinit=True)
    assert not np.allclose(carried, reset)


def test_variance_filter_rejects_misaligned_states():
    profile = _profile()
    with pytest.raises(ValueError, match="align"):
        variance_filter(profile, _returns(n=50), np.zeros(49, dtype=int))


def test_simulate_regimes_opens_from_the_stationary_distribution():
    """The regression guard on the start_probs defect. EUR/USD's start_probs is 83%
    turbulent against a 19% long-run share, so the two hypotheses are far apart and
    4000 draws separate them decisively."""
    profile = _profile()
    stationary = profile.hmm.stationary
    assert abs(stationary[0] - profile.hmm.start_probs[0]) > 0.3, "fixture lost its bite"

    rng = np.random.default_rng(0)
    opens = np.array([simulate_regimes(profile.hmm, 1, rng)[0] for _ in range(4000)])
    share_calm = (opens == 0).mean()
    assert abs(share_calm - stationary[0]) < 0.025
    assert abs(share_calm - profile.hmm.start_probs[0]) > 0.3


def test_simulate_regimes_accepts_an_opening_override():
    profile = _profile()
    rng = np.random.default_rng(1)
    forced = np.zeros(profile.hmm.n_states)
    forced[-1] = 1.0  # open in the most turbulent state, every time
    opens = [simulate_regimes(profile.hmm, 3, rng, start_probs=forced)[0] for _ in range(50)]
    assert set(opens) == {profile.hmm.n_states - 1}


def test_simulate_regimes_normalizes_an_unnormalized_override():
    profile = _profile()
    rng = np.random.default_rng(2)
    states = simulate_regimes(profile.hmm, 5, rng, start_probs=np.full(profile.hmm.n_states, 7.0))
    assert states[0] in range(profile.hmm.n_states)


def test_conditional_returns_opens_at_a_supplied_variance():
    profile = _profile()
    states = np.zeros(200, dtype=int)  # one regime: no switch to discard h0
    h0 = 9.0 * garch_unconditional_var(_sim_safe_garch(profile.state_garch[0]))
    _, h_path = conditional_returns(profile, states, np.random.default_rng(4), h0=h0)
    assert h_path[0] == pytest.approx(h0)

    _, baseline = conditional_returns(profile, states, np.random.default_rng(4))
    assert baseline[0] != pytest.approx(h0)
    # and the conditioning decays rather than persisting forever
    assert h_path[-1] < h_path[0]


def test_conditional_state_reports_today_and_serializes():
    profile = _profile()
    df = pd.read_parquet(DEFAULT_PROCESSED_DIR / "eurusd_1d.parquet")
    state = conditional_state(profile, df)

    assert state.asset == "eurusd"
    assert state.n_bars > 1000
    np.testing.assert_allclose(state.state_probs.sum(), 1.0, atol=1e-12)
    assert state.h > 0 and state.ann_vol > 0 and state.uncond_ann_vol > 0
    assert state.vol_ratio == pytest.approx(state.ann_vol / state.uncond_ann_vol)

    blob = json.dumps(state.as_dict(), allow_nan=False)  # NaN would white-screen the UI
    assert json.loads(blob)["asset"] == "eurusd"


def test_conditional_state_is_not_the_unconditional_level():
    """The feature only earns its place if conditioning moves the number. EUR/USD's
    conditional vol sits far below its unconditional level, so this also pins the
    direction, not merely the difference."""
    profile = _profile()
    df = pd.read_parquet(DEFAULT_PROCESSED_DIR / "eurusd_1d.parquet")
    state = conditional_state(profile, df)
    assert state.vol_ratio < 0.8


def test_simulate_regimes_survives_float_noise_in_the_opening_probabilities():
    """`stationary` is an eigenvector, so a near-absorbing state can carry a tiny
    negative component — and rng.choice rejects negatives outright rather than
    degrading. Clipping keeps a profile like that simulable."""
    profile = _profile()
    rng = np.random.default_rng(0)
    noisy = np.array([1.0 + 1e-12, -1e-13])
    states = simulate_regimes(profile.hmm, 10, rng, start_probs=noisy)
    assert states[0] == 0

    # an all-zero vector carries no information; fall back to uniform, not a crash
    states = simulate_regimes(profile.hmm, 10, rng, start_probs=np.zeros(2))
    assert states[0] in (0, 1)
