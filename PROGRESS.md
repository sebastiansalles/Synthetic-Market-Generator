# PROGRESS.md — Synthetic Market Generator

Cross-session memory: what's done, decisions made, current state. Append a dated entry at the end of each work session.

## Session 1 — 2026-09-17 — Phase 0 kickoff (scaffold)

**Done**
- Repo initialized; PROJECT_BRIEF.md committed
- `.venv` with full stack: numpy 2.5.3, scipy 1.18.1, pandas 3.0.5, pyarrow 25.0.1, hmmlearn 0.3.3, arch 8.0.0, pytest 9.1.1, yfinance 1.7.0, matplotlib, jupyter
- Monorepo skeleton: `engine/{synthetic,tests}`, `api/`, `web/`, `data/{raw,processed}`, `notebooks/`
- `engine/pyproject.toml` (setuptools); editable install verified; pytest collects from `engine/`

**Decisions**
- Engine is an installable package from day 1 — no sys.path hacks in tests/notebooks/API
- Single venv for the whole monorepo
- `data/raw/` gitignored (re-downloadable); `data/processed/` committed (small, validated)
- Test dirs without `__init__.py`
- Running on pandas 3.0.5 — CoW default, pyarrow-backed strings; watch `to_datetime` strictness

**Next**
- Assignment 2: yfinance download (EURUSD=X, GC=F), OHLCV schema + `validate_ohlcv`, `engine/synthetic/data.py` pipeline (raw → clean → validate → Parquet), tests

## Session 2 — 2026-09-17 — Phase 0 data pipeline (gate pending user review + commit)

**Done**
- Project moved to correctly-named dir `synthetic-market-generator/`; venv relocated with it (repaired 50 stale shebangs/entry points after the move)
- `engine/synthetic/data.py`: `clean_ohlcv` (schema normalize, UTC-naive ns, sort, dedupe, drop malformed), `validate_ohlcv` (contract enforcer, `OHLCVValidationError`), `fetch_raw` / `build_processed`, CLI (`python -m synthetic.data download|rebuild`)
- `engine/tests/test_validation.py` — 15 tests + scaffold smoke = 16 passing
- Downloaded & validated: eurusd_1h (17,001 rows), eurusd_1d (5,788), xauusd_1h (13,547), xauusd_1d (6,096) → `data/processed/*.parquet`

**Decisions**
- Gold = `GC=F` (COMEX futures), the liquid Yahoo XAU/USD proxy
- Malformed bars (high < max(o,c), etc.) are dropped in `clean_ohlcv` with a logged count — raw CSVs keep everything (128 bad EUR/USD daily bars from 2004+, 441 gold from 2001+); `validate_ohlcv` still guards the contract for any frame entering the engine
- `fetch_raw` flattens yfinance's repeated-ticker header level before saving raw CSVs (data values untouched)
- EUR/USD volume is identically 0 (spot FX has no tape) — schema keeps the column; validation uses `volume >= 0`

**Watch items**
- pandas 3.0.5 + yfinance 1.7.0: download returns MultiIndex columns; handled in clean/fetch
- `~/.venv` (home) is a separate, nearly-empty venv — not the project's; project venv lives at `.venv/`
- Stale dir `Projects/syntethic-market-generator/` (typo) still exists — contains only the two files since copied into the real repo; safe to delete
- Home dir `~` is a git repo ("Proyecto Enviado") — accidental `git add -A` there would commit secrets (`.git-credentials`, `.ssh/`); never run git from `~`

**Next**
- User: review + commit; delete stale typo dir
- Phase 0 gate sign-off → Phase 1 (MLE calibration + HMM regimes)

## Session 2 addendum — workflow decision

- Phase 1 exploration happens in `notebooks/01_calibration.ipynb` (jupyter from the venv); **lab → library rule**: gate-critical code graduates to `engine/synthetic/calibration.py` + pytest tests — the notebook uses the engine, never reimplements it
- Notebook hygiene: Restart & Run All before done; seed all randomness; conclusions in markdown (doubles as Phase 1 report)

