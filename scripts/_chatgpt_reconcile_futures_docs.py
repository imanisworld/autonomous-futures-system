from pathlib import Path
import re

inv = Path("docs/strategy-rules/Strategy_Inventory.md")
text = inv.read_text(encoding="utf-8")
text = text.replace("*Last updated: 2026-07-23*", "*Evidence classifications reconciled: 2026-09-01*", 1)

old_rows = '''| ORB Reclaim (MES) | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ n=305 | **PAPER PROOF** |
| ORB Reclaim (MNQ) | ✅ | ✅ | Partial | ✅ | ❌ insufficient | ✅ | ⚠️ n=253 thin | **PROMISING BUT UNPROVEN** |
| 4HR Re-Trigger | ✅ blockers resolved | ❌ | ❌ | Partial — external study | ✅ | Partial | ⚠️ n=32 MNQ | **WAIT — build detector** |
| 12HR Miyagi | ✅ blockers resolved | ✅ | Partial — standalone research module | ✅ | ✅ both halves (H2 thin) | ✅ 1-4 tick | ⚠️ n=15 MNQ / n=19 MES thin | **PROMISING BUT UNPROVEN** |
| 60M 3-2-2 First Live | ✅ blockers resolved | ✅ | Partial — standalone research module | ✅ IOC-faithful | ✅ both halves | ✅ 1-4 tick | ⚠️ n=34 MNQ thin | **PROMISING BUT UNPROVEN** |
'''
new_rows = '''| ORB Reclaim — current/first_cross (MNQ+MES) | ✅ | ✅ | ✅ isolated own-account audit (#368) | ✅ ioc_limit | ❌ own drawdown breaker halts H2 | n/a — halted | ⚠️ n=38; MNQ −$164.44 / MES −$49.30 | **BROKEN — negative evidence** |
| ORB Reclaim V4-R candidate | ✅ preregistered | ✅ research detector | ✅ isolated own-account audit (#368) | ✅ ioc_limit | ❌ H2 −$451.20 vs H1 +$900.57 | not established | ⚠️ n=31 | **WAIT** — positive aggregate, fails frozen H2 + concentration gates |
| 4HR Re-Trigger (MNQ) | ✅ | ✅ | ✅ full-engine audit (#372) | ❌ 1/81 real fills | n/a | n/a | n=81 known / 1 fill | **BROKEN FOR CURRENT EXECUTABLE FORM** |
| 4HR Re-Trigger (MES) | ✅ | ✅ | ✅ full-engine audit (#372) | ⚠️ ceiling 12/76 fills | ❌ H2 negative | n/a | n=76 known / 12 ceiling fills | **BROKEN / WAIT** |
| 12HR Miyagi | ✅ | ✅ | ✅ causal-stop closure (#366) | n/a — fails risk before fill | n/a | n/a | MNQ 0/8, MES 2/10 fit `max_stop_ticks` | **BROKEN FOR CURRENT SYSTEM RISK CONSTRAINTS** |
| 60M 3-2-2 First Live | ✅ | ✅ | ✅ full-engine closure (#367) | ❌ 0/34 real candidates fill | n/a | n/a | n=34 / 0 fill | **BROKEN FOR CURRENT SYSTEM RISK CONSTRAINTS** |
| ORB Breakout — inverted (MNQ evidence lane) | ✅ | ✅ | ✅ | ✅ IOC | ✅ historical sub-period/session/direction checks | ✅ through +4 ticks (#364) | n=111 historical study | **PROMISING BUT UNPROVEN** |
| MES 1-2-2 (`strat_122`) | ✅ | ✅ | ✅ executable audit (#373) | ✅ | ⚠️ executable subset thin | ✅ historical stress | 16/33 canonical candidates executable | **WAIT** |
'''
if text.count(old_rows) != 1:
    raise SystemExit("master-table stale block not found exactly once")
text = text.replace(old_rows, new_rows, 1)

orb_profiles = '''### ORB Reclaim — current/first_cross
**Verdict: BROKEN — negative evidence**

- Binding evidence: PR #368 isolated the currently implemented `first_cross` rule on its own account under IOC-faithful execution.
- Result: n=38 resolved, net −$213.74, PF 0.858; MNQ −$164.44 and MES −$49.30.
- The strategy's own drawdown breaker stops the second half; the older MES PAPER PROOF / MNQ PROMISING figures are superseded for the executable rule.
- Runtime enablement is a separate deployment fact and must be read from the actual box/config; this document does not infer current runtime posture from the evidence verdict.

---

### ORB Reclaim — V4-R candidate
**Verdict: WAIT**

- Preregistered PR #368 variant: New York + prior rejected-high/low context.
- Result: n=31, PF 1.338, +$449.37 aggregate, but H2 was −$451.20 and one month carried 70.6% of net P&L.
- It failed the frozen H2 and concentration criteria. Do not iterate another variant from the same corpus without new evidence.

---

'''
text, n = re.subn(r"### ORB Reclaim — MES\n.*?(?=### 4HR Re-Trigger)", orb_profiles, text, count=1, flags=re.S)
if n != 1:
    raise SystemExit("ORB Reclaim profile block not found")

