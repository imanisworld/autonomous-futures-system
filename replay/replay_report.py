"""
replay/replay_report.py

Summary object for offline replay runs.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass
class ReplayReport:
    source_path: str
    candles_processed: int
    decisions: int
    approved_trades: int
    no_trades: int
    wins: int
    losses: int
    open_trades: int
    realized_pnl_dollars: float
    stopped_reason: str | None
    journal_path: str
    review_path: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def write_markdown(self, path: str | Path) -> Path:
        report_path = Path(path)
        lines = [
            "# Replay Report",
            "",
            f"- Source: `{self.source_path}`",
            f"- Candles processed: {self.candles_processed}",
            f"- Decisions: {self.decisions}",
            f"- Approved trades: {self.approved_trades}",
            f"- NO_TRADE decisions: {self.no_trades}",
            f"- Wins: {self.wins}",
            f"- Losses: {self.losses}",
            f"- Open trades: {self.open_trades}",
            f"- Realized P/L: ${self.realized_pnl_dollars:.2f}",
            f"- Stopped reason: {self.stopped_reason or 'none'}",
            f"- Journal: `{self.journal_path}`",
            f"- Review: `{self.review_path or 'none'}`",
        ]
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return report_path
