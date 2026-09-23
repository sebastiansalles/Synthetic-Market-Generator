"""FastAPI serving layer: calibration profiles + simulation endpoints.

Run:  uvicorn app:app --app-dir api --reload
The app serves the persisted profiles (data/profiles/*.pkl) and exposes the
simulator. Phase 4 adds the WebSocket tick stream on top of synthetic.simulate.
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException, Query, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from synthetic.calibration import AssetProfile, calibrate, calibrated_drift
from synthetic.data import DEFAULT_PROCESSED_DIR
from synthetic.export import RESOLUTIONS, ExportManifest, aggregate_bars, export_bytes
from synthetic.profile_store import DEFAULT_PROFILE_DIR, load_profile, save_profile
from synthetic.registry import REGISTRY
from synthetic.simulate import (
    SCENARIO_PRESETS,
    Scenario,
    build_scenario,
    simulate,
    simulate_ticks,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

app = FastAPI(title="Synthetic Market Generator", version="0.2.0")

MAX_SIM_CANDLES = 10_000  # JSON responses stay snappy; large exports are Phase 5
MAX_TICKS_PER_CANDLE = 200

_CORS_ORIGINS = [o.strip() for o in os.getenv("SMG_CORS_ORIGINS", "*").split(",")]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def engine_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Engine failures (corrupt pickle, bad parquet, …) become clean JSON 500s."""
    if isinstance(exc, HTTPException):
        raise exc  # fall through to the default handler
    return JSONResponse(status_code=500, content={"error": f"{type(exc).__name__}: {exc}"})


@app.middleware("http")
async def no_store_html(request: Request, call_next):
    """Browsers must never cache the entry point (stale-bundle confusion)."""
    response = await call_next(request)
    if response.headers.get("content-type", "").startswith("text/html"):
        response.headers["Cache-Control"] = "no-store"
    return response


# ---------------------------------------------------------------------------
# Profile cache: unpickling hits disk only when the file changes (mtime-keyed);
# recalibrate invalidates naturally by rewriting the file.
# ---------------------------------------------------------------------------

_profile_cache: dict[str, tuple[float, AssetProfile]] = {}


def _get_profile(asset: str) -> AssetProfile:
    path = DEFAULT_PROFILE_DIR / f"{asset}.pkl"
    if not path.exists():
        raise HTTPException(404, f"no persisted profile for '{asset}'")
    mtime = path.stat().st_mtime
    cached = _profile_cache.get(asset)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    profile = load_profile(path)
    _profile_cache[asset] = (mtime, profile)
    return profile


# ---------------------------------------------------------------------------
# Serializers (numpy/scipy objects are not JSON-safe)
# ---------------------------------------------------------------------------

def _f(x) -> float:
    return float(x) if np.isfinite(x) else 0.0


