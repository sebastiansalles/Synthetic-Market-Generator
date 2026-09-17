import numpy as np
import pandas as pd
import pytest

from synthetic.data import (
    OHLCV_COLUMNS,
    OHLCVValidationError,
    build_processed,
    clean_ohlcv,
    validate_ohlcv,
)


def make_frame(**overrides) -> pd.DataFrame:
    rows = {
        "timestamp": pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-03"]),
        "open": [100.0, 101.0, 102.0],
        "high": [101.0, 102.0, 103.0],
        "low": [99.0, 100.0, 101.0],
        "close": [100.5, 101.5, 102.5],
        "volume": [10.0, 20.0, 30.0],
    }
    rows.update(overrides)
    return pd.DataFrame(rows)


VIOLATIONS = [
    {"high": [99.0, 102.0, 103.0]},  # high < open
    {"low": [100.6, 100.0, 101.0]},  # low > close
    {"high": [98.0, 102.0, 103.0]},  # high < low
    {"open": [0.0, 101.0, 102.0]},  # non-positive price
    {"close": [-1.0, 101.5, 102.5]},  # non-positive price
    {"volume": [10.0, -5.0, 30.0]},  # negative volume
    {"close": [np.nan, 101.5, 102.5]},  # NaN
    {"timestamp": pd.to_datetime(["2024-01-01", "2024-01-01", "2024-01-03"])},  # duplicate
    {"timestamp": pd.to_datetime(["2024-01-03", "2024-01-01", "2024-01-02"])},  # unsorted
]


@pytest.mark.parametrize("overrides", VIOLATIONS)
def test_validate_rejects_violations(overrides):
    with pytest.raises(OHLCVValidationError):
        validate_ohlcv(make_frame(**overrides))


def test_validate_rejects_wrong_schema():
    bad = make_frame().rename(columns={"close": "Close"})
    with pytest.raises(OHLCVValidationError):
        validate_ohlcv(bad)


def test_validate_rejects_tz_aware_timestamps():
    df = make_frame()
    df["timestamp"] = df["timestamp"].dt.tz_localize("UTC")
    with pytest.raises(OHLCVValidationError):
        validate_ohlcv(df)


def test_validate_accepts_valid_frame():
    df = make_frame()
    out = validate_ohlcv(df)
    assert out is df  # returns the validated frame


def test_clean_normalizes_yfinance_output():
    idx = pd.to_datetime(
        ["2024-01-03 09:30", "2024-01-02 09:30", "2024-01-02 09:30"]
    ).tz_localize("America/New_York")
    data = {
        ("Open", "GC=F"): [103.0, 101.0, 101.5],
        ("High", "GC=F"): [104.0, 102.0, 102.0],
        ("Low", "GC=F"): [102.0, 100.0, 100.5],
        ("Close", "GC=F"): [103.5, 101.5, 101.8],
        ("Adj Close", "GC=F"): [103.5, 101.5, 101.8],
        ("Volume", "GC=F"): [30, 10, 20],
    }
    raw = pd.DataFrame(data, index=pd.Index(idx, name="Datetime"))

    out = clean_ohlcv(raw)

    assert list(out.columns) == OHLCV_COLUMNS
    assert out["timestamp"].dt.tz is None
    assert list(out["timestamp"]) == list(pd.to_datetime(["2024-01-02 14:30", "2024-01-03 14:30"]))
    assert out["close"].tolist() == [101.8, 103.5]  # sorted; duplicate keeps the last row
    validate_ohlcv(out)  # cleaned output always passes validation


def test_build_processed_roundtrip(tmp_path):
    raw_path = tmp_path / "raw.csv"
    make_frame().rename(columns=str.capitalize).to_csv(raw_path, index=False)
    out_path = tmp_path / "out.parquet"

    df = build_processed(raw_path, out_path)

    assert len(df) == 3
    validate_ohlcv(pd.read_parquet(out_path))


def test_clean_drops_malformed_bars():
    out = clean_ohlcv(make_frame(high=[99.0, 102.0, 103.0]))  # row 0: high < open

    assert len(out) == 2
    assert out["timestamp"].min() == pd.Timestamp("2024-01-02")
    validate_ohlcv(out)
