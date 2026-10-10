#!/usr/bin/env python3
"""Read-only status dump for the observation-only setup-capture journal."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from alert_ranker.setup_capture import DEFAULT_JOURNAL  # noqa: E402
from alert_ranker.setup_capture_store import SetupCaptureJournal  # noqa: E402


def main() -> int:
    path = Path(os.environ.get("OPTIONS_SETUP_CAPTURE_JOURNAL", DEFAULT_JOURNAL))
    if not path.exists():
        print(json.dumps({"ok": False, "reason": "journal_missing", "path": str(path)}))
        return 2
    # Status is read-only: no mkdir, no torn-tail repair, no JOURNAL_REPAIR
    # append. Repair belongs to the collector, which holds the journal lock.
    store = SetupCaptureJournal(path, create=False)
    state = store.peek_state()
    counts = SetupCaptureJournal._counts_from_state(state)
    current = sorted(state["current"].values(), key=lambda row: row.persisted_at, reverse=True)
    recent = [row.to_dict() for row in current[:10]]
    print(
        json.dumps(
            {
                "ok": True,
                "observation_only": True,
                "execution_authority": False,
                "path": str(path),
                "counts": counts,
                "recent": recent,
            },
            indent=2,
            default=str,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
