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

## Session 5 — 2026-09-17 — HMM regimes + Phase 1 gate (PHASE 1 COMPLETE)

**Done**
- Engine: `HMMRegimes` (canonical calm→turbulent ordering, Viterbi `state_path`, smoothed `posterior`), `fit_hmm_regimes` (manual multi-start; hmmlearn 0.3.3 has no `n_init`), `simulate_regimes`, `fit_garch_t` wrapper (arch, decimal units), `AssetProfile` dataclass, `calibrate(df) -> AssetProfile`, `fit_state_innovation_df` (per-state t df on standardized residuals — avoids double-counting tail fatness), `simulate_profile_daily`, `profile_gate_check` (parametric-bootstrap gate). 30 tests passing (Markov-chain recovery, dwell-time theory check, end-to-end calibrate on synthetic OHLCV).
- Bug found & fixed: `mle_student_t` scale bounds assumed return-scale data; standardized residuals (scale≈1) started infeasible → L-BFGS-B clamped to bound, garbage fits. Bounds widened (scale log ∈ [-10, 2], df > 2.05).
- Notebook section 8 + Phase-1-complete verdict; executed headlessly, 0 errors, md synced to executed output.

**Results**
- Regimes: EUR/USD calm 7.5%/turbulent 15.3% ann vol, dwell 104d/24d, turbulent mean −1.8bp/day; gold 13.3%/29.9%, dwell 59d/17d, −6.3bp/day. Turbulence maps to real history: EUR 2008-11 + 2015 (euro crisis/SNB), gold 2008/2013/2020/2026 (99% capture of Jan-2026 crash).
- Per-state innovation df: EUR 8.4/13.2, gold 6.8/5.8.
- **Phase 1 gate: 6/6 PASS** (mean/vol/kurt × 2 assets, bootstrap tolerances).
- Documented gap: profile under-generates r² ACF at mid/long lags (above 95th pct at lags 2/5/10 EUR, 2/10 gold) → slow clustering component lives inside states → quantitative motivation for Phase 2 per-state GARCH(1,1).

**Next**
- Commit; Phase 2: regime-switching GARCH(1,1)-t simulator with per-state params, intra-candle ticks → bars, statistical validation suite vs the targets frozen in AssetProfile

## Session 6 — 2026-09-18 — Phase 1 hardening pass (user-requested full scope)

**Done**
- Engine: `time_split`, `holdout_check`, `profile_gate_check(targets=, n=)` (out-of-sample judging), `fit_state_garch`, `simulate_garch_t`, `simulate_profile_conditional` (regime path + per-state GARCH recursion — Phase 2 daily core), `garch_unconditional_var`, `_sim_safe_garch` (IGARCH guard), `hmm_bic`. `AssetProfile` gained `state_garch`. 34 tests passing incl. IGARCH-taming regression.
- **Bug fixed:** `fit_garch_t` (arch, `rescale=True`) reported μ/ω in internally-scaled units (×100) — params now divided by `res.scale`. Was silently corrupting every GARCH-derived variance in profiles.
- Notebook section 9 (holdout + era decomposition, conditional ACF recheck, scrub sensitivity, HMM bootstrap SEs + 2v3 BIC); executed headlessly, 0 errors; md synced.

**Hardening findings (the honest record)**
- Holdout 2003–14 → 2015–26: mean ✓ kurtosis ✓ both assets. Vol ✗ both — decomposed: state vol *levels* stable (gold turbulent test 2.25% vs train 2.07%), but test era realized turbulent shares at the 0th percentile of chain-implied bands (EUR 4.8% vs [10.3, 26.3]%; gold 8.7% vs [14.5, 24.9]%) — calm era draw; stationary model can't foresee regime mix → motivates Phase 2 scenario injection / initial-state control (already in brief).
- ACF gap CLOSED by conditional model: 7/8 inside (EUR 4/4; gold misses lag-10 only).
- Scrub rules are a plateau: vol 8.98–9.14% / 17.7–18.7%, df ±0.16, persistence ±0.0003 across z∈{8,12}, gap∈{4,8}.
- HMM: levels precise (±5–8%); **persistence weakly identified** (dwell CIs very wide — report as indicative). BIC: EUR 2-state; **gold 3-state wins** — third state = 54% vol, 1-day-dwell crash state ≡ the jump layer daily JD couldn't identify. Parked per brief ("start with 2"); gold crash-state/jump-layer upgrade is stretch-phase work.

**Phase 1 final status: hardened & accepted.** Limitations on record: era-dependence of mixture weight; HMM dwell uncertainty; gold long-lag residual; gold wants a crash state (parked).

