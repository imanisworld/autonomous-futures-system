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
import time
from collections import Counter
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from agent.daily_summary import DailySummaryAgent, validate_review_date
from config.settings import load_config
from journal.journal_logger import JournalLogger
from notifications.discord_notifier import notify_discord
from webhook.payload import AlertPayload
from webhook.runner import process_alert

logger = logging.getLogger(__name__)
_RATE_BUCKETS: dict[tuple[str, str], list[float]] = {}
_PUBLIC_RATE_LIMIT = int(os.getenv("PUBLIC_RATE_LIMIT_PER_MINUTE", "120") or 120)
_WEBHOOK_RATE_LIMIT = int(os.getenv("WEBHOOK_RATE_LIMIT_PER_MINUTE", "60") or 60)
_PRIVATE_RATE_LIMIT = int(os.getenv("PRIVATE_RATE_LIMIT_PER_MINUTE", "240") or 240)


def _configured_webhook_secret() -> str:
    return os.getenv("WEBHOOK_SECRET", "").strip()


def _verify_webhook_secret(provided: str | None) -> None:
    expected = _configured_webhook_secret()
    # No secret configured → reject every inbound webhook unconditionally.
    # A blank secret means the endpoint is public; that is never acceptable.
    if not expected:
        raise HTTPException(status_code=401, detail="WEBHOOK_SECRET is not configured.")
    if not provided or not hmac.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="Invalid webhook secret")


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "").split(",", 1)[0].strip()
    if forwarded:
        return forwarded
    return request.client.host if request.client else "unknown"


def _rate_bucket_for_path(path: str) -> tuple[str, int]:
    if path == "/webhook/alert":
        return "webhook", _WEBHOOK_RATE_LIMIT
    if path in {"/health", "/status/public", "/share", "/favicon.ico", "/manifest.json"} or path.startswith("/static/"):
        return "public", _PUBLIC_RATE_LIMIT
    return "private", _PRIVATE_RATE_LIMIT


def _enforce_rate_limit(request: Request) -> None:
    bucket, limit = _rate_bucket_for_path(request.url.path)
    if limit <= 0:
        return
    now = time.monotonic()
    cutoff = now - 60.0
    key = (_client_ip(request), bucket)
    hits = [stamp for stamp in _RATE_BUCKETS.get(key, []) if stamp >= cutoff]
    if len(hits) >= limit:
        raise HTTPException(status_code=429, detail="Rate limit exceeded")
    hits.append(now)
    _RATE_BUCKETS[key] = hits



@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Startup gate: refuse to serve if WEBHOOK_SECRET is blank."""
    if not _configured_webhook_secret():
        raise RuntimeError(
            "WEBHOOK_SECRET env var is required but not set. "
            "Set it in Railway dashboard before deploying."
        )
    yield


app = FastAPI(
    title="Paper Trading Webhook",
    description="TradingView → paper engine → JSONL journal. No live trading.",
    version="1.0.0",
    lifespan=_lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def _rate_limit_middleware(request: Request, call_next):
    try:
        _enforce_rate_limit(request)
    except HTTPException as exc:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    return await call_next(request)


_static = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=_static), name="static")


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    from fastapi.responses import FileResponse
    return FileResponse(str(_static / "icon-192.png"), media_type="image/png")


@app.get("/manifest.json", include_in_schema=False)
async def pwa_manifest():
    return JSONResponse({
        "name": "RiskSentinel",
        "short_name": "RiskSentinel",
        "description": "Paper futures trading dashboard",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#050507",
        "theme_color": "#00d5ff",
        "icons": [
            {"src": "/static/icon-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "/static/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"},
        ],
    })


# Load config once at startup; fail loudly if LIVE_TRADING_ENABLED=true.
_config = load_config()


# ─── Routes ───────────────────────────────────────────────────────────────────

@app.post("/webhook/alert")
async def receive_alert(
    payload: AlertPayload,
    x_webhook_secret: str | None = Header(default=None),
    secret: str | None = Query(default=None),
) -> JSONResponse:
    """
    Accept a bar-close alert from TradingView, run it through the
    paper-trading pipeline, and return a structured decision result.
    """
    _verify_webhook_secret(x_webhook_secret or secret)
    # Silently ignore non-futures tickers (e.g. stock alerts sharing the same webhook)
    _FUTURES_PREFIXES = {"MNQ", "MES", "ES", "NQ", "MGC", "MCL"}
    ticker_root = payload.ticker.upper().replace("1!", "").replace("!", "").strip()
    if not any(ticker_root.startswith(p) for p in _FUTURES_PREFIXES):
        return JSONResponse(content={"ok": True, "decision": "IGNORED", "reason": "non-futures ticker"})
    try:
        result = process_alert(payload, config=_config, log_dir=_config.log_dir)
        _record_latest_webhook(payload, result)
        notify_discord(payload=payload, result=result, config=_config)
        return JSONResponse(content={"ok": True, **result})
    except Exception as exc:
        logger.exception("Error processing alert: %s", exc)
        raise HTTPException(status_code=500, detail="Internal server error") from exc


@app.get("/futures", include_in_schema=False)
async def futures_redirect():
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/", status_code=301)


@app.get("/", response_class=HTMLResponse)
async def dashboard() -> HTMLResponse:
    """Read-only operator dashboard."""
    status = _dashboard_payload(date.today())
    return HTMLResponse(_render_dashboard(status))


@app.get("/health")
async def health() -> dict:
    """Liveness check. Returns safe status only."""
    import os
    broker_mode = os.getenv("BROKER", "paper").strip().lower()
    return {
        "ok": True,
        "paper_mode": _config.paper_mode,
        "live_trading_enabled": _config.live_trading_enabled,
        "broker": broker_mode,
        "broker_gateway_reachable": _ibkr_gateway_reachable(),
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
                "win_rate": payload["win_rate"],
            }
        )
    return {"days": history}


@app.get("/status/latest-webhook")
async def latest_webhook() -> dict:
    """Return the last TradingView payload and derived market context."""
    return _latest_webhook_payload()


@app.get("/status/strategy")
async def strategy_status() -> dict:
    """Return enabled strategy concepts and journal-derived strategy counts."""
    return _strategy_payload(date.today())


@app.get("/status/diagnostics")
async def status_diagnostics() -> dict:
    """Return plain-English component health and likely break reasons."""
    return _diagnostics_payload(date.today())


@app.get("/status/review")
async def status_review(
    review_date: str | None = Query(default=None, alias="date"),
    mode: str = Query(default="eod", pattern="^(morning|eod)$"),
) -> dict:
    """Return a read-only morning or end-of-day review report."""
    try:
        target_date = validate_review_date(review_date or date.today().isoformat())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    agent = DailySummaryAgent(_config)
    agent.log_dir = Path(_config.log_dir)
    agent.risk_reviewer.config.log_dir = _config.log_dir
    agent.trade_grader.config.log_dir = _config.log_dir
    return agent.preview_morning(target_date) if mode == "morning" else agent.preview_eod(target_date)


# ─── Adaptive committee endpoints ────────────────────────────────────────────



@app.get("/status/signa")
async def status_signa(symbol: str = Query(default="AAPL")) -> dict:
    """Read-only Signa connectivity and parsing check."""
    from sources.signa_client import SignaClient

    client = SignaClient(
        base_url=_config.signa_base_url,
        timeout=_config.signa_timeout_seconds,
    )
    if not _config.signa_api_enabled:
        return {
            "enabled": False,
            "configured": _config.signa_api_key_configured,
            "symbol": symbol.upper(),
            "ok": False,
            "error": "signa_api_disabled",
        }
    signal = client.fetch_signal(symbol.upper())
    return {
        "enabled": True,
        "configured": client.configured,
        **signal.to_dict(),
    }


@app.get("/status/risk")
async def status_risk() -> dict:
    """Return risk limits (config) and today's journal-derived risk state."""
    journal = JournalLogger(log_dir=_config.log_dir)
    today = date.today()
    daily_state = journal.get_daily_state(today)
    account_balance = journal.get_account_balance(_config.position_sizing.starting_balance, today)
    account_peak = journal.get_account_peak_balance(_config.position_sizing.starting_balance, today)

    daily_loss_used = max(0.0, account_peak - account_balance)
    drawdown_pct = round(((account_peak - account_balance) / account_peak * 100), 2) if account_peak else 0.0
    max_dd_pct = _config.max_drawdown_percent * 100
    drawdown_state = (
        "CRITICAL" if drawdown_pct >= max_dd_pct
        else "ELEVATED" if drawdown_pct >= (max_dd_pct * 0.5)
        else "NORMAL"
    )

    today_str = today.isoformat()
    news_blackout = (
        _config.news_blackout_mode != "off"
        and today_str in (_config.news_blackout_dates or [])
    )

    return {
        "max_daily_loss": _config.max_daily_loss,
        "daily_loss_used": round(daily_loss_used, 2),
        "drawdown_state": drawdown_state,
        "drawdown_pct": drawdown_pct,
        "max_trades": _config.max_trades_per_day + int(getattr(_config, "bonus_trades_after_max", 0) or 0),
        "bonus_trades": getattr(_config, "bonus_trades_after_max", 0),
        "consecutive_losses": daily_state.consecutive_losses,
        "max_consecutive_losses": _config.max_consecutive_losses,
        "session_restrictions": [],
        "news_blackout": news_blackout,
        "news_blackout_reason": (
            f"{_config.news_blackout_mode.upper()} — cutoff {_config.news_blackout_cutoff_et} ET"
            if news_blackout else None
        ),
    }


