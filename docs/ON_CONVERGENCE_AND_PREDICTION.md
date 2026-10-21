# On convergence and prediction — an analysis of the "gem" theory

> **The theory** (stated fairly): *if we keep running simulations and they converge
> to something, that something could be a prediction of the market. That would be a
> gem of a discovery.*

This document analyses the theory honestly: what convergence actually is, what it
would need to be a prediction, why those conditions fail for prices, and where the
real — genuinely valuable — discovery lives instead.

---

## 1. What convergence is (and isn't)

Run N simulations, average a statistic, and watch the estimate settle: the standard
error shrinks like 1/√N. For annualized vol over our standard setup
(N ≈ 300 paths, decades of simulated data per path) the relative error falls below
**1%**. Convergence is real, fast, and mathematically guaranteed — it is the Law of
Large Numbers, and it is already working in every `validation_suite` band this
project produces.

But notice what the average converges **to**: the statistic *of the model*. The
ensemble mean of simulated log-prices converges to the calibrated drift line; the
ensemble distribution converges to the model's implied distribution. Convergence
answers "what does *this engine* say?" — not "what will the market do?"

## 2. What prediction would require — and why it fails

For converged simulations to *predict*, two conditions must hold. Both fail.

**(a) The model must equal the data-generating process.** Calibration fits past
statistics; markets change. This project's own era study (`02_era_weighting.ipynb`)
*proved* the failure: BTC's fitted 66% vol describes a regime that no longer exists
(recent era ~45%), and gold's 2026 crisis exceeds everything in its history. The
target itself moves. Converging precisely to a moving target is not prediction.

**(b) Prices must be predictable at all.** Under the efficient-market hypothesis,
price series are close to martingales: today's price already reflects available
information, so the *directional* component is noise by construction. What is
predictable is the *conditional scale* — volatility clustering — not the sign of
the next move. No amount of simulation overrides that; a martingale simulated a
million times is still a martingale.

And the project's Phase 1 found the quiet killshot for the mean: **EUR/USD's drift
is unidentified** — the confidence interval spans roughly ±2 percentage points per
year. Even the *center* of the future distribution is statistically invisible over
any horizon this tool simulates. Averaging seeds reveals the calibrated drift; for
EUR/USD we *know* that number is indistinguishable from zero. There is nothing to
predict *with*.

## 3. The actual gem: distributional prediction

Strip the price-prophecy framing and the theory points at something real and
bank-grade: **the ensemble is a forward-looking risk oracle.**

- *Volatility forecasting.* GARCH is, literally, a model of how volatility tomorrow
  depends on shocks today. The HMM adds regime probabilities: "given today's state,
  there is a ~X% chance of being turbulent next week, with expected dwell Y days."
  That is a forecast — a probabilistic one, validated by construction.
- *Tail and stress quantification.* From a converged ensemble: P(loss > k) in the
  next year, expected shortfall, probability of a regime switch during a holding
  period, distribution of max drawdown. These are *well-posed predictive questions
  with ensemble answers* — the same questions risk desks answer with the same
  machinery (Monte Carlo VaR is industry standard).
- *Calibration honesty.* The engine's holdout gates exist precisely to test whether
  the converged distribution is *real*: BTC full-history fails on recent data;
  era-weighted passes. That is the scientific method applied to the oracle — most
  simulation tools never check.

If there is a discovery-grade result here, it is this: **a validated, regime-aware
distribution of the future is a form of prediction — the only kind markets permit.**
"Where will BTC be in a year?" is unanswerable. "What is the distribution of where
BTC could be, conditional on volatility regimes, and is that distribution
empirically honest?" — this engine answers, and the answer compounds in value with
every asset and every validated scenario.

## 4. The frontier, measured

§1–3 were an argument. This section is the experiment, run on the real series in
this repo. Every number below is reproducible from `synthetic.forecast` plus the
committed parquet; the method notes at the end say how, and where the traps are.

### Volatility is forecastable — but barely better than a moving average

Mincer–Zarnowitz R², forecasting the next 20 bars' **log realized variance**:

| profile | engine, causal | trailing-20 var | engine, Viterbi |
|---|---|---|---|
| eurusd | **0.557** | 0.497 | 0.596 |
| xauusd | **0.319** | 0.307 | 0.434 |
| btcusd | 0.137 | **0.196** | 0.224 |
| btcusd_last4y | 0.027 | 0.025 | 0.018 |

Three readings, in descending order of importance:

**The hindsight premium is most of the apparent edge.** The third column drives the
variance filter off the Viterbi path, which decodes the *entire* series — so the
state at bar `t` knows about bar `t+500`. It is not a forecast, and the gap between
columns one and three is the size of the self-deception. Any backtest that decodes
regimes over the full history before trading them is reading this column.

**Causally, the engine does not reliably beat a 20-bar moving average of squared
returns.** It wins on EUR/USD (0.557 vs 0.497) and ties on gold, and it *loses* on
BTC. A regime-switching GARCH-t with Student-t innovations, fitted by MLE, is
roughly level with the crudest estimator in the field. This is not a defect in the
fit; it is what volatility forecasting is like.

**Era-weighting destroys the vol-timing signal.** `btcusd_last4y` scores ~0.02, and
so does its baseline — there is little forecastable vol *variation* left inside a
homogeneous recent window. Distributional honesty and vol timing pull in opposite
directions: use the era window for scenario realism and full history for sizing.

### Position sizing beats regime timing, and both beat direction

Causal vol targeting (`weight = median(h) / sqrt(h_t)`, capped at 3x) and regime
gating, applied to the next bar's return, no costs:

| asset | buy & hold | vol targeted | regime gated | trailing-20 targeted |
|---|---|---|---|---|
| eurusd | −0.01 | −0.01 | −0.01 | 0.01 |
| xauusd | 0.54 | 0.63 | 0.63 | **0.67** |
| btcusd | 0.69 | 0.79 | 0.73 | 0.82 |

Sharpe, full history. Vol targeting improves on buy & hold where there is a return
to harvest, and it does so the *right* way — gold's gain is a 14% volatility
reduction, not a return increase. But the trailing-20 estimator does it as well or
better, which is the same verdict as the forecast table.

Regime gating is the weaker expression of the same signal and costs 5–20x the
turnover (BTC flips 65 times a year), so it is dominated once costs exist at all.

**EUR/USD staying flat is a confirmation, not a failure.** Phase 1 found its drift
unidentified (±2pp/yr); vol targeting cannot manufacture return where there is none
to scale. A method that "worked" there would be the result to distrust.

### Conditioning has a shelf life, and it is the dwell time

Opening a forecast from today's filtered regime and variance (`condition=true` on
`/ensemble`) is only worth something for as long as the chain remembers. Variance
is discarded by the first regime switch, so the horizon over which a conditioned
forecast differs from an unconditional one is the asset's dwell time:

| asset | dwell (days) | median bars to 1st switch | chain mixing time |
|---|---|---|---|
| eurusd | 104 / 24 | **56** | 66 |
| xauusd | 59 / 17 | 31 | 31 |
| btcusd | 11 / 4 | 6 | 10 |
| btcusd_last3y | 5 / 2 | **3** | 2 |

Measured on the opening spread of 300 paths, EUR/USD narrows from 0.0071 to 0.0040
over the first five bars — tracking its 0.54x conditional/unconditional vol ratio —
while `btcusd_last3y` does not move at all despite a comparable 0.57x ratio,
because it has already switched regime by bar three.

Two consequences. Conditioning is worth far more on FX and gold than on crypto. And
the same timescale governed the `start_probs` defect this phase fixed: both the
severity of that bug and the value of this feature scale with dwell time, because
both are questions about how long the chain's opening state survives.

### What this does and does not license

| Claim | Verdict |
|---|---|
| Ensemble mean predicts price | **No.** Converges to the calibrated drift line; unidentified for EUR/USD |
| Regime state carries directional information | **Not usefully.** Real but dominated by sizing, and eaten by turnover |
| Volatility is predictable | **Yes, weakly.** R² 0.14–0.56 causally, roughly level with a 20-bar moving average |
| The engine's vol forecast beats a trivial one | **Not reliably.** Wins on EUR/USD, ties on gold, loses on BTC |
| Converged distribution = realized distribution | **Yes, per asset/era**, while it stays calibrated — the validation suite is this gate |
| First-passage and tail quantities | **Yes, and uniquely.** No closed form exists under this model; the ensemble is the only instrument |

### Method notes (the traps)

- **Causality.** `forecast.state_filter` is a Hamilton filter: row `t` sees only
  bars before `t`. `HMMRegimes.posterior` (forward–backward) and `state_path`
  (Viterbi) both condition on the whole series and must never drive a backtest.
- **Variance carries across switches** in the filter (`reinit=False`) even though
  the *generator* resets on every switch. The generator knows its regime path
  exactly; a filter only estimates it, and resetting on a misclassified switch
  discards real information. Measured: R² 0.557 carried vs 0.407 reset.
- **Everything above is in-sample on parameters.** The profiles were fitted on the
  same history the strategies trade. The causal filter removes lookahead *within*
  the filter, not *parameter* lookahead. Expanding-window refits are the next gate
  and nothing here should be believed at face value until they run.
- **No transaction costs**, so every turnover figure is a liability the Sharpes do
  not yet pay.

## 5. The actual gem, restated precisely

The distributional claim survives the measurements; the forecasting claim comes out
much weaker than §3 asserted. That shifts where the value is, and it is worth being
exact about the destination.

The engine is not a better volatility forecaster than a moving average, and it is
not a price predictor at all. What it is — uniquely, with no closed-form
competitor — is a machine for **path-dependent questions under realistic
dynamics**:

- *What is the chance my stop is hit before my target, within 60 bars, starting
  from today's regime and volatility?*
- *What is the distribution of max drawdown over the next year — not the average
  path's, the 95th percentile path's?*
- *What is expected shortfall at 1%, under fat tails and volatility clustering
  rather than a lognormal guide?*

These have no analytic answer once you add regime switching, GARCH persistence and
Student-t innovations. Barrier probabilities under plain GBM are a textbook
formula; under *this* model, simulation is the only route. And unlike a price
forecast, each one is a question the model is actually entitled to answer, because
each is a property of the distribution the validation suite gates.

That is the gem, and it is narrower and more defensible than "the simulations
converge to a prediction": **the ensemble prices position risk, conditional on
today, under dynamics that pass empirical gates.** Risk desks pay for exactly this.
`web/src/engine/risk.ts` computes it and the distribution view shows it.

## 6. Verdict

The theory is half right, and the half that is right is the valuable half —
though not for the reason §3 originally gave. Simulations **do** converge, to the
model's distribution, which this project demonstrates can be made empirically
real. The conditional machinery now exists to start that distribution from today
rather than from the long run. But the forecasting edge over trivial estimators is
thin to absent, and the measurements above are the evidence.

So: a **predictor of distributions, regimes and position risk** — a risk oracle —
not a crystal ball for prices, and not a volatility alpha either. The gem is real,
it is probabilistic, and it is now measured rather than asserted.
