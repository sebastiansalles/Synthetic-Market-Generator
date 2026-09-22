import numpy as np
import pandas as pd
import pytest

from synthetic.calibration import acf_values, gate_stats, log_returns, robust_z, scrub_returns


def _returns(vals, start="2024-01-01", freq="B", gap_after=None, gap_days=10):
    n = len(vals)
    if gap_after is None:
        idx = pd.date_range(start, periods=n, freq=freq)
    else:
        head = pd.date_range(start, periods=gap_after + 1, freq=freq)
        tail = pd.date_range(head[-1] + pd.Timedelta(days=gap_days), periods=n - gap_after - 1, freq=freq)
        idx = head.append(tail)
    return pd.Series(vals, index=idx, name="ret")


def test_log_returns_values_and_index():
    df = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-03"]),
            "open": [1.0, 1.0, 1.0],
            "high": [1.1, 1.1, 1.1],
            "low": [0.9, 0.9, 0.9],
            "close": [100.0, 110.0, 99.0],
            "volume": [0.0, 0.0, 0.0],
        }
    )
    r = log_returns(df)

    assert np.isclose(r.iloc[0], np.log(1.1))
    assert np.isclose(r.iloc[1], np.log(0.9))
    assert list(r.index) == list(pd.to_datetime(["2024-01-02", "2024-01-03"]))


def test_robust_z_flags_outliers():
    rng = np.random.default_rng(0)
    calm = rng.normal(0.0, 0.0005, 200)
    calm[100] = 0.05
    z = robust_z(pd.Series(calm))

    assert z.iloc[100] > 10
    assert z.drop(index=100).max() < 10


def _fixture():
    rng = np.random.default_rng(1)
    vals = rng.normal(0.0, 0.0005, 80).tolist()
    vals[20] = 0.08  # corrupt print ...
    vals[21] = -0.081  # ... fully reverted next bar
    vals[40] = -0.09  # real crash: large, NOT reverted
    vals[41] = 0.004
    return vals


def test_scrub_drops_corrupt_pair_and_keeps_real_crash():
    r = _returns(_fixture())
    clean, rep = scrub_returns(r)

    assert rep.n_spike_revert == 1
    dropped = rep.dropped
    assert set(dropped["reason"]) == {"spike_revert", "revert_of_corrupt"}
    assert len(dropped) == 2
    assert len(rep.kept_spikes) == 1  # the un-reverted crash stays, but is reported
    assert np.isclose(rep.kept_spikes["ret"].iloc[0], -0.09)
    assert len(clean) == len(r) - 2


def test_scrub_drops_gap_returns():
    vals = _fixture()
    vals[60] = 0.03  # moderate move, but across a data hole
    r = _returns(vals, gap_after=59, gap_days=10)
    assert pd.Timedelta(days=10) < r.index[61] - r.index[59]

    clean, rep = scrub_returns(r)

    assert rep.n_gap >= 1
    assert "gap" in set(rep.dropped["reason"])
    assert len(clean) == len(r) - 3  # corrupt pair + the gap return


def test_gate_stats_scales_volatility():
    r = pd.Series([0.01, -0.01, 0.02, -0.02, 0.01, -0.02])
    stats = gate_stats(r, periods_per_year=252)

    assert stats["n"] == 6
    assert np.isclose(stats["mean_ret"], r.mean())
    assert np.isclose(stats["vol_ann"], r.std() * np.sqrt(252))
    assert np.isclose(stats["kurtosis"], r.kurt())


def test_acf_values_alternating_series():
    s = pd.Series([1.0, -1.0] * 50)
    acf = acf_values(s, lags=[1, 2, 3])

    assert np.isclose(acf[1], -1.0)
    assert np.isclose(acf[2], 1.0)
    assert np.isclose(acf[3], -1.0)


# ---------------------------------------------------------------------------
# MLE estimators: recovery on synthetic data with known parameters
# ---------------------------------------------------------------------------

from synthetic.calibration import (  # noqa: E402
    FitResult,
    implied_excess_kurtosis,
    mle_gbm,
    mle_merton_jd,
    mle_student_t,
    _num_hessian,
)