@app.get("/status/adaptive")
async def status_adaptive() -> dict:
    """Run the Adaptive Risk Committee and return the latest report."""
    from adaptive.committee import AdaptiveCommittee
    committee = AdaptiveCommittee(log_dir=_config.log_dir, config=_config)
    report = committee.run_and_persist(days=30)
    return report.to_dict()


@app.get("/status/adaptive/history")
async def status_adaptive_history(days: int = Query(default=7, ge=1, le=30)) -> dict:
    """Return cached committee reports from the last N days."""
    from adaptive.committee import AdaptiveCommittee
    committee = AdaptiveCommittee(log_dir=_config.log_dir, config=_config)
    return {"days": committee.load_history(days=days)}


# ─── Public / shareable endpoints ────────────────────────────────────────────


@app.get("/status/public")
async def status_public() -> dict:
    """Sanitized read-only status — no auth required, safe to share."""
    status = _dashboard_payload(date.today())
    webhook = status.get("latest_webhook") or {}
    received_at = (webhook.get("received_at") or "") if isinstance(webhook, dict) else ""
    return {
        "ok": True,
        "online": True,
        "mode": "paper" if status.get("paper_mode") else "live",
        "today_pnl": round(float(status.get("today_pnl_dollars") or 0), 2),
        "trades": status.get("trade_count", 0),
        "max_trades": status.get("max_trades_per_day", 0),
        "win_rate": status.get("win_rate", 0),
        "wins": status.get("wins", 0),
        "losses": status.get("losses", 0),
        "last_signal": _format_generated_age(received_at) if received_at else None,
    }


