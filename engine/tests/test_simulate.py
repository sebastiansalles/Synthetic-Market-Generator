import numpy as np
import pandas as pd

from synthetic.calibration import calibrate, log_returns, scrub_returns
from synthetic.data import validate_ohlcv
from synthetic.registry import REGISTRY
from synthetic.simulate import Scenario, simulate, simulate_ticks, validation_suite
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

    bars, _ = simulate_ticks(profile, 2000, seed=5)
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
