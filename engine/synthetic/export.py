"""Phase 5 export pipeline: simulated OHLCV -> formats external tools ingest.

Three targets:
    CSV      — the canonical OHLCV contract, ISO timestamps
    Parquet  — same frame, pyarrow
    LEAN     — QuantConnect's on-disk layout, which is NOT one schema but one
               per security type (see `lean_rows`)

Every export carries a manifest (`ExportManifest`) recording exactly what produced
it — engine version, profile, seed, scenario, resolution — plus a SHA-256 of the
payload. A run is reproducible from its manifest alone.
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from synthetic.data import OHLCV_COLUMNS, validate_ohlcv
from synthetic.registry import REGISTRY, AssetSpec

ENGINE_VERSION = "0.3.0"

# LEAN writes daily/hour timestamps as YYYYMMDD HH:mm.
LEAN_TIME_FORMAT = "%Y%m%d %H:%M"

# Which security types carry quote bars rather than trade bars. QuantConnect's
# forex and CFD daily/hour data is quote-only; crypto is trade data with volume.
_QUOTE_TYPES = frozenset({"forex", "cfd"})

TRADE_COLUMNS = ["time", "open", "high", "low", "close", "volume"]
QUOTE_COLUMNS = ["time", "bid_open", "bid_high", "bid_low", "bid_close", "last_bid_size",
                 "ask_open", "ask_high", "ask_low", "ask_close", "last_ask_size"]


# ---------------------------------------------------------------------------
# Resolution: the engine calibrates and simulates daily; coarser resolutions are
# honest aggregations of that path, never a re-parameterized model.
# ---------------------------------------------------------------------------

RESOLUTIONS: dict[str, str | None] = {"daily": None, "weekly": "W-FRI", "monthly": "ME"}


def aggregate_bars(bars: pd.DataFrame, resolution: str) -> pd.DataFrame:
    """Downsample validated OHLCV to a coarser resolution. Open = first, high = max,
    low = min, close = last, volume = sum — the only aggregation that preserves the
    OHLCV contract. `daily` returns the frame unchanged."""
    if resolution not in RESOLUTIONS:
        raise ValueError(f"unknown resolution '{resolution}' (have: {sorted(RESOLUTIONS)})")
    rule = RESOLUTIONS[resolution]
    if rule is None:
        return bars
    idx = pd.DatetimeIndex(bars["timestamp"])
    frame = bars.set_index(idx)
    # each bucket is labelled with the timestamp of its FIRST bar: a weekly bar opens
    # when the week opens, and a period-end label would stamp bars into the future
    out = (
        frame.resample(rule)
        .agg({"timestamp": "first", "open": "first", "high": "max", "low": "min",
              "close": "last", "volume": "sum"})
        .dropna(subset=["open"])
    )
    return validate_ohlcv(out.reset_index(drop=True)[OHLCV_COLUMNS])


# ---------------------------------------------------------------------------
# LEAN row construction
# ---------------------------------------------------------------------------


def lean_rows(bars: pd.DataFrame, spec: AssetSpec) -> pd.DataFrame:
    """Bars in LEAN's on-disk column layout for this security type.

    Trade types (crypto) map straight onto OHLCV. Quote types (forex, CFD) carry no
    trade prints at all, so the bid/ask envelope is synthesized from the simulated
    mid with the spec's typical spread, and the bid/ask *sizes* — which LEAN uses
    in place of volume — are 0: spot FX has no tape, and inventing depth would be
    fabricating data the simulator never modelled."""
    ts = pd.DatetimeIndex(bars["timestamp"]).strftime(LEAN_TIME_FORMAT)
    if spec.lean_security_type not in _QUOTE_TYPES:
        out = bars[["open", "high", "low", "close", "volume"]].copy()
        out.insert(0, "time", ts)
        out.columns = TRADE_COLUMNS
        return out

    half = spec.lean_spread_bps / 10_000.0 / 2.0
    out = pd.DataFrame({"time": ts})
    for side, sign in (("bid", -1.0), ("ask", 1.0)):
        for field_name in ("open", "high", "low", "close"):
            out[f"{side}_{field_name}"] = bars[field_name].to_numpy() * (1.0 + sign * half)
        out[f"last_{side}_size"] = 0.0
    return out[QUOTE_COLUMNS]


def lean_relative_path(spec: AssetSpec, resolution: str) -> Path:
    """LEAN's hour/daily layout: <securityType>/<market>/<resolution>/<ticker>.zip
    (crypto distinguishes trade from quote data with a `_trade` suffix)."""
    stem = spec.key if spec.lean_security_type in _QUOTE_TYPES else f"{spec.key}_trade"
    return Path(spec.lean_security_type) / spec.lean_market / resolution / f"{stem}.zip"


def lean_csv_bytes(bars: pd.DataFrame, spec: AssetSpec) -> bytes:
    """The CSV LEAN reads: no header, no index, one row per bar."""
    return lean_rows(bars, spec).to_csv(index=False, header=False, lineterminator="\n").encode()


# ---------------------------------------------------------------------------
# Manifest: a run is reproducible from this alone
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExportManifest:
    asset: str
    seed: int
    n_candles: int
    resolution: str
    scenario: dict | None
    start_price: float
    drift_annual: float | None = None  # None = the profile's calibrated drift
    engine_version: str = ENGINE_VERSION
    created_utc: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    payload_sha256: str = ""
    rows: int = 0

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True)


def _digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


# ---------------------------------------------------------------------------
# Writers
# ---------------------------------------------------------------------------


def export_bytes(bars: pd.DataFrame, asset: str, fmt: str, resolution: str = "daily",
                 manifest: ExportManifest | None = None) -> tuple[bytes, str]:
    """Serialize bars to `fmt` ("csv" | "parquet" | "lean"). Returns (payload, filename).

    The LEAN payload is a zip laid out exactly as LEAN expects on disk, so it can be
    unzipped straight into a Data folder; the manifest rides along beside the data."""
    spec = REGISTRY.get(asset)
    if spec is None:
        raise ValueError(f"'{asset}' is not in the registry")
    if resolution not in RESOLUTIONS:
        raise ValueError(f"unknown resolution '{resolution}' (have: {sorted(RESOLUTIONS)})")

    if fmt == "csv":
        frame = bars.copy()
        frame["timestamp"] = pd.DatetimeIndex(frame["timestamp"]).strftime("%Y-%m-%dT%H:%M:%SZ")
        return frame.to_csv(index=False, lineterminator="\n").encode(), f"{asset}_{resolution}.csv"

    if fmt == "parquet":
        buf = io.BytesIO()
        bars.to_parquet(buf, index=False)
        return buf.getvalue(), f"{asset}_{resolution}.parquet"

    if fmt != "lean":
        raise ValueError(f"unknown format '{fmt}' (have: csv, parquet, lean)")

    rel = lean_relative_path(spec, resolution)
    csv_bytes = lean_csv_bytes(bars, spec)
    inner_name = f"{rel.stem.removesuffix('_trade')}.csv"

    bundle = io.BytesIO()
    with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as zf:
        # the inner zip is what LEAN reads; nesting keeps LEAN's own path structure
        lean_zip = io.BytesIO()
        with zipfile.ZipFile(lean_zip, "w", zipfile.ZIP_DEFLATED) as inner:
            inner.writestr(inner_name, csv_bytes)
        zf.writestr(str(rel), lean_zip.getvalue())
        if manifest is not None:
            stamped = ExportManifest(**{**asdict(manifest),
                                        "payload_sha256": _digest(csv_bytes),
                                        "rows": len(bars)})
            zf.writestr("manifest.json", stamped.to_json())
        zf.writestr("README.txt", _lean_readme(spec, rel, resolution, len(bars)))
    return bundle.getvalue(), f"{asset}_{resolution}_lean.zip"


def _lean_readme(spec: AssetSpec, rel: Path, resolution: str, rows: int) -> str:
    kind = "quote" if spec.lean_security_type in _QUOTE_TYPES else "trade"
    cols = QUOTE_COLUMNS if kind == "quote" else TRADE_COLUMNS
    lines = [
        "Synthetic Market Generator — LEAN export",
        "",
        f"asset        : {spec.display_name} ({spec.key})",
        f"security type: {spec.lean_security_type}  [{kind} bars]",
        f"market       : {spec.lean_market}",
        f"resolution   : {resolution}",
        f"rows         : {rows}",
        f"columns      : {', '.join(cols)}",
        f"timestamps   : {LEAN_TIME_FORMAT} (UTC)",
        "",
        "Install: unzip this archive into your LEAN Data folder, preserving paths:",
        f"    <lean>/Data/{rel.as_posix()}",
        "",
    ]
    if kind == "quote":
        lines += [
            f"NOTE: LEAN stores {spec.lean_security_type} daily/hour data as quote bars.",
            f"The simulator produces mid prices; bid/ask are the mid +/- {spec.lean_spread_bps/2:g}",
            "bps (half of the instrument's typical spread). Bid/ask sizes are 0 —",
            "the simulator does not model depth, and fabricating it would be dishonest.",
            "",
        ]
    lines.append("manifest.json records the seed, scenario and engine version that")
    lines.append("produced this data — the run is reproducible from it alone.")
    return "\n".join(lines)


def write_export(bars: pd.DataFrame, asset: str, out_dir: Path, fmt: str,
                 resolution: str = "daily",
                 manifest: ExportManifest | None = None) -> Path:
    """Write an export to disk and return the path."""
    payload, name = export_bytes(bars, asset, fmt, resolution, manifest)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / name
    path.write_bytes(payload)
    return path


def read_lean_zip(payload: bytes) -> tuple[pd.DataFrame, dict]:
    """Read back an exported LEAN bundle the way LEAN would: locate the nested zip by
    its path, parse the headerless CSV, and return (rows, manifest). Used by the
    conformance tests — a format you cannot read back is a format you cannot trust."""
    with zipfile.ZipFile(io.BytesIO(payload)) as zf:
        inner_path = next(n for n in zf.namelist() if n.endswith(".zip"))
        manifest = json.loads(zf.read("manifest.json")) if "manifest.json" in zf.namelist() else {}
        with zipfile.ZipFile(io.BytesIO(zf.read(inner_path))) as inner:
            csv_name = inner.namelist()[0]
            frame = pd.read_csv(io.BytesIO(inner.read(csv_name)), header=None)
    frame.columns = TRADE_COLUMNS if len(frame.columns) == len(TRADE_COLUMNS) else QUOTE_COLUMNS
    return frame, manifest
