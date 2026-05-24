"""
webhook/app.py

FastAPI application — receives TradingView bar-close alerts and routes them
through the paper-trading pipeline.

Endpoints:
    POST /webhook/alert   — main inbound alert handler
    GET  /health          — liveness check
    GET  /status/today    — today's daily state (trade count, losses, open pos)

Start locally:
    python -m webhook              (port 8000)
    uvicorn webhook.app:app --reload --port 8000

Expose to TradingView via ngrok:
    ngrok http 8000
    → use https://<id>.ngrok-free.app/webhook/alert as the alert URL
"""

from __future__ import annotations

import hmac
import json
import logging
import os
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse

from config.settings import load_config
from journal.journal_logger import JournalLogger
from webhook.payload import AlertPayload
from webhook.runner import process_alert
from webhook.state_builder import build_market_state

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Paper Trading Webhook",
    description="TradingView → paper engine → JSONL journal. No live trading.",
    version="1.0.0",
)

# Load config once at startup; fail loudly if LIVE_TRADING_ENABLED=true.
_config = load_config()


# ─── Routes ───────────────────────────────────────────────────────────────────

@app.post("/webhook/alert")
async def receive_alert(
    payload: AlertPayload,
    secret: str | None = Query(default=None),
) -> JSONResponse:
    """
    Accept a bar-close alert from TradingView, run it through the
    paper-trading pipeline, and return a structured decision result.
    """
    _verify_webhook_secret(secret)
    try:
        result = process_alert(payload, config=_config)
        _record_latest_webhook(payload, result)
        return JSONResponse(content={"ok": True, **result})
    except Exception as exc:
        logger.exception("Error processing alert: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/", response_class=HTMLResponse)
async def dashboard() -> HTMLResponse:
    """Read-only local dashboard for today's paper-trading state."""
    status = _dashboard_payload(date.today())
    return HTMLResponse(_render_dashboard(status))


@app.get("/health")
async def health() -> dict:
    """Liveness check. Always returns 200 if the server is running."""
    return {
        "ok": True,
        "live_trading_enabled": _config.live_trading_enabled,
        "paper_mode": _config.paper_mode,
        "webhook_secret_required": bool(_configured_webhook_secret()),
    }


@app.get("/status/today")
async def status_today() -> dict:
    """Return today's reconstructed daily state from the journal."""
    return _dashboard_payload(date.today())


@app.get("/status/history")
async def status_history(days: int = Query(default=7, ge=1, le=30)) -> dict:
    """Return recent read-only daily summaries from the journal."""
    today = date.today()
    history = []
    for offset in range(days):
        day = today - timedelta(days=offset)
        payload = _dashboard_payload(day)
        history.append(
            {
                "date": payload["date"],
                "trade_count": payload["trade_count"],
                "max_trades_per_day": payload["max_trades_per_day"],
                "consecutive_losses": payload["consecutive_losses"],
                "has_open_position": payload["has_open_position"],
                "no_trades": payload["no_trades"],
                "wins": payload["wins"],
                "losses": payload["losses"],
                "realized_pnl_dollars": payload["realized_pnl_dollars"],
            }
        )
    return {"days": history}


@app.get("/status/latest-webhook")
async def latest_webhook() -> dict:
    """Return the last TradingView payload and derived market context."""
    return _latest_webhook_payload()


# ─── Error handlers ───────────────────────────────────────────────────────────

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled exception on %s: %s", request.url.path, exc)
    return JSONResponse(
        status_code=500,
        content={"ok": False, "error": str(exc)},
    )


def _configured_webhook_secret() -> str:
    return os.getenv("WEBHOOK_SECRET", "").strip()


def _verify_webhook_secret(provided: str | None) -> None:
    expected = _configured_webhook_secret()
    if not expected:
        return
    if not provided or not hmac.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="Invalid webhook secret")


def _dashboard_payload(for_date: date) -> dict:
    journal = JournalLogger(log_dir=_config.log_dir)
    daily_state = journal.get_daily_state(for_date)
    summary = journal.get_summary(for_date)
    path = journal._journal_path(for_date)
    entries = journal._read_entries(path) if path.exists() else []
    recent_entries = entries[-10:]
    no_trade_reasons = Counter(
        entry.get("reason", "Unknown")
        for entry in entries
        if entry.get("decision") == "NO_TRADE"
    )
    realized_pnl = sum(
        float((entry.get("outcome") or {}).get("pnl_dollars") or 0.0)
        for entry in entries
    )
    return {
        "date": daily_state.date,
        "live_trading_enabled": _config.live_trading_enabled,
        "paper_mode": _config.paper_mode,
        "trade_count": daily_state.trade_count,
        "max_trades_per_day": _config.max_trades_per_day,
        "consecutive_losses": daily_state.consecutive_losses,
        "max_consecutive_losses": _config.max_consecutive_losses,
        "has_open_position": daily_state.has_open_position,
        "open_position": journal.get_open_position(for_date),
        "no_trades": summary.get("no_trades", 0),
        "wins": summary.get("wins", 0),
        "losses": summary.get("losses", 0),
        "realized_pnl_dollars": round(realized_pnl, 2),
        "journal_path": summary.get("journal_path", str(path)),
        "latest_entries": [_public_entry(entry) for entry in recent_entries],
        "top_no_trade_reasons": [
            {"reason": reason, "count": count}
            for reason, count in no_trade_reasons.most_common(5)
        ],
        "latest_webhook": _latest_webhook_payload(),
    }


