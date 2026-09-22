import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

API_DIR = Path(__file__).resolve().parents[2] / "api"
sys.path.insert(0, str(API_DIR))

from fastapi.testclient import TestClient  # noqa: E402

from app import app  # noqa: E402

client = TestClient(app)


def test_health_lists_profiles_dir():
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert "eurusd" in body["registered_assets"]


def test_assets_endpoint_describes_registry():
    assets = client.get("/assets").json()
    assert assets["btcusd"]["calendar"] == "crypto"
    assert assets["eurusd"]["has_real_volume"] is False


def test_list_and_get_profile():
    listing = client.get("/profiles").json()
    assert "eurusd" in listing
    summary = client.get("/profiles/eurusd").json()
    assert summary["asset"] == "eurusd"
    assert len(summary["state_vol_ann"]) == 2
    assert len(summary["transition"]) == 2
    assert set(summary["targets"]) == {"mean", "vol", "kurt"}


def test_get_profile_404_for_unknown():
    assert client.get("/profiles/nope").status_code == 404


def test_simulate_returns_validated_bars_seeded():
    req = {"asset": "xauusd", "n_candles": 120, "seed": 5}
    body = client.post("/simulate", json=req).json()
    assert body["n_candles"] == 120
    bar = body["bars"][0]
    assert {"timestamp", "open", "high", "low", "close", "volume"} <= set(bar)
    assert bar["high"] >= max(bar["open"], bar["close"])
    assert bar["low"] <= min(bar["open"], bar["close"])

    again = client.post("/simulate", json=req).json()
    assert again["bars"] == body["bars"]  # seeded reproducibility through the API


def test_simulate_with_scenario():
    body = client.post("/simulate", json={
        "asset": "eurusd", "n_candles": 100, "seed": 1,
        "scenario": {"force_state": 1, "n_candles": 40},
    }).json()
    assert body["n_candles"] == 100


def test_simulate_404_and_candle_cap():
    assert client.post("/simulate", json={"asset": "nope"}).status_code == 404
    assert client.post("/simulate", json={"asset": "eurusd", "n_candles": 10_001}).status_code == 422


def test_recalibrate_uses_engine_and_persists(monkeypatch, tmp_path):
    from synthetic.calibration import AssetProfile, FitResult, fit_hmm_regimes, log_returns, scrub_returns

    def fake_calibrate(df, asset, **kw):
        r, rep = scrub_returns(log_returns(df))
        hmm = fit_hmm_regimes(r, n_states=2, seed=0)
        zero = FitResult("gbm", {"mu": 0.0, "sigma": 0.1}, 0.0, 2, {}, True)
        tfit = FitResult("student_t", {"df": 5.0, "mu": 0.0, "sigma": 0.1}, 0.0, 3, {}, True)
        return AssetProfile(asset=asset, periods_per_year=252, n_bars=len(r),
                            targets={"mean": 0.0, "vol": r.std(), "kurt": r.kurt()},
                            gbm=zero, student_t=tfit, merton_jd=zero,
                            garch={"persistence": 0.95}, state_garch=[{"mu": 0, "omega": 1e-5, "alpha": 0.05,
                                                    "beta": 0.9, "persistence": 0.95, "nu": 8.0,
                                                    "uncond_var": r.var(), "n_days": len(r)},
                                                   {"mu": 0, "omega": 1e-5, "alpha": 0.05,
                                                    "beta": 0.9, "persistence": 0.95, "nu": 8.0,
                                                    "uncond_var": 4 * r.var(), "n_days": len(r)}],
                            hmm=hmm, state_innov_df=np.array([np.inf, np.inf]),
                            scrub=rep, volume=None)

    import app as app_module

    monkeypatch.setattr(app_module, "calibrate", fake_calibrate)
    monkeypatch.setattr(app_module, "DEFAULT_PROFILE_DIR", tmp_path)
    resp = client.post("/profiles/eurusd/recalibrate")
    assert resp.status_code == 202
    assert (tmp_path / "eurusd.pkl").exists()
    assert resp.json()["profile"]["asset"] == "eurusd"


