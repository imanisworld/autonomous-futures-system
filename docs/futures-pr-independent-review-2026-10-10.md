# Independent PR review — #1208–#1211 + research deploy candidate (2026-10-10)

**Reviewer role:** Cursor (breaker-style, read-only). **Not** operator merge/deploy authorization.

**Exact heads reviewed:**

| PR | Branch | HEAD | GitHub CI (2026-10-10 session) |
|---|---|---|---|
| [#1208](https://github.com/imanisworld/autonomous-futures-system/pull/1208) | `fix/strict-paper-reference-fills-20261009` | `4c914134` | tests + Analyze + CodeQL + handoff **pass** |
| [#1209](https://github.com/imanisworld/autonomous-futures-system/pull/1209) | `fix/rr-verify-actual-bracket-20261009` | `6482c5d` | tests + Analyze + CodeQL + handoff **pass** |
| [#1210](https://github.com/imanisworld/autonomous-futures-system/pull/1210) | `test/combined-execution-risk-20261010` | `55d99de` | tests + Analyze + CodeQL + handoff **pass** |
| [#1211](https://github.com/imanisworld/autonomous-futures-system/pull/1211) | `fix/timeframe-causal-buckets-research-20261010` | `fb4cc41` | Analyze + CodeQL + handoff **pass**; **tests job pending** at first check, recheck before merge |

**Local targeted pytest (Mac, `MAX_CONTRACTS_HARD_CAP=1`):**

| Branch | Command scope | Result |
|---|---|---|
| 1208 @ `4c914134` | `test_paper_broker_market_at_reference`, `test_replay_strict_market_reference` | **19 passed** |
| 1209 @ `6482c5d` | `test_risk_reported_vs_actual_rr`, `test_risk_bracket_side_runner` | **20 passed** |
| 1210 @ `55d99de` | `test_combined_execution_risk_qa` | **5 passed** |
| 1211 @ `fb4cc41` | `test_timeframe_integrity` | **32 passed** |

Full Ubuntu CI suite on 1211 not re-run locally (non-Ubuntu env).

---

## #1208 — strict research reference fills

**VERDICT: PASS (merge-ready pending operator approval, after Grok re-confirm at this head)**

**Scope verified:** Opt-in `ResearchReferencePaperBroker` wrapper; canonical `PaperBroker` unchanged; replay `market_at_reference` uses **next adjacent bar open**; fail-closed on holes; `pessimistic_both_hit=True` in research wrapper; no Webull mirror in strict path (tested).

**Likely Grok AFS-0212 / AFS-0213 alignment:** Branch already includes post-review commits **`506d91b`**, **`c3959a0`**, **`4c91413`** (pessimistic **stop-first** when stop and target both touched; ambiguous bar cannot become optimistic win). If Grok’s IDs referred to those findings, fixes are **on this head**, not on `main`. **Action:** Grok/operator **re-review at `4c914134`** rather than re-implementing blindly.

**Live / broker:** No routing change to Tradovate or wide-stop DEMO IOC path in this PR.

**Residual risk:** `market_at_reference` is replay/research-only until explicitly configured; production default unchanged.

---

## #1209 — actual bracket R:R

**VERDICT: PASS (merge-ready pending operator approval)**

**Scope verified:** `_check_bracket_direction` before R:R; `_check_rr_ratio` uses **computed** actual R:R and rejects stale high `setup.rr_ratio`; runner R:R exemption preserved; `risk_rules.yaml` unchanged.

**Live impact:** Tightens rejection of malformed brackets on **all** paths using `RiskEngine` — intended. Wide-stop lane still uses same engine after global/lane gates.

---

## #1210 — combined QA

**VERDICT: PASS (merge after #1208 + #1209, or with them in one release train)**

**Scope verified:** Integration tests only + docs pin 1m vs DEMO mismatch (`docs/futures-demo-step4-1m-vs-5m-ioc-parity-2026-10-10.md` on this branch’s ancestry in 1210). Stacks 1208+1209 changes.

---

## #1211 — timeframe integrity helper

**VERDICT: CONDITIONAL PASS — hold merge until GitHub **tests** job green on `fb4cc41`**

**Scope verified:** Isolated `research/timeframe_integrity.py` + tests; docs QA note; **no** wiring to frozen MGC scorer or live webhook path in diff reviewed.

**Not a substitute for:** Step 4 execution parity or Track B deploy.

---

## Research branch — deploy candidate (not a PR merge verdict)

**Branch:** `research/4hr-mnq-400-forward-20261009` @ **`2f4f776`** (pushed).

**VERDICT: APPROVED AS DEPLOY CANDIDATE SHA** for **Track B only** (`docs/futures-track-b-forward-readiness-2026-10-10.md`), subject to:

1. Operator **explicit GO** (not issued in this session).
2. Controlled release of **this exact SHA** (or a merge commit that byte-preserves ledger + prereg intent).
3. **Do not** score forward trial on current box **300-tick** ledger.
4. Prefer landing **#1208–#1210** on `main` first if the release train includes shared `risk_engine` / replay — research branch may not contain those fixes; **deploy SHA vs merge order** is an operator choice (deploy-only research SHA still valid for lane caps only if runtime code at SHA matches).

**Material code changes (deploy-relevant):**

- `wide_stop_4k`: **400** tick cap, **$400** daily loss floor.
- **3-2-2 / Miyagi:** shadow-only (no fills) — aligns with forward prereg focusing 4HR.

**Out of scope on same branch:** ORB/VWAP sweep artifacts — DO NOT REDO.

---

## Explicitly NOT done (requires operator)

- Merge any PR
- Deploy / restart VPS
- Arm `WIDE_STOP_DEMO_EXECUTION_*`
- Start forward fill scoring
- VPS read-only B1/B5/B6 proof
- Grok ledger IDs **AFS-0212 / AFS-0213** — **not found in repo**; treat as **external Grok report** → re-run at #1208 head above

---

## Recommended merge order (when operator approves)

1. **#1208** → **#1209** (order either way; both correctness)
2. **#1210** (after both, or rebase)
3. **#1211** (after tests green; independent of 1208–1210 but safe to merge in parallel once green)
4. **Deploy** `research/4hr-mnq-400-forward-20261009` SHA via controlled release — **separate** from doc-only #1211 unless merged

---

## Safe next step

Operator: confirm Grok sign-off on **`4c914134`** / **`6482c5d`**; wait for **#1211 tests** green; then merge train decision. No forward collection until Track B checklist §4 complete.
