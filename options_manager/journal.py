"""Options journal — writes to its own JSONL file.

Never writes to logs/journal_<date>.jsonl (the futures journal). Uses a
distinct filename prefix (options_journal_) specifically to avoid any chance
of colliding with the futures system's daily-state reconstruction.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date as date_type
from pathlib import Path
from typing import Any

from .models import OptionTradePacket
from .validation.advisory_decision import AdvisoryDecisionResult

JOURNAL_FILENAME_PREFIX = "options_journal_"


def journal_path(journal_dir: str = "logs", for_date: date_type | None = None) -> Path:
    day = for_date or date_type.today()
    return Path(journal_dir) / f"{JOURNAL_FILENAME_PREFIX}{day.isoformat()}.jsonl"


def _serialize(packet: OptionTradePacket) -> dict[str, Any]:
    data = asdict(packet)
    data["contract_expiry"] = packet.contract_expiry.isoformat()
    data["created_at"] = packet.created_at.isoformat()
    return data


def _json_safe(value: Any) -> Any:
    if hasattr(value, "value"):
        return _json_safe(value.value)
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def log_packet(packet: OptionTradePacket, journal_dir: str = "logs") -> Path:
    path = journal_path(journal_dir, for_date=packet.created_at.date())
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(_serialize(packet)) + "\n")
    return path


def log_advisory_decision(
    *,
    request_payload: dict[str, Any],
    result: AdvisoryDecisionResult,
    journal_dir: str = "logs",
) -> Path:
    """Append a canonical advisory record to the existing options journal."""
    path = journal_path(journal_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "record_type": "advisory_decision",
        "request": _json_safe(request_payload),
        "decision": _json_safe(asdict(result)),
    }
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")
    return path
