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

import asyncio
import hashlib
import hmac
import inspect
import json
import logging
import os
import time
from urllib.parse import parse_qs
from collections import Counter
from contextlib import asynccontextmanager, suppress
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from ops.release_integrity import enforce_release_integrity

# Refuse to serve on drifted source. No-op unless RELEASE_INTEGRITY_ENFORCED
# is set (production); raises SystemExit before the app object exists, so
# uvicorn exits and systemd marks the unit failed instead of trading on
# unverified code.
enforce_release_integrity()

from agent.daily_summary import DailySummaryAgent, validate_review_date
from config.settings import load_config
from webhook import log_redaction as _log_redaction  # noqa: F401 — installs uvicorn.access secret redaction on import
from context.futures_session import futures_session_active, feed_stale_after_minutes
from execution.tradovate_supervisor import (
    reliability_snapshot,
    run_tradovate_supervisor,
)
from journal.journal_logger import JournalLogger
from notifications.discord_notifier import notify_discord, send_discord_alert
from ops.live_box_guard import live_box_drift_report
from ops.fill_realism import build_fill_realism_status
from ops.automation_evidence import automation_evidence_status
from ops.evidence_readiness import build_evidence_readiness
from ops.proof_30_mnq import DEFAULT_LIMIT as PROOF_30_MNQ_LIMIT
from ops.proof_30_mnq import build_report as build_mnq_proof_report
from ops.proof_30_mnq import parse_proof_ts
from ops.runner_shadow_evidence import runner_shadow_status
from webhook.payload import AlertPayload
from webhook.reconciler import run_reconciler_loop
from notifications.heartbeat import run_heartbeat_loop
from webhook.runner import process_alert
from webhook.state_builder import futures_root

# Roots accepted by the webhook ingest filter — the traded micros plus the ES/NQ
# e-minis (recognized so they're acknowledged, then RISK_REJECTED downstream as
# not in the allowed universe). Matching is exact-root + contract-suffix only.
_INGEST_FUTURES_ROOTS = ("MNQ", "MES", "ES", "NQ", "MGC", "MCL")

logger = logging.getLogger(__name__)
_RATE_BUCKETS: dict[tuple[str, str], list[float]] = {}
_PUBLIC_RATE_LIMIT = int(os.getenv("PUBLIC_RATE_LIMIT_PER_MINUTE", "120") or 120)
_WEBHOOK_RATE_LIMIT = int(os.getenv("WEBHOOK_RATE_LIMIT_PER_MINUTE", "60") or 60)
_PRIVATE_RATE_LIMIT = int(os.getenv("PRIVATE_RATE_LIMIT_PER_MINUTE", "240") or 240)

_TRUTHY = {"1", "true", "yes", "on"}


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in _TRUTHY


def _cors_allow_origins() -> list[str]:
    """Browser CORS allowlist. Defaults to '*' (unchanged) when ALLOWED_ORIGINS is
    unset, so existing deployments are not affected. Set ALLOWED_ORIGINS to a
    comma-separated list to lock the API to known front-ends."""
    raw = os.getenv("ALLOWED_ORIGINS", "").strip()
    if not raw:
        return ["*"]
    return [o.strip() for o in raw.split(",") if o.strip()]


# Public demo deployment: when on, serve ONLY a small sanitized read-only surface
# and 404 everything else (default-deny). The real/operator box leaves this OFF.
_DEMO_ALLOWED_PATHS = {
    "/health",
    "/status/public",
    "/status/fill-realism",
    "/share",
    "/favicon.ico",
    "/manifest.json",
}


def _public_demo_mode() -> bool:
    return _env_flag("PUBLIC_DEMO_MODE", default=False)


def _demo_path_allowed(path: str) -> bool:
    return path in _DEMO_ALLOWED_PATHS or path.startswith("/static/")


# ── Simple site access code ───────────────────────────────────────────────────
# A single shared passcode that gates the whole site: no valid code → you can't
# open it. Disabled by default (blank SITE_ACCESS_CODE) so it cannot break an
# existing deployment until the operator sets a code. This is a "keep randoms
# out" speed bump; it is only strong once the site is on HTTPS.
_GATE_COOKIE = "vp_access"
_GATE_EXEMPT_PATHS = {
    "/gate", "/health", "/webhook/alert", "/webhook/manual",
    "/favicon.ico", "/manifest.json",
}


def _site_access_code() -> str:
    return os.getenv("SITE_ACCESS_CODE", "").strip()


def _site_gate_enabled() -> bool:
    return bool(_site_access_code())


def _gate_token() -> str:
    """Opaque cookie value bound to BOTH the server secret and the current code,
    so rotating SITE_ACCESS_CODE invalidates every existing session. The raw code
    is never stored in the cookie."""
    key = _configured_webhook_secret().encode()
    msg = ("site-access:" + _site_access_code()).encode()
    return hmac.new(key, msg, hashlib.sha256).hexdigest()


def _gate_path_exempt(path: str) -> bool:
    return path in _GATE_EXEMPT_PATHS or path.startswith("/static/")


def _has_valid_gate_cookie(request: Request) -> bool:
    token = request.cookies.get(_GATE_COOKIE)
    return bool(token) and hmac.compare_digest(token, _gate_token())


def _gate_html(next_path: str, error: bool) -> str:
    safe_next = next_path if next_path.startswith("/") else "/"
    err = (
        '<p style="color:#FF4D5A;margin:0 0 14px">Wrong code — try again.</p>'
        if error else ""
    )
    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Vantage Point · Enter code</title></head>
<body style="margin:0;background:#070706;color:#F2EEE6;font-family:-apple-system,Segoe UI,Roboto,sans-serif;display:flex;min-height:100vh;align-items:center;justify-content:center">
<form method="post" action="/gate" style="background:#141312;border:1px solid #35312A;border-radius:10px;padding:28px;width:280px">
<div style="font-size:15px;color:#F0B85A;font-weight:600;margin-bottom:4px">Vantage Point</div>
<div style="font-size:12px;color:#AAA294;margin-bottom:18px">Enter access code to continue</div>
{err}
<input type="hidden" name="next" value="{safe_next}">
<input name="code" type="password" autofocus autocomplete="off"
 style="width:100%;box-sizing:border-box;padding:11px;background:#242018;border:1px solid #35312A;border-radius:7px;color:#F2EEE6;font-size:15px;margin-bottom:14px">
<button type="submit"
 style="width:100%;padding:11px;background:#F0B85A;color:#070706;border:0;border-radius:7px;font-size:15px;font-weight:600;cursor:pointer">Enter</button>
</form></body></html>"""


def _allow_secret_in_query() -> bool:
    """The webhook secret may still travel as a URL query param (?secret=) while
    this is on (default). Query params leak into access logs / browser history, so
    prefer the request body or X-Webhook-Secret header and flip this off once the
    TradingView alert is updated."""
    return _env_flag("ALLOW_SECRET_IN_QUERY", default=True)


def _configured_webhook_secret() -> str:
    return os.getenv("WEBHOOK_SECRET", "").strip()


def _accepted_webhook_secrets() -> list[str]:
    """Return the primary and temporary rotation secrets, without duplicates."""
    candidates = (
        _configured_webhook_secret(),
        os.getenv("TRADINGVIEW_WEBHOOK_SECRET", "").strip(),
        os.getenv("TRADINGVIEW_WEBHOOK_SECRET_NEXT", "").strip(),
    )
    return list(dict.fromkeys(secret for secret in candidates if secret))


async def _resolve_inbound_secret(
    request: Request,
    header_secret: str | None,
    query_secret: str | None,
) -> str | None:
    """Find the webhook secret from (in order of preference): the X-Webhook-Secret
    header, the JSON body `secret` field, then the URL query param (deprecated).

    Reading the body is wrapped so a malformed/absent body can never break the
    critical ingestion path — it simply falls back to header/query, exactly as
    before. The body secret is a local value only; it is never added to the
    payload model, so it cannot leak into the journal or market state."""
    if header_secret:
        return header_secret
    try:
        data = await request.json()
        if isinstance(data, dict):
            body_secret = data.get("secret")
            if isinstance(body_secret, str) and body_secret:
                return body_secret
    except Exception:
        pass
    if query_secret and _allow_secret_in_query():
        logger.warning(
            "Webhook secret received via URL query param (deprecated — prefer the "
            "request body or X-Webhook-Secret header). path=%s",
            request.url.path,
        )
        return query_secret
    return None


def _verify_webhook_secret(provided: str | None) -> None:
    accepted = _accepted_webhook_secrets()
    # No secret configured → reject every inbound webhook unconditionally.
    # A blank secret means the endpoint is public; that is never acceptable.
    if not accepted:
        raise HTTPException(status_code=401, detail="WEBHOOK_SECRET is not configured.")
    if not provided:
        raise HTTPException(status_code=401, detail="Invalid webhook secret")
    matched = False
    for expected in accepted:
        matched = hmac.compare_digest(provided, expected) or matched
    if not matched:
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
            "Set WEBHOOK_SECRET in the server .env before deploying."
        )
    # Loud startup visibility for the loaded universe + decision timeframe. This
    # makes a stale in-memory config (e.g. MNQ silently dropped) obvious in the
    # service logs instead of only surfacing as per-bar NO_TRADE rejections.
    allowed = list(_config.allowed_instruments or [])
    required = list(getattr(_config, "required_instruments", []) or [])
    missing = [s for s in required if s not in allowed]
    logger.info(
        "STARTUP universe: allowed=%s required=%s decision_tf=%sm",
        allowed, required, getattr(_config, "expected_timeframe_minutes", 15),
    )
    if missing:
        logger.error(
            "CONFIG ERROR: required instrument(s) missing from allowed universe: %s "
            "(allowed=%s). Live alerts for these will be rejected as 'not in allowed universe'.",
            missing, allowed,
        )
    # Background safety tasks run independently of the TradingView bar feed.
    background_tasks = [
        asyncio.create_task(run_tradovate_supervisor()),
        asyncio.create_task(run_reconciler_loop(_config, _config.log_dir)),
        asyncio.create_task(run_heartbeat_loop(_config, _config.log_dir)),
    ]
    try:
        yield
    finally:
        for task in background_tasks:
            task.cancel()
        for task in background_tasks:
            with suppress(asyncio.CancelledError):
                await task


app = FastAPI(
    title="Paper Trading Webhook",
    description="TradingView → paper engine → JSONL journal. No live trading.",
    version="1.0.0",
    lifespan=_lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_allow_origins(),
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def _security_headers_middleware(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    # HSTS is inert over plain HTTP and takes effect once the site is on HTTPS.
    response.headers.setdefault(
        "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
    )
    return response


@app.middleware("http")
async def _site_access_gate_middleware(request: Request, call_next):
    # When SITE_ACCESS_CODE is set, every non-exempt path requires a valid gate
    # cookie. Browsers are redirected to the /gate code page; API/non-HTML callers
    # get a 401. Disabled (pass-through) when no code is configured.
    if (
        _site_gate_enabled()
        and not _gate_path_exempt(request.url.path)
        and not _has_valid_gate_cookie(request)
    ):
        accepts_html = "text/html" in request.headers.get("accept", "")
        if accepts_html and request.method == "GET":
            from fastapi.responses import RedirectResponse
            return RedirectResponse(url=f"/gate?next={request.url.path}", status_code=302)
        return JSONResponse(status_code=401, content={"detail": "Access code required"})
    return await call_next(request)


@app.middleware("http")
async def _public_demo_gate_middleware(request: Request, call_next):
    # Default-deny: when PUBLIC_DEMO_MODE is on, only the sanitized read-only
    # surface is reachable; every other path 404s as if it does not exist.
    if _public_demo_mode() and not _demo_path_allowed(request.url.path):
        return JSONResponse(status_code=404, content={"detail": "Not Found"})
    return await call_next(request)


@app.middleware("http")
async def _rate_limit_middleware(request: Request, call_next):
    try:
        _enforce_rate_limit(request)
    except HTTPException as exc:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    return await call_next(request)


_static = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=_static), name="static")


@app.get("/gate", response_class=HTMLResponse, include_in_schema=False)
async def gate_form(next: str = "/"):
    if not _site_gate_enabled():
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/", status_code=302)
    return HTMLResponse(_gate_html(next_path=next, error=False))


@app.post("/gate", include_in_schema=False)
async def gate_submit(request: Request):
    raw = (await request.body()).decode("utf-8", "ignore")
    form = parse_qs(raw)
    code = (form.get("code") or [""])[0]
    next_path = (form.get("next") or ["/"])[0] or "/"
    if not next_path.startswith("/"):
        next_path = "/"
    from fastapi.responses import RedirectResponse
    if _site_gate_enabled() and hmac.compare_digest(code, _site_access_code()):
        resp = RedirectResponse(url=next_path, status_code=302)
        secure = (
            request.url.scheme == "https"
            or request.headers.get("x-forwarded-proto", "") == "https"
        )
        resp.set_cookie(
            _GATE_COOKIE, _gate_token(),
            httponly=True, samesite="lax", secure=secure,
            max_age=60 * 60 * 24 * 30,
        )
        return resp
    return HTMLResponse(_gate_html(next_path=next_path, error=True), status_code=401)


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

# Serializes alert processing so two bars can't race on position state, while
# keeping the work OFF the event loop (see _process_alert_async). Held across an
# asyncio.to_thread call, so it's an asyncio.Lock, not a thread lock.
_alert_lock = asyncio.Lock()
# Strong refs to in-flight background tasks so the loop doesn't GC them mid-run.
_alert_tasks: set[asyncio.Task] = set()


def _handle_alert_blocking(payload: AlertPayload) -> None:
    """Full alert pipeline — decision engine, broker, reference quote, Discord.

    Runs in a worker thread (via _process_alert_async) so its seconds of
    synchronous I/O never block the event loop. Blocking here is exactly what
    made TradingView's webhook POST time out (nginx 499) and drop the bar.
    """
    try:
        result = process_alert(payload, config=_config, log_dir=_config.log_dir)
        _record_latest_webhook(payload, result)
        # Attach an independent live index quote for the Discord display price.
        # Fail-soft: a quote-source hiccup must never affect ingestion or risk.
        if _config.live_quote_enabled:
            try:
                from quotes.live_index import get_live_quote
                instrument = (result.get("context") or {}).get("instrument") or payload.ticker
                live_quote = get_live_quote(instrument)
                if live_quote:
                    result["live_quote"] = live_quote
            except Exception as exc:
                logger.warning("live_quote attach failed: %s", exc)
        notify_discord(payload=payload, result=result, config=_config)
        logger.info("Alert processed: %s -> %s", payload.ticker, result.get("decision"))
    except Exception as exc:
        logger.exception("Error processing alert for %s: %s", payload.ticker, exc)


async def _process_alert_async(payload: AlertPayload) -> None:
    # One bar at a time (lock), each on a worker thread (to_thread) so the event
    # loop stays free to accept the next webhook immediately.
    async with _alert_lock:
        await asyncio.to_thread(_handle_alert_blocking, payload)


@app.post("/webhook/alert")
async def receive_alert(
    payload: AlertPayload,
    request: Request,
    x_webhook_secret: str | None = Header(default=None),
    secret: str | None = Query(default=None),
) -> JSONResponse:
    """
    Accept a bar-close alert from TradingView and acknowledge it immediately.

    The decision pipeline (which can take many seconds: broker calls, a Yahoo
    reference-price fetch, Discord) runs in the background — TradingView's webhook
    client times out after a few seconds, so doing that work before responding
    made every delivery fail (nginx 499) and silently dropped the bar. We
    validate + authenticate synchronously, then hand off and return 200 fast.
    """
    _verify_webhook_secret(await _resolve_inbound_secret(request, x_webhook_secret, secret))
    # Silently ignore non-futures tickers (e.g. stock alerts sharing the same
    # webhook). Exact root + contract-suffix matching — NOT a startswith prefix
    # — so a stock like ESTC or NQXX is never mistaken for the ES/NQ future.
    if futures_root(payload.ticker, _INGEST_FUTURES_ROOTS) is None:
        return JSONResponse(content={"ok": True, "decision": "IGNORED", "reason": "non-futures ticker"})
    task = asyncio.create_task(_process_alert_async(payload))
    _alert_tasks.add(task)
    task.add_done_callback(_alert_tasks.discard)
    return JSONResponse(content={"ok": True, "queued": True, "ticker": payload.ticker})


@app.get("/futures", include_in_schema=False)
async def futures_redirect():
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/", status_code=301)


@app.post("/webhook/manual")
async def manual_action(
    request: Request,
    x_webhook_secret: str | None = Header(default=None),
    secret: str | None = Query(default=None),
) -> JSONResponse:
    """
    Emergency manual control endpoint. Requires webhook secret.

    Actions:
        CLOSE_ALL   — cancel all open orders then send a closing MKT order to
                      flatten any open position. Safe even mid-trade — cancels
                      the bracket children first, then exits the position.
        STATUS      — return current broker connection status and open position.

    Manual OPEN (force-entry) has been removed: it bypassed all risk gates.
    Entries only happen via the risk-gated alert pipeline.

    Example:
        # Emergency exit
        curl -X POST https://yourserver/webhook/manual \\
             -H "X-Webhook-Secret: your-secret" \\
             -H "Content-Type: application/json" \\
             -d '{"action": "CLOSE_ALL"}'
    """
    _verify_webhook_secret(await _resolve_inbound_secret(request, x_webhook_secret, secret))
    if not getattr(_config, "enable_manual_execution_controls", False):
        raise HTTPException(status_code=403, detail="Manual execution controls are disabled.")

    body = await request.json()
    action = str(body.get("action", "")).upper().strip()

    if action == "STATUS":
        status = _broker_status()
        connected = status.get("connected", False)
        broker = status.get("broker", "unknown")
        balance = status.get("account_balance")
        pos = status.get("position")
        note_parts = [f"Broker: {broker}", f"Connected: {connected}"]
        if balance is not None:
            note_parts.append(f"Balance: ${balance:,.0f}")
        if pos:
            note_parts.append(f"Position: {pos.get('direction')} {pos.get('qty')}x {pos.get('instrument')}")
        return JSONResponse(content={
            "ok": connected,
            "action": "STATUS",
            "note": " · ".join(note_parts),
            **status,
        })

    if action == "CLOSE_ALL":
        return JSONResponse(content=_manual_close_all())

    if action == "OPEN":
        # Manual force-open was REMOVED — it bypassed every risk gate plus the
        # allowed-instrument and contract-cap checks. The only entry path is the
        # gated decision pipeline (TradingView alert → risk engine → broker).
        raise HTTPException(
            status_code=410,
            detail="Manual OPEN has been removed. Entries only via the risk-gated alert pipeline.",
        )

    raise HTTPException(status_code=400, detail=f"Unknown action '{action}'. Use CLOSE_ALL or STATUS.")



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
        "broker_gateway_reachable": None,  # IBKR-only concept; broker removed
        "webhook_secret_required": bool(_configured_webhook_secret()),
    }


@app.get("/status/today")
async def status_today() -> dict:
    """Return today's reconstructed daily state from the journal."""
    return _dashboard_payload(date.today())


@app.get("/status/five-min")
async def status_five_min() -> dict:
    """Observe-only 5M entry-feed summary: whether the feed is enabled and today's
    per-instrument 5M bar count + last timestamp (ingest-only; never trades)."""
    from context.five_min_feed import five_min_status
    return five_min_status(
        _config.log_dir,
        instruments=list(getattr(_config, "allowed_instruments", []) or []) or None,
    )