def test_cors_headers_present():
    resp = client.options("/simulate", headers={
        "Origin": "http://localhost:5173",
        "Access-Control-Request-Method": "POST",
    })
    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == "*"


def test_profile_cache_avoids_repeat_unpickle(monkeypatch, tmp_path):
    import app as app_module
    from synthetic.profile_store import save_profile
    from synthetic.calibration import AssetProfile, FitResult, fit_hmm_regimes, log_returns, scrub_returns
    from synthetic import data as smg_data

    df = pd.read_parquet(smg_data.DEFAULT_PROCESSED_DIR / "eurusd_1d.parquet")
    r, rep = scrub_returns(log_returns(df))
    hmm = fit_hmm_regimes(r, n_states=2, seed=0)
    zero = FitResult("gbm", {"mu": 0.0, "sigma": 0.1}, 0.0, 2, {}, True)
    tfit = FitResult("student_t", {"df": 5.0, "mu": 0.0, "sigma": 0.1}, 0.0, 3, {}, True)
    g = {"mu": 0, "omega": 1e-5, "alpha": 0.05, "beta": 0.9, "persistence": 0.95,
         "nu": 8.0, "uncond_var": r.var(), "n_days": len(r)}
    profile = AssetProfile(asset="eurusd", periods_per_year=252, n_bars=len(r),
                           targets={"mean": 0.0, "vol": r.std(), "kurt": r.kurt()},
                           gbm=zero, student_t=tfit, merton_jd=zero,
                           garch={"persistence": 0.95}, state_garch=[dict(g), dict(g)],
                           hmm=hmm, state_innov_df=np.array([np.inf, np.inf]),
                           scrub=rep, volume=None)
    save_profile(profile, tmp_path / "eurusd.pkl")

    monkeypatch.setattr(app_module, "DEFAULT_PROFILE_DIR", tmp_path)
    app_module._profile_cache.clear()

    calls = {"n": 0}
    real_load = app_module.load_profile

    def counting_load(path):
        calls["n"] += 1
        return real_load(path)

    monkeypatch.setattr(app_module, "load_profile", counting_load)
    client.get("/profiles/eurusd")
    client.get("/profiles/eurusd")
    client.post("/simulate", json={"asset": "eurusd", "n_candles": 10, "seed": 1})
    assert calls["n"] == 1  # subsequent reads served from the mtime-keyed cache


