"""U4: append-only enforcement for the trial ledger and OOS consumption ledger.

Both ledgers are append-only history. Every committed version after the
anchor, and the working tree, must keep every earlier line byte-identical and
in place; only new lines may be appended. The anchor is the first main commit
carrying the U2 OOS ledger; earlier history predates this rule.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ANCHOR = "cedeab8372eebcb888d6a550b6cfdae88f2e97c0"
LEDGERS = (
    "docs/research-trial-ledger.jsonl",
    "docs/research-oos-consumption-ledger.jsonl",
)


def _git(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(ROOT), *args], capture_output=True, text=True, check=check
    )


def _lines_at(commit: str, rel: str) -> list[str]:
    result = _git("show", f"{commit}:{rel}", check=False)
    if result.returncode != 0:
        raise AssertionError(f"{rel} missing at {commit[:12]}: {result.stderr.strip()}")
    return result.stdout.splitlines()


def assert_append_only(previous: list[str], current: list[str], *, where: str) -> None:
    assert len(current) >= len(previous), f"{where}: ledger lines were removed"
    for index, line in enumerate(previous):
        assert current[index] == line, f"{where}: ledger line {index + 1} was rewritten"


@pytest.mark.parametrize("rel", LEDGERS)
def test_ledger_is_append_only_since_anchor(rel: str) -> None:
    anchor_present = _git("cat-file", "-e", f"{ANCHOR}^{{commit}}", check=False)
    assert anchor_present.returncode == 0, (
        f"append-only anchor {ANCHOR[:12]} not in history; run against a full clone"
    )
    assert _git("merge-base", "--is-ancestor", ANCHOR, "HEAD", check=False).returncode == 0, (
        f"HEAD does not descend from the append-only anchor {ANCHOR[:12]}"
    )
    previous = _lines_at(ANCHOR, rel)
    commits = _git("rev-list", "--reverse", f"{ANCHOR}..HEAD", "--", rel).stdout.split()
    for commit in commits:
        current = _lines_at(commit, rel)
        assert_append_only(previous, current, where=f"{rel}@{commit[:12]}")
        previous = current
    working = (ROOT / rel).read_text(encoding="utf-8").splitlines()
    assert_append_only(previous, working, where=f"{rel}@working-tree")


def test_append_only_rule_rejects_rewrite_and_removal() -> None:
    base = ['{"a": 1}', '{"b": 2}']
    assert_append_only(base, base + ['{"c": 3}'], where="ok")
    with pytest.raises(AssertionError, match="rewritten"):
        assert_append_only(base, ['{"a": 1}', '{"b": 3}'], where="x")
    with pytest.raises(AssertionError, match="removed"):
        assert_append_only(base, ['{"a": 1}'], where="x")
    with pytest.raises(AssertionError, match="rewritten"):
        assert_append_only(base, ['{"b": 2}', '{"a": 1}'], where="x")
