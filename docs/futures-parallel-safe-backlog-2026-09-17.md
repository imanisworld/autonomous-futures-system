# Futures Parallel-Safe Backlog — 2026-09-17

**Status:** OPERATIONS / RESEARCH QUEUE ONLY

This queue separates work that can proceed safely in parallel from work that is blocked by active research, evidence accumulation, or the no-release/no-restart freeze.

| Item | Current state | Parallel-safe now? | Blocking condition / next proof |
|---|---|---:|---|
| #623 M2K v1.5 | active external lane | NO — owned by active Claude lane | X0 roll provenance + corrected R4 wording + current-main CI |
| MGC/MCL/MBT tranche 2 | active external lane | NO — owned by active Claude lane | product structural definitions + dated-contract/roll proof |
| C8 MES top-level `market_condition` | root cause proven | YES, offline only | journal normalization + regression tests; no deploy during freeze |
| C14 holiday daily-session anchor | root-cause class proven | YES, proof only | generalized holiday/session identity evidence before code fix |
| C16 VWAP missing-bar sensitivity | known separate issue | AUDIT ONLY | decide whether missing-bar rows can be reconstructed/flagged without hiding evidence; no tolerance widening |
| #612 shared-journal capacity coupling | fixed in repo, not deployed | CHECKLIST ONLY | sanctioned post-freeze paper-only release + runtime proof |
| #621 replay day-boundary shadow history | fixed/merged | COMPLETE repo-side | no VPS deployment required for offline replay parity |
| MNQ/MES R4 rerun | completed after #621 | COMPLETE pending report reconciliation | retain EMA bracket conflict; transition family remains NOT_TESTABLE |
| R5 structural outcome phase | HOLD | NO | clean prerequisite parity/corpus population and explicit authorization |
| M2K P3/P5 maturity | preliminary | NO — time/data bound | >=5 sessions and required level history, then rerun |
| six-root forward observation | active | WAIT/COLLECT | sample accumulation; no engineering needed unless monitoring fails |
| context-permission F7/F8 journal defect | same as C8 | covered by C8 | new prospective data required for confirmatory use after repair |
| dead observer `overnight_range_location` copy | redundant | DO NOT FIX | bar-derived ONH/ONL already supplies working evidence |
| old 5m rows in 15m context study | historical contamination | DO NOT FIX runtime | analysis exclusion by `timeframe_minutes`; preserve audit trail |
| `range_signal` missing candidate location | low-priority | HOLD | only revisit if context-permission direction survives 2026-09-30 review |
| impulse heuristic disagreement | definition conflict, not runtime bug | DO NOT FORCE-FIX | keep sources distinct unless a future prereg chooses one construct |
| MES isolated-lane log directory | intentional isolation side-effect | NO CODE FIX | census/join readers must avoid double counting |

## Safe ordering

1. Finish active #623/tranche-2 lane without interference.
2. Prepare/land C8 offline repair when exact patch + tests are available.
3. Execute C14 proof plan; code only after generalized rule is demonstrated.
4. After freeze: deploy the minimal futures runtime defect release (#612, optionally C8 only if its diff remains audit-only and proven).
5. Continue forward collection and M2K maturity wait.
6. Authorize R5 only after the prerequisite evidence state is clean.

No item in this document authorizes live trading, strategy promotion, outcome reading, deployment, restart, or threshold relaxation.