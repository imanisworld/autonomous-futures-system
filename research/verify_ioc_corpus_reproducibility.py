"""Preflight: can the corrected Corpus v1 IOC evidence (#346) be reproduced HERE?

EVIDENCE TOOLING ONLY. Read-only. Hashes files, reads git objects, runs nothing.

Why this exists
---------------
`scripts/corrected_ioc_corpus_evidence.py` records `corpus_tree_sha256` and
`risk_rules_sha256_before/after` into its results meta, but its only internal
guard is before == after WITHIN a single run. It does NOT check either hash
against the run being reproduced. So a re-run at today's HEAD passes every
assertion in that script while silently using a DIFFERENT risk_rules.yaml -- and
therefore a different enabled-strategy set -- than the canonical result.

That is exactly what happens today: PR #376 (`Isolate MNQ ORB Breakout inverse
lane as repo-default config, risk_rules 1.2.0`) changed risk_rules.yaml from
56677a0a... to 0325eefe.... A naive reproduction attempt at HEAD would produce
different numbers and report success.

Run this FIRST. It tells you whether reproduction is possible and, if the code
tree is wrong, which commit to check out.

    python3 research/verify_ioc_corpus_reproducibility.py \
        --corpus data/replay_corpus_v1_market_condition_fixed

Exit 0 = reproducible from this tree. Exit 1 = not; the report says why.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RESULTS = REPO / "scripts/corrected_ioc_corpus_results.json"
EXPECTED_FILES = 626

# Verified 2026-08-08: the only commit reachable from origin/main that carries
# BOTH the orchestration script and risk_rules.yaml @ 56677a0a...
REPRO_COMMIT = "69ec77fd33834a437fec77a51249fa1d66030a16"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _tree_sha256(root: Path) -> tuple[int, str]:
    """Byte-identical to scripts/corrected_ioc_corpus_evidence.py::_tree_sha256."""
    digest = hashlib.sha256()
    paths = sorted(root.rglob("*.jsonl"))
    for path in paths:
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
    return len(paths), digest.hexdigest()


def _git(*args: str) -> str | None:
    try:
        return subprocess.run(
            ["git", "-C", str(REPO), *args],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--corpus", type=Path,
                    default=REPO / "data/replay_corpus_v1_market_condition_fixed")
    args = ap.parse_args(argv)

    checks: list[tuple[str, bool, str]] = []

    if not RESULTS.exists():
        print(f"FATAL: canonical results not found at {RESULTS}")
        return 1
    meta = json.loads(RESULTS.read_text())["meta"]
    want_corpus = meta["corpus_tree_sha256"]
    want_rules = meta["risk_rules_sha256_before"]

    corpus = args.corpus if args.corpus.is_absolute() else (REPO / args.corpus)
    if not corpus.is_dir():
        checks.append(("corpus present", False, f"missing: {corpus}"))
        n_files, got_corpus = 0, ""
    else:
        n_files, got_corpus = _tree_sha256(corpus)
        checks.append((
            "corpus file count", n_files == EXPECTED_FILES,
            f"{n_files} (want {EXPECTED_FILES})",
        ))
        checks.append((
            "corpus tree sha256", got_corpus == want_corpus,
            f"{got_corpus[:16]}... (want {want_corpus[:16]}...)",
        ))

    rules = REPO / "risk_rules.yaml"
    got_rules = _sha256(rules) if rules.exists() else ""
    checks.append((
        "risk_rules.yaml sha256", got_rules == want_rules,
        f"{got_rules[:16]}... (want {want_rules[:16]}...)",
    ))

    script = REPO / "scripts/corrected_ioc_corpus_evidence.py"
    checks.append(("orchestration script present", script.exists(), str(script)))

    reachable = _git("merge-base", "--is-ancestor", REPRO_COMMIT, "origin/main")
    checks.append((
        "reproduction commit reachable from origin/main",
        reachable is not None, REPRO_COMMIT[:12],
    ))

    width = max(len(name) for name, _, _ in checks)
    print("=" * 78)
    print("CORRECTED IOC CORPUS (#346) -- REPRODUCIBILITY PREFLIGHT")
    print("=" * 78)
    for name, ok, detail in checks:
        print(f"  [{'OK ' if ok else 'BAD'}] {name:<{width}}  {detail}")

    ok = all(o for _, o, _ in checks)
    print()
    if ok:
        print("VERDICT: REPRODUCIBLE from this tree. Inputs are byte-identical to")
        print("the canonical run. Regenerate with the command in the report's")
        print("Reproduction section, then diff results.json excluding `meta`.")
        print()
        print("Expected differences even on a perfect reproduction:")
        print("  * meta.main_sha                 -- records the checked-out commit")
        print("  * raw_trades[].paper_order_id   -- uuid4, random per run")
        print("Everything else must match exactly.")
        return 0

    print("VERDICT: NOT REPRODUCIBLE from this tree as it stands.")
    if got_rules and got_rules != want_rules:
        print()
        print("  risk_rules.yaml does not match the canonical run. That file sets")
        print("  enabled_concepts, so a re-run here would replay a DIFFERENT")
        print("  strategy set and still pass every assertion inside the")
        print("  orchestration script. Do not treat such a run as a reproduction.")
        print()
        print(f"  Fix: reproduce from {REPRO_COMMIT[:12]} in a separate worktree:")
        print(f"    git worktree add --detach ../ioc-repro {REPRO_COMMIT[:12]}")
        print("  and pass this repo's corpus path via --corpus (data/ is gitignored,")
        print("  so the new worktree will not have it).")
    if n_files and n_files != EXPECTED_FILES:
        print()
        print(f"  Corpus has {n_files} files, not {EXPECTED_FILES}. This is vendor")
        print("  data regenerable from Polygon; see the corpus manifest for the")
        print("  frozen query parameters.")
    return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
