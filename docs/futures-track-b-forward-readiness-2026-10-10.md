# Track B — 5m IOC wide-stop forward readiness (2026-10-10)

**Context:** Step 4 parity (`docs/futures-demo-step4-1m-vs-5m-ioc-parity-2026-10-10.md`) fixed the execution story: the **primary historical lead** is **1m pre-armed touch** (Track A observer). The **only honest executable forward/DEMO hypothesis without new wiring** is **completed-5m close IOC8** on the isolated wide-stop lane (**Track B**).

This document is a **pre-deploy / pre-collection checklist**. It does **not** authorize merge, deploy, epoch reset, or broker orders.

---

## 1. What Track B measures

| Field | Frozen contract |
|---|---|
| Strategy | `strat_4hr_retrigger` only (fills) |
| Instrument | MNQ |
| Detection | Completed **5m** bar, 09:30–11:00 ET (`wide_stop_forward_collector`) |
| Entry | `ioc_limit`, **8** marketable ticks, decision bar **close** |
| Stop / R:R lane overlay | **400 ticks** max stop, **≥ 1.0** R:R on `wide_stop_4k` |
| Ledger | $4,000 start, **$400** daily-loss floor (2× $200 worst case) |
| 3-2-2 / Miyagi | **Shadow only** on research branch (no paper/demo fills) |
| Prereg | `T-2026-10-09-prereg-4hr-mnq-400-forward-2026-10-09-01` / `E-2026-10-09-4hr-mnq-400-forward-01` |
| First eligible session | **2026-10-12** (sessions through 2026-10-09 ineligible) |
| Pass (first look) | ≥ **40** forward fills; net **+**; **both halves +**; top-3-month share **< 60%** |

**Not measured by Track B:** 1m pre-armed P&L, ETF options expression, ORB/VWAP, real-book `$1,500` / 120-tick / 2R path.

---

## 2. Source branch vs what runs on the box today

| Item | Deployed / `main` family (typical) | `research/4hr-mnq-400-forward-20261009` @ **`2f4f776`** |
|---|---|---|
| `wide_stop_4k` stop cap | **300** ticks (`context/wide_stop_ledger_paper.py`) | **400** ticks |
| 3-2-2 fills | Still eligible in collector loop | **Disabled** — shadow only |
| Forward prereg + spec | Not on current #1211 branch | Present on research branch |
| Saved benchmark artifact | Referenced from checkpoint | `docs/research-evidence/T-2026-10-09-prereg-4hr-mnq-400-forward-2026-10-09-01/` |
| ORB/VWAP sweeps | — | Same branch (DO NOT REDO) |

**Gap:** Forward trial **E-2026-10-09-*** requires **400-tick** lane code. Deploying **`main` or #1211 without the research branch ledger change** would score the **wrong cell** (300-tick baseline in experiment spec).

---

## 3. Parallel tracks (operator)

```text
Track A — Mechanism (no orders)
  1m observer + prereg docs/prereg-forward-one-min-trigger-evidence-review-2026-09-18.md
  Continues on current box when flags pinned; does NOT prove profitability.

Track B — Executable forward (paper + optional guarded DEMO)
  5m IOC8 wide_stop_4k @ 400 ticks after reviewed deploy of research SHA
  Scores prereg E-2026-10-09-* only.

Do not conflate A and B in reports or Grok handoffs.
```

---

## 4. Pre-collection gates (all required before counting forward fills)

### 4.1 Source / review (step 1)

- [ ] **#1208 / #1209:** Grok **AFS-0212 / AFS-0213** fixes merged (operator-owned) before trusting fill-sensitive audits.
- [ ] **#1210 / #1211:** Independent review at **exact heads**; CI green (last verified: #1211 green on PR head).
- [ ] **Research branch:** Review diff `fix/timeframe-causal-buckets-research-20261010..research/4hr-mnq-400-forward-20261009` — focus `wide_stop_ledger_paper.py`, `wide_stop_ledger_offline_expectation.py`, experiment spec, 3-2-2 shadow change — not ORB/VWAP sweep reruns.

### 4.2 Deploy (explicit operator GO only)

- [ ] Nominate **exact SHA** from `research/4hr-mnq-400-forward-20261009` (or a merge commit that includes it unchanged).
- [ ] Controlled release: deploy lock, integrity, rollback pointer, **no** `--force-lock` without stale proof.
- [ ] **Do not reset** shared wide-stop epoch `2026-09-09T04:21:04Z` for cleanup (Daily 2-2 / ledger continuity — see Strategy Inventory).
- [ ] Post-deploy: deployed SHA matches reviewed SHA; `WIDE_STOP_LEDGER_MODE=paper_sim`; `FIVE_MIN_FEED_ENABLED=true`; journals writing.

### 4.3 Runtime pins (read-only box proof — **unverified in Cursor session**)

- [ ] `LIVE_TRADING_ENABLED=false`, `TRADOVATE_ENV=demo`, `MAX_CONTRACTS_HARD_CAP=1`
- [ ] Wide-stop demo route: if used, proof-pinned `WIDE_STOP_LEDGER_EXECUTION_ROUTE` + `WIDE_STOP_DEMO_EXECUTION_*` (additive to paper; see `wide_stop_execution.py`)
- [ ] 1m observer flags if Track A continues: `ONE_MIN_TRIGGER_ENABLED`, `ONE_MIN_4HR_OBSERVER_ENABLED`, matching proof pins per prereg

### 4.4 Scoring discipline

- [ ] Count only fills **on or after 2026-10-12** ET session date.
- [ ] Use runner / offline tooling aligned with **400-tick** `BRACKET_CELLS["wide_stop_4k"]` after deploy.
- [ ] Append outcomes to trial ledger per spec; **no** rescoring gzip benchmark dates.

---

## 5. What Cursor will not do without new instruction

- Merge research branch to `main`
- Deploy or restart VPS services
- Arm demo execution env vars
- Loosen global risk rules or enable `strat_4hr_retrigger` on the real book
- Reset epochs or backfill Sep 18–Oct 4 invalid 1m interval

---

## 6. Suggested operator sequence (“next” after step 4)

1. **You:** Finish #1210 / #1211 review; queue #1208 / #1209 Grok fixes.
2. **You:** Review research branch @ `2f4f776` (400-tick + 4HR-only fills) — approve or reject for deploy candidate.
3. **Push** `research/4hr-mnq-400-forward-20261009` to origin if not already (checkpoint last said unpushed).
4. **Commit/push** checkpoint docs on #1211 (`step4` parity + this file + work-state) so agents share one URL.
5. **Restricted VPS read-only pass** (B1/B5/B6, journals, deployed SHA) — see `docs/futures-operator-todo.md`.
6. **Separate GO** to deploy exact research SHA → forward collection begins scoring Track B from **2026-10-12** onward.

---

## 7. References

| Doc | Role |
|---|---|
| `docs/futures-demo-step4-1m-vs-5m-ioc-parity-2026-10-10.md` | Why Track B ≠ primary 1m lead |
| `docs/prereg-4hr-mnq-400-forward-2026-10-09.md` | On research branch |
| `docs/4hr-prearmed-touch-ab-2026-09-18.md` | IOC8 vs pre-armed fill rates (historical) |
| `docs/futures-research-resume-checkpoint-2026-10-10.md` | Grok + agent appendices |
| `docs/agent-work-state.md` | START HERE |