def _public_entry(entry: dict) -> dict:
    outcome = entry.get("outcome") or {}
    setup = entry.get("setup") or {}
    return {
        "ts": entry.get("ts"),
        "type": entry.get("type", "DECISION"),
        "instrument": entry.get("instrument"),
        "session": entry.get("session"),
        "decision": entry.get("decision"),
        "reason": entry.get("reason"),
        "market_condition": entry.get("market_condition"),
        "strategy": setup.get("strategy"),
        "outcome": outcome.get("result"),
        "pnl_dollars": outcome.get("pnl_dollars"),
    }


def _render_dashboard(status: dict) -> str:
    latest_rows = "\n".join(_render_entry_row(entry) for entry in status["latest_entries"])
    reason_rows = "\n".join(
        f"<li><span>{_escape(item['reason'])}</span><strong>{item['count']}</strong></li>"
        for item in status["top_no_trade_reasons"]
    ) or "<li><span>No NO_TRADE reasons yet</span><strong>0</strong></li>"
    lockout = status["consecutive_losses"] >= status["max_consecutive_losses"]
    trade_full = status["trade_count"] >= status["max_trades_per_day"]
    open_position = status["open_position"] or {}
    open_position_text = (
        f"{open_position.get('direction')} {open_position.get('instrument')} "
        f"@ {open_position.get('entry')}"
        if open_position else "None"
    )
    latest_webhook = status.get("latest_webhook") or {}
    webhook_context = latest_webhook.get("context") or {}
    webhook_result = latest_webhook.get("result") or {}
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="refresh" content="15">
  <title>RiskSentinel</title>
  <style>
    :root {{
      color-scheme: dark;
      --bg: #050507;
      --panel: #111216;
      --line: #2a2d34;
      --text: #f4f4f5;
      --muted: #a1a1aa;
      --cyan: #00d5ff;
      --green: #17d97f;
      --amber: #ffb020;
      --red: #ff3d71;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      min-height: 100vh;
      background: var(--bg);
      color: var(--text);
      font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      letter-spacing: 0;
    }}
    main {{
      width: min(1120px, calc(100% - 32px));
      margin: 0 auto;
      padding: 28px 0 40px;
    }}
    header {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      margin-bottom: 22px;
    }}
    h1, h2, p {{ margin: 0; }}
    h1 {{ font-size: 24px; line-height: 1.1; }}
    h2 {{ color: var(--muted); font-size: 13px; font-weight: 700; text-transform: uppercase; }}
    .sub {{ color: var(--muted); margin-top: 5px; font-size: 13px; }}
    .badge {{
      border: 1px solid var(--line);
      border-radius: 999px;
      color: var(--green);
      padding: 8px 12px;
      font-size: 13px;
      white-space: nowrap;
    }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 12px;
      margin-bottom: 14px;
    }}
    .panel {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 16px;
    }}
    .metric {{ font-size: 32px; font-weight: 800; margin-top: 10px; }}
    .metric small {{ color: var(--muted); font-size: 18px; }}
    .cyan {{ color: var(--cyan); }}
    .green {{ color: var(--green); }}
    .amber {{ color: var(--amber); }}
    .red {{ color: var(--red); }}
    .wide {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 12px;
      margin-bottom: 14px;
    }}
    ul {{ list-style: none; padding: 0; margin: 12px 0 0; }}
    li {{
      display: flex;
      justify-content: space-between;
      gap: 12px;
      padding: 10px 0;
      border-top: 1px solid var(--line);
      color: var(--muted);
      font-size: 14px;
    }}
    li strong {{ color: var(--text); }}
    table {{
      width: 100%;
      border-collapse: collapse;
      margin-top: 12px;
      font-size: 13px;
    }}
    th, td {{
      text-align: left;
      border-top: 1px solid var(--line);
      padding: 10px 8px;
      vertical-align: top;
    }}
    th {{ color: var(--muted); font-size: 12px; text-transform: uppercase; }}
    td.reason {{ color: var(--muted); max-width: 440px; }}
    .rules {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 8px;
      margin-top: 12px;
    }}
    .rule {{
      border-top: 1px solid var(--line);
      padding-top: 10px;
      color: var(--muted);
      font-size: 14px;
    }}
    .rule span {{ color: var(--green); margin-right: 8px; }}
    .rule.danger span {{ color: var(--red); }}
    .context-grid {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 10px;
      margin-top: 12px;
    }}
    .context-item {{
      border-top: 1px solid var(--line);
      padding-top: 10px;
    }}
    .context-item label {{
      display: block;
      color: var(--muted);
      font-size: 12px;
      text-transform: uppercase;
      margin-bottom: 5px;
    }}
    .context-item strong {{
      display: block;
      color: var(--text);
      font-size: 14px;
      min-height: 20px;
      overflow-wrap: anywhere;
    }}
    @media (max-width: 760px) {{
      header, .wide {{ grid-template-columns: 1fr; display: grid; }}
      .grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      table {{ font-size: 12px; }}
      .rules {{ grid-template-columns: 1fr; }}
      .context-grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
    }}
  </style>
