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

## 4. The testable frontier (what would change this verdict)

| Claim | Test | Expected outcome (and why) |
|---|---|---|
| Ensemble mean predicts price | Correlate mean seeded path with next-year realized | Fails for EUR (drift unidentified); ~drift line only for gold/BTC |
| Regime state has directional information | Conditional next-bar return by state | Small negative (turbulence ≈ drawdowns); magnitude within noise |
| Volatility is predictable | GARCH forecast vs realized vol | **Passes** — this is the engine's genuine edge |
| Converged distribution = realized distribution | Rolling holdout gates (Phase 1/2/era studies) | Passes per asset/era window while it stays calibrated |

## 5. Verdict

The theory is half right, and the half that's right is the valuable half.
Simulations **do** converge — to the model's distribution, which this project
demonstrates can be made empirically real. That makes the tool a **predictor of
distributions, regimes, and risk** — a risk oracle — not a crystal ball for prices.
The gem is real; it is probabilistic, and it is already in the repo, gated and
documented. The price-prediction version of the gem would require markets to be
predictable and stationary — and the project's own analyses are the evidence that
they are neither.
