# `premium_value` — PROVEN DEFECT, runtime repair deferred by governance

**Date:** 2026-08-08
**Status:** PROVEN DEFECT — runtime repair deferred by governance
**Reproduction:** `research/verify_premium_value_defect.py` (read-only; imports repo code, mutates nothing)

---

## RULING

```
premium_value is NON-EVIDENCE effective immediately.

Reason:
The current implementation derives Black-Scholes theoretical value using the
same contract's market-implied volatility, making theoretical price converge
to the market price by construction. Therefore its "edge" is not an
independent measure of option mispricing. Dividend-yield omission can further
produce false positive apparent discounts.

Until repaired and independently validated:
- Do not cite premium_value as evidence of contract mispricing.
- Do not use premium_value results in strategy-validation claims.
- Do not alter its runtime implementation during the current freeze.
- Preserve the defect reproduction and source trace for the post-freeze repair.
```

The runtime implementation is **deliberately untouched**. `premium_value` feeds advisory
scanner scoring, so any change to it is a runtime change, frozen through 2026-09-30.
No code-level marker was added to `alert_ranker/` for the same reason; that belongs to
the post-freeze repair ticket.

---

## Defect 1 — circular fair value (PROVEN, live)

`alert_ranker/options_valuation.py::evaluate_option_value` computes a Black-Scholes
theoretical value from **the contract's own implied volatility**, then scores
`edge = (theoretical − mark) / mark`.

Implied volatility is *defined* as the σ that makes the Black-Scholes price equal the
market price. So `BS(own IV) ≡ mark` by construction, and `edge ≡ 0`. The ±12% /
−15% discount/overpriced thresholds cannot be reached by genuine mispricing.

Measured (three realistic contracts, IV solved from the mark then fed back):

| S | K | DTE | mark | kind | solved IV | theo | edge | verdict | score |
|---|---|-----|------|------|-----------|------|------|---------|-------|
| 100 | 100 | 30 | 3.50 | call | 0.290205 | 3.4999 | -0.0% | fair | 0 |
| 450 | 460 | 7 | 2.10 | call | 0.224889 | 2.0999 | -0.0% | fair | 0 |
| 250 | 240 | 45 | 6.80 | put | 0.339011 | 6.7999 | -0.0% | fair | 0 |

### Why it is not merely inert

The only input that moves `edge` is model mismatch, and it is **directionally biased**.
The implementation assumes dividend yield q = 0. Against a SPY-like q = 1.2%:

| DTE | true (q=0.012) | repo (q=0) | apparent edge |
|-----|----------------|------------|---------------|
| 7   | 4.6162 | 4.6698 | **+1.16%** |
| 30  | 9.8631 | 10.1005 | **+2.41%** |
| 90  | 17.8125 | 18.5577 | **+4.18%** |
| 365 | 39.1092 | 42.4396 | **+8.52%** |

Always positive, growing with DTE, drifting toward the +12% "discount" threshold
(+2 score). Any firing therefore reports a **dividend artifact** as edge.

### Wiring (not dead code)

- `alert_ranker/scorer.py:84` → `evaluate_option_value(data)`
- `alert_ranker/scorer.py:118` → `components["premium_value"] = valuation.component_score`
- Contributes +2 / 0 / −3 among 8 scoring components.

`evaluate_option_value` short-circuits when the caller supplies `theoretical_value` or
`fair_value`, but **no repo code produces either** — `alert_ranker/scanner.py:264` only
reads them from the inbound context payload. The circular branch is the live path.

---

## Defect 2 — IV unit heuristic (PROVEN INPUT-PARSING DEFECT / NOT YET OBSERVED IN CURRENT DATA)

`alert_ranker/options_valuation.py:62`

```python
sigma = implied_volatility / 100.0 if implied_volatility > 3 else implied_volatility
```

Magnitude is not a valid discriminator between decimal IV and percent IV. A legitimate
decimal volatility of `3.5` means **350%**, not 3.5%:

| input | intended | parsed as |
|-------|----------|-----------|
| 0.22 | 22% | 22.0% ✓ |
| 22.0 | 22% | 22.0% ✓ |
| 2.50 | 250% | 250.0% ✓ |
| 3.00 | 300% | 300.0% ✓ |
| **3.50** | **350%** | **3.5%** ✗ |
| 350.0 | 350% | 350.0% ✓ |

Decimal IVs above 3.0 are misread by a factor of 100. Realistic for 0DTE, earnings, and
low-float names. Not observed in current production data. There is no IV sanity cap on
this path, unlike `sources/gex_compute.py`'s `_MAX_SANE_IV`.

Belongs in the same post-freeze ticket. Do not fix during the freeze.

---

## What is NOT wrong

The underlying arithmetic is correct — which is what made the inference error visible.
Verified against the reference below:

| Check | Result |
|-------|--------|
| `black_scholes_price` vs reference (q=0) | max abs err `4.99e-05` (pure `round(..., 4)` artifact) |
| Put-call parity `e^{-rT}K + C = S + P` | holds to `2.01e-05` |
| `sources/gex_compute.py::bs_gamma` vs reference γ (r=q=0) | max abs err `5.55e-17` — exact |

Note `gex_compute.bs_gamma` documents its r = q ≈ 0 assumption in its docstring;
`black_scholes_price` does not document its q = 0 assumption.

---

## Post-freeze repair — design question first

Do **not** simply "improve Black-Scholes". The prior question is:

> **What independent benchmark is `premium_value` supposed to represent?**

Any valid answer must derive fair value from a source *other than the contract's own
implied volatility*. Candidates:

- surface-relative IV (contract vs its own fitted vol surface)
- neighbouring-strike / neighbouring-expiry interpolation
- historical or forecast realised-volatility reference
- an independently derived fair-value model

Until that authority is defined **and validated**, the cleanest option may be to
**remove or neutralise the component** rather than invent a fake fair value.

If any replacement is built, it must also carry a null baseline — an apparent edge
distribution measured against randomised inputs — before it is cited as evidence.

---

## Source trace

Derived from the Columbia reference during the 2026-08-08 Batch 1 research run.

| | |
|---|---|
| Title | *The Black-Scholes Model*, IEOR E4706 Foundations of Financial Engineering |
| Author | Martin Haugh, © 2016 |
| URL | `https://www.columbia.edu/~mh2078/FoundationsFE/BlackScholes.pdf` |
| Retrieved | 2026-08-08 |
| Size / pages | 884,154 bytes / 12 pages |
| SHA-256 | `a84225df571d081f2eb2079d8e1160d51af50c72e4fd2c52f5073ac39d6884bb` |

Reference formulas used (S1, eq. 13 and the Greeks section):

```
C = e^{-qT} S Φ(d1) − e^{-rT} K Φ(d2)
d1 = [ln(S/K) + (r − q + σ²/2)T] / (σ√T)      d2 = d1 − σ√T
gamma = e^{-qT} φ(d1) / (σ S √T)
vega  = e^{-qT} S √T φ(d1)
put-call parity:  e^{-rT} K + C = e^{-qT} S + P
```

The defining sentence the circularity finding rests on:

> "σ(K,T) is the volatility that, when substituted into the Black-Scholes formula,
> gives the market price, C(S,K,T)."

Second source consulted in the same run and found **not** applicable to this defect:
*Foundations of Reinforcement Learning with Applications in Finance* (Ashwin Rao,
Stanford), `https://stanford.edu/~ashlearn/RLForFinanceBook/book.pdf`, 9,574,310 bytes /
538 pages, SHA-256 `610e5f381fcded8a4b7dd2e792c7b173a68f2eccd0da6ac428fe04e5c17c23e5`.
It contains zero occurrences of "Sharpe", "backtest", or "overfit" — it is a theory text
and bears on hedging/execution formulation, not on option-mispricing evidence.
