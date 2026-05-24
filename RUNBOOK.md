# Runbook

## Before Work Starts

1. Read `AGENT_CONTEXT.md`.
2. Read `FUTURES_SYSTEM_RULEBOOK.md`.
3. Confirm the task does not enable live trading.
4. Confirm the task does not introduce broker credentials.
5. If logic is unclear, stop and ask.

## Environment

Required local tools:

- Python 3.12+
- VS Code
- Git
- Docker Desktop
- Claude Code
- GitHub Desktop optional

Create a virtual environment:

```bash
python -m venv venv
source venv/bin/activate
```

Install basics:

```bash
pip install fastapi uvicorn pandas pydantic pyyaml websockets loguru pytest
```

## Safety Checklist

- `allow_live_trading` is false.
- `.env.example` contains no credentials.
- No broker SDK is required in Phase 0.
- No executable live order path exists.
- `NO_TRADE` is valid.

## Next Build Order

1. Fake paper broker
2. Fake fills
3. Replay engine
4. Live market data later
5. Tradovate simulation later