**Next**
- Commit; Phase 2: `simulate(profile, n_candles, scenario?) → OHLCV[]` wrapping `simulate_profile_conditional`; intra-candle ticks → bars; full validation suite vs frozen targets.

## Data-volume strategy (discussed, 2026-09-18)

- **More years**: at the practical limit for these assets — bounded by economic non-stationarity (euro pre-1999, gold price-fixed pre-1971), not data availability. Longer history adds regime contamination, not signal.
- **Smaller timeframes**: the one axis adding *new* information (jump identification, intraday dynamics) — and required for Phase 2's tick simulator. Caveats: effective sample size grows sublinearly under dependence; microstructure noise (≥5-min bars / pre-averaging); intraday seasonality; Yahoo intraday capped at ~730d → better source (Dukascopy/Polygon/exchange APIs) when tick realism demands it.
- **More markets**: generality proof now (BTC-USD stresses every config axis: 365d calendar, real volume, different regimes), copula correlation panel in Phase 6 per brief.
- **Architecture decision**: daily EOD = calibration backbone; intraday = execution-dynamics layer; multi-asset = Phase 6 surface.
- **Phase 2 additions agreed**: asset registry (ticker/calendar/ppy/scrub defaults), volume module (vol process w/ volume–vol correlation; FX zeros documented or tick-proxy), volume stats added to frozen targets, BTC-USD as plug-and-play proof.

## Session 7 — 2026-09-18 — Phase 2: stochastic core (engine + notebook §10)

**Done**
- `engine/synthetic/registry.py`: `AssetSpec` (ticker/calendar/ppy/scrub/volume semantics), `REGISTRY` with eurusd/xauusd/btcusd; `data.py` iterates registry (`download --only keys`); `calibrate(df, asset=...)` auto-pulls spec (BTC → 365d calendar, zero code changes).
- `engine/synthetic/volume.py`: per-state log-volume, winsorized return-surprise driver (z cap 95th pct), AR(1) with clamp ±0.9 + stationary-variance-preserving innov_std, per-state mean anchoring; FX (no tape) → disabled → zeros (LEAN convention). `AssetProfile.volume`.
- `engine/synthetic/simulate.py`: `Scenario(force_state, n_candles)` (forced stretch, chain resumes), `scenario_states`, intra-candle Brownian bridge → OHLC, `simulate_ticks` (returns tick stream for Phase 4), `simulate(...) → validated OHLCV` (seeded, reproducible), `validation_suite` (mean/vol/kurt/max|r|/r²-ACF×4/turb-share/volume×3, seeded bands). `conditional_returns` extracted in calibration (returns + h path shared).
- GARCH sim normalization: ω renormalized so recursion attractor = sample state variance (level from data, dynamics from fit) — removes 3–6% vol bias of near-unit-root fits.
- BTC-USD downloaded (4,388 daily bars 2014–2026 + 17,241 hourly) — registry plug-and-play proven.
- Hourly hygiene pass (calendar-aware): eurusd_1h 2 corrupt pairs/1 gap/33 kept spikes; btcusd_1h 1 pair/0 gaps/22 spikes.
- 40 tests passing; notebook §10 (41 cells) executed headlessly.

**Bugs found by the gate & fixed (all regression-tested)**
- `_sim_metrics` computed r²-ACF on raw returns (missing square) — suite showed 0 clustering.
- `fit_garch_t` (arch rescale=True) reported μ/ω in scaled units — divide by `res.scale`.
- `np.polyfit` slope/intercept SWAP in volume fit (returns `[p1, p0]`) — the real explosion cause.
- AR clamp without variance preservation; log-volume clip truncating the tail the arithmetic mean needs (clip removed; z-cap keeps sims bounded).

**Validation suite results (150 seeded sims/asset) — the gate discriminates**
- EUR/USD **8/8 PASS** (returns, extremes, r²-ACF, volume).
- XAU/USD **9/11**: named residuals only (r²-ACF lag-10; volume ACF hairline 0.881 vs 0.899).
- BTC/USD **4/11**, every failure attributable: fat tails/crash size (2-state can't make a −46% day → parked crash state), clustering overshoot (era-dependent mixture), volume persistence (BTC volume trended ~1000× over sample). Zero code changes calibrated the new asset — and the suite named exactly which documented limits matter.

**Phase 2 status: engine complete and gated.** Remaining per brief: LEAN export (Phase 5), tick-stream animation consumer (Phase 4; stream already emitted).

**Next**: commit; Phase 3 (FastAPI serving profiles + simulation endpoints) or Phase 4 (canvas animation consuming `simulate_ticks`).
