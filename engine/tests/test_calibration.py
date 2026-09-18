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