def profile_summary(p: AssetProfile) -> dict:
    h = p.hmm
    return {
        "asset": p.asset,
        "display_name": (REGISTRY.get(p.asset).display_name if p.asset in REGISTRY else p.asset),
        "periods_per_year": p.periods_per_year,
        "n_bars": p.n_bars,
        "targets": {k: _f(v) for k, v in p.targets.items()},
        # realized volatility of the clean returns, not the Student-t scale: with a
        # near-infinite-variance fit (BTC df=2.27) the scale is not the asset's
        # volatility and reported 103% against 66% realized
        "annual_vol": _f(p.targets["vol"] * np.sqrt(p.periods_per_year)),
        "t_scale_ann": _f(p.student_t.params["sigma"]),
        "t_df": _f(p.student_t.params["df"]),
        "state_vol_ann": [_f(v) for v in h.state_vol_ann(p.periods_per_year)],
        "state_mean_bp_day": [_f(m * 1e4) for m in h.state_mean],
        "expected_dwell_days": [_f(d) for d in h.expected_dwell],
        "transition": [[_f(x) for x in row] for row in h.transition],
        "garch_persistence": _f(p.garch["persistence"]),
        "state_garch_persistence": [_f(g["persistence"]) for g in p.state_garch],
        "innovation_df": [None if not np.isfinite(d) else _f(d) for d in p.state_innov_df],
        "scrub": {"n_total": p.scrub.n_total, "n_spike_revert": p.scrub.n_spike_revert,
                  "n_gap": p.scrub.n_gap, "n_kept_spikes": p.scrub.n_kept_spikes},
        "volume_mode": "modeled" if (p.volume and p.volume.enabled) else "zeros (no centralized tape)",
        "last_close": _f(getattr(p, "last_close", 100.0)),
        "last_date": getattr(p, "last_date", None),
        "drift_ann": _f(calibrated_drift(p) * p.periods_per_year),
        # se(mu) ~ sigma/sqrt(T): drift is the least identified parameter in the model
        "drift_ann_se": _f(p.targets["vol"] * np.sqrt(p.periods_per_year)
                           / np.sqrt(p.n_bars / p.periods_per_year)),
    }


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
def health() -> dict:
    return {"status": "ok", "version": app.version,
            "profiles_dir": str(DEFAULT_PROFILE_DIR),
            "registered_assets": sorted(REGISTRY)}


@app.get("/assets")
def assets() -> dict:
    return {k: {"ticker": s.yahoo_ticker, "display_name": s.display_name,
                "calendar": s.calendar, "ppy_daily": s.ppy_daily,
                "has_real_volume": s.has_real_volume}
            for k, s in REGISTRY.items()}


@app.get("/profiles")
def list_profiles() -> dict:
    out = {}
    for path in sorted(DEFAULT_PROFILE_DIR.glob("*.pkl")):
        try:
            out[path.stem] = profile_summary(_get_profile(path.stem))
        except Exception as exc:  # corrupt profile: report, don't 500 the list
            out[path.stem] = {"error": str(exc)}
    return out


@app.get("/profiles/{asset}")
def get_profile(asset: str) -> dict:
    return profile_summary(_get_profile(asset))


class ScenarioIn(BaseModel):
    force_state: int = Field(default=1, ge=0)
    n_candles: int = Field(default=60, ge=0)  # 0 = force nothing (jump-only scenarios)
    jump_at: int | None = None
    jump_return: float = 0.0


class SimulateIn(BaseModel):
    asset: str
    n_candles: int = Field(default=500, ge=1, le=MAX_SIM_CANDLES)
    seed: int = 0
    ticks_per_candle: int = Field(default=24, ge=1, le=MAX_TICKS_PER_CANDLE)
    start_price: float | None = Field(default=None)  # default: the asset's last real close
    scenario: ScenarioIn | None = None
    preset: str | None = None  # takes precedence: scaled to the asset's own volatility
    resolution: str = "daily"  # the chart shows exactly what /export would write
    end_date: str | None = None
    start_date: str | None = None  # default: the bar after the profile's last real one
    drift_annual: float | None = None  # None = calibrated; 0 neutralizes the trend


def _resolve_scenario(body: SimulateIn | ExportIn, profile: AssetProfile) -> Scenario | None:
    """A preset wins over explicit fields — it is the asset-aware form of the same thing."""
    if body.preset:
        try:
            return build_scenario(body.preset, body.n_candles, profile)
        except ValueError as exc:
            raise HTTPException(422, str(exc))
    if body.scenario is None:
        return None
    return Scenario(**body.scenario.model_dump())


def _forward_start(profile: AssetProfile) -> str | None:
    """The bar after the asset's last real one. Charting a run over dates that really
    happened invites it to be read as history; a projection forward from the last real
    close is what the simulator actually claims to produce."""
    last = getattr(profile, "last_date", None)
    return str((pd.Timestamp(last) + pd.Timedelta(days=1)).date()) if last else None