four_hr = '''### 4HR Re-Trigger
**Verdict: MNQ BROKEN FOR CURRENT EXECUTABLE FORM; MES BROKEN / WAIT**

- Binding full-engine audit: PR #372.
- MNQ: the prior 80-fill standalone population collapses to 1/81 real fills through `ReplayEngine -> DecisionEngine -> RiskEngine -> PaperBroker`, including the hypothetical parity-defect ceiling pass.
- MES: ceiling improves 7 to 12 fills out of 76, PF 1.854, but H2 is −$273.75 versus H1 +$655.00.
- Legitimate preserved gates, not a parity patch, explain the MNQ collapse. No strategy/risk widening is justified by this evidence.

---

'''
text, n = re.subn(r"### 4HR Re-Trigger\n.*?(?=### 12HR Miyagi)", four_hr, text, count=1, flags=re.S)
if n != 1:
    raise SystemExit("4HR profile block not found")

miyagi = '''### 12HR Miyagi
**Verdict: BROKEN FOR CURRENT SYSTEM RISK CONSTRAINTS**

- Binding causal-stop closure: PR #366.
- The earlier PF/P&L study used a stop-reference formula with a confirmed lookahead defect.
- With the causal stop corrected, MNQ 0/8 and MES 2/10 historical trigger events fit the account's existing `max_stop_ticks` risk cap.
- The cap was independently confirmed as an intentional account risk control and is not widened here. Any bounded-stop Miyagi idea would be a new strategy variant requiring new evidence.

---

'''
text, n = re.subn(r"### 12HR Miyagi\n.*?(?=### 60M 3-2-2 First Live)", miyagi, text, count=1, flags=re.S)
if n != 1:
    raise SystemExit("Miyagi profile block not found")

three22 = '''### 60M 3-2-2 First Live
**Verdict: BROKEN FOR CURRENT SYSTEM RISK CONSTRAINTS**

- Binding executable-parity closure: PR #367.
- The prior 34-candidate / 21-fill / PF 10.36 study was standalone research and did not exercise the account's real runtime controls.
- Full-engine result: 0/34 real historical candidates reach a fill; even the most favorable parity-defect ceiling still leaves the population blocked by legitimate risk architecture, principally stop width and confluence.
- Do not change those account controls to rescue this strategy.

---

'''
text, n = re.subn(r"### 60M 3-2-2 First Live\n.*?(?=### VWAP Hold — MNQ NY)", three22, text, count=1, flags=re.S)
if n != 1:
    raise SystemExit("3-2-2 profile block not found")

# A runtime warning belongs near the top because the strategy inventory is evidence truth,
# not proof of what the VPS is currently running.
needle = "## Master Table\n"
warning = '''> **Runtime boundary (2026-09-01):** strategy verdicts below are evidence classifications. They do not prove the current VPS service, environment pins, enabled concepts, feeds, or broker account routing. Those remain box-side facts to verify separately.\n\n'''
if text.count(needle) != 1:
    raise SystemExit("master table heading not unique")
text = text.replace(needle, warning + needle, 1)
inv.write_text(text, encoding="utf-8")

