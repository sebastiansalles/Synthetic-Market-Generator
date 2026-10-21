import pathlib

import numpy as np
import pandas as pd

from synthetic.calibration import calibrate, log_returns, scrub_returns
from synthetic.data import validate_ohlcv
from synthetic.profile_store import DEFAULT_PROFILE_DIR, load_profile
from synthetic.registry import REGISTRY
from synthetic.simulate import (
    Scenario,
    simulate,
    simulate_ticks,
    validation_report,
    validation_suite,
)
from synthetic.volume import simulate_volume


def _profile_with_volume(seed=21):
    """2-state synthetic profile where volume genuinely tracks |returns|."""
    rng = np.random.default_rng(seed)
    transition = np.array([[0.97, 0.03], [0.06, 0.94]])
    states = np.empty(3000, int)
    states[0] = 0
    for t in range(1, 3000):
        states[t] = rng.choice(2, p=transition[states[t - 1]])
    r = np.array([0.005, 0.012])[states] * rng.standard_normal(3000)
    volume = 1000 * np.expm1(0.5 + 0.8 * np.abs(r) / np.array([0.005, 0.012])[states]
                             + 0.1 * rng.standard_normal(3000))
    close = 100 * np.exp(np.cumsum(r))
    df = pd.DataFrame(
        {
            "timestamp": pd.date_range("2021-01-01", periods=3000, freq="B"),
            "open": close, "high": close, "low": close, "close": close,
            "volume": volume,
        }
    )
    return calibrate(df, asset="synthetic", n_states=2, seed=0)


def test_simulate_returns_validated_ohlcv_and_is_seeded():
    profile = _profile_with_volume()
    bars = simulate(profile, 500, seed=7)

    validate_ohlcv(bars)  # raises on any contract violation
    assert len(bars) == 500
    pd.testing.assert_frame_equal(bars, simulate(profile, 500, seed=7))


def test_scenario_forces_turbulent_stretch_then_chain_resumes():
    profile = _profile_with_volume()
    scenario = Scenario(force_state=1, n_candles=100)
    bars, dbg = simulate(profile, 800, scenario=scenario, seed=3, debug=True)

    assert (dbg["states"][:100] == 1).all()
    assert (dbg["states"][100:] == 1).mean() < 0.8  # chain resumes, not stuck
    forced_vol = np.std(dbg["returns"][:100])
    natural_vol = np.std(dbg["returns"][400:])
    assert forced_vol > 1.5 * natural_vol


def test_volume_model_tracks_returns_and_is_nonnegative():
    profile = _profile_with_volume()
    assert profile.volume.enabled

    bars, _, _ = simulate_ticks(profile, 2000, seed=5)
    r = np.log(bars["close"]).diff().dropna()
    corr = np.corrcoef(np.log1p(bars["volume"].iloc[1:]), np.abs(r))[0, 1]
    assert (bars["volume"] >= 0).all()
    assert corr > 0.3  # the volume-volatility stylized fact survives simulation


def test_zero_volume_asset_emits_zeros():
    rng = np.random.default_rng(22)
    transition = np.array([[0.97, 0.03], [0.06, 0.94]])
    states = np.empty(2000, int)
    states[0] = 0
    for t in range(1, 2000):
        states[t] = rng.choice(2, p=transition[states[t - 1]])
    r = np.array([0.005, 0.012])[states] * rng.standard_normal(2000)
    close = 100 * np.exp(np.cumsum(r))
    df = pd.DataFrame(
        {
            "timestamp": pd.date_range("2021-01-01", periods=2000, freq="B"),
            "open": close, "high": close, "low": close, "close": close,
            "volume": np.zeros(2000),
        }
    )
    profile = calibrate(df, asset="synthetic", n_states=2, seed=0)
    bars = simulate(profile, 300, seed=9)

    assert not profile.volume.enabled
    assert (bars["volume"] == 0).all()


def test_crypto_calendar_has_no_weekend_gaps():
    from synthetic import data as smg_data

    assert REGISTRY["btcusd"].calendar == "crypto"
    btc = validate_ohlcv(pd.read_parquet(smg_data.DEFAULT_PROCESSED_DIR / "btcusd_1d.parquet"))
    assert len(btc) > 4000
    weekday_counts = pd.DatetimeIndex(btc["timestamp"]).dayofweek.value_counts()
    assert weekday_counts.get(5, 0) > 500  # Saturdays present in a crypto calendar


def test_validation_suite_runs_end_to_end():
    profile = _profile_with_volume()
    bars = simulate(profile, 400, seed=11)
    suite = validation_suite(profile, bars, n_sims=10, seed=201)

    assert {"mean", "vol", "kurt", "r2_acf_1", "max_abs"} <= set(suite["metric"])
    assert {"volu_mean", "volu_corr_absr", "volu_acf1"} <= set(suite["metric"])
    assert suite["sim 5-95%"].str.contains(r"\[", regex=True).all()


# ---------------------------------------------------------------------------
# Pre-Phase-3 consolidation: fat-tailed bridge, candle-shape metrics, persistence
# ---------------------------------------------------------------------------

from synthetic.profile_store import load_profile, save_profile


def test_bridge_conserves_candle_return_with_fat_tailed_ticks():
    profile = _profile_with_volume()
    bars, ticks, _ = simulate_ticks(profile, 100, seed=13, ticks_per_candle=24)

    for bar_id in [0, 50, 99]:
        seg = ticks[ticks.bar_id == bar_id]["price"].to_numpy()
        o = bars["open"].iloc[bar_id]
        bridge_ret = np.log(seg[-1] / o)  # open = previous close by construction
        expected = np.log(bars["close"].iloc[bar_id] / o)
        assert np.isclose(bridge_ret, expected, atol=1e-10)


