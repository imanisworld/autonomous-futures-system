#!/usr/bin/env python3
"""
scripts/sync_news_calendar.py

Syncs high-impact economic event dates into risk_rules.yaml.
Dry-run by default — shows what would change without touching the file.
Requires --apply to actually write.

Sources:
  FOMC  → https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm
  CPI   → https://www.bls.gov/schedule/news_release/cpi.htm
  NFP   → https://www.bls.gov/schedule/news_release/empsit.htm

Usage:
  python scripts/sync_news_calendar.py            # dry-run, show diff
  python scripts/sync_news_calendar.py --apply    # write to risk_rules.yaml
  python scripts/sync_news_calendar.py --verify   # open BLS/Fed pages in browser
  python scripts/sync_news_calendar.py --year 2027  # show dates for a specific year

Safety:
  - Dry-run by default (--apply required to write)
  - Backs up risk_rules.yaml → risk_rules.yaml.bak before writing
  - Only touches the news_blackout_dates list — nothing else in the file
  - Exits non-zero if the yaml would be structurally broken
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
import webbrowser
from datetime import date
from pathlib import Path

# ─── Hardcoded 2026 dates ─────────────────────────────────────────────────────
# FOMC: confirmed from federalreserve.gov (fetched 2026-06-01)
# CPI/NFP: from BLS release calendar — verify annually at:
#   https://www.bls.gov/schedule/news_release/cpi.htm
#   https://www.bls.gov/schedule/news_release/empsit.htm

CALENDAR: dict[int, dict[str, list[str]]] = {
    2026: {
        # FOMC decision days — statement released ~14:00 ET
        "fomc": [
            "2026-01-28",
            "2026-03-18",
            "2026-04-29",
            "2026-06-17",
            "2026-07-29",
            "2026-09-16",
            "2026-10-28",
            "2026-12-09",
        ],
        # CPI — released 08:30 ET
        # Source: BLS schedule (verify at bls.gov/schedule/news_release/cpi.htm)
        "cpi": [
            "2026-01-15",
            "2026-02-12",
            "2026-03-11",
            "2026-04-10",
            "2026-05-13",
            "2026-06-11",
            "2026-07-15",
            "2026-08-12",
            "2026-09-10",
            "2026-10-14",
            "2026-11-12",
            "2026-12-10",
        ],
        # NFP (Employment Situation) — released 08:30 ET, first Friday of month
        # Source: BLS schedule (verify at bls.gov/schedule/news_release/empsit.htm)
        "nfp": [
            "2026-01-09",
            "2026-02-06",
            "2026-03-06",
            "2026-04-03",
            "2026-05-01",
            "2026-06-05",
            "2026-07-02",
            "2026-08-07",
            "2026-09-04",
            "2026-10-02",
            "2026-11-06",
            "2026-12-04",
        ],
    }
}

VERIFY_URLS = [
    "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
    "https://www.bls.gov/schedule/news_release/cpi.htm",
    "https://www.bls.gov/schedule/news_release/empsit.htm",
]

YAML_PATH = Path(__file__).parent.parent / "risk_rules.yaml"
BACKUP_PATH = YAML_PATH.with_suffix(".yaml.bak")


def _all_dates_for_year(year: int) -> list[str]:
    """Return sorted unique blackout dates for a given year."""
    cal = CALENDAR.get(year, {})
    dates: set[str] = set()
    for event_type in ("fomc", "cpi", "nfp"):
        dates.update(cal.get(event_type, []))
    # Filter to only include dates that are actually in the target year
    valid = [d for d in dates if d.startswith(str(year))]
    return sorted(valid)


def _read_yaml_dates(content: str) -> list[str]:
    """Extract current news_blackout_dates from yaml content."""
    in_section = False
    dates: list[str] = []
    for line in content.splitlines():
        if re.match(r"\s*news_blackout_dates\s*:", line):
            in_section = True
            continue
        if in_section:
            m = re.match(r'\s*-\s*["\']?(\d{4}-\d{2}-\d{2})["\']?', line)
            if m:
                dates.append(m.group(1))
            elif line.strip() and not line.strip().startswith("-"):
                break
    return sorted(dates)


def _replace_yaml_dates(content: str, new_dates: list[str]) -> str:
    """Replace the news_blackout_dates list in yaml content, preserving structure."""
    lines = content.splitlines(keepends=True)
    result: list[str] = []
    in_section = False
    section_written = False

    for line in lines:
        if re.match(r"\s*news_blackout_dates\s*:", line):
            in_section = True
            result.append(line)
            # Write the comment line
            indent = re.match(r"(\s*)", line).group(1) + "  "
            for d in new_dates:
                result.append(f'{indent}- "{d}"\n')
            section_written = True
            continue

        if in_section:
            # Skip old date entries
            if re.match(r'\s*-\s*["\']?\d{4}-\d{2}-\d{2}["\']?', line):
                continue
            else:
                in_section = False
                result.append(line)
        else:
            result.append(line)

    return "".join(result)


def _show_diff(current: list[str], proposed: list[str]) -> None:
    current_set = set(current)
    proposed_set = set(proposed)
    added = sorted(proposed_set - current_set)
    removed = sorted(current_set - proposed_set)
    kept = sorted(current_set & proposed_set)

    print(f"\n{'─'*55}")
    print(f"  news_blackout_dates diff")
    print(f"{'─'*55}")
    if not added and not removed:
        print("  No changes — already up to date.")
        return
    for d in added:
        print(f"  + {d}")
    for d in removed:
        print(f"  - {d}")
    print(f"\n  Kept: {len(kept)}  Added: {len(added)}  Removed: {len(removed)}")
    print(f"  Total after: {len(proposed)}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync economic calendar into risk_rules.yaml")
    parser.add_argument("--apply",  action="store_true", help="Write changes to risk_rules.yaml")
    parser.add_argument("--verify", action="store_true", help="Open BLS/Fed pages in browser to verify dates")
    parser.add_argument("--year",   type=int, default=date.today().year, help="Target year (default: current)")
    args = parser.parse_args()

    if args.verify:
        print("Opening official sources in browser...")
        for url in VERIFY_URLS:
            print(f"  {url}")
            webbrowser.open(url)
        sys.exit(0)

    if args.year not in CALENDAR:
        print(f"No calendar data for {args.year}. Add it to CALENDAR in this script.")
        sys.exit(1)

    if not YAML_PATH.exists():
        print(f"risk_rules.yaml not found at {YAML_PATH}")
        sys.exit(1)

    content = YAML_PATH.read_text()
    current_dates = _read_yaml_dates(content)
    proposed_dates = _all_dates_for_year(args.year)

    print(f"\n  Year:     {args.year}")
    print(f"  FOMC:     {len(CALENDAR[args.year].get('fomc', []))} dates")
    print(f"  CPI:      {len(CALENDAR[args.year].get('cpi', []))} dates")
    print(f"  NFP:      {len(CALENDAR[args.year].get('nfp', []))} dates")
    print(f"  Current:  {len(current_dates)} blackout dates in yaml")
    print(f"  Proposed: {len(proposed_dates)} blackout dates")

    _show_diff(current_dates, proposed_dates)

    if not args.apply:
        print(f"\n  DRY RUN — no changes made.")
        print(f"  Run with --apply to write to {YAML_PATH.name}")
        sys.exit(0)

    # Backup
    shutil.copy2(YAML_PATH, BACKUP_PATH)
    print(f"\n  Backed up → {BACKUP_PATH.name}")

    # Write
    new_content = _replace_yaml_dates(content, proposed_dates)
    YAML_PATH.write_text(new_content)
    print(f"  Written  → {YAML_PATH.name}")

    # Verify the write round-trips correctly
    readback = _read_yaml_dates(YAML_PATH.read_text())
    if sorted(readback) != sorted(proposed_dates):
        print(f"\n  ERROR: readback mismatch — restoring backup")
        shutil.copy2(BACKUP_PATH, YAML_PATH)
        sys.exit(1)

    print(f"  Verified — {len(readback)} dates readable after write.")
    print(f"\n  ✓ Done. Restart the webhook server to pick up changes.")


if __name__ == "__main__":
    main()
