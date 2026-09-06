"""Policy-regression scan over a PR's added/removed lines.

Heuristic, fail-closed, and explicit: every finding names its category,
file, and line so a human can confirm or dismiss it. A finding is a
REJECT — POLICY REGRESSION in the readiness verdict; it is never
auto-cleared. Categories (from the Phase 1 charter):

    strat_authority        a second Strat classifier outside the canonical modules
    signa_authority        Signa promoted to a gate / validity authority
    proxy_gex              fake, proxy, inferred, or pivot-derived GEX
    position_count_cap     a hard position-count cap
    inferred_aggregate_risk a numeric aggregate-risk default
    missing_risk_fail_open  missing risk treated as pass
    missing_contract_fail_open missing contract treated as pass / default valid
    execution              broker submission / order placement / auto entry-exit
    automatic_averaging     averaging down / adding to losers
    threshold_weakening     a MIN_* lowered or a MAX_* raised in source

Source files only for the execution/authority categories (test files
legitimately name those verbs in assertions). Test files are scanned
for removed fail-closed assertions, which HOLD rather than REJECT.
This module's own file is excluded from the scan because it must spell
the patterns it looks for; the capability audit covers it by AST.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Sequence

SELF_PATH = "ops/pr_promotion_readiness/regression.py"
CANONICAL_STRAT_MODULES = ("options_manager/strategies/strat_212.py", "options_manager/strategies/mechanical.py")

_SOURCE_PATTERNS: tuple[tuple[str, str], ...] = (
    ("strat_authority", r"def\s+_?(classify|detect)_?(strat|bar_type|candle_type|inside_bar)\b|\bINSIDE_BAR\s*=\s*['\"]"),
    # Config/code shapes only -- prose such as a validator's own "used Signa as authority" reason must not match.
    ("signa_authority", r"(enforce|require|strict)_signa\w*\s*=\s*True|signa_gate_enforced\s*=\s*True|SIGNA_GATE_ENFORCED\s*=\s*true|if\s+not\s+\w*signa\w*(_aligned|_ok|_valid)\w*\s*:\s*return\s+False"),
    ("proxy_gex", r"\b(proxy|synthetic|fake|inferred|estimated|estimate|infer|derive)_?gex\b|\bgex_(proxy|estimate|from_pivot|from_signa)\b|gex\w*\s*=\s*\w*(pivot|signa)\w*"),
    ("position_count_cap", r"\bmax_(open_)?positions?\s*[:=]\s*\d+|\bposition_count_cap\b|\bmax_position_count\b|\bMAX_(OPEN_)?POSITIONS\b"),
    ("inferred_aggregate_risk", r"(max_aggregate_open_risk_dollars|aggregate_risk_budget)\s*(:\s*[\w\[\]|., ]+?)?\s*=\s*(float\()?\s*\d|DEFAULT_(MAX_)?AGGREGATE_\w*RISK\w*\s*=\s*\d"),
    ("missing_risk_fail_open", r"(risk|budget)\w*\s+is\s+None\s*(:|\)|or)\s*(return\s+True|PASS|VALID|pass\b)|\brisk\w*\s*=\s*\w*\s+or\s+(DEFAULT_|\d)|(risk|budget)\w*_missing\w*\s*=\s*False"),
    ("missing_contract_fail_open", r"contract\w*\s+is\s+None\s*(:|\)|or)\s*(return\s+True|PASS|VALID|pass\b)|contract_valid\s*:\s*bool\s*=\s*True|contract_valid\s*=\s*True\s*#?\s*default|constraints_met\s*=\s*True\b"),
    ("execution", r"\b(place|submit|create|send)_(option_|equity_|crypto_)?orders?(_\w+)?\b|order_instruction|\bbroker\.(submit|place|send)\b|\bauto_(entry|exit|execute)\w*\s*=\s*True|\bexecute_trade\b|\bplace_trade\b"),
    ("automatic_averaging", r"\b(average|averaging)_down\b|\badd_to_(loser|losing)\b|\bscale_in_on_loss\b|\bmartingale\b"),
)
_THRESHOLD_RE = re.compile(r"^\s*(?P<name>(MIN|MAX|DEFAULT_MIN|DEFAULT_MAX)_[A-Z0-9_]+)\s*(?::\s*\w+\s*)?=\s*(?P<value>-?\d+(?:\.\d+)?)\s*$")
_TEST_FAIL_CLOSED_RE = re.compile(r"assert\b.*\b(fail|closed|block|reject|invalid|hold|missing|raises)\w*", re.IGNORECASE)


@dataclass(frozen=True)
class RegressionFinding:
    category: str
    file: str
    line: str
    severity: str  # "reject" | "hold"
    note: str = ""


def _is_test_file(path: str) -> bool:
    return path.startswith("tests/") or "/tests/" in path or path.split("/")[-1].startswith("test_")


def _added_removed(patch: str) -> tuple[list[str], list[str]]:
    added, removed = [], []
    for raw in (patch or "").splitlines():
        if raw.startswith("+++") or raw.startswith("---"):
            continue
        if raw.startswith("+"):
            added.append(raw[1:])
        elif raw.startswith("-"):
            removed.append(raw[1:])
    return added, removed


def scan_patch(path: str, patch: str) -> tuple[RegressionFinding, ...]:
    if path == SELF_PATH:
        return ()
    added, removed = _added_removed(patch)
    findings: list[RegressionFinding] = []
    is_test = _is_test_file(path)
    if not is_test and path.endswith(".py"):
        for line in added:
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            for category, pattern in _SOURCE_PATTERNS:
                if category == "strat_authority" and path in CANONICAL_STRAT_MODULES:
                    continue
                if re.search(pattern, line, re.IGNORECASE):
                    findings.append(RegressionFinding(category=category, file=path, line=stripped[:160], severity="reject"))
                    break
        removed_thresholds = {m.group("name"): float(m.group("value")) for m in map(_THRESHOLD_RE.match, removed) if m}
        for line in added:
            match = _THRESHOLD_RE.match(line)
            if not match or match.group("name") not in removed_thresholds:
                continue
            name, new = match.group("name"), float(match.group("value"))
            old = removed_thresholds[name]
            weakened = (name.startswith(("MIN", "DEFAULT_MIN")) and new < old) or (name.startswith(("MAX", "DEFAULT_MAX")) and new > old)
            if weakened:
                findings.append(RegressionFinding(category="threshold_weakening", file=path, line=line.strip()[:160], severity="reject", note=f"{name}: {old:g} -> {new:g}"))
    if is_test:
        for line in removed:
            if _TEST_FAIL_CLOSED_RE.search(line):
                findings.append(RegressionFinding(category="test_weakening", file=path, line=line.strip()[:160], severity="hold", note="fail-closed assertion removed; human review required"))
    return tuple(findings)


def scan_patches(patches: Iterable[tuple[str, str]]) -> tuple[RegressionFinding, ...]:
    out: list[RegressionFinding] = []
    for path, patch in patches:
        out.extend(scan_patch(path, patch))
    return tuple(out)


def describe(findings: Sequence[RegressionFinding]) -> tuple[str, ...]:
    return tuple(f"POLICY REGRESSION [{f.category}] {f.file}: {f.line}" + (f" ({f.note})" if f.note else "") for f in findings)