## Session 3 — 2026-09-17 — Data hygiene + corrected Phase 1 targets

**Done**
- `engine/synthetic/calibration.py`: `log_returns`, `robust_z`, `scrub_returns` (corrupt-print pairs + gap returns dropped, un-reverted spikes kept & reported via `ScrubReport`), `gate_stats`, `acf_values` — 22 tests passing total
- Notebook rebuilt on engine functions, executed headlessly (nbconvert), 0 errors; conclusions rewritten from clean numbers

**Decisions**
- Scrub rules: robust z (median/MAD) > 10 + next-day reversion ≥ 50% → drop corrupt pair (both returns); returns across ≥ 6-day holes dropped; flagged non-reverting spikes kept, adjudicated by hand
- Hygiene is a calibration-time view; raw + processed untouched

**Phase 1 calibration targets (daily, clean)**
- EUR/USD: kurt 2.55, vol_ann 8.98%, ACF r² ≈ 0.144 lag1 decaying to 0.122 @ lag10; extremes ≤ 3.4%, no kept spikes
- XAU/USD: kurt 6.82, vol_ann 18.1%, ACF r² 0.075–0.124; 3 kept real extremes (2008-11-25, 2013-04-15 −9.8% largest gold crash on record, 2026-01-30 −11.4%)
- Ghosts removed: EUR/USD 5 corrupt pairs (all 2008) + 3 gap returns; gold 53 gap returns, 0 corrupt pairs

**Next**
- User: commit (Phase 0 + hygiene still uncommitted)
- Phase 1 MLE: GBM baseline → Student-t → Merton jump-diffusion (gold first); model selection via log-lik/AIC vs kurtosis reproduction

## Session 4 — 2026-09-17 — MLE baseline + fat tails + jumps (engine + notebook)

**Done**
- `engine/synthetic/calibration.py` gained: `FitResult` (params/loglik/AIC/SEs), `mle_gbm` (analytic), `mle_student_t`, `mle_merton_jd` (Poisson-mixture, multi-start L-BFGS-B), `_num_hessian` + `_se_from_hessian` (observed-information SEs), `implied_excess_kurtosis`. 27 tests passing incl. synthetic-data parameter recovery.
- Notebook sections 4–7: fit comparison table, density overlays, **kurtosis gate via parametric bootstrap** (B=2000), GARCH(1,1)-t decomposition (`arch`). Executed headlessly, 0 errors; md synced to executed numbers.

**Results (daily, clean)**
- GBM decisively rejected (Δloglik vs t: EUR +248, gold +531). Student-t wins on AIC both assets: df 4.49±0.09 / 3.39±0.06.
- Merton JD collapses to "many tiny jumps" (λ̂≈143–145/yr, tiny σ_J); se(σ) blows up ±0.0008→±0.197 (flat ridge). Daily data structurally cannot identify jumps — kept in engine for later hourly/tick work; jump behavior will come from HMM regimes + scenario injection.
- GARCH(1,1)-t: EUR persistence 0.998, nu 8.05±0.81 (vs iid df 4.49 — clustering explains most marginal kurt); gold persistence 0.995, nu 4.35±0.25 (genuinely fat innovations). Kurtosis gate ✓ via decomposition + bootstrap.
- Drift: gold +13.7–16.9%/yr ≈5σ (identified); EUR/USD μ unidentified (±~2pp — structural, se≈σ_ann/√T).

**Data sufficiency verdict** — sufficient: σ (±0.5pp), tail index, persistence, gold drift. Insufficient (structural, not fixable with more daily data): EUR drift, daily jump intensity. Engine choice: GARCH(1,1) + Student-t innovations for both assets.

**Next**
- User: commit (everything still uncommitted)
- HMM regime detection (2-state Gaussian, hmmlearn) → then `calibrate(asset) -> AssetProfile` graduates to engine → Phase 2 simulator