@app.get("/status/fill-realism")
async def status_fill_realism(
    days: int = Query(default=7, ge=1, le=90),
    recent_limit: int = Query(default=20, ge=0, le=100),
) -> dict:
    """Return journal-derived resolved fill attempts and actual no-fill rates."""
    return build_fill_realism_status(
        _config.log_dir,
        days=days,
        recent_limit=recent_limit,
    )


@app.get("/status/evidence-readiness")
async def status_evidence_readiness(
    days: int = Query(default=30, ge=1, le=180),
) -> dict:
    """Return unified read-only research evidence readiness."""
    return build_evidence_readiness(_config.log_dir, days=days, config=_config)


@app.get("/status/history")
async def status_history(days: int = Query(default=7, ge=1, le=90)) -> dict:
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
                # Per-day history must report each day's OWN realized P&L, not the
                # cumulative account figure. `realized_pnl_dollars` in the single-day
                # payload is `account_balance - starting_balance` (a running total),
                # so a win persists in it on every later day. Frontends SUM this
                # array for the "7D P&L" total and chart it as daily bars — summing a
                # running total double-counts every prior win (one +$55 win showed as
                # +$110 across two days). Emit the daily increment (today_pnl_dollars)
                # here; expose the running total separately for anyone who wants it.
                "realized_pnl_dollars": payload["today_pnl_dollars"],
                "cumulative_realized_pnl_dollars": payload["realized_pnl_dollars"],
                "win_rate": payload["win_rate"],
            }
        )
    return {"days": history}


# Short TTL cache so many clients (every Futures tab polls 2 instruments every
# 15s) share one upstream broker/Yahoo call instead of each hitting it — faster
# and keeps Tradovate well under its 5-req/hr auth limit. instrument → (ts, quote).
_QUOTE_CACHE: dict[str, tuple[float, dict]] = {}
_QUOTE_TTL_SECONDS = 30.0  # price is a bar-close + Yahoo proxy; >poll interval so most polls hit cache

# Module-level Tradovate broker singleton — reused across requests so the auth
# token persists (the token is instance-level; a fresh broker per request would
# re-auth and blow the ~5-req/hr auth cap). Lazily created.
_TV_BROKER = None


def _tv_broker():
    global _TV_BROKER
    if _TV_BROKER is None:
        from execution.tradovate_broker import TradovateBroker
        _TV_BROKER = TradovateBroker()
    return _TV_BROKER


# Live Tradovate account snapshot, cached so the dashboard mirror (polled ~30s)
# shares one upstream call and stays under the auth/data limits.
_ACCOUNT_CACHE: dict = {"ts": 0.0, "data": None}
_ACCOUNT_TTL_SECONDS = 30.0


@app.get("/status/broker-account")
async def status_broker_account() -> dict:
    """Live Tradovate demo/live account truth (equity / open P&L / realized P&L /
    open position) for the dashboard mirror — read straight from the broker so the
    UI never diverges from Tradovate. Cached ~30s; only meaningful when
    BROKER=tradovate."""
    broker_mode = os.getenv("BROKER", "paper").strip().lower()
    if broker_mode != "tradovate":
        return {
            "ok": False,
            "error": "broker_not_tradovate",
            "message": f"Broker account panel is unavailable because BROKER={broker_mode}.",
            "next_step": "Set BROKER=tradovate only when you want the dashboard to read a Tradovate account.",
            "reliability": reliability_snapshot(),
        }
    now = time.time()
    cached = _ACCOUNT_CACHE.get("data")
    if cached is not None and (now - _ACCOUNT_CACHE["ts"]) < _ACCOUNT_TTL_SECONDS:
        return {**cached, "reliability": reliability_snapshot()}
    # Broker auth/network I/O is synchronous and can hang on timeouts; run it off
    # the event loop so a slow/unauthenticated Tradovate session can't stall other
    # routes (health/today/quote) and make the dashboard look like an API flap.
    summary = await asyncio.to_thread(_account_summary_blocking)
    summary["reliability"] = reliability_snapshot()
    _decorate_broker_account_status(summary)
    summary["cached_at"] = now
    _ACCOUNT_CACHE["ts"] = now
    _ACCOUNT_CACHE["data"] = summary
    return summary


@app.get("/status/tradovate-reliability")
async def status_tradovate_reliability() -> dict:
    """Protected operator view of the process-global broker readiness state."""
    return reliability_snapshot()


def _live_preflight_notify(message: str) -> None:
    send_discord_alert(_config, message)


@app.get("/status/live-preflight")
async def status_live_preflight() -> dict:
    """Daily live-order preflight/arming state."""
    from execution.live_preflight import live_order_status

    state = live_order_status()
    state["reliability"] = reliability_snapshot()
    return state


@app.post("/admin/live-preflight/run")
async def admin_live_preflight_run(
    request: Request,
    x_webhook_secret: str | None = Header(default=None),
    secret: str | None = Query(default=None),
) -> JSONResponse:
    """Run the broker/account/flat/no-working-orders preflight check."""
    _verify_webhook_secret(await _resolve_inbound_secret(request, x_webhook_secret, secret))
    if os.getenv("BROKER", "paper").strip().lower() != "tradovate":
        raise HTTPException(status_code=400, detail="BROKER must be tradovate to run live preflight.")
    from execution.live_preflight import run_preflight

    result = await asyncio.to_thread(
        run_preflight,
        _tv_broker(),
        notify=_live_preflight_notify,
    )
    return JSONResponse(content=result)


@app.post("/admin/live-preflight/arm")
async def admin_live_preflight_arm(
    request: Request,
    x_webhook_secret: str | None = Header(default=None),
    secret: str | None = Query(default=None),
) -> JSONResponse:
    """Arm live trading for today after a passing preflight."""
    _verify_webhook_secret(await _resolve_inbound_secret(request, x_webhook_secret, secret))
    from execution.live_preflight import arm_today

    result = arm_today(notify=_live_preflight_notify)
    if not result.get("ready"):
        raise HTTPException(status_code=409, detail=result)
    return JSONResponse(content=result)


@app.post("/admin/live-preflight/disarm")
async def admin_live_preflight_disarm(
    request: Request,
    x_webhook_secret: str | None = Header(default=None),
    secret: str | None = Query(default=None),
) -> JSONResponse:
    """Manually disarm live trading."""
    _verify_webhook_secret(await _resolve_inbound_secret(request, x_webhook_secret, secret))
    body = await request.json()
    reason = str(body.get("reason") or "manual").strip() or "manual"
    from execution.live_preflight import disarm

    return JSONResponse(content=disarm(reason=reason, notify=_live_preflight_notify))


@app.post("/admin/test-discord")
async def admin_test_discord(
    request: Request,
    x_webhook_secret: str | None = Header(default=None),
    secret: str | None = Query(default=None),
) -> JSONResponse:
    """Send an operator-visible Discord smoke alert."""
    _verify_webhook_secret(await _resolve_inbound_secret(request, x_webhook_secret, secret))
    result = send_discord_alert(
        _config,
        "DISCORD TEST: RiskSentinel operator alerts are reaching this channel.",
    )
    return JSONResponse(content={"ok": result.sent, "reason": result.reason})


def _account_summary_blocking() -> dict:
    """Blocking Tradovate account fetch (auth + network). Runs in a worker thread
    via status_broker_account so it never blocks the async event loop."""
    try:
        return _tv_broker().get_account_summary()
    except Exception as exc:
        logger.exception("status_broker_account failed: %s", exc)
        return {"ok": False, "error": str(exc)}


def _decorate_broker_account_status(summary: dict) -> None:
    """Attach UI-safe account status text while preserving raw error codes."""
    if summary.get("ok"):
        summary.setdefault("message", "Broker account session is active.")
        return
    raw_error = str(summary.get("error") or "").strip()
    if raw_error == "not_authenticated":
        summary.setdefault("status_label", "SESSION NOT ACTIVE")
        summary.setdefault("message", "Tradovate account is unavailable because the demo broker session is not authenticated.")
        summary.setdefault(
            "next_step",
            "Refresh Tradovate auth and confirm the API ACL is enabled; this is separate from TradingView alert freshness.",
        )
    elif raw_error:
        summary.setdefault("status_label", "BROKER ACCOUNT UNAVAILABLE")
        summary.setdefault("message", f"Tradovate account status check failed: {raw_error}")
        summary.setdefault("next_step", "Check broker credentials, network reachability, and the service logs.")
    else:
        summary.setdefault("status_label", "BROKER ACCOUNT UNKNOWN")
        summary.setdefault("message", "Tradovate account status is unavailable.")
        summary.setdefault("next_step", "Retry after the backend has refreshed its broker session.")


def _compute_quote(instrument: str) -> dict:
    """Blocking quote fetch — broker auth + get_quote + Yahoo reference price.

    Runs in a worker thread (see status_quote) so its ~10s of synchronous network
    I/O never freezes the async event loop and stall the health/today polls the
    dashboard uses to decide it is "live". Writes the result into _QUOTE_CACHE.
    """
    cache_key = instrument.upper()
    try:
        broker = _tv_broker()  # shared singleton — one auth token across endpoints
        # Seed the last price from the latest webhook payload if available.
        # ticker/close live in the nested `payload` — top-level lacks them, so
        # without the payload fallback ticker was "" and seeding never happened
        # (panel stuck on "no_bar_received_yet" despite bars arriving).
        latest = _latest_webhook_payload()
        payload = latest.get("payload") or {}
        close = latest.get("close") or payload.get("close")
        ticker = (latest.get("ticker") or payload.get("ticker") or "").replace("1!", "").upper()
        root = instrument.replace("1!", "").upper()
        if close and ticker == root:
            broker._last_price[root] = float(close)
        quote = broker.get_quote(instrument)
        # Display-only reference price (ES=F/NQ=F HTTP proxy) with freshness, kept
        # clearly separate from the broker price. NOT an execution feed — trading
        # logic never reads it. Fail-soft so a proxy hiccup can't break the panel.
        try:
            from quotes.live_index import get_live_quote
            ref = get_live_quote(instrument)
            if ref is not None:
                quote["reference_price"] = ref
        except Exception as exc:
            logger.warning("reference_price attach failed: %s", exc)
        _QUOTE_CACHE[cache_key] = (time.time(), quote)
        return quote
    except Exception as exc:
        logger.exception("status_quote failed: %s", exc)
        # Cache the failure briefly too, so a flapping upstream isn't hammered.
        err = {"ok": False, "error": str(exc)}
        _QUOTE_CACHE[cache_key] = (time.time(), err)
        return err


@app.get("/status/quote")
async def status_quote(instrument: str = Query(default="MES")) -> dict:
    """Return last known price for the instrument.

    Price comes from the close of the last TradingView webhook bar — Tradovate's
    REST API has no live quote endpoints (WebSocket-only). If no webhook has been
    received yet, returns ok=false with error=no_bar_received_yet.

    The actual fetch is offloaded to a thread (asyncio.to_thread) so a slow upstream
    can never block the event loop and flap the dashboard offline.
    """
    broker_mode = os.getenv("BROKER", "paper").strip().lower()
    if broker_mode != "tradovate":
        return {"ok": False, "error": f"BROKER={broker_mode}, not tradovate"}
    cache_key = instrument.upper()
    cached = _QUOTE_CACHE.get(cache_key)
    if cached is not None and (time.time() - cached[0]) < _QUOTE_TTL_SECONDS:
        return cached[1]
    return await asyncio.to_thread(_compute_quote, instrument)


@app.get("/status/test-bracket")
async def status_test_bracket(
    instrument: str = Query(default="MES"),
    direction: str = Query(default="LONG"),
) -> dict:
    """
    Dry-run bracket check. Resolves contract ID, fetches live quote, and computes
    what a bracket order WOULD look like. No order is placed.
    Only works when BROKER=tradovate.
    """
    broker_mode = os.getenv("BROKER", "paper").strip().lower()
    if broker_mode != "tradovate":
        return {"ok": False, "error": f"BROKER={broker_mode}, not tradovate"}
    direction = direction.upper()
    if direction not in ("LONG", "SHORT"):
        return {"ok": False, "error": "direction must be LONG or SHORT"}
    try:
        from execution.tradovate_broker import TradovateBroker
        broker = TradovateBroker()
        # Step 1 — get live quote
        q = broker.get_quote(instrument)
        if not q.get("ok"):
            return {"ok": False, "stage": "quote", "error": q.get("error")}
        price = q.get("price") or q.get("last")
        if price is None:
            return {"ok": False, "stage": "quote", "error": "no price in quote response"}
        # Step 2 — resolve contract ID (exercises the cache)
        try:
            contract_id = broker._find_contract_id(instrument)
        except Exception as exc:
            return {"ok": False, "stage": "contract_lookup", "error": str(exc)}
        # Step 3 — compute illustrative bracket (7-pt stop, 14-pt target — standard MES)
        tick = 0.25
        stop_pts = 7.0
        target_pts = 14.0
        if direction == "LONG":
            stop  = round(price - stop_pts, 2)
            target = round(price + target_pts, 2)
        else:
            stop  = round(price + stop_pts, 2)
            target = round(price - target_pts, 2)
        return {
            "ok": True,
            "dry_run": True,
            "instrument": instrument,
            "contract_id": contract_id,
            "symbol": q.get("symbol"),
            "direction": direction,
            "price": price,
            "bid": q.get("bid"),
            "ask": q.get("ask"),
            "bracket": {"entry": price, "stop": stop, "target": target},
            "note": "No order placed. Confirms quote + contract resolution works end-to-end.",
        }
    except Exception as exc:
        logger.exception("status_test_bracket failed: %s", exc)
        return {"ok": False, "error": str(exc)}


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


@app.get("/status/proof/mnq-30")
async def status_proof_mnq_30(
    freeze_ts: str | None = Query(default=None, description="Only count trades/outcomes at or after this ISO timestamp."),
    limit: int = Query(default=PROOF_30_MNQ_LIMIT, ge=1, le=300),
) -> dict:
    """Return the read-only MNQ proof-window report from approved runtime sources."""
    freeze = parse_proof_ts(freeze_ts) if freeze_ts else None
    if freeze_ts and freeze is None:
        raise HTTPException(status_code=422, detail="freeze_ts must be an ISO timestamp or Unix epoch.")
    status_payload = _dashboard_payload(date.today())
    broker_result = status_broker_account()
    broker_payload = await broker_result if inspect.isawaitable(broker_result) else broker_result
    return build_mnq_proof_report(
        journal_dir=Path(_config.log_dir),
        freeze_ts=freeze,
        limit=limit,
        api_base=None,
        status_payload=status_payload,
        broker_payload=broker_payload,
    )


# ─── Adaptive committee endpoints ────────────────────────────────────────────



@app.get("/status/signa")
async def status_signa(symbol: str = Query(default="AAPL")) -> dict:
    """Read-only Signa connectivity and parsing check."""
    from sources.signa_client import SignaClient

    requested_symbol = symbol.upper()
    client = SignaClient(
        base_url=_config.signa_base_url,
        timeout=_config.signa_timeout_seconds,
    )
    if not _config.signa_api_enabled:
        return {
            "enabled": False,
            "configured": _config.signa_api_key_configured,
            "symbol": requested_symbol,
            "ok": False,
            "error": "signa_api_disabled",
            "status_label": "DISABLED",
            "message": "Signa is configured but disabled; no Signa API calls are made.",
            "next_step": "Set SIGNA_API_ENABLED=true and restart the backend to enable read-only Signa checks.",
            "display": "DISABLED · no Signa API calls",
        }
    if not client.configured:
        return {
            "enabled": True,
            "configured": False,
            "symbol": requested_symbol,
            "ok": False,
            "error": "missing_api_key",
            "status_label": "MISSING KEY",
            "message": "Signa is enabled, but SIGNA_API_KEY is not configured.",
            "next_step": "Set SIGNA_API_KEY or turn SIGNA_API_ENABLED=false.",
            "display": "MISSING KEY",
        }
    signal = client.fetch_signal(requested_symbol)
    status_label = "CONNECTED" if signal.ok else "UNAVAILABLE"
    display_parts = [status_label]
    if signal.grade:
        display_parts.append(f"grade {signal.grade}")
    if signal.score is not None:
        display_parts.append(f"score {signal.score:g}")
    if signal.action:
        display_parts.append(str(signal.action).upper())
    if signal.risk_rating:
        display_parts.append(str(signal.risk_rating).upper())
    if signal.error:
        display_parts.append(signal.error)
    return {
        "enabled": True,
        "configured": client.configured,
        "status_label": status_label,
        "message": (
            "Signa read-only signal check is connected."
            if signal.ok
            else f"Signa read-only signal check failed: {signal.error or 'unknown_error'}."
        ),
        "next_step": (
            None
            if signal.ok
            else "Check Signa network reachability, API key validity, and the configured SIGNA_BASE_URL."
        ),
        "display": " · ".join(display_parts),
        **signal.to_dict(),
    }


@app.get("/status/gex-shadow")
async def status_gex_shadow(days: int = Query(default=30, ge=1, le=180)) -> dict:
    """Return default-off, read-only GEX shadow analysis across recent journals."""
    today = date.today()
    journal = JournalLogger(log_dir=_config.log_dir)
    entries: list[dict] = []
    journal_files_scanned = 0
    journal_entries_scanned = 0
    for offset in range(days):
        day = today - timedelta(days=offset)
        path = journal._journal_path(day)
        if path.exists():
            journal_files_scanned += 1
            day_entries = journal._read_entries(path)
            journal_entries_scanned += len(day_entries)
            entries.extend(
                entry
                for entry in day_entries
                if entry.get("type") == "OUTCOME"
                or (
                    entry.get("decision") == "TRADE"
                    and (entry.get("risk_check") or {}).get("result") == "APPROVED"
                )
            )
    payload = _gex_shadow_analysis_payload(entries)
    payload["days"] = days
    payload["journal_files_scanned"] = journal_files_scanned
    payload["journal_entries_scanned"] = journal_entries_scanned
    payload["analysis_entries_retained"] = len(entries)
    return payload


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
async def status_adaptive_history(days: int = Query(default=7, ge=1, le=90)) -> dict:
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


def _diag_slug(component: str) -> str:
    """Machine-readable code derived from a display label (fallback)."""
    import re
    return re.sub(r"[^a-z0-9]+", "_", component.strip().lower()).strip("_")


# Codes pinned to stay stable even if the display `component` label changes, so
# external consumers (UI, scripts, prompts) can key off `code` not `component`.
# Add the old label here on any rename to keep the code constant.
_DIAG_CODE_OVERRIDES = {
    "TradingView feed": "tradingview_feed",
    "TradingView alerts": "tradingview_feed",  # legacy label → same stable code
    "Tradovate config": "tradovate_config",
    "Trading mode": "trading_mode",
    "Backend API": "backend_api",
    "Broker": "broker",
    "Webhook secret": "webhook_secret",
    "Live box guard": "live_box_guard",
}


def _diagnostic(status: str, component: str, message: str, next_step: str | None = None,
                code: str | None = None) -> dict:
    item = {
        "status": status,
        "component": component,
        # Stable machine-readable identifier; display `component` may change freely.
        "code": code or _DIAG_CODE_OVERRIDES.get(component) or _diag_slug(component),
        "message": message,
    }
    if next_step:
        item["next_step"] = next_step
    return item


def _latest_webhook_age_seconds(latest: dict | None = None) -> int | None:
    latest = latest or _latest_webhook_payload()
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


