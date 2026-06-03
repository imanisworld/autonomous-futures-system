# Backlog — Tasks, Fixes & Ideas

Living tracker for what's done, what's next, and ideas worth keeping.
Add freely. Keep `live_trading_enabled: false` until explicitly decided otherwise.

**Status legend:** `[ ]` todo · `[~]` in progress · `[x]` done · `[?]` idea / needs decision

_Last updated: 2026-06-03_

---

## 🔴 Known issues / immediate

- [~] **"MES isn't triggering" — RESOLVED as not-a-bug.** The LOG tab shows MES *is*
  arriving and being evaluated (e.g. 11:10/11:15/11:20 MES entries). MNQ is rejected at
  the instrument gate (`not in allowed universe`, by design); MES returns NO_TRADE for
  legit reasons (range-bound tape, time-window gates). The `LIVE PRICE • MES — Waiting
  for first TradingView bar…` panel is just a separate dashboard quirk, not proof MES is
  missing. Watch item: confirm the dashboard LIVE PRICE panel binds to the right ticker.

---

## 🟡 Planned work

### Separate MNQ and MES into their own lanes (the big one)
- [ ] **Goal:** each instrument gets its own place — config, journal, and dashboard
  view — so they don't collide. This is also the *only* clean way to run both at once
  (see architecture note below).
- [ ] **Separate journals** so P&L, daily limits, and open-position state don't mix
  between instruments. (Today `JournalLogger` is keyed by date, not instrument.)
- [ ] **Per-instrument config / sizing ladder** so each has its own tiers + contract
  caps instead of fighting over one balance band.
- [ ] **Separate dashboard views** — one page/state per instrument (the LIVE PRICE
  panel already hints at this split).
- [ ] Test thoroughly on this branch before deploying — touches core config + runner.
- Notes: defer until current MES validation is sorted. Won't break the current setup
  if done deliberately.

### Pre-market H/L level + late-window soft-open  ← TEST LATER TODAY
- [ ] **Add a true pre-market high/low level.** The system has no overnight-range
  reference today — only ORB (NY first 15m) and PDH/PDL. The one "pre-market" mention
  (`strat_4hr_retrigger`, `signal_engine.py:1637`) just *proxies* it with the ORB high.
  Pre-market H/L captures overnight positioning (Europe/Asia + data) and its extremes
  are real liquidity levels.
- [ ] **Use it to soft-open the late window (14:00–16:00 ET).** Today `late` is
  `allow: "none"` (closed) — avoids post-lunch chop AND end-of-day/MOC tail risk. Don't
  blanket-open it; instead make it `restricted` and only admit a **pre-market level
  break + retest that holds** (level flips role) with trend/VWAP confirm. The *retest*
  requirement is what filters late-day fakeouts. (Narrower alt: open 15:00–15:45 only,
  skip the 15:45–16:00 MOC zone.)
- [ ] **Build order:** (1) Pine computes premarket H/L (e.g. 04:00–09:30 ET) → add
  `premarket_high`/`premarket_low` to alert JSON; (2) add fields to
  `market_state.schema.json` + `context/market_context.py` + `webhook/state_builder.py`;
  (3) new `premarket_break_retest` strategy (usable as the late-window unlock *and* a
  general setup); (4) **backtest on replay data carrying PMH/PML, measure WR/PF, then
  decide live.**
- [ ] **⚠️ Two-layer 14:00 cutoff — relax BOTH or it won't work.** The cutoff is enforced
  twice: signal engine `late: none` (`signal_engine.py` WINDOWS) **and** the risk engine
  `_check_session_cutoff` (`risk_engine.py:146`, reading `session_cutoffs_et.new_york:
  14:00`). Soft-opening the signal-engine window alone → the decision engine approves and
  the risk engine then rejects (dead-end). Must relax both in sync.
- **⚠️ Backtest sequencing + data:** can't backtest yet — PMH/PML aren't in the current
  historical payloads, and the sample replay data is **RTH-only (starts 09:30, no
  pre-market bars)**. To get usable data, export the TradingView CSV with **Extended
  Trading Hours (ETH) ON** before running `scripts/csv_to_replay.py` — RTH export has no
  04:00–09:30 bars to compute the level from. Build feature first, then validate on
  replay. No WR claim until that runs. (Replay engine: `replay/` +
  `scripts/run_replay_batch.py`.)

---

## 💡 Ideas / possible fixes & improvements

- [?] **Revisit MES disabled concepts.** MES disables `vwap_reclaim`, `orb_reclaim`,
  `pdl_reclaim` (`risk_rules.yaml`). Worth re-checking on fresh data whether any are
  worth re-enabling — but only after MES is actually feeding the box.
