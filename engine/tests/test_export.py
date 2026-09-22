"""Phase 5: export pipeline + scenario presets.

The gate is "an exported dataset ingests cleanly into an external backtesting
framework". Running LEAN itself needs Docker, so the tests here hold the line the
format actually depends on: LEAN's documented column layout per security type, its
path convention, its timestamp format, and a read-back that parses the bytes the
way LEAN's reader would.
"""

from __future__ import annotations

import io
import json
import re
import zipfile

import numpy as np
import pandas as pd
import pytest

from synthetic.data import OHLCV_COLUMNS, validate_ohlcv
from synthetic.export import (
    LEAN_TIME_FORMAT,
    QUOTE_COLUMNS,
    RESOLUTIONS,
    TRADE_COLUMNS,
    ExportManifest,
    aggregate_bars,
    export_bytes,
    lean_relative_path,
    lean_rows,
    read_lean_zip,
)
from synthetic.profile_store import DEFAULT_PROFILE_DIR, load_profile
from synthetic.registry import REGISTRY
from synthetic.simulate import (
    SCENARIO_PRESETS,
    Scenario,
    build_scenario,
    simulate,
)

LEAN_TIME_RE = re.compile(r"^\d{8} \d{2}:\d{2}$")


@pytest.fixture(scope="module")
def profiles():
    return {k: load_profile(DEFAULT_PROFILE_DIR / f"{k}.pkl") for k in ("eurusd", "btcusd")}


@pytest.fixture(scope="module")
def bars(profiles):
    p = profiles["eurusd"]
    return simulate(p, 260, seed=5, start_price=p.last_close)


# ---------------------------------------------------------------------------
# Resolution aggregation
# ---------------------------------------------------------------------------


def test_aggregation_preserves_the_ohlcv_contract(bars):
    for resolution in RESOLUTIONS:
        out = aggregate_bars(bars, resolution)
        validate_ohlcv(out)  # raises if the contract broke
        assert list(out.columns) == OHLCV_COLUMNS
        assert out["timestamp"].is_monotonic_increasing


def test_aggregation_is_a_true_aggregation(bars):
    """Weekly bars must open where the week opened and close where it closed —
    aggregation may not invent prices the daily path never traded."""
    weekly = aggregate_bars(bars, "weekly")
    assert 40 <= len(weekly) <= 60  # ~52 weeks from 260 business days
    assert weekly["open"].iloc[0] == bars["open"].iloc[0]
    assert weekly["close"].iloc[-1] == bars["close"].iloc[-1]
    assert weekly["high"].max() == pytest.approx(bars["high"].max())
    assert weekly["low"].min() == pytest.approx(bars["low"].min())
    assert weekly["volume"].sum() == pytest.approx(bars["volume"].sum())


def test_daily_resolution_is_a_passthrough(bars):
    pd.testing.assert_frame_equal(aggregate_bars(bars, "daily"), bars)


def test_unknown_resolution_rejected(bars):
    with pytest.raises(ValueError, match="unknown resolution"):
        aggregate_bars(bars, "hourly")  # not calibrated: must not silently succeed


# ---------------------------------------------------------------------------
# LEAN format conformance
# ---------------------------------------------------------------------------


def test_quote_assets_emit_leans_eleven_quote_columns(bars):
    """LEAN stores forex/CFD daily as QUOTE bars, not OHLCV."""
    rows = lean_rows(bars, REGISTRY["eurusd"])
    assert list(rows.columns) == QUOTE_COLUMNS
    assert len(rows.columns) == 11
    assert (rows["last_bid_size"] == 0).all()  # no tape in spot FX
    assert (rows["last_ask_size"] == 0).all()


def test_crypto_emits_lean_trade_bars(bars, profiles):
    p = profiles["btcusd"]
    btc = simulate(p, 60, seed=2, start_price=p.last_close)
    rows = lean_rows(btc, REGISTRY["btcusd"])
    assert list(rows.columns) == TRADE_COLUMNS
    assert (rows["volume"] > 0).any()  # crypto has a real tape


def test_synthesized_spread_straddles_the_simulated_mid(bars):
    spec = REGISTRY["eurusd"]
    rows = lean_rows(bars, spec)
    half = spec.lean_spread_bps / 10_000.0 / 2.0
    for col in ("open", "high", "low", "close"):
        mid = bars[col].to_numpy()
        assert np.allclose(rows[f"bid_{col}"], mid * (1 - half))
        assert np.allclose(rows[f"ask_{col}"], mid * (1 + half))
        assert (rows[f"ask_{col}"] > rows[f"bid_{col}"]).all()


