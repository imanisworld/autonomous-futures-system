"""Tests for research/equity_corpus_batch_preflight.py (equity corpus v2).

Three things these tests exist to prove:

1. **The preflight never touches the network.** Sockets and every HTTP client
   entry point are hard-blocked for the whole module, and no API key is present.

2. **Every gate fails closed.** There is a negative test for each failure mode,
   including one per defect the v2 correction was written to fix.

3. **The four v2 corrections actually hold**, each with its own section below:
   D-1 calendar-aware sessions, D-2 real grid coverage, D-3 no one-page
   pagination assumption, D-4 checkpoints bound to the corpus bytes.
"""

from __future__ import annotations

import json
import socket
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research import equity_corpus_batch_preflight as pf  # noqa: E402


# ---------------------------------------------------------------------------
# Hard no-network guard — applies to every test in this module
# ---------------------------------------------------------------------------


def forbidden_network(*args, **kwargs):
    raise AssertionError("network access forbidden during preflight")


@pytest.fixture(autouse=True)
def block_all_network(monkeypatch):
    monkeypatch.setattr(socket, "socket", forbidden_network)
    monkeypatch.setattr(socket, "create_connection", forbidden_network)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden_network)
    monkeypatch.setattr(socket, "gethostbyname", forbidden_network)

    for mod_name, attrs in (
        ("httpx", ("get", "post", "request", "stream", "Client", "AsyncClient")),
        ("requests", ("get", "post", "request", "Session")),
        ("urllib.request", ("urlopen", "urlretrieve")),
    ):
        mod = sys.modules.get(mod_name)
        if mod is None:
            continue
        for attr in attrs:
            if hasattr(mod, attr):
                monkeypatch.setattr(mod, attr, forbidden_network)

    monkeypatch.delenv("POLYGON_API_KEY", raising=False)


def test_preflight_module_imports_no_network_library():
    source = Path(pf.__file__).read_text()
    for banned in ("import httpx", "import requests", "urllib.request", "http.client"):
        assert banned not in source, f"preflight must not reference {banned!r}"


def test_network_guard_is_actually_armed():
    with pytest.raises(AssertionError, match="forbidden"):
        socket.socket()


def test_no_production_trading_imports():
    """The preflight must not pull in the live trading stack."""
    source = Path(pf.__file__).read_text()
    for banned in ("from strategy", "from risk", "from execution", "from webhook",
                   "from broker", "import config.settings", "from config"):
        assert banned not in source, f"preflight must not import {banned!r}"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

ET = pf.ET


