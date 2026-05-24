"""
replay/manifest.py

Manifest support for curated replay suites.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class ReplayManifestEntry:
    path: str
    instrument: str
    session: str
    expected_behavior: str
    notes: Optional[str] = None
    allow_mixed_instruments: bool = False


class ReplayManifest:
    REQUIRED_ENTRY_FIELDS = {"path", "instrument", "session", "expected_behavior"}

    def __init__(self, entries: list[ReplayManifestEntry]):
        self.entries = entries

    @classmethod
    def load(cls, path: str | Path) -> "ReplayManifest":
        manifest_path = Path(path)
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        base_dir = manifest_path.parent
        days = payload.get("days")
        if not isinstance(days, list) or not days:
            raise ValueError(f"{manifest_path} must contain a non-empty 'days' list")

        entries = []
        for idx, raw in enumerate(days, start=1):
            missing = cls.REQUIRED_ENTRY_FIELDS.difference(raw)
            if missing:
                raise ValueError(
                    f"{manifest_path}:days[{idx}] missing fields: {sorted(missing)}"
                )
            raw_path = Path(raw["path"])
            resolved = raw_path if raw_path.is_absolute() else base_dir / raw_path
            if not resolved.exists():
                raise ValueError(f"{manifest_path}:days[{idx}] missing replay file: {resolved}")
            entries.append(
                ReplayManifestEntry(
                    path=str(resolved),
                    instrument=raw["instrument"],
                    session=raw["session"],
                    expected_behavior=raw["expected_behavior"],
                    notes=raw.get("notes"),
                    allow_mixed_instruments=raw.get("allow_mixed_instruments", False),
                )
            )
        return cls(entries)

    @property
    def paths(self) -> list[str]:
        return [entry.path for entry in self.entries]
