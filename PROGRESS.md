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
