"""Asset registry: per-asset market configuration.

Adding a new market = adding one entry here (ticker + calendar + scrub defaults).
Everything downstream (download, calibration, simulation, validation) reads the spec.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AssetSpec:
    key: str
    yahoo_ticker: str
    display_name: str
    calendar: str = "bday"  # "bday" (FX/metals/equities) | "crypto" (24/7)
    ppy_daily: int = 252
    ppy_hourly: int = 24 * 252
    scrub_max_gap_days: float = 6.0  # daily bars: beyond a long weekend = data hole
    hourly_max_gap_days: float = 3.0  # hourly bars: beyond a weekend = hole
    has_real_volume: bool = True


REGISTRY: dict[str, AssetSpec] = {
    "eurusd": AssetSpec("eurusd", "EURUSD=X", "EUR/USD", has_real_volume=False),
    # GC=F (COMEX gold futures) is the standard liquid XAU/USD proxy on Yahoo; spot
    # XAUUSD=X exists but has patchier history.
    "xauusd": AssetSpec("xauusd", "GC=F", "XAU/USD (gold futures)"),
    "btcusd": AssetSpec("btcusd", "BTC-USD", "Bitcoin", calendar="crypto", ppy_daily=365,
                        ppy_hourly=24 * 365, has_real_volume=True),
}
