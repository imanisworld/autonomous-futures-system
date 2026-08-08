"""Detect journal fields that runtime writes but replay cannot -- and vice versa.

EVIDENCE TOOLING ONLY. Read-only static analysis (ast + text scan). Imports no
runtime module, executes no strategy code, changes no behaviour.

The failure mode this exists to catch
-------------------------------------
A verdict is computed from a journal field. The live path populates that field.
The replay path does not. Replay then produces a confident verdict that was
never actually measured -- and because the field is merely absent or constant
rather than wrong, nothing raises. Three independently-confirmed instances
prompted this tool:

  1. SHADOW_OUTCOME     replay resolves shadow candidates but stores them as
                        decision["shadow_candidates"][i]["outcome"], while every
                        reader filters on record["type"] == "SHADOW_OUTCOME".
                        Same data, different shape, invisible.
  2. pine_has_bracket   derived from the TradingView webhook payload
                        (state.raw). Replay synthesises state from candles, so
                        the whole Pine-bracket override branch is unreachable.
  3. entry_status       PaperBroker hardcodes entry_status="dead", so replay's
                        no_fill_reason is ALWAYS NO_FILL_PRICE_MOVED_AWAY and
                        NO_FILL_LIMIT_TOO_PASSIVE can never occur. Replay is not
                        blind here -- it is confidently wrong, which is worse.

Usage:
    python3 research/replay_field_parity.py            # full report
    python3 research/replay_field_parity.py --json     # machine-readable
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# Call-site classification. A file is assigned to exactly one lane; anything
# unmatched is "shared" and reported separately rather than silently binned.
LIVE_PREFIXES = ("webhook/", "execution/", "strategy/", "notifications/")
REPLAY_PREFIXES = ("replay/", "research/", "scripts/")
SKIP_PREFIXES = ("tests/", ".git/", ".claude/", "__pycache__/", "interactive-course/")

JOURNAL_WRITERS = (
    "log_outcome",
    "log_decision",
    "log_shadow_outcome",
    "log_scout",
    "log_order_ids",
    "log_block_visibility",
    "log_day_only_exit_issue",
    "claim_bar",
)

# Writer method -> the journal record `type` it emits. Used to attribute a
# record type to the lanes that CALL it, rather than to journal_logger.py which
# merely defines it.
WRITER_METHOD_TYPE = {
    "log_outcome": "OUTCOME",
    "log_shadow_outcome": "SHADOW_OUTCOME",
    "log_scout": "SCOUT_SIGNAL",
    "log_order_ids": "ORDER_IDS",
    "log_block_visibility": "BLOCK_VISIBILITY",
    "log_day_only_exit_issue": "DAY_ONLY_EXIT_ISSUE",
    "claim_bar": "BAR_CLAIM",
}


def _iter_py() -> list[Path]:
    out = []
    for path in sorted(REPO.rglob("*.py")):
        rel = path.relative_to(REPO).as_posix()
        if any(rel.startswith(p) or f"/{p}" in f"/{rel}" for p in SKIP_PREFIXES):
            continue
        out.append(path)
    return out


def _lane(rel: str) -> str:
    if rel.startswith(REPLAY_PREFIXES):
        return "replay"
    if rel.startswith(LIVE_PREFIXES):
        return "live"
    return "shared"


@dataclass
class WriteSite:
    rel: str
    line: int
    method: str
    lane: str
    # kwarg -> "value" | "literal_none" (explicitly passed as None)
    kwargs: dict[str, str] = field(default_factory=dict)


def collect_write_sites(paths: list[Path]) -> list[WriteSite]:
    sites: list[WriteSite] = []
    for path in paths:
        rel = path.relative_to(REPO).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else (
                fn.id if isinstance(fn, ast.Name) else None
            )
            if name not in JOURNAL_WRITERS:
                continue
            site = WriteSite(rel=rel, line=node.lineno, method=name, lane=_lane(rel))
            for kw in node.keywords:
                if kw.arg is None:  # **kwargs -- cannot resolve statically
                    site.kwargs["**"] = "unresolved"
                    continue
                is_none = isinstance(kw.value, ast.Constant) and kw.value.value is None
                site.kwargs[kw.arg] = "literal_none" if is_none else "value"
            sites.append(site)
    return sites


def collect_record_types(paths: list[Path]) -> tuple[dict, dict]:
    """Writers and readers of journal record `type` discriminators.

    Returned refs are "path:line" so the lane of each can be recovered by the
    caller -- a type written only from live-lane files is one replay can never
    emit, which is exactly the SHADOW_OUTCOME failure.
    """
    writers: dict[str, list[str]] = defaultdict(list)
    readers: dict[str, list[str]] = defaultdict(list)
    for path in paths:
        rel = path.relative_to(REPO).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            # writer:  {"type": "OUTCOME", ...}  or  record["type"] = "OUTCOME"
            if isinstance(node, ast.Dict):
                for k, v in zip(node.keys, node.values):
                    if (
                        isinstance(k, ast.Constant) and k.value == "type"
                        and isinstance(v, ast.Constant) and isinstance(v.value, str)
                    ):
                        writers[v.value].append(f"{rel}:{node.lineno}")
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
                for tgt in node.targets:
                    if (
                        isinstance(tgt, ast.Subscript)
                        and isinstance(tgt.slice, ast.Constant)
                        and tgt.slice.value == "type"
                        and isinstance(node.value.value, str)
                    ):
                        writers[node.value.value].append(f"{rel}:{node.lineno}")
            # reader: comparison / membership against .get("type")
            if isinstance(node, ast.Compare):
                src = ast.dump(node.left)
                if '"type"' in src or "'type'" in src or "value='type'" in src:
                    for comp in node.comparators:
                        for const in ast.walk(comp):
                            if isinstance(const, ast.Constant) and isinstance(const.value, str):
                                readers[const.value].append(f"{rel}:{node.lineno}")
    return dict(writers), dict(readers)


def build_report() -> dict:
    paths = _iter_py()
    sites = collect_write_sites(paths)

    per_lane: dict[str, dict[str, set[str]]] = {
        lane: defaultdict(set) for lane in ("live", "replay", "shared")
    }
    for site in sites:
        for kwarg, kind in site.kwargs.items():
            per_lane[site.lane][kwarg].add(kind)

    def real(lane: str) -> set[str]:
        """Kwargs the lane passes with an actual value at least once."""
        return {k for k, kinds in per_lane[lane].items() if "value" in kinds}

    def none_only(lane: str) -> set[str]:
        return {k for k, kinds in per_lane[lane].items() if kinds == {"literal_none"}}

    live_real, replay_real = real("live"), real("replay")

    type_writers, type_readers = collect_record_types(paths)

    # journal/journal_logger.py DEFINES the record shapes; it is not itself a
    # lane. Attribute those types to the lanes that CALL the writer method,
    # otherwise every type looks "shared" and no gap can ever be detected.
    effective: dict[str, set[str]] = {
        t: {r for r in refs if not r.startswith("journal/")}
        for t, refs in type_writers.items()
    }
    for site in sites:
        rec_type = WRITER_METHOD_TYPE.get(site.method)
        if rec_type:
            effective.setdefault(rec_type, set()).add(f"{site.rel}:{site.line}")

    def lanes_of(refs) -> set[str]:
        return {_lane(r.rsplit(":", 1)[0]) for r in refs}

    # A record type whose ONLY writers sit in live-lane files is a type replay
    # can never emit -- while readers filter on it regardless. This is the
    # SHADOW_OUTCOME class of gap.
    live_only_types = {
        t: sorted(refs)
        for t, refs in effective.items()
        if t in type_readers and refs and lanes_of(refs) <= {"live"}
    }
    orphan_types = {
        t: sorted(refs)
        for t, refs in type_readers.items()
        if not effective.get(t)
    }

    return {
        "write_sites": len(sites),
        "live_only_fields": sorted(live_real - replay_real),
        "replay_only_fields": sorted(replay_real - live_real),
        "both": sorted(live_real & replay_real),
        "never_populated_anywhere": sorted(
            (none_only("live") | none_only("replay") | none_only("shared"))
            - (live_real | replay_real | real("shared"))
        ),
        "record_types_written": {k: sorted(v) for k, v in sorted(type_writers.items())},
        "record_types_read_but_never_written": dict(sorted(orphan_types.items())),
        "record_types_written_only_by_live": dict(sorted(live_only_types.items())),
        "sites_by_lane": {
            lane: sorted({f"{s.rel}:{s.line}:{s.method}" for s in sites if s.lane == lane})
            for lane in ("live", "replay", "shared")
        },
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args(argv)

    rep = build_report()
    if args.json:
        print(json.dumps(rep, indent=2, sort_keys=True))
        return 0

    print("=" * 78)
    print("REPLAY / RUNTIME JOURNAL FIELD PARITY")
    print("=" * 78)
    print(f"journal write sites analysed: {rep['write_sites']}")
    print(f"  live lane   : {len(rep['sites_by_lane']['live'])}")
    print(f"  replay lane : {len(rep['sites_by_lane']['replay'])}")
    print(f"  shared      : {len(rep['sites_by_lane']['shared'])}")

    print("\n-- WRITTEN BY LIVE, NEVER BY REPLAY "
          "(a verdict using these is unearnable from replay) --")
    for f in rep["live_only_fields"]:
        print(f"   {f}")
    if not rep["live_only_fields"]:
        print("   (none)")

    print("\n-- WRITTEN BY REPLAY, NEVER BY LIVE --")
    for f in rep["replay_only_fields"]:
        print(f"   {f}")
    if not rep["replay_only_fields"]:
        print("   (none)")

    print("\n-- DECLARED IN THE SCHEMA BUT NEVER GIVEN A VALUE ANYWHERE --")
    for f in rep["never_populated_anywhere"]:
        print(f"   {f}")
    if not rep["never_populated_anywhere"]:
        print("   (none)")

    print("\n-- RECORD TYPES READ BY EVIDENCE CODE BUT WRITTEN ONLY ON THE LIVE LANE --")
    print("   (replay journals can never contain these; readers filter on them anyway)")
    for t, refs in rep["record_types_written_only_by_live"].items():
        print(f"   {t}")
        for r in refs[:4]:
            print(f"       written at {r}")
    if not rep["record_types_written_only_by_live"]:
        print("   (none)")

    print("\n-- RECORD TYPES READ BUT NEVER WRITTEN ANYWHERE --")
    for t, refs in rep["record_types_read_but_never_written"].items():
        print(f"   {t}")
        for r in refs[:4]:
            print(f"       read at {r}")
    if not rep["record_types_read_but_never_written"]:
        print("   (none)")

    print("\nLIMITS OF THIS TOOL (read before quoting it):")
    print("  * Lane is assigned by CALL-SITE PATH, not by reachability. A shared")
    print("    module invoked only from live is classed 'shared', not 'live'.")
    print("  * It cannot see fields that both lanes write but with different")
    print("    SEMANTICS (e.g. PaperBroker's hardcoded entry_status='dead').")
    print("    Those need the hand-audit in the Batch 2 report.")
    print("  * It cannot see structural mismatches where replay nests data a")
    print("    reader expects at top level (the SHADOW_OUTCOME case).")
    print("  A clean run is NOT proof of parity. It is proof of one class of gap.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
