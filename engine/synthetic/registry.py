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
    # LEAN export (Phase 5). LEAN's daily/hour layout is per security type:
    # forex and cfd carry QUOTE bars (bid/ask OHLC + sizes), crypto carries TRADE
    # bars (OHLCV). `lean_spread_bps` synthesizes the bid/ask envelope around our
    # simulated mid price — a documented assumption, not a calibrated quantity;
    # values are typical retail spreads for the instrument.
    lean_security_type: str = "cfd"  # "forex" | "cfd" | "crypto"
    lean_market: str = "oanda"
    lean_spread_bps: float = 1.5


REGISTRY: dict[str, AssetSpec] = {
    "eurusd": AssetSpec("eurusd", "EURUSD=X", "EUR/USD", has_real_volume=False,
                        lean_security_type="forex", lean_market="oanda",
                        lean_spread_bps=0.9),  # ~0.9 pip on a ~1.10 quote
    # GC=F (COMEX gold futures) is the standard liquid XAU/USD proxy on Yahoo; spot
    # XAUUSD=X exists but has patchier history.
    "xauusd": AssetSpec("xauusd", "GC=F", "XAU/USD (gold futures)",
                        lean_security_type="cfd", lean_market="oanda",
                        lean_spread_bps=1.5),  # ~$0.35 on a ~$2400 quote
    "btcusd": AssetSpec("btcusd", "BTC-USD", "Bitcoin", calendar="crypto", ppy_daily=365,
                        ppy_hourly=24 * 365, has_real_volume=True,
                        lean_security_type="crypto", lean_market="bitfinex"),
}