def test_num_hessian_recovers_exact_quadratic():
    f = lambda th: 3 * th[0] ** 2 + 0.5 * th[1] ** 2 + th[0] * th[1]
    H = _num_hessian(f, np.array([1.0, -2.0]))

    assert np.allclose(H, [[6.0, 1.0], [1.0, 1.0]], atol=1e-3)


def test_mle_gbm_recovers_parameters_within_se():
    rng = np.random.default_rng(0)
    x = pd.Series(rng.normal(0.0004, 0.006, 20000))

    fit = mle_gbm(x, 252)

    assert fit.converged
    assert abs(fit.params["mu"] - 0.0004 * 252) < 3 * fit.se["mu"]
    assert abs(fit.params["sigma"] - 0.006 * np.sqrt(252)) < 3 * fit.se["sigma"]
    assert implied_excess_kurtosis(fit) == 0.0


def test_mle_student_t_recovers_df():
    rng = np.random.default_rng(1)
    x = pd.Series(0.0002 + 0.005 * rng.standard_t(5, 30000))

    fit = mle_student_t(x, 252)

    assert fit.converged
    assert 4.2 < fit.params["df"] < 6.0
    assert abs(fit.params["mu"] - 0.0002 * 252) < 0.02
    assert 4.5 < implied_excess_kurtosis(fit) < 8.0


def _simulate_jd(rng, n, mu, sigma, lam, mu_j, sig_j, dt=1 / 252):
    n_jumps = rng.poisson(lam * dt, n)
    r = mu * dt + sigma * np.sqrt(dt) * rng.standard_normal(n)
    total = int(n_jumps.sum())
    if total:
        sizes = mu_j + sig_j * rng.standard_normal(total)
        day_idx = np.repeat(np.arange(n), n_jumps)
        r = r + np.bincount(day_idx, weights=sizes, minlength=n)
    return pd.Series(r)


def test_mle_merton_jd_recovers_parameters_and_beats_gbm():
    rng = np.random.default_rng(2)
    lam = 3.0
    x = _simulate_jd(rng, 15000, mu=0.06, sigma=0.15, lam=lam, mu_j=-0.015, sig_j=0.025)

    fit = mle_merton_jd(x, 252, n_starts=5)
    gbm = mle_gbm(x, 252)

    assert fit.converged
    assert fit.loglik > gbm.loglik  # the data-generating model must win selection
    assert abs(fit.params["sigma"] - 0.15) < 0.02
    assert abs(fit.params["lambda"] - lam) < 2.5
    assert abs(fit.params["mu_j"] - (-0.015)) < 0.015
    assert np.isfinite(fit.se["lambda"])  # SE machinery works on the real likelihood


def test_implied_kurtosis_analytic_values():
    assert implied_excess_kurtosis(FitResult("gbm", {}, 0.0, 2, {}, True)) == 0.0
    assert implied_excess_kurtosis(FitResult("student_t", {"df": 6.0}, 0.0, 3, {}, True)) == 3.0
    no_jumps = FitResult(
        "merton_jd", {"mu": 0.0, "sigma": 0.1, "lambda": 0.0, "mu_j": 0.0, "sigma_j": 0.01},
        0.0, 5, {}, True,
    )
    assert implied_excess_kurtosis(no_jumps) == 0.0


# ---------------------------------------------------------------------------
# Regime detection (HMM) and the AssetProfile contract
# ---------------------------------------------------------------------------

from synthetic.calibration import (  # noqa: E402
    calibrate,
    fit_hmm_regimes,
    profile_gate_check,
    simulate_profile_daily,
    simulate_regimes,
)


def _simulate_markov_normal(rng, n, transition, means, vols):
    states = np.empty(n, dtype=int)
    states[0] = 0
    for t in range(1, n):
        states[t] = rng.choice(len(means), p=transition[states[t - 1]])
    r = means[states] + vols[states] * rng.standard_normal(n)
    return r, states