def _latest_webhook_summary(latest: dict) -> str | None:
    payload = latest.get("payload") or {}
    context = latest.get("context") or {}
    ticker = payload.get("ticker") or context.get("instrument")
    close = payload.get("close") or context.get("close")
    timestamp = payload.get("timestamp") or context.get("timestamp")
    if not any((ticker, close, timestamp)):
        return None
    parts = []
    if ticker:
        parts.append(str(ticker))
    if close is not None:
        parts.append(f"close={close}")
    if timestamp:
        parts.append(f"bar_ts={timestamp}")
    return " | ".join(parts)


def _tradovate_env_diagnostic() -> dict:
    try:
        from execution.tradovate_broker import TradovateConfig
        config = TradovateConfig.from_env()
    except Exception as exc:
        return _diagnostic(
            "error",
            "Tradovate config",
            f"Tradovate environment is invalid: {exc}",
            "Set TRADOVATE_API_KEY_ID to the numeric CID only and put the UUID in TRADOVATE_API_KEY_SECRET.",
        )

    missing = []
    if not config.username:
        missing.append("TRADOVATE_USERNAME")
    if not config.password:
        missing.append("TRADOVATE_PASSWORD")
    if not config.cid:
        missing.append("TRADOVATE_API_KEY_ID")
    if not config.secret:
        missing.append("TRADOVATE_API_KEY_SECRET")
    if missing:
        return _diagnostic(
            "warn",
            "Tradovate config",
            f"Tradovate broker is selected, but missing: {', '.join(missing)}.",
            "Fill the missing demo credentials before attempting a manual OPEN.",
        )
    return _diagnostic(
        "ok",
        "Tradovate config",
        f"Tradovate credentials parse cleanly for {config.env}; this checks configuration, not the active broker session.",
        "If the account panel says session not active, refresh Tradovate auth and confirm the API ACL is enabled.",
    )


def _active_configured_windows(now: datetime | None = None) -> list[str]:
    try:
        from zoneinfo import ZoneInfo
        current = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo("America/New_York"))
    except Exception:
        current = now or datetime.now(timezone.utc)
    current_minutes = current.hour * 60 + current.minute
    active = []
    for session, windows in (_config.session_windows or {}).items():
        for window in windows or []:
            if not isinstance(window, dict) or not window.get("allow"):
                continue
            start = _hhmm_to_minutes(window.get("start"))
            end = _hhmm_to_minutes(window.get("end"))
            if start is None or end is None:
                continue
            if start <= end:
                in_window = start <= current_minutes < end
            else:
                in_window = current_minutes >= start or current_minutes < end
            if in_window:
                active.append(f"{session} {window.get('start')}-{window.get('end')}")
    return active


def _hhmm_to_minutes(value: object) -> int | None:
    try:
        hour, minute = str(value).split(":", 1)
        return int(hour) * 60 + int(minute)
    except (TypeError, ValueError):
        return None


def _quality_gate_summary() -> str:
    strong = [
        instrument
        for instrument, enabled in (_config.require_strong_trend or {}).items()
        if enabled
    ]
    volume = [
        f"{instrument}>={threshold:g}"
        for instrument, threshold in (_config.min_signal_bar_volume or {}).items()
        if float(threshold or 0) > 0
    ]
    htf = [
        instrument
        for instrument, enabled in (_config.require_htf_alignment or {}).items()
        if enabled
    ]
    if getattr(_config, "strict_directional_alignment", False):
        htf_summary = "strict (fail-closed)"
    else:
        htf_summary = ", ".join(htf) if htf else "passive"
    parts = [
        f"strong trend: {', '.join(strong) if strong else 'off'}",
        f"volume: {', '.join(volume) if volume else 'off'}",
        f"confluence: {(_config.min_confluence_grade or 'off')}",
        f"HTF: {htf_summary}",
    ]
    return "; ".join(parts) + "."


def _timeframe_mismatch_state(entries: list[dict]) -> dict | None:
    """Shared off-timeframe-alert detector for the banner AND the OPS diagnostic.

    Returns None if no TIMEFRAME_MISMATCH bars exist today. Otherwise returns
    {"blocks": [...], "last": <block>, "current": bool}. `current` is True only
    when the latest mismatch is newer than the last on-timeframe decision — i.e.
    the alert is STILL misconfigured right now, not already fixed earlier today.
    Single source of truth so the banner and OPS can't drift (they did once:
    47 overnight 5m alerts kept OPS red all day after the alert was fixed).
    """
    tf_blocks = [
        e for e in entries
        if e.get("decision") == "CONFIG_BLOCKED"
        and e.get("config_block") == "TIMEFRAME_MISMATCH"
    ]
    if not tf_blocks:
        return None

    def _ts(entry):
        t = entry.get("ts")
        try:
            return datetime.fromisoformat(t) if t else None
        except (ValueError, TypeError):
            return None

    last = tf_blocks[-1]
    block_ts = _ts(last)
    # Any non-mismatch decision proves an on-timeframe alert passed the guard.
    good_ts = [
        _ts(e) for e in entries
        if e.get("type") != "OUTCOME"
        and not (e.get("decision") == "CONFIG_BLOCKED"
                 and e.get("config_block") == "TIMEFRAME_MISMATCH")
    ]
    last_good = max((t for t in good_ts if t is not None), default=None)
    current = (
        block_ts is None       # unparseable — fail loud
        or last_good is None   # no on-TF bar ever seen today
        or block_ts > last_good
    )
    return {"blocks": tf_blocks, "last": last, "current": current}


def _feed_window_active(now: datetime | None = None) -> bool:
    """Thin wrapper over the shared futures_session_active() helper, kept as a
    module-level name so diagnostics and tests can reference/monkeypatch it."""
    return futures_session_active(now)


def _diagnostics_payload(for_date: date) -> dict:
    broker = os.getenv("BROKER", "paper").strip().lower()
    latest = _latest_webhook_payload()
    latest_age = _latest_webhook_age_seconds(latest)
    journal = JournalLogger(log_dir=_config.log_dir)
    journal_path = journal._journal_path(for_date)
    evidence_readiness = build_evidence_readiness(
        _config.log_dir,
        days=30,
        through_date=for_date,
        config=_config,
    )
    runner_shadow = runner_shadow_status(_config.log_dir)
    items = [
        _diagnostic("ok", "Backend API", "FastAPI is responding on the public API routes."),
    ]
    runner_shadow_diag_status = (
        "ok" if runner_shadow["proof_sufficient"] else
        "warn" if runner_shadow["enabled"] or runner_shadow["live_enabled"] else
        "info"
    )
    items.append(_diagnostic(
        runner_shadow_diag_status,
        "Runner shadow proof",
        runner_shadow["summary"],
        runner_shadow["next_step"],
    ))
    automation_status = automation_evidence_status(_config.log_dir)
    for job in automation_status["jobs"]:
        label = job["job"].replace("_", " ").title()
        path = job["evidence_path"] or "no matching artifact found"
        if job["status"] == "fresh":
            items.append(_diagnostic(
                "ok",
                f"Ops automation: {label}",
                f"Fresh evidence from {job['generated_at']} at {path}.",
            ))
        elif job["status"] == "stale":
            items.append(_diagnostic(
                "warn",
                f"Ops automation: {label}",
                f"Evidence is stale (age {job['age_seconds']}s; limit {job['fresh_for_seconds']}s) at {path}.",
                f"Check the external cron and its logs; do not run it from this status surface.",
            ))
        else:
            items.append(_diagnostic(
                "warn",
                f"Ops automation: {label}",
                f"No readable run evidence at {path}.",
                f"Check the external cron and its logs; evidence appears after the next successful job write.",
            ))

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
        # PAPER_MODE=false with live (real-money) trading off means orders route to
        # the broker's DEMO/sim account — intended "demo execution" mode, a VALID
        # configured state. Do NOT advise setting PAPER_MODE=true here: that disables
        # demo execution (back to internal sim). Only the genuinely ambiguous combos
        # (broker=paper, or a LIVE broker env gated off so nothing actually routes)
        # warrant the warn.
        tv_env = os.getenv("TRADOVATE_ENV", "").strip().lower()
        if broker != "paper" and tv_env and tv_env != "live":
            items.append(_diagnostic(
                "ok",
                "Trading mode",
                f"Demo execution active: orders route to the {broker} {tv_env} account; "
                "live (real-money) trading is off.",
            ))
        else:
            items.append(_diagnostic(
                "warn",
                "Trading mode",
                "Live trading is off, but PAPER_MODE is not true.",
                "Set PAPER_MODE=true for internal sim, or set a demo broker "
                "(e.g. BROKER=tradovate, TRADOVATE_ENV=demo) for demo execution.",
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

    if broker == "tradovate":
        items.append(_tradovate_env_diagnostic())
        try:
            from execution.live_preflight import live_order_status
            preflight = live_order_status()
            if preflight.get("ready"):
                items.append(_diagnostic("ok", "Live preflight", "Live trading is armed for today."))
            else:
                items.append(_diagnostic(
                    "warn",
                    "Live preflight",
                    f"Live orders are blocked: {preflight.get('reason')}.",
                    "Run /admin/live-preflight/run, resolve any failed checks, then arm today.",
                ))
        except Exception as exc:
            items.append(_diagnostic(
                "warn",
                "Live preflight",
                f"Live preflight state is unavailable: {exc}",
            ))
    else:
        items.append(_diagnostic("ok", "Broker", f"Broker is set to {broker}; no external gateway required."))

    guard = live_box_drift_report(
        risk_rules_path=getattr(_config, "risk_rules_path", "risk_rules.yaml"),
        log_dir=_config.log_dir,
        for_date=for_date,
        manual_controls_enabled=getattr(
            _config, "enable_manual_execution_controls", False
        ),
    )
    guard_status = str(guard.get("status") or "warn")
    items.append(_diagnostic(
        guard_status if guard_status in {"ok", "info", "warn", "error"} else "warn",
        "Live box guard",
        str(guard.get("summary") or "Live box guard unavailable."),
        None if guard.get("ok") else str(guard.get("next_step") or ""),
    ))
    active_overrides = list(guard.get("proof_critical_runtime_overrides") or [])
    active_overrides = [item for item in active_overrides if item.get("active")]
    unpinned_overrides = list(guard.get("unpinned_runtime_overrides") or [])
    if active_overrides:
        rendered = ", ".join(
            f"{item.get('name')}={item.get('observed')} "
            f"({'pinned' if item.get('pinned') else 'UNPINNED'})"
            for item in active_overrides
        )
        items.append(_diagnostic(
            "warn" if unpinned_overrides else "ok",
            "Proof runtime overrides",
            rendered,
            (
                "Pin each active value with EXPECTED_PROOF_<NAME> and restart "
                "before continuing the proof window."
                if unpinned_overrides else None
            ),
        ))
    else:
        items.append(_diagnostic(
            "ok",
            "Proof runtime overrides",
            "No proof-critical runtime strategy overrides are active.",
        ))

    security_runtime = guard.get("security_runtime") or {}
    if security_runtime:
        manual = security_runtime.get("manual_endpoint") or {}
        rotation = security_runtime.get("webhook_secret_rotation") or {}
        manual_inert = manual.get("effectively_inert") is True
        items.append(_diagnostic(
            "ok" if manual_inert else "error",
            "Manual webhook control",
            (
                "/webhook/manual is effectively inert; loaded runtime controls are disabled."
                if manual_inert else
                "/webhook/manual is active; loaded runtime controls are enabled."
            ),
            None if manual_inert else (
                "Disable ENABLE_MANUAL_EXECUTION_CONTROLS and restart the service."
            ),
        ))
        rotation_ready = rotation.get("rotation_ready") is True
        primary_configured = rotation.get("primary_configured") is True
        configured_names = ", ".join(rotation.get("configured_env_names") or []) or "none"
        items.append(_diagnostic(
            "ok" if rotation_ready else ("warn" if primary_configured else "error"),
            "Webhook secret rotation",
            (
                f"Secret env presence: {configured_names}; "
                f"distinct configured values: {rotation.get('distinct_configured_count', 0)}; "
                f"rotation ready: {'yes' if rotation_ready else 'no'}. "
                "Secret material is redacted."
            ),
            None if rotation_ready else (
                "Keep WEBHOOK_SECRET configured and stage a distinct value in "
                "TRADINGVIEW_WEBHOOK_SECRET or TRADINGVIEW_WEBHOOK_SECRET_NEXT."
            ),
        ))

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
        items.append(_diagnostic(
            "ok",
            "Signa",
            "Signa is enabled and configured; /status/signa performs the live read-only connectivity check.",
        ))
    else:
        items.append(_diagnostic("info", "Signa", "Signa is disabled; no Signa API calls are made."))

    items.append(_diagnostic("info", "Quality gates", _quality_gate_summary()))

    # Allowed-universe integrity: required instruments must be present.
    allowed_syms = [s.upper() for s in (_config.allowed_instruments or [])]
    required_syms = [s.upper() for s in (getattr(_config, "required_instruments", []) or [])]
    missing_syms = [s for s in required_syms if s not in allowed_syms]
    if missing_syms:
        items.append(_diagnostic(
            "error",
            "Allowed universe",
            f"CONFIG ERROR: required instrument(s) missing from the allowed universe: "
            f"{', '.join(missing_syms)} (allowed: {', '.join(allowed_syms) or 'none'}).",
            "Add them under instruments.allowed in risk_rules.yaml and restart the service.",
        ))
    else:
        items.append(_diagnostic(
            "ok",
            "Allowed universe",
            f"Allowed: {', '.join(allowed_syms)} · required present ({', '.join(required_syms)}).",
        ))

    # Decision timeframe: surface any misconfigured-alert (wrong timeframe) bars today.
    expected_tf = int(getattr(_config, "expected_timeframe_minutes", 15))
    diag_entries = journal._read_entries(journal_path) if journal_path.exists() else []
    tf_state = _timeframe_mismatch_state(diag_entries)
    if tf_state and tf_state["current"]:
        last = tf_state["last"]
        items.append(_diagnostic(
            "error",
            "Alert timeframe",
            f"LIVE ALERT MISCONFIGURED: expected {expected_tf}m, received "
            f"{last.get('received_timeframe')} ({len(tf_state['blocks'])} bar(s) today).",
            f"Recreate the TradingView alert on the {expected_tf}m chart.",
        ))
    elif tf_state:
        # Mismatches earlier today, but an on-timeframe bar has since been
        # evaluated — resolved, so this must NOT keep OPS red.
        items.append(_diagnostic(
            "ok",
            "Alert timeframe",
            f"Decision timeframe is {expected_tf}m; {len(tf_state['blocks'])} "
            f"off-timeframe alert(s) earlier today, now resolved.",
        ))
    else:
        items.append(_diagnostic(
            "ok",
            "Alert timeframe",
            f"Decision timeframe is {expected_tf}m; no off-timeframe alerts received today.",
        ))

    # Only treat an absent/stale feed as a problem when the futures session is
    # active and bars are actually expected — otherwise it's normal overnight/
    # weekend idle (matches the frontend IDLE state and the feed watchdog).
    feed_active = _feed_window_active()
    feed_stale_seconds = feed_stale_after_minutes(expected_tf) * 60  # shared ~2 bars + grace
    if latest_age is None:
        if feed_active:
            items.append(_diagnostic(
                "warn",
                "TradingView feed",
                "No TradingView webhook has been received yet; backend API is still online.",
                "Check the TradingView alert URL, webhook secret, and whether the alert is enabled.",
            ))
        else:
            items.append(_diagnostic(
                "info",
                "TradingView feed",
                "No TradingView webhook yet; outside the active futures session, so none is expected right now.",
            ))
    elif latest_age > feed_stale_seconds:
        age_txt = _format_generated_age(_latest_webhook_payload().get("received_at"))
        if feed_active:
            items.append(_diagnostic(
                "warn",
                "TradingView feed",
                f"TradingView feed is stale; last webhook was {age_txt} (expected a {expected_tf}m bar every {expected_tf}m).",
                "The futures session is active — check whether the TradingView alert is still running.",
            ))
        else:
            items.append(_diagnostic(
                "info",
                "TradingView feed",
                f"TradingView feed idle; last webhook was {age_txt}. Outside the active futures session, so no webhooks are expected.",
            ))
    else:
        items.append(_diagnostic("ok", "TradingView feed", "TradingView webhooks are arriving recently."))

    latest_summary = _latest_webhook_summary(latest)
    if latest_summary:
        items.append(_diagnostic("info", "Latest webhook", latest_summary))

    active_windows = _active_configured_windows()
    if active_windows:
        items.append(_diagnostic(
            "ok",
            "Configured windows",
            f"Active allow window(s): {', '.join(active_windows)}.",
        ))
    else:
        items.append(_diagnostic(
            "info",
            "Configured windows",
            "No configured allow-only test/session window is active right now.",
        ))

    if journal_path.exists():
        items.append(_diagnostic("ok", "Journal", f"Today journal exists at {journal_path}."))
    else:
        items.append(_diagnostic("info", "Journal", "No journal file exists yet today; no decisions have been recorded."))

    readiness_summary = evidence_readiness["summary"]
    items.append(_diagnostic(
        "info",
        "Research evidence",
        (
            f"{readiness_summary['ready_for_review']} ready for human review · "
            f"{readiness_summary['collecting']} collecting/insufficient · "
            f"{readiness_summary['blocked']} data-quality blocked · "
            f"{readiness_summary['inactive']} inactive."
        ),
        "Use /status/evidence-readiness for the read-only per-track scorecard.",
    ))

    rank = {"error": 3, "warn": 2, "info": 1, "ok": 0}
    worst = max(items, key=lambda item: rank.get(item["status"], 0))
    overall = "error" if worst["status"] == "error" else "warn" if worst["status"] == "warn" else "ok"
    return {
        "ok": overall == "ok",
        "overall_status": overall,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "top_issue": None if overall == "ok" else worst,
        "ops_automations": automation_status,
        "evidence_readiness": evidence_readiness,
        "runner_shadow": runner_shadow,
        "items": items,
    }


def _safe_live_preflight_status() -> dict:
    try:
        from execution.live_preflight import live_order_status
        return live_order_status()
    except Exception as exc:
        return {"ready": False, "reason": f"unavailable:{exc}", "armed": False}


def _dashboard_payload(for_date: date) -> dict:
    journal = JournalLogger(log_dir=_config.log_dir)
    daily_state = journal.get_daily_state(for_date)
    summary = journal.get_summary(for_date)
    path = journal._journal_path(for_date)
    entries = journal._read_entries(path) if path.exists() else []
    decision_entries = [e for e in entries if e.get("type") != "OUTCOME"]
    recent_entries = entries[-10:]
    no_trade_reasons = Counter(
        entry.get("reason", "Unknown")
        for entry in decision_entries
        if entry.get("decision") == "NO_TRADE"
    )
    # Latest timeframe-mismatch (misconfigured alert) seen today, if any. These
    # are journaled as CONFIG_BLOCKED / TIMEFRAME_MISMATCH — a distinct category,
    # never counted as NO_TRADE — and drive the loud dashboard banner.
    tf_state = _timeframe_mismatch_state(decision_entries)
    alert_validation = None
    if tf_state and tf_state["current"]:
        latest_block = tf_state["last"]
        alert_validation = {
            "ok": False,
            "issue": "TIMEFRAME_MISMATCH",
            "expected": latest_block.get("expected_timeframe"),
            "received": latest_block.get("received_timeframe"),
            "count": len(tf_state["blocks"]),
            "last_ts": latest_block.get("ts"),
        }
    wins = summary.get("wins", 0)
    losses = summary.get("losses", 0)
    resolved = wins + losses
    win_rate = round((wins / resolved) * 100, 1) if resolved else 0.0
    account_balance = journal.get_account_balance(_config.position_sizing.starting_balance, for_date)
    account_peak = journal.get_account_peak_balance(_config.position_sizing.starting_balance, for_date)
    realized_pnl = round(account_balance - _config.position_sizing.starting_balance, 2)
    diagnostics = _diagnostics_payload(for_date)
    gex_shadow_analysis = _gex_shadow_analysis_payload(entries)
    evidence_readiness = diagnostics["evidence_readiness"]
    runner_shadow = diagnostics["runner_shadow"]
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
        # When BROKER=tradovate and a position is open outside session hours, the
        # bracket orders on Tradovate's side will close it. No action required from
        # this system — just flag it so the dashboard makes the state obvious.
        "position_bracket_managed": (
            daily_state.has_open_position
            and os.getenv("BROKER", "paper").strip().lower() == "tradovate"
        ),
        "no_trades": summary.get("no_trades", 0),
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "realized_pnl_dollars": round(realized_pnl, 2),
        "today_pnl_dollars": round(float(daily_state.realized_pnl_dollars or 0.0), 2),
        "journal_path": summary.get("journal_path", str(path)),
        "latest_entries": [_public_entry(entry) for entry in recent_entries],
        "instrument_breakdown": _instrument_breakdown(entries),
        "top_no_trade_reasons": [
            {"reason": reason, "count": count}
            for reason, count in no_trade_reasons.most_common(5)
        ],
        "latest_webhook": _latest_webhook_payload(),
        "latest_webhooks": _latest_webhooks_by_instrument(),
        "strategy_status": _strategy_payload(for_date),
        "performance": journal.get_performance_stats(_config.position_sizing.starting_balance),
        "broker_gateway_reachable": None,  # IBKR-only concept; broker removed
        "diagnostics": diagnostics,
        "gex_shadow_analysis": gex_shadow_analysis,
        "evidence_readiness": evidence_readiness,
        "runner_shadow": runner_shadow,
        "live_preflight": _safe_live_preflight_status(),
        "live_box_drift_guard": live_box_drift_report(
            risk_rules_path=getattr(_config, "risk_rules_path", "risk_rules.yaml"),
            log_dir=_config.log_dir,
            for_date=for_date,
        ),
        "alert_validation": alert_validation,
        "expected_timeframe_minutes": int(getattr(_config, "expected_timeframe_minutes", 15)),
        # Feed-health window + stale threshold from the one shared definition, so the
        # dashboards stop deciding "is a webhook expected now?" with their own clocks.
        "feed_window_active": futures_session_active(),
        "feed_stale_after_minutes": feed_stale_after_minutes(
            int(getattr(_config, "expected_timeframe_minutes", 15))
        ),
        "instrument_universe": list(_config.allowed_instruments),
    }


def _gex_shadow_analysis_payload(entries: list[dict]) -> dict:
    try:
        from sources.gex_shadow_analysis import disabled_summary, summarize_gex_shadow

        if not getattr(_config, "gex_shadow_analysis_enabled", False):
            return disabled_summary()
        return summarize_gex_shadow(entries)
    except Exception as exc:  # noqa: BLE001 - status analysis must never affect dashboard
        return {
            "enabled": bool(getattr(_config, "gex_shadow_analysis_enabled", False)),
            "mode": "observe_only",
            "trade_gating_changed": False,
            "error": exc.__class__.__name__,
        }


def _instrument_breakdown(entries: list[dict]) -> list[dict]:
    instruments = [
        instrument
        for instrument in _config.allowed_instruments
        if instrument in {"MES", "MNQ"}
    ]
    if not instruments:
        instruments = ["MES", "MNQ"]

    breakdown = []
    for instrument in instruments:
        inst_entries = [
            entry for entry in entries
            if (entry.get("instrument") or "").upper() == instrument
        ]
        decision_entries = [entry for entry in inst_entries if entry.get("type") != "OUTCOME"]
        outcome_entries = [entry for entry in inst_entries if entry.get("type") == "OUTCOME"]
        trades = [
            entry for entry in decision_entries
            if entry.get("decision") == "TRADE"
            and (entry.get("risk_check") or {}).get("result") == "APPROVED"
        ]
        no_trades = [entry for entry in decision_entries if entry.get("decision") == "NO_TRADE"]
        pnl = 0.0
        wins = 0
        losses = 0
        for entry in outcome_entries:
            outcome = entry.get("outcome") or {}
            result = outcome.get("result")
            if result == "WIN":
                wins += 1
            elif result == "LOSS":
                losses += 1
            pnl += float(outcome.get("pnl_dollars") or 0.0)

        latest = _public_entry(inst_entries[-1]) if inst_entries else None
        breakdown.append({
            "instrument": instrument,
            "decisions": len(decision_entries),
            "trades": len(trades),
            "no_trades": len(no_trades),
            "wins": wins,
            "losses": losses,
            "pnl_dollars": round(pnl, 2),
            "latest": latest,
            "latest_entries": [_public_entry(entry) for entry in inst_entries[-5:]],
        })
    return breakdown


def _public_entry(entry: dict) -> dict:
    outcome = entry.get("outcome") or {}
    setup = entry.get("setup") or {}
    pnl_dollars = outcome.get("pnl_dollars")
    raw_exit_reason = outcome.get("exit_reason")
    outcome_explanation = _explain_outcome(outcome)
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
        "exit_reason": outcome_explanation["display_exit_reason"],
        "raw_exit_reason": raw_exit_reason,
        "pnl_dollars": pnl_dollars,
        "outcome_explanation": outcome_explanation["message"],
        "outcome_warning": outcome_explanation["warning"],
    }


