# Backlog — Tasks, Fixes & Ideas

Living tracker for what's done, what's next, and ideas worth keeping.
Add freely. Keep `live_trading_enabled: false` until explicitly decided otherwise.

**Status legend:** `[ ]` todo · `[~]` in progress · `[x]` done · `[?]` idea / needs decision

_Last updated: 2026-06-03_

---

## 🔴 Known issues / immediate

- [ ] **MES alerts never reach the backend.** Dashboard shows `LIVE PRICE • MES —
  Waiting for first TradingView bar…` while only `MNQ1!` webhooks arrive. This is a
  **TradingView alert-config problem on the MES chart**, not backend tuning — MES is
  not being rejected, it's never sent.
  - Confirm an alert actually exists on the MES chart.
  - Condition: `RiskSentinel — Full Context` → `Any alert() function call`.
  - Same webhook URL + secret as the MNQ alert.
  - Check it isn't expired / paused; expiry should be open-ended.
  - Confirm the chart symbol is MES (webhook normalizes `MES1!` → `MES`).

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

## 🧹 Cleanup (after testing)

- [ ] Remove temporary **Evening Test** windows once testing is done:
  - `risk_rules.yaml` → `session_windows.asian` (17:00–22:00 ET test window).
  - Pine `Evening Test Session (ET)` input (`1700-2200`) in `risksentinel_context.pine`.

---

## ✅ Done

- [x] Diagnosed "MES isn't triggering, MNQ is" → MES alerts never reach the backend;
  MNQ heartbeats arrive and correctly return NO_TRADE (no active setup). (2026-06-03)

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