def test_hmm_recovers_regimes_and_decodes_path():
    rng = np.random.default_rng(3)
    transition = np.array([[0.98, 0.02], [0.05, 0.95]])
    means = np.array([0.0003, -0.0008])
    vols = np.array([0.004, 0.012])
    r_np, true_states = _simulate_markov_normal(rng, 4000, transition, means, vols)
    r = pd.Series(r_np, index=pd.date_range("2020-01-01", periods=len(r_np), freq="B"))

    hmm = fit_hmm_regimes(r, n_states=2, n_init=4, seed=0)

    assert hmm.converged
    assert list(np.argsort(hmm.state_var)) == [0, 1]  # canonical calm-first order
    assert 0.003 < hmm.state_vol[0] < 0.005
    assert 0.010 < hmm.state_vol[1] < 0.014
    assert abs(hmm.transition[0, 0] - 0.98) < 0.02
    assert abs(hmm.transition[1, 1] - 0.95) < 0.03
    assert abs(hmm.expected_dwell[0] - 50) < 15  # dwell estimates are noisy: ~80 observed episodes
    accuracy = (hmm.state_path(r) == true_states).mean()
    assert accuracy > 0.9  # vol separation 3x -> near-clean decoding


def test_simulate_regimes_respects_dwell_times():
    rng = np.random.default_rng(4)
    transition = np.array([[0.95, 0.05], [0.08, 0.92]])
    r_np, _ = _simulate_markov_normal(rng, 3000, transition,
                                      np.array([0.0, 0.0]), np.array([0.004, 0.015]))
    hmm = fit_hmm_regimes(pd.Series(r_np), n_states=2, n_init=3, seed=1)

    path = simulate_regimes(hmm, 50000, rng)
    assert set(np.unique(path)) <= set(range(hmm.n_states))

    sim_switch = (np.diff(path) != 0).mean()
    # theoretical stationary switch rate: sum_i pi_i * (1 - P_ii)
    pi1 = hmm.transition[0, 1] / (hmm.transition[0, 1] + hmm.transition[1, 0])
    pi = np.array([1 - pi1, pi1])
    theory_switch = float((pi * (1 - np.diag(hmm.transition))).sum())
    assert abs(sim_switch - theory_switch) < 0.1 * theory_switch


def test_simulate_profile_daily_reproduces_state_volatility():
    rng = np.random.default_rng(5)
    transition = np.array([[0.97, 0.03], [0.06, 0.94]])
    r_np, _ = _simulate_markov_normal(rng, 3000, transition,
                                      np.array([0.0, 0.0]), np.array([0.005, 0.012]))
    close = 100 * np.exp(np.cumsum(r_np))
    df = pd.DataFrame(
        {
            "timestamp": pd.date_range("2021-01-01", periods=len(r_np), freq="B"),
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "volume": np.zeros(len(r_np)),
        }
    )

    profile = calibrate(df, asset="synthetic", n_states=2, seed=0)
    sim = simulate_profile_daily(profile, 40000, np.random.default_rng(6))

    assert profile.hmm.n_states == 2
    # innovations of a pure Gaussian mixture should be near-Gaussian per state —
    # guards against double-counting tail fatness in the composition
    assert profile.state_innov_df.min() > 6
    assert set(profile_gate_check(profile, n_sims=50, seed=2)["verdict"]) == {"PASS"}
    # simulated unconditional vol within 20% of the calibrated target
    assert abs(sim.std() - profile.targets["vol"]) < 0.2 * profile.targets["vol"]


# ---------------------------------------------------------------------------
# Hardening: holdout validation, per-state GARCH, conditional simulation
# ---------------------------------------------------------------------------

from synthetic.calibration import (  # noqa: E402
    fit_state_garch,
    garch_unconditional_var,
    holdout_check,
    simulate_garch_t,
    simulate_profile_conditional,
    time_split,
)


def test_time_split_partitions_at_boundary():
    df = pd.DataFrame(
        {
            "timestamp": pd.date_range("2020-01-01", periods=10, freq="D"),
            "open": 1.0, "high": 1.1, "low": 0.9, "close": 1.0, "volume": 0.0,
        }
    )
    train, test = time_split(df, "2020-01-05")

    assert len(train) == 5 and len(test) == 5
    assert (train["timestamp"] <= pd.Timestamp("2020-01-05")).all()
    assert (test["timestamp"] > pd.Timestamp("2020-01-05")).all()