def _explain_outcome(outcome: dict) -> dict:
    result = outcome.get("result")
    exit_reason = outcome.get("exit_reason")
    pnl = outcome.get("pnl_dollars")
    try:
        pnl_value = float(pnl) if pnl is not None else None
    except (TypeError, ValueError):
        pnl_value = None

    if not result and not exit_reason:
        return {"message": None, "warning": False, "display_exit_reason": None}

    if exit_reason == "TARGET_HIT" and pnl_value is not None and pnl_value < 0:
        return {
            "message": "Loss recorded. Raw exit label said TARGET_HIT, but negative P&L means this was not a profit target.",
            "warning": True,
            "display_exit_reason": "LOSS_RECORDED",
        }
    if exit_reason == "STOP_HIT" and pnl_value is not None and pnl_value > 0:
        return {
            "message": "Profit recorded. Raw exit label said STOP_HIT, but positive P&L means this was not a losing stop.",
            "warning": True,
            "display_exit_reason": "PROFIT_RECORDED",
        }
    if exit_reason == "TARGET_HIT":
        return {"message": "Closed because target was hit.", "warning": False, "display_exit_reason": "TARGET_HIT"}
    if exit_reason == "STOP_HIT":
        return {"message": "Closed because stop was hit.", "warning": False, "display_exit_reason": "STOP_HIT"}
    if exit_reason == "BREAKEVEN_STOP":
        return {"message": "Closed at breakeven stop.", "warning": False, "display_exit_reason": "BREAKEVEN_STOP"}
    if exit_reason and str(exit_reason).startswith("FORCE_CLOSE_"):
        return {
            "message": f"Force closed: {str(exit_reason).replace('FORCE_CLOSE_', '').replace('_', ' ').lower()}.",
            "warning": False,
            "display_exit_reason": exit_reason,
        }
    if exit_reason:
        return {"message": f"Closed by {exit_reason}.", "warning": False, "display_exit_reason": exit_reason}
    return {"message": f"Outcome recorded as {result}.", "warning": False, "display_exit_reason": result}


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


# ─────────────────────────────────────────────────────────────────────────────
# Operator dashboard — tabbed single page.
#
# The server ships a JSON view-model embedded in the page; every tab is rendered
# client-side from that view-model so the 30s refresh reuses one render path and
# the server never duplicates presentation logic. UI only — no trading state is
# mutated here; the only manual action (CLOSE_ALL emergency exit) goes through
# the guarded /webhook/manual endpoint. Force-OPEN was removed.
# ─────────────────────────────────────────────────────────────────────────────

_FUTURES_UNIVERSE = ["MES", "MNQ", "MGC", "MCL"]


def _dashboard_init(status: dict) -> dict:
    """Assemble the JSON view-model the client renders every tab from."""
    committee = _load_committee_panel(_config.log_dir)
    broker = (os.getenv("BROKER", "paper") or "paper").strip().upper()
    allowed = {s.upper() for s in (_config.allowed_instruments or [])}
    required = [s.upper() for s in (getattr(_config, "required_instruments", []) or [])]
    universe = [
        {"sym": s, "enabled": s in allowed, "required": s in required}
        for s in _FUTURES_UNIVERSE
    ]
    universe_missing = [s for s in required if s not in allowed]
    return {
        "today": status,
        "committee": committee,
        "universe": universe,
        # Required instruments missing from the allowed universe → CONFIG ERROR.
        "universe_missing": universe_missing,
        # Loud "LIVE ALERT MISCONFIGURED" banner data (timeframe mismatch today).
        "alert_validation": status.get("alert_validation"),
        "expected_timeframe_minutes": int(getattr(_config, "expected_timeframe_minutes", 15)),
        "broker": broker,
        "paper_mode": bool(status.get("paper_mode", True)),
        "live_trading_enabled": bool(status.get("live_trading_enabled")),
        "max_drawdown_pct": round(float(getattr(_config, "max_drawdown_percent", 0.10)) * 100, 2),
        "poll_seconds": 30,
        # Monitor-only mode: when False the UI renders NO manual execution controls.
        "manual_controls_enabled": bool(
            getattr(_config, "enable_manual_execution_controls", False)
        ),
    }


