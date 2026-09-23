"""
ops/push_relay/app.py — Web Push relay for app.afsvp.com.

A small standalone FastAPI service (its own systemd unit, its own venv) that:

1. Serves the VAPID public key and stores browser push subscriptions
   (`/push/vapid-public-key`, `/push/subscribe`, `/push/unsubscribe`,
   `/push/test`, `/push/health`).
2. Polls the futures-bot read-only status endpoints (default every 60s) and
   pushes a notification when something the operator cares about changes:
   position opened / closed, a trade resolved, the daily loss lock, and the
   backend going down / coming back.

It never talks to a broker, never writes to the bot, and never imports bot
code — it is a pure observer sitting next to the watcher. Secrets (VAPID
private key) and subscriptions live under ``PUSH_RELAY_STATE_DIR`` on the
box, never in this repo.

Run:  uvicorn ops.push_relay.app:app --host 127.0.0.1 --port 8020
Env:  PUSH_RELAY_STATE_DIR   (default /root/afs-shared/push-relay)
      PUSH_RELAY_STATUS_BASE (default http://127.0.0.1:8000)
      PUSH_RELAY_POLL_SEC    (default 60)
      PUSH_RELAY_VAPID_SUBJECT (default mailto:ops@afsvp.com)
      PUSH_RELAY_WATCH       (default 1; set 0 to disable the poller)
      PUSH_RELAY_DAILY_ET    (default 16:15; HH:MM America/New_York for the
                              daily close summary; empty to disable)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from typing import Any, Mapping, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger("push_relay")

STATE_DIR = Path(os.environ.get("PUSH_RELAY_STATE_DIR", "/root/afs-shared/push-relay"))
STATUS_BASE = os.environ.get("PUSH_RELAY_STATUS_BASE", "http://127.0.0.1:8000").rstrip("/")
POLL_SEC = max(15, int(os.environ.get("PUSH_RELAY_POLL_SEC", "60") or "60"))
VAPID_SUBJECT = os.environ.get("PUSH_RELAY_VAPID_SUBJECT", "mailto:ops@afsvp.com")
WATCH = os.environ.get("PUSH_RELAY_WATCH", "1") not in ("0", "false", "no", "off")
DAILY_ET = (os.environ.get("PUSH_RELAY_DAILY_ET", "16:15") or "").strip()
ET = ZoneInfo("America/New_York")
OFFLINE_AFTER_FAILURES = 3
MAX_SUBSCRIPTIONS = 50


# ─── Subscription store ──────────────────────────────────────────────────────

class SubscriptionStore:
    """endpoint → {subscription, ua, added}. Atomic JSON file, tiny."""

    def __init__(self, path: Path):
        self.path = path
        self._rows: dict[str, dict[str, Any]] = {}
        self.load()

    def load(self) -> None:
        try:
            raw = json.loads(self.path.read_text())
            self._rows = {k: v for k, v in raw.items() if isinstance(v, dict) and "subscription" in v}
        except (FileNotFoundError, ValueError):
            self._rows = {}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._rows, indent=1, sort_keys=True))
        os.chmod(tmp, 0o600)
        tmp.replace(self.path)

    def add(self, subscription: Mapping[str, Any], ua: str = "") -> bool:
        endpoint = str(subscription.get("endpoint", "") or "")
        keys = subscription.get("keys") or {}
        if not endpoint.startswith("https://") or not keys.get("p256dh") or not keys.get("auth"):
            return False
        if endpoint not in self._rows and len(self._rows) >= MAX_SUBSCRIPTIONS:
            return False
        self._rows[endpoint] = {
            "subscription": {"endpoint": endpoint, "keys": {"p256dh": keys["p256dh"], "auth": keys["auth"]}},
            "ua": str(ua or "")[:200],
            "added": self._rows.get(endpoint, {}).get("added") or time.time(),
        }
        self.save()
        return True

    def remove(self, endpoint: str) -> bool:
        if endpoint in self._rows:
            del self._rows[endpoint]
            self.save()
            return True
        return False

    def get(self, endpoint: str) -> Optional[dict[str, Any]]:
        row = self._rows.get(endpoint)
        return row["subscription"] if row else None

    def all(self) -> list[dict[str, Any]]:
        return [row["subscription"] for row in self._rows.values()]

    def __len__(self) -> int:
        return len(self._rows)


# ─── VAPID keys ──────────────────────────────────────────────────────────────

def load_or_create_vapid(state_dir: Path) -> tuple[str, str]:
    """Returns (private_key_pem, public_key_b64url). Generated once per box."""
    path = state_dir / "vapid.json"
    try:
        data = json.loads(path.read_text())
        return data["private_pem"], data["public_b64"]
    except (FileNotFoundError, ValueError, KeyError):
        pass
    from py_vapid import Vapid  # dependency of pywebpush

    v = Vapid()
    v.generate_keys()
    import base64

    from cryptography.hazmat.primitives import serialization

    private_pem = v.private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    raw_pub = v.public_key.public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    public_b64 = base64.urlsafe_b64encode(raw_pub).rstrip(b"=").decode()
    state_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"private_pem": private_pem, "public_b64": public_b64}))
    os.chmod(path, 0o600)
    return private_pem, public_b64


# ─── Event detection (pure) ──────────────────────────────────────────────────

@dataclass(frozen=True)
class Event:
    title: str
    body: str
    tag: str
    url: str = "/"


def _money(value: Any) -> str:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return "—"
    sign = "+" if f > 0 else "-" if f < 0 else ""
    return f"{sign}${abs(f):,.2f}"


def signature(today: Mapping[str, Any] | None) -> dict[str, Any]:
    """The handful of fields whose change means 'tell the operator'."""
    if not isinstance(today, dict):
        return {}
    pos = today.get("open_position") if isinstance(today.get("open_position"), dict) else None
    return {
        "has_open_position": bool(today.get("has_open_position")),
        "position": (
            {
                "instrument": pos.get("instrument"),
                "direction": pos.get("direction"),
                "entry": pos.get("entry_price", pos.get("entry")),
            }
            if pos
            else None
        ),
        "trade_count": int(today.get("trade_count") or 0),
        "wins": int(today.get("wins") or 0),
        "losses": int(today.get("losses") or 0),
        "realized": today.get("realized_pnl_dollars"),
        "consecutive_losses": int(today.get("consecutive_losses") or 0),
        "max_consecutive_losses": int(today.get("max_consecutive_losses") or 0),
        "date": today.get("date"),
    }


def diff_events(prev: Mapping[str, Any] | None, curr: Mapping[str, Any]) -> list[Event]:
    """Events between two signatures. First observation (prev None/empty) → none."""
    if not prev or not curr:
        return []
    if prev.get("date") != curr.get("date"):
        return []  # new trading day: counters reset, nothing to announce
    events: list[Event] = []

    p_pos, c_pos = prev.get("position"), curr.get("position")
    if c_pos and not p_pos:
        events.append(Event(
            title="Position opened",
            body=f"{c_pos.get('instrument')} {c_pos.get('direction')} @ {c_pos.get('entry')}",
            tag="position",
            url="/futures",
        ))
    elif p_pos and not c_pos:
        d_w = curr.get("wins", 0) - prev.get("wins", 0)
        d_l = curr.get("losses", 0) - prev.get("losses", 0)
        outcome = "WIN" if d_w > 0 and d_l == 0 else "LOSS" if d_l > 0 and d_w == 0 else "closed"
        try:
            delta = float(curr.get("realized") or 0) - float(prev.get("realized") or 0)
            pnl = f" {_money(delta)}"
        except (TypeError, ValueError):
            pnl = ""
        events.append(Event(
            title=f"Position closed · {outcome}",
            body=f"{p_pos.get('instrument')} {p_pos.get('direction')}{pnl} · day {_money(curr.get('realized'))}",
            tag="position",
            url="/futures",
        ))
    else:
        d_w = curr.get("wins", 0) - prev.get("wins", 0)
        d_l = curr.get("losses", 0) - prev.get("losses", 0)
        if d_w > 0 or d_l > 0:
            events.append(Event(
                title="Trade resolved",
                body=f"+{d_w}W / +{d_l}L · day {_money(curr.get('realized'))} · {curr.get('wins')}W-{curr.get('losses')}L",
                tag="trade",
                url="/journal",
            ))

    mx = curr.get("max_consecutive_losses") or 0
    if 0 < mx < 9999 and curr.get("consecutive_losses", 0) >= mx > prev.get("consecutive_losses", 0):
        events.append(Event(
            title="Daily lock",
            body=f"{curr.get('consecutive_losses')} consecutive losses — trading locked for today",
            tag="lock",
            url="/risk",
        ))
    return events


# ─── Daily close summary (pure) ──────────────────────────────────────────────

def daily_summary(today: Mapping[str, Any]) -> Event:
    """One line for the day: trades, W/L, P&L, top blocker, open position."""
    trades = int(today.get("trade_count") or 0)
    wins = int(today.get("wins") or 0)
    losses = int(today.get("losses") or 0)
    pnl = _money(today.get("realized_pnl_dollars"))
    parts = [f"{trades} trade{'s' if trades != 1 else ''}"]
    if trades:
        parts.append(f"{wins}W-{losses}L")
    parts.append(f"P&L {pnl}")
    reasons = today.get("top_no_trade_reasons")
    if not trades and isinstance(reasons, list) and reasons:
        top = reasons[0] if isinstance(reasons[0], dict) else {}
        reason = str(top.get("reason") or "").strip()
        if reason:
            parts.append(f"top block: {reason[:60]}")
    pos = today.get("open_position") if isinstance(today.get("open_position"), dict) else None
    if pos:
        parts.append(f"open {pos.get('instrument')} {pos.get('direction')}")
    return Event(title=f"Close · {today.get('date') or 'today'}", body=" · ".join(parts), tag="daily", url="/journal")


def daily_due(now_et: datetime, last_sent_date: str | None, at: str = DAILY_ET) -> bool:
    """True once per ET calendar day, on/after `at` (HH:MM), weekdays only."""
    if not at:
        return False
    try:
        hh, mm = (int(x) for x in at.split(":", 1))
    except ValueError:
        return False
    if now_et.weekday() >= 5:
        return False
    if (now_et.hour, now_et.minute) < (hh, mm):
        return False
    return now_et.strftime("%Y-%m-%d") != (last_sent_date or "")


# ─── App ─────────────────────────────────────────────────────────────────────

app = FastAPI(title="AFSVP push relay", docs_url=None, redoc_url=None, openapi_url=None)

_store: SubscriptionStore | None = None
_vapid: tuple[str, str] | None = None


def store() -> SubscriptionStore:
    global _store
    if _store is None:
        _store = SubscriptionStore(STATE_DIR / "subscriptions.json")
    return _store


def vapid() -> tuple[str, str]:
    global _vapid
    if _vapid is None:
        _vapid = load_or_create_vapid(STATE_DIR)
    return _vapid


def _send(subscription: Mapping[str, Any], event: Event) -> bool:
    """Deliver one push. Returns False (and drops the sub) when the endpoint is gone."""
    from pywebpush import WebPushException, webpush

    private_pem, _ = vapid()
    payload = json.dumps({"title": event.title, "body": event.body, "tag": event.tag, "url": event.url, "ts": int(time.time() * 1000)})
    try:
        webpush(
            subscription_info=dict(subscription),
            data=payload,
            vapid_private_key=private_pem,
            vapid_claims={"sub": VAPID_SUBJECT},
            ttl=600,
        )
        return True
    except WebPushException as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status in (404, 410):
            store().remove(str(subscription.get("endpoint", "")))
            logger.info("dropped dead subscription (%s)", status)
        else:
            logger.warning("push failed: %s", exc)
        return False
    except Exception as exc:  # pragma: no cover - transport
        logger.warning("push error: %s", type(exc).__name__)
        return False


def broadcast(event: Event) -> int:
    sent = 0
    for sub in list(store().all()):
        if _send(sub, event):
            sent += 1
    logger.info("broadcast %r → %d/%d", event.title, sent, len(store()))
    return sent


@app.get("/push/health")
async def health() -> dict[str, Any]:
    return {"ok": True, "subscriptions": len(store()), "watch": WATCH, "poll_sec": POLL_SEC, "backend_online": _watch_state.get("online")}


@app.get("/push/vapid-public-key")
async def vapid_public_key() -> dict[str, str]:
    return {"publicKey": vapid()[1]}


@app.post("/push/subscribe")
async def subscribe(request: Request) -> JSONResponse:
    body = await _json_body(request)
    sub = body.get("subscription") if isinstance(body, dict) else None
    if not isinstance(sub, dict) or not store().add(sub, str(body.get("ua", ""))):
        raise HTTPException(status_code=400, detail="invalid subscription")
    return JSONResponse({"ok": True, "subscriptions": len(store())})


@app.post("/push/unsubscribe")
async def unsubscribe(request: Request) -> dict[str, Any]:
    body = await _json_body(request)
    endpoint = str(body.get("endpoint", "") or "") if isinstance(body, dict) else ""
    return {"ok": True, "removed": store().remove(endpoint)}


@app.post("/push/test")
async def test_push(request: Request) -> dict[str, Any]:
    body = await _json_body(request)
    endpoint = str(body.get("endpoint", "") or "") if isinstance(body, dict) else ""
    sub = store().get(endpoint)
    if not sub:
        raise HTTPException(status_code=404, detail="not subscribed")
    ok = await asyncio.to_thread(_send, sub, Event(title="AFSVP test", body="Push alerts are working on this device.", tag="test"))
    return {"ok": ok}


async def _json_body(request: Request) -> Any:
    try:
        return await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid json")


# ─── Watcher ─────────────────────────────────────────────────────────────────

_watch_state: dict[str, Any] = {"online": None, "failures": 0, "sig": None, "daily_sent": None}
_DAILY_MARK = STATE_DIR / "daily_sent.txt"


def _load_daily_mark() -> str | None:
    try:
        return _DAILY_MARK.read_text().strip() or None
    except FileNotFoundError:
        return None


def _save_daily_mark(day: str) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    _DAILY_MARK.write_text(day)


_http_client: Any = None


def _client() -> Any:
    """One long-lived HTTP client for the poll loop.

    A new ``httpx.AsyncClient`` per poll builds a fresh SSL context (certifi
    bundle) each time; on the box that retained ~0.7 MB per poll and grew the
    relay to ~850 MB (mostly swapped) over two days. Reusing one client is flat.
    """
    global _http_client
    if _http_client is None or _http_client.is_closed:
        import httpx

        _http_client = httpx.AsyncClient(timeout=10.0)
    return _http_client


async def _fetch_today() -> dict[str, Any] | None:
    r = await _client().get(f"{STATUS_BASE}/status/today")
    r.raise_for_status()
    data = r.json()
    return data if isinstance(data, dict) else None


async def _tick() -> None:
    st = _watch_state
    try:
        today = await _fetch_today()
        if today is None:
            raise ValueError("bad payload")
        st["failures"] = 0
        if st["online"] is False:
            await asyncio.to_thread(broadcast, Event(title="Backend back online", body="AFSVP status feed recovered.", tag="backend"))
        st["online"] = True
        sig = signature(today)
        for ev in diff_events(st.get("sig"), sig):
            await asyncio.to_thread(broadcast, ev)
        st["sig"] = sig
        now_et = datetime.now(ET)
        if daily_due(now_et, st.get("daily_sent")):
            day = now_et.strftime("%Y-%m-%d")
            st["daily_sent"] = day
            _save_daily_mark(day)
            await asyncio.to_thread(broadcast, daily_summary(today))
    except Exception as exc:
        st["failures"] += 1
        logger.warning("status poll failed (%d): %s", st["failures"], type(exc).__name__)
        if st["failures"] >= OFFLINE_AFTER_FAILURES and st["online"] is not False:
            st["online"] = False
            await asyncio.to_thread(broadcast, Event(title="Backend down", body=f"AFSVP status feed unreachable for {OFFLINE_AFTER_FAILURES} polls.", tag="backend"))


async def _watch_loop() -> None:
    while True:
        await _tick()
        await asyncio.sleep(POLL_SEC)


@app.on_event("startup")
async def _startup() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    store()
    vapid()
    _watch_state["daily_sent"] = _load_daily_mark()
    if WATCH:
        asyncio.create_task(_watch_loop())
        logger.info("watching %s every %ds; %d subscriptions", STATUS_BASE, POLL_SEC, len(store()))


@app.on_event("shutdown")
async def _shutdown() -> None:
    global _http_client
    if _http_client is not None:
        await _http_client.aclose()
        _http_client = None
