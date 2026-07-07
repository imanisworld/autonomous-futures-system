"""options_manager FastAPI process — Phase 1 only.

Accepts option trade packets, validates them, journals and notifies. No
broker calls, no order calls, no imports from execution/, webhook/, or
risk/risk_engine.py.
"""

from __future__ import annotations

from fastapi import FastAPI, Request

from .live_lock import assert_live_options_trading_disabled
from .packet_builder import build_packet

# Checked at import/startup time — mirrors the futures system's fail-closed
# pattern. options_manager must not boot at all if this is set true.
assert_live_options_trading_disabled()

app = FastAPI(title="options_manager", version="0.1.0-phase1")


@app.post("/options/packet")
async def receive_packet(request: Request) -> dict:
    raw_input = await request.json()
    packet = build_packet(raw_input)
    return {
        "status": packet.status,
        "rejection_reason": packet.rejection_reason,
        "ticker": packet.ticker,
        "direction": packet.direction,
        "created_at": packet.created_at.isoformat(),
    }


def run() -> None:
    import uvicorn

    from .config import OptionsManagerConfig

    config = OptionsManagerConfig.from_env()
    uvicorn.run("options_manager.app:app", host="0.0.0.0", port=config.port)


if __name__ == "__main__":
    run()
