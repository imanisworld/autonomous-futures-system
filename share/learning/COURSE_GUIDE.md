# Course Guide — Detailed Reference

The full curriculum, technology options, costs, and build path. For the short
pitch see **[COURSE_OVERVIEW.md](COURSE_OVERVIEW.md)**.

**Pace:** self-paced 6–10 weeks · cohort 8 weeks · intensive 4–5 days + capstone.
**Completion:** a working paper-trading system that passes the capstone checklist.

## Core principles

- **Paper first** — start and finish safely in paper mode; live execution is never required.
- **Risk is independent** — the strategy *suggests* a trade; the risk engine *decides* whether it's allowed.
- **Deterministic before intelligent** — the decision/risk path is explicit, testable rules. AI may review journals, never override risk.
- **Every decision is auditable** — inputs, reasons, approvals, rejections, simulated fills, and outcomes are all recorded.
- **Replay before trust** — changes are tested against historical candles before being trusted live.

---

## Curriculum

Each module = short concept lessons → guided implementation → a deliverable →
a completion check.

| # | Module | Time | Deliverable |
|---|---|---|---|
| 0 | Orientation & Safety | 1–2 h | Written safety policy (paper-only, allowed instruments/sessions, max risk) |
| 1 | Python Project Foundations | 2–4 h | Working local dev env; tests run; secrets not committed |
| 2 | Market Data Contract | 3–5 h | Documented alert schema + validation tests (valid/invalid/stale) |
| 3 | TradingView Webhooks | 3–5 h | Authenticated `/webhook/alert` route recording the latest payload |
| 4 | Canonical Market State | 3–5 h | Tested state builder (normalizes instrument/session/timeframe/OHLC) |
| 5 | Strategy Engine | 4–7 h | A strategy returning structured `TRADE`/`NO_TRADE` setups |
| 6 | Independent Risk Engine | 4–7 h | Deterministic engine returning `APPROVED`/`REJECTED` + reason |
| 7 | Paper Execution & Position State | 3–6 h | Paper-broker adapter with tested bracket behavior |
| 8 | Journaling & Daily Review | 3–5 h | Auditable JSONL journal + daily summary |
| 9 | Replay & Regression Testing | 4–7 h | Replay report (alerts, decisions, risk results, outcomes) |
| 10 | Notifications & Monitoring | 2–4 h | Notification/health layer separating healthy/warn/fail states |
| 11 | Dashboard | 4–8 h | Read-only dashboard of webhook/risk/strategy/position state |
| 12 | Deployment | 4–7 h | Reachable, authenticated paper webhook that survives restart |
| 13 | Advanced Expansion | 4–10+ h | One extension (DB, React UI, broker adapter, AI review) without weakening risk |

### Key teaching points per module

- **M2** — required vs optional fields, OHLC/timestamp validation, ticker
  normalization, session detection, freshness; stale data must never create a trade.
- **M5** — separate *classification* from *trade approval*; same input → same
  decision; no hidden discretion. Concepts: ORB reclaim/rejection, VWAP
  reclaim/hold, PDH/PDL reclaim, continuation pullback, Strat triggers.
- **M6** — the most important module. Allowed instruments/sessions, max
  trades/day, consecutive-loss & daily-loss limits, one open position, min R:R,
  bracket required, fail-closed on missing/stale data. Strategy can't override.
- **M9** — replay ≠ profitability backtesting. Reuse the *live* decision
  pipeline on historical candles to catch overtrading, session conflicts, and
  regressions from strategy/risk changes.
- **M12** — env/secret management, tunnels for dev, Railway or VPS (systemd +
  nginx), process supervision, HTTPS, health checks; deployment stays paper-only.

---

## Capstone

Demonstrate a complete paper-trading day:

1. Receive an authenticated TradingView-style alert
2. Reject a malformed alert
3. Reject stale market data
4. Produce a valid strategy setup
5. Reject at least one setup via the risk engine
6. Approve and paper-execute at least one setup
7. Resolve or display the simulated position
8. Write complete journal records
9. Send a Discord notification
10. Display system state on the dashboard
11. Replay a historical session
12. Run automated tests

**Deliverables:** source code, config + safety policy, alert-payload docs,
replay report, example journal, dashboard screenshots, deployment runbook, short
demo video.

**Grading:** Safety/risk 25% · Correctness/testing 20% · Auditability 15% ·
Replay/reproducibility 15% · Dashboard/ops 10% · Deployment 10% · Docs 5%.