def _epoch_ms(day: date, minutes: int) -> int:
    dt = datetime(day.year, day.month, day.day, minutes // 60, minutes % 60, tzinfo=ET)
    return int(dt.timestamp() * 1000)


def make_bars(days, *, price: float = 100.0) -> list[dict]:
    """Complete, calendar-aware 5-minute bars for the given sessions."""
    bars: list[dict] = []
    for day in days:
        for tag in pf.SESSION_TAGS:
            for ts in pf.expected_grid(day, tag):
                bars.append({
                    "t": ts, "o": price, "h": price + 1, "l": price - 1,
                    "c": price, "v": 1000, "session": tag,
                })
    bars.sort(key=lambda b: b["t"])
    return bars


def write_bars(bars_dir: Path, ticker: str, bars, *, truncate: bool = False) -> Path:
    bars_dir.mkdir(parents=True, exist_ok=True)
    path = pf.bar_file_for(bars_dir, ticker)
    text = "".join(json.dumps(b) + "\n" for b in bars)
    if truncate:
        text = text[: len(text) - 12]
    path.write_text(text)
    return path


def make_evidence(ticker: str, bars, manifest_requests, *, pages: int = 1) -> dict:
    """Well-formed pagination evidence consistent with `bars`."""
    by_day: dict[date, list[int]] = {}
    for bar in bars:
        by_day.setdefault(pf._et_datetime(bar["t"]).date(), []).append(bar["t"])

    requests = []
    counter = 0
    for req in manifest_requests:
        lo = date.fromisoformat(req["slice_start"])
        hi = date.fromisoformat(req["slice_end"])
        stamps = sorted(t for d, ts in by_day.items() if lo <= d <= hi for t in ts)

        chunks: list[list[int]] = []
        if stamps:
            size = max(1, -(-len(stamps) // pages))
            chunks = [stamps[i:i + size] for i in range(0, len(stamps), size)]
        if not chunks:
            chunks = [[]]

        page_records = []
        for i, chunk in enumerate(chunks):
            counter += 1
            page_records.append({
                "page_index": i,
                "provider_request_id": f"{ticker}-{req['request_id']}-{counter}",
                "query_count": len(chunk) * 5,
                "results_count": len(chunk),
                "first_ts": chunk[0] if chunk else None,
                "last_ts": chunk[-1] if chunk else None,
                "next_url_present": i < len(chunks) - 1,
            })

        requests.append({
            "request_id": req["request_id"],
            "slice_start": req["slice_start"],
            "slice_end": req["slice_end"],
            "pages": page_records,
            "page_count": len(page_records),
            "next_url_exhausted": True,
            "complete": True,
            "first_ts": page_records[0]["first_ts"],
            "last_ts": page_records[-1]["last_ts"],
        })

    return {
        "schema": pf.EVIDENCE_SCHEMA,
        "corpus_version": pf.CORPUS_VERSION,
        "symbol": ticker,
        "requests": requests,
    }


def write_evidence(evidence_dir: Path, ticker: str, evidence: dict) -> Path:
    evidence_dir.mkdir(parents=True, exist_ok=True)
    path = pf.evidence_file_for(evidence_dir, ticker)
    path.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
    return path


SHORT_START = date(2025, 3, 3)
SHORT_END = date(2025, 3, 14)


def short_days() -> list[date]:
    return pf.expected_sessions(SHORT_START, SHORT_END)


def validate_short(bars, **kwargs) -> pf.SymbolResult:
    kwargs.setdefault("window_start", SHORT_START)
    kwargs.setdefault("window_end", SHORT_END)
    kwargs.setdefault("require_dst", False)
    return pf.validate_symbol_bars("TEST", bars, **kwargs)


def codes(result) -> set[str]:
    findings = result.findings if hasattr(result, "findings") else result
    return {f.code for f in findings}


@pytest.fixture(scope="session")
def universe():
    return pf.load_universe()


@pytest.fixture(scope="session")
def manifest(universe):
    return pf.build_manifest(universe)


@pytest.fixture(scope="session")
def two_symbols(universe):
    return sorted(universe.tickers)[:2]


@pytest.fixture(scope="session")
def corpus(tmp_path_factory, two_symbols, manifest):
    """Complete, valid bars + evidence over the whole frozen window."""
    root = tmp_path_factory.mktemp("corpus")
    bars_dir, evidence_dir = root / "bars", root / "evidence"
    bars = make_bars(pf.expected_sessions())
    for i, ticker in enumerate(two_symbols):
        write_bars(bars_dir, ticker, bars)
        reqs = [r for r in manifest["requests"] if r["ticker"] == ticker]
        # Exercise both single-page and multi-page evidence.
        write_evidence(evidence_dir, ticker,
                       make_evidence(ticker, bars, reqs, pages=1 + i))
    return bars_dir, evidence_dir


def run(corpus, checkpoint_dir, symbols, **kwargs):
    bars_dir, evidence_dir = corpus
    return pf.run_preflight(
        bars_dir=bars_dir, evidence_dir=evidence_dir,
        checkpoint_dir=checkpoint_dir, symbols=symbols, **kwargs
    )


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------


def test_universe_loads_and_matches_pinned_hashes(universe):
    assert universe.version == pf.UNIVERSE_VERSION == "equity_corpus_v1"
    assert pf.CORPUS_VERSION == "equity_corpus_v2"
    assert universe.universe_sha256 == pf.UNIVERSE_SHA256
    assert universe.source_sha256 == pf.SOURCE_CSV_SHA256
    assert len(universe.entries) == 156
    assert sum(1 for e in universe.entries if e.is_setup_candidate) == 155


def test_v1_preregistration_is_pinned_and_unmodified():
    """Superseded history must stay byte-identical."""
    hashes = pf.verify_preregistration_docs()
    assert hashes["preregistration_v1_sha256"] == pf.PREREG_V1_SHA256
    assert hashes["preregistration_v2_sha256"]


def test_tampering_with_v1_preregistration_is_rejected(tmp_path):
    fake = tmp_path / "v1.md"
    fake.write_text("# not the frozen v1\n")
    with pytest.raises(pf.ProvenanceError, match="v1 preregistration SHA-256"):
        pf.verify_preregistration_docs(v1_path=fake)


def test_editing_v2_preregistration_invalidates_provenance(tmp_path, universe,
                                                           manifest):
    """v2 is binding: changing it must invalidate every checkpoint."""
    base = pf.build_provenance(manifest, universe, doc_hashes={"a": "1"})
    other = pf.build_provenance(manifest, universe, doc_hashes={"a": "2"})
    assert base["config_sha256"] != other["config_sha256"]


def test_altered_universe_is_rejected(tmp_path):
    altered = tmp_path / "universe.json"
    raw = json.loads(Path(pf.UNIVERSE_PATH).read_text())
    raw["entries"][0]["ticker"] = "ZZZZ"
    altered.write_text(json.dumps(raw, indent=2))
    with pytest.raises(pf.ProvenanceError, match="universe SHA-256 mismatch"):
        pf.load_universe(altered)


def test_altered_universe_membership_rejected_even_if_hash_is_repinned(tmp_path):
    altered = tmp_path / "universe.json"
    raw = json.loads(Path(pf.UNIVERSE_PATH).read_text())
    raw["entries"].pop()
    altered.write_text(json.dumps(raw, indent=2))
    with pytest.raises(pf.ProvenanceError, match="expected 156 entries"):
        pf.load_universe(altered, expected_universe_sha256=pf.sha256_file(altered))


def test_altered_source_watchlist_is_rejected(tmp_path):
    bogus = tmp_path / "watchlist.csv"
    bogus.write_text("ticker\nAAPL\n")
    with pytest.raises(pf.ProvenanceError, match="source watchlist SHA-256 mismatch"):
        pf.load_universe(pf.UNIVERSE_PATH, source_csv_path=bogus)


def test_missing_universe_file_is_rejected(tmp_path):
    with pytest.raises(pf.ProvenanceError, match="universe file not found"):
        pf.load_universe(tmp_path / "nope.json")


# ---------------------------------------------------------------------------
# D-1 — calendar-aware sessions (half-day RTH correction)
# ---------------------------------------------------------------------------

HALF_DAY = date(2025, 12, 24)
NORMAL_DAY = date(2025, 12, 23)


def test_half_day_is_in_the_frozen_early_close_table():
    assert HALF_DAY in pf.EARLY_CLOSES
    assert NORMAL_DAY not in pf.EARLY_CLOSES


def test_half_day_afternoon_is_after_hours_not_rth():
    """The exact v1 defect: 13:00-16:00 on a half day is NOT RTH."""
    for minute in (13 * 60, 14 * 60, 15 * 60 + 55):
        ts = _epoch_ms(HALF_DAY, minute)
        assert pf.session_tag(ts) == "AFTER_HOURS"
        assert pf.session_tag_v1_flat(ts) == "RTH", "v1 mislabelled this bar"


def test_half_day_rth_ends_at_1300():
    assert pf.session_tag(_epoch_ms(HALF_DAY, 12 * 60 + 55)) == "RTH"
    assert pf.session_tag(_epoch_ms(HALF_DAY, 13 * 60)) == "AFTER_HOURS"


def test_half_day_extended_session_ends_at_1700():
    assert pf.session_tag(_epoch_ms(HALF_DAY, 16 * 60 + 55)) == "AFTER_HOURS"
    assert pf.session_tag(_epoch_ms(HALF_DAY, 17 * 60)) is None


def test_normal_day_is_unchanged_from_v1():
    for minute in range(pf.PREMARKET_OPEN_MIN, pf.EXT_CLOSE_MIN, 5):
        ts = _epoch_ms(NORMAL_DAY, minute)
        assert pf.session_tag(ts) == pf.session_tag_v1_flat(ts), minute


def test_v1_and_v2_differ_only_on_early_close_sessions():
    """Bound the blast radius of the correction: 5 sessions, nothing else."""
    differing = set()
    for day in pf.expected_sessions():
        for minute in range(0, 24 * 60, 5):
            ts = _epoch_ms(day, minute)
            if pf.session_tag(ts) != pf.session_tag_v1_flat(ts):
                differing.add(day)
    assert differing == set(pf.EARLY_CLOSES)


def test_half_day_expected_rth_grid_is_shorter():
    assert len(pf.expected_grid(HALF_DAY, "RTH")) == (13 * 60 - 570) // 5   # 42
    assert len(pf.expected_grid(NORMAL_DAY, "RTH")) == (16 * 60 - 570) // 5  # 78


def test_closed_day_has_no_bounds_and_no_grid():
    saturday = date(2025, 12, 27)
    assert saturday.weekday() == 5
    assert pf.session_bounds(saturday) is None
    assert pf.expected_grid(saturday, "RTH") == []
    assert pf.session_bounds(date(2025, 12, 25)) is None  # Christmas


def test_bar_after_half_day_close_fails():
    days = pf.expected_sessions(date(2025, 12, 22), date(2025, 12, 31))
    bars = make_bars(days)
    ts = _epoch_ms(HALF_DAY, 18 * 60)
    bars.append({"t": ts, "o": 1, "h": 1, "l": 1, "c": 1, "session": "AFTER_HOURS"})
    bars.sort(key=lambda b: b["t"])
    result = pf.validate_symbol_bars(
        "TEST", bars, window_start=date(2025, 12, 22), window_end=date(2025, 12, 31),
        require_dst=False,
    )
    assert result.status == "FAIL"
    assert "OUTSIDE_SESSION_HOURS" in codes(result)


def test_half_day_bar_tagged_rth_by_v1_rules_is_rejected():
    """A corpus built under v1 tagging must not pass v2 validation."""
    days = pf.expected_sessions(date(2025, 12, 22), date(2025, 12, 31))
    bars = make_bars(days)
    for bar in bars:
        if pf._et_datetime(bar["t"]).date() == HALF_DAY and \
                pf.session_tag_v1_flat(bar["t"]) == "RTH":
            bar["session"] = "RTH"
    result = pf.validate_symbol_bars(
        "TEST", bars, window_start=date(2025, 12, 22), window_end=date(2025, 12, 31),
        require_dst=False,
    )
    assert result.status == "FAIL"
    assert "INVALID_SESSION_TAG" in codes(result)


def test_session_tag_matches_the_smoke_harness_on_ordinary_days():
    """Parity guard for the deliberate duplication, on days v2 did not change."""
    pytest.importorskip("httpx")
    from research import equity_corpus_smoke as smoke

    day = date(2025, 6, 11)
    assert day not in pf.EARLY_CLOSES
    for minute in range(0, 24 * 60, 5):
        ts = _epoch_ms(day, minute)
        assert pf.session_tag(ts) == smoke.session_tag(ts), minute


# ---------------------------------------------------------------------------
# D-2 — real bar coverage
# ---------------------------------------------------------------------------


def test_clean_bars_pass():
    result = validate_short(make_bars(short_days()))
    assert result.status == "PASS", result.findings
    assert result.coverage_totals["RTH"]["missing"] == 0
    assert result.coverage_sha256


def test_single_bar_per_session_no_longer_passes():
    """The exact v1 defect: session presence is not coverage."""
    bars = []
    for day in short_days():
        ts = pf.expected_grid(day, "RTH")[0]
        bars.append({"t": ts, "o": 1, "h": 1, "l": 1, "c": 1, "session": "RTH"})
    result = validate_short(bars)
    assert result.status == "FAIL"
    assert "MISSING_RTH_INTERVAL" in codes(result)
    # Every session is "present", which is precisely why v1 accepted this.
    assert "MISSING_SESSION" not in codes(result)


def test_single_missing_rth_interval_is_detected():
    bars = make_bars(short_days())
    victim = next(b for b in bars if b["session"] == "RTH")
    bars.remove(victim)
    result = validate_short(bars)
    assert result.status == "FAIL"
    finding = next(f for f in result.findings if f.code == "MISSING_RTH_INTERVAL")
    assert finding.count == 1
    assert pf._et_datetime(victim["t"]).isoformat() in finding.detail


def test_one_hour_rth_hole_is_detected_with_exact_count():
    days = short_days()
    grid = pf.expected_grid(days[2], "RTH")
    hole = set(grid[10:22])  # 12 consecutive 5-minute intervals
    bars = [b for b in make_bars(days) if b["t"] not in hole]
    result = validate_short(bars)
    finding = next(f for f in result.findings if f.code == "MISSING_RTH_INTERVAL")
    assert finding.count == 12


def test_extended_hours_gaps_are_published_but_do_not_fail_equities():
    """Frozen policy: extended sparsity is expected, enumerated, not a failure."""
    bars = make_bars(short_days())
    premarket = [b for b in bars if b["session"] == "PREMARKET"]
    dropped = {b["t"] for b in premarket[:25]}
    bars = [b for b in bars if b["t"] not in dropped]
    result = validate_short(bars)
    assert result.status == "PASS", result.findings
    assert result.coverage_totals["PREMARKET"]["missing"] == 25
    assert result.coverage_totals["RTH"]["missing"] == 0


def test_index_policy_does_not_fail_on_missing_rth_intervals():
    """Index values publish on update, not on trade."""
    bars = make_bars(short_days())
    rth = [b for b in bars if b["session"] == "RTH"]
    dropped = {b["t"] for b in rth[:40]}
    bars = [b for b in bars if b["t"] not in dropped]
    result = validate_short(bars, policy=pf.POLICY_INDEX)
    assert result.status == "PASS", result.findings
    assert result.coverage_totals["RTH"]["missing"] == 40


def test_index_cohort_maps_to_the_index_policy(universe):
    assert pf.policy_for("index") is pf.POLICY_INDEX
    for cohort in ("single_name", "etf", "leveraged_inverse"):
        assert pf.policy_for(cohort) is pf.POLICY_EQUITY
    assert universe.by_ticker("VIX").cohort == "index"


def test_rth_exception_table_is_empty_at_freeze():
    assert pf.RTH_EXCEPTIONS == {}


def test_coverage_rows_carry_per_session_counts():
    rows, totals = pf.compute_coverage(
        make_bars(short_days()), window_start=SHORT_START, window_end=SHORT_END
    )
    assert len(rows) == len(short_days()) * 3
    for row in rows:
        assert set(row) == {"date", "tag", "expected", "observed", "missing",
                            "duplicate"}
    assert totals["RTH"]["expected"] == sum(
        len(pf.expected_grid(d, "RTH")) for d in short_days()
    )


def test_coverage_hash_changes_when_a_single_bar_moves():
    days = short_days()
    a, _ = pf.compute_coverage(make_bars(days), window_start=SHORT_START,
                               window_end=SHORT_END)
    bars = make_bars(days)
    bars.pop(50)
    b, _ = pf.compute_coverage(bars, window_start=SHORT_START, window_end=SHORT_END)
    assert pf.coverage_sha256(a) != pf.coverage_sha256(b)


# ---------------------------------------------------------------------------
# D-3 — no one-page pagination assumption
# ---------------------------------------------------------------------------


def test_no_one_page_invariant_exists_anywhere():
    """The false invariant and its constant must be gone, not merely relaxed."""
    assert not hasattr(pf, "PROVIDER_PAGE_LIMIT")
    source = Path(pf.__file__).read_text()
    assert "one logical request is expected to be exactly one physical page" \
        not in source


def test_manifest_counts_logical_requests_only(manifest):
    assert manifest["logical_request_count"] == 156 * 9
    assert manifest["logical_requests_per_symbol"] == 9
    assert "physical page count is provider-determined" in manifest["pagination"]
    assert "request_count" not in manifest, "ambiguous key must not return"


def test_quarter_slices_tile_the_window_without_gap_or_overlap():
    slices = pf.quarter_slices()
    assert slices[0][0] == pf.WINDOW_START
    assert slices[-1][1] == pf.WINDOW_END
    for (_, end_a), (start_b, _) in zip(slices, slices[1:]):
        assert start_b == end_a + timedelta(days=1)


def test_a_quarter_slice_exceeds_the_row_limit_in_base_aggregates():
    """Documents why one-page completion was never provable."""
    for start, end in pf.quarter_slices()[1:-1]:
        sessions = pf.expected_sessions(start, end)
        base_1m = sum(
            (pf.session_bounds(d).ext_close - pf.session_bounds(d).premarket_open)
            for d in sessions
        )
        assert base_1m > 50_000


def test_request_ids_are_stable_and_field_sensitive():
    base = dict(
        symbol="AAPL", endpoint=pf.ENDPOINT, adjustment=pf.ADJUSTMENT,
        timeframe=pf.TIMEFRAME, slice_start=date(2025, 1, 1),
        slice_end=date(2025, 3, 31),
    )
    rid = pf.request_id(**base)
    assert rid == pf.request_id(**base)
    for field, value in (
        ("symbol", "MSFT"), ("adjustment", "adjusted=false"), ("timeframe", "15m"),
        ("slice_start", date(2025, 1, 2)), ("slice_end", date(2025, 4, 1)),
        ("corpus_version", "equity_corpus_v1"),
    ):
        assert pf.request_id(**{**base, field: value}) != rid, field


def test_v2_request_ids_differ_from_v1(manifest):
    """v2 artefacts can never be confused with v1 artefacts."""
    slice_start, slice_end = pf.quarter_slices()[0]
    common = dict(symbol="AAPL", endpoint=pf.ENDPOINT, adjustment=pf.ADJUSTMENT,
                  timeframe=pf.TIMEFRAME, slice_start=slice_start,
                  slice_end=slice_end)
    assert pf.request_id(**common, corpus_version="equity_corpus_v1") != \
        pf.request_id(**common, corpus_version="equity_corpus_v2")


def test_index_entry_gets_the_index_namespace(manifest):
    vix = [r for r in manifest["requests"] if r["ticker"] == "VIX"]
    assert vix
    assert all(r["provider_ticker"] == "I:VIX" for r in vix)
    assert all(r["role"] == "regime_input_only" for r in vix)
    assert all(r["coverage_policy"] == "index_enumerate_only" for r in vix)


def test_manifest_is_byte_deterministic(universe):
    a = pf.canonical_json(pf.build_manifest(universe))
    b = pf.canonical_json(pf.build_manifest(universe))
    assert a == b


# --- evidence validation ---------------------------------------------------


@pytest.fixture
def evidence_case(manifest, two_symbols):
    """A small but real symbol/bars/evidence triple for evidence unit tests."""
    ticker = two_symbols[0]
    reqs = [r for r in manifest["requests"] if r["ticker"] == ticker]
    bars = make_bars(pf.expected_sessions(date(2024, 8, 1), date(2024, 8, 9)))
    return ticker, bars, reqs


def check_evidence(evidence_case, evidence):
    ticker, bars, reqs = evidence_case
    return pf.validate_fetch_evidence(
        ticker, evidence, request_ids=[r["request_id"] for r in reqs],
        manifest_requests=reqs, bars=bars,
    )


def test_single_page_evidence_validates(evidence_case):
    ticker, bars, reqs = evidence_case
    assert check_evidence(evidence_case,
                          make_evidence(ticker, bars, reqs, pages=1)) == []


def test_multi_page_evidence_validates(evidence_case):
    """Pagination is supported, not assumed away."""
    ticker, bars, reqs = evidence_case
    ev = make_evidence(ticker, bars, reqs, pages=4)
    populated = [r for r in ev["requests"] if r["pages"][0]["results_count"]]
    assert any(r["page_count"] > 1 for r in populated)
    assert check_evidence(evidence_case, ev) == []


def test_final_page_still_reporting_next_url_fails(evidence_case):
    ticker, bars, reqs = evidence_case
    ev = make_evidence(ticker, bars, reqs)
    ev["requests"][0]["pages"][-1]["next_url_present"] = True
    assert "NEXT_URL_NOT_EXHAUSTED" in codes(check_evidence(evidence_case, ev))


def test_missing_next_url_exhausted_flag_fails(evidence_case):
    ticker, bars, reqs = evidence_case
    ev = make_evidence(ticker, bars, reqs)
    ev["requests"][0]["next_url_exhausted"] = False
    assert "NEXT_URL_NOT_EXHAUSTED" in codes(check_evidence(evidence_case, ev))


def test_missing_completion_marker_fails(evidence_case):
    ticker, bars, reqs = evidence_case
    ev = make_evidence(ticker, bars, reqs)
    ev["requests"][0].pop("complete")
    assert "INCOMPLETE_REQUEST" in codes(check_evidence(evidence_case, ev))


def test_page_index_gap_fails(evidence_case):
    ticker, bars, reqs = evidence_case
    ev = make_evidence(ticker, bars, reqs, pages=3)
    target = next(r for r in ev["requests"] if r["page_count"] >= 3)
    target["pages"][1]["page_index"] = 7
    assert "PAGE_INDEX_GAP" in codes(check_evidence(evidence_case, ev))


def test_duplicate_provider_request_id_fails(evidence_case):
    ticker, bars, reqs = evidence_case
    ev = make_evidence(ticker, bars, reqs, pages=2)
    target = next(r for r in ev["requests"] if r["page_count"] >= 2)
    target["pages"][1]["provider_request_id"] = target["pages"][0][
        "provider_request_id"]
    assert "DUPLICATE_PAGE" in codes(check_evidence(evidence_case, ev))


def test_missing_provider_request_id_fails(evidence_case):
    ticker, bars, reqs = evidence_case
    ev = make_evidence(ticker, bars, reqs)
    ev["requests"][0]["pages"][0]["provider_request_id"] = ""
    assert "MISSING_PROVIDER_REQUEST_ID" in codes(check_evidence(evidence_case, ev))


@pytest.mark.parametrize("key", ["query_count", "results_count"])
def test_missing_provider_counts_fail(evidence_case, key):
    ticker, bars, reqs = evidence_case
    ev = make_evidence(ticker, bars, reqs)
    ev["requests"][0]["pages"][0].pop(key)
    assert "MISSING_PROVIDER_COUNTS" in codes(check_evidence(evidence_case, ev))


def test_overlapping_pages_fail(evidence_case):
    ticker, bars, reqs = evidence_case
    ev = make_evidence(ticker, bars, reqs, pages=2)
    target = next(r for r in ev["requests"] if r["page_count"] >= 2)
    target["pages"][1]["first_ts"] = target["pages"][0]["first_ts"]
    assert "OVERLAPPING_PAGES" in codes(check_evidence(evidence_case, ev))


def test_evidence_bar_count_mismatch_fails(evidence_case):
    ticker, bars, reqs = evidence_case
    ev = make_evidence(ticker, bars, reqs)
    populated = next(r for r in ev["requests"] if r["pages"][0]["results_count"])
    populated["pages"][0]["results_count"] += 1
    assert "EVIDENCE_BAR_COUNT_MISMATCH" in codes(check_evidence(evidence_case, ev))


def test_evidence_request_set_mismatch_fails(evidence_case):
    ticker, bars, reqs = evidence_case
    ev = make_evidence(ticker, bars, reqs)
    ev["requests"].pop()
    assert "EVIDENCE_REQUEST_SET_MISMATCH" in codes(check_evidence(evidence_case, ev))


def test_evidence_schema_mismatch_fails(evidence_case):
    ticker, bars, reqs = evidence_case
    ev = make_evidence(ticker, bars, reqs)
    ev["schema"] = "something/else"
    assert "EVIDENCE_SCHEMA_MISMATCH" in codes(check_evidence(evidence_case, ev))


def test_evidence_declared_span_must_match_pages(evidence_case):
    ticker, bars, reqs = evidence_case
    ev = make_evidence(ticker, bars, reqs)
    populated = next(r for r in ev["requests"] if r["pages"][0]["results_count"])
    populated["last_ts"] = 1
    assert "EVIDENCE_TIMESTAMP_MISMATCH" in codes(check_evidence(evidence_case, ev))


def test_truncated_evidence_file_is_rejected(tmp_path):
    path = pf.evidence_file_for(tmp_path, "TEST")
    tmp_path.mkdir(parents=True, exist_ok=True)
    path.write_text('{"schema": "x", "requests": [')
    with pytest.raises(pf.EvidenceFileError, match="truncated"):
        pf.load_evidence(path)


def test_missing_evidence_file_is_rejected(tmp_path):
    with pytest.raises(pf.EvidenceFileError, match="not found"):
        pf.load_evidence(pf.evidence_file_for(tmp_path, "NOPE"))


# ---------------------------------------------------------------------------
# Ordinary bar gates
# ---------------------------------------------------------------------------


def test_duplicate_timestamp_fails():
    bars = make_bars(short_days())
    bars.insert(5, dict(bars[4]))
    assert "DUPLICATE_TIMESTAMP" in codes(validate_short(bars))


def test_out_of_order_timestamp_fails():
    bars = make_bars(short_days())
    bars[10], bars[11] = bars[11], bars[10]
    assert "OUT_OF_ORDER" in codes(validate_short(bars))


def test_out_of_window_bar_fails():
    bars = make_bars(short_days())
    ts = _epoch_ms(SHORT_START - timedelta(days=7), pf.RTH_OPEN_MIN)
    bars.insert(0, {"t": ts, "o": 1, "h": 1, "l": 1, "c": 1, "session": "RTH"})
    assert "OUT_OF_WINDOW" in codes(validate_short(bars))


def test_missing_session_tag_fails():
    bars = make_bars(short_days())
    bars[3].pop("session")
    assert "INVALID_SESSION_TAG" in codes(validate_short(bars))


def test_unknown_session_tag_fails():
    bars = make_bars(short_days())
    bars[0]["session"] = "OVERNIGHT"
    assert "INVALID_SESSION_TAG" in codes(validate_short(bars))


def test_misaligned_five_minute_bar_fails():
    bars = make_bars(short_days())
    bars[7]["t"] += 60_000
    assert "MISALIGNED_INTERVAL" in codes(validate_short(bars))


def test_missing_expected_session_fails():
    days = short_days()
    dropped = days[3]
    bars = make_bars([d for d in days if d != dropped])
    result = validate_short(bars)
    assert "MISSING_SESSION" in codes(result)
    assert dropped.isoformat() in "".join(f.detail for f in result.findings)


def test_weekend_bar_fails():
    saturday = SHORT_START + timedelta(days=5)
    assert saturday.weekday() == 5
    bars = make_bars(short_days())
    ts = _epoch_ms(saturday, pf.RTH_OPEN_MIN)
    bars.append({"t": ts, "o": 1, "h": 1, "l": 1, "c": 1, "session": "RTH"})
    bars.sort(key=lambda b: b["t"])
    result = validate_short(bars)
    assert "EXTRA_SESSION" in codes(result)


def test_empty_bar_list_fails():
    assert "NO_BARS" in codes(validate_short([]))


def test_fixed_utc_offset_timestamps_fail_the_dst_gate():
    days = pf.expected_sessions(date(2025, 1, 2), date(2025, 1, 10))
    result = pf.validate_symbol_bars(
        "TEST", make_bars(days), window_start=date(2025, 1, 2),
        window_end=date(2025, 1, 10), require_dst=True,
    )
    assert "DST_NOT_OBSERVED" in codes(result)


def test_dst_is_observed_across_a_transition():
    days = pf.expected_sessions(date(2025, 3, 5), date(2025, 3, 20))
    result = pf.validate_symbol_bars(
        "TEST", make_bars(days), window_start=date(2025, 3, 5),
        window_end=date(2025, 3, 20), require_dst=True,
    )
    assert result.status == "PASS", result.findings
    assert len(result.distinct_utc_offsets) == 2


# ---------------------------------------------------------------------------
# Bar file loading
# ---------------------------------------------------------------------------


def test_truncated_bar_file_is_rejected(tmp_path):
    write_bars(tmp_path, "TEST", make_bars(short_days()), truncate=True)
    with pytest.raises(pf.BarFileError, match="truncated"):
        pf.load_bars_jsonl(pf.bar_file_for(tmp_path, "TEST"))


def test_malformed_bar_line_is_rejected(tmp_path):
    path = write_bars(tmp_path, "TEST", make_bars(short_days()))
    path.write_text(path.read_text() + "{not json\n")
    with pytest.raises(pf.BarFileError, match="not valid JSON"):
        pf.load_bars_jsonl(path)


def test_bar_missing_required_key_is_rejected(tmp_path):
    path = write_bars(tmp_path, "TEST", make_bars(short_days()))
    path.write_text(path.read_text() + json.dumps({"t": 1, "o": 1}) + "\n")
    with pytest.raises(pf.BarFileError, match="missing keys"):
        pf.load_bars_jsonl(path)


def test_missing_bar_file_is_rejected(tmp_path):
    with pytest.raises(pf.BarFileError, match="bar file not found"):
        pf.load_bars_jsonl(pf.bar_file_for(tmp_path, "NOPE"))


# ---------------------------------------------------------------------------
# D-4 — checkpoints bound to the corpus bytes
# ---------------------------------------------------------------------------


@pytest.fixture
def provenance(universe, manifest):
    return pf.build_provenance(manifest, universe,
                               doc_hashes=pf.verify_preregistration_docs())


@pytest.fixture
def checkpoint_case(tmp_path, provenance):
    """A written checkpoint plus everything needed to verify it."""
    bars = make_bars(short_days())
    bars_dir = tmp_path / "bars"
    path = write_bars(bars_dir, "AAPL", bars)
    digest = pf.digest_bar_file(path, bars)
    rows, _ = pf.compute_coverage(bars, window_start=SHORT_START,
                                  window_end=SHORT_END)
    cov = pf.coverage_sha256(rows)

    result = pf.SymbolResult(ticker="AAPL", status="PASS", bar_count=len(bars),
                             session_count=len(short_days()), coverage_sha256=cov)
    store = pf.CheckpointStore(tmp_path / "ck")
    store.write(result, provenance=provenance, request_ids=["a", "b"],
                bar_digest=digest, evidence_sha256="ev-sha", evidence_page_count=3)
    return store, path, digest, cov, provenance


def verify_case(case, **overrides):
    store, path, digest, cov, provenance = case
    kwargs = dict(provenance=provenance, request_ids=["a", "b"],
                  bar_digest=digest, coverage_sha=cov, evidence_sha256="ev-sha")
    kwargs.update(overrides)
    return store.verify("AAPL", **kwargs)


def test_checkpoint_roundtrip(checkpoint_case):
    payload = verify_case(checkpoint_case)
    assert payload is not None
    assert payload["status"] == pf.STATUS_COMPLETE
    assert payload["bar_file"]["sha256"]
    assert payload["fetch_evidence"]["page_count"] == 3


def test_absent_checkpoint_returns_none(tmp_path, provenance):
    store = pf.CheckpointStore(tmp_path)
    digest = pf.BarFileDigest("x", 1, 1, 1, 2)
    assert store.verify("AAPL", provenance=provenance, request_ids=["a"],
                        bar_digest=digest, coverage_sha="c",
                        evidence_sha256="e") is None


def test_failed_symbol_is_never_checkpointed(tmp_path, provenance):
    store = pf.CheckpointStore(tmp_path)
    with pytest.raises(pf.CheckpointError, match="refusing to checkpoint"):
        store.write(pf.SymbolResult(ticker="AAPL", status="FAIL"),
                    provenance=provenance, request_ids=["a"],
                    bar_digest=pf.BarFileDigest("x", 1, 1, 1, 2),
                    evidence_sha256="e", evidence_page_count=1)


def test_checkpoint_write_is_atomic(checkpoint_case):
    store = checkpoint_case[0]
    assert [p for p in store.root.iterdir() if p.suffix == ".tmp"] == []
    assert json.loads(store.path_for("AAPL").read_text())["payload_sha256"]


def test_bar_file_digest_mismatch_is_rejected(checkpoint_case):
    """A different sha256 for the same row count must still be caught."""
    _, _, digest, _, _ = checkpoint_case
    tampered = pf.BarFileDigest("0" * 64, digest.size_bytes, digest.row_count,
                                digest.first_ts, digest.last_ts)
    with pytest.raises(pf.CheckpointError, match="does not match the bar file"):
        verify_case(checkpoint_case, bar_digest=tampered)


def test_coverage_hash_mismatch_is_rejected(checkpoint_case):
    with pytest.raises(pf.CheckpointError, match="stale coverage hash"):
        verify_case(checkpoint_case, coverage_sha="0" * 64)


def test_evidence_hash_mismatch_is_rejected(checkpoint_case):
    with pytest.raises(pf.CheckpointError, match="fetch-evidence file"):
        verify_case(checkpoint_case, evidence_sha256="different")


def test_corrupted_checkpoint_json_is_rejected(checkpoint_case):
    store = checkpoint_case[0]
    store.path_for("AAPL").write_text("{ this is not json")
    with pytest.raises(pf.CheckpointError, match="corrupted"):
        verify_case(checkpoint_case)


def test_tampered_checkpoint_body_is_rejected(checkpoint_case):
    store = checkpoint_case[0]
    payload = json.loads(store.path_for("AAPL").read_text())
    payload["summary"]["bar_count"] = 999_999
    store.path_for("AAPL").write_text(json.dumps(payload))
    with pytest.raises(pf.CheckpointError, match="payload_sha256 does not match"):
        verify_case(checkpoint_case)


def test_incomplete_checkpoint_is_rejected(checkpoint_case):
    store = checkpoint_case[0]
    payload = json.loads(store.path_for("AAPL").read_text())
    body = {k: v for k, v in payload.items() if k != "payload_sha256"}
    body["status"] = "in_progress"
    body["payload_sha256"] = pf.sha256_obj(body)
    store.path_for("AAPL").write_text(pf.canonical_json(body))
    with pytest.raises(pf.CheckpointError, match="incomplete"):
        verify_case(checkpoint_case)


def test_checkpoint_without_payload_hash_is_rejected(checkpoint_case):
    store = checkpoint_case[0]
    payload = json.loads(store.path_for("AAPL").read_text())
    payload.pop("payload_sha256")
    store.path_for("AAPL").write_text(json.dumps(payload))
    with pytest.raises(pf.CheckpointError, match="no payload_sha256"):
        verify_case(checkpoint_case)


@pytest.mark.parametrize("key", ["bar_file", "coverage_sha256", "fetch_evidence"])
def test_checkpoint_missing_a_binding_field_is_rejected(checkpoint_case, key):
    """A v1-shaped checkpoint must not be resumable under v2."""
    store = checkpoint_case[0]
    payload = json.loads(store.path_for("AAPL").read_text())
    body = {k: v for k, v in payload.items() if k not in ("payload_sha256", key)}
    body["payload_sha256"] = pf.sha256_obj(body)
    store.path_for("AAPL").write_text(pf.canonical_json(body))
    with pytest.raises(pf.CheckpointError, match="incomplete"):
        verify_case(checkpoint_case)


@pytest.mark.parametrize("key", pf.CheckpointStore.PROVENANCE_KEYS)
def test_each_provenance_hash_mismatch_is_rejected(checkpoint_case, key):
    _, _, _, _, provenance = checkpoint_case
    with pytest.raises(pf.CheckpointError, match=f"stale {key}"):
        verify_case(checkpoint_case, provenance={**provenance, key: "0" * 64})


def test_checkpoint_for_a_different_request_set_is_rejected(checkpoint_case):
    with pytest.raises(pf.CheckpointError, match="different request set"):
        verify_case(checkpoint_case, request_ids=["a", "c"])


# --- the operator's six-step byte-binding regression -----------------------


def _complete_and_checkpoint(tmp_path, corpus, two_symbols):
    ck = tmp_path / "ck"
    report = run(corpus, ck, two_symbols)
    assert report["status"] == "PASS", report["findings_by_code"]
    return ck


def test_resume_fails_closed_when_bar_file_is_deleted(tmp_path, corpus, two_symbols):
    ck = _complete_and_checkpoint(tmp_path, corpus, two_symbols)
    pf.bar_file_for(corpus[0], two_symbols[0]).unlink()
    try:
        report = run(corpus, ck, two_symbols)
        assert report["status"] == "FAIL"
        assert "BAR_FILE_UNUSABLE" in report["findings_by_code"]
        assert two_symbols[0] in report["failed_symbols"]
    finally:
        write_bars(corpus[0], two_symbols[0], make_bars(pf.expected_sessions()))


def test_resume_fails_closed_when_a_bar_is_altered(tmp_path, corpus, two_symbols):
    ck = _complete_and_checkpoint(tmp_path, corpus, two_symbols)
    path = pf.bar_file_for(corpus[0], two_symbols[0])
    original = path.read_text()
    try:
        lines = original.splitlines()
        bar = json.loads(lines[500])
        bar["c"] = bar["c"] + 7.5
        lines[500] = json.dumps(bar)
        path.write_text("\n".join(lines) + "\n")

        report = run(corpus, ck, two_symbols)
        assert report["status"] == "FAIL"
        assert "CHECKPOINT_REJECTED" in report["findings_by_code"]
    finally:
        path.write_text(original)


def test_resume_fails_closed_when_bar_file_is_truncated(tmp_path, corpus,
                                                        two_symbols):
    ck = _complete_and_checkpoint(tmp_path, corpus, two_symbols)
    path = pf.bar_file_for(corpus[0], two_symbols[0])
    original = path.read_text()
    try:
        path.write_text(original[: len(original) - 12])
        report = run(corpus, ck, two_symbols)
        assert report["status"] == "FAIL"
        assert "BAR_FILE_UNUSABLE" in report["findings_by_code"]
    finally:
        path.write_text(original)


def test_resume_fails_closed_when_two_lines_are_reordered(tmp_path, corpus,
                                                          two_symbols):
    """Same bytes, same row count, same endpoints — only the order changed."""
    ck = _complete_and_checkpoint(tmp_path, corpus, two_symbols)
    path = pf.bar_file_for(corpus[0], two_symbols[0])
    original = path.read_text()
    try:
        lines = original.splitlines()
        lines[300], lines[301] = lines[301], lines[300]
        path.write_text("\n".join(lines) + "\n")

        report = run(corpus, ck, two_symbols)
        assert report["status"] == "FAIL"
        assert "CHECKPOINT_REJECTED" in report["findings_by_code"]
    finally:
        path.write_text(original)


def test_resume_fails_closed_when_evidence_file_is_deleted(tmp_path, corpus,
                                                           two_symbols):
    ck = _complete_and_checkpoint(tmp_path, corpus, two_symbols)
    path = pf.evidence_file_for(corpus[1], two_symbols[0])
    original = path.read_text()
    path.unlink()
    try:
        report = run(corpus, ck, two_symbols)
        assert report["status"] == "FAIL"
        assert "EVIDENCE_FILE_UNUSABLE" in report["findings_by_code"]
    finally:
        path.write_text(original)


def test_resume_fails_closed_when_evidence_is_altered(tmp_path, corpus, two_symbols):
    ck = _complete_and_checkpoint(tmp_path, corpus, two_symbols)
    path = pf.evidence_file_for(corpus[1], two_symbols[0])
    original = path.read_text()
    try:
        payload = json.loads(original)
        payload["requests"][0]["pages"][0]["query_count"] += 1
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        report = run(corpus, ck, two_symbols)
        assert report["status"] == "FAIL"
        assert "CHECKPOINT_REJECTED" in report["findings_by_code"]
    finally:
        path.write_text(original)


# ---------------------------------------------------------------------------
# End-to-end runner
# ---------------------------------------------------------------------------


def test_full_run_passes_and_checkpoints(tmp_path, corpus, two_symbols):
    ck = tmp_path / "ck"
    report = run(corpus, ck, two_symbols)
    assert report["status"] == "PASS", report["findings_by_code"]
    assert report["exit_code"] == 0
    assert report["counts"]["passed"] == len(two_symbols)
    assert report["counts"]["resumed"] == 0
    assert report["authorizes_corpus_batch"] is False
    assert report["corpus_version"] == "equity_corpus_v2"
    assert report["supersedes"] == "equity_corpus_v1"
    for ticker in two_symbols:
        assert (ck / f"{ticker}.json").is_file()


def test_missing_symbol_file_fails_closed(tmp_path, corpus, two_symbols, universe):
    absent = sorted(set(universe.tickers) - set(two_symbols))[0]
    report = run(corpus, tmp_path / "ck", [*two_symbols, absent])
    assert report["status"] == "FAIL"
    assert report["exit_code"] == 1
    assert absent in report["failed_symbols"]
    assert "BAR_FILE_UNUSABLE" in report["findings_by_code"]


def test_resume_skips_revalidation_but_still_reads_the_bytes(tmp_path, corpus,
                                                             two_symbols):
    ck = tmp_path / "ck"
    run(corpus, ck, two_symbols)
    second = run(corpus, ck, two_symbols)
    assert second["status"] == "PASS"
    assert second["counts"]["resumed"] == len(two_symbols)


def test_no_resume_revalidates_everything(tmp_path, corpus, two_symbols):
    ck = tmp_path / "ck"
    run(corpus, ck, two_symbols)
    second = run(corpus, ck, two_symbols, resume=False)
    assert second["counts"]["resumed"] == 0
    assert second["status"] == "PASS"


def test_interrupted_run_resumes_deterministically(tmp_path, corpus, two_symbols):
    first, second = two_symbols
    ck = tmp_path / "ck"

    partial = run(corpus, ck, [first])
    assert partial["status"] == "PASS"
    assert (ck / f"{first}.json").is_file()
    assert not (ck / f"{second}.json").is_file()

    resumed = run(corpus, ck, two_symbols)
    uninterrupted = run(corpus, tmp_path / "ck2", two_symbols)

    assert resumed["status"] == uninterrupted["status"] == "PASS"
    assert resumed["counts"]["resumed"] == 1

    def normalize(report):
        stripped = json.loads(json.dumps(report))
        stripped["counts"]["resumed"] = 0
        for sym in stripped["symbols"].values():
            sym["resumed"] = False
        return stripped

    assert normalize(resumed) == normalize(uninterrupted)


def test_stale_provenance_fails_the_run(tmp_path, corpus, two_symbols):
    ck = tmp_path / "ck"
    run(corpus, ck, two_symbols)

    target = ck / f"{two_symbols[0]}.json"
    payload = json.loads(target.read_text())
    body = {k: v for k, v in payload.items() if k != "payload_sha256"}
    body["provenance"]["code_sha256"] = "0" * 64
    body["payload_sha256"] = pf.sha256_obj(body)
    target.write_text(pf.canonical_json(body))

    report = run(corpus, ck, two_symbols)
    assert report["status"] == "FAIL"
    assert "CHECKPOINT_REJECTED" in report["findings_by_code"]


def test_corrupt_checkpoint_fails_the_run(tmp_path, corpus, two_symbols):
    ck = tmp_path / "ck"
    run(corpus, ck, two_symbols)
    (ck / f"{two_symbols[0]}.json").write_text("{ corrupted")
    report = run(corpus, ck, two_symbols)
    assert report["status"] == "FAIL"
    assert "CHECKPOINT_REJECTED" in report["findings_by_code"]


def test_bad_evidence_fails_the_run_and_leaves_no_checkpoint(tmp_path, corpus,
                                                             two_symbols, manifest):
    bars_dir, evidence_dir = corpus
    ticker = two_symbols[0]
    path = pf.evidence_file_for(evidence_dir, ticker)
    original = path.read_text()
    ck = tmp_path / "ck"
    try:
        payload = json.loads(original)
        payload["requests"][0]["pages"][-1]["next_url_present"] = True
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")

        report = run(corpus, ck, [ticker])
        assert report["status"] == "FAIL"
        assert "NEXT_URL_NOT_EXHAUSTED" in report["findings_by_code"]
        assert not (ck / f"{ticker}.json").exists()
    finally:
        path.write_text(original)


def test_unknown_symbol_is_rejected(tmp_path, corpus):
    with pytest.raises(pf.ProvenanceError, match="not in the frozen universe"):
        run(corpus, tmp_path / "ck", ["NOT_A_REAL_TICKER"])


def test_report_is_deterministic_and_carries_no_wall_clock(tmp_path, corpus,
                                                           two_symbols):
    a = run(corpus, tmp_path / "a", two_symbols)
    b = run(corpus, tmp_path / "b", two_symbols)
    assert pf.canonical_json(a) == pf.canonical_json(b)
    assert "generated_at" not in pf.canonical_json(a)


def test_report_never_reports_complete_with_warnings(tmp_path, corpus, two_symbols):
    bars_dir, _ = corpus
    ticker = two_symbols[0]
    path = pf.bar_file_for(bars_dir, ticker)
    original = path.read_text()
    try:
        lines = original.splitlines()
        bar = json.loads(lines[500])
        bar["session"] = "OVERNIGHT"
        lines[500] = json.dumps(bar)
        path.write_text("\n".join(lines) + "\n")

        report = run(corpus, tmp_path / "ck", [ticker])
        assert report["status"] == "FAIL"
        assert report["exit_code"] != 0
        assert report["counts"]["passed"] == 0
    finally:
        path.write_text(original)


def test_report_publishes_policies_and_exceptions(tmp_path, corpus, two_symbols):
    report = run(corpus, tmp_path / "ck", two_symbols)
    assert report["coverage_policies"]["equity_rth_strict"][
        "rth_requires_full_grid"] is True
    assert report["coverage_policies"]["index_enumerate_only"][
        "rth_requires_full_grid"] is False
    assert report["rth_exceptions"] == {}
    assert report["counts"]["early_close_sessions"] == 5
    for sym in report["symbols"].values():
        assert sym["coverage_totals"]["RTH"]["missing"] == 0
        assert sym["coverage_sha256"]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def cli(corpus, tmp_path, symbols, *extra):
    bars_dir, evidence_dir = corpus
    return pf.main([
        "--bars-dir", str(bars_dir),
        "--evidence-dir", str(evidence_dir),
        "--checkpoint-dir", str(tmp_path / "ck"),
        "--symbols", ",".join(symbols),
        *extra,
    ])


def test_cli_exits_zero_on_pass_and_writes_artifacts(tmp_path, corpus, two_symbols,
                                                     capsys):
    report_path, manifest_path = tmp_path / "report.json", tmp_path / "manifest.json"
    rc = cli(corpus, tmp_path, two_symbols,
             "--report", str(report_path), "--manifest-out", str(manifest_path))
    assert rc == 0
    assert json.loads(report_path.read_text())["status"] == "PASS"
    written = json.loads(manifest_path.read_text())
    assert written["symbol_count"] == 156
    assert written["logical_request_count"] == 1404
    out = capsys.readouterr().out
    assert "NOT GRANTED" in out
    assert "physical page count is NOT implied" in out


def test_cli_exits_nonzero_on_failure(tmp_path, corpus, two_symbols, universe):
    absent = sorted(set(universe.tickers) - set(two_symbols))[0]
    assert cli(corpus, tmp_path, [absent]) == 1


def test_cli_exits_two_on_provenance_abort(tmp_path, corpus, capsys):
    bogus = tmp_path / "universe.json"
    bogus.write_text("{}")
    bars_dir, evidence_dir = corpus
    rc = pf.main([
        "--bars-dir", str(bars_dir), "--evidence-dir", str(evidence_dir),
        "--checkpoint-dir", str(tmp_path / "ck"), "--universe", str(bogus),
    ])
    assert rc == 2
    assert "PREFLIGHT ABORTED" in capsys.readouterr().err


def test_cli_needs_no_api_key(monkeypatch, tmp_path, corpus, two_symbols):
    monkeypatch.delenv("POLYGON_API_KEY", raising=False)
    assert cli(corpus, tmp_path, two_symbols) == 0
