"""Hard refusals that sit in front of every mutating grok-ops verb.

Refusal codes name the gate. They do not include environment values.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FlatSnapshot:
    positions_readable: bool
    open_positions: int
    orders_readable: bool
    working_orders: int


@dataclass(frozen=True)
class GateStatus:
    ok: bool
    refusals: tuple[str, ...]


def parse_env(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        if not key.replace("_", "").isalnum():
            continue
        values[key] = value.strip()
    return values


def variants_are_inactive(text: str) -> bool:
    found = False
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if not stripped.startswith("VARIANTS_START") or "=" not in stripped:
            continue
        found = True
        body = stripped.split("#", 1)[0].strip()
        if body != "VARIANTS_START = None":
            return False
    return found


def evaluate_gates(
    *,
    env_text: str | None,
    env_unreadable: bool,
    lock_exists: bool | None,
    variants_text: str | None,
    variants_unreadable: bool,
    flat: FlatSnapshot | None,
) -> GateStatus:
    refusals: list[str] = []
    if env_unreadable or env_text is None:
        refusals.append("posture-unreadable")
    else:
        values = parse_env(env_text)
        if values.get("LIVE_TRADING_ENABLED") != "false":
            refusals.append("posture-live")
        if values.get("TRADOVATE_ENV") != "demo":
            refusals.append("posture-env")
        if values.get("VARIANTS_START"):
            refusals.append("shadow-active")
    if lock_exists is None:
        refusals.append("deploy-lock-unreadable")
    elif lock_exists:
        refusals.append("deploy-lock")
    if variants_unreadable or variants_text is None:
        refusals.append("shadow-unreadable")
    elif not variants_are_inactive(variants_text):
        refusals.append("shadow-active")
    if flat is None or not flat.positions_readable or not flat.orders_readable:
        refusals.append("flat-unreadable")
    else:
        if flat.open_positions:
            refusals.append("position-open")
        if flat.working_orders:
            refusals.append("order-open")
    deduped: list[str] = []
    for code in refusals:
        if code not in deduped:
            deduped.append(code)
    return GateStatus(ok=not deduped, refusals=tuple(deduped))
