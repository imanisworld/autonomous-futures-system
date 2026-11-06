"""Process-lifetime latch for startup reconciliation.

The lifespan records the reconcile result before it yields. Unresolved or
untrusted submissions stay blocked for the rest of the process. A later
background pass does not clear this latch; a restart does.
"""

from __future__ import annotations

_BLOCK: dict | None = None


def _id_list(payload: object) -> list:
    if not isinstance(payload, dict):
        return []
    raw = payload.get("unresolved")
    if isinstance(raw, list):
        return [item for item in raw if item]
    return []


def reconciliation_blocks(result: object) -> bool:
    """True unless the reconcile result is an explicit clear.

    A missing result, a non-dict, unresolved client ids, an untrusted store,
    or ``entries_blocked`` / ``recovery_required`` all block. ``unresolved``
    is read on the top-level result and under ``submissions``.
    """
    if not isinstance(result, dict):
        return True
    if _id_list(result):
        return True
    submissions = result.get("submissions")
    if not isinstance(submissions, dict):
        return True
    if submissions.get("ok") is not True:
        return True
    if submissions.get("entries_blocked") is True or submissions.get("recovery_required") is True:
        return True
    if submissions.get("store_trusted") is False:
        return True
    if _id_list(submissions):
        return True
    return False


def note_startup_reconciliation(result: object) -> str:
    """Record the startup result. Returns ``accept`` or ``block``."""
    global _BLOCK
    if reconciliation_blocks(result):
        submissions = result.get("submissions") if isinstance(result, dict) else None
        unresolved = _id_list(submissions) or _id_list(result)
        _BLOCK = {
            "entries_blocked": True,
            "recovery_required": True,
            "client_order_ids": [str(item) for item in unresolved],
        }
        return "block"
    _BLOCK = None
    return "accept"


def startup_entry_block() -> dict | None:
    """None when startup reconciliation cleared entries. Otherwise a block."""
    return None if _BLOCK is None else dict(_BLOCK)


def reset_startup_gate() -> None:
    """Test isolation. Production startup always calls ``note_startup_reconciliation``."""
    global _BLOCK
    _BLOCK = None