_DASHBOARD_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
  <meta name="theme-color" content="#050507">
  <meta name="mobile-web-app-capable" content="yes">
  <meta name="apple-mobile-web-app-capable" content="yes">
  <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
  <meta name="apple-mobile-web-app-title" content="Backend Console">
  <link rel="manifest" href="/manifest.json">
  <link rel="apple-touch-icon" href="/static/icon-192.png">
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@500;650;750;850&family=JetBrains+Mono:wght@500;700;800&display=swap" rel="stylesheet">
  <title>RiskSentinel Backend Console</title>
  <style>
    :root {
      color-scheme: dark;
      --font-ui: "Inter", ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      --font-console: "JetBrains Mono", "SFMono-Regular", Menlo, Monaco, Consolas, monospace;
      --bg: #05060d;
      --shell: #080a14;
      --shell2: #101427;
      --panel: #171a2d;
      --panel2: #111527;
      --panel3: #0d1020;
      --line: #2b3458;
      --line-soft: rgba(126, 138, 185, 0.18);
      --text: #f3f5ff;
      --muted: #8d94b3;
      --muted2: #626b8f;
      --purple: #9a68ff;
      /* severity system */
      --green: #00FF88;   /* pass / live / clear / fresh / profit */
      --yellow: #FFB800;  /* warning / stale / watch / defend */
      --red: #FF4444;     /* blocked / locked / loss / error */
      --blue: #00d5ff;    /* info / paper / broker */
      --gray: #8b90a6;    /* inactive / disabled / neutral */
      --nav-h: 54px;
    }
    * { box-sizing: border-box; }
    html, body { margin: 0; }
    body {
      min-height: 100vh;
      background: var(--bg);
      color: var(--text);
      font-family: var(--font-ui);
      -webkit-text-size-adjust: 100%;
      font-weight: 650;
    }
    h1, h2, h3, p { margin: 0; }
    a { color: inherit; }
    .green { color: var(--green); }
    .yellow { color: var(--yellow); }
    .red { color: var(--red); }
    .blue { color: var(--blue); }
    .gray, .muted { color: var(--muted); }

    .live-frame {
      width: min(1156px, calc(100% - 24px));
      margin: 10px auto 0;
      border: 1px solid var(--line-soft);
      border-radius: 18px;
      overflow: hidden;
      background: var(--shell);
      box-shadow: 0 18px 40px rgba(0,0,0,0.32);
    }
    .mode-tabs {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 12px;
      padding: 22px 28px 16px;
      background: var(--shell);
    }
    .mode-tabs button {
      position: relative;
      height: 68px;
      border: 1px solid var(--line);
      border-radius: 18px;
      background: var(--panel2);
      color: var(--muted2);
      font: inherit;
      font-family: var(--font-console);
      font-size: 21px;
      font-weight: 800;
      letter-spacing: 0.16em;
      text-transform: uppercase;
      cursor: pointer;
    }
    .mode-tabs button.on {
      color: var(--purple);
      background: var(--panel3);
      border-color: rgba(154,104,255,0.62);
    }
    .mode-tabs button.on::before {
      content: "";
      position: absolute;
      top: 0;
      left: 50%;
      transform: translateX(-50%);
      width: 40px;
      height: 4px;
      border-radius: 0 0 4px 4px;
      background: var(--purple);
      box-shadow: 0 0 14px rgba(154,104,255,0.85);
    }
    .appbar {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 18px;
      padding: 6px 24px 20px;
      background: var(--shell);
      border-bottom: 1px solid var(--line-soft);
    }
    .brand-title {
      font-family: var(--font-console);
      font-size: 29px;
      font-weight: 900;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }
    .brand-title .afs { color: var(--purple); }
    .brand-title .slash { color: var(--muted2); margin: 0 8px; }
    .brand-sub {
      margin-top: 8px;
      color: var(--muted);
      font-family: var(--font-console);
      font-size: 14px;
      font-weight: 700;
      letter-spacing: 0.08em;
    }
    .app-actions { display: flex; align-items: center; gap: 18px; }
    .live-chip {
      border: 1px solid rgba(0, 255, 136, 0.50);
      border-radius: 10px;
      padding: 8px 15px;
      color: var(--green);
      background: rgba(0, 255, 136, 0.08);
      font-family: var(--font-console);
      font-size: 20px;
      font-weight: 900;
      letter-spacing: 0.08em;
    }
    .refresh-tile {
      width: 76px;
      height: 76px;
      border: 1px solid var(--line);
      border-radius: 18px;
      background: #171c35;
      color: #aab4d4;
      font-size: 33px;
      cursor: pointer;
      transition: transform 0.2s ease, border-color 0.2s ease, color 0.2s ease;
    }
    .refresh-tile:hover { transform: translateY(-2px); border-color: rgba(154,104,255,0.62); color: var(--text); }

    /* ── Global status bar ─────────────────────────────────────────────── */
    .statusbar {
      z-index: 30;
      display: flex;
      gap: 0;
      align-items: stretch;
      background: var(--shell2);
      backdrop-filter: blur(8px);
      border-bottom: 1px solid var(--line);
      overflow-x: auto;
      -webkit-overflow-scrolling: touch;
      scrollbar-width: none;
      padding: 0 14px;
    }
    .statusbar::-webkit-scrollbar { display: none; }
    .statusbar .seg {
      display: flex;
      flex-direction: row;
      justify-content: center;
      align-items: center;
      gap: 6px;
      padding: 14px 14px;
      white-space: nowrap;
      border-right: 0;
      color: var(--muted);
    }
    .statusbar .seg:last-child { border-right: 0; }
    .statusbar .seg b {
      display: inline;
      color: var(--muted);
      font-family: var(--font-console);
      font-size: 16px;
      font-weight: 800;
      letter-spacing: 0.08em;
    }
    .statusbar .seg span { font-family: var(--font-console); font-size: 17px; font-weight: 900; letter-spacing: 0.02em; }

    /* ── Layout ────────────────────────────────────────────────────────── */
    main {
      width: min(1120px, calc(100% - 24px));
      margin: 0 auto;
      padding: 22px 0 calc(var(--nav-h) + 28px);
    }
    .tab { display: none; }
    .tab.active { display: block; }
    .tabhead {
      display: flex; align-items: baseline; justify-content: space-between;
      gap: 12px; margin: 4px 0 14px;
    }
    .tabhead h1 { font-size: 21px; }
    .tabhead .when { color: var(--muted); font-size: 12px; }

    .hero {
      border: 1px solid var(--line);
      border-radius: 12px;
      background: linear-gradient(180deg, rgba(23,26,45,0.98), rgba(18,21,39,0.98));
      padding: 16px;
      margin-bottom: 12px;
    }
    .hero-top { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
    .hero h1 { font-size: 23px; font-weight: 850; letter-spacing: 0.01em; }
    .badges { display: flex; gap: 8px; flex-wrap: wrap; }
    .badge {
      display: inline-flex; align-items: center; gap: 6px;
      border: 1px solid var(--line); border-radius: 999px;
      background: rgba(255,255,255,0.018);
      padding: 5px 10px;
      font-family: var(--font-console);
      font-size: 12px; font-weight: 800;
    }
    .mood { margin-top: 10px; color: var(--muted); font-size: 14px; line-height: 1.45; }

    .monitorbar {
      margin: 8px 12px 0; padding: 9px 12px; border-radius: 10px;
      background: rgba(0,213,255,0.08); border: 1px solid rgba(0,213,255,0.30);
      color: #cfe2ff; font-size: 12px; line-height: 1.5; text-align: center;
    }
    .monitorbar b { color: #eaf2ff; letter-spacing: 0.04em; }

    .sandbox-banner {
      z-index: 29;
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 10px;
      padding: 8px 12px;
      background: rgba(255, 184, 0, 0.12);
      border-bottom: 1px solid rgba(255, 184, 0, 0.45);
      color: #ffe3a3;
      font-family: var(--font-console);
      font-size: 12px;
      font-weight: 800;
      letter-spacing: 0.04em;
      text-align: center;
    }
    .sandbox-banner .soft { color: var(--muted); font-weight: 700; letter-spacing: 0; }

    .alertbar {
      margin: 8px 12px 0; padding: 11px 14px; border-radius: 10px;
      background: rgba(255,68,68,0.12); border: 1px solid rgba(255,68,68,0.55);
      color: #ffd9e2; font-size: 13px; line-height: 1.55; text-align: center;
      font-weight: 600;
    }
    .alertbar b { color: #fff; letter-spacing: 0.05em; display: block; font-size: 14px; margin-bottom: 2px; }
    .alertbar .sub { font-weight: 500; color: #ffb3c4; font-size: 12px; }

    .panel {
      background: linear-gradient(180deg, rgba(23,26,45,0.98), rgba(18,21,39,0.98));
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 15px;
      margin-bottom: 12px;
      transition: transform 0.2s ease, border-color 0.2s ease, background-color 0.2s ease;
    }
    .panel:hover { transform: translateY(-2px); border-color: rgba(255,255,255,0.18); }
    .panel > h2 {
      color: var(--muted);
      font-size: 11px;
      font-weight: 800;
      letter-spacing: 0.05em;
      text-transform: uppercase;
      margin-bottom: 4px;
    }
    .panel.accent-yellow { border-color: rgba(255,184,0,0.45); }
    .panel.accent-red { border-color: rgba(255,68,68,0.40); }
    .panel.accent-green { border-color: rgba(0,255,136,0.40); }

    .source-grid {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 8px;
      margin-top: 10px;
    }
    .source-item {
      border: 1px solid var(--line);
      border-radius: 10px;
      background: rgba(255,255,255,0.022);
      padding: 9px 10px;
      min-height: 62px;
    }
    .source-item b {
      display: block;
      color: var(--text);
      font-family: var(--font-console);
      font-size: 11px;
      font-weight: 900;
      letter-spacing: 0.04em;
      text-transform: uppercase;
    }
    .source-item span { display: block; margin-top: 5px; color: var(--muted); font-size: 12px; line-height: 1.35; }
    .source-note {
      display: flex;
      align-items: center;
      gap: 7px;
      flex-wrap: wrap;
      margin-top: 10px;
      color: var(--muted);
      font-family: var(--font-console);
      font-size: 11px;
      font-weight: 800;
      letter-spacing: 0.02em;
    }
    .source-pill {
      display: inline-flex;
      align-items: center;
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 3px 8px;
      font-size: 10px;
      font-weight: 900;
      letter-spacing: 0.04em;
      text-transform: uppercase;
    }
    .source-pill.pulled { color: var(--green); border-color: rgba(0,255,136,0.34); background: rgba(0,255,136,0.06); }
    .source-pill.derived { color: var(--blue); border-color: rgba(0,213,255,0.34); background: rgba(0,213,255,0.06); }
    .source-pill.waiting { color: var(--yellow); border-color: rgba(255,184,0,0.34); background: rgba(255,184,0,0.08); }
    .source-pill.not-pulled { color: var(--gray); border-color: rgba(139,144,166,0.28); background: rgba(139,144,166,0.06); }
    @media (max-width: 720px) { .source-grid { grid-template-columns: 1fr; } }

    .grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
    @media (max-width: 560px) { .grid2 { grid-template-columns: 1fr; } }

    /* decision chip */
    .decision {
      display: inline-flex; align-items: center; gap: 8px;
      font-size: 22px; font-weight: 900; letter-spacing: 0.02em;
    }
    .decision .dot { width: 11px; height: 11px; border-radius: 50%; background: currentColor; }
    .d-NO_TRADE, .d-WAITING { color: var(--yellow); }
    .d-TRADE, .d-TRADE_READY { color: var(--green); }
    .d-RISK_REJECTED { color: var(--red); }
    .d-CONFIG_BLOCKED { color: var(--gray); }

    /* key/value rows */
    .kv { display: grid; grid-template-columns: auto 1fr; gap: 7px 14px; margin-top: 10px; font-size: 14px; }
    .kv dt { color: var(--muted); }
    .kv dd { margin: 0; text-align: right; font-family: var(--font-console); font-weight: 800; }

    .metric-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 9px; margin-top: 12px; }
    .metric {
      border: 1px solid var(--line); border-radius: 10px; background: rgba(255,255,255,0.025);
      padding: 10px 12px;
    }
    .metric label { display: block; color: var(--muted); font-size: 10px; font-weight: 750; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 5px; }
    .metric strong { font-family: var(--font-console); font-size: 18px; font-weight: 800; }
    .sparkline { font-family: var(--font-console); color: var(--blue); font-size: 12px; letter-spacing: 0.05em; margin-left: 8px; white-space: nowrap; }
    .empty-val { color: var(--gray); opacity: 0.65; cursor: help; }
    .placeholder { color: var(--gray); opacity: 0.7; font-style: italic; }
    .update-line { margin-top: 8px; color: var(--gray); font-family: var(--font-console); font-size: 11px; }
    .skeleton {
      position: relative; overflow: hidden; border-radius: 8px; background: rgba(255,255,255,0.055);
      min-height: 14px;
    }
    .skeleton::after {
      content: ""; position: absolute; inset: 0; transform: translateX(-100%);
      background: linear-gradient(90deg, transparent, rgba(255,255,255,0.09), transparent);
      animation: shimmer 1.25s infinite;
    }
    .skeleton.line { width: 72%; }
    .skeleton.short { width: 44%; }
    @keyframes shimmer { 100% { transform: translateX(100%); } }
    @media (max-width: 560px) { .metric-grid { grid-template-columns: 1fr; } }

    ul.reasons { list-style: none; padding: 0; margin: 8px 0 0; }
    ul.reasons li {
      display: flex; gap: 9px; align-items: flex-start;
      padding: 8px 12px; border-top: 0; border-left: 3px solid var(--yellow);
      border-radius: 6px; background: rgba(255, 184, 0, 0.10);
      font-size: 13px; color: #ffe3a3; margin-top: 7px;
    }
    ul.reasons li .mk { font-weight: 900; flex: none; }
    ul.reasons li:first-child { border-top: 0; }

    .pill {
      display: inline-block; border: 1px solid var(--line); border-radius: 999px;
      padding: 3px 9px; font-size: 11px; font-weight: 800; letter-spacing: 0.03em;
    }
    .pill.green { color: var(--green); border-color: rgba(0,255,136,0.4); }
    .pill.yellow { color: var(--yellow); border-color: rgba(255,184,0,0.4); }
    .pill.red { color: var(--red); border-color: rgba(255,68,68,0.4); }
    .pill.gray { color: var(--gray); }
    .pill.blue { color: var(--blue); border-color: rgba(0,213,255,0.4); }

    .freshbar {
      display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
      font-size: 13px;
    }
    .freshbar .tag { font-weight: 900; letter-spacing: 0.04em; }

    .ctx { display: grid; grid-template-columns: repeat(auto-fit, minmax(120px, 1fr)); gap: 9px; margin-top: 8px; }
    .ctx .c { border-top: 1px solid var(--line); padding-top: 8px; }
    .ctx .c label { display: block; color: var(--muted); font-size: 10px; text-transform: uppercase; margin-bottom: 3px; }
    .ctx .c strong { font-size: 15px; }

    .stat-row { display: grid; grid-template-columns: repeat(auto-fit, minmax(110px,1fr)); gap: 4px; margin-top: 6px; }
    .stat-row .s { border-top: 1px solid var(--line); padding: 9px 4px; }
    .stat-row .s label { display: block; color: var(--muted); font-size: 10px; text-transform: uppercase; margin-bottom: 3px; }
    .stat-row .s strong { font-size: 18px; font-weight: 800; }

    /* universe */
    .uni { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 8px; }
    .uni .u { display: flex; align-items: center; gap: 7px; border: 1px solid var(--line); border-radius: 8px; padding: 7px 10px; font-size: 13px; }
    .uni .u b { font-weight: 800; }

    .futures-layout {
      display: grid;
      grid-template-columns: minmax(0, 1fr) 304px;
      gap: 12px;
      align-items: start;
    }
    .futures-main, .futures-rail {
      display: flex;
      flex-direction: column;
      gap: 12px;
      min-width: 0;
    }
    .futures-main .panel, .futures-rail .panel { margin-bottom: 0; }
    .futures-overview {
      display: grid;
      grid-template-columns: minmax(190px, 0.85fr) minmax(0, 1.15fr);
      gap: 12px;
      align-items: stretch;
    }
    .futures-overview .overview-cell {
      border: 1px solid var(--line-soft);
      border-radius: 10px;
      background: rgba(255,255,255,0.018);
      padding: 10px 12px;
      min-width: 0;
    }
    .overview-label {
      color: var(--muted);
      font-family: var(--font-console);
      font-size: 10px;
      font-weight: 900;
      letter-spacing: 0.06em;
      text-transform: uppercase;
    }
    .overview-value {
      margin-top: 6px;
      color: var(--text);
      font-family: var(--font-console);
      font-size: 15px;
      font-weight: 900;
      line-height: 1.35;
    }
    .overview-sub { margin-top: 4px; color: var(--muted); font-size: 12px; line-height: 1.4; }
    .instrument-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; align-items: start; }
    .instrument-card { min-height: 100%; }
    .instrument-head {
      display: flex; align-items: center; justify-content: space-between; gap: 10px;
      border-bottom: 1px solid var(--line); padding-bottom: 10px; margin-bottom: 10px;
    }
    .instrument-head h2 {
      margin: 0; color: var(--text); font-family: var(--font-console);
      font-size: 20px; font-weight: 800; letter-spacing: 0.08em;
    }
    .instrument-meta { color: var(--muted); font-size: 11px; font-family: var(--font-console); }
    .instrument-decision { margin-top: 6px; font-size: 20px; }
    .mini-context { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    @media (max-width: 1040px) {
      .futures-layout { grid-template-columns: 1fr; }
      .futures-rail { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); }
    }
    @media (max-width: 780px) {
      .instrument-grid { grid-template-columns: 1fr; }
      .futures-overview { grid-template-columns: 1fr; }
      .futures-rail { display: flex; }
    }

    .risk-ladder { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 8px; margin-top: 12px; }
    .risk-step {
      display: flex; align-items: center; justify-content: center; gap: 7px;
      min-height: 44px; border: 1px solid var(--line); border-radius: 10px;
      background: rgba(255,255,255,0.025);
      color: var(--muted); font-family: var(--font-console);
      font-size: 12px; font-weight: 800; letter-spacing: 0.03em;
      transition: color 0.2s ease, background-color 0.2s ease, border-color 0.2s ease, opacity 0.2s ease;
    }
    .risk-step.active { background: rgba(0,255,136,0.06); }
    .risk-step.active.yellow { background: rgba(255,184,0,0.08); }
    .risk-step.active.red { background: rgba(255,68,68,0.08); }
    .risk-step.dim { opacity: 0.48; }
    @media (max-width: 560px) { .risk-ladder { grid-template-columns: 1fr 1fr; } }

    /* committee */
    .cmt { display: grid; grid-template-columns: auto 1fr auto; gap: 6px 12px; margin-top: 8px; font-size: 14px; align-items: center; }
    .cmt .nm { color: var(--muted); }
    .cmt .note { font-size: 12px; color: var(--muted); grid-column: 1 / -1; margin: -2px 0 4px; }
    .consensus { margin-top: 12px; padding-top: 11px; border-top: 1px solid var(--line); font-size: 15px; font-weight: 800; }

    /* journal */
    .filters { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 10px; }
    .filters button {
      border: 1px solid var(--line); background: transparent; color: var(--muted);
      border-radius: 999px; padding: 5px 11px; font: inherit; font-size: 12px; font-weight: 700;
      cursor: pointer;
    }
    .filters button.on { color: var(--bg); background: var(--yellow); border-color: var(--yellow); }
    .jrow { display: grid; grid-template-columns: 96px 54px 1fr auto; gap: 10px; padding: 9px 0; border-top: 1px solid var(--line); font-size: 13px; align-items: start; }
    .jrow:first-child { border-top: 0; }
    .jrow .jt { color: var(--muted); font-variant-numeric: tabular-nums; font-size: 12px; }
    .jrow .jd { font-weight: 800; }
    .jrow .jr { color: var(--muted); overflow-wrap: anywhere; }
    .jrow .jx { font-size: 11px; font-weight: 800; }
    @media (max-width: 560px) { .jrow { grid-template-columns: 78px 46px 1fr; } .jrow .jx { grid-column: 2 / -1; text-align: left; } }

    /* options lab */
    .demo-banner {
      display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
      background: rgba(255,184,0,0.10); border: 1px dashed rgba(255,184,0,0.55);
      color: var(--yellow); border-radius: 10px; padding: 11px 13px; margin-bottom: 12px;
      font-size: 13px; font-weight: 700;
    }
    .opt-row { display: grid; grid-template-columns: 1fr auto; gap: 10px; padding: 10px 0; border-top: 1px solid var(--line); align-items: center; }
    .opt-row:first-child { border-top: 0; }
    .opt-row .on { font-weight: 800; }
    .opt-row .om { color: var(--muted); font-size: 12px; }

    /* buttons */
    .btn {
      border-radius: 9px; padding: 12px 14px; font: inherit; font-weight: 900;
      letter-spacing: 0.02em; cursor: pointer; border: 1px solid var(--line);
      background: var(--panel2); color: var(--text); text-align: center; width: 100%;
    }
    .btn.long { border-color: rgba(0,255,136,0.6); color: var(--green); background: rgba(0,255,136,0.07); }
    .btn.short { border-color: rgba(255,68,68,0.55); color: var(--red); background: rgba(255,68,68,0.06); }
    .btn.danger { border-color: rgba(255,68,68,0.7); color: var(--red); background: rgba(255,68,68,0.10); }
    .btn:disabled { opacity: 0.4; cursor: not-allowed; }
    .btn.slim { padding: 9px 11px; font-size: 12px; }
    .ops-card {
      display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 12px;
      align-items: center; margin-top: 12px; padding: 12px;
      border: 1px solid var(--line-soft); border-radius: 10px;
      background: rgba(255,255,255,0.018);
    }
    .ops-card b { font-family: var(--font-console); font-size: 13px; letter-spacing: 0.04em; }
    .ops-card p { margin-top: 4px; color: var(--muted); font-size: 12px; line-height: 1.45; overflow-wrap: anywhere; }
    .ops-card .btn { width: auto; min-width: 128px; }
    .ops-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 9px; margin-top: 12px; }
    .ops-grid .btn { min-height: 44px; }
    .ops-secret { margin-top: 12px; }
    .ops-output {
      margin-top: 12px; min-height: 42px; padding: 10px;
      border: 1px solid var(--line-soft); border-radius: 8px;
      background: rgba(0,0,0,0.18); color: var(--muted);
      font-family: var(--font-console); font-size: 11px; line-height: 1.45;
      white-space: pre-wrap; overflow-wrap: anywhere;
    }
    .force-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 9px; margin-top: 6px; }
    .force-meta { color: var(--muted); font-size: 12px; margin-top: 10px; line-height: 1.5; }

    /* modal */
    .modal-wrap { position: fixed; inset: 0; z-index: 60; display: none; align-items: center; justify-content: center; background: rgba(0,0,0,0.66); padding: 18px; }
    .modal-wrap.open { display: flex; }
    .modal { width: min(420px, 100%); background: var(--panel); border: 1px solid var(--line); border-radius: 14px; padding: 18px; }
    .modal.ops { width: min(560px, 100%); }
    .modal h3 { font-size: 17px; margin-bottom: 6px; }
    .modal .mode-tag { font-size: 12px; font-weight: 900; letter-spacing: 0.05em; }
    .modal .kv { margin-top: 12px; }
    .modal input[type=password] { width: 100%; margin-top: 12px; border: 1px solid var(--line); background: var(--panel3); color: var(--text); border-radius: 8px; padding: 11px 12px; font: inherit; }
    .modal .warn { margin-top: 12px; font-size: 12px; color: var(--yellow); line-height: 1.5; }
    .modal .actions { display: grid; grid-template-columns: 1fr 1fr; gap: 9px; margin-top: 14px; }
    .modal .actions .btn { padding: 11px; }
    .modal .ghost { background: transparent; color: var(--muted); }
    .modal .result { margin-top: 10px; font-size: 12px; color: var(--muted); min-height: 16px; overflow-wrap: anywhere; }

    canvas.chart { width: 100%; display: block; margin-top: 8px; border-radius: 6px; background: var(--panel3); }

    /* ── Bottom nav ────────────────────────────────────────────────────── */
    nav.bottom {
      position: fixed; left: 0; right: 0; bottom: 0; z-index: 40;
      height: calc(var(--nav-h) + env(safe-area-inset-bottom));
      padding-bottom: env(safe-area-inset-bottom);
      display: flex; background: rgba(8,10,20,0.98);
      border-top: 1px solid var(--line); backdrop-filter: blur(8px);
    }
    nav.bottom button {
      flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center;
      gap: 2px; background: transparent; border: 0; color: var(--muted);
      font: inherit; font-size: 10px; font-weight: 700; cursor: pointer;
      padding: 6px 2px 7px; position: relative;
    }
    nav.bottom button .ic { font-size: 17px; line-height: 1; }
    nav.bottom button.on { color: var(--text); }
    nav.bottom button.on::after {
      content: ""; position: absolute; top: 0; left: 50%; transform: translateX(-50%);
      width: 26px; height: 3px; border-radius: 0 0 3px 3px; background: var(--yellow);
    }
    @media (max-width: 720px) {
      .live-frame { width: 100%; margin-top: 0; border-left: 0; border-right: 0; border-radius: 0; }
      .mode-tabs { gap: 8px; padding: 14px 12px 10px; }
      .mode-tabs button { height: 48px; border-radius: 12px; font-size: 13px; }
      .appbar { padding: 10px 12px 14px; align-items: flex-start; }
      .brand-title { font-size: 22px; }
      .brand-sub { font-size: 12px; line-height: 1.45; }
      .app-actions { gap: 8px; }
      .live-chip { font-size: 14px; padding: 6px 10px; }
      .refresh-tile { width: 52px; height: 52px; border-radius: 14px; font-size: 24px; }
      .statusbar { padding: 0 8px; }
      .statusbar .seg { padding: 10px 8px; }
      .statusbar .seg b { font-size: 13px; }
      .statusbar .seg span { font-size: 14px; }
    }
    @media (max-width: 560px) {
      .ops-card { grid-template-columns: 1fr; }
      .ops-card .btn { width: 100%; }
      .ops-grid { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <div class="live-frame">
    <div class="mode-tabs">
      <button class="top-tab" data-tab="futures">Futures</button>
      <button class="top-tab" data-tab="options">Options</button>
    </div>
    <header class="appbar">
      <div>
        <div class="brand-title"><span class="afs">AFS</span><span class="slash">/</span>Backend Console</div>
        <div class="brand-sub">Autonomous Futures System · Backend SAT · Paper Mode</div>
      </div>
      <div class="app-actions">
        <span class="live-chip">CONSOLE ONLINE</span>
        <button class="refresh-tile" id="refresh-now" type="button" aria-label="Refresh">↻</button>
      </div>
    </header>
    <div class="statusbar" id="statusbar"></div>
    <div class="alertbar" id="alertbar" hidden></div>
    <div class="monitorbar" id="monitorbar" hidden></div>
    <div class="sandbox-banner">SAT / BACKEND CONSOLE <span class="soft">read-only regression surface · not the operator app</span></div>
    <main>
      <section class="tab active" id="tab-home"></section>
      <section class="tab" id="tab-futures"></section>
      <section class="tab" id="tab-options"></section>
      <section class="tab" id="tab-risk"></section>
      <section class="tab" id="tab-log"></section>
    </main>
  </div>

  <nav class="bottom" id="nav">
    <button data-tab="home" class="on"><span class="ic">▣</span>Home</button>
    <button data-tab="futures"><span class="ic">📈</span>Futures</button>
    <button data-tab="options"><span class="ic">🧪</span>Options Lab</button>
    <button data-tab="risk"><span class="ic">🛡</span>Risk</button>
    <button data-tab="log"><span class="ic">≣</span>Log</button>
  </nav>

  <div class="modal-wrap" id="force-modal">
    <div class="modal">
      <h3 id="fm-title">Confirm</h3>
      <div class="mode-tag" id="fm-mode"></div>
      <dl class="kv" id="fm-kv"></dl>
      <div class="warn" id="fm-warn"></div>
      <input type="password" id="fm-secret" placeholder="Webhook secret (required)" autocomplete="off">
      <div class="actions">
        <button class="btn ghost" id="fm-cancel">Cancel</button>
        <button class="btn" id="fm-confirm">Confirm</button>
      </div>
      <div class="result" id="fm-result"></div>
    </div>
  </div>

  <div class="modal-wrap" id="ops-modal">
    <div class="modal ops">
      <h3>Ops Controls</h3>
      <div class="mode-tag" id="ops-mode"></div>
      <dl class="kv" id="ops-kv"></dl>
      <input class="ops-secret" type="password" id="ops-secret" placeholder="Webhook secret for protected actions" autocomplete="off">
      <div class="ops-grid">
        <button class="btn" data-ops="preflight">Run Preflight</button>
        <button class="btn long" data-ops="arm">Arm Live Today</button>
        <button class="btn danger" data-ops="disarm">Disarm Live</button>
        <button class="btn" data-ops="broker">Broker Status</button>
        <button class="btn" data-ops="diagnostics">Run Diagnostics</button>
        <button class="btn" data-ops="discord">Send Test Discord</button>
      </div>
      <div class="ops-output" id="ops-output">Choose an action.</div>
      <div class="actions">
        <button class="btn ghost" id="ops-close">Close</button>
      </div>
    </div>
  </div>

  <script type="application/json" id="init-data">__INIT_JSON__</script>
  <script>
  (function () {
    "use strict";
    var INIT = JSON.parse(document.getElementById('init-data').textContent);
    var POLL = (INIT.poll_seconds || 30) * 1000;
    var TF_LABEL = (INIT.expected_timeframe_minutes || 15) + 'm';
    // Freshness tolerance keyed off the real decision timeframe: ~2 missed bars
    // + 1m delivery grace (matches the ops monitor, the feed watchdog, and the
    // mobile UI). A hardcoded 6m flagged STALE during normal 15m bar spacing.
    var FRESH_MAX_MIN = (INIT.expected_timeframe_minutes || 15) * 2 + 1;
    var state = { tab: 'home', today: INIT.today || {}, risk: null, filter: 'ALL', history: null, fillRealism: null, lastUpdate: Date.now(), firstLoad: !(INIT.today && INIT.today.date) };

    // ── helpers ────────────────────────────────────────────────────────
    function esc(v) {
      return String(v == null ? '' : v)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;').replace(/'/g, '&#x27;');
    }
    function el(id) { return document.getElementById(id); }
    function num(v) { var n = Number(v); return isFinite(n) ? n : 0; }
    function money(v) { var n = num(v); return (n < 0 ? '-$' : '$') + Math.abs(n).toFixed(2); }
    function hasTradeData(today) { return num(today.trade_count) > 0 || num(today.wins) > 0 || num(today.losses) > 0; }
    function emptyValue(label) { return '<span class="empty-val" title="' + esc(label || 'No data yet') + '">—</span>'; }
    function metricValue(value, emptyLabel) { return value == null ? emptyValue(emptyLabel) : value; }
    function agePhrase(fr) { return fr.state === 'NONE' ? 'never' : (fr.label + ' ago'); }
    function clock(ts) {
      try { return new Date(ts).toLocaleTimeString('en-US', { hour12: false }); }
      catch (e) { return '--:--:--'; }
    }
    function updateAgeText() {
      var node = el('last-update');
      if (!node) return;
      var elapsed = Math.max(0, Math.floor((Date.now() - state.lastUpdate) / 1000));
      var next = Math.max(0, Math.ceil((POLL - (Date.now() - state.lastUpdate)) / 1000));
      node.textContent = 'Last update: ' + clock(state.lastUpdate) + ' · ' + elapsed + 's ago · Next in ' + next + 's';
    }

    function ageMs(iso) {
      if (!iso) return Infinity;
      var t = new Date(iso).getTime();
      if (!isFinite(t)) return Infinity;
      return Date.now() - t;
    }
    function humanAge(ms) {
      if (!isFinite(ms)) return 'never';
      var m = Math.floor(ms / 60000);
      if (m < 1) return 'just now';
      if (m < 60) return m + 'm';
      var h = Math.floor(m / 60);
      if (h < 48) return h + 'h';
      return Math.floor(h / 24) + 'd';
    }
    function etHour() {
      try {
        var s = new Date().toLocaleString('en-US', { timeZone: 'America/New_York', hour12: false, weekday: 'short', hour: '2-digit', minute: '2-digit' });
        // s like "Mon, 14:05"
        var parts = s.replace(',', '').split(' ');
        var wd = parts[0];
        var hm = parts[parts.length - 1].split(':');
        return { wd: wd, h: num(hm[0]), m: num(hm[1]) };
      } catch (e) { return { wd: 'Mon', h: 12, m: 0 }; }
    }
    function inActiveWindow() {
      var t = etHour();
      if (t.wd === 'Sat' || t.wd === 'Sun') return false;
      var mins = t.h * 60 + t.m;
      // RTH 09:30-16:00 ET, plus evening test window 17:00-22:00 ET
      return (mins >= 570 && mins <= 960) || (mins >= 1020 && mins <= 1320);
    }
    function freshness(today) {
      var wh = today.latest_webhook || {};
      var iso = wh.received_at || null;
      var a = ageMs(iso);
      var mins = a / 60000;
      // Prefer the server's one-definition feed window + stale threshold; fall
      // back to the local clock only if the fields are absent.
      var active = (typeof today.feed_window_active === 'boolean') ? today.feed_window_active : inActiveWindow();
      var staleMax = today.feed_stale_after_minutes || FRESH_MAX_MIN;
      var stateName;
      if (!iso) stateName = 'NONE';
      else if (mins <= staleMax) stateName = 'FRESH';
      else if (active) stateName = 'STALE';
      else stateName = 'IDLE';
      return { state: stateName, label: humanAge(a), mins: mins, active: active, iso: iso };
    }
    function isConfig(e) {
      var r = (e.reason || '').toLowerCase();
      return r.indexOf('not in allowed') !== -1 || r.indexOf('not allowed') !== -1;
    }
    function baseRisk(today) {
      var lock = num(today.consecutive_losses) >= num(today.max_consecutive_losses) && num(today.max_consecutive_losses) > 0;
      var full = num(today.trade_count) >= num(today.max_trades_per_day) && num(today.max_trades_per_day) > 0;
      if (lock || full) return 'LOCKED';
      var peak = num(today.account_peak_balance), bal = num(today.account_balance);
      var ddpct = peak > 0 ? (peak - bal) / peak * 100 : 0;
      var maxdd = num(INIT.max_drawdown_pct) || 10;
      if (ddpct >= maxdd) return 'DEFEND';
      if (ddpct >= maxdd * 0.5) return 'WATCH';
      return 'CLEAR';
    }
    var RISK_COLOR = { CLEAR: 'green', WATCH: 'yellow', DEFEND: 'yellow', LOCKED: 'red' };
    var RISK_ICON = { CLEAR: '🟢', WATCH: '🟡', DEFEND: '🟠', LOCKED: '🔴' };
    var RISK_MOOD = {
      CLEAR: 'All systems nominal — normal sizing permitted',
      WATCH: 'Caution — reduce size',
      DEFEND: 'Defensive only — protect capital',
      LOCKED: 'No trades — wait for conditions to improve'
    };
    function moodLine(risk) { return RISK_MOOD[risk] || RISK_MOOD.CLEAR; }
    function modeLabel() { return INIT.paper_mode ? '📄 PAPER MODE' : '⚡ LIVE MODE'; }
    function lockLabel(lock) { return lock ? '🔒 ACTIVE' : '🔓 NONE'; }
    function livePreflight() { return (state.today && state.today.live_preflight) || {}; }
    function preflightStatusLine() {
      var pf = livePreflight();
      if (pf.ready) return ['green', 'ARMED', 'Live preflight passed and armed for today.'];
      if (pf.last_result === true) return ['yellow', 'PASSED · NOT ARMED', 'Preflight passed; live orders remain blocked until armed.'];
      if (pf.last_result === false) return ['red', 'FAILED', pf.reason || pf.disarmed_reason || 'preflight failed'];
      return ['gray', 'NOT RUN', pf.reason || 'preflight required'];
    }
    function sourcePill(kind, label) {
      return '<span class="source-pill ' + esc(kind) + '">' + esc(label) + '</span>';
    }
    function sourceNote(kind, label, detail) {
      return '<div class="source-note">' + sourcePill(kind, label) + '<span>' + esc(detail) + '</span></div>';
    }
    // SAT data contract: keep this list explicit so the preview never implies
    // live-operator parity for data that is not actually pulled into this page.
    function sourceBoundaryPanel() {
      return '<div class="panel accent-yellow"><h2>SAT Data Boundary</h2>' +
        '<div class="source-grid">' +
        '<div class="source-item"><b>Pulled Now</b><span>/status/today, /status/history, /status/risk, per-instrument latest webhook snapshots.</span></div>' +
        '<div class="source-item"><b>Derived Here</b><span>Risk mood, freshness age, empty states, UI grouping, and placeholder sparkline.</span></div>' +
        '<div class="source-item"><b>Not Pulled Yet</b><span>Live operator quote panel, live options chain, and order-entry/bracket controls.</span></div>' +
        '</div></div>';
    }
    function opsCard() {
      var pf = preflightStatusLine();
      return '<div class="panel"><h2>Ops</h2>' +
        '<div class="ops-card">' +
        '<div><b class="' + pf[0] + '">LIVE PREFLIGHT: ' + esc(pf[1]) + '</b>' +
        '<p>' + esc(pf[2]) + '</p></div>' +
        '<button class="btn slim" id="open-ops" type="button">Open Ops</button>' +
        '</div></div>';
    }
    function fillRealismCard() {
      var fill = state.fillRealism;
      if (!fill) {
        return '<div class="panel"><h2>Fill Realism</h2>' +
          '<p class="placeholder" style="margin-top:8px;font-size:12px;">Loading journal-derived fill outcomes…</p></div>';
      }
      var overall = fill.overall || {};
      var windowData = fill.window || {};
      var rate = overall.no_fill_rate_pct;
      var rateText = rate == null ? '—' : num(rate).toFixed(1) + '%';
      var setupRows = (fill.by_setup || []).slice(0, 4).map(function (row) {
        var setupRate = row.no_fill_rate_pct == null ? '—' : num(row.no_fill_rate_pct).toFixed(1) + '%';
        return kv(esc(row.setup), esc(row.no_fills + ' / ' + row.resolved_attempts + ' · ' + setupRate));
      }).join('');
      if (!setupRows) setupRows = kv('Setups', emptyValue('No resolved attempts in this window'));
      return '<div class="panel ' + (num(overall.no_fills) > 0 ? 'accent-yellow' : '') + '">' +
        '<h2>Fill Realism <span class="source-pill pulled">Journal only</span></h2>' +
        '<div class="metric-grid">' +
        '<div class="metric"><label>Actual no-fill rate</label><strong class="' + (num(overall.no_fills) > 0 ? 'yellow' : 'green') + '">' + rateText + '</strong></div>' +
        '<div class="metric"><label>Recent sample</label><strong>' + esc(overall.no_fills || 0) + ' / ' + esc(overall.resolved_attempts || 0) + '</strong></div>' +
        '</div><dl class="kv">' + setupRows + '</dl>' +
        '<p class="muted" style="margin-top:10px;font-size:12px;">' +
        esc(windowData.start_date || '—') + ' → ' + esc(windowData.end_date || '—') +
        ' · ' + esc(windowData.journal_files_found || 0) + ' journal file(s)' +
        ' · ' + esc(windowData.unresolved_attempts || 0) + ' unresolved</p>' +
        '<a class="btn slim" id="fill-realism-details" style="display:inline-block;width:auto;margin-top:12px;text-decoration:none;" href="/status/fill-realism?days=7" target="_blank" rel="noopener">View fill details</a>' +
        '</div>';
    }

    function latestHeadline(today) {
      var entries = today.latest_entries || [];
      for (var i = entries.length - 1; i >= 0; i--) {
        var e = entries[i];
        if ((e.type || 'DECISION') === 'OUTCOME') continue;
        if (isConfig(e)) continue;
        return e;
      }
      return null;
    }
    var GATE_LABELS = {
      SESSION_WINDOW: 'Session window restricted',
      NY_SESSION_WINDOW: 'Outside NY session window',
      MARKET_CONDITION_NOT_TRADABLE: 'Market condition not tradable',
      REGIME_RESTRICTED: 'Regime restricted',
      TREND_STRENGTH: 'Trend strength below required',
      TREND_WEAK: 'Trend strength weak'
    };
    function prettyGate(g) {
      if (GATE_LABELS[g]) return GATE_LABELS[g];
      return String(g).replace(/_/g, ' ').toLowerCase().replace(/^./, function (c) { return c.toUpperCase(); });
    }
    function decisionView(today, fr) {
      var head = latestHeadline(today);
      var decision = head ? (head.decision || 'WAITING') : 'WAITING';
      var reason = head ? (head.reason || '') : 'Awaiting first signal of the session';
      var wh = today.latest_webhook || {};
      var gates = (wh.result && wh.result.failed_gates) || [];
      var blocked = gates.length ? gates.map(prettyGate) : [];
      if (!blocked.length && (decision === 'NO_TRADE' || decision === 'WAITING') && reason) blocked = [reason];
      if (fr.state === 'STALE') blocked = blocked.concat(['Last ' + TF_LABEL + ' webhook stale (' + fr.label + ' old)']);

      var text = (blocked.join(' ') + ' ' + reason).toLowerCase();
      var nv = [];
      if (/trend|strength|weak/.test(text)) nv.push('Trend strength must move WEAK → STRONG');
      if (/regime|restrict|condition|range/.test(text)) nv.push('Regime / market-condition restriction must clear');
      if (/session|window/.test(text)) nv.push('Wait for an allowed session window');
      if (fr.state === 'STALE') nv.unshift('Fresh ' + TF_LABEL + ' bar-close webhook required');
      if (!nv.length && (decision === 'NO_TRADE' || decision === 'WAITING')) nv.push('Conditions for a valid setup must be met');
      return { decision: decision, reason: reason, blocked: blocked, nextValidation: nv };
    }

    function openPositionText(today) {
      var p = today.open_position || null;
      if (!p) return today.has_open_position ? 'OPEN' : 'FLAT';
      return (p.direction || '') + ' ' + (p.instrument || '') + ' @ ' + (p.entry != null ? p.entry : '?');
    }

    // ── status bar ─────────────────────────────────────────────────────
    function renderStatusBar() {
      var today = state.today;
      var fr = freshness(today);
      var risk = baseRisk(today);
      if (risk === 'CLEAR' && fr.state === 'STALE') risk = 'WATCH';
      var lock = num(today.consecutive_losses) >= num(today.max_consecutive_losses) && num(today.max_consecutive_losses) > 0;
      var mode = INIT.paper_mode ? 'PAPER' : 'LIVE';
      var modeC = INIT.paper_mode ? 'blue' : 'red';
      var whTag = fr.state === 'NONE' ? 'gray' : (fr.state === 'FRESH' ? 'green' : (fr.state === 'IDLE' ? 'gray' : 'yellow'));
      var segs = [
        ['BACKEND:', 'ONLINE', 'green'],
        ['MODE:', mode, modeC],
        ['BROKER:', (INIT.broker || 'PAPER'), 'blue'],
        ['RISK:', risk, RISK_COLOR[risk] || 'gray'],
        ['LOCKOUT:', lock ? 'ACTIVE' : 'NONE', lock ? 'red' : 'green'],
        ['DATA:', fr.state === 'NONE' ? 'NONE' : fr.state, whTag],
        ['POLL:', (POLL / 1000) + 's', 'gray']
      ];
      el('statusbar').innerHTML = segs.map(function (s) {
        return '<div class="seg"><b>' + esc(s[0]) + '</b><span class="' + s[2] + '">' + esc(s[1]) + '</span></div>';
      }).join('');
    }

    // ── freshness widget (shared Home + Futures) ───────────────────────
    function freshWidget(fr) {
      var map = { FRESH: ['green', 'FRESH'], STALE: ['yellow', 'STALE'], IDLE: ['gray', 'IDLE'], NONE: ['gray', 'NO DATA'] };
      var m = map[fr.state] || map.NONE;
      var sub = fr.state === 'NONE' ? '⏳ Waiting for first webhook...' :
        ('Last ' + TF_LABEL + ' webhook: ' + fr.label + ' ago · Expected: every ' + TF_LABEL);
      return '<div class="panel ' + (fr.state === 'STALE' ? 'accent-yellow' : '') + '">' +
        '<h2>Data Freshness</h2>' +
        '<div class="freshbar"><span class="tag ' + m[0] + '">' + m[1] + '</span>' +
        '<span class="muted">' + esc(sub) + '</span></div></div>';
    }

    // ── HOME ───────────────────────────────────────────────────────────
    function renderHome() {
      var today = state.today;
      var fr = freshness(today);
      var risk = baseRisk(today);
      if (risk === 'CLEAR' && fr.state === 'STALE') risk = 'WATCH';
      var dv = decisionView(today, fr);
      var pnl = num(today.today_pnl_dollars);
      var lock = num(today.consecutive_losses) >= num(today.max_consecutive_losses) && num(today.max_consecutive_losses) > 0;
      var clearLabel = risk === 'CLEAR' && !lock ? '✅ CLEAR TO TRADE' : ((RISK_ICON[risk] || '•') + ' ' + risk);
      var traded = hasTradeData(today);
      var pnlDisplay = traded ? money(pnl) : emptyValue('No trades yet');
      var winRateDisplay = traded ? (num(today.win_rate).toFixed(1) + '%') : emptyValue('Win rate appears after at least 1 trade');
      var tradesDisplay = num(today.trade_count) + ' / ' + num(today.max_trades_per_day);
      var html = '';
      if (state.firstLoad) {
        html += '<div class="hero"><div class="hero-top"><h1>Backend Console</h1><div class="badges"><span class="badge gray">Loading</span></div></div>' +
          '<div class="metric-grid"><div class="metric"><div class="skeleton line"></div></div><div class="metric"><div class="skeleton short"></div></div></div></div>';
      }
      html += '<div class="hero">' +
        '<div class="hero-top"><h1>Backend Console</h1><div class="badges">' +
        '<span class="badge green">🟢 CONSOLE ONLINE</span>' +
        '<span class="badge ' + (RISK_COLOR[risk] || 'gray') + '">' + esc(clearLabel) + '</span>' +
        '<span class="badge blue">' + modeLabel() + '</span>' +
        '</div></div>' +
        '<p class="mood">' + esc(moodLine(risk)) + '</p>' +
        '<div class="update-line" id="last-update"></div>' +
        '</div>';
      html += sourceBoundaryPanel();
      html += opsCard();
      html += fillRealismCard();

      html += '<div class="tabhead"><h1>Today</h1><span class="when">' + esc(today.date || '') + '</span></div>';

      html += '<div class="panel">' +
        '<h2>Decision</h2>' +
        '<div class="decision d-' + esc(dv.decision) + '"><span class="dot"></span>' + esc(dv.decision) + '</div>' +
        '<div class="metric-grid">' +
        '<div class="metric"><label>💰 Today P&amp;L</label><strong class="' + (!traded ? 'gray' : (pnl < 0 ? 'red' : 'green')) + '">' + pnlDisplay + '</strong><span class="sparkline">▁▂▃▅▇█▇▅▃▂▁</span></div>' +
        '<div class="metric"><label>📊 Trades Used</label><strong>' + tradesDisplay + '</strong></div>' +
        '<div class="metric"><label>🏁 Win Rate</label><strong>' + winRateDisplay + '</strong></div>' +
        '</div>' +
        '<dl class="kv">' +
        kv('Risk state', '<span class="' + (RISK_COLOR[risk] || 'gray') + '">' + risk + '</span>') +
        kv('Open position', esc(openPositionText(today))) +
        kv('Last webhook', esc(agePhrase(fr))) +
        '</dl>' +
        '<p class="muted" style="margin-top:10px;font-size:13px;">Reason: ' + esc(dv.reason || '—') + '</p>' +
        '</div>';

      html += freshWidget(fr);

      if (dv.decision === 'NO_TRADE' || dv.decision === 'WAITING') {
        html += card('Why No Trade?', listReasons(dv.blocked, '!', 'yellow'), dv.blocked.length ? 'accent-yellow' : '');
        html += card('Next Required Validation', listReasons(dv.nextValidation, '→', 'blue'), '');
      }

      html += compactPnl(today);
      el('tab-home').innerHTML = html;
      var opsBtn = el('open-ops');
      if (opsBtn) opsBtn.addEventListener('click', openOpsModal);
      updateAgeText();
    }
    function kv(k, v) { return '<dt>' + esc(k) + '</dt><dd>' + v + '</dd>'; }
    function card(title, body, cls) {
      return '<div class="panel ' + (cls || '') + '"><h2>' + esc(title) + '</h2>' + body + '</div>';
    }
    function listReasons(items, mark, color) {
      if (!items || !items.length) return '<p class="muted" style="margin-top:8px;font-size:13px;">None.</p>';
      return '<ul class="reasons">' + items.map(function (r, i) {
        return '<li><span class="mk ' + color + '">' + mark + '</span><span>' + (i + 1) + '. ' + esc(r) + '</span></li>';
      }).join('') + '</ul>';
    }
    function compactPnl(today) {
      var hist = state.history && state.history.days ? state.history.days : [];
      var anyData = hist.some(function (d) { return num(d.realized_pnl_dollars) !== 0; }) || num(today.today_pnl_dollars) !== 0;
      var pnl7 = hist.reduce(function (a, d) { return a + num(d.realized_pnl_dollars); }, 0);
      if (!anyData) {
        return '<div class="panel"><h2>P&L</h2><dl class="kv">' +
          kv('P&L today', hasTradeData(today) ? money(today.today_pnl_dollars) : emptyValue('No trades yet')) +
          kv('7D P&L', pnl7 ? money(pnl7) : emptyValue('No realized P&L yet')) +
          '</dl><p class="placeholder" style="margin-top:8px;font-size:12px;">No realized P&L yet — chart hidden until there is history.</p></div>';
      }
      return '<div class="panel"><h2>Equity Curve <span class="muted" id="chart-range" style="font-size:11px;font-weight:400;"></span></h2>' +
        '<canvas id="pnl-chart" class="chart" style="height:140px;"></canvas></div>';
    }

    // ── FUTURES ────────────────────────────────────────────────────────
    function freshnessForWebhook(wh) {
      var st = state.today || {};
      var iso = wh && wh.received_at ? wh.received_at : null;
      var a = ageMs(iso);
      var mins = a / 60000;
      // Same server-driven window/threshold as freshness(today), via state.today.
      var active = (typeof st.feed_window_active === 'boolean') ? st.feed_window_active : inActiveWindow();
      var staleMax = st.feed_stale_after_minutes || FRESH_MAX_MIN;
      var stateName;
      if (!iso) stateName = 'NONE';
      else if (mins <= staleMax) stateName = 'FRESH';
      else if (active) stateName = 'STALE';
      else stateName = 'IDLE';
      return { state: stateName, label: humanAge(a), mins: mins, active: active, iso: iso };
    }
    function instrumentAccent(decision, fr) {
      if (fr.state === 'STALE') return 'yellow';
      if (decision === 'TRADE') return 'green';
      if (decision === 'WAITING' || decision === 'NO_TRADE') return 'yellow';
      return 'red';
    }
    function instrumentStatus(fr) {
      if (fr.state === 'FRESH') return '<span class="pill green">🟢 FRESH</span>';
      if (fr.state === 'STALE') return '<span class="pill yellow">🟡 STALE</span>';
      if (fr.state === 'IDLE') return '<span class="pill gray">⚪ IDLE</span>';
      return '<span class="pill gray">⚪ WAITING</span>';
    }
    function instrumentCard(inst, today) {
      var webhooks = today.latest_webhooks || {};
      var wh = webhooks[inst] || {};
      var ctx = wh.context || {};
      var result = wh.result || {};
      var hasPayload = !!(wh.payload || wh.context || wh.result);
      var fr = freshnessForWebhook(wh);
      var decision = hasPayload ? (result.decision || 'WAITING') : 'WAITING';
      var failed = (result.failed_gates || []).map(prettyGate);
      if (!failed.length && !hasPayload) failed = ['No ' + inst + ' alert received yet today'];
      if (fr.state === 'STALE') failed = failed.concat(['Last ' + TF_LABEL + ' webhook stale (' + fr.label + ' old)']);
      var price = ctx.close != null ? ctx.close : '—';
      var session = ctx.session || '—';
      var vwap = ctx.vwap || {};
      var orb = ctx.orb || {};
      var vol = ctx.volume || {};
      var condition = ctx.market_condition || '—';
      var meta = fr.state === 'NONE' ? 'waiting for alert' : (fr.label + ' since last bar');
      var html = '<div class="panel instrument-card accent-' + instrumentAccent(decision, fr) + '">' +
        '<div class="instrument-head"><div><h2>' + esc(inst) + '</h2><div class="instrument-meta">' + esc(meta) + '</div></div>' +
        instrumentStatus(fr) + '</div>' +
        '<div class="decision instrument-decision d-' + esc(decision) + '"><span class="dot"></span>' + esc(decision) + '</div>' +
        '<dl class="kv">' +
        kv('Last close', esc(price)) +
        kv('Session', esc(session)) +
        kv('Timeframe', TF_LABEL) +
        kv('Last webhook', esc(fr.label) + (fr.state === 'NONE' ? '' : ' ago')) +
        '</dl>' +
        '<div class="ctx mini-context">' +
        ctxItem('VWAP', vwap.value != null ? vwap.value : '—') +
        ctxItem('ORB High', orb.high != null ? orb.high : '—') +
        ctxItem('ORB Low', orb.low != null ? orb.low : '—') +
        ctxItem('Condition', condition) +
        ctxItem('Volume', vol.current_bar != null ? vol.current_bar : '—') +
        '</div>';
      if (failed.length) {
        html += '<div class="instrument-reasons">' + listReasons(failed, '!', 'yellow') + '</div>';
      }
      html += hasPayload ?
        sourceNote('pulled', 'Pulled', '/status/today latest_webhooks.' + inst + ' · result + context snapshot') :
        sourceNote('waiting', 'Waiting', 'No ' + inst + ' webhook snapshot has been recorded for this SAT session');
      return html + '</div>';
    }
    function futuresOverview(fr) {
      var map = { FRESH: ['green', 'FRESH'], STALE: ['yellow', 'STALE'], IDLE: ['gray', 'IDLE'], NONE: ['gray', 'NO DATA'] };
      var m = map[fr.state] || map.NONE;
      var sub = fr.state === 'NONE' ? 'Waiting for first webhook snapshot.' :
        ('Last ' + TF_LABEL + ' webhook: ' + fr.label + ' ago · Expected every ' + TF_LABEL);
      return '<div class="panel">' +
        '<h2>Futures Intake</h2>' +
        '<div class="futures-overview">' +
        '<div class="overview-cell">' +
        '<div class="overview-label">Data Freshness</div>' +
        '<div class="overview-value ' + m[0] + '">' + esc(m[1]) + '</div>' +
        '<div class="overview-sub">' + esc(sub) + '</div>' +
        '</div>' +
        '<div class="overview-cell">' +
        '<div class="overview-label">SAT Boundary</div>' +
        '<div class="overview-value">' + sourcePill('derived', 'Webhook Snapshots') + '</div>' +
        '<div class="overview-sub">MES/MNQ cards show pulled per-instrument webhook result + context when present. Blanks are not live quote-panel data.</div>' +
        '</div>' +
        '</div></div>';
    }
    function renderFutures() {
      var today = state.today;
      var fr = freshness(today);
      var html = '';
      html += '<div class="tabhead"><h1>Futures</h1><span class="when">' + esc(agePhrase(fr)) + '</span></div>';

      html += '<div class="futures-layout"><div class="futures-main">';
      html += futuresOverview(fr);
      html += '<div class="instrument-grid">' +
        instrumentCard('MES', today) +
        instrumentCard('MNQ', today) +
        '</div>';
      html += '</div><aside class="futures-rail">';
      html += renderUniverse();
      html += renderForce();
      html += '</aside></div>';
      el('tab-futures').innerHTML = html;
      wireForce();
    }
    function ctxItem(label, val) {
      return '<div class="c"><label>' + esc(label) + '</label><strong>' + esc(val) + '</strong></div>';
    }
    function renderUniverse() {
      var u = INIT.universe || [];
      var body = '<div class="uni">' + u.map(function (i) {
        var on = i.enabled;
        // A required instrument that is NOT enabled is a CONFIG ERROR (red),
        // not a benign DISABLED (gray).
        var configErr = i.required && !on;
        var cls = on ? 'green' : (configErr ? 'red' : 'gray');
        var label = on ? 'ENABLED' : (configErr ? 'CONFIG ERROR' : 'DISABLED');
        return '<div class="u"><b>' + esc(i.sym) + '</b><span class="pill ' + cls + '">' + label + '</span></div>';
      }).join('') + '</div>';
      return card('Allowed Futures', body, '');
    }

    // ── force-open controls ────────────────────────────────────────────
    var POINTS = { MES: { stop: 7, target: 15, dollar: 5 }, MNQ: { stop: 7, target: 15, dollar: 2 }, MGC: { stop: 5, target: 10, dollar: 10 }, MCL: { stop: 0.3, target: 0.6, dollar: 100 } };
    function modeWord() { return INIT.live_trading_enabled && !INIT.paper_mode ? 'LIVE' : 'PAPER'; }
    function renderForce() {
      return '<div class="panel accent-yellow"><h2>Manual Execution — Disabled</h2>' +
        '<div class="force-meta">Entries only run through the risk-gated alert pipeline. ' +
        'Emergency flattening is handled directly in Tradovate, which remains the broker source of truth.</div></div>';
    }
    function wireForce() {
      var btns = document.querySelectorAll('[data-force]');
      Array.prototype.forEach.call(btns, function (b) {
        b.addEventListener('click', function () { openForceModal(b.getAttribute('data-force')); });
      });
    }
    var pendingForce = null;
    function openForceModal(spec) {
      var parts = spec.split('|');
      var sym = parts[0], side = parts[1];
      var mode = modeWord();
      var live = mode === 'LIVE';
      el('fm-mode').innerHTML = '<span class="' + (live ? 'red' : 'blue') + '">MODE: ' + mode + '</span>';
      // CLOSE_ALL is the only manual action — force-OPEN was removed.
      if (side !== 'CLOSE') return;
      el('fm-title').textContent = 'Close all ' + mode.toLowerCase() + ' positions?';
      el('fm-kv').innerHTML = kv('Action', 'CLOSE ALL') + kv('Mode', mode) + kv('Scope', 'Cancel working orders + flatten');
      el('fm-warn').textContent = live
        ? 'LIVE MODE — this cancels real working orders and flattens a real broker position. This cannot be undone.'
        : 'Paper mode — cancels working orders and flattens the simulated position.';
      pendingForce = { action: 'CLOSE_ALL' };
      el('fm-confirm').className = 'btn danger';
      el('fm-confirm').textContent = 'CONFIRM CLOSE';
      el('fm-secret').value = '';
      el('fm-result').textContent = '';
      el('force-modal').classList.add('open');
    }
    function closeForceModal() { el('force-modal').classList.remove('open'); pendingForce = null; }
    function submitForce() {
      if (!pendingForce) return;
      var secret = el('fm-secret').value.trim();
      if (!secret) { el('fm-result').textContent = 'Webhook secret is required.'; return; }
      el('fm-confirm').disabled = true;
      el('fm-result').textContent = 'Sending…';
      fetch('/webhook/manual', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Webhook-Secret': secret },
        body: JSON.stringify(pendingForce)
      }).then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
        .then(function (res) {
          var d = res.d || {};
          if (!res.ok || d.ok === false) el('fm-result').innerHTML = '<span class="red">' + esc(d.error || d.detail || 'Request rejected.') + '</span>';
          else { el('fm-result').innerHTML = '<span class="green">' + esc(d.note || 'Request accepted.') + '</span>'; }
        }).catch(function () { el('fm-result').innerHTML = '<span class="red">Failed before reaching the server.</span>'; })
        .then(function () { el('fm-confirm').disabled = false; });
    }

    function openOpsModal() {
      var pf = preflightStatusLine();
      var raw = livePreflight();
      el('ops-mode').innerHTML = '<span class="' + pf[0] + '">LIVE PREFLIGHT: ' + esc(pf[1]) + '</span>';
      el('ops-kv').innerHTML =
        kv('Broker', esc(INIT.broker || 'PAPER')) +
        kv('Mode', esc(modeWord())) +
        kv('Reason', esc(raw.reason || raw.disarmed_reason || '—')) +
        kv('Last preflight', esc(raw.last_preflight_at || 'never'));
      el('ops-output').textContent = 'Choose an action.';
      el('ops-modal').classList.add('open');
    }
    function closeOpsModal() { el('ops-modal').classList.remove('open'); }
    function opsSecretRequired() {
      var secret = el('ops-secret').value.trim();
      if (!secret) {
        el('ops-output').innerHTML = '<span class="yellow">Webhook secret required for this action.</span>';
        return null;
      }
      return secret;
    }
    function opsPrint(label, data, ok) {
      var text = label + '\n' + JSON.stringify(data, null, 2);
      el('ops-output').innerHTML = '<span class="' + (ok === false ? 'red' : 'green') + '">' + esc(text) + '</span>';
    }
    function opsFetch(path, opts, label) {
      el('ops-output').textContent = 'Working…';
      return fetch(path, opts || { cache: 'no-store' })
        .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
        .then(function (res) { opsPrint(label, res.d, res.ok && res.d.ok !== false); return res; })
        .catch(function (err) { opsPrint(label, { error: String(err) }, false); });
    }
    function protectedPost(path, body, label) {
      var secret = opsSecretRequired();
      if (!secret) return;
      return opsFetch(path, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Webhook-Secret': secret },
        body: JSON.stringify(body || {})
      }, label).then(function () { refresh(); });
    }
    function wireOps() {
      var btns = document.querySelectorAll('[data-ops]');
      Array.prototype.forEach.call(btns, function (btn) {
        btn.addEventListener('click', function () {
          var action = btn.getAttribute('data-ops');
          if (action === 'preflight') return protectedPost('/admin/live-preflight/run', {}, 'Preflight');
          if (action === 'arm') return protectedPost('/admin/live-preflight/arm', {}, 'Arm Live Today');
          if (action === 'disarm') return protectedPost('/admin/live-preflight/disarm', { reason: 'manual_dashboard' }, 'Disarm Live');
          if (action === 'discord') return protectedPost('/admin/test-discord', {}, 'Discord Test');
          if (action === 'broker') return opsFetch('/status/broker-account', { cache: 'no-store' }, 'Broker Status');
          if (action === 'diagnostics') return opsFetch('/status/diagnostics', { cache: 'no-store' }, 'Diagnostics');
        });
      });
    }

    // ── OPTIONS LAB (demo) ─────────────────────────────────────────────
    var DEMO_OPTIONS = [
      { sym: 'NVDA', type: 'CALL', strike: 88, score: 'A' },
      { sym: 'AAPL', type: 'CALL', strike: 72, score: 'B' },
      { sym: 'AMD', type: 'PUT', strike: 61, score: 'B' },
      { sym: 'TSLA', type: 'CALL', strike: 240, score: 'C' }
    ];
    function renderOptions() {
      var html = '<div class="tabhead"><h1>Options Lab</h1><span class="when">demo</span></div>';
      html += '<div class="demo-banner">🧪 OPTIONS LAB · DEMO DATA · Scanner not live yet</div>';
      var rows = DEMO_OPTIONS.map(function (o) {
        return '<div class="opt-row">' +
          '<div><span class="on">' + esc(o.sym) + ' ' + esc(o.type) + ' ' + esc(o.strike) + '</span>' +
          '<div class="om">Confluence grade ' + esc(o.score) + ' · simulated ranking</div></div>' +
          '<span class="pill yellow">SIMULATED</span></div>';
      }).join('');
      html += '<div class="panel"><h2>Simulated Alerts</h2>' + rows + '</div>';
      html += '<div class="panel"><h2>About</h2><p class="muted" style="font-size:13px;line-height:1.6;">' +
        'These rows are placeholder/demo output. No live options scanner is connected, and nothing here is sent to any broker. ' +
        'Every row is labelled <b>SIMULATED</b> so it can never be mistaken for a live futures decision.</p></div>';
      el('tab-options').innerHTML = html;
    }

    // ── RISK ───────────────────────────────────────────────────────────
    function renderRisk() {
      var today = state.today;
      var fr = freshness(today);
      var r = state.risk;
      var html = '<div class="tabhead"><h1>Risk</h1><span class="when">' + esc(today.date || '') + '</span></div>';

      var base = baseRisk(today);
      var shown = base;
      if (base === 'CLEAR' && fr.state === 'STALE') shown = 'WATCH';
      html += '<div class="panel accent-' + (RISK_COLOR[shown] === 'red' ? 'red' : (RISK_COLOR[shown] === 'yellow' ? 'yellow' : 'green')) + '">' +
        '<h2>Risk Ladder</h2>' +
        '<div class="decision ' + RISK_COLOR[shown] + '" style="font-size:24px;">' + (RISK_ICON[shown] || '•') + ' ' + shown + '</div>' +
        '<p class="mood">' + esc(moodLine(shown)) + '</p>';
      if (shown !== base) html += '<p class="muted" style="margin-top:8px;font-size:12px;">OPS: WATCH — base state ' + base + ', raised to WATCH because DATA FRESHNESS: STALE.</p>';
      html += ladder(shown) + '</div>';

      if (r) {
        html += '<div class="panel"><h2>Limits</h2><dl class="kv">' +
          kv('Max daily loss', money(r.max_daily_loss)) +
          kv('Daily loss used', money(r.daily_loss_used)) +
          kv('Drawdown', num(r.drawdown_pct).toFixed(2) + '% (' + esc(r.drawdown_state) + ')') +
          kv('Trades', num(today.trade_count) + ' / ' + num(r.max_trades)) +
          kv('Consecutive losses', num(r.consecutive_losses) + ' / ' + num(r.max_consecutive_losses)) +
          '</dl></div>';
        html += '<div class="panel"><h2>Session &amp; News</h2><dl class="kv">' +
          kv('News blackout', r.news_blackout ? '<span class="red">ACTIVE</span>' : '<span class="green">NONE</span>') +
          (r.news_blackout_reason ? kv('Reason', esc(r.news_blackout_reason)) : '') +
          kv('In active window', fr.active ? '<span class="green">YES</span>' : '<span class="gray">NO</span>') +
          '</dl></div>';
      } else {
        html += '<div class="panel"><h2>Limits</h2><p class="muted">Loading…</p></div>';
      }

      html += renderCommittee(fr);
      el('tab-risk').innerHTML = html;
    }
    function ladder(shown) {
      var steps = ['CLEAR', 'WATCH', 'DEFEND', 'LOCKED'];
      return '<div class="risk-ladder">' + steps.map(function (s) {
        var active = s === shown;
        var c = RISK_COLOR[s];
        var icon = active ? (RISK_ICON[s] || '●') : '○';
        return '<div class="risk-step ' + (active ? ('active ' + c) : 'dim') + ' ' + (active ? c : 'gray') + '">' + icon + ' ' + s + '</div>';
      }).join('') + '</div>';
    }
    var AGENT_MAP = [
      ['payload_auditor', 'Payload'],
      ['risk_steward', 'Risk'],
      ['strategy_analyst', 'Strategy'],
      ['ops_monitor', 'Ops']
    ];
    var STATUS_WORD = { OK: ['PASS', 'green'], WARNING: ['WARN', 'yellow'], CRITICAL: ['FAIL', 'red'] };
    function renderCommittee(fr) {
      var c = INIT.committee || {};
      var agents = c.agents || [];
      var byName = {};
      agents.forEach(function (a) { byName[a.agent] = a; });
      var anyWarn = false, anyCrit = false;
      var rows = AGENT_MAP.map(function (m) {
        var a = byName[m[0]];
        var st = a ? a.status : null;
        var note = '';
        // Ops freshness override
        if (m[0] === 'ops_monitor' && fr.state === 'STALE') { st = 'WARNING'; note = 'TradingView feed stale (' + fr.label + ' old) — data freshness, not an ops-agent failure'; }
        if (!st) { st = 'NONE'; }
        var sw = STATUS_WORD[st] || ['—', 'gray'];
        if (st === 'WARNING') anyWarn = true;
        if (st === 'CRITICAL') anyCrit = true;
        if (!note && a && a.recommendations && a.recommendations.length) note = (a.recommendations[0].reason || '').slice(0, 90);
        var line = '<span class="nm">' + esc(m[1]) + '</span><span class="' + sw[1] + '" style="font-weight:800;">' + sw[0] + '</span><span></span>';
        if (note) line += '<span class="note">' + esc(note) + '</span>';
        return line;
      }).join('');

      var consensus, cc, blocker = '';
      var suff = c.sample_sufficiency || '';
      if (!c.overall_status) { consensus = 'NOT RUN'; cc = 'gray'; blocker = 'Committee has not run yet — call /status/adaptive.'; }
      else if (fr.state === 'STALE') { consensus = 'WITHHELD'; cc = 'yellow'; blocker = 'TradingView feed stale → consensus withheld (data freshness during active window).'; }
      else if (suff === 'insufficient_sample') { consensus = 'INSUFFICIENT'; cc = 'yellow'; blocker = 'Insufficient resolved-trade sample (' + num(c.sample_size) + ' trades) to form consensus.'; }
      else if (anyCrit) { consensus = 'BLOCKED'; cc = 'red'; blocker = 'A committee agent reported CRITICAL.'; }
      else if (anyWarn) { consensus = 'PARTIAL'; cc = 'yellow'; blocker = 'At least one agent reported WARNING.'; }
      else { consensus = 'OK'; cc = 'green'; }

      var body = '<div class="cmt">' + rows + '</div>' +
        '<div class="consensus">Consensus: <span class="' + cc + '">' + consensus + '</span></div>' +
        (blocker ? '<p class="muted" style="margin-top:6px;font-size:12px;">' + esc(blocker) + '</p>' : '');
      return '<div class="panel"><h2>Adaptive Committee</h2>' + body + '</div>';
    }

    // ── LOG ────────────────────────────────────────────────────────────
    function categoryOf(e) {
      if ((e.type || '') === 'OUTCOME' || e.outcome) return e.outcome || 'OUTCOME';
      if (isConfig(e)) return 'CONFIG_BLOCK';
      return e.decision || e.type || 'EVENT';
    }
    var FILTER_MATCH = {
      ALL: function () { return true; },
      TRADE: function (c) { return c === 'TRADE'; },
      NO_TRADE: function (c) { return c === 'NO_TRADE'; },
      RISK_REJECTED: function (c) { return c === 'RISK_REJECTED'; },
      CONFIG: function (c) { return c === 'CONFIG_BLOCK'; },
      ERROR: function (c) { return c === 'ERROR'; },
      WIN: function (c) { return c === 'WIN'; },
      LOSS: function (c) { return c === 'LOSS'; }
    };
    function compress(entries) {
      // collapse consecutive same (instrument, category) runs
      var groups = [];
      entries.forEach(function (e) {
        var cat = categoryOf(e);
        var inst = (e.instrument || '—').toUpperCase();
        var last = groups[groups.length - 1];
        if (last && last.cat === cat && last.inst === inst && (last.reason === (e.reason || ''))) {
          last.count++; last.lastTs = e.ts; last.items.push(e);
        } else {
          groups.push({ cat: cat, inst: inst, reason: e.reason || '', firstTs: e.ts, lastTs: e.ts, count: 1, items: [e], outcome: e });
        }
      });
      return groups;
    }
    function shortT(iso) {
      if (!iso) return '';
      try {
        return new Date(iso).toLocaleTimeString('en-US', { timeZone: 'America/New_York', hour: 'numeric', minute: '2-digit', hour12: true });
      } catch (e) { return String(iso).split('T')[1] ? String(iso).split('T')[1].slice(0, 5) : ''; }
    }
    var CAT_COLOR = { TRADE: 'green', WIN: 'green', NO_TRADE: 'yellow', WAITING: 'yellow', CONFIG_BLOCK: 'gray', RISK_REJECTED: 'red', LOSS: 'red', ERROR: 'red', BREAKEVEN: 'blue' };
    function renderLog() {
      var today = state.today;
      var entries = (today.latest_entries || []).slice();
      var filters = ['ALL', 'TRADE', 'NO_TRADE', 'RISK_REJECTED', 'CONFIG', 'ERROR', 'WIN', 'LOSS'];
      var html = '<div class="tabhead"><h1>Log</h1><span class="when">' + entries.length + ' recent</span></div>';
      html += '<div class="filters">' + filters.map(function (f) {
        return '<button data-filter="' + f + '" class="' + (state.filter === f ? 'on' : '') + '">' + f.replace('_', ' ') + '</button>';
      }).join('') + '</div>';

      var match = FILTER_MATCH[state.filter] || FILTER_MATCH.ALL;
      var groups = compress(entries).filter(function (g) { return match(g.cat); });
      var body = '';
      if (!groups.length) body = '<p class="muted" style="font-size:13px;">No entries for this filter.</p>';
      else {
        // newest first
        groups.reverse().forEach(function (g) {
          var label = g.cat === 'CONFIG_BLOCK' ? 'CONFIG_BLOCK' : g.cat;
          var cnt = g.count > 1 ? ' <span class="muted">x' + g.count + '</span>' : '';
          var trange = g.count > 1 ? (shortT(g.firstTs) + '–' + shortT(g.lastTs)) : shortT(g.firstTs);
          var color = CAT_COLOR[g.cat] || 'gray';
          var reason = g.reason;
          if (g.cat === 'CONFIG_BLOCK') reason = (g.inst + ' not in allowed universe');
          var px = '';
          if (g.outcome && g.outcome.pnl_dollars != null) px = '<span class="jx ' + (num(g.outcome.pnl_dollars) < 0 ? 'red' : 'green') + '">' + money(g.outcome.pnl_dollars) + '</span>';
          body += '<div class="jrow">' +
            '<span class="jt">' + esc(trange) + '</span>' +
            '<span class="jd ' + color + '">' + esc(g.inst) + '</span>' +
            '<span class="jr"><b class="' + color + '">' + esc(label) + cnt + '</b><br>' + esc(reason || '—') + '</span>' +
            px + '</div>';
        });
      }
      html += '<div class="panel"><h2>Journal (compressed)</h2>' + body + '</div>';
      el('tab-log').innerHTML = html;
      Array.prototype.forEach.call(document.querySelectorAll('[data-filter]'), function (b) {
        b.addEventListener('click', function () { state.filter = b.getAttribute('data-filter'); renderLog(); });
      });
    }

    // ── charts (only drawn when present) ───────────────────────────────
    function drawPnlChart() {
      var canvas = el('pnl-chart');
      var hist = state.history;
      if (!canvas || !hist || !hist.days || !hist.days.length) return;
      var days = hist.days.slice().reverse();
      var dpr = window.devicePixelRatio || 1;
      var W = canvas.offsetWidth, H = canvas.offsetHeight;
      if (!W || !H) return;
      canvas.width = W * dpr; canvas.height = H * dpr;
      var ctx = canvas.getContext('2d'); ctx.scale(dpr, dpr);
      var css = getComputedStyle(document.documentElement);
      var chartBg = css.getPropertyValue('--panel3').trim() || '#0d1020';
      var chartLine = css.getPropertyValue('--blue').trim() || '#00d5ff';
      var chartGreen = css.getPropertyValue('--green').trim() || '#00FF88';
      var chartRed = css.getPropertyValue('--red').trim() || '#FF4444';
      var chartGray = css.getPropertyValue('--gray').trim() || '#8b90a6';
      var PAD = { t: 14, r: 14, b: 22, l: 48 };
      var cw = W - PAD.l - PAD.r, ch = H - PAD.t - PAD.b;
      var vals = days.map(function (d) { return num(d.realized_pnl_dollars); });
      var mx = Math.max(0, Math.max.apply(null, vals)), mn = Math.min(0, Math.min.apply(null, vals));
      var range = (mx - mn) || 1;
      var toX = function (i) { return PAD.l + (i / Math.max(days.length - 1, 1)) * cw; };
      var toY = function (v) { return PAD.t + ((mx - v) / range) * ch; };
      ctx.fillStyle = chartBg; ctx.fillRect(0, 0, W, H);
      ctx.strokeStyle = 'rgba(141,148,179,0.30)'; ctx.setLineDash([4, 4]);
      ctx.beginPath(); ctx.moveTo(PAD.l, toY(0)); ctx.lineTo(W - PAD.r, toY(0)); ctx.stroke(); ctx.setLineDash([]);
      ctx.beginPath(); ctx.strokeStyle = chartLine; ctx.lineWidth = 2;
      days.forEach(function (d, i) { var x = toX(i), y = toY(num(d.realized_pnl_dollars)); i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y); });
      ctx.stroke();
      days.forEach(function (d, i) { var v = num(d.realized_pnl_dollars); ctx.beginPath(); ctx.arc(toX(i), toY(v), 3, 0, 7); ctx.fillStyle = v > 0 ? chartGreen : v < 0 ? chartRed : chartGray; ctx.fill(); });
      var rl = el('chart-range');
      if (rl) { var last = vals[vals.length - 1] || 0; rl.textContent = days.length + 'd · ' + (last >= 0 ? '+' : '') + money(last).replace('$', '$'); }
    }

    // ── tab switching + render dispatch ────────────────────────────────
    function renderActive() {
      if (state.tab === 'home') renderHome();
      else if (state.tab === 'futures') renderFutures();
      else if (state.tab === 'options') renderOptions();
      else if (state.tab === 'risk') renderRisk();
      else if (state.tab === 'log') renderLog();
      if (state.tab === 'home') drawPnlChart();
    }
    function setTab(name) {
      state.tab = name;
      Array.prototype.forEach.call(document.querySelectorAll('.tab'), function (s) { s.classList.toggle('active', s.id === 'tab-' + name); });
      Array.prototype.forEach.call(document.querySelectorAll('[data-tab]'), function (b) { b.classList.toggle('on', b.getAttribute('data-tab') === name); });
      if (name === 'risk' && !state.risk) loadRisk();
      renderActive();
      window.scrollTo(0, 0);
    }

    // ── data ───────────────────────────────────────────────────────────
    function loadRisk() {
      return fetch('/status/risk', { cache: 'no-store' }).then(function (r) { return r.json(); }).then(function (d) { state.risk = d; if (state.tab === 'risk') renderRisk(); }).catch(function () {});
    }
    function refresh() {
      Promise.all([
        fetch('/status/today', { cache: 'no-store' }).then(function (r) { return r.json(); }),
        fetch('/status/history?days=7', { cache: 'no-store' }).then(function (r) { return r.json(); }),
        fetch('/status/fill-realism?days=7', { cache: 'no-store' }).then(function (r) { return r.json(); })
      ]).then(function (res) {
        state.today = res[0] || state.today;
        state.history = res[1] || state.history;
        state.fillRealism = res[2] || state.fillRealism;
        state.lastUpdate = Date.now();
        state.firstLoad = false;
        renderStatusBar();
        renderAlertBar();
        renderActive();
      }).catch(function (e) { /* keep last good state */ });
    }

    // ── wire ───────────────────────────────────────────────────────────
    Array.prototype.forEach.call(document.querySelectorAll('[data-tab]'), function (b) {
      b.addEventListener('click', function () { setTab(b.getAttribute('data-tab')); });
    });
    el('refresh-now').addEventListener('click', refresh);
    el('fm-cancel').addEventListener('click', closeForceModal);
    el('fm-confirm').addEventListener('click', submitForce);
    el('force-modal').addEventListener('click', function (e) { if (e.target === el('force-modal')) closeForceModal(); });
    el('ops-close').addEventListener('click', closeOpsModal);
    el('ops-modal').addEventListener('click', function (e) { if (e.target === el('ops-modal')) closeOpsModal(); });
    wireOps();

    function renderMonitorBar() {
      var bar = el('monitorbar');
      if (!bar) return;
      if (INIT.manual_controls_enabled) { bar.hidden = true; return; }
      bar.hidden = false;
      bar.innerHTML = '<b>MODE: MONITOR ONLY</b> · Manual execution controls disabled · ' +
        'Broker actions hidden until the alert pipeline is validated';
    }

    // Loud red banner for a misconfigured live alert: a required instrument
    // missing from the universe (CONFIG ERROR), or a wrong-timeframe webhook
    // (LIVE ALERT MISCONFIGURED). Reads polled state so it clears on its own
    // once the next correct webhook arrives.
    function renderAlertBar() {
      var bar = el('alertbar');
      if (!bar) return;
      var msgs = [];
      var missing = INIT.universe_missing || [];
      if (missing.length) {
        msgs.push('<b>CONFIG ERROR</b>' +
          '<span class="sub">Required instrument(s) not in allowed universe: ' +
          esc(missing.join(', ')) + ' · fix risk_rules.yaml + restart</span>');
      }
      var av = (state.today && state.today.alert_validation) || INIT.alert_validation;
      if (av && av.ok === false && av.issue === 'TIMEFRAME_MISMATCH') {
        msgs.push('<b>LIVE ALERT MISCONFIGURED</b>' +
          '<span class="sub">Expected: ' + esc(av.expected || (INIT.expected_timeframe_minutes + 'm')) +
          ' · Received: ' + esc(av.received || '?') +
          ' · Recreate TradingView alert on ' + esc(av.expected || (INIT.expected_timeframe_minutes + 'm')) +
          ' chart' + (av.count ? ' (' + av.count + ' today)' : '') + '</span>');
      }
      if (!msgs.length) { bar.hidden = true; bar.innerHTML = ''; return; }
      bar.hidden = false;
      bar.innerHTML = msgs.join('<hr style="border:none;border-top:1px solid rgba(255,61,113,0.3);margin:8px 0;">');
    }

    renderMonitorBar();
    renderAlertBar();
    renderStatusBar();
    renderActive();
    updateAgeText();
    // initial history fetch for compact-pnl / chart decisions
    fetch('/status/history?days=7', { cache: 'no-store' }).then(function (r) { return r.json(); }).then(function (d) { state.history = d; state.firstLoad = false; renderActive(); }).catch(function () {});
    fetch('/status/fill-realism?days=7', { cache: 'no-store' }).then(function (r) { return r.json(); }).then(function (d) { state.fillRealism = d; renderActive(); }).catch(function () {});
    setInterval(function () { renderStatusBar(); }, 1000 * 20);
    setInterval(updateAgeText, 1000);
    setInterval(refresh, POLL);
    window.addEventListener('resize', function () { if (state.tab === 'home') drawPnlChart(); });
  })();
  </script>