@app.get("/workflow")
def workflow_options() -> dict:
    """Everything the three-step workflow needs to render itself in one round trip."""
    return {
        "resolutions": sorted(RESOLUTIONS),
        "formats": ["lean", "csv", "parquet"],
        "presets": [{"key": p.key, "label": p.label, "description": p.description}
                    for p in SCENARIO_PRESETS],
        "max_candles": MAX_SIM_CANDLES,
    }


@app.post("/simulate")
def simulate_endpoint(body: SimulateIn) -> dict:
    profile = _get_profile(body.asset)
    start_price = body.start_price if body.start_price is not None else profile.last_close
    scenario = _resolve_scenario(body, profile)
    try:
        # the chart projects forward; /export stays anchored in the past so the data
        # remains replayable by a backtester
        start_date = body.start_date
        if start_date is None and body.end_date is None:
            start_date = _forward_start(profile)
        bars = simulate(profile, body.n_candles, scenario=scenario, seed=body.seed,
                        ticks_per_candle=body.ticks_per_candle, start_price=start_price,
                        end_date=body.end_date, start_date=start_date,
                        drift_annual=body.drift_annual)
        bars = aggregate_bars(bars, body.resolution)
    except ValueError as exc:  # e.g. a jump placed outside the path, or a bad resolution
        raise HTTPException(422, str(exc))
    records = bars.copy()
    records["timestamp"] = pd.to_datetime(records["timestamp"]).dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"asset": body.asset, "seed": body.seed, "n_candles": len(records),
            "resolution": body.resolution, "bars": records.to_dict(orient="records")}


class ExportIn(BaseModel):
    asset: str
    n_candles: int = Field(default=500, ge=1, le=MAX_SIM_CANDLES)  # daily bars simulated
    seed: int = 0
    resolution: str = "daily"
    fmt: str = "lean"
    start_price: float | None = None
    scenario: ScenarioIn | None = None
    preset: str | None = None
    end_date: str | None = None
    drift_annual: float | None = None


_MEDIA_TYPES = {"csv": "text/csv", "parquet": "application/vnd.apache.parquet",
                "lean": "application/zip"}