def test_suite_includes_candle_shape_and_turb_share():
    profile = _profile_with_volume()
    bars = simulate(profile, 400, seed=11)
    suite = validation_suite(profile, bars, n_sims=10, seed=201)

    metrics = set(suite["metric"])
    assert {"range_mean", "body_ratio", "close_loc", "up_bar_share", "turb_share"} <= metrics
    row = suite[suite.metric == "turb_share"].iloc[0]
    assert np.isfinite(row["sim_median"])


def test_profile_persistence_round_trip(tmp_path):
    profile = _profile_with_volume()
    path = save_profile(profile, tmp_path / "synthetic.pkl")
    loaded = load_profile(path)

    assert loaded.asset == profile.asset
    assert loaded.periods_per_year == profile.periods_per_year
    np.testing.assert_allclose(loaded.hmm.transition, profile.hmm.transition)
    np.testing.assert_allclose(loaded.state_innov_df, profile.state_innov_df)
    np.testing.assert_allclose(loaded.targets["vol"], profile.targets["vol"])
    # a loaded profile simulates identically (hmm decode must survive the pickle)
    a = simulate(profile, 200, seed=1)
    b = simulate(loaded, 200, seed=1)
    pd.testing.assert_frame_equal(a, b)


# ---------------------------------------------------------------------------
# Latent paths: the regime path the UI colours must be the one that made the bars
# ---------------------------------------------------------------------------


def test_debug_latent_paths_reproduce_the_bars_they_came_with():
    """The exactness here is the point, not a tolerance to be loosened.

    The intra-candle bridge makes each candle's increments sum to that candle's
    return, so close_t == start * exp(cumsum(returns))_t to floating-point error.
    `simulate(debug=True)` used to recover the latent paths by re-running
    scenario_states + conditional_returns against a SECOND default_rng(seed); that
    agreed only because those two calls happened to be the first draws inside
    simulate_ticks. Any reordering there would have silently handed the chart a
    regime path belonging to a different realization — a heatmap that looks
    plausible and is wrong. simulate_ticks now returns the real paths, and this
    assertion is what notices if that ever stops being true.
    """
    profile = load_profile(DEFAULT_PROFILE_DIR / "eurusd.pkl")
    start = 1.1
    bars, latent = simulate(profile, 500, seed=17, start_price=start, debug=True)
    reconstructed = start * np.exp(np.cumsum(latent["returns"]))
    assert np.allclose(reconstructed, bars["close"].to_numpy(), rtol=0, atol=1e-12)
    assert len(latent["states"]) == len(bars) == 500
    assert set(np.unique(latent["states"])) <= set(range(profile.hmm.n_states))
    assert (latent["h"] > 0).all()


def test_debug_latent_paths_follow_a_scenario_and_a_drift_override():
    """The forced-regime prefix and the drift shift must both show up in the latent
    path the chart is given, or the heatmap would contradict the scenario label."""
    profile = load_profile(DEFAULT_PROFILE_DIR / "eurusd.pkl")
    scenario = Scenario(force_state=1, n_candles=40)
    bars, latent = simulate(profile, 200, scenario=scenario, seed=3,
                            start_price=1.1, drift_annual=0.0, debug=True)
    assert (latent["states"][:40] == 1).all()
    reconstructed = 1.1 * np.exp(np.cumsum(latent["returns"]))
    assert np.allclose(reconstructed, bars["close"].to_numpy(), rtol=0, atol=1e-12)


def test_simulate_ticks_latent_matches_the_tick_stream_states():
    """ws_simulate reads regimes off the tick frame; /simulate reads them off the
    latent dict. Both must describe the same session."""
    profile = load_profile(DEFAULT_PROFILE_DIR / "eurusd.pkl")
    bars, ticks, latent = simulate_ticks(profile, 60, seed=9, ticks_per_candle=4,
                                         start_price=1.1)
    per_bar = ticks.groupby("bar_id")["state"].first().to_numpy()
    assert np.array_equal(per_bar, latent["states"])


def test_validation_report_is_strictly_json_serializable():
    """Python's json writes bare NaN and Infinity, which JSON.parse rejects — so a
    single unmeasurable metric would white-screen the evidence panel rather than show
    a gap. The report must contain no non-finite numbers at all."""
    import json

    profile = _profile_with_volume()
    bars = simulate(profile, 300, seed=11)
    report = validation_report(profile, bars, n_sims=6, seed=7)
    json.dumps(report, allow_nan=False)  # raises ValueError if any NaN slipped in

    for m in report["metrics"]:
        for field in ("real", "sim_median", "lo", "hi"):
            assert np.isfinite(m[field]), (m["key"], field)
    for series in ("real", "lo", "median", "hi", "centers"):
        assert np.isfinite(report["density"][series]).all()


def test_real_returns_resolves_an_era_variant_to_its_base_asset():
    """The registry is keyed by base asset. An era stem that misses the lookup falls
    back to the default scrub threshold, which would judge the era profile against
    returns scrubbed differently from the ones it was fitted on."""
    import dataclasses

    from synthetic.registry import REGISTRY
    from synthetic.simulate import real_returns

    profile = load_profile(DEFAULT_PROFILE_DIR / "btcusd.pkl")
    df = pd.read_parquet(
        pathlib.Path(__file__).resolve().parents[2] / "data/processed/btcusd_1d.parquet"
    )
    base = real_returns(profile, df)

    era = dataclasses.replace(profile, asset="btcusd_last3y")
    assert REGISTRY.get("btcusd_last3y") is None  # the lookup that used to miss
    assert real_returns(era, df).equals(base)
