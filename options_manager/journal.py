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

JOURNAL_FILENAME_PREFIX = "options_journal_"


def journal_path(journal_dir: str = "logs", for_date: date_type | None = None) -> Path:
    day = for_date or date_type.today()
    return Path(journal_dir) / f"{JOURNAL_FILENAME_PREFIX}{day.isoformat()}.jsonl"


def _serialize(packet: OptionTradePacket) -> dict[str, Any]:
    data = asdict(packet)
    data["contract_expiry"] = packet.contract_expiry.isoformat()
    data["created_at"] = packet.created_at.isoformat()
    return data


def log_packet(packet: OptionTradePacket, journal_dir: str = "logs") -> Path:
    path = journal_path(journal_dir, for_date=packet.created_at.date())
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(_serialize(packet)) + "\n")
    return path
