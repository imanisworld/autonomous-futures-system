"""Read-only LLM triage for ACTION REQUIRED watcher events.

Operator ruling 2026-09-15: an ACTION REQUIRED alert may also be sent to an LLM
as a separate READ-ONLY ADVISORY lane —

    1. the watcher raises a whitelisted critical condition;
    2. it sends a small structured packet (finding code, instrument, open lane /
       position, latest bar age, release SHA, recent watcher facts);
    3. the model replies with "what happened / what to check / what not to touch";
    4. the answer is posted to Discord (DISCORD_ROUTE_TRIAGE, else the error route).

Boundary: the model has ZERO authority.  No tools, no broker, no env edits, no
restart commands, no automatic fixes.  It sees a packet and returns text; the
watcher remains the source of truth and stays read-only.

Why raw HTTP and not the `anthropic` SDK: the watcher runs `/usr/bin/python3` from
a tmpfs copy inside a read-only mount namespace (run_ro.sh) with stdlib only, and
no `anthropic` package exists on the box (system or venv).  Installing one is a
box change.  This module mirrors the Discord path (urllib) and is shaped so that
swapping `_post_messages` for `client.messages.create` is a one-function change.

Default OFF: nothing happens unless an API key is present (see `api_key()`), so
installing this alongside the watcher changes no behaviour until the operator
enables it.  Every failure degrades to a log line — triage can never raise into
the watcher tick or delay a Discord alert.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"
MODEL = "claude-opus-5"
MAX_TOKENS = 1200
TIMEOUT_S = 90
DAILY_CAP = 12                     # advisory posts per UTC day — a noise ceiling, not a target
DISCORD_LIMIT = 1900               # watcher's own content cap (2000 minus prefix)
KEY_ENV = "AFS_TRIAGE_API_KEY"
KEY_FILE_ENV = "AFS_TRIAGE_API_KEY_FILE"
DEFAULT_KEY_FILE = "/root/afs-shared/afs_watcher_src/.triage_key"
ROUTE_TRIAGE = "DISCORD_ROUTE_TRIAGE"
ROUTE_FALLBACK = "DISCORD_ROUTE_ERROR"

SYSTEM_PROMPT = """You are the read-only triage layer for an automated futures paper-trading watcher.
You receive one structured packet describing a condition the watcher has flagged as ACTION REQUIRED,
plus a few recent watcher facts. You have no tools and no authority: you cannot place or cancel
orders, flatten positions, edit environment files, restart services, or change any configuration,
and you must not tell the operator to do any of those things unless the packet itself makes it the
only safe option — and even then say "operator decision", never "do this now".

Answer for a human reading Discord on a phone. Use EXACTLY these three headings, each followed by
1–3 short lines, and nothing else (no preamble, no sign-off, no markdown tables):

What happened:
What to check:
Do not touch:

Write plain English for a non-trader: no jargon, codes or abbreviations, times in US Eastern (ET).
Ground every statement in the packet. If a field is missing or null, say it is unknown rather than
guessing. Keep the whole reply under 900 characters. Prefer the smallest reversible check first.
"""


# ── enablement ───────────────────────────────────────────────────────────────
def api_key(env_value) -> str | None:
    """Resolve the API key: ENV_FILE value, then process env, then a root-only key file.

    `env_value` is the watcher's `_env_value` (reads /root/afs-shared/.env). Returns
    None when triage is not configured — the caller must then do nothing.
    """
    for source in (lambda: env_value(KEY_ENV), lambda: os.environ.get(KEY_ENV)):
        try:
            v = source()
        except Exception:  # noqa: BLE001
            v = None
        if v:
            return v.strip()
    path = Path(os.environ.get(KEY_FILE_ENV) or (env_value(KEY_FILE_ENV) or DEFAULT_KEY_FILE))
    try:
        v = path.read_text(encoding="utf-8").strip()
        return v or None
    except OSError:
        return None


def enabled(env_value) -> bool:
    return api_key(env_value) is not None


# ── packet ───────────────────────────────────────────────────────────────────
def _age_min(now: datetime, ts: str | None) -> int | None:
    if not ts:
        return None
    try:
        d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return int((now - d).total_seconds() // 60)
    except ValueError:
        return None


def build_packet(*, key: str, finding: dict | None, tick: dict, state: dict, first_utc: str | None,
                 release_sha: str, service: str, now: datetime, recent_events: list[dict] | None = None) -> dict:
    """The only thing the model sees. Small, structured, secret-free by construction:
    every field is copied from named keys — no env, no URLs, no file bodies."""
    lanes = tick.get("lanes") or {}
    inv = lanes.get("inventory") or {}
    open_positions = []
    for name in lanes.get("open_positions") or []:
        pos = (inv.get(name) or {}).get("open_position") or {}
        open_positions.append({
            "lane": name,
            "direction": pos.get("direction"), "entry": pos.get("entry"),
            "stop": pos.get("stop"), "target": pos.get("target"),
            "entry_time": pos.get("entry_time"),
        })
    runtime = tick.get("runtime") or {}
    feed = (runtime.get("feed") or {}).get("instruments") or {}
    m = re.match(r"feed_([A-Z]+)_", key)
    instrument = m.group(1) if m else ("MNQ" if key.startswith("five_min_feed_") else None)
    detail = dict((finding or {}).get("detail") or {})
    samples = [str(s)[-200:] for s in (detail.pop("samples", None) or [])[:3]]
    packet = {
        "finding": {"code": key, "level": "BLOCKED", "summary": (finding or {}).get("summary"),
                    "detail": {k: v for k, v in detail.items() if isinstance(v, (str, int, float, bool, type(None)))},
                    "samples": samples, "first_seen_utc": first_utc},
        "instrument": instrument,
        "exposure": {"open_positions": open_positions},
        "bars": {
            "newest_5m_mnq_age_min": _age_min(now, lanes.get("newest_5m_mnq_bar_mtime")),
            "newest_15m_mnq_age_min": _age_min(now, lanes.get("newest_15m_mnq_bar_mtime")),
            "newest_15m_mes_age_min": _age_min(now, lanes.get("newest_mes_15m_bar_mtime")),
            "five_min_feed_stalled": lanes.get("five_min_feed_stalled"),
            "feed_gap_alarm": {k: {"status": (v or {}).get("status"), "stale_since": (v or {}).get("stale_since")} for k, v in feed.items()},
        },
        "runtime": {
            "release_sha": release_sha[:12], "service": service,
            "service_state": {k: (runtime.get("service") or {}).get(k) for k in ("ActiveState", "SubState", "NRestarts", "ActiveEnterTimestamp")},
            "webhook_posts_since_last_tick": runtime.get("alerts_since_last_tick"),
            "journal_newest_mtime": runtime.get("journal_newest_mtime"),
            "tradovate": {k: (runtime.get("tradovate") or {}).get(k) for k in ("state", "ready", "failure_reason", "market_active", "error")},
            "broker": {k: (runtime.get("broker") or {}).get(k) for k in ("ok", "env", "position", "message", "error")},
        },
        "other_open_blocked": sorted(k for k in (state.get("blocked") or {}) if k != key),
        "recent_watcher_facts": [
            {"utc": e.get("utc"), "kind": e.get("kind"), "key": e.get("key"), "summary": str(e.get("summary") or "")[:160]}
            for e in (recent_events or [])[-8:]
        ],
        "now_utc": now.isoformat(),
        "authority": "none — advisory only; the watcher is read-only and no action is taken automatically",
    }
    return packet


# ── model call (raw HTTP, stdlib) ────────────────────────────────────────────
def _post_messages(key: str, packet: dict, *, urlopen=urllib.request.urlopen) -> dict:
    body = json.dumps({
        "model": MODEL,
        "max_tokens": MAX_TOKENS,
        "system": SYSTEM_PROMPT,
        "output_config": {"effort": "medium"},
        "messages": [{"role": "user", "content": "ACTION REQUIRED packet:\n" + json.dumps(packet, sort_keys=True, default=str)}],
    }).encode("utf-8")
    req = urllib.request.Request(
        API_URL, data=body, method="POST",
        headers={"content-type": "application/json", "x-api-key": key, "anthropic-version": API_VERSION,
                 "user-agent": "afs-watcher-triage-readonly"},
    )
    with urlopen(req, timeout=TIMEOUT_S) as resp:
        return json.loads(resp.read().decode("utf-8"))


def extract_text(response: dict) -> str | None:
    """Text of the reply, or None on refusal / empty. Never raises on shape."""
    if response.get("stop_reason") == "refusal":
        return None
    parts = [b.get("text", "") for b in response.get("content") or [] if isinstance(b, dict) and b.get("type") == "text"]
    text = "\n".join(p for p in parts if p).strip()
    return text or None


# ── Discord text ─────────────────────────────────────────────────────────────
def triage_discord_text(key: str, headline: str, advice: str) -> str:
    advice = advice.strip()
    head = f"🧭 Suggestions: {headline}"
    foot = f"-# {key} · {MODEL} · advice only, no authority — it can't change anything"
    room = DISCORD_LIMIT - len(head) - len(foot) - 2
    if len(advice) > room:
        advice = advice[: max(0, room - 1)].rstrip() + "…"
    return "\n".join([head, advice, foot])


# ── orchestration ────────────────────────────────────────────────────────────
def _day(now: datetime) -> str:
    return now.strftime("%Y-%m-%d")


def maybe_triage(*, key: str, finding: dict | None, tick: dict, state: dict, first_utc: str | None,
                 headline: str, release_sha: str, service: str, now: datetime,
                 env_value, notify, log, recent_events: list[dict] | None = None,
                 urlopen=urllib.request.urlopen) -> bool:
    """Run one advisory for a newly raised ACTION REQUIRED condition.

    Returns True when a triage post was made. Never raises. Once per (key, first
    raise), at most DAILY_CAP per UTC day. All I/O failures become a log line.
    """
    tri = state.setdefault("triage", {"done": {}, "day": None, "count": 0})
    episode = f"{key}:{first_utc}"
    if episode in tri["done"]:
        return False
    k = api_key(env_value)
    if not k:
        return False
    if tri.get("day") != _day(now):
        tri["day"], tri["count"] = _day(now), 0
    if tri["count"] >= DAILY_CAP:
        log(f"TRIAGE skipped {key}: daily cap {DAILY_CAP} reached")
        tri["done"][episode] = "capped"
        return False
    tri["done"][episode] = "attempted"
    tri["count"] += 1
    try:
        packet = build_packet(key=key, finding=finding, tick=tick, state=state, first_utc=first_utc,
                              release_sha=release_sha, service=service, now=now, recent_events=recent_events)
        response = _post_messages(k, packet, urlopen=urlopen)
    except urllib.error.HTTPError as exc:
        log(f"TRIAGE FAILED {key}: HTTP {exc.code}")
        return False
    except Exception as exc:  # noqa: BLE001 — advisory lane must never break the tick
        log(f"TRIAGE FAILED {key}: {type(exc).__name__}: {exc}")
        return False
    advice = extract_text(response)
    if not advice:
        log(f"TRIAGE {key}: no usable reply (stop_reason={response.get('stop_reason')})")
        return False
    route = ROUTE_TRIAGE if env_value(ROUTE_TRIAGE) else ROUTE_FALLBACK
    notify(state, route, triage_discord_text(key, headline, advice), f"triage:{episode}")
    usage = response.get("usage") or {}
    log(f"TRIAGE posted {key} via {route} (in={usage.get('input_tokens')} out={usage.get('output_tokens')})")
    tri["done"][episode] = "posted"
    return True