</head>
<body>
  <main>
    <header>
      <div>
        <h1>RiskSentinel</h1>
        <p class="sub">{_escape(status['date'])} · Paper-only futures monitor</p>
      </div>
      <div class="badge">LIVE TRADING OFF</div>
    </header>

    <section class="grid">
      <div class="panel">
        <h2>Session P/L</h2>
        <div class="metric {'green' if status['realized_pnl_dollars'] >= 0 else 'red'}">${status['realized_pnl_dollars']:.2f}</div>
      </div>
      <div class="panel">
        <h2>Trades</h2>
        <div class="metric cyan">{status['trade_count']}<small>/{status['max_trades_per_day']}</small></div>
      </div>
      <div class="panel">
        <h2>Loss Streak</h2>
        <div class="metric {'red' if lockout else 'amber'}">{status['consecutive_losses']}<small>/{status['max_consecutive_losses']}</small></div>
      </div>
      <div class="panel">
        <h2>NO_TRADE</h2>
        <div class="metric">{status['no_trades']}</div>
      </div>
    </section>

    <section class="wide">
      <div class="panel">
        <h2>Rule State</h2>
        <div class="rules">
          <div class="rule {'danger' if trade_full else ''}"><span>●</span>Max 3 trades/day</div>
          <div class="rule {'danger' if lockout else ''}"><span>●</span>2-loss lockout</div>
          <div class="rule {'danger' if status['has_open_position'] else ''}"><span>●</span>Open position: {_escape(open_position_text)}</div>
          <div class="rule"><span>●</span>Paper mode: {str(status['paper_mode']).lower()}</div>
          <div class="rule"><span>●</span>Live trading: {str(status['live_trading_enabled']).lower()}</div>
          <div class="rule"><span>●</span>Bracket-only engine</div>
        </div>
      </div>
      <div class="panel">
        <h2>Top NO_TRADE Reasons</h2>
        <ul>{reason_rows}</ul>
      </div>
    </section>

    <section class="panel" style="margin-bottom: 14px;">
      <h2>Latest Webhook Context</h2>
      <div class="context-grid">
        <div class="context-item"><label>Received</label><strong>{_escape(latest_webhook.get('received_at') or 'None')}</strong></div>
        <div class="context-item"><label>Decision</label><strong>{_escape(webhook_result.get('decision') or 'None')}</strong></div>
        <div class="context-item"><label>Symbol</label><strong>{_escape(webhook_context.get('instrument') or 'None')}</strong></div>
        <div class="context-item"><label>Session</label><strong>{_escape(webhook_context.get('session') or 'None')}</strong></div>
        <div class="context-item"><label>Close</label><strong>{_escape(webhook_context.get('close') or 'None')}</strong></div>
        <div class="context-item"><label>VWAP</label><strong>{_escape(_format_vwap(webhook_context.get('vwap')))}</strong></div>
        <div class="context-item"><label>ORB</label><strong>{_escape(_format_orb(webhook_context.get('orb')))}</strong></div>
        <div class="context-item"><label>Trend</label><strong>{_escape(_format_trend(webhook_context.get('trend')))}</strong></div>
        <div class="context-item"><label>Market</label><strong>{_escape(webhook_context.get('market_condition') or 'None')}</strong></div>
        <div class="context-item"><label>PDH/PDL</label><strong>{_escape(_format_previous_day(webhook_context.get('previous_day')))}</strong></div>
        <div class="context-item"><label>Volume</label><strong>{_escape(_format_volume(webhook_context.get('volume')))}</strong></div>
        <div class="context-item"><label>Risk</label><strong>{_escape(webhook_result.get('risk') or 'None')}</strong></div>
      </div>
    </section>

    <section class="panel">
      <h2>Latest Journal Entries</h2>
      <table>
        <thead>
          <tr>
            <th>Time</th>
            <th>Symbol</th>
            <th>Session</th>
            <th>Decision</th>
            <th>Reason</th>
            <th>Outcome</th>
          </tr>
        </thead>
        <tbody>{latest_rows or '<tr><td colspan="6">No journal entries yet.</td></tr>'}</tbody>
      </table>
    </section>
  </main>
