# Build a Paper-Trading Automation System

## Detailed Course Syllabus

### Course Goal

Build, test, and deploy a paper-only trading operations system that turns structured market alerts into auditable strategy decisions, independently risk-checks them, simulates execution, records outcomes, and exposes operational status through a dashboard.

### Recommended Pace

| Format | Schedule |
|---|---|
| Self-paced | 6-10 weeks |
| Cohort | 8 weeks |
| Intensive workshop | 4-5 full days plus capstone |

### Completion Standard

Students complete the course by demonstrating a working paper-trading system and passing the capstone verification checklist.

---

## Module 0: Orientation and Safety

**Estimated time:** 1-2 hours

### Lessons

- What the system does and does not do
- Paper trading versus simulated broker trading versus live trading
- Why autonomous trading systems fail
- Separating strategy, risk, and execution
- Understanding the repository structure

### Assignment

Create a personal system-safety checklist and identify which actions the system must never take automatically.

### Deliverable

A written safety policy defining paper-only behavior, allowed instruments, allowed sessions, and maximum risk limits.

### Completion Check

- Student can explain the full pipeline
- Live trading remains disabled
- Safety rules are documented

---

## Module 1: Python Project Foundations

**Estimated time:** 2-4 hours

### Lessons

- Python environments and dependencies
- Modules, dataclasses, and interfaces
- Environment variables and secrets
- JSON, YAML, and JSONL
- Running commands and tests

### Assignment

Install dependencies, run the sample market-state decision, and run the test suite.

### Deliverable

A working local development environment.

### Completion Check

- Application runs locally
- Configuration loads successfully
- Tests execute successfully
- Secrets are not committed to source control

---

## Module 2: Designing the Market Data Contract

**Estimated time:** 3-5 hours

### Lessons

- What a market-data contract is
- Required versus optional alert fields
- OHLC and timestamp validation
- Ticker normalization
- Session detection
- Timeframe and freshness requirements
- Handling missing and contradictory data

### Assignment

Create valid, invalid, stale, and contradictory sample alert payloads.

### Deliverable

A documented alert schema and validation test set.

### Completion Check

- Valid payloads are accepted
- Invalid payloads are rejected
- Stale data cannot create a trade
- Missing optional fields do not crash the system

---

## Module 3: Receiving TradingView Webhooks

**Estimated time:** 3-5 hours

### Lessons

- HTTP requests and webhook basics
- FastAPI routes
- Request validation
- Webhook-secret authentication
- TradingView alert-message templates
- Testing with curl
- Local tunnels and public HTTPS URLs

### Assignment

Send an authenticated test alert into the local webhook server.

### Deliverable

A working `/webhook/alert` route that records the latest valid payload.

### Completion Check

- Correct secret is accepted
- Incorrect or missing secret is rejected
- Malformed requests do not reach strategy logic
- Health and status endpoints respond

---

## Module 4: Building Canonical Market State

**Estimated time:** 3-5 hours

### Lessons

- Why raw alerts should not directly control strategy logic
- Building a canonical `MarketState`
- Deriving sessions from timestamps
- Deriving conservative fallback context
- Normalizing instruments and timeframes
- Preserving raw payloads for audits

### Assignment

Convert multiple alert variations into consistent market-state objects.

### Deliverable

A tested market-state builder.

### Completion Check

- Equivalent payloads create equivalent state
- Unsupported instruments are rejected
- Derived fields are conservative
- Raw and derived data remain distinguishable

---

## Module 5: Strategy Engine

**Estimated time:** 4-7 hours

### Lessons

- Deterministic strategy rules
- Separating classification from trade approval
- Setup direction, entry, stop, and target
- Calculating risk/reward
- Returning `TRADE`, `NO_TRADE`, `WAIT`, and `DONE_FOR_DAY`
- Avoiding hidden discretionary behavior

### Assignment

Implement or configure one complete strategy concept.

Possible concepts:

- ORB reclaim
- ORB rejection
- VWAP reclaim
- Previous day high/low reclaim
- Continuation pullback
- Strat pattern trigger

### Deliverable

A strategy that produces structured setup decisions from known market states.

### Completion Check

- Same input always produces the same decision
- Trade setup contains entry, stop, target, direction, and R:R
- No-setup conditions return a clear reason
- Strategy does not bypass risk rules

---

## Module 6: Independent Risk Engine

**Estimated time:** 4-7 hours

### Lessons

- Why risk must be separate from strategy
- Allowed instruments and sessions
- Maximum trades per day
- Consecutive-loss limits
- Maximum daily loss
- One-position limits
- Minimum R:R
- Bracket requirements
- Kill switches and fail-closed behavior

### Assignment

Implement a risk rule and write tests proving it blocks invalid trades.

### Deliverable

A deterministic risk engine that returns `APPROVED` or `REJECTED` with a reason.

### Completion Check

- Every required rule has a test
- First failure is clearly reported
- Missing or stale data fails closed
- Strategy cannot override rejection

---

## Module 7: Paper Execution and Position State

**Estimated time:** 3-6 hours

### Lessons

- Broker-interface design
- Paper broker versus broker simulation
- Bracket orders
- Entry, stop, and target simulation
- Open-position state
- Fill assumptions and their limitations
- Handling ambiguous candles

### Assignment

Run approved setups through the PaperBroker and resolve win, loss, and open outcomes.

### Deliverable

A paper-execution adapter with tested bracket behavior.

### Completion Check

- Rejected trades cannot execute
- Approved setups create simulated orders
- Open-position limits are enforced
- Outcomes are recorded consistently

---

## Module 8: Journaling and Daily Review

**Estimated time:** 3-5 hours

### Lessons

