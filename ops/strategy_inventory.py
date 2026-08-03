"""Read-only parser for docs/strategy-rules/Strategy_Inventory.md's Master Table.

Shared by ops.strategy_promotion (per-strategy research-evidence lookup) and
ops.daily_reconciliation (inventory-vs-runtime drift detection) so the table
is parsed in exactly one place instead of twice.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

COLUMNS = [
    "strategy", "rules", "detector", "replay_parity", "honest_fills",
    "walk_forward", "slippage", "sample", "verdict",
]

_VERDICT_RE = re.compile(r"\*\*([^*]+)\*\*")
_STOPWORDS = {"strat"}


def parse_master_table(text: str) -> list[dict[str, Any]]:
    lines = text.splitlines()
    try:
        table_start = next(i for i, line in enumerate(lines) if line.strip().startswith("## Master Table"))
    except StopIteration:
        return []
    header_idx = None
    for i in range(table_start + 1, len(lines)):
        if lines[i].strip().startswith("| Strategy"):
            header_idx = i
            break
    if header_idx is None or header_idx + 2 > len(lines):
        return []

    rows: list[dict[str, Any]] = []
    for line in lines[header_idx + 2:]:
        stripped = line.strip()
        if not stripped.startswith("|"):
            break
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if len(cells) < len(COLUMNS):
            continue
        row = dict(zip(COLUMNS, cells[: len(COLUMNS)]))
        verdict_match = _VERDICT_RE.search(row["verdict"])
        row["verdict_normalized"] = verdict_match.group(1).strip() if verdict_match else row["verdict"].strip("* ")
        rows.append(row)
    return rows


def load_master_table(path: str | Path) -> list[dict[str, Any]]:
    p = Path(path)
    if not p.exists():
        return []
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return []
    return parse_master_table(text)


def _tokens(value: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", value.lower()))


def match_strategy_rows(machine_name: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Best-effort match from a machine strategy name (e.g. ``orb_breakout``,
    ``strat_4hr_retrigger``) to Master Table rows (e.g. ``ORB Breakout
    (MNQ)``). Returns every row whose Strategy-column tokens are a superset
    of the machine name's tokens (minus generic stopwords) — deliberately
    permissive so a real match is never silently dropped. Multiple matches
    (e.g. one row per instrument) are all returned; resolving ambiguity is
    left to the caller/human, not guessed away.
    """
    wanted = _tokens(machine_name) - _STOPWORDS
    if not wanted:
        return []
    return [row for row in rows if wanted <= _tokens(row["strategy"])]