def test_holdout_check_judges_against_test_targets():
    rng = np.random.default_rng(7)
    transition = np.array([[0.97, 0.03], [0.06, 0.94]])
    r_np, _ = _simulate_markov_normal(rng, 3000, transition,
                                      np.array([0.0, 0.0]), np.array([0.005, 0.012]))
    close = 100 * np.exp(np.cumsum(r_np))
    df = pd.DataFrame(
        {
            "timestamp": pd.date_range("2021-01-01", periods=3000, freq="B"),
            "open": close, "high": close, "low": close, "close": close, "volume": 0.0,
        }
    )
    split_date = str(df["timestamp"].iloc[2000].date())  # ~2/3 train, ~1/3 test
    train, test = time_split(df, split_date)
    profile = calibrate(train, asset="synthetic", n_states=2, seed=0)

    table, targets = holdout_check(profile, test, n_sims=30, seed=3)

    assert set(table["stat"]) == {"mean", "vol", "kurt"}
    assert np.isclose(table.loc[table.stat == "vol", "target"].iloc[0], round(targets["vol"], 4))
    # same distribution both halves -> vol verdict should pass out-of-sample
    assert table.loc[table.stat == "vol", "verdict"].iloc[0] == "PASS"


def test_per_state_garch_and_conditional_simulation():
    rng = np.random.default_rng(8)
    transition = np.array([[0.97, 0.03], [0.06, 0.94]])
    r_np, _ = _simulate_markov_normal(rng, 3000, transition,
                                      np.array([0.0, 0.0]), np.array([0.005, 0.012]))
    close = 100 * np.exp(np.cumsum(r_np))
    df = pd.DataFrame(
        {
            "timestamp": pd.date_range("2021-01-01", periods=3000, freq="B"),
            "open": close, "high": close, "low": close, "close": close, "volume": 0.0,
        }
    )
    profile = calibrate(df, asset="synthetic", n_states=2, seed=0)

    assert len(profile.state_garch) == 2
    for g in profile.state_garch:
        assert {"mu", "omega", "alpha", "beta", "nu", "n_days"} <= set(g)

    # a single-state GARCH-t sim must hover near its theoretical unconditional vol
    g0 = profile.state_garch[0]
    sim = simulate_garch_t(g0, 40000, np.random.default_rng(9))
    theory = np.sqrt(garch_unconditional_var(g0))
    assert abs(sim.std() - theory) < 0.1 * theory

    # the conditional (regime + per-state GARCH) sim must track the marginal vol
    full = simulate_profile_conditional(profile, 40000, np.random.default_rng(10))
    assert abs(full.std() - profile.targets["vol"]) < 0.2 * profile.targets["vol"]


def test_sim_safe_garch_tames_igarch_params():
    explosive = {"mu": 0.0, "omega": 1e-6, "alpha": 0.05, "beta": 0.96,
                 "persistence": 1.01, "nu": 8.0, "uncond_var": 4e-4}

    sim = simulate_garch_t(explosive, 30000, np.random.default_rng(11))

    # level anchored at the sample variance, not blown up by omega/(1-a-b)
    assert abs(sim.std() - 0.02) < 0.2 * 0.02
    assert np.isfinite(sim).all()


def test_profile_carries_last_real_close_anchor():
    rng = np.random.default_rng(33)
    transition = np.array([[0.97, 0.03], [0.06, 0.94]])
    states = np.empty(2500, int)
    states[0] = 0
    for t in range(1, 2500):
        states[t] = rng.choice(2, p=transition[states[t - 1]])
    r_np = np.array([0.005, 0.012])[states] * rng.standard_normal(2500)
    close = 100 * np.exp(np.cumsum(r_np))
    df = pd.DataFrame(
        {
            "timestamp": pd.date_range("2021-01-01", periods=2500, freq="B"),
            "open": close, "high": close, "low": close, "close": close, "volume": 0.0,
        }
    )
    profile = calibrate(df, asset="synthetic", n_states=2, seed=0)
    assert np.isclose(profile.last_close, close[-1])
