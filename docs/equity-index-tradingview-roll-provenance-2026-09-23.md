# Equity-index TradingView roll provenance — 2026-09-23

**Status:** research/data-provenance correction only. No strategy, risk, execution, broker, scheduler, collector, deployment, or live-enablement authority.

## Source

On 2026-09-23 the operator captured TradingView **Contract Switch** markers directly from the continuous charts for `MNQ1!`, `MES1!`, and `M2K1!`.

These markers establish TradingView's **switch date** and the old/new dated contracts. They do **not** establish the exact intraday/UTC switch timestamp.

## Proven 2026 TradingView switch dates

| Continuous symbol | Switch date | From contract | To contract |
|---|---|---|---|
| `MNQ1!` | **2026-06-15** | `MNQM2026` | `MNQU2026` |
| `MNQ1!` | **2026-09-15** | `MNQU2026` | `MNQZ2026` |
| `MES1!` | **2026-06-15** | `MESM2026` | `MESU2026` |
| `MES1!` | **2026-09-15** | `MESU2026` | `MESZ2026` |
| `M2K1!` | **2026-06-16** | `M2KM2026` | `M2KU2026` |
| `M2K1!` | **2026-09-16** | `M2KU2026` | `M2KZ2026` |

## What this proves

- The repo's generic quarterly `roll_days=N` schedules are **not TradingView roll authority**.
- The historical 8-day scheduler's 2026-06-11 M6→U6 seam is earlier than TradingView's marker date for all three roots:
  - MNQ: TradingView 2026-06-15
  - MES: TradingView 2026-06-15
  - M2K: TradingView 2026-06-16
- MNQ and MES share the same TradingView marker dates in both June and September 2026.
- M2K switches one marker-date later than MNQ/MES in both observed 2026 quarters.
- The stitched M2K September corpus seam at `2026-09-15T00:00Z` is **not TradingView-authoritative** and is date-level inconsistent with the `M2K1!` Contract Switch marker dated 2026-09-16.

## What remains unknown

- The exact intraday/UTC instant of each TradingView switch is **not proven by the marker date alone**.
- Existing MNQ/MES live evidence still proves only:
  - U6 before the 2026-09-14 observation gap;
  - Z6 from 2026-09-14T22:00Z onward;
  - no contiguous U6→Z6 boundary in the saved 5m/15m feed evidence.
- For M2K, the box still did not observe the U6→Z6 transition itself; the first available live M2K bars were already Z6. The TradingView marker now proves the **date** (2026-09-16), not the exact switch timestamp.

## Research handling

For proof-quality research:

1. Treat the TradingView Contract Switch marker as authoritative for the **switch date** for the six events above.
2. Do not convert the marker date into an exact UTC seam without independent intraday evidence.
3. Do not use generic 3-day/8-day scheduler rules as continuous-feed provenance.
4. Prefer fixed dated-contract windows or exclude windows whose result depends on the unknown intraday seam.
5. Historical documents that say the M2K switch date itself was unknown are superseded by this note; statements that the **box did not observe the exact transition** remain true.

No proof, no run.
