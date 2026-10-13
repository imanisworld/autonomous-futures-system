# Track B — 5m IOC wide-stop forward readiness (2026-10-10)

**Context:** Step 4 parity (`docs/futures-demo-step4-1m-vs-5m-ioc-parity-2026-10-10.md`) fixed the execution story: the **primary historical lead** is **1m pre-armed touch** (Track A observer). The **only honest executable forward/DEMO hypothesis without new wiring** is **completed-5m close IOC8** on the isolated wide-stop lane (**Track B**).

This document is a **pre-deploy / pre-collection checklist**. It does **not** authorize merge, deploy, epoch reset, or broker orders.

---

## 0. Safety refresh — 2026-10-13

Cursor/Cloud Agent's same-day restricted read-only runtime check reported:

- active futures release `94a71bdbb975b53f21a81b7084482ea3c5270ceb`; service active; no restart drift observed;
- live execution off, Tradovate DEMO, hard cap 1, account pin match, broker flat, zero working orders, deploy lock absent;
- **release integrity FAIL** because the immutable release contains extra `scripts/__pycache__/options_122_prospective_collect.cpython-313.pyc`;
- Track B `wide_stop_4k` still has **0 fills** and recorded state last dated **2026-09-30**.

Therefore Track B is **not cleared for progression or forward-fill counting** from this checkpoint. Do not repair the release by deleting files inside `/root/afs-releases`; immutable-release recovery must use the sanctioned deploy-lock path under separate operator authorization, then pass fresh post-recovery proof.

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

## 2. Source vs box (updated 2026-10-11 after **#1216** on `main`)

| Item | **`main` @ `58d4cd6` (#1216)** | Legacy `research/4hr-mnq-400-forward-20261009` @ **`2f4f776`** (superseded for deploy) |
|---|---|---|
| `wide_stop_4k` stop cap | **400** ticks paper; **300** ticks max on Tradovate demo orders | **400** ticks paper (same intent) |
| 3-2-2 / Miyagi | **Shadow only** on `wide_stop_4k` ledger | Shadow-only variant on old branch |
| Forward prereg + spec | On **`main`**: `docs/research-experiment-specs/E-2026-10-09-4hr-mnq-400-forward-01.json` | Same spec lineage |
| Saved benchmark / sweeps | `docs/research-notes/4hr-mnq-400-historical-benchmark-2026-10-10/`, `orb-vwap-tf-window-sweep-2026-10-10/` | DO NOT REDO |

**Deploy gap (2026-10-11):** The **400-tick paper cell is on `main`**. Promoted release **`94a71bd`** is **tree-identical** to `58d4cd6`. A **separate research-branch deploy is not required** for stop-cap scoring **if** the box SHA matches and journals prove collection. **Do not** redeploy without operator GO (`docs/agent-work-state.md`).

---

## 3. Parallel tracks (operator)

```text
Track A — Mechanism (no orders)
  1m observer + prereg docs/prereg-forward-one-min-trigger-evidence-review-2026-09-18.md
  Continues on current box when flags pinned; does NOT prove profitability.

Track B — Executable forward (paper + optional guarded DEMO)
  5m IOC8 wide_stop_4k @ 400 ticks paper (demo orders capped 300) on deployed SHA matching `main` #1216
  Scores prereg E-2026-10-09-* only from 2026-10-12 sessions onward.

Do not conflate A and B in reports or Grok handoffs.
```

---

## 4. Pre-collection gates (all required before counting forward fills)

### 4.1 Source / review (step 1)

- [x] **#1208–#1211:** Merged to **`main`** 2026-10-10 (exact-head CI green on merge heads). Independent breaker sign-off on **deployed tree `58d4cd6`** still operator/breaker-owned.
- [x] **#1216:** Merged — 400-tick paper + 300-tick demo cap on `main`.
- [ ] **Fill-sensitive replays:** Only after operator confirms Grok **AFS-0212 / AFS-0213** items (if any) on merged code — do not assume post-merge replay done here.

### 4.2 Deploy (explicit operator GO only)

- [x] **One promote recorded:** release **`94a71bd`** @ 2026-10-10T15:45:33Z; tree equals **`58d4cd6`**. **No second deploy/restart** unless SHA/posture drift is proven.
- [x] **Partial read-only box proof (2026-10-13):** deployed SHA/service identity, deploy lock absent, demo/live pins, cap 1, account pin, broker flat, zero working orders. **Release integrity FAIL** and Track B journal/state is stale/0 fills, so progression remains blocked — see §4.3.
- [ ] **Do not reset** shared wide-stop epoch `2026-09-09T04:21:04Z` for cleanup (Daily 2-2 / ledger continuity — see Strategy Inventory).
- [ ] If drift found: nominate **exact reviewed SHA** only; controlled release with lock/integrity; post-check `WIDE_STOP_LEDGER_MODE=paper_sim`, `FIVE_MIN_FEED_ENABLED=true`, journals writing.

### 4.3 Runtime pins / integrity (read-only box proof — **partial 2026-10-13**)

- [x] `LIVE_TRADING_ENABLED=false`, `TRADOVATE_ENV=demo`, `MAX_CONTRACTS_HARD_CAP=1`
- [x] Account pin match; broker flat; zero working orders; deploy lock absent
- [ ] **Release integrity PASS** — currently FAIL on extra `.pyc` inside immutable release `94a71bd…`; no in-place repair
- [ ] Wide-stop demo route: if used, re-prove `WIDE_STOP_LEDGER_EXECUTION_ROUTE` + `WIDE_STOP_DEMO_EXECUTION_*` after integrity recovery (additive to paper; see `wide_stop_execution.py`)
- [ ] 1m observer flags if Track A continues: re-prove `ONE_MIN_TRIGGER_ENABLED`, `ONE_MIN_4HR_OBSERVER_ENABLED`, matching proof pins per prereg

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

## 6. Suggested operator sequence (updated 2026-10-13)

1. **Contain the verified integrity drift:** leave the running process untouched; do not edit `/root/afs-releases` in place.
2. Under separate operator authorization, use the sanctioned immutable-release/deploy-lock recovery path; then re-run release integrity and the full runtime/broker/journal proof.
3. Only after integrity is **PASS**, verify Track B journal progression from the 2026-10-12 eligible start. The current fresh read shows **0 fills** and stale state dated 2026-09-30.
4. Count only eligible forward fills that are causally produced after all required gates are proven; keep paper 400t and demo 300t results separate.
5. Do not redeploy merely because `main` is ahead. Any future candidate/restart remains a separate operator GO.
6. Keep the automated weekday proof as supporting evidence only; it cannot override a failed release-integrity gate.

---
## 7. References

| Doc | Role |
|---|---|
| `docs/futures-demo-step4-1m-vs-5m-ioc-parity-2026-10-10.md` | Why Track B ≠ primary 1m lead |
| `docs/prereg-4hr-mnq-400-forward-2026-10-09.md` | On research branch |
| `docs/4hr-prearmed-touch-ab-2026-09-18.md` | IOC8 vs pre-armed fill rates (historical) |
| `docs/futures-research-resume-checkpoint-2026-10-10.md` | Grok + agent appendices |
| `docs/agent-work-state.md` | START HERE |
