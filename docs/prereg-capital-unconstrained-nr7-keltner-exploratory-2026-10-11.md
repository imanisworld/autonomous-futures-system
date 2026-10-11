# Prereg — capital-unconstrained NR7 + Keltner exploratory (MNQ)

**Trial ID:** `T-2026-10-11-exploratory-nr7-keltner-capital-unconstrained-01`

**Status:** Exploratory historical research only. Not forward validation. No strategy inventory or promotion authority.

## Population

- Instrument: MNQ
- Data: `data/replay_polygon_v2/MNQ` 15m, RTH daily/60m aggregates as specified per variant
- Window: through **2026-06-26** inclusive; **no** bars on or after **2026-06-29** (MNQ seal)

## Variants (frozen before scoring)

1. **nr7_inside_day** — ChartArt NR7ID Pine; structural stop at signal-day low; Pine bullish-day exit at next session open; scripts `nr7_inside_day_mnq_research_2026_10_11.py`
2. **keltner_kc_long** — Vault Hybrid-Keltner Pine **Long_KC** leg only; 60m RTH; ATR stop/target; scripts `keltner_kc_long_mnq_research_2026_10_11.py`

## Decision criteria (pre-declared)

- No parameter optimization or tick-cap research filters
- **RESEARCH_ONLY** / **RETIRE** if net ≤ 0 or PF < 1 on Keltner; NR7 if n < 20 regardless of sign
- Does not authorize deploy, Track B changes, or live trading

## Artifacts

- Exploratory notes: `docs/research-notes/capital-unconstrained-strategy-research-2026-10-11/`
- Canonical result: `docs/research-evidence/T-2026-10-11-exploratory-nr7-keltner-capital-unconstrained-01/result.json`
