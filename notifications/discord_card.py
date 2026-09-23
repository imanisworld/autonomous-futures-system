"""
notifications/discord_card.py

Presentation-only: turn a plain-text operator alert into a Discord embed card
in the paper-collection style (docs/discord-operator-message-style.md):
title, status color, labelled fields, footer.

The alert TEXT is never rewritten — only laid out. Parsing is deliberately
simple and deterministic:

  - first non-empty line                       -> card title (a long
                                                  "a · b · c" line keeps "a")
  - "Key: value" lines before any section      -> inline fields
  - "**Header**" / "Header:" lines (no value)  -> full-width field sections
  - fenced ``` blocks                          -> kept verbatim in place
  - "-# ..." subtext / trailing "READ ONLY …"
    or "[read-only …]" boundary line           -> footer
  - everything else                            -> description

Stdlib only, so standalone ops scripts can import it. Callers post the card
with ``post_card_or_text`` which falls back to the original plain text when
Discord rejects the embed (HTTP 400), so a layout problem can never drop an
alert.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Callable, Optional

# Paper-collection palette.
COLOR_FAIL = 0xED4245
COLOR_WARN = 0xF0B232
COLOR_PASS = 0x57F287
COLOR_INFO = 0x5865F2

_TITLE_MAX = 256
_DESCRIPTION_MAX = 3500
_FIELD_NAME_MAX = 256
_FIELD_VALUE_MAX = 1000
_FOOTER_MAX = 300
_MAX_FIELDS = 25
_TOTAL_MAX = 5800  # Discord hard limit is 6000 across all embed text.

_RECOVERY = re.compile(r"\b(RECOVER\w*|RESTORED|RESOLVED|CLEARED|BACK UP|BACK ONLINE)\b", re.I)
_FAIL = re.compile(
    r"(🔴|🚨|❌|⛔|🛑)|\b(FAIL\w*|ERROR\w*|DOWN|CRITICAL|CRASH\w*|HALT\w*|BLOCKED|STALL\w*|"
    r"ACTION REQUIRED|REJECT\w*|DEAD|OUTAGE|UNREACHABLE)\b",
    re.I,
)
_WARN = re.compile(r"(⚠|🟡|🟠)|\b(WARN\w*|STALE|DEGRADED|ATTENTION|GAP|DRIFT|RETRY\w*|NO DATA)\b", re.I)
_PASS = re.compile(r"(✅|🟢|✓)|\b(OK|PASS\w*|HEALTHY|UP)\b", re.I)

_KV = re.compile(r"^(?P<key>[^:\n]{1,40}?):\s+(?P<value>\S.*)$")
_HEADER_BOLD = re.compile(r"^\*\*(?P<name>[^*]{1,80})\*\*:?$")
_HEADER_COLON = re.compile(r"^(?P<name>[A-Za-z][^:]{0,60}):$")
_BULLET = re.compile(r"^\s*([-*•·]|\d+[.)])\s")
_BOUNDARY = re.compile(r"^(\[.*\]|(READ ONLY|OBSERVATION ONLY|PAPER ONLY|EVIDENCE ONLY)\b.*)$", re.I)
_INLINE_MAX = 40
_TITLE_SPLIT_AT = 60


def status_color(title: str, body: str = "") -> int:
    """Color from the title; fall back to emoji markers in the body."""
    if _RECOVERY.search(title):
        return COLOR_PASS
    for pattern, color in ((_FAIL, COLOR_FAIL), (_WARN, COLOR_WARN), (_PASS, COLOR_PASS)):
        if pattern.search(title):
            return color
    # Body words are too noisy ("Errors: 0"); only explicit emoji count there.
    if re.search(r"🔴|🚨|❌|⛔|🛑", body):
        return COLOR_FAIL
    if re.search(r"⚠|🟡|🟠", body):
        return COLOR_WARN
    return COLOR_INFO


def _clean_title(line: str) -> str:
    # Embed titles do not render markdown.
    return line.strip().lstrip("#").strip().replace("**", "").replace("__", "")


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _is_kv_key(key: str) -> bool:
    key = key.strip().strip("*").strip()
    if not key or key.startswith(("http", "`")):
        return False
    return len(key.split()) <= 5


def text_card(text: str, *, source: str = "", timestamp: Optional[datetime] = None) -> dict[str, Any]:
    """Lay out plain alert text as one paper-collection-style embed."""
    lines = str(text or "").replace("\r\n", "\n").split("\n")
    footer_bits: list[str] = []
    body: list[str] = []
    for line in lines:
        if line.startswith("-# "):
            footer_bits.append(line[3:].strip())
        else:
            body.append(line)
    while body and not body[0].strip():
        body.pop(0)
    while body and not body[-1].strip():
        body.pop()
    if len(body) > 1 and _BOUNDARY.match(body[-1].strip()):
        footer_bits.insert(0, body.pop().strip().strip("[]"))
    title = _clean_title(body.pop(0)) if body else (source or "AFS alert")
    if len(title) > _TITLE_SPLIT_AT and " · " in title:
        title, rest = title.split(" · ", 1)
        body.insert(0, rest)

    description: list[str] = []
    fields: list[dict[str, Any]] = []
    section: Optional[dict[str, Any]] = None
    in_code = False

    def target() -> list[str]:
        return section["lines"] if section is not None else description

    for raw in body:
        line = raw.rstrip()
        if line.strip().startswith("```"):
            in_code = not in_code
            target().append(line)
            continue
        if in_code:
            target().append(line)
            continue
        stripped = line.strip()
        header = _HEADER_BOLD.match(stripped) or _HEADER_COLON.match(stripped)
        if header and not _BULLET.match(line):
            section = {"name": header.group("name").strip(), "lines": []}
            fields.append(section)
            continue
        kv = _KV.match(stripped)
        if section is None and kv and _is_kv_key(kv.group("key")) and not _BULLET.match(line):
            fields.append({
                "name": kv.group("key").strip().strip("*").strip(),
                "value": kv.group("value").strip(),
                "inline": len(kv.group("value").strip()) <= _INLINE_MAX,
            })
            continue
        target().append(line)

    out_fields: list[dict[str, Any]] = []
    for field in fields:
        if "lines" in field:
            value = "\n".join(field["lines"]).strip("\n")
            out_fields.append({"name": field["name"], "value": value or "—"})
        else:
            out_fields.append(field)
    if len(out_fields) > _MAX_FIELDS:
        overflow = out_fields[_MAX_FIELDS - 1:]
        out_fields = out_fields[: _MAX_FIELDS - 1] + [{
            "name": "More",
            "value": "\n".join(f"{f['name']}: {f['value']}" for f in overflow),
        }]
    for field in out_fields:
        field["name"] = _cut(field["name"], _FIELD_NAME_MAX)
        field["value"] = _cut(field["value"], _FIELD_VALUE_MAX)

    desc = "\n".join(description).strip("\n")
    footer = " · ".join(bit for bit in [*footer_bits, source and f"AFS · {source}"] if bit) or "AFS · operator alert"
    embed: dict[str, Any] = {
        "title": _cut(title, _TITLE_MAX),
        "color": status_color(title, str(text or "")),
        "footer": {"text": _cut(footer, _FOOTER_MAX)},
        "timestamp": (timestamp or datetime.now(timezone.utc)).isoformat(),
    }
    if desc:
        embed["description"] = _cut(desc, _DESCRIPTION_MAX)
    if out_fields:
        embed["fields"] = out_fields

    # Keep the whole card under Discord's 6000-character embed budget.
    def total() -> int:
        return (len(embed["title"]) + len(embed.get("description", "")) + len(embed["footer"]["text"])
                + sum(len(f["name"]) + len(f["value"]) for f in embed.get("fields", [])))

    while total() > _TOTAL_MAX and embed.get("fields"):
        embed["fields"].pop()
        embed["description"] = _cut(
            (embed.get("description", "") + "\n… more detail in the source log.").strip(), _DESCRIPTION_MAX
        )
    if total() > _TOTAL_MAX:
        embed["description"] = _cut(embed.get("description", ""), max(0, _TOTAL_MAX - total() + len(embed.get("description", ""))))
    return embed


def card_payload(message: str | dict[str, Any], *, source: str = "") -> dict[str, Any]:
    """Webhook body for a message: dict bodies pass through, text becomes a card."""
    if isinstance(message, dict):
        return message
    return {"allowed_mentions": {"parse": []}, "embeds": [text_card(message, source=source)]}


def text_payload(message: str | dict[str, Any]) -> dict[str, Any]:
    """Plain-content fallback body; never used for a dict body that has no text."""
    if isinstance(message, dict):
        return message
    return {"content": str(message)[:2000], "allowed_mentions": {"parse": []}}


def is_payload_rejection(exc: BaseException) -> bool:
    """True only when Discord answered 400 (bad body) — safe to resend as text.

    Timeouts and 5xx are NOT retried as text: the card may already have been
    delivered and a second post would duplicate the alert.
    """
    code = getattr(exc, "code", None)  # urllib.error.HTTPError
    if code is None:
        response = getattr(exc, "response", None)  # httpx / requests
        code = getattr(response, "status_code", None)
    return code == 400


def post_card_or_text(
    post: Callable[[dict[str, Any]], Any],
    message: str | dict[str, Any],
    *,
    source: str = "",
) -> Any:
    """Post ``message`` as a card; on a 400 rejection resend it as plain text.

    ``post`` takes the JSON-able body and must raise on failure. Any other
    error propagates unchanged so each caller keeps its own failure handling.
    """
    try:
        return post(card_payload(message, source=source))
    except Exception as exc:
        if isinstance(message, dict) or not is_payload_rejection(exc):
            raise
        return post(text_payload(message))


def card_text(body: dict[str, Any]) -> str:
    """Flatten a webhook body (text or card) back to readable text — for logs and tests."""
    parts = [str(body.get("content") or "")]
    for embed in body.get("embeds") or []:
        parts.append(str(embed.get("title") or ""))
        parts.append(str(embed.get("description") or ""))
        parts.extend(f"{f.get('name')}: {f.get('value')}" for f in embed.get("fields") or [])
        parts.append(str((embed.get("footer") or {}).get("text") or ""))
    return "\n".join(p for p in parts if p)
