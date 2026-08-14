"""
webhook/viewer.py — sanitized read-only LIVE viewer tier.

A separate, revocable public share surface (``/viewer/*``) so the live demo
dashboard state can be shared WITHOUT exposing balances, account IDs, raw webhook
payloads, config, or any secret. Auth is its own ``VIEWER_TOKEN`` (distinct from
the operator ``SITE_ACCESS_CODE`` gate, and from ``WEBHOOK_SECRET``). Every value
is explicitly selected, then passed through a recursive redaction net as
defense-in-depth. This module contains NO trading / risk / execution / broker
logic — it only reads already-computed, sanitized state.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from datetime import date
from typing import Any, Optional
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

router = APIRouter()

_VIEWER_COOKIE = "vp_viewer"
_COOKIE_MAX_AGE = 30 * 24 * 3600  # 30 days

# Defense-in-depth net: drop any dict key whose lowercased name contains one of
# these. Applied AFTER explicit field selection (the primary control). Chosen to
# never collide with the legitimate viewer fields (mode, trades, win_rate,
# direction, entry, stop, target, rr, strategy, decision, reason, session, ...).
_REDACT_SUBSTRINGS = (
    "api_key", "secret", "token", "password", "authorization", "bearer",
    "cookie", "account_id", "broker_account_id", "account_balance",
    "account_peak", "latest_webhook", "payload", "context", "journal_path",
    "config_raw", "debug", "stack", "traceback",
)


def _app():
    """Lazy import to avoid a circular import (app imports this module)."""
    import webhook.app as app_module
    return app_module


# ── config / flags ────────────────────────────────────────────────────────────
def _viewer_token() -> str:
    return os.getenv("VIEWER_TOKEN", "").strip()


def _viewer_enabled() -> bool:
    return bool(_viewer_token())


def _flag(name: str, default: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw not in ("0", "false", "no", "off")


def _magic_link_enabled() -> bool:
    return _flag("VIEWER_MAGIC_LINK", True)


def _show_candidate_prices() -> bool:
    return _flag("VIEWER_SHOW_CANDIDATE_PRICES", True)


def _viewer_cookie_token() -> str:
    """Opaque cookie value bound to WEBHOOK_SECRET + VIEWER_TOKEN. Rotating
    VIEWER_TOKEN invalidates every viewer session; the raw token is never stored
    in the cookie."""
    key = _app()._configured_webhook_secret().encode()
    msg = ("viewer-access:" + _viewer_token()).encode()
    return hmac.new(key, msg, hashlib.sha256).hexdigest()


def _is_https(request: Request) -> bool:
    xf = request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower()
    return xf == "https" or request.url.scheme == "https"


# ── auth ──────────────────────────────────────────────────────────────────────
def _is_viewer_authed(request: Request) -> bool:
    cookie = request.cookies.get(_VIEWER_COOKIE)
    if cookie and hmac.compare_digest(cookie, _viewer_cookie_token()):
        return True
    raw = _viewer_token()
    if not raw:
        return False
    header = request.headers.get("x-viewer-token")
    if header and hmac.compare_digest(header, raw):
        return True
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        tok = auth.split(" ", 1)[1].strip()
        if tok and hmac.compare_digest(tok, raw):
            return True
    return False


def require_viewer(request: Request) -> None:
    """FastAPI dependency: 401 unless the request carries a valid viewer cookie /
    header / bearer. Fail-closed when VIEWER_TOKEN is unset."""
    if not _viewer_enabled():
        raise HTTPException(status_code=401, detail="Viewer is not configured.")
    if not _is_viewer_authed(request):
        raise HTTPException(status_code=401, detail="Viewer access required.")


def _set_viewer_cookie(resp, request: Request) -> None:
    resp.set_cookie(
        _VIEWER_COOKIE, _viewer_cookie_token(),
        max_age=_COOKIE_MAX_AGE, httponly=True, samesite="lax",
        secure=_is_https(request), path="/viewer",
    )


# ── redaction net ─────────────────────────────────────────────────────────────
def redact(obj: Any) -> Any:
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            kl = str(k).lower()
            if any(s in kl for s in _REDACT_SUBSTRINGS):
                continue
            out[k] = redact(v)
        return out
    if isinstance(obj, (list, tuple)):
        return [redact(v) for v in obj]
    return obj


# ── JSON endpoints (all GET, all require the viewer token, all redacted) ──────
@router.get("/viewer/api/status")
async def viewer_status(request: Request, _: None = Depends(require_viewer)):
    return JSONResponse(redact(await _app().status_public()))


@router.get("/viewer/api/dashboard")
async def viewer_dashboard(request: Request, _: None = Depends(require_viewer)):
    app_module = _app()
    p = app_module._dashboard_payload(date.today())
    mode_info = app_module._execution_mode_info()
    latest = p.get("latest_webhook") or {}
    age_seconds = app_module._latest_webhook_age_seconds(latest)
    stale_after_seconds = int(p.get("feed_stale_after_minutes") or 0) * 60
    feed_active = bool(p.get("feed_window_active"))
    if age_seconds is None:
        market_data_state = "NONE"
    elif stale_after_seconds and age_seconds <= stale_after_seconds:
        market_data_state = "FRESH"
    elif feed_active:
        market_data_state = "STALE"
    else:
        market_data_state = "IDLE"
    safe = {
        "mode": mode_info["key"],
        "execution_mode_label": mode_info["label"],
        "market_data_state": market_data_state,
        "market_data_age_seconds": age_seconds,
        "today_pnl": round(float(p.get("today_pnl_dollars") or 0), 2),
        "trades": p.get("trade_count", 0),
        "max_trades": p.get("max_trades_per_day", 0),
        "wins": p.get("wins", 0),
        "losses": p.get("losses", 0),
        "win_rate": p.get("win_rate", 0),
        "has_open_position": bool(p.get("has_open_position")),
        "feed_window_active": bool(p.get("feed_window_active")),
        "top_no_trade_reasons": p.get("top_no_trade_reasons", []),
    }
    return JSONResponse(redact(safe))


@router.get("/viewer/api/risk")
async def viewer_risk(request: Request, _: None = Depends(require_viewer)):
    r = await _app().status_risk()
    max_dl = float(r.get("max_daily_loss") or 0)
    used = float(r.get("daily_loss_used") or 0)
    usage_pct = round((used / max_dl) * 100, 1) if max_dl else 0.0
    safe = {
        "max_trades": r.get("max_trades", 0),
        "consecutive_losses": r.get("consecutive_losses", 0),
        "max_consecutive_losses": r.get("max_consecutive_losses", 0),
        "drawdown_state": r.get("drawdown_state", "NORMAL"),
        "max_daily_loss_usage_pct": usage_pct,
        "news_blackout": bool(r.get("news_blackout")),
    }
    return JSONResponse(redact(safe))


def _read_today_entries() -> list:
    from journal.journal_logger import JournalLogger
    journal = JournalLogger(log_dir=_app()._config.log_dir)
    path = journal._journal_path(date.today())
    return journal._read_entries(path) if path.exists() else []


@router.get("/viewer/api/decisions")
async def viewer_decisions(
    request: Request, limit: int = Query(20, ge=1, le=100), _: None = Depends(require_viewer)
):
    entries = _read_today_entries()
    rows = [_app()._public_entry(e) for e in entries[-limit:]]
    return JSONResponse(redact({"decisions": rows}))


@router.get("/viewer/api/latest-decision")
async def viewer_latest_decision(request: Request, _: None = Depends(require_viewer)):
    decisions = [e for e in _read_today_entries() if e.get("type") != "OUTCOME"]
    if not decisions:
        return JSONResponse(redact({"available": False}))
    e = decisions[-1]
    ts = str(e.get("ts") or "")
    instrument = e.get("instrument")
    trace_id = hashlib.sha1(f"{ts}:{instrument}".encode()).hexdigest()[:12]

    cand_src = e.get("candidate") or (e.get("setup") or None)
    candidate = None
    candidate_status = "NONE"
    if cand_src:
        candidate_status = "PRESENT"
        if _show_candidate_prices():
            candidate = {
                "direction": cand_src.get("direction"),
                "entry": cand_src.get("entry"),
                "stop": cand_src.get("stop"),
                "target": cand_src.get("target"),
                "rr": cand_src.get("rr") if cand_src.get("rr") is not None else cand_src.get("rr_ratio"),
            }
        else:
            candidate = {"direction": cand_src.get("direction")}

    out = {
        "available": True,
        "trace_id": trace_id,
        "timestamp": ts,
        "instrument": instrument,
        "final_decision": e.get("decision"),
        "primary_reason": e.get("reason"),
        "candidate_status": candidate_status,
        "candidate": candidate,
        "execution": "DISABLED",
        "no_trade_taken": e.get("decision") != "TRADE",
    }
    return JSONResponse(redact(out))


# ── HTML + magic-link handoff ─────────────────────────────────────────────────
@router.get("/viewer", response_class=HTMLResponse)
async def viewer_page(request: Request, key: Optional[str] = Query(None)):
    if not _viewer_enabled():
        return HTMLResponse(_disabled_html(), status_code=503)
    if key is not None and _magic_link_enabled():
        if hmac.compare_digest(key, _viewer_token()):
            resp = RedirectResponse(url="/viewer", status_code=302)
            _set_viewer_cookie(resp, request)
            return resp
        return HTMLResponse(_login_html(error=True), status_code=401)
    if not _is_viewer_authed(request):
        return HTMLResponse(_login_html(error=False), status_code=401)
    return HTMLResponse(_viewer_html())


@router.post("/viewer/enter")
async def viewer_enter(request: Request):
    if not _viewer_enabled():
        return JSONResponse({"detail": "Viewer is not configured."}, status_code=503)
    # Parse the raw body ourselves (urlencoded form or JSON) so we don't depend
    # on python-multipart just to read one field.
    raw = (await request.body()).decode("utf-8", "ignore")
    token = ""
    parsed = parse_qs(raw)
    if "token" in parsed:
        token = (parsed["token"][0] or "").strip()
    if not token:
        try:
            token = str(json.loads(raw).get("token", "")).strip()
        except Exception:  # noqa: BLE001 — not JSON
            token = ""
    if token and hmac.compare_digest(token, _viewer_token()):
        resp = RedirectResponse(url="/viewer", status_code=302)
        _set_viewer_cookie(resp, request)
        return resp
    return HTMLResponse(_login_html(error=True), status_code=401)


# ── minimal read-only HTML (data loaded client-side via textContent = XSS-safe) ─
_CSS = """
*{box-sizing:border-box;margin:0;padding:0}
body{background:#070706;color:#E9E4DA;font-family:Inter,system-ui,sans-serif;padding:18px;max-width:560px;margin:0 auto}
.banner{background:#141312;border:1px solid #35312A;border-radius:10px;padding:12px 14px;margin-bottom:14px}
.banner b{color:#F0B85A;letter-spacing:.5px}
.muted{color:#8A8478;font-size:12px}
.strip{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:14px}
.cell{background:#141312;border:1px solid #35312A;border-radius:8px;padding:8px 10px;font-size:12px;flex:1;min-width:120px}
.cell .k{color:#8A8478;font-size:10px;letter-spacing:.5px}
.cell .v{font-size:15px;font-weight:600}
.card{background:#141312;border:1px solid #35312A;border-radius:10px;padding:14px;margin-bottom:12px}
.card h3{font-size:11px;color:#8A8478;letter-spacing:.5px;margin-bottom:10px;text-transform:uppercase}
.row{display:flex;justify-content:space-between;padding:5px 0;font-size:13px;border-bottom:1px solid #211E19}
.row:last-child{border-bottom:none}
.good{color:#7FB069}.bad{color:#D9685F}.gold{color:#F0B85A}
"""

_VIEWER_BODY = """
<div class="banner"><b>READ-ONLY LIVE VIEW</b><div class="muted">Real demo dashboard data · Controls disabled</div></div>
<div class="strip">
  <div class="cell"><div class="k">MARKET DATA</div><div class="v muted" id="market">Loading</div></div>
  <div class="cell"><div class="k">EXECUTION MODE</div><div class="v" id="mode">—</div></div>
  <div class="cell"><div class="k">ORDERS</div><div class="v bad">Disabled</div></div>
  <div class="cell"><div class="k">ACCESS</div><div class="v">Read-only</div></div>
</div>
<div class="card"><h3>Today</h3>
  <div class="row"><span>P&amp;L</span><span id="pnl">—</span></div>
  <div class="row"><span>Trades</span><span id="trades">—</span></div>
  <div class="row"><span>Open position</span><span id="open">—</span></div>
</div>
<div class="card"><h3>Risk</h3>
  <div class="row"><span>Risk state</span><span id="risk_state">—</span></div>
  <div class="row"><span>Daily loss usage</span><span id="loss_usage">—</span></div>
  <div class="row"><span>Consecutive losses</span><span id="losses">—</span></div>
  <div class="row"><span>News blackout</span><span id="blackout">—</span></div>
</div>
<div class="card"><h3>Latest decision</h3>
  <div class="row"><span>Instrument</span><span id="d_inst">—</span></div>
  <div class="row"><span>Decision</span><span id="d_dec">—</span></div>
  <div class="row"><span>Reason</span><span id="d_reason" style="text-align:right;max-width:60%">—</span></div>
  <div class="row"><span>Candidate</span><span id="d_cand">—</span></div>
</div>
<div class="muted" id="updated">Loading…</div>
<script>
function t(id,v){var el=document.getElementById(id);if(el)el.textContent=(v==null?'—':v);}
function stateText(state,age){
  if(state==='FRESH')return 'Fresh'+(age==null?'':(' · '+Math.floor(age/60)+'m old'));
  if(state==='STALE')return 'Stale'+(age==null?'':(' · '+Math.floor(age/60)+'m old'));
  if(state==='IDLE')return 'Idle / outside feed window';
  return 'No data';
}
function stateClass(state){return state==='FRESH'?'good':(state==='STALE'?'bad':(state==='IDLE'?'gold':'muted'));}
async function poll(){
  try{
    var responses=await Promise.all([
      fetch('/viewer/api/dashboard',{credentials:'same-origin'}),
      fetch('/viewer/api/risk',{credentials:'same-origin'}),
      fetch('/viewer/api/latest-decision',{credentials:'same-origin'})
    ]);
    var d=await responses[0].json();
    var r=await responses[1].json();
    var x=await responses[2].json();
    var market=document.getElementById('market');
    t('market',stateText(d.market_data_state,d.market_data_age_seconds));
    market.className='v '+stateClass(d.market_data_state);
    t('mode',d.execution_mode_label);
    var p=d.today_pnl; t('pnl',(p>=0?'+':'')+'$'+p);
    t('trades',d.trades+' / '+d.max_trades);
    t('open',d.has_open_position?'Yes':'Flat');
    t('risk_state',r.drawdown_state||'UNKNOWN');
    t('loss_usage',(r.max_daily_loss_usage_pct||0)+'%');
    t('losses',(r.consecutive_losses||0)+' / '+(r.max_consecutive_losses||0));
    t('blackout',r.news_blackout?'ACTIVE':'None');
    if(x && x.available){
      t('d_inst',x.instrument); t('d_dec',x.final_decision); t('d_reason',x.primary_reason);
      if(x.candidate_status==='PRESENT' && x.candidate){
        var c=x.candidate; t('d_cand',(c.direction||'')+' '+(c.entry!=null?c.entry:'')+(c.stop!=null?(' / stop '+c.stop):'')+(c.target!=null?(' / tgt '+c.target):'')+(c.rr!=null?(' · R '+c.rr):''));
      } else { t('d_cand','none'); }
    } else { t('d_inst','—'); t('d_dec','no decisions yet'); t('d_reason','—'); t('d_cand','—'); }
    t('updated','Updated '+new Date().toLocaleTimeString());
  }catch(e){ t('updated','Update failed — retrying'); }
}
poll(); setInterval(poll, 30000);
</script>
"""

_LOGIN_BODY = """
<div class="banner"><b>READ-ONLY LIVE VIEW</b><div class="muted">Enter the viewer access token to continue.</div></div>
<div class="card">
  <form method="post" action="/viewer/enter">
    <input name="token" type="password" placeholder="viewer token" autofocus
      style="width:100%;padding:10px;background:#0E0D0C;border:1px solid #35312A;border-radius:8px;color:#E9E4DA;margin-bottom:10px"/>
    <button type="submit" style="width:100%;padding:10px;background:#F0B85A;border:none;border-radius:8px;color:#1A1206;font-weight:600">Enter</button>
    %ERROR%
  </form>
</div>
"""


def _page(body: str) -> str:
    return (
        "<!DOCTYPE html><html lang=\"en\"><head><meta charset=\"UTF-8\"/>"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\"/>"
        "<title>Vantage Point · Live View</title><style>" + _CSS + "</style></head><body>"
        + body + "</body></html>"
    )


def _viewer_html() -> str:
    return _page(_VIEWER_BODY)


def _login_html(error: bool) -> str:
    err = '<div class="bad" style="margin-top:8px;font-size:12px">Invalid token.</div>' if error else ""
    return _page(_LOGIN_BODY.replace("%ERROR%", err))


def _disabled_html() -> str:
    return _page('<div class="banner"><b>Viewer not configured</b><div class="muted">VIEWER_TOKEN is not set on this server.</div></div>')
