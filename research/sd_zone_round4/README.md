# S/D zone Round 4: audit artifacts

Research only; nothing here is imported by runtime code. For the full write-up see `docs/options-sd-zone-round4-audit-2026-09-24.md`. For the frozen validation plan see `docs/prereg-mnq-sd-zone-lfull-hold-validation-2026-09-24.md`.

| file | what |
|---|---|
| `ROUND4_PREREG_FROZEN.md` | Byte copy of the Round-4 prereg (sha256 `6d9b9aaa…c44`) |
| `frozen_round4_code/` | Byte copy of the frozen Round-4 code. The sha256s are in the manifest |
| `round4_archive_manifest.json` | SHA-256 and size of every file in the private archive, plus the raw-corpus fingerprints |
| `independent_l_full.py` | Independent re-implementation of LOOSE FULL/EDGE H2/H15, written from the prereg text only. It matched the archived ledgers trade for trade |
| `robustness.py` | Concentration (top-1, top-3, top-5%), R multiples, half-years, trades per day and MAE/MFE, computed from the ledgers |
| `round4_robustness.json` | Output of `robustness.py` on the reproduced ledgers |

**Not committed:**
- the raw bars (`data/replay_polygon_5m`, which is gitignored);
- the cache `.npz` files (about 6.7 MB each);
- the per-cell ledgers.

All three stay in the private local archive `afs-private-archive/sdzones4-20260924/`, which is left unchanged.

**Reproduce the frozen run.** Copy the archive to scratch, then run:

```bash
cd code && python3 verify_detectors4.py && python3 verify_engine4.py && python3 run4.py 500
```

**Check the reproduction:**

```bash
python3 independent_l_full.py <archive>/cache <archive>/results
python3 robustness.py <archive>/cache <archive>/results out.json
```