def test_lean_timestamps_match_the_documented_format(bars):
    rows = lean_rows(bars, REGISTRY["eurusd"])
    assert all(LEAN_TIME_RE.match(t) for t in rows["time"])
    # round-trips through LEAN's own format string
    parsed = pd.to_datetime(rows["time"], format=LEAN_TIME_FORMAT)
    assert list(parsed) == list(pd.DatetimeIndex(bars["timestamp"]))


@pytest.mark.parametrize(
    "asset, expected",
    [("eurusd", "forex/oanda/daily/eurusd.zip"),
     ("xauusd", "cfd/oanda/daily/xauusd.zip"),
     ("btcusd", "crypto/bitfinex/daily/btcusd_trade.zip")],
)
def test_lean_path_layout(asset, expected):
    assert lean_relative_path(REGISTRY[asset], "daily").as_posix() == expected


def test_lean_bundle_round_trips(bars):
    """A format you cannot read back is a format you cannot trust."""
    manifest = ExportManifest(asset="eurusd", seed=5, n_candles=len(bars),
                              resolution="daily", scenario=None, start_price=1.1)
    payload, name = export_bytes(bars, "eurusd", "lean", "daily", manifest)
    assert name.endswith("_lean.zip")

    frame, meta = read_lean_zip(payload)
    assert list(frame.columns) == QUOTE_COLUMNS
    assert len(frame) == len(bars)
    assert meta["seed"] == 5
    assert meta["rows"] == len(bars)
    assert len(meta["payload_sha256"]) == 64
    # the bid/ask envelope survived serialization
    assert np.allclose(frame["bid_close"].to_numpy(),
                       bars["close"].to_numpy() * (1 - 0.9 / 10_000 / 2), rtol=1e-9)


def test_lean_csv_has_no_header_and_no_index(bars):
    payload, _ = export_bytes(bars, "eurusd", "lean")
    with zipfile.ZipFile(io.BytesIO(payload)) as zf:
        inner = next(n for n in zf.namelist() if n.endswith(".zip"))
        with zipfile.ZipFile(io.BytesIO(zf.read(inner))) as z2:
            first = z2.read(z2.namelist()[0]).decode().splitlines()[0]
    assert LEAN_TIME_RE.match(first.split(",")[0])  # data starts on line 1
    assert len(first.split(",")) == 11


def test_lean_bundle_carries_install_instructions(bars):
    payload, _ = export_bytes(bars, "eurusd", "lean")
    with zipfile.ZipFile(io.BytesIO(payload)) as zf:
        readme = zf.read("README.txt").decode()
    assert "forex/oanda/daily/eurusd.zip" in readme
    assert "quote bars" in readme


# ---------------------------------------------------------------------------
# CSV / Parquet
# ---------------------------------------------------------------------------


def test_csv_export_keeps_the_canonical_schema(bars):
    payload, name = export_bytes(bars, "eurusd", "csv")
    frame = pd.read_csv(io.BytesIO(payload))
    assert list(frame.columns) == OHLCV_COLUMNS
    assert name == "eurusd_daily.csv"


def test_parquet_export_round_trips(bars):
    payload, name = export_bytes(bars, "eurusd", "parquet")
    frame = pd.read_parquet(io.BytesIO(payload))
    assert name == "eurusd_daily.parquet"
    pd.testing.assert_frame_equal(frame, bars)


def test_unknown_format_and_asset_rejected(bars):
    with pytest.raises(ValueError, match="unknown format"):
        export_bytes(bars, "eurusd", "hdf5")
    with pytest.raises(ValueError, match="not in the registry"):
        export_bytes(bars, "dogecoin", "csv")


# ---------------------------------------------------------------------------
# Scenario presets + jump injection
# ---------------------------------------------------------------------------


def test_every_preset_builds_and_simulates(profiles):
    p = profiles["eurusd"]
    for preset in SCENARIO_PRESETS:
        scenario = build_scenario(preset.key, 200, p)
        out = simulate(p, 200, scenario=scenario, seed=1, start_price=p.last_close)
        validate_ohlcv(out)
        assert len(out) == 200


def test_unknown_preset_rejected(profiles):
    with pytest.raises(ValueError, match="unknown scenario preset"):
        build_scenario("armageddon", 100, profiles["eurusd"])