@app.get("/share", response_class=HTMLResponse)
async def share_dashboard() -> HTMLResponse:
    """Public shareable view — shows safe summary, no private data."""
    status = _dashboard_payload(date.today())
    webhook = status.get("latest_webhook") or {}
    received_at = (webhook.get("received_at") or "") if isinstance(webhook, dict) else ""
    mode = "Paper" if status.get("paper_mode") else "Live"
    pnl = round(float(status.get("today_pnl_dollars") or 0), 2)
    pnl_sign = "+" if pnl >= 0 else ""
    trades = status.get("trade_count", 0)
    max_trades = status.get("max_trades_per_day", 0)
    win_rate = status.get("win_rate", 0)
    last_signal = _format_generated_age(received_at) if received_at else "No recent signal"

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>RiskSentinel</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ background: #1A2C1E; color: #EEF2E8; font-family: 'IBM Plex Mono', 'Courier New', monospace; display: flex; justify-content: center; align-items: center; min-height: 100vh; padding: 24px; }}
    .card {{ background: #2C4430; border: 1px solid #3A5C3E; border-radius: 12px; padding: 28px 24px; max-width: 360px; width: 100%; box-shadow: 0 6px 24px rgba(0,0,0,0.5); }}
    .header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; }}
    .title {{ font-size: 13px; letter-spacing: 2px; color: #A8B8A0; font-weight: 600; }}
    .dot {{ width: 8px; height: 8px; border-radius: 50%; background: #2AE87C; box-shadow: 0 0 6px #2AE87C; }}
    .badge {{ font-size: 10px; background: #344C3C; border: 1px solid #3A5C3E; padding: 3px 8px; border-radius: 4px; color: #A8B8A0; letter-spacing: 1px; }}
    .row {{ display: flex; justify-content: space-between; align-items: baseline; padding: 10px 0; border-bottom: 1px solid #3A5C3E40; }}
    .row:last-child {{ border-bottom: none; }}
    .label {{ font-size: 10px; color: #607864; letter-spacing: 1px; }}
    .value {{ font-size: 14px; font-weight: 700; }}
    .good {{ color: #2AE87C; }}
    .bad {{ color: #FF4444; }}
    .muted {{ color: #A8B8A0; }}
    .footer {{ margin-top: 18px; font-size: 9px; color: #607864; letter-spacing: 1px; text-align: center; }}
  </style>
</head>
<body>
  <div class="card">
    <div class="header">
      <span class="title">RISKSENTINEL</span>
      <div style="display:flex;gap:8px;align-items:center">
        <span class="badge">{_escape(mode.upper())}</span>
        <span class="dot"></span>
      </div>
    </div>
    <div class="row">
      <span class="label">TODAY P&amp;L</span>
      <span class="value {'good' if pnl >= 0 else 'bad'}">{pnl_sign}${abs(pnl):,.2f}</span>
    </div>
    <div class="row">
      <span class="label">TRADES</span>
      <span class="value muted">{trades} / {max_trades}</span>
    </div>
    <div class="row">
      <span class="label">WIN RATE</span>
      <span class="value muted">{win_rate:.1f}%</span>
    </div>
    <div class="row">
      <span class="label">LAST SIGNAL</span>
      <span class="value muted">{_escape(last_signal)}</span>
    </div>
    <div class="footer">READ-ONLY · PAPER SYSTEM · AUTOMATED FUTURES TRADING</div>
  </div>
</body>
</html>"""
    return HTMLResponse(html)


# ─── Error handlers ───────────────────────────────────────────────────────────

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled exception on %s: %s", request.url.path, exc)
    return JSONResponse(
        status_code=500,
        content={"ok": False, "error": "Internal server error"},
    )


def _ibkr_gateway_reachable() -> bool | None:
    """Return True/False if BROKER=ibkr; None if not using IBKR."""
    if os.getenv("BROKER", "paper").strip().lower() != "ibkr":
        return None
    try:
        import socket
        host = os.getenv("IBKR_HOST", "127.0.0.1")
        port = int(os.getenv("IBKR_PORT", "4002"))
        with socket.socket() as s:
            s.settimeout(0.5)
            return s.connect_ex((host, port)) == 0
    except Exception:
        return False


def _diagnostic(status: str, component: str, message: str, next_step: str | None = None) -> dict:
    item = {
        "status": status,
        "component": component,
        "message": message,
    }
    if next_step:
        item["next_step"] = next_step
    return item


def _latest_webhook_age_seconds() -> int | None:
    latest = _latest_webhook_payload()
    received_at = latest.get("received_at")
    if not received_at:
        return None
    try:
        received = datetime.fromisoformat(str(received_at).replace("Z", "+00:00"))
        if received.tzinfo is None:
            received = received.replace(tzinfo=timezone.utc)
        return max(0, int((datetime.now(timezone.utc) - received).total_seconds()))
    except (TypeError, ValueError):
        return None


def _diagnostics_payload(for_date: date) -> dict:
    broker = os.getenv("BROKER", "paper").strip().lower()
    gateway = _ibkr_gateway_reachable()
    latest_age = _latest_webhook_age_seconds()
    journal = JournalLogger(log_dir=_config.log_dir)
    journal_path = journal._journal_path(for_date)
    items = [
        _diagnostic("ok", "Backend API", "FastAPI is responding on the public API routes."),
    ]

    if _config.live_trading_enabled:
        items.append(_diagnostic(
            "error",
            "Trading mode",
            "Live trading is enabled. This system should be paper-only right now.",
            "Set LIVE_TRADING_ENABLED=false and PAPER_MODE=true, then restart the service.",
        ))
    elif _config.paper_mode:
        items.append(_diagnostic("ok", "Trading mode", "Paper mode is active; live trading is off."))
    else:
        items.append(_diagnostic(
            "warn",
            "Trading mode",
            "Live trading is off, but PAPER_MODE is not true.",
            "Set PAPER_MODE=true so the dashboard state is unambiguous.",
        ))

    if _configured_webhook_secret():
        items.append(_diagnostic("ok", "Webhook secret", "TradingView webhook protection is configured."))
    else:
        items.append(_diagnostic(
            "error",
            "Webhook secret",
            "WEBHOOK_SECRET is missing, so inbound alerts will be rejected.",
            "Set WEBHOOK_SECRET on the server and include it in the TradingView webhook URL.",
        ))

    if broker == "ibkr":
        if gateway is True:
            items.append(_diagnostic("ok", "IBKR gateway", "IBKR Gateway is reachable from the backend."))
        elif gateway is False:
            items.append(_diagnostic(
                "error",
                "IBKR gateway",
                "BROKER=ibkr, but the backend cannot reach IBKR Gateway.",
                "Check IBKR Gateway/TWS is running and IBKR_HOST/IBKR_PORT match the server.",
            ))
        else:
            items.append(_diagnostic("warn", "IBKR gateway", "IBKR gateway status is unknown."))
    else:
        items.append(_diagnostic("ok", "Broker", f"Broker is set to {broker}; IBKR gateway is not required."))

    if _config.discord_notifications_enabled and not _config.discord_webhook_url:
        items.append(_diagnostic(
            "warn",
            "Discord alerts",
            "Discord alerts are enabled, but no Discord webhook URL is configured.",
            "Set DISCORD_WEBHOOK_URL or turn DISCORD_NOTIFICATIONS_ENABLED=false.",
        ))
    elif _config.discord_notifications_enabled:
        items.append(_diagnostic("ok", "Discord alerts", "Discord alerts are enabled and have a webhook URL."))
    else:
        items.append(_diagnostic("info", "Discord alerts", "Discord alerts are off."))

    if _config.signa_api_enabled and not _config.signa_api_key_configured:
        items.append(_diagnostic(
            "warn",
            "Signa",
            "Signa is enabled, but the API key is not configured.",
            "Set SIGNA_API_KEY or turn SIGNA_API_ENABLED=false.",
        ))
    elif _config.signa_api_enabled:
        items.append(_diagnostic("ok", "Signa", "Signa is enabled and configured."))
    else:
        items.append(_diagnostic("info", "Signa", "Signa is off."))

    if latest_age is None:
        items.append(_diagnostic(
            "warn",
            "TradingView alerts",
            "No TradingView webhook has been received yet.",
            "Check the TradingView alert URL and webhook secret.",
        ))
    elif latest_age > 15 * 60:
        items.append(_diagnostic(
            "warn",
            "TradingView alerts",
            f"Last TradingView webhook was {_format_generated_age(_latest_webhook_payload().get('received_at'))}.",
            "If the market is open, check whether the TradingView alert is still running.",
        ))
    else:
        items.append(_diagnostic("ok", "TradingView alerts", "A TradingView webhook was received recently."))

    if journal_path.exists():
        items.append(_diagnostic("ok", "Journal", f"Today journal exists at {journal_path}."))
    else:
        items.append(_diagnostic("info", "Journal", "No journal file exists yet today; no decisions have been recorded."))

    rank = {"error": 3, "warn": 2, "info": 1, "ok": 0}
    worst = max(items, key=lambda item: rank.get(item["status"], 0))
    overall = "error" if worst["status"] == "error" else "warn" if worst["status"] == "warn" else "ok"
    return {
        "ok": overall == "ok",
        "overall_status": overall,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "top_issue": None if overall == "ok" else worst,
        "items": items,
    }


def _dashboard_payload(for_date: date) -> dict:
    journal = JournalLogger(log_dir=_config.log_dir)
    daily_state = journal.get_daily_state(for_date)
    summary = journal.get_summary(for_date)
    path = journal._journal_path(for_date)
    entries = journal._read_entries(path) if path.exists() else []
    # Only show DECISION entries in the table (OUTCOME entries are ephemeral close records)
    decision_entries = [e for e in entries if e.get("type") != "OUTCOME"]
    recent_entries = decision_entries[-10:]
    no_trade_reasons = Counter(
        entry.get("reason", "Unknown")
        for entry in decision_entries
        if entry.get("decision") == "NO_TRADE"
    )
    wins = summary.get("wins", 0)
    losses = summary.get("losses", 0)
    resolved = wins + losses
    win_rate = round((wins / resolved) * 100, 1) if resolved else 0.0
    account_balance = journal.get_account_balance(_config.position_sizing.starting_balance, for_date)
    account_peak = journal.get_account_peak_balance(_config.position_sizing.starting_balance, for_date)
    realized_pnl = round(account_balance - _config.position_sizing.starting_balance, 2)
    diagnostics = _diagnostics_payload(for_date)
    return {
        "date": daily_state.date,
        "live_trading_enabled": _config.live_trading_enabled,
        "paper_mode": _config.paper_mode,
        "account_balance": account_balance,
        "account_peak_balance": account_peak,
        "trade_count": daily_state.trade_count,
        "max_trades_per_day": _config.max_trades_per_day + int(getattr(_config, "bonus_trades_after_max", 0) or 0),
        "consecutive_losses": daily_state.consecutive_losses,
        "max_consecutive_losses": _config.max_consecutive_losses,
        "has_open_position": daily_state.has_open_position,
        "open_position": journal.get_open_position(for_date),
        "no_trades": summary.get("no_trades", 0),
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "realized_pnl_dollars": round(realized_pnl, 2),
        "today_pnl_dollars": round(float(daily_state.realized_pnl_dollars or 0.0), 2),
        "journal_path": summary.get("journal_path", str(path)),
        "latest_entries": [_public_entry(entry) for entry in recent_entries],
        "top_no_trade_reasons": [
            {"reason": reason, "count": count}
            for reason, count in no_trade_reasons.most_common(5)
        ],
        "latest_webhook": _latest_webhook_payload(),
        "strategy_status": _strategy_payload(for_date),
        "performance": journal.get_performance_stats(_config.position_sizing.starting_balance),
        "broker_gateway_reachable": _ibkr_gateway_reachable(),
        "diagnostics": diagnostics,
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


def _strategy_payload(for_date: date) -> dict:
    journal = JournalLogger(log_dir=_config.log_dir)
    path = journal._journal_path(for_date)
    entries = journal._read_entries(path) if path.exists() else []

    decision_counts = Counter(
        entry.get("decision") or entry.get("type")
        for entry in entries
    )
    market_condition_counts = Counter(
        entry.get("market_condition")
        for entry in entries
        if entry.get("decision")
    )
    approved_strategy_counts = Counter(
        (entry.get("setup") or {}).get("strategy")
        for entry in entries
        if entry.get("decision") == "TRADE"
        and (entry.get("risk_check") or {}).get("result") == "APPROVED"
    )
    rejected_strategy_counts = Counter(
        (entry.get("setup") or {}).get("strategy")
        for entry in entries
        if entry.get("decision") == "TRADE"
        and (entry.get("risk_check") or {}).get("result") == "REJECTED"
    )
    no_trade_reasons = Counter(
        entry.get("reason", "Unknown")
        for entry in entries
        if entry.get("decision") == "NO_TRADE"
    )

    return {
        "date": for_date.isoformat(),
        "enabled_concepts": list(_config.enabled_concepts),
        "strat_confirmation_only": True,
        "decision_counts": _counter_items(decision_counts, "decision"),
        "market_condition_counts": _counter_items(market_condition_counts, "market_condition"),
        "approved_strategy_counts": _counter_items(approved_strategy_counts, "strategy"),
        "rejected_strategy_counts": _counter_items(rejected_strategy_counts, "strategy"),
        "top_no_trade_reasons": [
            {"reason": reason, "count": count}
            for reason, count in no_trade_reasons.most_common(5)
        ],
    }


def _counter_items(counter: Counter, key_name: str) -> list[dict]:
    return [
        {key_name: name, "count": count}
        for name, count in counter.most_common()
    ]


def _load_committee_panel(log_dir: str) -> dict:
    """Read the latest cached adaptive review artifact (never recomputes)."""
    try:
        from adaptive.committee import AdaptiveCommittee
        cached = AdaptiveCommittee(log_dir=log_dir).load_cached()
        return cached or {}
    except Exception:
        return {}


def _format_generated_age(value: str) -> str:
    if not value:
        return ""
    try:
        generated = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if generated.tzinfo is None:
            generated = generated.replace(tzinfo=timezone.utc)
        age_seconds = max(0, int((datetime.now(timezone.utc) - generated).total_seconds()))
    except (TypeError, ValueError):
        return "unknown age"
    if age_seconds < 60:
        return "just now"
    minutes = age_seconds // 60
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 48:
        return f"{hours}h ago"
    return f"{hours // 24}d ago"


def _render_dashboard(status: dict) -> str:
    committee = _load_committee_panel(_config.log_dir)
    committee_status = committee.get("overall_status", "")
    committee_sample = committee.get("sample_size", 0)
    committee_sufficiency = committee.get("sample_sufficiency", "")
    committee_date = committee.get("date", "")
    committee_generated = committee.get("generated_at", "")
    committee_generated_label = _format_generated_age(committee_generated)
    committee_recs = committee.get("top_recommendations") or []
    committee_color = {"OK": "green", "WARNING": "amber", "CRITICAL": "red"}.get(committee_status, "muted")
    committee_rec_rows = "\n".join(
        f"<li><span class='rec-code'>{_escape(r.get('code',''))}</span>"
        f"<span class='rec-subject'>{_escape(r.get('subject',''))}</span>"
        f"<span class='rec-reason'>{_escape((r.get('reason') or '')[:120])}</span></li>"
        for r in committee_recs[:5]
    ) or "<li><span>No recommendations — run /status/adaptive to generate</span></li>"
    committee_summary = (
        f"{committee_sample} trades ({committee_sufficiency})" if committee_status
        else "Not yet run — call GET /status/adaptive"
    )
    if committee_generated_label:
        committee_summary = f"{committee_summary} · generated {committee_generated_label}"

    today_pnl = status.get("today_pnl_dollars", 0.0)
    today_pnl_class = "green" if today_pnl >= 0 else "red"
    wr_resolved = status["wins"] + status["losses"]
    wr_str = "—" if wr_resolved == 0 else f"{status['win_rate']:.1f}"
    wr_suffix = "" if wr_resolved == 0 else "<small>%</small>"
    wr_class = "muted" if wr_resolved == 0 else ("green" if status["win_rate"] >= 50 else "amber")
    open_class = "green" if status["has_open_position"] else "muted"
    open_str = "1" if status["has_open_position"] else "—"

    perf = status.get("performance") or {}
    pf_val = perf.get("profit_factor")
    pf_str = f"{pf_val:.2f}×" if pf_val is not None else "—"
    avg_win_str   = _fmt_stat(perf.get("avg_win"))
    avg_loss_str  = _fmt_stat(perf.get("avg_loss"))
    maxdd_str     = _fmt_stat(perf.get("max_drawdown"))
    best_day_str  = _fmt_stat(perf.get("best_day"))
    worst_day_str = _fmt_stat(perf.get("worst_day"))
    lg_win_str    = _fmt_stat(perf.get("largest_win"))
    lg_loss_str   = _fmt_stat(perf.get("largest_loss"))

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
    strategy_status = status.get("strategy_status") or {}
    strategy_rows = "\n".join(
        f"<li><span>{_escape(item['strategy'])}</span><strong>{item['count']}</strong></li>"
        for item in strategy_status.get("approved_strategy_counts", [])
    ) or "<li><span>No approved strategy yet</span><strong>0</strong></li>"
    decision_rows = "\n".join(
        f"<li><span>{_escape(item['decision'])}</span><strong>{item['count']}</strong></li>"
        for item in strategy_status.get("decision_counts", [])
    ) or "<li><span>No decisions yet</span><strong>0</strong></li>"
    gw = status.get("broker_gateway_reachable")
    if gw is True:
        gateway_badge = '<div id="badge-gateway" class="badge" style="color:var(--green)">IBKR ● CONNECTED</div>'
    elif gw is False:
        gateway_badge = '<div id="badge-gateway" class="badge" style="color:var(--red)">IBKR ✕ OFFLINE</div>'
    else:
        gateway_badge = '<div id="badge-gateway" style="display:none"></div>'
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
  <meta name="theme-color" content="#00d5ff">
  <meta name="mobile-web-app-capable" content="yes">
  <meta name="apple-mobile-web-app-capable" content="yes">
  <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
  <meta name="apple-mobile-web-app-title" content="RiskSentinel">
  <link rel="manifest" href="/manifest.json">
  <link rel="apple-touch-icon" href="/static/icon-192.png">
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
      width: fit-content;
      align-self: flex-start;
    }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
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
    td.reason {{ color: var(--muted); max-width: 440px; word-break: break-word; overflow-wrap: anywhere; }}
    td {{ word-break: break-word; overflow-wrap: anywhere; }}
    .committee-status {{ font-size: 18px; font-weight: 700; margin-top: 8px; }}
    .committee-meta {{ font-size: 12px; color: var(--muted); margin-top: 4px; margin-bottom: 10px; }}
    .committee-recs {{ list-style: none; padding: 0; margin: 0; }}
    .committee-recs li {{
      display: grid;
      grid-template-columns: 120px 120px 1fr;
      gap: 8px;
      padding: 8px 0;
      border-top: 1px solid var(--line);
      font-size: 12px;
      align-items: start;
    }}
    .rec-code {{ color: var(--amber); font-weight: 600; word-break: break-word; }}
    .rec-subject {{ color: var(--cyan); word-break: break-word; }}
    .rec-reason {{ color: var(--muted); word-break: break-word; }}
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
      grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
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
    .stats-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(130px, 1fr));
      margin-top: 10px;
    }}
    .stat-item {{
      border-top: 1px solid var(--line);
      padding: 10px 6px;
    }}
    .stat-item label {{
      display: block;
      color: var(--muted);
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.03em;
      margin-bottom: 4px;
    }}
    .stat-item strong {{
      font-size: 18px;
      font-weight: 700;
    }}
    .chart-canvas {{
      width: 100%;
      display: block;
      margin-top: 10px;
      border-radius: 4px;
      background: #111216;
    }}
    @media (max-width: 760px) {{
      header, .wide {{ grid-template-columns: 1fr; display: grid; }}
      .grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .grid > :last-child:nth-child(odd) {{ grid-column: span 2; }}
      table {{ font-size: 12px; table-layout: auto; width: max-content; min-width: 100%; }}
      .table-wrap {{ overflow-x: auto; -webkit-overflow-scrolling: touch; }}
      .rules {{ grid-template-columns: 1fr; }}
      .context-grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .col-session {{ display: none; }}
      td, td.reason {{ white-space: nowrap; overflow: visible; text-overflow: clip; word-break: normal; overflow-wrap: normal; max-width: none; }}
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
      <div style="display:flex;gap:8px;align-items:flex-start;flex-wrap:wrap;">
        <div class="badge">LIVE TRADING OFF</div>
        {gateway_badge}
      </div>
    </header>

    <section class="grid">
      <div class="panel">
        <h2>Today P/L</h2>
        <div id="metric-pnl" class="metric {today_pnl_class}">${today_pnl:.2f}</div>
      </div>
      <div class="panel">
        <h2>Trades</h2>
        <div id="metric-trades" class="metric cyan">{status['trade_count']}<small>/{status['max_trades_per_day']}</small></div>
      </div>
      <div class="panel">
        <h2>Win Rate</h2>
        <div id="metric-winrate" class="metric {wr_class}">{wr_str}{wr_suffix}</div>
      </div>
      <div class="panel">
        <h2>Open</h2>
        <div id="metric-open" class="metric {open_class}">{open_str}</div>
      </div>
      <div class="panel">
        <h2>Balance</h2>
        <div id="metric-balance" class="metric cyan">${status['account_balance']:.2f}</div>
        <p id="metric-peak" class="sub">Peak ${status['account_peak_balance']:.2f}</p>
      </div>
    </section>

    <section class="panel" style="margin-bottom:14px;">
      <h2>Equity Curve <span id="chart-range" style="font-size:11px;font-weight:400;color:var(--muted);margin-left:8px;"></span></h2>
      <canvas id="pnl-chart" class="chart-canvas" style="height:140px;"></canvas>
    </section>

    <section class="panel" style="margin-bottom:14px;">
      <h2>Performance Stats <span style="font-size:11px;font-weight:400;color:var(--muted);margin-left:8px;">all-time</span></h2>
      <div class="stats-grid">
        <div class="stat-item"><label>Profit Factor</label><strong id="stat-pf">{pf_str}</strong></div>
        <div class="stat-item"><label>Avg Win</label><strong id="stat-avg-win" class="green">{avg_win_str}</strong></div>
        <div class="stat-item"><label>Avg Loss</label><strong id="stat-avg-loss" class="red">{avg_loss_str}</strong></div>
        <div class="stat-item"><label>Max Drawdown</label><strong id="stat-maxdd" class="amber">{maxdd_str}</strong></div>
        <div class="stat-item"><label>Best Day</label><strong id="stat-best-day" class="green">{best_day_str}</strong></div>
        <div class="stat-item"><label>Worst Day</label><strong id="stat-worst-day" class="red">{worst_day_str}</strong></div>
        <div class="stat-item"><label>Largest Win</label><strong id="stat-lg-win" class="green">{lg_win_str}</strong></div>
        <div class="stat-item"><label>Largest Loss</label><strong id="stat-lg-loss" class="red">{lg_loss_str}</strong></div>
      </div>
    </section>

    <section class="wide">
      <div class="panel">
        <h2>Rule State</h2>
        <div class="rules">
          <div class="rule {'danger' if trade_full else ''}"><span>●</span>Max {status['max_trades_per_day']} trades/day</div>
          <div class="rule {'danger' if lockout else ''}"><span>●</span>{status['max_consecutive_losses']}-loss lockout</div>
          <div class="rule {'danger' if status['has_open_position'] else ''}"><span>●</span>Open position: {_escape(open_position_text)}</div>
        </div>
      </div>
      <div class="panel">
        <h2>Top NO_TRADE Reasons</h2>
        <ul>{reason_rows}</ul>
      </div>
    </section>

    <section class="wide">
      <div class="panel">
        <h2>Enabled Strategy Concepts</h2>
        <ul>
          <li><span>Total enabled</span><strong>{len(strategy_status.get('enabled_concepts', []))}</strong></li>
          <li><span>First enabled</span><strong>{_escape((strategy_status.get('enabled_concepts') or ['None'])[0])}</strong></li>
          <li><span>Strat mode</span><strong>{'confirmation' if strategy_status.get('strat_confirmation_only') else 'active'}</strong></li>
        </ul>
      </div>
      <div class="panel">
        <h2>Strategy Pulse</h2>
        <ul>{strategy_rows}</ul>
        <ul>{decision_rows}</ul>
      </div>
    </section>

    <section class="panel" style="margin-bottom: 14px;">
      <h2>Committee Review</h2>
      <div class="committee-status {committee_color}">{committee_status or '—'}</div>
      <div class="committee-meta">{_escape(committee_summary)}{' · ' + committee_date if committee_date else ''}</div>
      <ul class="committee-recs">
        {committee_rec_rows}
      </ul>
    </section>

    <section class="panel" style="margin-bottom: 14px;">
      <h2>Latest Webhook Context</h2>
      <div class="context-grid">
        <div class="context-item"><label>Received</label><strong>{_escape(_format_webhook_received(latest_webhook.get('received_at')))}</strong></div>
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
        <div class="context-item"><label>Strat</label><strong>{_escape(_format_strat(webhook_context.get('strat')))}</strong></div>
        <div class="context-item"><label>Risk</label><strong>{_escape(webhook_result.get('risk') or 'None')}</strong></div>
      </div>
    </section>

    <section class="panel" style="margin-bottom: 14px;">
      <h2>Daily Made / Lost <span id="bar-chart-range" style="font-size:11px;font-weight:400;color:var(--muted);margin-left:8px;"></span></h2>
      <canvas id="pnl-bar-chart" class="chart-canvas" style="height:160px;"></canvas>
    </section>

    <section class="panel">
      <h2>Latest Journal Entries</h2>
      <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Time</th>
            <th>Symbol</th>
            <th class="col-session">Session</th>
            <th>Decision</th>
            <th>Reason</th>
            <th>Outcome</th>
          </tr>
        </thead>
        <tbody id="journal-tbody">{latest_rows or '<tr><td colspan="6">No journal entries yet.</td></tr>'}</tbody>
      </table>
      </div>
    </section>
  </main>
  <script>
    function escapeHtml(value) {{
      return String(value ?? '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#x27;');
    }}

    function shortTime(value) {{
      if (!value) return '';
      try {{
        const date = new Date(value);
        return date.toLocaleTimeString([], {{hour: 'numeric', minute: '2-digit'}});
      }} catch (error) {{
        return String(value).split('T').at(-1)?.slice(0, 5) || '';
      }}
    }}

    function renderEntryRow(entry) {{
      const outcome = entry.outcome
        ? `${{entry.outcome}}${{entry.pnl_dollars != null ? ' $' + Number(entry.pnl_dollars).toFixed(2) : ''}}`
        : '';
      return `<tr>
        <td>${{escapeHtml(shortTime(entry.ts))}}</td>
        <td>${{escapeHtml(entry.instrument || '')}}</td>
        <td class="col-session">${{escapeHtml(entry.session || '')}}</td>
        <td>${{escapeHtml(entry.decision || entry.type || '')}}</td>
        <td class="reason">${{escapeHtml(entry.reason || '')}}</td>
        <td>${{escapeHtml(outcome)}}</td>
      </tr>`;
    }}

    function drawPnlChart(history) {{
      const canvas = document.getElementById('pnl-chart');
      const rangeLabel = document.getElementById('chart-range');
      if (!canvas || !history || !history.days || !history.days.length) return;

      // History arrives newest-first — reverse so oldest is left
      const days = [...history.days].reverse();

      const dpr = window.devicePixelRatio || 1;
      const W = canvas.offsetWidth;
      const H = canvas.offsetHeight;
      if (!W || !H) return;
      canvas.width = W * dpr;
      canvas.height = H * dpr;
      const ctx = canvas.getContext('2d');
      ctx.scale(dpr, dpr);

      const PAD = {{ top: 16, right: 16, bottom: 26, left: 56 }};
      const chartW = W - PAD.left - PAD.right;
      const chartH = H - PAD.top - PAD.bottom;

      const pnlValues = days.map(d => d.realized_pnl_dollars || 0);
      const maxVal = Math.max(0, ...pnlValues);
      const minVal = Math.min(0, ...pnlValues);
      const range = (maxVal - minVal) || 1;

      const toX = i => PAD.left + (i / Math.max(days.length - 1, 1)) * chartW;
      const toY = v => PAD.top + ((maxVal - v) / range) * chartH;
      const zeroY = toY(0);

      // Background
      ctx.fillStyle = '#111216';
      ctx.fillRect(0, 0, W, H);

      // Subtle grid lines
      ctx.strokeStyle = '#1e2028';
      ctx.lineWidth = 1;
      [0.25, 0.5, 0.75].forEach(frac => {{
        const y = PAD.top + frac * chartH;
        ctx.beginPath(); ctx.moveTo(PAD.left, y); ctx.lineTo(W - PAD.right, y); ctx.stroke();
      }});

      // Zero line
      ctx.strokeStyle = '#2a2d34';
      ctx.lineWidth = 1;
      ctx.setLineDash([4, 4]);
      ctx.beginPath(); ctx.moveTo(PAD.left, zeroY); ctx.lineTo(W - PAD.right, zeroY); ctx.stroke();
      ctx.setLineDash([]);

      // Y-axis labels
      ctx.fillStyle = '#a1a1aa';
      ctx.font = `${{Math.max(10, Math.min(12, W / 40))}}px ui-sans-serif,system-ui,sans-serif`;
      ctx.textAlign = 'right';
      ctx.textBaseline = 'middle';
      const yTicks = [minVal, 0, maxVal].filter((v, i, a) => a.indexOf(v) === i);
      yTicks.forEach(v => {{
        ctx.fillText(`$${{v >= 0 ? '' : '-'}}${{Math.abs(v).toFixed(0)}}`, PAD.left - 6, toY(v));
      }});

      // X-axis date labels
      ctx.textAlign = 'center';
      ctx.textBaseline = 'bottom';
      const labelIdxs = days.length <= 3
        ? days.map((_, i) => i)
        : [0, Math.floor(days.length / 2), days.length - 1];
      labelIdxs.forEach(i => {{
        const label = (days[i].date || '').slice(5); // MM-DD
        ctx.fillText(label, toX(i), H - 2);
      }});

      // Gradient fill under the curve
      const grad = ctx.createLinearGradient(0, PAD.top, 0, PAD.top + chartH);
      grad.addColorStop(0, 'rgba(0,213,255,0.18)');
      grad.addColorStop(1, 'rgba(0,213,255,0.01)');
      ctx.beginPath();
      days.forEach((d, i) => {{
        const x = toX(i), y = toY(d.realized_pnl_dollars || 0);
        i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
      }});
      ctx.lineTo(toX(days.length - 1), zeroY);
      ctx.lineTo(toX(0), zeroY);
      ctx.closePath();
      ctx.fillStyle = grad;
      ctx.fill();

      // Main line
      ctx.beginPath();
      ctx.strokeStyle = '#00d5ff';
      ctx.lineWidth = 2;
      ctx.lineJoin = 'round';
      ctx.lineCap = 'round';
      days.forEach((d, i) => {{
        const x = toX(i), y = toY(d.realized_pnl_dollars || 0);
        i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
      }});
      ctx.stroke();

      // Dots
      days.forEach((d, i) => {{
        const v = d.realized_pnl_dollars || 0;
        ctx.beginPath();
        ctx.arc(toX(i), toY(v), 3, 0, Math.PI * 2);
        ctx.fillStyle = v > 0 ? '#17d97f' : v < 0 ? '#ff3d71' : '#a1a1aa';
        ctx.fill();
      }});

      // Range label
      if (rangeLabel) {{
        const latest = pnlValues[pnlValues.length - 1] || 0;
        const sign = latest >= 0 ? '+' : '';
        rangeLabel.textContent = `${{days.length}}d · ${{sign}}$${{latest.toFixed(2)}} cumulative`;
      }}
    }}

    function drawPnlBarChart(history) {{
      const canvas = document.getElementById('pnl-bar-chart');
      const rangeLabel = document.getElementById('bar-chart-range');
      if (!canvas || !history || !history.days || !history.days.length) return;

      const days = [...history.days].reverse();
      const dpr = window.devicePixelRatio || 1;
      const W = canvas.offsetWidth;
      const H = canvas.offsetHeight;
      if (!W || !H) return;
      canvas.width = W * dpr;
      canvas.height = H * dpr;
      const ctx = canvas.getContext('2d');
      ctx.scale(dpr, dpr);

      const PAD = {{ top: 16, right: 16, bottom: 30, left: 56 }};
      const chartW = W - PAD.left - PAD.right;
      const chartH = H - PAD.top - PAD.bottom;
      const values = days.map(d => Number(d.realized_pnl_dollars || 0));
      const maxAbs = Math.max(1, ...values.map(v => Math.abs(v)));
      const zeroY = PAD.top + chartH / 2;
      const barGap = Math.min(10, Math.max(4, chartW / Math.max(days.length, 1) * 0.18));
      const barW = Math.max(6, (chartW - barGap * Math.max(days.length - 1, 0)) / Math.max(days.length, 1));
      const toY = v => zeroY - (v / maxAbs) * (chartH / 2);

      ctx.fillStyle = '#111216';
      ctx.fillRect(0, 0, W, H);

      ctx.strokeStyle = '#1e2028';
      ctx.lineWidth = 1;
      [0.25, 0.75].forEach(frac => {{
        const y = PAD.top + frac * chartH;
        ctx.beginPath(); ctx.moveTo(PAD.left, y); ctx.lineTo(W - PAD.right, y); ctx.stroke();
      }});

      ctx.strokeStyle = '#2a2d34';
      ctx.setLineDash([4, 4]);
      ctx.beginPath(); ctx.moveTo(PAD.left, zeroY); ctx.lineTo(W - PAD.right, zeroY); ctx.stroke();
      ctx.setLineDash([]);

      ctx.fillStyle = '#a1a1aa';
      ctx.font = `${{Math.max(10, Math.min(12, W / 40))}}px ui-sans-serif,system-ui,sans-serif`;
      ctx.textAlign = 'right';
      ctx.textBaseline = 'middle';
      ctx.fillText(`+$${{maxAbs.toFixed(0)}}`, PAD.left - 6, PAD.top);
      ctx.fillText('$0', PAD.left - 6, zeroY);
      ctx.fillText(`-$${{maxAbs.toFixed(0)}}`, PAD.left - 6, PAD.top + chartH);

      days.forEach((d, i) => {{
        const value = Number(d.realized_pnl_dollars || 0);
        const x = PAD.left + i * (barW + barGap);
        const y = value >= 0 ? toY(value) : zeroY;
        const h = Math.max(2, Math.abs(toY(value) - zeroY));
        ctx.fillStyle = value > 0 ? '#17d97f' : value < 0 ? '#ff3d71' : '#2a2d34';
        ctx.fillRect(x, y, barW, h);
      }});

      ctx.fillStyle = '#a1a1aa';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'bottom';
      const labelIdxs = days.length <= 4
        ? days.map((_, i) => i)
        : [0, Math.floor(days.length / 2), days.length - 1];
      labelIdxs.forEach(i => {{
        const x = PAD.left + i * (barW + barGap) + barW / 2;
        ctx.fillText((days[i].date || '').slice(5), x, H - 2);
      }});

      if (rangeLabel) {{
        const wins = values.filter(v => v > 0).reduce((sum, v) => sum + v, 0);
        const losses = values.filter(v => v < 0).reduce((sum, v) => sum + v, 0);
        rangeLabel.textContent = `made $${{wins.toFixed(2)}} · lost $${{Math.abs(losses).toFixed(2)}}`;
      }}
    }}

    async function refreshDashboard() {{
      try {{
        const [today, history] = await Promise.all([
          fetch('/status/today', {{cache: 'no-store'}}).then(r => r.json()),
          fetch('/status/history?days=7', {{cache: 'no-store'}}).then(r => r.json())
        ]);
        const pnl = document.getElementById('metric-pnl');
        const trades = document.getElementById('metric-trades');
        const winrate = document.getElementById('metric-winrate');
        const open = document.getElementById('metric-open');
        const bal = document.getElementById('metric-balance');
        const peak = document.getElementById('metric-peak');
        const tbody = document.getElementById('journal-tbody');
        if (pnl) pnl.textContent = `$${{Number(today.today_pnl_dollars || 0).toFixed(2)}}`;
        if (trades) trades.innerHTML = `${{today.trade_count || 0}}<small>/${{today.max_trades_per_day || 0}}</small>`;
        if (winrate) {{
          const res = (today.wins || 0) + (today.losses || 0);
          winrate.innerHTML = res === 0 ? '—' : `${{Number(today.win_rate || 0).toFixed(1)}}<small>%</small>`;
        }}
        if (open) open.textContent = today.has_open_position ? '1' : '—';
        if (bal) bal.textContent = `$${{Number(today.account_balance || 0).toFixed(2)}}`;
        if (peak) peak.textContent = `Peak $${{Number(today.account_peak_balance || 0).toFixed(2)}}`;
        if (tbody && today.latest_entries) {{
          tbody.innerHTML = today.latest_entries.length
            ? today.latest_entries.map(renderEntryRow).join('')
            : '<tr><td colspan="6">No journal entries yet.</td></tr>';
        }}
        // Performance stats
        const perf = today.performance || {{}};
        function updStat(id, val, prefix) {{
          const el = document.getElementById(id);
          if (!el) return;
          el.textContent = val != null ? ((prefix || '$') + Number(val).toFixed(2)) : '—';
        }}
        const pfEl = document.getElementById('stat-pf');
        if (pfEl) pfEl.textContent = perf.profit_factor != null ? Number(perf.profit_factor).toFixed(2) + '×' : '—';
        updStat('stat-avg-win',   perf.avg_win);
        updStat('stat-avg-loss',  perf.avg_loss);
        updStat('stat-maxdd',     perf.max_drawdown);
        updStat('stat-best-day',  perf.best_day);
        updStat('stat-worst-day', perf.worst_day);
        updStat('stat-lg-win',    perf.largest_win);
        updStat('stat-lg-loss',   perf.largest_loss);

        // IBKR gateway badge
        const gwBadge = document.getElementById('badge-gateway');
        if (gwBadge && today.broker_gateway_reachable !== null && today.broker_gateway_reachable !== undefined) {{
          gwBadge.textContent = today.broker_gateway_reachable ? 'IBKR ● CONNECTED' : 'IBKR ✕ OFFLINE';
          gwBadge.style.color = today.broker_gateway_reachable ? 'var(--green)' : 'var(--red)';
          gwBadge.style.display = '';
        }}

        window.__riskSentinelHistory = history;
        drawPnlChart(history);
        drawPnlBarChart(history);
      }} catch (error) {{
        console.warn('Dashboard refresh failed', error);
      }}
    }}
    refreshDashboard();
    setInterval(refreshDashboard, 30000);
    // Redraw chart on resize (handles rotation on mobile)
    window.addEventListener('resize', () => {{
      if (window.__riskSentinelHistory) {{
        drawPnlChart(window.__riskSentinelHistory);
        drawPnlBarChart(window.__riskSentinelHistory);
      }}
    }});
  </script>

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
        f"<td class=\"col-session\">{_escape(entry.get('session') or '')}</td>"
        f"<td>{_escape(entry.get('decision') or entry.get('type') or '')}</td>"
        f"<td class=\"reason\">{_escape(entry.get('reason') or '')}</td>"
        f"<td>{_escape(outcome)}</td>"
        "</tr>"
    )


def _short_time(value: str | None) -> str:
    if not value:
        return ""
    try:
        from zoneinfo import ZoneInfo
        dt = datetime.fromisoformat(value)
        dt_et = dt.astimezone(ZoneInfo("America/New_York"))
        return dt_et.strftime("%-I:%M %p")
    except Exception:
        return value.split("T")[-1][:5]


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


def _format_strat(value: dict | None) -> str:
    if not value:
        return "None"
    current = value.get("current_bar_type") or "None"
    sequence = value.get("strat_sequence") or "no_sequence"
    direction = value.get("strat_direction") or "None"
    return f"{current} · {sequence} · {direction}"


def _record_latest_webhook(payload: AlertPayload, result: dict) -> None:
    path = _latest_webhook_path()
    data = {
        "received_at": datetime.now(timezone.utc).isoformat(),
        "payload": _payload_to_dict(payload),
        "context": result.get("context"),
        "result": {
            "decision": result.get("decision"),
            "resolution": result.get("resolution"),
            "risk": result.get("risk"),
            "fill": result.get("fill"),
            "failed_gates": result.get("failed_gates") or [],
        },
    }
    from agent.daily_summary import atomic_write_text
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(data, indent=2, sort_keys=True))


def _latest_webhook_payload() -> dict:
    path = _latest_webhook_path()
    if not path.exists():
        return {"received_at": None, "payload": None, "context": None, "result": None}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"received_at": None, "payload": None, "context": None, "result": None}


def _latest_webhook_path() -> Path:
    return Path(_config.log_dir) / "latest_webhook.json"


def _payload_to_dict(payload: AlertPayload) -> dict:
    if hasattr(payload, "model_dump"):
        return payload.model_dump()
    return payload.dict()


def _format_webhook_received(value: str | None) -> str:
    """Format a UTC ISO timestamp to a readable ET string, e.g. 'May 31 · 1:07 AM ET'."""
    if not value:
        return "None"
    try:
        from zoneinfo import ZoneInfo
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        dt_et = dt.astimezone(ZoneInfo("America/New_York"))
        return dt_et.strftime("%b %-d · %-I:%M %p ET")
    except Exception:
        return value


def _fmt_stat(value: object, prefix: str = "$", none_str: str = "—") -> str:
    """Format a numeric stat for display, returning none_str when value is None."""
    if value is None:
        return none_str
    return f"{prefix}{float(value):.2f}"


def _escape(value: object) -> str:
    text = "" if value is None else str(value)
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#x27;")
    )
