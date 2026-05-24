# Autonomous Futures System

Phase 0 foundation for an autonomous futures paper-trading system.

The first goal is not profit. The first goal is a system that can survive one
week without crashing, overtrading, violating rules, duplicating positions,
revenge trading, or drifting into disabled sessions.

## Status

Foundation only. No trading engine is included in the initial commit.

## Core Rules

- Paper trading only
- Never enable live trading
- No broker API
- No credentials
- No Asian session trading initially
- No overnight holds
- No averaging down
- Bracket orders only
- `NO_TRADE` is valid

## Allowed Instruments

- `MNQ`
- `MES`
- `MGC`
- `MCL`

## Allowed Sessions

- `london`
- `ny_open`

## Phase Roadmap

1. Phase 0: foundation
2. Phase 1: fake paper broker and fake fills
3. Phase 2: replay engine with historical candles
4. Phase 3: live market data
5. Phase 4: Tradovate simulation connection

Live broker execution is not part of this roadmap.