- Append-only journal design
- Recording raw context and derived decisions
- Recording risk checks and outcomes
- Deduplicating repeated webhook events
- Reconstructing daily state
- Building morning and end-of-day summaries

### Assignment

Generate a complete daily journal containing approved and rejected decisions.

### Deliverable

An auditable JSONL journal and daily summary report.

### Completion Check

- Every processed alert creates an explainable record
- Journal survives application restarts
- Daily state can be reconstructed
- Duplicate alerts do not create duplicate trades

---

## Module 9: Replay and Regression Testing

**Estimated time:** 4-7 hours

### Lessons

- Why replay is different from profitability backtesting
- Converting candle exports into replay data
- Reusing the live decision pipeline
- Expected-outcome fixtures
- Regression testing strategy and risk changes
- Identifying overtrading and session conflicts

### Assignment

Replay at least one historical session and investigate every trade decision.

### Deliverable

A replay report showing alerts, decisions, risk results, and outcomes.

### Completion Check

- Replay completes without crashing
- Results are reproducible
- Unexpected decisions are investigated
- Strategy or risk changes have regression tests

---

## Module 10: Notifications and Operational Monitoring

**Estimated time:** 2-4 hours

### Lessons

- Discord webhook notifications
- Decision notifications versus system alerts
- Health endpoints
- Detecting stale webhook feeds
- Avoiding notification spam
- Operational status versus trading status

### Assignment

Send a test notification and simulate a stale or broken alert feed.

### Deliverable

A notification and monitoring layer that distinguishes healthy, warning, and failure states.

### Completion Check

- Notifications contain useful context
- Notification failures do not crash trading logic
- Stale feeds are visible
- Health status does not falsely claim fresh data

---

## Module 11: Dashboard

**Estimated time:** 4-8 hours

### Lessons

- Designing an operational trading dashboard
- Displaying latest webhook state
- Displaying strategy and risk status
- Displaying positions and recent decisions
- Separating read-only monitoring from control actions
- Handling loading, stale, empty, and error states

### Assignment

Build or customize a dashboard view that explains the system's current state.

### Deliverable

A read-only dashboard showing the system's most important operational data.

### Completion Check

- Dashboard works on desktop and mobile
- Stale and missing data are clearly labeled
- No sensitive secrets are displayed
- Dashboard does not imply that paper fills are real fills

---

## Module 12: Deployment

**Estimated time:** 4-7 hours

### Lessons

- Local deployment limitations
- Tunnels for development
- Environment variables and secret management
- Railway-style deployment
- VPS deployment with systemd and nginx
- Process supervision
- HTTPS, health checks, logs, and restarts

### Assignment

Deploy the paper-trading webhook and verify it survives a restart.

### Deliverable

A publicly reachable, authenticated paper-trading webhook with health monitoring.

### Completion Check

- Webhook is reachable through HTTPS
- Secrets are stored outside source control
- Process restarts automatically
- Health endpoint is externally reachable
- Deployment remains paper-only

---

## Module 13: Advanced Expansion Options

**Estimated time:** 4-10+ hours

### Lessons

- Moving from JSONL to SQLite or Postgres
- Adding a React or Next.js dashboard
- Adding user authentication
- Designing broker adapters
- Tradovate and IBKR simulation concepts
- AI-assisted summaries and journal review
- Why live execution is a separate project

### Assignment

Choose and implement one advanced extension without weakening risk controls.

### Deliverable

One documented advanced feature with tests and operational notes.

### Completion Check

- Feature has a clear ownership boundary
- Existing risk behavior remains unchanged
- New dependencies and costs are documented
- Failure behavior is tested

---

## Final Capstone

**Estimated time:** 8-15 hours

### Scenario

Demonstrate a complete paper-trading day from alert ingestion through review.

### Required Demonstration

1. Receive an authenticated TradingView-style alert
2. Reject a malformed alert
3. Reject stale market data
4. Produce a valid strategy setup
5. Reject at least one setup through the risk engine
6. Approve and paper-execute at least one setup
7. Resolve or display the simulated position
8. Write complete journal records
9. Send a Discord notification
10. Display system state on the dashboard
11. Replay a historical session
12. Run automated tests

### Final Deliverables

- Working source code
- Configuration and safety policy
- Alert-payload documentation
- Replay report
- Example daily journal
- Dashboard screenshots
- Deployment runbook
- Short system demonstration video

### Capstone Evaluation

| Area | Weight |
|---|---:|
| Safety and risk enforcement | 25% |
| Correctness and testing | 20% |
| Auditability and journaling | 15% |
| Replay and reproducibility | 15% |
| Dashboard and operations | 10% |
| Deployment reliability | 10% |
| Documentation | 5% |

---

## Student Tracks

### Operator Track

Students configure and operate the supplied system.

Focus:

- TradingView alerts
- Risk configuration
- Replay
- Journals
- Dashboard
- Deployment

### Builder Track

Students implement and understand the system module by module.

Focus:

- Python
- FastAPI
- Strategy logic
- Risk engine
- Paper execution
- Testing

### Advanced Track

Students extend the system while preserving its safety model.

Focus:

- Databases
- Frontend applications
- Broker simulation
- AI review
- Production operations

---

## Suggested Course Package

The complete course package should eventually include:

- Student starter repository
- Completed instructor repository
- Video lessons
- Written lesson notes
- Architecture diagrams
- Assignments and answer keys
- Replay datasets
- Automated tests
- Deployment checklist
- Risk and safety checklist
- Community or support channel

---

## Educational and Risk Disclaimer

This course is for educational and paper-trading purposes. It does not provide financial advice, promise profitability, or guarantee reliable execution.

Trading futures and other leveraged products involves substantial risk. Connecting educational software to a live brokerage account requires additional safeguards, testing, monitoring, permissions, and professional review beyond this syllabus.