handoff = Path("docs/futures-current-state-handoff.md")
handoff.write_text('''# Futures — Current State Handoff

_As of 2026-09-01. This is the single current handoff. It records verified repository state and explicitly separates it from box/runtime facts that have not yet been reverified._

## Verdict

**HOLD / AUDIT ONLY until the pending futures fixes are reviewed and the VPS evidence pass is complete.**

No strategy parameters, inverse mechanics, risk limits, or fill mathematics were changed in this cleanup.

## Repository state

- Docs reconciliation base: `1b07b6a482423b57d966bcd53c1940bdbe3dac78` (`main` at the time this branch was created).
- The concurrent options session advanced `main` during the futures audit; its options commits were preserved and not rewritten.
- Current MNQ ORB Breakout inverse implementation passed the repo audit and is a **do-not-touch** area: isolated PaperBroker, fixed 1 contract, pessimistic same-bar handling, static mirrored bracket, IOC-limit entry.
- Forward evidence resolver remains conservative; no fill-math changes are authorized.

## Futures fixes prepared for review

All items below are **unmerged** unless a later handoff update explicitly says otherwise.

| PR | Fix | Verification state |
|---|---|---|
| #397 | Normal PaperBroker/replay config parity | targeted tests + full CI passed on its reviewed head |
| #399 | Promotion gate fails closed on blockers / instrument-scoped execution claims | targeted tests + full CI passed |
| #400 | Exact five forward-campaign populations, conflicting-duplicate integrity, collector-census ownership correction | targeted tests + full CI passed |
| #401 | `project_check daily` overall blockers / false-green correction | targeted tests + full CI passed |
| #406 | Durable final no-trade suppression evidence + Discord reason visibility + existing why-no-trade diagnostic update | targeted tests + full CI passed |
| #407 | Current `TRADE_INTENT -> CANCELLED` trade-chain semantics; exact identity preferred when available | targeted trade-chain tests passed; latest exact-client-id head requires normal PR CI/review before merge |
| #408 | Persist the existing deterministic `AFS-...` client order id across intent/trade/outcome/order-id evidence | targeted webhook/order-id tests passed; normal PR CI/review required |

Older PRs #371/#374/#377/#383/#390 are not substitutes for these fixes. In particular, #374's Tradovate account pin remains conditional on box evidence showing multiple/ambiguous demo-account routing.

## Strategy evidence classifications

`docs/strategy-rules/Strategy_Inventory.md` is the strategy evidence source of truth. The September reconciliation supersedes the stale July optimistic rows with the already-proven closure results:

- ORB Reclaim current/first_cross — **BROKEN — negative evidence** (#368).
- ORB Reclaim V4-R — **WAIT** (#368 preregistered study).
- 4HR Re-Trigger MNQ — **BROKEN FOR CURRENT EXECUTABLE FORM** (#372).
- 4HR Re-Trigger MES — **BROKEN / WAIT** (#372).
- 12HR Miyagi — **BROKEN FOR CURRENT SYSTEM RISK CONSTRAINTS** (#366).
- 60M 3-2-2 First Live — **BROKEN FOR CURRENT SYSTEM RISK CONSTRAINTS** (#367).
- ORB Breakout inverted evidence lane — **PROMISING BUT UNPROVEN** (#364 historical study; runtime status must be verified on box).
- MES `strat_122` — **WAIT** (#373 executable-population audit).

Do not merge PR #369 as-is. Only its already-proven classification evidence was reconciled here; its stale runtime claims and large evidence payload were not imported.

## Monitoring architecture already verified in repo

- Most futures evidence/shadow collectors are embedded in the single `futures-bot` webhook process; they are not separate daemons.
- `scripts/evidence_lane_health.py` / `ops/evidence_lane_health.py` owns MNQ/MES event-driven lane health. Fresh feed + zero candidates can correctly be `QUIET`; candidate-file age alone must not label the lane DEAD.
- The forward campaign has exactly five configured populations: `vwap_hold/control`, `vwap_hold/modified`, `orb_reclaim/control`, `orb_reclaim/modified`, `vwap_rejection/observer`.
- Campaign enablement alone does not prove all five can produce evidence; entry-refresh, 5-minute feed, VWAP-early mode, and actual 5-minute webhook delivery are box-side prerequisites.
- Generic `feed_watchdog` and per-instrument `feed_gap_alarm` serve different purposes. Actual timer/cron installation must be verified on the box.

## Box/runtime facts still required

No current VPS shell evidence was available in this chat. Do not infer these from repository configuration alone. The next runtime pass is **READ ONLY** and must establish:

1. `futures-bot` service state, process cwd, deployed SHA/manifest, and release-integrity enforcement.
2. Nonsecret futures env pins, especially broker/demo mode, schedule mode, inverse/proof modes, fill model/tolerances, campaign prerequisites, 5-minute feed, and evidence-lane modes.
3. Fresh MNQ/MES authoritative 15-minute bars; 5-minute bars if enabled; generic webhook receipt freshness separately.
4. `scripts/evidence_lane_health.py --log-dir /root/afs-shared/logs --json` output.
5. Raw exact-five campaign counts/outcomes/days/generating SHAs and any duplicate-ID conflicts.
6. Actual systemd timers / cron for feed watchdog, per-instrument gap alarm, day-only exit, and ops automation evidence.
7. Current journal `TRADE_INTENT`, `TRADE`, `OUTCOME`, `ORDER_IDS`, `BLOCK_VISIBILITY` / suppression evidence and unresolved state.
8. Tradovate demo account list, resolved account, positions/orders, and whether account pinning is actually needed.

## Safe next step

Review the isolated futures PRs. After approved fixes land, perform one read-only VPS evidence pass against the then-current `main` SHA and update **this same handoff** with the resulting proof. Do not create another current-state handoff.
''', encoding="utf-8")

# Validate the reconciliation is internally non-contradictory for the corrected strategies.
check = inv.read_text(encoding="utf-8")
required = [
    "ORB Reclaim — current/first_cross (MNQ+MES)",
    "BROKEN FOR CURRENT EXECUTABLE FORM",
    "MNQ 0/8, MES 2/10 fit `max_stop_ticks`",
    "n=34 / 0 fill",
    "ORB Breakout — inverted (MNQ evidence lane)",
    "MES 1-2-2 (`strat_122`)",
]
for token in required:
    if token not in check:
        raise SystemExit(f"missing reconciled inventory token: {token}")
for stale in (
    "### ORB Reclaim — MES\n**Verdict: PAPER PROOF**",
    "### ORB Reclaim — MNQ\n**Verdict: PROMISING BUT UNPROVEN**",
    "### 12HR Miyagi\n**Verdict: PROMISING BUT UNPROVEN**",
    "### 60M 3-2-2 First Live\n**Verdict: PROMISING BUT UNPROVEN**",
):
    if stale in check:
        raise SystemExit(f"stale profile survived: {stale}")

for raw in (
    "scripts/_chatgpt_reconcile_futures_docs.py",
    ".github/workflows/chatgpt-reconcile-futures-docs.yml",
):
    p = Path(raw)
    if p.exists():
        p.unlink()