def test_flash_crash_produces_a_crash_scaled_to_the_asset(profiles):
    p = profiles["eurusd"]
    scenario = build_scenario("flash_crash", 200, p)
    assert scenario.jump_at == 120
    # 8 turbulent-state sigmas, taken from the profile rather than hand-set
    assert scenario.jump_return == pytest.approx(-8.0 * p.hmm.state_vol[-1])

    out = simulate(p, 200, scenario=scenario, seed=3, start_price=p.last_close)
    r = np.log(out["close"]).diff().to_numpy()
    assert r[scenario.jump_at] < 0.5 * scenario.jump_return  # the crash landed
    assert r[scenario.jump_at] == min(r[1:])  # and it is the worst day of the path


def test_jump_propagates_into_the_garch_aftershock(profiles):
    """The shock must enter the variance recursion: a crash followed by a calm
    market would be the model contradicting its own volatility clustering."""
    p = profiles["eurusd"]
    at = 120
    crash = Scenario(force_state=1, n_candles=0, jump_at=at, jump_return=-0.10)
    quiet = simulate(p, 200, scenario=None, seed=3, start_price=p.last_close)
    shocked = simulate(p, 200, scenario=crash, seed=3, start_price=p.last_close)

    def vol_after(bars):
        r = np.log(bars["close"]).diff().to_numpy()
        return np.std(r[at + 1: at + 21])

    assert vol_after(shocked) > vol_after(quiet)


def test_scenario_without_a_jump_is_unchanged(profiles):
    """Regime forcing alone must not perturb the return path — otherwise every
    pre-Phase-5 result silently moves."""
    p = profiles["eurusd"]
    plain = simulate(p, 100, scenario=Scenario(1, 30), seed=7, start_price=p.last_close)
    explicit = simulate(p, 100, scenario=Scenario(1, 30, jump_at=None, jump_return=0.0),
                        seed=7, start_price=p.last_close)
    pd.testing.assert_frame_equal(plain, explicit)


def test_jump_outside_the_path_is_rejected(profiles):
    with pytest.raises(ValueError, match="outside a"):
        simulate(profiles["eurusd"], 50, scenario=Scenario(1, 0, jump_at=90, jump_return=-0.1),
                 seed=1)


def test_runs_end_in_the_past_so_backtesters_can_replay_them(profiles):
    """Found by actually running LEAN: timestamps used to start at a hardcoded
    2026-01-01, so any run longer than the elapsed year landed in the future and
    the backtester silently replayed only the part before today — a 500-bar export
    ran 227 bars and skipped the injected crash entirely."""
    p = profiles["eurusd"]
    today = pd.Timestamp.now("UTC").normalize().tz_localize(None)
    bars = simulate(p, 2000, seed=1, start_price=p.last_close)
    assert pd.DatetimeIndex(bars["timestamp"]).max() <= today
    assert pd.DatetimeIndex(bars["timestamp"]).min() < today  # a real span, not all one day


def test_start_date_projects_the_run_forward(profiles):
    """The chart anchors forward from the last real bar so a synthetic path is not
    drawn over dates that actually happened and misread as history."""
    p = profiles["eurusd"]
    bars = simulate(p, 120, seed=2, start_date="2030-01-02", start_price=p.last_close)
    ts = pd.DatetimeIndex(bars["timestamp"])
    assert ts.min() == pd.Timestamp("2030-01-02")
    assert ts.is_monotonic_increasing
    assert bars["open"].iloc[0] == pytest.approx(p.last_close)


def test_start_date_wins_over_end_date(profiles):
    p = profiles["eurusd"]
    bars = simulate(p, 50, seed=2, start_date="2030-01-02", end_date="2020-01-01",
                    start_price=p.last_close)
    assert pd.DatetimeIndex(bars["timestamp"]).min() == pd.Timestamp("2030-01-02")


def test_profiles_carry_the_last_real_date(profiles):
    for p in profiles.values():
        assert p.last_date is not None
        assert pd.Timestamp(p.last_date) <= pd.Timestamp.now("UTC").normalize().tz_localize(None)


def test_end_date_is_explicit_when_given(profiles):
    p = profiles["eurusd"]
    bars = simulate(p, 100, seed=1, end_date="2020-06-30", start_price=p.last_close)
    assert pd.DatetimeIndex(bars["timestamp"]).max() == pd.Timestamp("2020-06-30")
    assert len(bars) == 100


def test_exports_are_reproducible_from_the_manifest(profiles):
    """Same seed + scenario + profile => byte-identical payload. This is the whole
    point of the manifest."""
    p = profiles["eurusd"]
    scenario = build_scenario("flash_crash", 120, p)
    runs = [
        export_bytes(simulate(p, 120, scenario=scenario, seed=42, start_price=p.last_close),
                     "eurusd", "csv")[0]
        for _ in range(2)
    ]
    assert runs[0] == runs[1]
