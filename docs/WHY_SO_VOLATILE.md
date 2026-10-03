# Why does it look SO volatile — and why does each seed look like a different market?

Short answer: **that is what an honest random walk looks like.** Nothing is broken. Three effects stack on top of each other, and once you see them separately, the screenshots stop looking suspicious.

---

## 1. Volatility compounds with the square root of time — visibly

The simulator anchors at the asset's last real close and then lets a calibrated random
walk run. A random walk's dispersion grows with √T:

```
sd(log price after T years) = annual_vol × √T
```

So over the default 1,500-candle run, the **central 90% of where paths can end** is:

| asset | span | annual vol | 90% terminal band |
|---|---|---|---|
| EUR/USD | 5.95 y | 9.0% | **×0.70 to ×1.43** of the start |
| XAU/USD | 5.95 y | 18.9% | **×0.47 to ×2.13** |
| BTC/USD | 4.11 y | 65.7% | **×0.11 to ×8.9** |

A factor-of-2 swing for gold over six years is not the simulator being dramatic — it
is arithmetic. The workflow panel computes exactly this band for your current
settings (the "*~90% of paths end between …*" hint under the duration field); use it
as the reality check for any single path you see.

## 2. The regime path is itself random — that's the "different market" feeling

The HMM doesn't just add noise; it decides **which years of your simulated history
were turbulent**. The turbulent *share* is stable (~18–21% of days, and the
validation suite checks it), but *where* those stretches fall is drawn fresh per
seed. One seed gives you a quiet decade with one storm; the next puts three
crisis-like years in the middle. Different placement of the same ingredients reads
as "a whole different market" — but the fingerprint (vol, kurtosis, clustering,
regime dwells) is identical across seeds. That is what the validation suite gates.

A useful analogy: two worlds with the same climate can have very different weather
histories. You're looking at weather; the product is the climate.

## 3. EUR/USD has essentially zero drift — so it wanders with no directional pull

Phase 1 established that EUR/USD's annual drift is statistically
**unidentifiable** — every model's confidence interval spans roughly ±2
percentage points around zero. With no trend to anchor the eye, a zero-drift walk
on a linear price scale looks restless and directionless, and switching seeds
flips the direction of the wander. This is not instability; it is the honest visual
signature of "we cannot know which way this market goes."

Note the contrast: gold and BTC *do* have identified positive drift (their bands sit
noticeably upward-sloping across seeds), so their paths look more "purposeful" even
though their volatility is higher.

---

## What is stable across seeds (the actual product)

- Annualized volatility, excess kurtosis, max-move, squared-return ACF
- Regime mixture: turbulent share, state vols, dwell times
- Volume level, volume–volatility correlation, volume persistence

All of these are inside seeded simulation bands in `validation_suite` — the path is
the sample, the distribution is the deliverable.

## How to work with it

1. **Never judge one seed.** Judge ten. The toolbar's `↻ new seed` is the correct
   reflex: each press is another draw from the same climate.
2. **Use the horizon hint** in the workflow panel — if a path ends outside the
   ~90% band for its settings, *that's* the unusual event worth inspecting.
3. **Match the window to the question.** If the calibrated full-history vol looks
   hot relative to the market you watch (EUR/USD's recent era runs ~7% vs the 9%
   calibration; BTC ~45% vs 66%), era-weighted profiles (`btcusd_last3y`,
   `btcusd_last4y`, and recalibratable for anything) fit the recent regime instead
   — see `notebooks/02_era_weighting.ipynb`.
4. **For deliberate stress**, use scenarios (`flash crash`, `crisis`) — those inject
   what the calibrated model says shouldn't happen often, on purpose.

The volatility you see is not a bug in the mirror. It is the mirror working.