</body>
</html>"""


def _render_entry_row(entry: dict) -> str:
    outcome = entry.get("outcome") or ""
    pnl = entry.get("pnl_dollars")
    if pnl is not None:
        outcome = f"{outcome} ${float(pnl):.2f}"
    return (
        "<tr>"
        f"<td>{_escape(_short_time(entry.get('ts')))}</td>"
        f"<td>{_escape(entry.get('instrument') or '')}</td>"
        f"<td>{_escape(entry.get('session') or '')}</td>"
        f"<td>{_escape(entry.get('decision') or entry.get('type') or '')}</td>"
        f"<td class=\"reason\">{_escape(entry.get('reason') or '')}</td>"
        f"<td>{_escape(outcome)}</td>"
        "</tr>"
    )


def _short_time(value: str | None) -> str:
    if not value:
        return ""
    return value.replace("T", " ").split(".")[0].replace("+00:00", "Z")


def _format_vwap(value: dict | None) -> str:
    if not value:
        return "None"
    return f"{value.get('value')} · {value.get('price_vs_vwap')}"


def _format_orb(value: dict | None) -> str:
    if not value:
        return "None"
    return f"H {value.get('high')} / L {value.get('low')} · {value.get('status')}"


def _format_trend(value: dict | None) -> str:
    if not value:
        return "None"
    return f"{value.get('direction') or 'None'} · {value.get('strength') or 'None'}"


def _format_previous_day(value: dict | None) -> str:
    if not value:
        return "None"
    return (
        f"H {value.get('high')} / L {value.get('low')} · "
        f"PDH {value.get('price_vs_pdh')} / PDL {value.get('price_vs_pdl')}"
    )


def _format_volume(value: dict | None) -> str:
    if not value:
        return "None"
    relative = value.get("relative")
    relative_text = f"{float(relative):.2f}x" if isinstance(relative, (int, float)) else "None"
    return f"{value.get('current_bar')} / avg {value.get('avg_bar')} · {relative_text}"


def _record_latest_webhook(payload: AlertPayload, result: dict) -> None:
    path = _latest_webhook_path()
    state = build_market_state(payload)
    data = {
        "received_at": datetime.now(timezone.utc).isoformat(),
        "payload": _payload_to_dict(payload),
        "context": {
            "instrument": state.instrument,
            "session": state.session,
            "timestamp": state.timestamp.isoformat(),
            "close": state.ohlc.close,
            "timeframe": state.ohlc.timeframe,
            "vwap": {
                "value": state.vwap.value,
                "price_vs_vwap": state.vwap.price_vs_vwap,
                "reclaimed": state.vwap.reclaimed,
                "holding": state.vwap.holding,
            },
            "orb": {
                "high": state.orb.high,
                "low": state.orb.low,
                "status": state.orb.status,
            },
            "trend": {
                "direction": state.trend.direction if state.trend else None,
                "strength": state.trend.strength if state.trend else None,
            },
            "market_condition": state.market_condition,
            "previous_day": {
                "high": state.previous_day.high,
                "low": state.previous_day.low,
                "close": state.previous_day.close,
                "price_vs_pdh": state.previous_day.price_vs_pdh,
                "price_vs_pdl": state.previous_day.price_vs_pdl,
            },
            "volume": {
                "current_bar": state.volume.current_bar,
                "avg_bar": state.volume.avg_bar,
                "relative": state.volume.relative,
            },
        },
        "result": {
            "decision": result.get("decision"),
            "resolution": result.get("resolution"),
            "risk": result.get("risk"),
            "fill": result.get("fill"),
        },
    }
    path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def _latest_webhook_payload() -> dict:
    path = _latest_webhook_path()
    if not path.exists():
        return {"received_at": None, "payload": None, "context": None, "result": None}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"received_at": None, "payload": None, "context": None, "result": None}


def _latest_webhook_path() -> Path:
    path = Path(_config.log_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path / "latest_webhook.json"


def _payload_to_dict(payload: AlertPayload) -> dict:
    if hasattr(payload, "model_dump"):
        return payload.model_dump()
    return payload.dict()


def _escape(value: object) -> str:
    text = "" if value is None else str(value)
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#x27;")
    )
