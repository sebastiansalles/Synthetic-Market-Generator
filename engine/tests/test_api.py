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


def test_simulate_projects_forward_but_export_stays_backtestable():
    """Two anchors on purpose: the chart must not overlay synthetic prices on dates
    that really happened, and the export must not land in the future where a
    backtester would silently refuse to replay it."""
    summary = client.get("/profiles/eurusd").json()
    last_real = pd.Timestamp(summary["last_date"])

    bars = client.post("/simulate", json={"asset": "eurusd", "n_candles": 100}).json()["bars"]
    assert pd.Timestamp(bars[0]["timestamp"][:10]) > last_real

    csv = client.post("/export", json={"asset": "eurusd", "n_candles": 100, "fmt": "csv"})
    rows = csv.content.decode().splitlines()[1:]
    today = pd.Timestamp.now("UTC").normalize().tz_localize(None)
    assert pd.Timestamp(rows[-1].split(",")[0][:10]) <= today


def test_reported_annual_vol_is_realized_not_the_student_t_scale():
    """With a near-infinite-variance t fit (BTC df ~2.3) the scale is not volatility:
    it read 103% against 66% realized."""
    body = client.get("/profiles/btcusd").json()
    assert 0.5 < body["annual_vol"] < 0.85
    assert body["t_scale_ann"] > body["annual_vol"]


def test_workflow_options_drive_the_wizard():
    body = client.get("/workflow").json()
    assert "daily" in body["resolutions"] and "weekly" in body["resolutions"]
    assert body["formats"] == ["lean", "csv", "parquet"]
    keys = [p["key"] for p in body["presets"]]
    assert "standard" in keys and "flash_crash" in keys
    assert all(p["description"] for p in body["presets"])


def test_export_returns_a_lean_bundle_as_a_download():
    import io
    import zipfile

    resp = client.post("/export", json={"asset": "eurusd", "n_candles": 120, "seed": 4,
                                        "fmt": "lean", "resolution": "daily"})
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"
    assert "eurusd_daily_lean.zip" in resp.headers["content-disposition"]
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        names = zf.namelist()
    assert "forex/oanda/daily/eurusd.zip" in names
    assert "manifest.json" in names


def test_export_manifest_records_the_scenario_that_produced_it():
    import io
    import json
    import zipfile

    resp = client.post("/export", json={"asset": "eurusd", "n_candles": 200, "seed": 9,
                                        "fmt": "lean", "preset": "flash_crash"})
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        manifest = json.loads(zf.read("manifest.json"))
    assert manifest["seed"] == 9
    assert manifest["scenario"]["jump_at"] == 120
    assert manifest["scenario"]["jump_return"] < 0
    assert manifest["rows"] == 200


def test_export_csv_and_parquet_formats():
    csv = client.post("/export", json={"asset": "eurusd", "n_candles": 50, "fmt": "csv"})
    assert csv.status_code == 200 and csv.headers["content-type"].startswith("text/csv")
    assert csv.content.splitlines()[0].decode().startswith("timestamp,open")

    pq = client.post("/export", json={"asset": "eurusd", "n_candles": 50, "fmt": "parquet"})
    assert pq.status_code == 200
    assert pq.content[:4] == b"PAR1"  # parquet magic


def test_export_aggregates_to_the_requested_resolution():
    daily = client.post("/export", json={"asset": "eurusd", "n_candles": 260, "fmt": "csv"})
    weekly = client.post("/export", json={"asset": "eurusd", "n_candles": 260, "fmt": "csv",
                                          "resolution": "weekly"})
    assert len(weekly.content.splitlines()) < len(daily.content.splitlines())


@pytest.mark.parametrize(
    "payload, expected",
    [({"asset": "eurusd", "fmt": "hdf5"}, "unknown format"),
     ({"asset": "eurusd", "resolution": "hourly"}, "unknown resolution"),
     ({"asset": "eurusd", "preset": "armageddon"}, "unknown scenario preset")],
)
def test_export_rejects_unknown_options(payload, expected):
    resp = client.post("/export", json={"n_candles": 50, **payload})
    assert resp.status_code == 422
    assert expected in resp.json()["detail"]


