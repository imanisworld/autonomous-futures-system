"""R6 driver: frozen P1 over R5 candidates — offline, deterministic, fail-closed. Synthetic only."""
from __future__ import annotations

import ast
import hashlib
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import structural_level_r6_features as r6  # noqa: E402

UTC = timezone.utc


def _corpus(tmp_path: Path, inst: str = "MNQ", days: int = 3) -> Path:
    d = tmp_path / inst
    d.mkdir()
    t = datetime(2025, 3, 3, 0, 0, tzinfo=UTC)
    px = 20000.0
    for day in range(days):
        rows = []
        for i in range(96):
            ts = t + timedelta(days=day, minutes=15 * i)
            px += (1.0 if (i % 7) else -3.0)
            rows.append({"timestamp": ts.isoformat(), "instrument": inst, "session": "asian",
                         "open": px, "high": px + 4, "low": px - 4, "close": px + 1, "volume": 100})
        (d / f"{inst}_{(t + timedelta(days=day)).date().isoformat()}.jsonl").write_text(
            "\n".join(json.dumps(r) for r in rows) + "\n")
    (d / "MANIFEST.json").write_text(json.dumps({"instrument": inst}))
    return d


def _candidates(tmp_path: Path, corpus: Path, inst: str = "MNQ") -> Path:
    bars = [json.loads(l) for f in sorted(corpus.glob("*.jsonl")) for l in f.read_text().splitlines()]
    rows = []
    for b in (bars[5], bars[150], bars[150], bars[250]):       # two candidates share one B0
        e = b["close"]
        key = f"shadow_setups|{inst}|{b['timestamp']}|fam|LONG|{e + len(rows)}"
        rows.append({"candidate_key": key, "instrument": inst, "bar_ts": b["timestamp"], "strategy": "ema_pullback_trend",
                     "family": "ema_pullback_trend", "direction": "LONG", "entry": e + len(rows), "stop": e - 10,
                     "target": e + 20, "session": "asian", "contract": "MNQM5", "is_roll_utc_day": False})
    p = tmp_path / "candidates.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return p


def test_r6_rows_keys_causality_and_determinism(tmp_path):
    corpus = _corpus(tmp_path)
    cands = _candidates(tmp_path, corpus)
    rows, meta = r6.build(cands, corpus, "MNQ")
    assert [r["candidate_key"] for r in rows] == [json.loads(l)["candidate_key"] for l in cands.read_text().splitlines()]
    assert meta["unique_b0"] == 3 and meta["b0_not_in_corpus"] == 0
    assert all("outcome" not in r and r["instrument"] == "MNQ" for r in rows)
    assert set(rows[0]["hypotheses"]) == set(r6.HYPOTHESES)
    # first candidate has 6 bars of history: every MAJOR level is NOT_AVAILABLE (fail-closed, nothing
    # substituted) so no hypothesis can be T or F
    assert rows[0]["n_bars_in_window"] == 6
    assert {"PDH", "PDL", "PWH", "PWL"} <= set(rows[0]["admitted_levels_not_available"])
    assert all(h["label"] in ("NOT_AVAILABLE", "NOT_APPLICABLE") for h in rows[0]["hypotheses"].values())
    # levels at a B0 are a function of bars <= B0 only: appending later bars must not change the row
    later = corpus / "MNQ_2025-03-06.jsonl"
    later.write_text(json.dumps({"timestamp": "2025-03-06T00:00:00+00:00", "instrument": "MNQ", "session": "asian",
                                 "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 1}) + "\n")
    rows2, _ = r6.build(cands, corpus, "MNQ")
    assert json.dumps(rows, sort_keys=True, default=str) == json.dumps(rows2, sort_keys=True, default=str)
    # CLI: byte-identical rerun, manifest hashes, p1 identity recorded
    out_a, out_b = tmp_path / "a", tmp_path / "b"
    assert r6.main(["--candidates", str(cands), "--corpus-dir", str(corpus), "--instrument", "MNQ", "--out-dir", str(out_a)]) == 0
    assert r6.main(["--candidates", str(cands), "--corpus-dir", str(corpus), "--instrument", "MNQ", "--out-dir", str(out_b)]) == 0
    assert (out_a / "features.jsonl").read_bytes() == (out_b / "features.jsonl").read_bytes()
    m = json.loads((out_a / "manifest.json").read_text())
    assert m["features_file"]["sha256"] == hashlib.sha256((out_a / "features.jsonl").read_bytes()).hexdigest()
    assert m["inputs"]["p1_module"]["prereg_version"] and m["inputs"]["p1_module"]["sha256"]
    assert m["summary"]["rows"] == 4 and m["summary"]["unique_candidate_keys"] == 4 and m["outcome_files_read"] == []


def test_r6_refuses_wrong_hash_wrong_instrument_and_outcome_fields(tmp_path):
    corpus = _corpus(tmp_path)
    cands = _candidates(tmp_path, corpus)
    assert r6.main(["--candidates", str(cands), "--corpus-dir", str(corpus), "--instrument", "MNQ",
                    "--out-dir", str(tmp_path / "x"), "--expect-sha256", "0" * 64]) == 2
    with pytest.raises(SystemExit):
        r6.build(cands, corpus, "MES")
    bad = tmp_path / "bad.jsonl"
    row = json.loads(cands.read_text().splitlines()[0]); row["outcome"] = {"result": "WIN"}
    bad.write_text(json.dumps(row) + "\n")
    with pytest.raises(SystemExit):
        r6.build(bad, corpus, "MNQ")


def test_r6_never_imports_runtime():
    src = (Path(__file__).resolve().parent.parent / "scripts" / "structural_level_r6_features.py").read_text()
    names = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    forbidden = ("webhook", "execution", "broker", "strategy", "replay", "risk", "runner", "sources")
    assert not [n for n in names if n.split(".")[0] in forbidden], names
    assert "outcomes.sealed" not in src.replace("never opens ``outcomes.sealed.jsonl``", "")