@app.post("/export")
def export_endpoint(body: ExportIn) -> Response:
    """Phase 5 gate: a simulated run as a file an external backtester can ingest.

    The engine simulates daily bars; coarser resolutions are aggregations of that
    same path (one code path), and the returned bundle carries the manifest that
    makes the run reproducible."""
    profile = _get_profile(body.asset)
    start_price = body.start_price if body.start_price is not None else profile.last_close
    scenario = _resolve_scenario(body, profile)
    try:
        bars = simulate(profile, body.n_candles, scenario=scenario, seed=body.seed,
                        start_price=start_price, end_date=body.end_date,
                        drift_annual=body.drift_annual)
        bars = aggregate_bars(bars, body.resolution)
        manifest = ExportManifest(
            asset=body.asset, seed=body.seed, n_candles=body.n_candles,
            resolution=body.resolution, start_price=float(start_price),
            scenario=(asdict(scenario) if scenario else None),
            drift_annual=body.drift_annual,
        )
        payload, filename = export_bytes(bars, body.asset, body.fmt, body.resolution, manifest)
    except ValueError as exc:  # unknown format/resolution/preset, or a bad jump index
        raise HTTPException(422, str(exc))
    return Response(
        content=payload,
        media_type=_MEDIA_TYPES.get(body.fmt, "application/octet-stream"),
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/profiles/{asset}/recalibrate", status_code=202)
def recalibrate(asset: str, seed: int = Query(default=0)) -> dict:
    """Re-run the full empirical calibration from the processed Parquet and persist."""
    if asset not in REGISTRY:
        raise HTTPException(404, f"'{asset}' not in registry")
    parquet = DEFAULT_PROCESSED_DIR / f"{asset}_1d.parquet"
    if not parquet.exists():
        raise HTTPException(404, f"no processed data for '{asset}' — run: python -m synthetic.data download --only {asset}")
    df = pd.read_parquet(parquet)
    profile = calibrate(df, asset=asset, seed=seed)
    path = save_profile(profile, DEFAULT_PROFILE_DIR / f"{asset}.pkl")
    _profile_cache[asset] = (path.stat().st_mtime, profile)
    return {"status": "recalibrated", "profile": profile_summary(profile)}


# ---------------------------------------------------------------------------
# Phase 4: live tick stream (one code path: events come from simulate_ticks)
# ---------------------------------------------------------------------------


@app.websocket("/ws/simulate")
async def ws_simulate(
    ws: WebSocket,
    asset: str,
    seed: int = 0,
    n_candles: int = 300,
    ticks_per_candle: int = 24,
    ticks_per_sec: float = 250.0,
    force_state: int | None = None,
    force_n: int = 60,
) -> None:
    """Stream a full simulated session tick-by-tick: bar_open -> ticks -> bar_close
    per candle, closing with `done`. Generation runs off the event loop; pacing is
    deadline-based (0 ticks_per_sec = as fast as possible, for tests)."""
    await ws.accept()
    try:
        if not 1 <= n_candles <= MAX_SIM_CANDLES:
            raise ValueError(f"n_candles must be in [1, {MAX_SIM_CANDLES}]")
        if not 1 <= ticks_per_candle <= MAX_TICKS_PER_CANDLE:
            raise ValueError(f"ticks_per_candle must be in [1, {MAX_TICKS_PER_CANDLE}]")
        profile = _get_profile(asset)
        scenario = Scenario(force_state, force_n) if force_state is not None else None
        await ws.send_json({"type": "generating", "n_candles": n_candles})  # session builds off-loop
        loop = asyncio.get_running_loop()
        bars, ticks = await loop.run_in_executor(
            None,
            lambda: simulate_ticks(
                profile, n_candles, seed=seed, scenario=scenario,
                ticks_per_candle=ticks_per_candle, start_price=profile.last_close,
            ),
        )
        # Regimes come from the tick stream itself — re-deriving them from a second
        # RNG would silently desync the heatmap if simulate_ticks ever reorders draws.
        bar_ids = ticks["bar_id"].to_numpy()
        prices = ticks["price"].to_numpy(dtype=float)
        tick_states = ticks["state"].to_numpy()
        starts = np.searchsorted(bar_ids, np.arange(n_candles), side="left")
        ends = np.searchsorted(bar_ids, np.arange(n_candles), side="right")

        delay = 1.0 / ticks_per_sec if ticks_per_sec > 0 else 0.0
        deadline = time.perf_counter()
        cols = bars.to_dict(orient="records")
        for t in range(n_candles):
            bar = cols[t]
            state = int(tick_states[starts[t]])
            await ws.send_json({"type": "bar_open", "bar_id": t, "open": bar["open"],
                                "state": state})
            for price in prices[starts[t]:ends[t]]:
                await ws.send_json({"type": "tick", "bar_id": t, "price": float(price)})
                if delay:
                    # pace against a moving deadline so send time doesn't compound
                    deadline += delay
                    ahead = deadline - time.perf_counter()
                    if ahead > 0:
                        await asyncio.sleep(ahead)
                    else:
                        deadline = time.perf_counter()  # fell behind: don't bank a debt
            await ws.send_json({"type": "bar_close", "bar_id": t, "high": bar["high"],
                                "low": bar["low"], "close": bar["close"],
                                "volume": bar["volume"], "state": state})
        await ws.send_json({"type": "done", "n_candles": n_candles})
    except WebSocketDisconnect:
        return  # client navigated away mid-session: nothing to clean up
    except Exception as exc:
        try:
            await ws.send_json({"type": "error", "message": f"{type(exc).__name__}: {exc}"})
        except (WebSocketDisconnect, RuntimeError):
            return  # socket already gone: the original error has nowhere to go


# Single-port serving: the built frontend, served by the same process (mounted
# last so the API routes above keep precedence).
_DIST = REPO_ROOT / "web" / "dist"
if _DIST.exists():
    app.mount("/", StaticFiles(directory=_DIST, html=True), name="web")