def test_simulate_accepts_a_preset_and_scales_it_to_the_asset():
    """The same preset must mean the same severity, not the same percentage."""
    import numpy as np

    out = {}
    for asset in ("eurusd", "xauusd"):
        bars = client.post("/simulate", json={"asset": asset, "n_candles": 200, "seed": 3,
                                              "preset": "flash_crash"}).json()["bars"]
        closes = np.array([b["close"] for b in bars])
        out[asset] = np.log(closes[1:] / closes[:-1]).min()
    assert out["eurusd"] < -0.02 and out["xauusd"] < -0.02  # both crashed
    assert out["xauusd"] < out["eurusd"]  # gold is the more volatile asset


def test_simulate_rejects_a_jump_outside_the_path():
    resp = client.post("/simulate", json={"asset": "eurusd", "n_candles": 50,
                                          "scenario": {"n_candles": 0, "jump_at": 400,
                                                       "jump_return": -0.1}})
    assert resp.status_code == 422
    assert "outside a" in resp.json()["detail"]


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
    _, ticks, _ = simulate_ticks(profile, 12, seed=7, ticks_per_candle=3,
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
    _, ticks, _ = simulate_ticks(profile, 50, seed=1, ticks_per_candle=8)

    assert "state" in ticks.columns
    assert set(ticks["state"].unique()) <= set(range(profile.hmm.n_states))
    # one regime per bar: the heatmap column is constant within a bar
    for bar_id in ticks["bar_id"].unique():
        assert ticks.loc[ticks.bar_id == bar_id, "state"].nunique() == 1
    # every tick annotated: bar count x ticks_per_candle rows
    assert len(ticks) == 50 * 8


def test_recalibrate_with_history_window_saves_named_artifact(monkeypatch, tmp_path):
    import app as app_module

    seen = {}

    def spy_calibrate(df, asset, **kw):
        seen["max_history_years"] = kw.get("max_history_years")
        from synthetic.calibration import AssetProfile, FitResult, fit_hmm_regimes, log_returns, scrub_returns

        r, rep = scrub_returns(log_returns(df))
        hmm = fit_hmm_regimes(r, n_states=2, seed=0)
        zero = FitResult("gbm", {"mu": 0.0, "sigma": 0.1}, 0.0, 2, {}, True)
        tfit = FitResult("student_t", {"df": 5.0, "mu": 0.0, "sigma": 0.1}, 0.0, 3, {}, True)
        g = {"mu": 0, "omega": 1e-5, "alpha": 0.05, "beta": 0.9, "persistence": 0.95,
             "nu": 8.0, "uncond_var": r.var(), "n_days": len(r)}
        return AssetProfile(asset=asset, periods_per_year=365, n_bars=len(r),
                            targets={"mean": 0.0, "vol": r.std(), "kurt": r.kurt()},
                            gbm=zero, student_t=tfit, merton_jd=zero,
                            garch={"persistence": 0.95}, state_garch=[dict(g), dict(g)],
                            hmm=hmm, state_innov_df=np.array([np.inf, np.inf]),
                            scrub=rep, volume=None, last_close=1.0,
                            history_years=kw.get("max_history_years"))

    monkeypatch.setattr(app_module, "calibrate", spy_calibrate)
    monkeypatch.setattr(app_module, "DEFAULT_PROFILE_DIR", tmp_path)
    app_module._profile_cache.clear()

    resp = client.post("/profiles/btcusd/recalibrate?max_history_years=4")
    assert resp.status_code == 202
    assert seen["max_history_years"] == 4.0
    assert (tmp_path / "btcusd_last4y.pkl").exists()
    body = resp.json()["profile"]
    assert body["history_years"] == 4.0
    assert body["periods_per_year"] == 365  # registry spec of the BASE asset, not the suffix


def test_ensemble_returns_path_matrix_deterministically():
    req = {"asset": "eurusd", "n_candles": 60, "n_paths": 25, "seed": 4}
    a = client.post("/ensemble", json=req).json()
    b = client.post("/ensemble", json=req).json()
    assert a["n_paths"] == 25 and a["n_candles"] == 60
    assert len(a["paths"]) == 25 and all(len(p) == 60 for p in a["paths"])
    assert a["paths"] == b["paths"]  # seeded reproducibility
    firsts = [p[0] for p in a["paths"]]
    anchor = client.get("/profiles/eurusd").json()["last_close"]
    assert all(abs(f / anchor - 1) < 0.02 for f in firsts)  # every path opens at the anchor


def test_ensemble_caps_and_shock_metadata():
    assert client.post("/ensemble", json={"asset": "eurusd", "n_paths": 401}).status_code == 422
    body = client.post("/ensemble", json={
        "asset": "eurusd", "n_candles": 100, "n_paths": 10, "preset": "flash_crash",
    }).json()
    assert body["shock_at"] == 60
    assert body["preset"] == "flash_crash"


def test_websocket_streams_ensemble_paths_in_order():
    with client.websocket_connect(
        "/ws/ensemble?asset=eurusd&seed=2&n_candles=30&n_paths=6"
    ) as ws:
        n_start = n_paths = done = 0
        indices = []
        meta = {}
        while True:
            msg = ws.receive_json()
            if msg["type"] == "start":
                n_start += 1
                meta = msg
            elif msg["type"] == "path":
                n_paths += 1
                indices.append(msg["index"])
                assert len(msg["path"]) == 30
            elif msg["type"] == "done":
                done += 1
                break
            else:
                raise AssertionError(f"unexpected: {msg}")
    assert n_start == 1 and done == 1 and n_paths == 6
    assert indices == [0, 1, 2, 3, 4, 5]
    assert meta["n_paths"] == 6 and meta["n_candles"] == 30
    assert abs(meta["start_price"] - client.get("/profiles/eurusd").json()["last_close"]) < 1e-6


# ---------------------------------------------------------------------------
# /simulate carries the regime path: the heatmap used to exist only in live mode
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("resolution,expected", [("daily", 300), ("weekly", 60), ("monthly", 15)])
def test_simulate_returns_a_regime_state_per_returned_bar(resolution, expected):
    """One state per BAR AS RETURNED — not per simulated daily bar. The chart indexes
    the heatmap by displayed-bar position, so a weekly run with 300 daily states would
    colour the first 60 weeks with the first 60 days' regimes."""
    body = client.post("/simulate", json={"asset": "eurusd", "n_candles": 300, "seed": 3,
                                          "resolution": resolution}).json()
    assert body["n_candles"] == expected
    assert len(body["states"]) == len(body["bars"]) == expected
    assert body["n_states"] == 2
    assert set(body["states"]) <= {0, 1}


def test_simulate_states_are_the_path_that_made_the_bars():
    """The endpoint must not re-simulate the regimes: it hands back the latent path
    from the same call that produced the candles."""
    from synthetic.profile_store import DEFAULT_PROFILE_DIR, load_profile
    from synthetic.simulate import simulate as engine_simulate

    profile = load_profile(DEFAULT_PROFILE_DIR / "eurusd.pkl")
    body = client.post("/simulate", json={"asset": "eurusd", "n_candles": 120, "seed": 42,
                                          "start_price": 1.1}).json()
    _, latent = engine_simulate(profile, 120, seed=42, start_price=1.1,
                                start_date=body["bars"][0]["timestamp"][:10], debug=True)
    assert body["states"] == [int(s) for s in latent["states"]]


def test_simulate_states_reflect_a_forced_scenario():
    """A turbulent-open preset must show as turbulent bars at the head of the heatmap,
    otherwise the chart would contradict the scenario the user picked."""
    body = client.post("/simulate", json={"asset": "eurusd", "n_candles": 400, "seed": 8,
                                          "preset": "turbulent"}).json()
    assert all(s == 1 for s in body["states"][:60])
    assert 0 in body["states"][60:]  # the chain resumes rather than staying forced


# ---------------------------------------------------------------------------
# Validation evidence: Phase 2's gate, served and cached
# ---------------------------------------------------------------------------


def _await_validation(asset: str, tries: int = 120, pause: float = 1.0) -> dict:
    """Poll until the background worker finishes. The suite is genuinely slow, so the
    endpoint is asynchronous by design and the test waits like the UI does."""
    import time as _time

    for _ in range(tries):
        body = client.get(f"/profiles/{asset}/validation").json()
        if body["status"] != "computing":
            return body
        _time.sleep(pause)
    raise AssertionError(f"validation for {asset} never finished")


def test_validation_report_is_asynchronous_then_cached():
    first = client.get("/profiles/eurusd/validation").json()
    assert first["status"] in {"computing", "ready"}  # may already be cached on disk
    body = _await_validation("eurusd")
    assert body["status"] == "ready"

    assert body["n_sims"] == 200
    assert body["n_bars"] > 5000
    assert body["summary"]["total"] == len(body["metrics"])
    assert body["summary"]["passed"] == sum(m["inside"] for m in body["metrics"])

    # a second call must be served from cache, not recomputed
    again = client.get("/profiles/eurusd/validation").json()
    assert again["status"] == "ready"
    assert again["cache"]["computed_at"] == body["cache"]["computed_at"]


def test_validation_metrics_carry_everything_the_ui_renders():
    body = _await_validation("eurusd")
    for m in body["metrics"]:
        assert set(m) == {"key", "label", "family", "unit", "real", "sim_median",
                          "lo", "hi", "inside"}
        assert m["unit"] in {"ret", "pct", "num"}
        assert m["lo"] <= m["sim_median"] <= m["hi"]
        # the verdict is derivable from the band, so the two can never disagree
        assert m["inside"] == (m["lo"] <= m["real"] <= m["hi"])
        assert m["label"] and m["family"]

    families = {f["key"] for f in body["families"]}
    assert families == {m["family"] for m in body["metrics"]}
    for f in body["families"]:
        rows = [m for m in body["metrics"] if m["family"] == f["key"]]
        assert f["total"] == len(rows)
        assert f["passed"] == sum(m["inside"] for m in rows)
        assert f["blurb"]


def test_validation_density_band_is_ordered_and_plottable():
    body = _await_validation("eurusd")
    d = body["density"]
    n = len(d["centers"])
    assert n == 64
    assert all(len(d[k]) == n for k in ("real", "lo", "median", "hi"))
    assert d["real_sd"] > 0
    # a band whose floor crosses its ceiling would render as an inverted ribbon
    for i in range(n):
        assert d["lo"][i] <= d["median"][i] <= d["hi"][i]
        assert d["real"][i] >= 0
    assert d["centers"][0] < 0 < d["centers"][-1]
    assert max(d["real"]) > 0


def test_validation_cache_is_keyed_on_the_profile_contents():
    """Evidence must never outlive the parameters it was measured on, and must survive
    a clone: the key is a hash of the profile pickle, not its mtime."""
    import hashlib
    import json

    from synthetic.profile_store import DEFAULT_PROFILE_DIR

    _await_validation("eurusd")
    cache_path = DEFAULT_PROFILE_DIR / "eurusd.validation.json"
    cached = json.loads(cache_path.read_text())
    expected = hashlib.sha256((DEFAULT_PROFILE_DIR / "eurusd.pkl").read_bytes()).hexdigest()[:16]
    assert cached["cache"]["profile_digest"] == expected

    # touching the profile must NOT invalidate a valid report (what mtime keying did)
    (DEFAULT_PROFILE_DIR / "eurusd.pkl").touch()
    assert client.get("/profiles/eurusd/validation").json()["status"] == "ready"

    # but different parameters must
    stale = {**cached, "cache": {**cached["cache"], "profile_digest": "deadbeefdeadbeef"}}
    cache_path.write_text(json.dumps(stale))
    try:
        assert client.get("/profiles/eurusd/validation").json()["status"] == "computing"
    finally:
        _await_validation("eurusd")  # leave the cache warm for the other tests


def test_validation_404s_for_an_unknown_profile():
    assert client.get("/profiles/nosuchasset/validation").status_code == 404


def test_validation_families_endpoint_describes_the_metric_groups():
    body = client.get("/validation/families").json()
    keys = [f["key"] for f in body["families"]]
    assert keys[0] == "returns"
    assert "clustering" in keys and "candles" in keys
    assert all(f["label"] and f["blurb"] for f in body["families"])


def test_conditional_endpoint_reports_today_against_the_unconditional_level():
    body = client.get("/profiles/eurusd/conditional").json()
    assert body["asset"] == "eurusd"
    assert body["n_bars"] > 1000
    assert len(body["state_probs"]) == 2
    assert abs(sum(body["state_probs"]) - 1.0) < 1e-9
    assert abs(sum(body["stationary"]) - 1.0) < 1e-9
    assert body["ann_vol"] > 0 and body["uncond_ann_vol"] > 0
    assert body["vol_ratio"] == pytest.approx(body["ann_vol"] / body["uncond_ann_vol"])
    assert body["periods_per_year"] == 252
    assert body["last_date"] is not None


def test_conditional_endpoint_is_era_aware():
    """An era profile must be filtered on its own window — the same rule the
    validation suite follows. Different window, different conditional state."""
    full = client.get("/profiles/btcusd/conditional").json()
    era = client.get("/profiles/btcusd_last3y/conditional").json()
    assert era["n_bars"] < full["n_bars"]
    assert era["uncond_ann_vol"] != full["uncond_ann_vol"]


def test_conditional_endpoint_404s_for_unknown_profile():
    assert client.get("/profiles/nope/conditional").status_code == 404


def test_conditioned_ensemble_differs_from_the_unconditional_one():
    """Same seed, same everything else: only the opening regime and variance change.
    If these matched, the conditioning would not be reaching the generator."""
    req = {"asset": "eurusd", "n_candles": 60, "n_paths": 30, "seed": 9}
    plain = client.post("/ensemble", json=req).json()
    cond = client.post("/ensemble", json={**req, "condition": True}).json()
    assert plain["conditioned"] is False
    assert cond["conditioned"] is True
    assert cond["paths"] != plain["paths"]
    assert cond["paths"] == client.post("/ensemble", json={**req, "condition": True}).json()["paths"]


def test_conditioned_ensemble_opens_at_the_conditional_dispersion():
    """EUR/USD's conditional vol is well below its unconditional level, so a
    conditioned ensemble must fan out more slowly over the first bars. Measured on
    the cross-sectional spread of the first 5 bars, where the seeding still bites."""
    req = {"asset": "eurusd", "n_candles": 120, "n_paths": 200, "seed": 3}
    plain = np.array(client.post("/ensemble", json=req).json()["paths"])
    cond = np.array(client.post("/ensemble", json={**req, "condition": True}).json()["paths"])
    spread = lambda m: float(np.std(np.log(m[:, :5] / m[:, :1]), axis=0).mean())
    assert spread(cond) < spread(plain)


def test_websocket_ensemble_reports_conditioning():
    with client.websocket_connect(
        "/ws/ensemble?asset=eurusd&seed=2&n_candles=20&n_paths=3&condition=true"
    ) as ws:
        msg = ws.receive_json()
        assert msg["type"] == "start" and msg["conditioned"] is True
