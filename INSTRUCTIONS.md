# Synthetic Market Generator — Instructions

An empirically calibrated Monte Carlo engine that learns how real markets behave — then generates statistically faithful alternative realities to stress-test trading strategies.

**The pipeline:** download real market data → scrub data errors → fit the statistical fingerprint (Student-t innovations, GARCH(1,1) volatility, 2-state HMM regimes) → simulate synthetic OHLCV bars tick-by-tick → validate against the real statistics → serve over a REST/WebSocket API → render in a proprietary canvas charting engine (no chart libraries).

---

## 1. Repository layout

```
├── engine/                  # the quant core — a pure, installable Python package
│   ├── synthetic/
│   │   ├── data.py          # OHLCV contract, yfinance download, cleaning/validation
│   │   ├── registry.py      # per-asset config (ticker, calendar, scrub defaults)
│   │   ├── calibration.py   # scrub, MLE (GBM/t/JD), GARCH-t, HMM, AssetProfile
│   │   ├── volume.py        # per-state log-volume model
│   │   ├── simulate.py      # regime-switching GARCH-t sim → OHLCV bars, validation suite
│   │   └── profile_store.py # versioned profile persistence
│   └── tests/               # 113 pytest — the executable spec
├── api/app.py               # FastAPI: profiles, simulate, export, /ws tick stream
├── web/                     # React + TS + Vite + Zustand, custom Canvas 2D engine
├── data/
│   ├── raw/                 # untouched Yahoo downloads (gitignored, re-downloadable)
│   ├── processed/           # validated Parquet (committed)
│   └── profiles/            # calibrated AssetProfiles, *.pkl (committed)
├── notebooks/
│   ├── 01_calibration.ipynb # the Phase 1–2 report: hygiene → MLE → regimes → gates
│   └── 02_era_weighting.ipynb # era-window study: when history stops describing the asset
├── PROGRESS.md              # the memory bridge: decisions, findings, limitations, next steps
└── PROJECT_BRIEF.md         # the original spec
```