---

## Stack & build options

The current v1 stack is simple enough to teach but serious enough to run as a
real paper-trading ops system. Alternatives below are for the Advanced track —
**don't adopt them until replay, risk, and journaling are solid.**

**Signal source** — TradingView Pine (default, fastest) · broker market data
(more backend control) · CSV/replay only (teaching) · hybrid TV + backend
enrichment (serious paper) · full backend scanner (advanced). *Path: TV webhooks
→ replay → backend-owned data later.*

**Backend** — FastAPI (default; best for trading logic/tests/JSON/AI) · Flask
(simpler, less structured) · Node/Express · Next.js full-stack (great UI, more
complex early) · Go/Rust (overkill for a first course). *Keep FastAPI.*

**Execution** — PaperBroker (start here) → Tradovate simulated (futures micros)
→ IBKR/Alpaca paper → advisory-only (safest product mode) → guarded live
(advanced only). *Never teach live until replay/journaling/risk/monitoring are strong.*

**Storage** — JSONL (simple, auditable v1) → SQLite (local structured history) →
Postgres/Supabase (multi-user analytics). Redis for transient state; S3/R2 for
long-term replay archives.

**Dashboard** — FastAPI embedded HTML (v1) → React/Vite or Next.js (polished
capstone) → mobile later. Add auth only if it becomes a product.

**Notifications** — Discord (free, easy, default) · Telegram (mobile UX) ·
SMS/Twilio (urgent) · Email/Resend (reports) · Slack (teams).

**Hosting** — local Mac + tunnel (learning) · Railway/Render/Fly (easy app
hosting) · Hetzner VPS (cheap, controllable) · Vercel for a Next.js frontend with
the Python backend elsewhere.

---

## Costs (monthly, software/infra)

| Item | Low | High | Notes |
|---|---:|---:|---|
| Python / FastAPI / code | $0 | $0 | Open source |
| TradingView | $0 | $15–$60+ | Paid plans for serious alerts |
| Tunnel | $0 | $10–$20 | Cloudflare/ngrok/localtunnel |
| VPS / app hosting | $5 | $30+ | Hetzner/Railway/Render/Fly |
| Domain | ~$1 | ~$3 | ($12–$30/yr) |
| Discord | $0 | $0 | Webhooks free |
| Database | $0 | $25+ | JSONL/SQLite free; Postgres later |
| AI summaries | $0 | $5–$30 | Optional, usage-based |
| Broker platform/data | $0 | $100+ | Depends on broker/data subs |

Real trading capital is planned separately from software cost — the risk plan
matters far more than the number.

---

## AI: around the system, not inside the risk path

**Good uses:** daily summaries, trade grading, explaining rejections, pattern
review, journal analysis, a course tutor.
**Avoid early:** live entry decisions, risk overrides, unlogged discretionary
trades, broker execution without deterministic guards.
**Principle:** *AI reviews the system; it does not control the risk engine.*

---

## Recommended build path

1. Keep FastAPI, TradingView, JSONL, Discord, PaperBroker.
2. Make the alert payload and state builder extremely clear.
3. Strengthen replay and tests.
4. Polish the dashboard.
5. Deploy to Railway or Hetzner.
6. Add SQLite/Postgres only when querying JSONL gets painful.
7. Add AI summaries after journaling is reliable.
8. Add Tradovate simulation only after replay and risk controls are solid.
9. Treat live execution as a separate, advanced safety project.

---

## Product shapes (if you sell it)

The cleanest first product: **a paper-only course + repo template + dashboard**.

| Product | Description |
|---|---|
| Developer course | Build the full Python system |
| Trader course | Focus on setups, risk, replay, operation |
| Template kit | Repo + videos + setup guide + checklists |
| SaaS dashboard | Users connect their own TradingView webhooks |
| Private ops tool | Internal trading command center |
| Community lab | Students submit replay journals for review |

**To package as a course:** create a sanitized student starter repo (skeleton,
sample payloads, paper-only config, failing module tests, guided TODOs, replay
fixtures) plus a completed instructor repo (full implementation, answer keys,
checkpoint tags). Then add lessons, assignments, and a small beta cohort.

---

*Educational / paper-trading only. Not financial advice; no guarantee of
profitability or reliability. Connecting to a live brokerage requires additional
safeguards, testing, monitoring, permissions, and professional review beyond this
guide.*
