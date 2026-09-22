"""FastAPI serving layer: calibration profiles + simulation endpoints.

Run:  uvicorn app:app --app-dir api --reload
The app serves the persisted profiles (data/profiles/*.pkl) and exposes the
simulator. Phase 4 adds the WebSocket tick stream on top of synthetic.simulate.
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from synthetic.calibration import AssetProfile, calibrate
from synthetic.data import DEFAULT_PROCESSED_DIR
from synthetic.profile_store import DEFAULT_PROFILE_DIR, load_profile, save_profile
from synthetic.registry import REGISTRY
from synthetic.simulate import Scenario, simulate

REPO_ROOT = Path(__file__).resolve().parents[1]

app = FastAPI(title="Synthetic Market Generator", version="0.2.0")

MAX_SIM_CANDLES = 10_000  # JSON responses stay snappy; large exports are Phase 5

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
        "annual_vol": _f(p.student_t.params["sigma"]),
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
    }


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
def health() -> dict:
    return {"status": "ok", "profiles_dir": str(DEFAULT_PROFILE_DIR),
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
    n_candles: int = Field(default=60, ge=1)


class SimulateIn(BaseModel):
    asset: str
    n_candles: int = Field(default=500, ge=1, le=MAX_SIM_CANDLES)
    seed: int = 0
    ticks_per_candle: int = Field(default=24, ge=1, le=200)
    scenario: ScenarioIn | None = None


@app.post("/simulate")
def simulate_endpoint(body: SimulateIn) -> dict:
    profile = _get_profile(body.asset)
    scenario = (Scenario(force_state=body.scenario.force_state,
                         n_candles=body.scenario.n_candles)
                if body.scenario else None)
    bars = simulate(profile, body.n_candles, scenario=scenario, seed=body.seed,
                    ticks_per_candle=body.ticks_per_candle)
    records = bars.copy()
    records["timestamp"] = pd.to_datetime(records["timestamp"]).dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"asset": body.asset, "seed": body.seed, "n_candles": len(records),
            "bars": records.to_dict(orient="records")}


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


# Single-port serving: the built frontend, served by the same process (mounted
# last so the API routes above keep precedence).
_DIST = REPO_ROOT / "web" / "dist"
if _DIST.exists():
    app.mount("/", StaticFiles(directory=_DIST, html=True), name="web")
