# DRAFT — not registered, for Operator (Imani) review then Claude

## Change log
- **v7, 2026-11-11 ~16:50 ET.** PM ruling: the MNQ caution is descriptive, not a decision rule. Only the caution was rewritten.
  - Base case is now the measured in-sample IOC fill rate, 20/34 = 0.588: 77 → ≈ 45 fills → ≈ 43 resolved → **37/43** wins needed at BE 0.738, verified with Clopper-Pearson.
  - 0.41 is kept only as a labeled stress case: ≈ 30 resolved, needing 27/30 or 28/31.
  - The text states that the real fill rate is unknown until scoring, and notes that Claude's #1274 "~41% fill" was the no-fill rate.
  - The v6 flagged note is folded in.
  - Pre-edit copy: `PREREG_322_SLICE_SUCCESS_DRAFT_2026-11-11_v6_pre.md` (sha256 ef704fe2…2a8d). Diff: `PREREG_322_SLICE_SUCCESS_DRAFT_2026-11-11_v6_to_v7.diff`.
- **v6, 2026-11-11 ~16:45 ET.** One correction, per the PM review of #1276 (**AFS-0633**, confirmed verbatim in the ledger).
  - MNQ is 79/79 before unscored-day removal and **77/77 after**. Two candidates fall on Juneteenth early closes (2022-06-20, 2024-06-19) and are excluded under `EARLY_CLOSE_DISPOSITION=exclude`.
  - Updated: exposure (c); A5 expectations (NQ 95→90 vs 90 with 5 drops, 92→88 vs 88 with 4; MNQ 0 drops at 79/79 and 77/77; #1276 reports gap and non-gap drop counts in the artifact and SLICE event); and the MNQ caution, redone at 77.
  - Flag: the "0.41" in the caution is the in-sample **no-fill** rate, not the fill rate. See the caution.
  - Nothing else changed. Pre-edit copy: `PREREG_322_SLICE_SUCCESS_DRAFT_2026-11-11_v5_preClaude4.md` (sha256 77689e92…d3e0). Diff: `PREREG_322_SLICE_SUCCESS_DRAFT_2026-11-11_v5_to_v6.diff`.
- **v5, 2026-11-11 ~16:25 ET.** Edits from Claude via Imani, routed through PM.
  - (1) T16 note: Claude independently recomputed the T16 values from the committed gate replay bytes. Strategy Lab did not recompute them.
  - (2) New amendment **A5 (AFS-0629/AFS-0630)**: drop every research candidate whose entry is not strictly inside its bracket. This matches `strategy/strat_322_first_live.py`. Note: :191 is `stop = …`; the check itself is :193-198. Also added: per-slice dropped counts, the gate precondition (34 candidates / 306 records / 0 mismatches / 0 invalid), the N1 test in the same code PR, and an honest post-exposure note marked for QA as a judgement call.
  - (3) Added exposure disclosure (c): the #1274-review detection counts and the five zero-width NQ rows.
  - (4) Status: Q4 closed (#1272 = e9e1d041, verified on main). CONFLICT-1 RESOLVED: #1274 merged as e5d3b780, verified on GitHub (AFS-0630).
  - (5) MNQ caution: ~79 candidates × ~41% fill ≈ 32 fills ≈ ~30 resolved IOC trades, right at the 30 minimum. The earlier 35-40 and ~49 estimates are marked superseded.
  - Pre-edit copy: `PREREG_322_SLICE_SUCCESS_DRAFT_2026-11-11_v4_preClaude3.md` (sha256 20a6993d…b3f8). Diff: `PREREG_322_SLICE_SUCCESS_DRAFT_2026-11-11_v4_to_v5.diff`.
- **v4, 2026-11-11 ~15:05 ET.** Claude review of v3 (sha 585a5d0d…): ready after 5 edits; all applied. Plus the PM steering on Q1.
  - (1) New cell order: INSUFFICIENT → FAIL (net ≤ 0) → WAIT_UNRESOLVED → FAIL_DRAWDOWN → WAIT_CI → PASS. A losing cell with more than 10% unresolved is now FAIL. Tests re-labeled, and fixture T2c added. Q8.1 resolved.
  - (2) A drawdown breach is cell status FAIL_DRAWDOWN. Overall it is FAIL, labeled "FAIL — risk profile" (not OVERFIT). No promotion; any retry is a new trial. Q8.4 resolved.
  - (3) OVERFIT is kept for a net ≤ 0 FAIL (Q8.3 resolved). The mechanical-void label was renamed BROKEN → VOID in 4 places (see the Q8 note).
  - (4) `combine_cells` refuses mismatched evaluator sha256 values (test T17). Q8.2 resolved with the separate pure combine.
  - (5) Added the pure ledger-row builder `trade_records_from_ledger` and test T16 on the recorded structural-gate replay ledger.
  - (Q1) Q1 RESOLVED by **AFS-0627**, Imani's own typed words (t966u): "q1: IOC-only decisive, my decision." AFS-0621 (the PM decision under delegation) is kept as history only.
  - Unchanged as instructed: the T11 CP values, the fixture arithmetic and the schema rules. Part 2 is unchanged except the A4 status wording.
  - Pre-edit copy: `PREREG_322_SLICE_SUCCESS_DRAFT_2026-11-11_v3_preClaude2.md` (sha256 585a5d0d…e6e1). Diff: `PREREG_322_SLICE_SUCCESS_DRAFT_2026-11-11_v3_to_v4.diff`.
- **v3, 2026-11-11 ~14:55 ET.** Claude review returned CHANGES REQUIRED; every requested change is applied.
  - (1) A gate win is now `net > 0`, and `net == 0` counts as a loss. The harness label is report-only. BE = max(BE_geom, BE_real), with BE_real taken from the net-defined split. This resolves Q2.
  - (2) A decisive cell with n ≥ 30 and net ≤ 0 is FAIL: no promotion, and any retry is a new trial.
  - (3) The IOC no-fill band is now descriptive, reported by era and price level. Unresolved above 10% of fills is a mechanical WAIT. Q6 is resolved.
  - (4) A full evaluator spec was added (1.13), which resolves Q3.
  - (5) Added the MNQ small-sample caution.
  - (6) Open items updated: CONFLICT-1 → PR #1274; Q1 → PENDING Imani's written confirmation; Claude confirmations recorded.
  - Part 1 was restructured. Part 2 text is unchanged except the A4 status line.
  - Pre-edit copy: `PREREG_322_SLICE_SUCCESS_DRAFT_2026-11-11_v2_preClaude.md` (sha256 f969e85f…726d).
- **v2, 2026-11-11 ~14:35 ET:** Q1 resolved, by PM decision under Operator delegation, Nov 11 2026. Honest IOC @3 ticks is the ONLY decisive model, giving 2 decisive cells (NQ, MNQ). Pre-armed @3 ticks becomes report-only. Updated: 1.2, 1.4 (break-even and power table now IOC-only), 1.5 (drawdown arithmetic IOC-only; the 8R limit is unchanged), 1.8 (pre-armed no-fill flag now descriptive), 1.9, 1.11, the open-items list, and the CONFLICT-1 status. Pre-edit copy: `PREREG_322_SLICE_SUCCESS_DRAFT_2026-11-11_v1_preQ1.md` (sha256 95fdf965…4848).
- **v1, 2026-11-11 ~14:25 ET:** initial draft.

AFS 3-2-2 (60M First Live, MNQ lane) — older-years slice success criteria + structural-prereg amendment
Author: Strategy Lab (research-only). Written 2026-11-11 ~14:40 ET. Nothing scored, run, pulled, committed or ledgered.

## Provenance

- Repo: imanisworld/autonomous-futures-system, main **f612049fcd891a65a0834870b698a8141af1e3b1** (#1273 merged; read-only clone).
- PR #1272: was reviewed at 09a6612caec10897d006f14fc2c3920e9f5fb1a8. The live head is now **abaa1a4b2a07a25f1580ff6b36dfd85498f0608f**, a merge of main f612049f into 09a6612c, dated 2026-11-11 14:17 ET. `research/databento_322_bar_adapter.py` is byte-identical between 09a6612c and abaa1a4b (VERIFIED, empty git diff). The PR is not merged and needs a fresh Claude PASS at abaa1a4b.
- Files read. Line numbers are at f612049f unless "@PR" is shown; @PR means abaa1a4b.
  - `docs/prereg-322-afs0523-oos-structural-2026-11-10.md` :3, :38-40, :47-49
  - `docs/prereg-322-afs0523-oos-reproduction-2026-11-09.md` :23-29, :41-55, :59-63, :69-71, :73-77, :97
  - `research/afs0523_322_repro_wrapper.py` :20, :69-71, :91-103, :119-123, :142-153, :181-211, :944-1004, :1007-1064, :1778-1799, :1823-1899, :1910-1950, :2058, :2065-2118, :2121-2199, :2202-2233 (same line numbers @PR for the functions cited)
  - `research/databento_322_bar_adapter.py`
    - main: :50 `EARLY_CLOSE_ET = (13, 0)`, :175 TODO
    - @PR: :23-46, :68-81, :86-170, :173-207, :210-227, :239-255, :282-337, :361-369, :679
  - `scripts/322_trigger_timing_ab_2026_09_18.py` :31, :96-119, :237-256
  - `scripts/edge_decomposition_audit.py` :90, :585-671
  - `execution/paper_broker.py` :106-117, :190-196, :235-300, :427-445, :569-636
  - `docs/strategy-rules/60M_322_FirstLive_Rules.md` :58-68, :89-98, :104-123, :154
  - `risk_rules.yaml` :91, :93, :161-164, :205-213, :219-220, :621
  - `research/run_322_expanded_evidence.py` :47-72; `research/bars_322_polygon_loader.py` :131-138
  - `docs/strategy-rules/60M_322_EXPANDED_EVIDENCE_2026-07-26.md` :162, :248-250
  - `docs/research-trial-ledger.jsonl` lines 12-13 and 27-32. It has 0 `SLICE` events and 0 `GATE_REVIEW` events (VERIFIED).
- Shared ledger IDs consulted (pointers only): AFS-0119, AFS-0459, AFS-0523, AFS-0571-0574, AFS-0580, AFS-0582, AFS-0586, AFS-0589, AFS-0594, AFS-0600, AFS-0610, AFS-0612, AFS-0613, AFS-0615, AFS-0616, AFS-0617, AFS-0618, AFS-0619. The multiple-testing tally of 433 comes from Strategy Lab's prior work (#432 V-A, #433 V-C).
- No market data, slice trees, slice results or result files were opened for this draft. Calendar dates below were computed from the adapter's date functions only.

---

# PART 1 — Success-criteria prereg: older-years slices

## 1.1 Scope, seal and AFS-0119 checks

| Slice id (wrapper :189-203) | Window | Source |
|---|---|---|
| `nq-2010-06-to-2019-05` | 2010-06-06 .. 2019-04-30 | Databento GLBX.MDP3 ohlcv-1m + definition, parent `NQ.FUT`. Written onto the MNQ path, labeled "MNQ-equivalent research, not executable historical MNQ" (wrapper :117) |
| `mnq-2019-05-to-2024-06` | 2019-05-01 .. 2024-06-30 | Databento GLBX.MDP3 ohlcv-1m + definition, parent `MNQ.FUT` |

- **Shared provenance for both slices:**
  - front contract via `front_contract(roll_days=8)` on the civil ET date (reproduction prereg :59);
  - pinned manifest `6c9d5ab3…` (wrapper :86); DOWNLOAD_RECORD `4b1ad258…` (AFS-0573);
  - slice tree hashes must match that manifest's own window entry (`_refuse_slice_data_mismatch`, wrapper :2114).
- **MNQ seal (AFS-0459): no overlap (VERIFIED).** The latest slice date is 2024-06-30 (wrapper :194), and the reproduction window ends 2026-06-26 (:114). Both are before 2026-06-29. Registered guard: any row dated 2026-06-29 or later in a slice tree is a refusal, not a filter.
- **AFS-0119: no re-slice conflict found (LIKELY).** Every 3-2-2 population in the repo sits in 2024-07-01 .. 2026-07-23:
  - `60M_322_EXPANDED_EVIDENCE` :248-250;
  - trial ledger lines 12-13 (population 2024-08-02..2026-06-11);
  - the structural gate (2024-07-02..2026-06-26).
  - The ledger grep found no 3-2-2 outcome on 2010-2024 data.
- **Disclosed exposure, none of it outcome-level:**
  - (a) PM read slice-tree DATA_GAP and early-close counts (AFS-0574, -0617, -0618: MNQ 7 gaps / 33 early closes, NQ 26 / 39). These are data-quality counts, not trades.
  - (b) The Strat pattern's provenance is unknown (AFS-0523), so its author may have seen these years. This is why NQ is "best available evidence, not fully clean".
  - **(c) Candidate-level exposure during the #1274 review (v5 disclosure).** Detection was run on the slice data.
    - Counts:
      - NQ: **95 research candidates vs 90 state-machine candidates**, or **92 vs 88** after unscored-day removal;
      - MNQ: **79 vs 79**, or **77 vs 77** after unscored-day removal. The two removed candidates are on Juneteenth early closes, 2022-06-20 and 2024-06-19, excluded under `EARLY_CLOSE_DISPOSITION=exclude` (AFS-0633).
    - It also exposed the entry, stop and target levels of five NQ zero-width rows (trigger == target):
      - 2013-07-16
      - 2014-05-26
      - 2016-08-26
      - 2017-09-05
      - 2017-09-07
    - The levels are recorded in `/workspace/afs-shared/reviews/claude_review_pack_1274_97476fda_2026-11-11.md` :350-367 (sha256 236255e9a65af42558de88c706cd83abf1e4b08275c4a687644cf479b0a90290; AFS-0629). They are not copied here.
    - This was **candidate-level only. No fills, exits, P&L or other outcomes were seen.** Strategy Lab read only that section of the pack.
    - Amendment A5 was written after this exposure (see A5).
- **No overlap and no double count:** NQ ends 2019-04-30 and MNQ starts 2019-05-01 (VERIFIED). Databento MNQ starts 2019-05-05 22:03Z (AFS-0573), so 2019-05-01..03 is DATA_GAP in the MNQ slice. It is **not** back-filled from NQ.

> ## ⚠ CAUTION: MNQ sample size (descriptive only; not a decision rule) (v7)
> This box describes what to expect. The gate is 1.4-1.6 and the evaluator (1.13); nothing here changes them. **The real fill rate is unknown until scoring.**
>
> **Base case: measured in-sample honest-IOC fill rate**
> - The rate is 20/34 = **0.588** (Oct 4 ledger, 3 ticks).
> - 77 MNQ candidates after unscored-day removal (AFS-0633) × 0.588 ≈ **45 fills**.
> - Less ~5% unresolved (in-sample 1 of 20 fills): **≈ 43 resolved**.
> - At the illustrative BE 0.738 that needs **37/43 wins (86.0%)**:
>   - LCB(37,43) = 0.7431 > 0.738;
>   - 36/43 gives LCB 0.7159, which fails.
>   - This is the same one-sided 95% Clopper-Pearson method as 1.4.
>
> **Stress case: "if fills come in near the in-sample no-fill rate" (0.41)**
> - 77 × 0.41 ≈ 31.6 fills ≈ **30 resolved**, right at the 30 minimum.
> - That needs **27/30** (LCB 0.7614) or **28/31** (LCB 0.7685).
> - Below 30 resolved, the result is INSUFFICIENT, and the whole trial would be a WAIT.
>
> **Notes**
> - Claude's #1274 comment used "~41% fill", but 41% is the in-sample **no-fill** rate (14/34), not the fill rate.
> - The v5 and v6 cautions used that figure as the base case.
> - The fixed 8-pt IOC cap may move the fill rate by era.
> - A5 drops 0 MNQ rows.
>
> *Superseded:*
> - the v6 base case "77 × 0.41 ≈ 31.6 fills ≈ 30 resolved" and its separate rate flag (both folded in above);
> - the v5 figure "~79 × 0.41";
> - the v3/v4 estimates "35-40" and "~49".

## 1.2 Decisive cells

- The wrapper's three models are separate (`MODEL_LABELS`, wrapper :142-146; reproduction prereg :23-27).
- **Decisive model: honest IOC at 3 ticks only.**
  - **Authority: AFS-0627 (2026-11-11 14:54 EST).** These are Imani's own typed words (t966u): "q1: IOC-only decisive, my decision." The ledger text was confirmed verbatim. The ledger is a pointer, not proof.
  - **History only:** AFS-0621 (14:27 EST), the PM decision made under her delegation, which AFS-0627 supersedes.
  - The 3-tick level comes from AFS-0621 / AFS-0613; AFS-0627 does not restate it.
- **Honest IOC** (`ioc_close` → PaperBroker `ioc_limit`):
  - The Limit-IOC is capped at trigger ± 32 ticks (`IOC_TOLERANCE`, harness :31). It is priced at the decision-bar close, `market_price=row["close"]` (edge_decomposition_audit :608-613).
  - The fill is `min(limit, close + slip)` for LONG and `max(limit, close − slip)` for SHORT. Otherwise the order is `NO_FILL / ENTRY_NOT_FILLED` (paper_broker :238-255).
  - A fill beyond its own stop or target is `NO_FILL / ENTRY_BRACKET_INVALID_AT_FILL` (:288-300).
  - This model mirrors the live Tradovate entry leg (:106-110).
- **Pre-armed (REPORT-ONLY)** (`prearmed_touch` → `stop_market`):
  - armed on the prior bar at the trigger (harness :96-119);
  - next-bar fill at the open on a gap, otherwise at the trigger, ± slip (paper_broker :436-445);
  - gap-open candidates armed at the 10AM open (wrapper :1028-1033).
- **Slippage:** 3 adverse ticks = 0.75 pt = $1.50 MNQ-equivalent per side where applied.
- **Decisive cells: 2.** `nq-2010-06-to-2019-05/honest_ioc/3tick` and `mnq-2019-05-to-2024-06/honest_ioc/3tick`. **Both must PASS.**
- **Report-only (never gates):**
  - pre-armed @3;
  - `original` (`plan`, wrapper :143);
  - every model at 1 and 2 ticks;
  - the harness WIN/LOSS/BREAKEVEN labels.

## 1.3 Cost model (frozen; nothing new)

- **Commission:** `COMMISSION_ROUND_TRIP = 1.48` USD per contract round trip, `scripts/edge_decomposition_audit.py:90`. It is subtracted once per resolved trade: `net = gross − COMMISSION_ROUND_TRIP` (:662).
- **Contract economics:** `contract_economics("MNQ")` returns tick 0.25 pt and $0.50/tick, i.e. **$2.00/pt** (`config/futures_contracts.py`; profile, wrapper :100-102).
- **Size:** 1 contract; `MAX_CONTRACTS_HARD_CAP="1"`; `OCT4_SCORING_PROFILE` (wrapper :91-103).
- **Slippage, by event** (`slippage_ticks`):
  - IOC/market entries and stop exits take adverse slippage (paper_broker :237, :614-636).
  - Gap-through stops exit at the bar open ± slip, so a loss can exceed 1R.
  - Targets fill clean (:626-628).
  - When the same bar hits both stop and target, the stop is taken (`pessimistic_both_hit=True`).
- **Day-only exit:** flatten at the close of the 15:55 ET 5m bar. A missing EOD bar → `UNRESOLVED / EOD_BAR_MISSING` (edge_decomposition_audit :589-599, :635-640). Slippage on the flatten exit is UNVERIFIED (not traced).
- **NQ:** NQ points × **$2/pt** (MNQ-equivalent), not $20/pt (wrapper :20; adapter @PR :48-50). Tick 0.25 in both slices.
- **The gate uses the harness `net` field.** It already includes commission and the frozen slippage. The evaluator does not re-price realized trades. It uses the cost constants only for R and BE_geom (1.13).

## 1.4 Win definition and break-even (Claude change 1; resolves Q2)

- **Gate win:** a resolved trade with **`net > 0`** after commission and the frozen slippage.
  - `net ≤ 0` is a **loss**, and **`net == 0` counts as a loss**.
  - The harness label (`classify_result`, execution/day_only_exit.py :69-83, sign of the gross move) is **report-only**. Example: a 15:55 flatten up 1 tick is a harness WIN at net −0.98, and a gate loss.
- `n` = resolved trades. `k` = count of net > 0. Loss set L = resolved trades with net ≤ 0.
- **BE_geom** is fixed geometry, the repo method (reproduction prereg :77; `aggregate_break_even` / `_target_and_stop_nets`, wrapper :2121-2199).
  - For each resolved trade, take `win_geom_i` and `loss_geom_i`: its net at its own target and at its own stop, with 3-tick adverse slip on both legs and commission on both.
  - BE_geom = Σ|loss_geom_i| / (Σ win_geom_i + Σ|loss_geom_i|).
  - If Σ win_geom_i ≤ 0, BE_geom = 1.0 and the cell cannot pass.
- **BE_real** uses the net-defined split:
  - BE_real = avg_net_loss / (avg_net_win + avg_net_loss);
  - avg_net_win = mean(net_i, i ∈ W);
  - avg_net_loss = mean(|net_i|, i ∈ L), where net == 0 contributes 0.
- **BE_real edge cases:**
  - No net losses (L empty): BE_real = 0.0.
  - No net wins (W empty): BE_real = 1.0. The cell cannot pass, and the net ≤ 0 FAIL already fires first.
  - Both empty only when n = 0, which is already INSUFFICIENT.
  - When W is non-empty, avg_net_win > 0, so the denominator is > 0.
- **BE = max(BE_geom, BE_real).**
- Identity: with net-defined wins, Σnet > 0 ⇔ k/n > BE_real. The confidence-bound check against BE_real is therefore a confidence version of "net > 0".
- **LCB:** one-sided 95% Clopper-Pearson. The pass rule is **LCB > BE** (strict).
- **Power illustration.** All break-evens below are **illustrative** assumptions, not slice values.
  - The 0.738 column is the Oct 4 in-sample IOC BE at 3 ticks (prereg :77). Only the n values and the CP math are exact.
  - Minimum k for LCB > BE:

  | n | BE 0.70 | BE 0.738 | BE 0.80 |
  |---|---|---|---|
  | 30 | 26 | 27 | 28 |
  | 35 | 30 | 31 | 33 |
  | 40 | 34 | 35 | 37 |
  | 50 | 41 | 43 | 45 |
  | 90 | 71 | 74 | 79 |
  | 150 | 115 | 120 | 129 |

## 1.5 Drawdown cap: **8R per decisive cell** (Claude confirmed)

- **R definition:** R = median over the cell's resolved trades of |loss_geom_i|, in USD MNQ-equivalent.
  - It comes from entry geometry, not outcomes.
  - It is comparable across 2010 and 2024 price levels.
- **Max drawdown:**
  - Closed-trade equity in chronological order, starting at 0, built from the resolved trades' `net`.
  - Peak starts at 0.
  - max_dd_usd = max over trades of (running peak − equity).
  - max_dd_r = max_dd_usd / R. **A breach is max_dd_r > 8.0.**
  - A breach sets cell status **FAIL_DRAWDOWN**. Overall that is FAIL, labeled "FAIL — risk profile": no promotion, and any retry is a new trial.
- **Arithmetic at the illustrative BE 0.738:**
  - win = +g R, loss = −1R, g = 0.262/0.738 = 0.355; σ = √g = 0.596R.

  | n | 30 | 50 | 90 | 150 |
  |---|---|---|---|---|
  | Expected longest losing run (losses) | 2.3 | 2.7 | 3.1 | 3.5 |
  | 95th-percentile run (losses) | 4.5 | 4.9 | 5.4 | 5.7 |
  | Zero-edge expected max DD | 4.1R | 5.3R | 7.1R | 9.2R |

  - Expected longest run = ln(n·BE)/ln(1/(1−BE)). 95th-percentile run = ln(n·BE/0.0513)/1.339.
  - Zero-edge expected max DD ≈ 1.2533·σ·√n. It equals 8R at n ≈ 115.
  - Why 8R: a 95th-percentile run (≈ 6R) plus ≈ 2R of give-back.
- **Account feasibility (UNSAFE annotation; report-only, not a gate):**
  - Applies to the MNQ cell only.
  - UNSAFE if max_dd_usd > **$450** (30% × $1,500; risk_rules.yaml :93, :220) or any single net loss < **−$150** (:91).
  - Context: 3-2-2 has no stop cap (rules :98), and the rules file notes it is incompatible with the real account (:154).

## 1.6 Check order and cell status (Claude v3 changes 2-3, v4 edits 1-2)

Evaluated in this order. **The first match sets the status.** All metrics are still computed and reported.

| # | Check | Status |
|---|---|---|
| 0 | Input invalid (schema, duplicate date, non-finite value, R ≤ 0, MNQ row dated 2026-06-29 or later) | exception → **VOID** (run void) |
| 1 | n < 30 | **INSUFFICIENT** |
| 2 | round(Σnet, 2) ≤ 0 | **FAIL** |
| 3 | (unresolved + open) / fills > 0.10 | **WAIT_UNRESOLVED** (mechanical; no reviewer discretion after results) |
| 4 | max_dd_r > 8.0 | **FAIL_DRAWDOWN** |
| 5 | LCB ≤ BE | **WAIT_CI** ("FAIL (screen), not refuted") |
| 6 | otherwise | **PASS** |

- **FAIL** and **FAIL_DRAWDOWN** mean no promotion. Any retry is a **new trial** in the tally.
- A losing cell (n ≥ 30, net ≤ 0) is FAIL even when more than 10% of fills are unresolved.
- **IOC no-fill rate:** descriptive only.
  - The 32-tick (8-pt) cap is fixed in points, so it will predictably move the fill rate on NQ.
  - It is reported by era (year) and by price level (median trigger price per year).

## 1.7 Combine and verdict (intersection-union test; Claude confirmed IUT size)

**Gate status:**
- **FAIL** if either cell is FAIL or FAIL_DRAWDOWN;
- else **PASS** if both cells are PASS;
- else **WAIT**. This covers any mix of PASS, INSUFFICIENT, WAIT_UNRESOLVED and WAIT_CI.

**FAIL label:** **OVERFIT** if any cell is FAIL (net ≤ 0). Otherwise, when only FAIL_DRAWDOWN is present, the label is **"FAIL — risk profile"**. OVERFIT takes precedence when both are present.

| Overall | Verdict label |
|---|---|
| VOID (exception, parity or identity refusal, dirty tree, seal guard) | **VOID** (run void, not edge evidence; not the AFS BROKEN verdict) |
| FAIL with any net ≤ 0 cell (incl. PASS + FAIL, INSUFFICIENT + FAIL, FAIL + FAIL_DRAWDOWN) | **OVERFIT**: the in-sample edge did not survive out of sample. Gate FAIL, no promotion, any retry is a new trial |
| FAIL from FAIL_DRAWDOWN only (e.g. PASS + FAIL_DRAWDOWN) | **"FAIL — risk profile"**: no promotion, any retry is a new trial |
| WAIT (e.g. PASS + INSUFFICIENT, PASS + WAIT_CI, PASS + WAIT_UNRESOLVED) | **WAIT**: "FAIL (screen), not refuted" |
| PASS | **PROMISING BUT UNPROVEN**. **Never VALIDATED** on older slices alone; VALIDATED needs prospective forward evidence |
| MNQ UNSAFE annotation set | adds **UNSAFE at current account sizing**, whatever the verdict above |

## 1.8 How NQ and MNQ combine

- **Conjunctive (IUT), never pooled.** `assemble_slice_report` (wrapper :2202-2233) is descriptive only.
- **Dollars:** NQ points × $2.
- **Dates:** windows are disjoint (wrapper :190-203), and `combined` is refused (:2092-2093).

## 1.9 One attempt per slice

- The decisive result is **attempt-01** of each slice; existing attempt files are refused (wrapper :1910-1918).
- Both slices are scored back-to-back before either summary is opened.
- No parameter, window, model, slippage, calendar, disposition or evaluator change. Any change means a new trial and a POST-HOC label.
- **Crash path** (SLICE line with an error and a null ledger, :1933-1950). A fix requires:
  - a pre-fix copy;
  - a written reason;
  - PM + Claude review;
  - an Imani GO.
- attempt-02 is allowed only if attempt-01 produced no replay-ledger or summary bytes.

## 1.10 Reporting (descriptive)

- For each cell:
  - candidates, fills, resolved, unresolved (with dates and reasons), open, no-fills by reason, and their rates;
  - DATA_GAP, EARLY_CLOSE-excluded, closed-session and invalid-bracket counts;
  - harness-label counts.
- Pre-armed, original, and the 1-2 tick results are reported with the same fields.
- IOC fill rate is broken out by year and price level (1.6).

## 1.11 Lookahead and session handling (as frozen)

- 60m 7-10AM bars are built from the 15m tree in America/New_York time.
- The IOC uses the trigger 5m bar's own close.
- Gap-opens are scored at the 10AM open (reproduction prereg :69).
- **From A5 (AFS-0629):** any research candidate whose entry is not strictly inside its bracket is dropped and counted, gap-open or not. This replaces the gap-open-only drop (reproduction prereg :71; `partition_invalid_gap_opens`).
- Resolution never walks past 15:55 ET (edge_decomposition_audit :589-599).
- One position at a time.
- The calendar is pure date arithmetic.
- Candidate parity must hold or the slice raises (wrapper :969-1003).

## 1.12 Multiple-testing context

- The tally is 433. This trial is **#434** (number to be assigned by PM/Imani).
- A FAIL retry would be another new attempt.
- 3-2-2 was selected on an in-sample period of 2024-07..2026-06 (honest IOC 18-1, AFS-0523).

## 1.13 Evaluator specification (for Cursor; Claude change 4)

**Module:** `research/afs0523_322_slice_evaluator.py` (new).
- It is committed with tests **before** the `SLICE_SCORING_AUTHORIZED` flip.
- It is pure and deterministic: **no I/O, no randomness, no clock, no environment reads, no network.**
- Standard library only (`math`, `statistics`, `dataclasses`, `typing`). No numpy or scipy.

**Wiring (part of the flip PR):**
- `_score_admitted_slice` (wrapper :1953) calls `evaluate_cell` in-process on the 3-tick honest-IOC rows of the replay ledger it just built.
- It writes the result into the slice artifact.
- `_append_slice_event` (:2032-2062) adds `evaluator_path` and `evaluator_sha256` (the sha256 of the module file bytes) to the SLICE event and the artifact.
- The caller stamps `evaluator_sha256` into the cell result. The evaluator cannot hash its own file, because it does no I/O.
- **Separate pure combine (Q8.2 resolved):** `combine_cells` runs once both attempt-01 artifacts exist.
  - A thin wrapper verifies each artifact against its SLICE event sha256 and evaluator sha256, then calls the pure function.
  - Its output and both input shas go in a separate COMBINE artifact.
  - `combine_cells` **raises `ValueError`** if `nq["evaluator_sha256"] != mnq["evaluator_sha256"]`, or if either is missing or not 64 lowercase hex characters.

### Signatures

```python
from typing import Any, Literal, Mapping, Sequence, TypedDict, Optional

Market = Literal["NQ", "MNQ"]
Status = Literal["RESOLVED", "UNRESOLVED", "OPEN", "NO_FILL"]

class TradeRecord(TypedDict):
    date: str                 # "YYYY-MM-DD", civil ET session date; unique within a cell
    cell_id: str              # "<slice_id>/honest_ioc/3tick"
    market: Market            # "NQ" for nq-2010-06-to-2019-05, "MNQ" for mnq-2019-05-to-2024-06
    model: Literal["honest_ioc"]
    slippage_ticks: float     # must equal 3.0
    direction: Literal["LONG", "SHORT"]
    status: Status            # from replay-ledger row["models"]["ioc_close"]["status"]
    fill_entry: Optional[float]   # index points; required (finite) when status in RESOLVED/UNRESOLVED/OPEN
    stop: float               # index points, structural stop (opposite 9AM boundary)
    target: float             # index points, structural target (8AM outside-bar extreme)
    net: Optional[float]      # USD MNQ-equivalent after commission + frozen slippage; required (finite) iff RESOLVED
    harness_result: Optional[Literal["WIN", "LOSS", "BREAKEVEN"]]  # REPORT-ONLY
    reason: Optional[str]     # exit_reason / no-fill / unresolved reason; report-only

def trade_records_from_ledger(
    ledger: Mapping[str, Sequence[Mapping[str, Any]]],   # the inner {"1tick": [...], "2tick": [...], "3tick": [...]} dict
    *,
    slice_id: str,
    market: Market,
    slippage_key: str = "3tick",
    model_key: str = "ioc_close",
) -> list[TradeRecord]: ...
def evaluate_cell(trades: Sequence[TradeRecord], *, cell_id: str, market: Market) -> dict: ...
def combine_cells(nq: dict, mnq: dict) -> dict: ...
def clopper_pearson_lower(k: int, n: int, alpha: float = ALPHA_ONE_SIDED) -> float: ...
```

`trades` holds every candidate row of the cell, including NO_FILL. Rows on unscored dates were already removed upstream (wrapper :964-967).

### Ledger-row builder (pure, in the evaluator module; v4 edit 5)

**Input:** the `ledger` dict as the wrapper writes it.
- `{"ledger": ledgers}` (wrapper :1346, :2007). The caller passes the inner value.
- Each slippage key holds the rows from `harness.ledger` (scripts/322_trigger_timing_ab_2026_09_18.py :199-220).
- Each model cell is the `resolve_bracket` dict minus `cand` (edge_decomposition_audit :614-671).

**Order and errors:**
- The output preserves the input row order.
- It raises `ValueError` on a missing key, an unknown status, or (MNQ only) any `date ≥ "2026-06-29"` (AFS-0459 seal guard).

| TradeRecord field | Source |
|---|---|
| `date` | `str(row["date"])` (ISO; the harness writes `c.date` as a string) |
| `cell_id` | `f"{slice_id}/honest_ioc/{slippage_key}"` |
| `market` | argument |
| `model` | `"honest_ioc"` (requires `model_key == "ioc_close"`) |
| `slippage_ticks` | `float(slippage_key.removesuffix("tick"))` |
| `direction` | `row["direction"]` |
| `stop` / `target` | `float(row["stop"])` / `float(row["target"])` |
| `status` | `row["models"][model_key]["status"]` |
| `fill_entry` | `cell.get("fill_entry")` as float or None. Present on RESOLVED/UNRESOLVED, and on NO_FILL `ENTRY_BRACKET_INVALID_AT_FILL`, where it is ignored |
| `net` | `cell.get("net")` as float, only when status is RESOLVED, else None |
| `harness_result` | `cell.get("result")` (RESOLVED only, else None) |
| `reason` | `cell.get("reason")`, falling back to `cell.get("exit_reason")`; same order as `_cell_reason`, wrapper :1814-1820 |

### Frozen constants (module level; a test asserts each equals the repo source)

| Name | Value | Repo source asserted in tests |
|---|---|---|
| `COMMISSION_ROUND_TRIP_USD` | 1.48 | `scripts.edge_decomposition_audit.COMMISSION_ROUND_TRIP` |
| `TICK_SIZE_PT` / `TICK_VALUE_USD` | 0.25 / 0.50 | `config.futures_contracts.contract_economics("MNQ")` |
| `USD_PER_POINT` | 2.0 | TICK_VALUE_USD / TICK_SIZE_PT |
| `DECISIVE_SLIPPAGE_TICKS` | 3.0 | `OCT4_SCORING_PROFILE["slippage_ticks"]` contains 3.0 |
| `MIN_RESOLVED` | 30 | — |
| `UNRESOLVED_MAX_FRACTION` | 0.10 (WAIT if strictly greater) | — |
| `DD_CAP_R` | 8.0 (breach if strictly greater) | — |
| `ALPHA_ONE_SIDED` | 0.05, Clopper-Pearson | — |
| `ACCOUNT_DD_FLOOR_USD` / `ACCOUNT_SINGLE_LOSS_USD` | 450.0 / 150.0 (MNQ annotation only) | risk_rules.yaml :93×:220, :91 |

### `evaluate_cell` algorithm

0. **Validate.** Raise `ValueError` on any of the following; the caller records the slice as VOID.
   - Any record whose `cell_id`, `market`, `model` or `slippage_ticks` differs from the arguments or constants.
   - A duplicate `date`.
   - A required field missing or non-finite.
   - RESOLVED with `net` None, or a non-RESOLVED row with a `net`.
1. **Counts:**
   - `candidates = len(trades)`;
   - `fills` = rows with status RESOLVED, UNRESOLVED or OPEN;
   - `resolved` = RESOLVED (this is `n`);
   - `unresolved` = UNRESOLVED;
   - `open` = OPEN (counted with unresolved for the threshold);
   - `no_fill` = NO_FILL;
   - `unresolved_fraction = (unresolved + open) / fills`, or 0.0 when fills == 0.
2. **Sort** resolved trades by `date` ascending.
3. **Wins:** `k` = #{net > 0}. L = {net ≤ 0}. `net_total = round(Σ net, 2)`.
4. **Geometry**, per resolved trade, with s = 3 × 0.25 = 0.75 pt:
   - LONG: `win_pts = (target − s) − fill_entry`, `loss_pts = (stop − s) − fill_entry`.
   - SHORT: `win_pts = fill_entry − (target + s)`, `loss_pts = fill_entry − (stop + s)`.
   - `win_geom = win_pts × 2.0 − 1.48`, `loss_geom = loss_pts × 2.0 − 1.48`.
   - This is identical to `_target_and_stop_nets` (wrapper :2121-2147); a test asserts equality.
5. **R and BE:**
   - `R = statistics.median(|loss_geom_i|)`. If R ≤ 0, raise.
   - `be_geom = Σ|loss_geom| / (Σwin_geom + Σ|loss_geom|)`; 1.0 if Σwin_geom ≤ 0.
   - `be_real` per 1.4.
   - `be = max(be_geom, be_real)`.
6. **Drawdown:** equity = 0 and peak = 0. For each trade in order, `equity += net`, `peak = max(peak, equity)`, `dd = max(dd, peak − equity)`. `max_dd_r = dd / R`.
7. **LCB:** `lcb = clopper_pearson_lower(k, n)`.
   - It is 0.0 if k = 0.
   - Otherwise it is the unique p in (0, 1) with Σ_{i=k..n} C(n,i) pⁱ(1−p)ⁿ⁻ⁱ = α.
   - Solve by bisection on [0, 1] for exactly 200 iterations, using `math.comb`, and return the midpoint. The result is deterministic.
   - If n = 0, return 0.0.
8. **Status:** apply the checks in the 1.6 order: 1 INSUFFICIENT → 2 FAIL (net ≤ 0) → 3 WAIT_UNRESOLVED → 4 FAIL_DRAWDOWN → 5 WAIT_CI → 6 PASS. The first match wins.
9. **UNSAFE annotation (MNQ only):** set if `dd > 450.0` or any `net < −150.0`.

### Per-cell output schema (JSON-serializable dict)

```text
{ "cell_id", "market", "model": "honest_ioc", "slippage_ticks": 3.0,
  "status": "INSUFFICIENT"|"FAIL"|"WAIT_UNRESOLVED"|"FAIL_DRAWDOWN"|"WAIT_CI"|"PASS",
  "decided_by_check": int 1..6,
  "counts": {"candidates","fills","resolved","unresolved","open","no_fill"} (int),
  "unresolved_fraction": float,
  "net_total_usd": float, "wins_net": int, "losses_net": int, "zero_net_count": int,
  "win_rate_net": float|null, "avg_net_win_usd": float|null, "avg_net_loss_usd": float|null,
  "be_geom": float|null, "be_real": float|null, "be": float|null, "lcb": float|null,
  "r_usd": float|null, "max_dd_usd": float, "max_dd_r": float|null,
  "harness_label_counts": {"WIN","LOSS","BREAKEVEN"} (report-only),
  "unsafe_account": bool|null (MNQ only), "worst_net_loss_usd": float|null,
  "evaluator_sha256": str (stamped by the caller, not the evaluator),
  "constants": {all frozen constants above} }
```

- Floats are rounded to 10 decimal places, and USD values to 2.
- Values that cannot be computed when n = 0 are `null`.

`combine_cells` returns `{ "overall": "PASS"|"FAIL"|"WAIT", "verdict": "PROMISING BUT UNPROVEN"|"OVERFIT"|"FAIL — risk profile"|"WAIT", "cells": [nq.status, mnq.status], "evaluator_sha256": shared value, "unsafe_account": mnq.unsafe_account }`.

It raises if:
- the cells are not exactly one NQ and one MNQ honest-IOC 3-tick cell; or
- the `evaluator_sha256` values differ or are malformed.

### Required unit tests (synthetic fixtures; all LONG)

**Base trade:** fill 1000.00, stop 985.00, target 1007.00.
- `win_geom` = (1007 − 0.75 − 1000) × 2 − 1.48 = **+11.02**.
- `loss_geom` = (985 − 0.75 − 1000) × 2 − 1.48 = **−32.98**.
- So R = 32.98 and be_geom = 32.98/44.00 = **0.7495454545**.
- A target trade has net +11.02 and a stop trade has net −32.98, so be_real = 0.7495454545 too.
- Dates are consecutive weekdays from 2015-01-05.

| # | Fixture | Expected |
|---|---|---|
| T1 | 29 RESOLVED trades, all net +11.02 | INSUFFICIENT (check 1) |
| T2 | 89 RESOLVED (all wins) + 11 UNRESOLVED → 11/100 = 0.11 | WAIT_UNRESOLVED (check 3) |
| T2b | 90 RESOLVED + 10 UNRESOLVED → 0.10 | not WAIT_UNRESOLVED (boundary is strict) |
| T2c | T3's 30 RESOLVED (net −109.40) + 4 UNRESOLVED → 4/34 = 0.1176 > 0.10 | **FAIL (check 2)**: net ≤ 0 wins over unresolved |
| T3 | 30 RESOLVED: 20 × +11.02, 10 × −32.98 → net −109.40 | FAIL (check 2) |
| T4 | 40 RESOLVED: 31 wins, then 9 consecutive losses → net +44.80; DD 296.82 = 9.0R | FAIL_DRAWDOWN (check 4) |
| T5a | 30 RESOLVED: 27 wins, 3 losses (spaced) → net +198.60, DD ≤ 4R, LCB(27,30) = 0.761402 > 0.749545 | PASS |
| T5b | 30 RESOLVED: 26 wins, 4 losses (spaced) → net +154.60, LCB(26,30) = 0.720385 ≤ 0.749545 | WAIT_CI (check 5) |
| T6 | 30 RESOLVED: 27 × +11.02, 2 × −32.98, 1 trade with net 0.00 | k = 27, losses_net = 3, zero_net_count = 1, avg_net_loss = 65.96/3 = 21.9866666667; be_real = 21.9866666667/(11.02 + 21.9866666667) = 0.6661278406; be = be_geom 0.7495454545 → PASS |
| T7 | T5a, but one target trade is replaced by a record with `harness_result="WIN"`, `reason="DAY_ONLY_FLATTEN"`, `net=-0.98` (same fill/stop/target) → net +186.60 | that trade is a gate loss: wins_net = 26, losses_net = 4, harness_label_counts.WIN = 27; LCB(26,30) = 0.720385 ≤ 0.749545 → WAIT_CI |
| T8 | `combine_cells(PASS, FAIL)` and `(FAIL, PASS)` | overall FAIL, verdict OVERFIT |
| T8b | `combine_cells(PASS, FAIL_DRAWDOWN)` and `(FAIL_DRAWDOWN, PASS)` | overall FAIL, verdict "FAIL — risk profile" |
| T8c | `combine_cells(FAIL, FAIL_DRAWDOWN)` | overall FAIL, verdict OVERFIT (precedence) |
| T9 | `combine_cells(PASS, INSUFFICIENT)`, `(PASS, WAIT_CI)` and `(PASS, WAIT_UNRESOLVED)` | overall WAIT |
| T10 | `combine_cells(PASS, PASS)` | PASS, PROMISING BUT UNPROVEN |
| T11 | `clopper_pearson_lower`: (27,30) = 0.761402, (26,30) = 0.720385, (31,35) = 0.757278, (35,40) = 0.754969, (30,30) = 0.904966 (= 0.05^(1/30)), (0,n) = 0 | abs tolerance 1e-6. Reference values from `scipy.stats.beta.ppf(0.05, k, n−k+1)`, computed on the box |
| T12 | Constants equal the repo sources; geometry equals `_target_and_stop_nets` on the T5a trades | equality |
| T13 | Duplicate date / RESOLVED with net None / slippage_ticks 2.0 | ValueError |
| T14 | Determinism: the same input twice gives byte-identical `json.dumps(sort_keys=True)`; the module imports no `random`, `time`, `datetime.now`, `os` or `open` (AST check) | pass |
| T15 | MNQ cell with one net −150.01 | unsafe_account true; status unaffected |
| T16 | **Recorded gate replay.** Read `docs/research-evidence/T-2026-11-10-prereg-322-afs0523-oos-structural-2026-11-10-01/reproduction-replay-ledger.json` (sha256 `6c6b0141e4936795db1dd19c87efb3a257684943d0e637d7c7143f52530ae573`, committed in #1271 at a21ee24e). Assert the file's sha256 first. Then call `trade_records_from_ledger(json["ledger"], slice_id="reproduction", market="MNQ")` → `evaluate_cell` | exactly: candidates 34, fills 20, resolved 19, unresolved 1, no_fill 14, wins_net 18, net_total_usd 1603.38, status INSUFFICIENT. **Claude independently recomputed these values from the committed gate replay bytes on GitHub.** IOC 3-tick gives 34 candidates, 14 NO_FILL, 1 UNRESOLVED and 19 RESOLVED (18 WIN, 1 LOSS), with 20 fills, 18 net wins and net $1,603.38. Strategy Lab did not recompute them. The evaluator PR's test is still the real check |
| T17 | `combine_cells` with valid NQ/MNQ cells whose `evaluator_sha256` differ (e.g. `"a"*64` vs `"b"*64`), plus a missing or short sha | ValueError (refuse) |
| T18 | Builder seal guard: an MNQ ledger row dated `2026-06-29` | ValueError |

T7 note: the evaluator never re-prices `net`. It trusts the frozen harness value.

### Code-PR requirements for A5 (separate from the evaluator; same PR as N1)

The A5 bracket rule lives in the wrapper's candidate path, not in the evaluator. **The PR must be merged and verified before the `SLICE_SCORING_AUTHORIZED` flip.**

- **R-A5a:** replace the gap-open-only partition with a drop of **every** research row that is outside its bracket:
  - LONG requires `stop < entry_price < target`;
  - SHORT requires `target < entry_price < stop`;
  - equality is outside the bracket.
  - The rule is identical to `strategy/strat_322_first_live.py` :193-198.
  - The drop happens before `_parity`'s count check (wrapper :962-977).
- **R-A5b:** record per-slice `invalid_entry_bracket_count` plus the dates in the slice artifact and the SLICE event. Report gap-open and non-gap-open drops separately. #1276 at d235f03b does this: drop counts split into gap and non-gap, in both the artifact and the SLICE event (AFS-0633, PM PASS).
- **R-A5c (precondition):** on the pinned reproduction trees, the gate path must still produce:
  - **34 candidates**;
  - **306 outcome records, 0 mismatches**;
  - **0 invalid brackets**.

| Test | What it asserts |
|---|---|
| **T19** | Synthetic rows. A non-gap-open zero-width row (trigger == target) is dropped and counted, and a strictly inside row is kept. Covers LONG and SHORT, and equality at each boundary. |
| **T20** | Gate-window regression on the committed replay. The candidate count is 34, `compare_replay_structural` gives 306 / 0 mismatches, and `invalid_entry_bracket_count == 0`. |
| **N1** | `_run_window` fixture on the REPRO window. `holiday_closed_unscored == []`, and a planted closed-day row is **not** removed by the reproduction-window path (AFS-0629/0630; PM #1274 review N1). |

**T16 provenance and seal check:**
- **Path:** confirmed at main f612049f (`git log` → a21ee24e "record the structural reproduction gate PASS"). The file's sha256 on the box clone is 6c6b0141…ae573, matching AFS-0612 and AFS-0615. Only the hash was computed; the contents were not opened.
- **Date range:**
  - gate window 2024-07-02 .. 2026-06-26 (wrapper :113-114, enforced by the gate);
  - candidate population 2024-08-02 .. 2026-06-11 (trial ledger line 12; 34 candidates).
  - Both are taken from provenance, not from reading the file.
- **Seal (AFS-0459):** no row should be on or after 2026-06-29. **No seal concern**, because the window ends 2026-06-26. The builder's seal guard (T18) would raise if one appeared.
- T16 is a reproduction-window check, not slice evidence. INSUFFICIENT is expected because n = 19 < 30.

---

# PART 2 — Amendment to `docs/prereg-322-afs0523-oos-structural-2026-11-10.md`

**Amendment metadata**

- Trial: `T-2026-11-10-prereg-322-afs0523-oos-structural-2026-11-10-01` (prereg :3).
- Draft date: 2026-11-11.
- Reason:
  - align the prereg with #1272's early-close slice policy;
  - remove the stale line :49;
  - register the disposition, the halt split and the closed-day exclusions before any slice is scored (Claude open items, AFS-0619).
- **No slice results exist or were viewed:**
  - `SLICE_SCORING_AUTHORIZED = False` (wrapper :71, main and @PR);
  - 0 SLICE events in `docs/research-trial-ledger.jsonl`.
- The gate artifact `41d76ab1…` and its DATA_GAPs (2025-01-09, 2025-11-28; count 2) are untouched: the gate policy keeps 13:00 ET for every early close (adapter @PR :68-70, :189-190, :219-227; AFS-0618).
- This amendment must be committed before the `SLICE_SCORING_AUTHORIZED` flip PR. It cites #1272 at abaa1a4b; re-verify line numbers if it merges at another SHA.

**A1. Replace stale line 49.**

Current text (:49, VERIFIED):
> - Early-close labeling is not changed. Pre-open 15-minute bars on an early-close day, such as 2025-11-28 from 07:00 through 08:15, remain `DATA_GAP` until a later change labels them `expected_holiday_closure`.

Replacement:
> - Every session, including an early close, requires 15-minute bars from 07:00 ET, because the 3-2-2 detector needs the 7AM and 8AM hours. A missing 07:00-08:15 bar is `DATA_GAP` on both the gate and slice policies and is never relabeled `expected_holiday_closure`. 2025-11-28 stays `DATA_GAP` (Globex outage, not a normal half-day open). The gate window 2024-07-02..2026-06-26 keeps a 13:00 ET halt for every early close; its gaps stay 2025-01-09 and 2025-11-28 (count 2). Only bars after the session's halt are `expected_holiday_closure`.

Source: adapter @PR :23-31, :33-36, :239-255, :291. This supersedes main adapter :175 (TODO).

**A2. Register `EARLY_CLOSE_DISPOSITION = "exclude"` for the slice path.**
- Constant: adapter @PR :81. Any value other than exclude/include refuses (:198-207).
- A complete early-close day (no DATA_GAP) is labeled `EARLY_CLOSE` with `scored: false` (:318-327).
- The wrapper adds it to the unscored set (wrapper :1792-1798), and candidates on that date are dropped before parity and scoring (:964-967).
- The count is reported (`early_close_count`, :335-336).
- Reason: the frozen replay flattens only at 15:55 ET, and there is no preregistered halt-time exit (:43-45).
- Switching to `include` after results is prohibited.

**A3. Register the slice halt split.** VERIFIED against code, no mismatch with the requested split:
- **13:15 ET (12:15 CT):** the day after Thanksgiving, Jul 3, and Dec 24, only when that date is a weekday and not itself the observed closure (`_late_halt_dates` :173-179, `_weekday_half_day` :122-134).
- **13:00 ET (12:00 CT):** MLK, Presidents' Day, Memorial Day, Labor Day, and Juneteenth (observed, from 2022) (:155-166, :182-195).
- Gate policy: 13:00 ET for all early closes (:189-190).
- The policy is chosen by window equality with 2024-07-02..2026-06-26 (:219-227).
- Calendar counts inside the windows, from the calendar arithmetic (some are DATA_GAP instead of EARLY_CLOSE):

  | Slice | 13:00 ET days | 13:15 ET days |
  |---|---|---|
  | NQ | 35 | 19 |
  | MNQ | 24 | 9 |

- External CME schedule history for 2010-2024 is **UNVERIFIED** here. The code comment cites cmegroup.com/trading-hours (:71-73).

**A4. Conservative closed-day exclusions.**
- The code labels these days `closed`, which means 0 required bars and never DATA_GAP (adapter @PR :146-154, :247-251):
  - observed New Year;
  - Good Friday;
  - Thanksgiving Day;
  - observed Jul 4: 2010-07-05, 2015-07-03, 2020-07-03 and 2021-07-05 plus the plain Jul 4s;
  - observed Christmas, including **Dec 26**: 2011-12-26, 2016-12-26, 2022-12-26.
- Globex LIKELY traded abbreviated mornings on several of these days. Examples:
  - the Jul 4 and Thanksgiving mornings;
  - the **Good Friday NFP mornings** 2012-04-06 and 2015-04-03 (NQ slice) and 2021-04-02 and 2023-04-07 (MNQ slice). Coincidence with NFP is LIKELY; the BLS schedule was not checked.
- Registration: **no candidate dated on a `closed` weekday is scored**, even if bars exist. These dates are reported as `CLOSED_SESSION` with a count.
- **CONFLICT-1 (must-fix before the flip):**
  - Code at f612049f/abaa1a4b does **not** enforce A4.
  - `_unscored_dates` (wrapper :1792-1798) only holds DATA_GAP and excluded EARLY_CLOSE dates.
  - The adapter still writes bars that exist on closed days; `_closure_kind` only labels missing minutes (:679-691).
  - The detector iterates every weekday (`trading_days`, bars_322_polygon_loader :131-138).
  - So a 3-2-2 candidate on a closed-day morning would be scored. It could resolve before the halt or go UNRESOLVED.
  - Fix: add closed weekdays to the unscored set, with a count, with a test.
  - **Status (v5): RESOLVED.**
    - #1274 merged as **e5d3b780068b6266337b9cb0bce003a5a570e1e0**, verified on GitHub: PR state MERGED, mergedAt 2026-11-11 16:09:38 ET, and it is the head of main.
    - It got Claude PASS and PM PASS at 97476fda (AFS-0629, AFS-0630).
    - H1: only `kind == closed` days are held back; closures counted MNQ 24 / NQ 44 (AFS-0629).

**A5. Strict-inside-bracket candidate drop (registers AFS-0629 / AFS-0630 condition).**

- **Rule:** drop any research candidate whose entry is not strictly inside its bracket. LONG requires `stop < entry < target`; SHORT requires `target < entry < stop`; equality is outside the bracket. This applies to **all** such rows, not only gap-opens.
  - Dropped rows are counted and dated per slice. They are never scored.
- **Live-code match (VERIFIED at main e5d3b780; the file is unchanged since 0db7c088, 2026-07-26):** `strategy/strat_322_first_live.py`.
  - **:191 is the stop assignment, not the check:**
    > `    stop = float(previous["stop"])`
  - The bracket check is :193-198:
    > ```
    >     bracket_valid = (
    >         target < entry_price < stop
    >         if direction == "SHORT"
    >         else stop < entry_price < target
    >     )
    >     if not bracket_valid:
    > ```
    and :200 sets `invalidation="INVALID_ENTRY_BRACKET"`.
  - The instruction cited ":191". The registration cites :193-198 (CONFLICT-2, minor).
- **Expected dropped rows per slice.** These are LIKELY, from candidate-level counts only; the code reports the actual numbers.
  - **NQ:** 95 research vs 90 state machine; **5 drops → 90 vs 90**.
    - After unscored-day removal: 92 vs 88; **4 drops → 88 vs 88**.
    - 2014-05-26 is Memorial Day, an early close already removed by `exclude`.
  - **MNQ:** **0 drops**: 79 vs 79, and **77 vs 77** after unscored-day removal (Juneteenth early closes 2022-06-20 and 2024-06-19).
  - These figures match PM's recompute in AFS-0633 (#1276 @ d235f03b).
  - The #1276 code reports drops split into **gap and non-gap counts**, in both the slice artifact and the SLICE event.
- **Precondition (R-A5c):** gate 34 candidates, replay 306 records / 0 mismatches, and the gate window had 0 invalid brackets (AFS-0612, AFS-0629: "Gate path unchanged (34 cands, 306/0)"). It must be re-proven in the A5 PR.
- **N1** goes in the same code PR (see 1.13, code-PR requirements).
- **Honest note: registered after candidate-level exposure.**
  - This rule is written **after** detection was run on the slice data during the #1274 review: counts plus the five zero-width rows' levels (exposure (c)).
  - **No outcomes were seen.**
  - **Why it is still acceptable:**
    - (i) It adds no new judgement. It applies the existing live-code validity check (:193-198) that the production state machine already enforces, and it is the same rule the wrapper already applies to gap-opens.
    - (ii) Without it, `_parity` raises on NQ (92 vs 88), so the slice cannot run at all.
    - (iii) The rule is outcome-blind. Zero-width rows have trigger == target, so they cannot be honest trades.
  - **For QA: a judgement call, not settled.** QA should decide whether post-exposure registration of a mechanical validity rule taints the NQ slice, and whether the NQ label should carry a note.

---

## Open items / resolved

**Open**
- **A5 code PR (new, blocking):** the strict-inside-bracket drop, the per-slice counts, the gate re-proof (34 / 306 / 0) and N1. It must be merged and verified before the flip. Slice scoring is also still gated on the evaluator PR, the flip PR, Claude reviews and Imani's GO.
- **QA judgement call (new):** A5 was registered post-exposure (candidate-level only). Is that acceptable, and should NQ carry a note?
- **CONFLICT-2 (minor):** the AFS-0629 instruction cites `strat_322_first_live.py:191`. On main, :191 is `stop = float(previous["stop"])`, and the bracket check is :193-198. The draft cites :193-198.
- **Q5:** External CME holiday history for 2010-2024 is UNVERIFIED.
- **Q7:** Assign the tally number (#434).
- **Q9 (new, minor):**
  - "VOID" and "FAIL — risk profile" are labels outside the six AFS verdict labels (VALIDATED / PROMISING BUT UNPROVEN / BROKEN / OVERFIT / UNSAFE / WAIT). This was adopted on Claude's instruction; QA should note the extension.
  - AFS-0627 says "IOC-only" without restating 3 ticks; the 3-tick level comes from AFS-0621 / AFS-0613.

**Resolved / confirmed**
- **CONFLICT-1: RESOLVED.** #1274 merged as e5d3b780068b6266337b9cb0bce003a5a570e1e0, verified on GitHub (AFS-0630).
- **Q4: CLOSED.** #1272 merged as e9e1d041e1defac8b4bc96e31c877e31d96229cd (AFS-0623). It is verified to be an ancestor of main e5d3b780.
- **Q1: RESOLVED by AFS-0627** (2026-11-11 14:54 EST).
  - Imani's own typed words (t966u): "q1: IOC-only decisive, my decision." The ledger text was confirmed verbatim.
  - AFS-0621 (the PM decision under delegation) is history only.
- **Q8 (v3 instruction inconsistencies), all resolved by Claude's v4 edits:**
  - **Q8.1:** FAIL (net ≤ 0) now precedes WAIT_UNRESOLVED.
  - **Q8.2:** separate pure `combine_cells`, which refuses mismatched evaluator sha256.
  - **Q8.3:** OVERFIT is kept for a net ≤ 0 FAIL. The mechanical-void label was renamed BROKEN → VOID in 4 places:
    - the 1.6 check-0 row;
    - the 1.7 table row (both cells);
    - the 1.13 validate step.
    - No genuine AFS BROKEN verdict use remained in the draft.
  - **Q8.4:** a drawdown breach is FAIL_DRAWDOWN, which overall is FAIL, labeled "FAIL — risk profile".
- **Q2:** resolved by the net-defined win (1.4).
- **Q3:** resolved by the evaluator spec (1.13).
- **Q6:** resolved. The 32-tick (8-pt) IOC cap stays frozen, and its effect on fill rate is reported by era and price level. The no-fill band is descriptive only.
- **Claude confirmed:** IUT size (no Bonferroni split across the 2 cells), one-sided 95% Clopper-Pearson, the 8R drawdown cap, and amendments A1-A3.