**How to read this repo:** `PROGRESS.md` first (what was decided and why, what's parked and its trigger), then the notebooks (the science), then `engine/tests/` (the contract).

---

## 2. Setup

Requires Python ≥ 3.11 and Node ≥ 18.

```bash
# Python environment
python3 -m venv .venv
source .venv/bin/activate
pip install -e "engine[dev,api]"

# Frontend dependencies
cd web && npm install && cd ..
```

## 3. Data

Processed Parquet and calibrated profiles are committed — the app runs out of the box. To re-download or add data:

```bash
python -m synthetic.data download                # all registered assets (2 intervals each)
python -m synthetic.data download --only btcusd  # one asset
```

## 4. Run

**Single port (production-style):** the API serves the built frontend.

```bash
cd web && npm run build && cd ..                 # (re)build the frontend bundle
.venv/bin/uvicorn app:app --app-dir api --port 8000
# → http://localhost:8000
```

**Dev mode (hot reload):** terminal 1: `uvicorn app:app --app-dir api --port 8000`; terminal 2: `cd web && npm run dev` → http://localhost:5173 (proxies API + WS).

> **Runbook rule:** before (re)starting the server, `pkill -f "uvicorn app:app"` — a stale server on port 8000 serves old code silently. Run it on its own: `pkill -f "uvicorn app:app" && uvicorn app:app ...` matches *its own* command line through `-f` and kills the shell issuing it. The toolbar shows the engine version (`engine 0.2.0`); if it looks wrong, hard-refresh (Ctrl+Shift+R) — HTML is served `no-store`.

### Using the app

- **Toolbar:** mode switch (`single | distribution | evidence`), asset picker, `↻ new seed` (re-simulates, keeps your zoom), `reset view`, `▶ live`.
- **Evidence mode:** the validation suite on screen — a headline tally ("11/13 statistics
  reproduced"), the real return density against the band of densities 200 simulated
  histories produce, then every statistic grouped by family with its real value, the
  model's 5–95% band, and where the real value falls on that band. The first request for
  an uncached profile computes in the background (~30 s) and the report is then cached
  next to the profile, keyed on a hash of the pickle — so a recalibration invalidates it
  and a clone does not.
- **Live mode:** streams a 400-candle session tick-by-tick over WebSocket — watch each candle form, with the regime heatmap (blue = calm, red = turbulent) behind the price pane. The view auto-follows the tail until you pan.
- **Chart:** wheel = zoom at cursor · drag = pan · double-click / `R` = reset · `←`/`→` pan · `+`/`−` zoom. Crosshair shows price tag + hovered OHLC; the right strip is the volume-at-price profile.
- **Regime heatmap:** behind every run, not just live ones — a solid ribbon along the top
  of the price pane plus a background wash, coloured by the HMM state that generated each
  bar, with a legend giving the run's realized regime mix. `/simulate` returns the latent
  state path, so the regimes you see are the ones that made the candles.
- **Workflow (Phase 5):** pick profile → resolution/duration → scenario preset → export **LEAN / CSV / Parquet**. Runs are seeded and anchored to the asset's last real close.
- **Benchmark:** append `?bench=1` for a scripted pan/zoom loop with an fps readout (the 60 fps / 1,000+ candle gate).

### API quick reference

| Endpoint | Purpose |
|---|---|
| `GET /health` | status + engine version + registered assets |
| `GET /profiles` | all profiles (incl. era artifacts like `btcusd_last3y`) |
| `GET /profiles/{asset}` | full parameter summary (state vols, dwells, drift ± SE, scrub audit) |
| `GET /profiles/{asset}/validation` | the validation suite: every statistic vs the simulated 5–95% band, plus the return-density band. Cached per profile (`?refresh=true` recomputes) |
| `GET /validation/families` | the metric families and the question each answers |
| `POST /simulate` | seeded OHLCV JSON (`n_candles` ≤ 10k, optional `start_price`, `drift_annual`, scenario) |
| `POST /profiles/{asset}/recalibrate?max_history_years=N` | re-fit empirically; windowed fits save as `{asset}_last{N}y.pkl` |
| `POST /export` | LEAN / CSV / Parquet download of a run spec |
| `WS /ws/simulate?...` | live tick stream: `bar_open → ticks → bar_close → done` |
| `GET /workflow` | resolutions, formats, scenario presets |

Set `SMG_CORS_ORIGINS` to override the default `*` CORS policy.

## 5. Tests

```bash
cd engine && pytest -q        # 138 tests: engine math, estimators, API, WS, validation
cd web && npm test            # 71 vitest: viewport/layout/live-reducer/store/evidence
npm run build                 # tsc typecheck + production bundle
```

Notebooks execute headlessly and top-to-bottom: `jupyter nbconvert --to notebook --execute --inplace notebooks/01_calibration.ipynb`.

## 6. Methodology in one paragraph

Real Yahoo data is haunted (corrupt prints, gap returns), so a robust scrubber removes what no per-bar rule can catch, keeping adjudicated real crashes. Parameters come from maximum likelihood with observed-information standard errors — not hand-tuning. Daily jump intensity is provably unidentifiable at daily frequency (flat likelihood ridge), so the engine uses GARCH(1,1) + Student-t innovations, with HMM regime switching per state; jump-like behavior enters via scenarios. Every gate is parametric-bootstrap honest: simulation bands, never point comparisons. Known limits and their remedies are on record in `PROGRESS.md` (gold wants a 3-state crash component; BTC-era effects; intrabar microstructure).

Two explainers worth reading: [`docs/WHY_SO_VOLATILE.md`](docs/WHY_SO_VOLATILE.md) — why honest simulations look extreme and why seeds differ (dispersion math, random regime paths, zero identifiable drift); and [`docs/ON_CONVERGENCE_AND_PREDICTION.md`](docs/ON_CONVERGENCE_AND_PREDICTION.md) — an analysis of the convergence-as-prediction theory: what converges, why price prophecy fails, and why the converged ensemble is a *risk oracle* — the gem that is actually there.

## 7. Adding a new market

1. Add one `AssetSpec` entry in `engine/synthetic/registry.py` (Yahoo ticker, calendar, periods/year, scrub defaults).
2. `python -m synthetic.data download --only newkey`.
3. `curl -X POST "localhost:8000/profiles/newkey/recalibrate"` (optionally `?max_history_years=N` for maturing assets).

That's the whole plug-and-play path — everything downstream (calibration, simulation, validation, serving, UI) is asset-agnostic. BTC-USD was added this way as the generality proof.