- [?] **MNQ tuning before enabling.** MNQ has never been backtested the way MES was. If
  it goes live, it runs the *full* `enabled_concepts` set, including `vwap_reclaim`
  (flagged as a loose 40% WR strategy on MES). Tune before trusting MNQ trades.
- [?] **Dashboard: show webhook source per instrument** so it's obvious at a glance
  which instruments are actually sending data (would have caught the MES gap instantly).
- [?] **Alert health / heartbeat monitor** — warn if no bar received from an expected
  instrument within N minutes during an active session.

---

## 🔬 Validation & observability

- [ ] **"Why NO_TRADE" daily tally (highest leverage, low effort).** Today's whole pain
  was *"why didn't it trade?"* — answered by squinting at screenshots. The reason is
  already recorded per bar (`failed_gates` + `reason` in the journal). Build a daily
  rollup — e.g. "62% window-blocked, 20% trend-strength, 12% chop, 6% volume" — so it's
  instantly clear which gate is starving trades and whether tuning does what we think.
  Makes every future tuning call (incl. pre-market) evidence-based. Do this FIRST.
- [ ] **In-market shadow / dry-run test lane (doesn't interrupt the live code).** A way
  to evaluate a candidate config or strategy against the *same live incoming alerts* in
  parallel, without touching the production journal or placing orders. Feasible because
  `process_alert(payload, config=..., log_dir=...)` is already parameterized: tee each
  webhook to a second evaluation with the candidate config + a separate `log_dir`
  (e.g. `logs_shadow/`) and execution disabled. Lets us A/B changes (mid_early open vs
  restricted, pre-market on/off) on live bars safely before promoting them.
- [ ] **Backtest the `mid_early` open we already shipped.** It was changed on reasoning,
  not data. Once replay data covers 10:45–11:30, run `restricted` vs `all` and confirm
  it helps (or at least doesn't hurt) WR/PF. Low risk (paper, downstream gates intact)
  but shouldn't stay unvalidated.
- [ ] **Validation throughput.** The live paper lane is very selective (3/day cap,
  STRONG-trend, disabled concepts, time windows) → potentially few fills, slow to
  validate execution/journal/broker plumbing. Lean on replay (and the shadow lane above)
  to exercise the pipeline rather than waiting on rare live fills.
- [ ] **Fix dashboard LIVE PRICE binding.** Panel showed "MES" while ticker was `MNQ1!` —
  actively misled diagnosis today. Bind it to the actual incoming ticker.

---

## 🧹 Cleanup (after testing)

- [ ] Remove temporary **Evening Test** windows once testing is done:
  - `risk_rules.yaml` → `session_windows.asian` (17:00–22:00 ET test window).
  - Pine `Evening Test Session (ET)` input (`1700-2200`) in `risksentinel_context.pine`.

---

## ✅ Done

- [x] **Opened NY `mid_early` window (10:45–11:30 ET) from `restricted` → `all`.**
  Setups in that window now run the standard pipeline; downstream `require_strong_trend`
  (MES/MNQ true), market-condition, volume, R:R, and daily-limit gates still apply, so
  core safety is unchanged. Updated `test_1050_mid_early` test; full suite green
  (488 passed). (2026-06-03)
- [x] Confirmed **shorts are fully wired** (vwap_rejection, vwap_hold, orb_rejection,
  orb_breakout short, continuation/Strat). The noon MES drop wasn't a missed short — the
  morning was range-bound (no DOWN trend) and the break printed in the 11:30–12:00 lunch
  block (`mid_late: none`, hard-closed). Note: `pdl_reclaim` (a breakdown short) is
  disabled for MES. (2026-06-03)
- [x] Diagnosed "MES isn't triggering, MNQ is" → MES *is* evaluated; MNQ rejected at the
  instrument gate by design. (2026-06-03)

---

## 📌 Architecture notes (so we don't relearn these)

- **Alerts are per-bar heartbeats**, not per-setup. The Pine fires every confirmed bar
  close during an active session (`should_send` in `risksentinel_context.pine`). Each
  alert is tagged `signal` (a setup exists) or `heartbeat`. The backend decides
  TRADE / NO_TRADE.
- **Single-instrument-per-balance-band sizing.** `position_sizing.sizing_rules` maps
  each balance band to exactly one instrument. `_check_position_sizing`
  (`risk/risk_engine.py`) rejects any instrument that isn't the one owning the current
  balance band. **You cannot run MES and MNQ at the same time** under this design — hence
  the "separate lanes" work above.
- **Three gates block a new instrument:** (1) `instruments.allowed`, (2)
  `max_contracts_per_instrument` > 0, (3) a matching `position_sizing` tier. All three
  must be set, not just the first.
