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
- [ ] **Read-only box proof** (2026-10-12 session open): deployed SHA, rollback pointer, deploy lock, demo/live pins, flat account, collector + journals — see §4.3.
- [ ] **Do not reset** shared wide-stop epoch `2026-09-09T04:21:04Z` for cleanup (Daily 2-2 / ledger continuity — see Strategy Inventory).
- [ ] If drift found: nominate **exact reviewed SHA** only; controlled release with lock/integrity; post-check `WIDE_STOP_LEDGER_MODE=paper_sim`, `FIVE_MIN_FEED_ENABLED=true`, journals writing.

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

## 6. Suggested operator sequence (2026-10-11)

1. **Read-only VPS pass** before **2026-10-12** RTH — deployed SHA `94a71bd`/`58d4cd6`, posture pins, 5m feed, wide-stop collector/journals (`docs/futures-operator-todo.md`, `/futures-deployment-safety-audit`).
2. **No redeploy** unless drift proven — HOLD per `docs/agent-work-state.md`.
3. **Score Track B** only from **2026-10-12** sessions; label **paper 400t** vs **demo 300t** separately.
4. **Post-merge breaker** (optional): review **`58d4cd6`** mechanism/DEMO isolation — not a redeploy prerequisite if box SHA already matches.
5. **Automated weekday proof (VPS):** `afs-forward-lanes-daily-proof.timer` runs **Mon–Fri 17:25 America/New_York** (after six-micro **17:10**). Read-only JSON append log: `/root/afs-shared/logs/forward_lanes_daily_proof.jsonl`. Source: `ops/forward_lanes_daily_proof.py` (installed copy under `/root/afs-shared/ops/`).

---

## 7. References

| Doc | Role |
|---|---|
| `docs/futures-demo-step4-1m-vs-5m-ioc-parity-2026-10-10.md` | Why Track B ≠ primary 1m lead |
| `docs/prereg-4hr-mnq-400-forward-2026-10-09.md` | On research branch |
| `docs/4hr-prearmed-touch-ab-2026-09-18.md` | IOC8 vs pre-armed fill rates (historical) |
| `docs/futures-research-resume-checkpoint-2026-10-10.md` | Grok + agent appendices |
| `docs/agent-work-state.md` | START HERE |
