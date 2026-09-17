"""OHLCV data contract and pipeline: fetch -> raw CSV -> clean -> validate -> Parquet.

Canonical schema (the contract everything obeys):
    timestamp (UTC, ns), open, high, low, close, volume
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

OHLCV_COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]
PRICE_COLUMNS = ["open", "high", "low", "close"]

# GC=F (COMEX gold futures) is the standard liquid XAU/USD proxy on Yahoo; spot
# XAUUSD=X exists but has patchier history.
ASSETS = {"eurusd": "EURUSD=X", "xauusd": "GC=F"}
INTERVAL_PERIODS = {"1h": "720d", "1d": "max"}  # Yahoo caps 1h history at ~730 days

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RAW_DIR = REPO_ROOT / "data" / "raw"
DEFAULT_PROCESSED_DIR = REPO_ROOT / "data" / "processed"


class OHLCVValidationError(ValueError):
    """Raised when a frame violates the canonical OHLCV contract."""


def clean_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize raw yfinance output to the canonical schema (structural fixes only)."""
    df = df.copy()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.droplevel(-1)
    df.columns = [str(c).lower() for c in df.columns]
    if "timestamp" not in df.columns:
        df = df.reset_index()
        df.columns = [str(c).lower() for c in df.columns]
        df = df.rename(columns={"datetime": "timestamp", "date": "timestamp"})

    missing = set(OHLCV_COLUMNS) - set(df.columns)
    if missing:
        raise ValueError(f"missing required columns: {sorted(missing)}")

    df = df.assign(timestamp=pd.to_datetime(df["timestamp"], utc=True).dt.tz_localize(None))
    df = df[OHLCV_COLUMNS].astype({c: "float64" for c in PRICE_COLUMNS + ["volume"]})
    df = df.dropna(subset=OHLCV_COLUMNS)

    # Bars whose prices violate exchange-record arithmetic (e.g. Yahoo's 2005-era
    # FX daily bars) are unrecoverable records; drop them loudly, never silently.
    malformed = ~(
        (df["high"] >= df[["open", "close"]].max(axis=1))
        & (df["low"] <= df[["open", "close"]].min(axis=1))
        & (df["high"] >= df["low"])
        & df[PRICE_COLUMNS].gt(0).all(axis=1)
    )
    if malformed.any():
        logger.warning(
            "dropped %d malformed bar(s), first at %s",
            int(malformed.sum()),
            df.loc[malformed, "timestamp"].iloc[0],
        )
    df = df[~malformed]

    df = df.sort_values("timestamp", kind="mergesort")
    df = df.drop_duplicates(subset="timestamp", keep="last")
    return df[OHLCV_COLUMNS].reset_index(drop=True)


def validate_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    """Enforce the OHLCV contract; return the frame or raise with the violation."""
    if list(df.columns) != OHLCV_COLUMNS:
        raise OHLCVValidationError(
            f"schema mismatch: expected {OHLCV_COLUMNS}, got {list(df.columns)}"
        )
    if not pd.api.types.is_datetime64_any_dtype(df["timestamp"]) or df["timestamp"].dt.tz is not None:
        raise OHLCVValidationError("timestamp must be timezone-naive datetime64 (UTC wall time)")

    nan_cols = [c for c in OHLCV_COLUMNS if df[c].isna().any()]
    if nan_cols:
        raise OHLCVValidationError(f"NaN values in columns: {nan_cols}")

    def _fail(mask: pd.Series, msg: str) -> None:
        n = int(mask.sum())
        if n:
            first = df.loc[mask, "timestamp"].iloc[0]
            raise OHLCVValidationError(f"{msg}: {n} row(s), first at {first}")

    _fail(df["high"] < df[["open", "close"]].max(axis=1), "high < max(open, close)")
    _fail(df["low"] > df[["open", "close"]].min(axis=1), "low > min(open, close)")
    _fail(df["high"] < df["low"], "high < low")
    _fail((df[PRICE_COLUMNS] <= 0).any(axis=1), "non-positive price")
    _fail(df["volume"] < 0, "negative volume")

    if not df["timestamp"].is_monotonic_increasing or not df["timestamp"].is_unique:
        raise OHLCVValidationError("timestamps must be strictly increasing (unsorted or duplicates)")
    return df


def fetch_raw(ticker: str, interval: str, dest_dir: Path) -> Path:
    """Download one asset/interval from yfinance; save the untouched raw CSV."""
    import yfinance as yf  # lazy: data source stays out of the core import path

    raw = yf.download(
        ticker,
        period=INTERVAL_PERIODS[interval],
        interval=interval,
        auto_adjust=False,
        progress=False,
    )
    if raw is None or raw.empty:
        raise RuntimeError(f"yfinance returned no data for {ticker} ({interval})")
    if isinstance(raw.columns, pd.MultiIndex):
        raw = raw.copy()
        raw.columns = raw.columns.droplevel(-1)  # drop yfinance's repeated-ticker level
    dest_dir.mkdir(parents=True, exist_ok=True)
    path = dest_dir / f"{ticker}_{interval}.csv"
    raw.to_csv(path, index_label="timestamp")
    return path


def build_processed(raw_path: Path, dest_path: Path) -> pd.DataFrame:
    """Raw CSV -> clean -> validate -> Parquet; return the validated frame."""
    df = validate_ohlcv(clean_ohlcv(pd.read_csv(raw_path)))
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(dest_path, index=False)
    return df


def _summary(df: pd.DataFrame) -> str:
    return f"{len(df):>7} rows | {df['timestamp'].iloc[0]} -> {df['timestamp'].iloc[-1]}"


def download(raw_dir: Path = DEFAULT_RAW_DIR, processed_dir: Path = DEFAULT_PROCESSED_DIR) -> None:
    for name, ticker in ASSETS.items():
        for interval in INTERVAL_PERIODS:
            raw_path = fetch_raw(ticker, interval, raw_dir)
            dest = processed_dir / f"{name}_{interval}.parquet"
            df = build_processed(raw_path, dest)
            print(f"[ok] {name:6s} {interval}: {_summary(df)}  ->  {dest.name}")


def rebuild(processed_dir: Path = DEFAULT_PROCESSED_DIR, raw_dir: Path = DEFAULT_RAW_DIR) -> None:
    for name, ticker in ASSETS.items():
        for interval in INTERVAL_PERIODS:
            raw_path = raw_dir / f"{ticker}_{interval}.csv"
            dest = processed_dir / f"{name}_{interval}.parquet"
            df = build_processed(raw_path, dest)
            print(f"[ok] {name:6s} {interval}: {_summary(df)}  ->  {dest.name}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        nargs="?",
        choices=["download", "rebuild"],
        default="download",
        help="download: fetch from Yahoo + build Parquet; rebuild: Parquet from existing raw CSVs",
    )
    args = parser.parse_args()
    rebuild() if args.command == "rebuild" else download()


if __name__ == "__main__":
    main()
