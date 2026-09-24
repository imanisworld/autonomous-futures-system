# Fallback: Operator re-run of the frozen 3-2-2 34 with the patched resolver (AFS-0034)

Run this where the frozen 5m corpus lives (`data/replay_corpus_v1_5m_4hr_audit/MNQ`). It is read-only: nothing is deployed, no broker is touched, and the output is a local JSON file.

Caveat: the flags below come from the scripts' docstrings (`--data-root`). Run `--help` first and adjust if a flag name differs. This command was not executed here; the corpus is not in the repo.

```bash
# 1. Clean checkout of the draft PR branch
git fetch origin && git checkout cursor/resolve-bracket-exact-eod-38fe
git rev-parse HEAD:scripts/edge_decomposition_audit.py
# expect 230ec92e69168d34c310baae4e37bf50bb05cc7f (patched blob)

# 2. Confirm the corpus is the frozen one before trusting any number
#    Expected MNQ-dir SHA-256 (A/B doc): 7f09a7f82ee282e892f5db9b86be3130e6a5c1210d86828a56b9666bb06afd35
#    The harness computes and prints this itself; stop if it differs.

# 3. New regression tests
python -m pytest tests/test_resolve_bracket_exact_eod.py -q

# 4. Re-run the A/B harness with the patched resolver
python scripts/322_trigger_timing_ab_2026_09_18.py --help
python scripts/322_trigger_timing_ab_2026_09_18.py --data-root data > /tmp/322_rerun.log 2>&1; echo "exit=$?"
```

What to expect

- If the ERRATUM gate values are in place, `check_repro` passes. Without them, it RAISES on EXPECTED_PLAN/EXPECTED_IOC, which proves the fix changed the result (the sealed values include the 2025-01-20 evening win).
- The corrected expected values (derived from exit timestamps, pending corpus re-run) are: plan net 2293.64 / H1 1144.32 / H2 1149.32; IOC net 1622.38 / H1 831.66 / H2 790.72; filled counts unchanged. Pre-armed at 3 ticks: 32-0, 1 EOD_BAR_MISSING (2025-01-20), 1 bracket-invalid no-fill (2026-05-12), net +2471.64.
- Pass condition: the only rows that differ from `scripts/322_trigger_timing_ab_2026-09-18.json` are the 9 cells for 2025-01-20 (3 fill models x 3 slippages), now UNRESOLVED / EOD_BAR_MISSING. Any other changed row is a finding.

Send back: the output JSON, the printed corpus hash, the pytest result, and `git rev-parse HEAD`.

Corpus re-run: HOLD. The Operator approves the erratum at merge review.
