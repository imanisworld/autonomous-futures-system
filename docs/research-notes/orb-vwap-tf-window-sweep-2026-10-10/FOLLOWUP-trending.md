# Follow-up — TRENDING filter ± MNQ ORB stop 48 ticks (2026-10-10)

Same windows and TFs as `README.md`. **IOC** fills unchanged.

## A) `--require-trending` only

- **File:** `results-trending.json`
- **Effect:** Drops non-`TRENDING` bars via `_score_market_condition` before bracket walk.
- **Result:** All reported cells stay **negative or flat** (no resolved afternoon 15m ORB). The prior **10m reclaim** green slice **does not survive** trending (+$19 → morning **−$356** on 18 resolved).

## B) `--require-trending` + `--mnq-orb-stop-ticks 48`

- **File:** `results-trending-orb48.json`
- **Effect:** Wider ORB boundary stop offset for MNQ (48 ticks vs engine default 8 when unset in yaml).
- **Result:** Still **no positive morning/afternoon window** with meaningful n. Reclaim losses shrink in some cells but remain net negative.

**DO NOT REDO** unless script flags or corpora change.