</body>
</html>"""


def _render_dashboard(status: dict) -> str:
    """Render the operator dashboard.

    All tabs are rendered client-side from the embedded JSON view-model, so the
    30s refresh reuses one render path and the server holds no presentation
    logic. `<` is escaped to keep any reason text from breaking out of the
    JSON <script> island.
    """
    init_json = json.dumps(_dashboard_init(status), default=str).replace("<", "\\u003c")
    return _DASHBOARD_HTML.replace("__INIT_JSON__", init_json)


def _instrument_root_of(ticker: str | None) -> str | None:
    """Map a TradingView ticker (MES1!, CME_MINI:MNQ1!, …) to MES/MNQ, else None.

    Exact root + contract-suffix matching (shared with the ingest filter) so a
    stock sharing a substring never buckets into a futures side.
    """
    return futures_root(ticker, ("MES", "MNQ"))


def _latest_webhook_inst_path(inst: str) -> Path:
    return Path(_config.log_dir) / f"latest_webhook_{inst}.json"


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
    # Also keep the latest webhook per instrument so the dashboard can show a
    # dedicated MES side and MNQ side instead of one global slot that flips
    # MES↔MNQ on every bar.
    root = _instrument_root_of(getattr(payload, "ticker", None))
    if root:
        atomic_write_text(
            _latest_webhook_inst_path(root),
            json.dumps(data, indent=2, sort_keys=True),
        )


def _latest_webhooks_by_instrument() -> dict:
    empty = {"received_at": None, "payload": None, "context": None, "result": None}
    out: dict = {}
    for inst in ("MES", "MNQ"):
        path = _latest_webhook_inst_path(inst)
        if not path.exists():
            out[inst] = dict(empty)
            continue
        try:
            out[inst] = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            out[inst] = dict(empty)
    return out


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


def _broker_status() -> dict:
    """Return broker connection and open position status."""
    import os as _os
    broker_type = _os.getenv("BROKER", "paper").strip().lower()
    if broker_type == "tradovate":
        try:
            from execution.tradovate_broker import TradovateBroker, TradovateConfig
            broker = TradovateBroker(config=TradovateConfig.from_env())
            authenticated = broker._authenticate()
            pos = broker.get_position() if authenticated else None
            balance = broker.get_account_balance() if authenticated else None
            return {
                "broker": f"tradovate-{broker.config.env}",
                "connected": authenticated,
                "account_balance": balance,
                "position": {
                    "instrument": pos.instrument,
                    "direction": pos.direction,
                    "qty": pos.quantity,
                    "entry": pos.entry_price,
                } if pos else None,
            }
        except Exception as exc:
            return {"broker": "tradovate", "connected": False, "error": str(exc)}
    return {"broker": "paper", "connected": True, "position": None}



def _manual_close_all() -> dict:
    """Cancel all orders and flatten position. Logs forced close to journal."""
    import os as _os
    from datetime import date as _date
    broker_type = _os.getenv("BROKER", "paper").strip().lower()
    result = {"action": "CLOSE_ALL", "broker": broker_type}

    if broker_type == "tradovate":
        try:
            from execution.tradovate_broker import TradovateBroker, TradovateConfig
            broker = TradovateBroker(config=TradovateConfig.from_env())
            flatten = broker.flatten_position()
            result["ok"] = True
            result["position_was"] = flatten.get("position_was")
            result["cancelled_orders"] = flatten.get("cancelled_orders", False)
            result["close_sent"] = flatten.get("close_sent", False)
            result["note"] = (
                "Tradovate position liquidated + orders cancelled."
                if flatten.get("close_sent")
                else "No open position — orders cancelled only."
            )
            if "error" in flatten:
                result["close_error"] = flatten["error"]
        except Exception as exc:
            return {**result, "ok": False, "error": str(exc)}
    else:
        result["ok"] = True
        result["note"] = "Paper mode — no live orders to cancel."

    # Clear open-position flag in journal without polluting P&L / streak stats.
    # Use result="CANCELLED" so _compute_daily_state clears has_open_position
    # without adding to consecutive_losses or win_rate denominator.
    try:
        journal = JournalLogger(log_dir=_config.log_dir)
        open_pos = journal.get_open_position(_date.today())
        if open_pos:
            journal.log_outcome(
                instrument=open_pos.get("instrument") or "UNKNOWN",
                session=open_pos.get("session") or "manual",
                result="CANCELLED",
                entry_price=float(open_pos.get("entry") or 0.0),
                exit_price=0.0,
                exit_reason="MANUAL_CLOSE_ALL",
                pnl_ticks=0.0,
                pnl_dollars=0.0,
                contracts=int(open_pos.get("contracts") or 1),
                for_date=_date.today(),
            )
    except Exception as exc:
        logger.warning("Manual close journal log failed: %s", exc)

    return result


def _escape(value: object) -> str:
    text = "" if value is None else str(value)
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#x27;")
    )
