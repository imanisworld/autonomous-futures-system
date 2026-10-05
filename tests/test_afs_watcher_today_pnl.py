"""The watcher must not label cumulative account P&L as today's.

/status/today's legacy `realized_pnl_dollars` is CUMULATIVE account P&L. On
2026-10-02 the watcher's "today" block showed -59.75 with 0 trades because it
copied that field. Its two /status/today projections must record the explicit
`today_pnl_dollars` and `cumulative_realized_pnl_dollars` fields instead.
"""
from __future__ import annotations

import re
from pathlib import Path

SRC = (Path(__file__).parent.parent / "ops" / "afs_watcher" / "watcher.py").read_text()


def _status_today_projections() -> list[str]:
    # rt["today"] = {k: td.get(k) for k in (...)} and rep["status_today"] = {...}
    return re.findall(r'(?:rt\["today"\]|rep\["status_today"\]) = \{k: [^\n]*', SRC)


def test_both_projections_found():
    assert len(_status_today_projections()) == 2


def test_projections_use_explicit_pnl_fields():
    for line in _status_today_projections():
        assert '"today_pnl_dollars"' in line, line
        assert '"cumulative_realized_pnl_dollars"' in line, line
        assert '"realized_pnl_dollars"' not in line, line