def test_corrupt_profile_returns_clean_json_500(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    import app as app_module

    (tmp_path / "broken.pkl").write_bytes(b"not a pickle")
    monkeypatch.setattr(app_module, "DEFAULT_PROFILE_DIR", tmp_path)
    app_module._profile_cache.clear()

    quiet = TestClient(app, raise_server_exceptions=False)  # let the 500 response surface
    resp = quiet.get("/profiles/broken")
    assert resp.status_code == 500
    assert "error" in resp.json()


def test_static_frontend_mounted_when_dist_exists():
    from app import _DIST

    if not _DIST.exists():
        import pytest

        pytest.skip("web/dist not built")
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert client.get("/health").status_code == 200  # API keeps precedence


def test_simulation_anchors_to_last_real_close_by_default():
    summary = client.get("/profiles/eurusd").json()
    body = client.post("/simulate", json={"asset": "eurusd", "n_candles": 30, "seed": 9}).json()
    first_open = body["bars"][0]["open"]
    assert abs(first_open - summary["last_close"]) < 1e-9

    custom = client.post("/simulate", json={
        "asset": "eurusd", "n_candles": 10, "seed": 9, "start_price": 50.0,
    }).json()
    assert abs(custom["bars"][0]["open"] - 50.0) < 1e-9


def test_health_reports_version():
    body = client.get("/health").json()
    assert body["version"] == "0.2.0"


def test_html_entry_point_is_not_cached():
    from app import _DIST

    if not _DIST.exists():
        import pytest

        pytest.skip("web/dist not built")
    resp = client.get("/")
    assert resp.headers.get("cache-control") == "no-store"


def _read_session(params: str):
    with client.websocket_connect(f"/ws/simulate?{params}") as ws:
        opens = closes = ticks = generating = 0
        open_states = []
        while True:
            msg = ws.receive_json()
            if msg["type"] == "generating":
                generating += 1
            elif msg["type"] == "bar_open":
                opens += 1
                open_states.append(msg["state"])
            elif msg["type"] == "tick":
                ticks += 1
                assert np.isfinite(msg["price"])
            elif msg["type"] == "bar_close":
                closes += 1
            elif msg["type"] == "done":
                break
            else:
                raise AssertionError(f"unexpected message: {msg}")
    return opens, closes, ticks, open_states, generating


def test_websocket_streams_full_session():
    opens, closes, ticks, open_states, generating = _read_session(
        "asset=eurusd&seed=3&n_candles=5&ticks_per_candle=4&ticks_per_sec=0")
    assert opens == closes == 5
    assert ticks == 20
    assert set(open_states) <= {0, 1}
    assert generating == 1  # sent before off-loop generation starts


def test_websocket_scenario_forces_opening_regime():
    _, _, _, open_states, _ = _read_session(
        "asset=eurusd&seed=3&n_candles=5&ticks_per_candle=1&ticks_per_sec=0"
        "&force_state=1&force_n=3")
    assert open_states[:3] == [1, 1, 1]  # deterministic forced stretch


@pytest.mark.parametrize(
    "params, expected",
    [("n_candles=0", "n_candles"),
     ("n_candles=10001", "n_candles"),
     ("n_candles=5&ticks_per_candle=0", "ticks_per_candle"),
     ("n_candles=5&ticks_per_candle=201", "ticks_per_candle")],
)
def test_websocket_rejects_out_of_range_session_size(params, expected):
    """The WS stream honours the same caps as POST /simulate — an unbounded
    n_candles would let one client generate an arbitrarily large session."""
    with client.websocket_connect(f"/ws/simulate?asset=eurusd&ticks_per_sec=0&{params}") as ws:
        msg = ws.receive_json()
    assert msg["type"] == "error"
    assert expected in msg["message"]


def test_websocket_states_match_the_simulated_tick_stream():
    """Regime heat must come from the same path as the bars. The endpoint used to
    re-derive states from a second RNG, which silently desyncs if simulate_ticks
    ever reorders its draws."""
    from synthetic.profile_store import DEFAULT_PROFILE_DIR, load_profile
    from synthetic.simulate import simulate_ticks

    profile = load_profile(DEFAULT_PROFILE_DIR / "eurusd.pkl")
    _, ticks = simulate_ticks(profile, 12, seed=7, ticks_per_candle=3,
                              start_price=profile.last_close)
    expected = ticks.groupby("bar_id")["state"].first().tolist()

    _, _, _, open_states, _ = _read_session(
        "asset=eurusd&seed=7&n_candles=12&ticks_per_candle=3&ticks_per_sec=0")
    assert open_states == expected


def test_tick_stream_carries_state_for_heatmap():
    from synthetic.simulate import simulate_ticks

    df = pd.read_parquet(
        __import__("synthetic.data", fromlist=["smg_data"]).DEFAULT_PROCESSED_DIR / "xauusd_1d.parquet"
    )
    from synthetic.calibration import calibrate

    profile = calibrate(df, asset="xauusd", seed=0)
    _, ticks = simulate_ticks(profile, 50, seed=1, ticks_per_candle=8)

    assert "state" in ticks.columns
    assert set(ticks["state"].unique()) <= set(range(profile.hmm.n_states))
    # one regime per bar: the heatmap column is constant within a bar
    for bar_id in ticks["bar_id"].unique():
        assert ticks.loc[ticks.bar_id == bar_id, "state"].nunique() == 1
    # every tick annotated: bar count x ticks_per_candle rows
    assert len(ticks) == 50 * 8
