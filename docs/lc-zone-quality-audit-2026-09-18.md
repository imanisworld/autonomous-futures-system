# LC_ZONE Quality Audit — 2026-09-18

## Verdict

**Zone-quality classification: NO EVIDENCE OF ZONE QUALITY**

**HTF completion semantics: UNSAFE FOR TARGET-RULE VALIDATION**

Offline diagnostic only. No runtime, strategy, risk, target, broker, or execution rule was changed.

## Gate 0 — current vs completed higher-timeframe semantics

Maximum nearest-zone identity disagreement rate: **9.39%**.
4HR target-geometry rows that used a zone before its defining impulse HTF candle completed: **2**.

### MNQ

| TF | Kind | Comparable | Identity disagree | Partial current-nearest |
|---|---|---:|---:|---:|
| 60m | supply | 31106 | 3.57% | 847 |
| 60m | demand | 38045 | 3.39% | 828 |
| 240m | supply | 26241 | 9.39% | 1235 |
| 240m | demand | 36735 | 5.63% | 1134 |

Rolling-MTR qualification churn:
- 60m: 1791 identities; 378 late retroactive appearances (21.11%); qualification absent on 9.42% of expected-alive identity rows; 115 identities reappeared.
- 240m: 530 identities; 116 late retroactive appearances (21.89%); qualification absent on 14.05% of expected-alive identity rows; 59 identities reappeared.

### MES

| TF | Kind | Comparable | Identity disagree | Partial current-nearest |
|---|---|---:|---:|---:|
| 60m | supply | 30769 | 3.46% | 880 |
| 60m | demand | 37845 | 2.86% | 794 |
| 240m | supply | 26102 | 8.72% | 1206 |
| 240m | demand | 36201 | 5.24% | 1040 |

Rolling-MTR qualification churn:
- 60m: 1846 identities; 427 late retroactive appearances (23.13%); qualification absent on 9.15% of expected-alive identity rows; 83 identities reappeared.
- 240m: 519 identities; 122 late retroactive appearances (23.51%); qualification absent on 12.29% of expected-alive identity rows; 54 identities reappeared.

## Matched reaction-area quality

Real zones: **3643**; controls: **6394**; matched: **3643** (100.00%).

Primary metric: clean 0.5-MTR rejection within 2h, with far-edge close-through counted first on ambiguous bars.

| Population | n paired touches | Actual | Control | Uplift | 95% CI |
|---|---:|---:|---:|---:|---:|
| Combined | 2698 | 86.43% | 90.51% | -4.08 pp | [-5.67, -2.41] pp |
| MNQ | 1333 | 86.20% | 90.10% | -3.90 pp | [-6.23, -1.50] pp |
| MES | 1365 | 86.67% | 90.92% | -4.25 pp | [-6.52, -1.98] pp |
| 60m | 2142 | 86.46% | 89.82% | -3.36 pp | [-5.23, -1.49] pp |
| 240m | 556 | 86.33% | 93.17% | -6.83 pp | [-10.25, -3.42] pp |
| supply | 1444 | 88.71% | 88.92% | -0.21 pp | [-2.42, +2.01] pp |
| demand | 1254 | 83.81% | 92.34% | -8.53 pp | [-10.93, -6.14] pp |

Instrument x timeframe:
- MNQ_60: n=1057, uplift -3.50 pp
- MNQ_240: n=276, uplift -5.43 pp
- MES_60: n=1085, uplift -3.23 pp
- MES_240: n=280, uplift -8.21 pp

Secondary combined metrics:
- 1.0-MTR clean rejection: actual 76.06%, control 72.05%.
- far-edge close-through within 2h: actual 23.09%, control 22.61%.
- mean directional close return +1h: actual -0.474 MTR, control -0.022.
- mean directional close return +2h: actual -0.555 MTR, control +0.013.
- mean 2h MFE: actual 3.141 MTR, control 2.662.
- mean 2h penetration: actual 3.424 MTR, control 2.441.

## Freshness / repeated tests (descriptive only)

- 1_fresh: n=2935, clean 0.5-MTR rejection 86.71%, clean 1.0-MTR 76.05%, close-through 23.03%.
- 2: n=2663, clean 0.5-MTR rejection 68.42%, clean 1.0-MTR 56.29%, close-through 24.60%.
- 3plus: n=21704, clean 0.5-MTR rejection 45.07%, clean 1.0-MTR 35.37%, close-through 17.52%.

## Interpretation rule

This report follows the preregistered classification exactly. It does not tune the impulse threshold, control band, reaction thresholds, matching features, or instrument/timeframe population after reading outcomes.

Any alternative zone boundary (body-only, proximal/distal, multi-bar base, BOS/volume confirmation) requires a separate preregistered follow-up.

## Post-result diagnostics — not part of the preregistered decision rule

Independent recomputation from the frozen result JSON reproduced the primary
paired-touch result exactly:
- paired complete first touches: 2,698;
- actual clean 0.5-MTR rejections: 2,332;
- matched controls: 2,442;
- uplift: −4.077 pp.

The current detector also creates zones that are not on the intuitive expected
side of price at formation. On the 3,643 real formations:
- 546 (14.99%) were side-invalid at availability under the simple rule
  demand-top <= impulse close / supply-bottom >= impulse close.

This was **not** filtered after outcomes; the preregistration froze the current
detector unchanged. Adding a side-validity requirement would define a new
detector and requires a new preregistered study.

### 4HR target-geometry contamination

The historical 4HR target-geometry artifact contained 75 rows with an exact
opposing zone. Two used a 1H zone before its defining impulse 1H candle had
completed:

- 2024-11-25 SHORT, entry 09:55 ET: old premature 1H demand
  20943.00–21027.25 classified the 20908.00 target as `BEYOND_ZONE`.
  Strict completed-HTF semantics instead select 4H demand
  20514.00–20907.00, making the same target `BEFORE_ZONE`.
- 2025-07-29 SHORT, entry 09:50 ET: old premature 1H demand
  23572.50–23615.00 is replaced by completed 1H demand
  23541.50–23554.75; the target remains `BEYOND_ZONE`.

Therefore the old 4HR zone-geometry table is not sufficiently causal to support
a target-clipping rule.

## Harness correction provenance

The first execution of this audit used an invalid "completed HTF" helper that
required every nominal 15m row to exist inside a clock bucket. CME scheduled
maintenance makes that stricter than the preregistered semantics. That run is
explicitly invalidated in
`docs/lc-zone-quality-audit-harness-correction-2026-09-18.md`.

The results in this report come from the corrected committed harness at
`11bb0ef75b5d84944320329594db1e29da3a4fa3`, which excludes only the
still-forming clock bucket and otherwise uses the available market bars.

