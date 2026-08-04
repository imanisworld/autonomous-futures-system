"""Tests for research/equity_corpus_batch_preflight.py.

Two things these tests exist to prove:

1. **The preflight never touches the network.** Sockets and every HTTP client
   entry point are hard-blocked for the whole module, and no API key is present.
   If any code path tried to fetch, these tests would fail rather than quietly
   succeed on a developer machine that happens to be online.

2. **Every gate fails closed.** There is a negative test for each failure mode in
   the preregistration's restart/idempotency gate: missing files, incomplete and
   corrupted checkpoints, provenance drift, an altered universe, and each bar
   coverage defect.
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
    """Block sockets, DNS, and every HTTP client the repo has available.

    The preflight must be provably offline, not merely offline by habit.
    """
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

    # No credential is available, so a fetch could not even be attempted.
    monkeypatch.delenv("POLYGON_API_KEY", raising=False)


def test_preflight_module_imports_no_network_library():
    """The module must not even import an HTTP client."""
    source = Path(pf.__file__).read_text()
    for banned in ("import httpx", "import requests", "urllib.request", "http.client"):
        assert banned not in source, f"preflight must not reference {banned!r}"


def test_network_guard_is_actually_armed():
    """Guard against the guard silently not applying."""
    with pytest.raises(AssertionError, match="forbidden"):
        socket.socket()


# ---------------------------------------------------------------------------
# Bar fixtures
# ---------------------------------------------------------------------------

ET = pf.ET


def _epoch_ms(day: date, minutes: int) -> int:
    dt = datetime(day.year, day.month, day.day, minutes // 60, minutes % 60, tzinfo=ET)
    return int(dt.timestamp() * 1000)


def make_bars(days, *, price: float = 100.0) -> list[dict]:
    """Well-formed 5-minute bars for the given sessions, 04:00-close ET."""
    bars: list[dict] = []
    for day in days:
        _, ext_close = pf.session_close_minutes(day)
        for minute in range(pf.PREMARKET_OPEN_MIN, ext_close, pf.BAR_INTERVAL_MINUTES):
            ts = _epoch_ms(day, minute)
            bars.append(
                {
                    "t": ts,
                    "o": price,
                    "h": price + 1,
                    "l": price - 1,
                    "c": price,
                    "v": 1000,
                    "session": pf.session_tag(ts),
                }
            )
    return bars


def write_bars(bars_dir: Path, ticker: str, bars, *, truncate: bool = False) -> Path:
    bars_dir.mkdir(parents=True, exist_ok=True)
    path = pf.bar_file_for(bars_dir, ticker)
    text = "".join(json.dumps(b) + "\n" for b in bars)
    if truncate:
        # Simulate a process killed mid-write: last line has no newline.
        text = text[: len(text) - 12]
    path.write_text(text)
    return path


SHORT_START = date(2025, 3, 3)
SHORT_END = date(2025, 3, 14)


def short_days() -> list[date]:
    return pf.expected_sessions(SHORT_START, SHORT_END)


def validate_short(bars, **kwargs) -> pf.SymbolResult:
    """Validate against a two-week window — fast, and DST is not expected."""
    kwargs.setdefault("window_start", SHORT_START)
    kwargs.setdefault("window_end", SHORT_END)
    kwargs.setdefault("require_dst", False)
    return pf.validate_symbol_bars("TEST", bars, **kwargs)


def codes(result: pf.SymbolResult) -> set[str]:
    return {f.code for f in result.findings}


@pytest.fixture(scope="session")
def universe():
    return pf.load_universe()


@pytest.fixture(scope="session")
def two_symbols(universe):
    return sorted(universe.tickers)[:2]


@pytest.fixture(scope="session")
def full_bars_dir(tmp_path_factory, two_symbols):
    """Complete, valid bars over the whole frozen window for two symbols.

    Built once for the session: the full window is ~96k bars per symbol.
    """
    directory = tmp_path_factory.mktemp("bars")
    bars = make_bars(pf.expected_sessions())
    for ticker in two_symbols:
        write_bars(directory, ticker, bars)
    return directory


# ---------------------------------------------------------------------------
# Universe provenance
# ---------------------------------------------------------------------------


def test_universe_loads_and_matches_pinned_hashes(universe):
    assert universe.version == pf.CORPUS_VERSION
    assert universe.universe_sha256 == pf.UNIVERSE_SHA256
    assert universe.source_sha256 == pf.SOURCE_CSV_SHA256
    assert len(universe.entries) == 156
    assert sum(1 for e in universe.entries if e.is_setup_candidate) == 155


def test_altered_universe_is_rejected(tmp_path, universe):
    """A single changed byte in the frozen universe must abort the preflight."""
    altered = tmp_path / "universe.json"
    raw = json.loads(Path(pf.UNIVERSE_PATH).read_text())
    raw["entries"][0]["ticker"] = "ZZZZ"
    altered.write_text(json.dumps(raw, indent=2))

    with pytest.raises(pf.ProvenanceError, match="universe SHA-256 mismatch"):
        pf.load_universe(altered)


def test_altered_universe_membership_rejected_even_if_hash_is_repinned(tmp_path):
    """Re-pinning the hash does not launder a membership change."""
    altered = tmp_path / "universe.json"
    raw = json.loads(Path(pf.UNIVERSE_PATH).read_text())
    raw["entries"].pop()  # 156 -> 155
    altered.write_text(json.dumps(raw, indent=2))

    with pytest.raises(pf.ProvenanceError, match="expected 156 entries"):
        pf.load_universe(
            altered, expected_universe_sha256=pf.sha256_file(altered)
        )


def test_altered_source_watchlist_is_rejected(tmp_path, universe):
    bogus = tmp_path / "watchlist.csv"
    bogus.write_text("ticker\nAAPL\n")
    with pytest.raises(pf.ProvenanceError, match="source watchlist SHA-256 mismatch"):
        pf.load_universe(pf.UNIVERSE_PATH, source_csv_path=bogus)


def test_missing_universe_file_is_rejected(tmp_path):
    with pytest.raises(pf.ProvenanceError, match="universe file not found"):
        pf.load_universe(tmp_path / "nope.json")


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------


def test_manifest_covers_all_156_entries(universe):
    manifest = pf.build_manifest(universe)
    assert manifest["symbol_count"] == 156
    assert set(manifest["request_ids_by_symbol"]) == set(universe.tickers)
    assert manifest["request_count"] == 156 * manifest["requests_per_symbol"]
    assert manifest["window"] == {"start": "2024-07-31", "end": "2026-07-30"}


def test_manifest_is_byte_deterministic(universe):
    a = pf.canonical_json(pf.build_manifest(universe))
    b = pf.canonical_json(pf.build_manifest(universe))
    assert a == b
    assert pf.manifest_sha256(pf.build_manifest(universe)) == pf.sha256_text(a)


def test_quarter_slices_tile_the_window_without_gap_or_overlap():
    slices = pf.quarter_slices()
    assert slices[0][0] == pf.WINDOW_START
    assert slices[-1][1] == pf.WINDOW_END
    for (_, end_a), (start_b, _) in zip(slices, slices[1:]):
        assert start_b == end_a + timedelta(days=1)


def test_each_slice_stays_under_the_provider_page_limit():
    """One logical request must map to one physical page — no pagination."""
    for start, end in pf.quarter_slices():
        sessions = pf.expected_sessions(start, end)
        max_bars = len(sessions) * (
            (pf.EXT_CLOSE_MIN - pf.PREMARKET_OPEN_MIN) // pf.BAR_INTERVAL_MINUTES
        )
        assert max_bars < pf.PROVIDER_PAGE_LIMIT


def test_request_ids_are_stable_and_field_sensitive():
    base = dict(
        symbol="AAPL",
        endpoint=pf.ENDPOINT,
        adjustment=pf.ADJUSTMENT,
        timeframe=pf.TIMEFRAME,
        slice_start=date(2025, 1, 1),
        slice_end=date(2025, 3, 31),
    )
    rid = pf.request_id(**base)
    assert rid == pf.request_id(**base), "request id must be stable"

    for field, value in (
        ("symbol", "MSFT"),
        ("adjustment", "adjusted=false"),
        ("timeframe", "15m"),
        ("slice_start", date(2025, 1, 2)),
        ("slice_end", date(2025, 4, 1)),
        ("corpus_version", "equity_corpus_v2"),
    ):
        assert pf.request_id(**{**base, field: value}) != rid, field


def test_index_entry_gets_the_index_namespace(universe):
    """VIX is an index; requesting a plain equity ticker named VIX is a defect."""
    manifest = pf.build_manifest(universe)
    vix = [r for r in manifest["requests"] if r["ticker"] == "VIX"]
    assert vix, "VIX must still appear in the manifest"
    assert all(r["provider_ticker"] == "I:VIX" for r in vix)
    assert all(r["role"] == "regime_input_only" for r in vix)


# ---------------------------------------------------------------------------
# Bar validation gates
# ---------------------------------------------------------------------------


def test_clean_bars_pass():
    result = validate_short(make_bars(short_days()))
    assert result.status == "PASS", result.findings
    assert result.findings == []
    assert result.rth_bar_count > 0


def test_duplicate_timestamp_fails():
    bars = make_bars(short_days())
    bars.insert(5, dict(bars[4]))
    result = validate_short(bars)
    assert result.status == "FAIL"
    assert "DUPLICATE_TIMESTAMP" in codes(result)


def test_out_of_order_timestamp_fails():
    bars = make_bars(short_days())
    bars[10], bars[11] = bars[11], bars[10]
    result = validate_short(bars)
    assert result.status == "FAIL"
    assert "OUT_OF_ORDER" in codes(result)


def test_out_of_window_bar_fails():
    bars = make_bars(short_days())
    stray_day = SHORT_START - timedelta(days=7)
    ts = _epoch_ms(stray_day, pf.RTH_OPEN_MIN)
    bars.insert(0, {"t": ts, "o": 1, "h": 1, "l": 1, "c": 1,
                    "session": pf.session_tag(ts)})
    result = validate_short(bars)
    assert result.status == "FAIL"
    assert "OUT_OF_WINDOW" in codes(result)


def test_missing_session_tag_fails():
    bars = make_bars(short_days())
    bars[3].pop("session")
    result = validate_short(bars)
    assert result.status == "FAIL"
    assert "INVALID_SESSION_TAG" in codes(result)


def test_wrong_session_tag_fails():
    bars = make_bars(short_days())
    rth = next(b for b in bars if b["session"] == "RTH")
    rth["session"] = "PREMARKET"
    result = validate_short(bars)
    assert result.status == "FAIL"
    assert "INVALID_SESSION_TAG" in codes(result)


def test_unknown_session_tag_fails():
    bars = make_bars(short_days())
    bars[0]["session"] = "OVERNIGHT"
    result = validate_short(bars)
    assert result.status == "FAIL"
    assert "INVALID_SESSION_TAG" in codes(result)


def test_misaligned_five_minute_bar_fails():
    bars = make_bars(short_days())
    bars[7]["t"] += 60_000  # +1 minute — no longer on a 5-minute boundary
    result = validate_short(bars)
    assert result.status == "FAIL"
    assert "MISALIGNED_INTERVAL" in codes(result)


def test_missing_expected_session_fails():
    days = short_days()
    dropped = days[3]
    bars = make_bars([d for d in days if d != dropped])
    result = validate_short(bars)
    assert result.status == "FAIL"
    assert "MISSING_SESSION" in codes(result)
    assert dropped.isoformat() in "".join(f.detail for f in result.findings)


def test_session_present_but_no_rth_bars_fails():
    days = short_days()
    bars = [
        b for b in make_bars(days)
        if not (pf.session_tag(b["t"]) == "RTH"
                and pf._et_datetime(b["t"]).date() == days[2])
    ]
    result = validate_short(bars)
    assert result.status == "FAIL"
    assert "MISSING_RTH_SESSION" in codes(result)


def test_weekend_bar_fails():
    days = short_days()
    saturday = SHORT_START + timedelta(days=5)
    assert saturday.weekday() == 5
    bars = make_bars(days) + make_bars([saturday])
    bars.sort(key=lambda b: b["t"])
    result = validate_short(bars)
    assert result.status == "FAIL"
    assert "EXTRA_SESSION" in codes(result)


def test_bar_after_half_day_close_fails():
    """Half sessions close at 17:00 ET; an 18:00 Christmas Eve bar is a defect."""
    half_day = date(2025, 12, 24)
    assert half_day in pf.EARLY_CLOSES
    days = pf.expected_sessions(date(2025, 12, 22), date(2025, 12, 31))
    bars = make_bars(days)
    ts = _epoch_ms(half_day, 18 * 60)
    bars.append({"t": ts, "o": 1, "h": 1, "l": 1, "c": 1,
                 "session": pf.session_tag(ts)})
    bars.sort(key=lambda b: b["t"])
    result = pf.validate_symbol_bars(
        "TEST", bars,
        window_start=date(2025, 12, 22), window_end=date(2025, 12, 31),
        require_dst=False,
    )
    assert result.status == "FAIL"
    assert "BAR_AFTER_SESSION_CLOSE" in codes(result)


def test_empty_bar_list_fails():
    result = validate_short([])
    assert result.status == "FAIL"
    assert "NO_BARS" in codes(result)


def test_fixed_utc_offset_timestamps_fail_the_dst_gate():
    """Bars built from a fixed -05:00 offset must not pass as America/New_York."""
    days = pf.expected_sessions(date(2025, 1, 2), date(2025, 1, 10))
    bars = make_bars(days)
    result = pf.validate_symbol_bars(
        "TEST", bars,
        window_start=date(2025, 1, 2), window_end=date(2025, 1, 10),
        require_dst=True,
    )
    assert result.status == "FAIL"
    assert "DST_NOT_OBSERVED" in codes(result)


def test_dst_is_observed_across_the_full_window():
    """The real window straddles both DST transitions in each direction."""
    bars = make_bars(pf.expected_sessions(date(2025, 3, 5), date(2025, 3, 20)))
    result = pf.validate_symbol_bars(
        "TEST", bars,
        window_start=date(2025, 3, 5), window_end=date(2025, 3, 20),
        require_dst=True,
    )
    assert result.status == "PASS", result.findings
    assert len(result.distinct_utc_offsets) == 2


def test_session_tag_matches_the_smoke_harness_implementation():
    """Parity guard for the deliberate duplication of session tagging."""
    pytest.importorskip("httpx")
    from research import equity_corpus_smoke as smoke

    day = date(2025, 6, 11)
    for minute in range(0, 24 * 60, 5):
        ts = _epoch_ms(day, minute)
        assert pf.session_tag(ts) == smoke.session_tag(ts), minute


# ---------------------------------------------------------------------------
# Bar file loading
# ---------------------------------------------------------------------------


def test_truncated_bar_file_is_rejected(tmp_path):
    """Partial final output from an interrupted write must never load."""
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
# Checkpoints
# ---------------------------------------------------------------------------


@pytest.fixture
def provenance(universe):
    return pf.build_provenance(pf.build_manifest(universe), universe)


@pytest.fixture
def passing_result():
    return pf.SymbolResult(
        ticker="AAPL", status="PASS", bar_count=10, session_count=2,
        rth_bar_count=5, distinct_utc_offsets=("-1 day, 19:00:00",),
        earliest="2025-01-02T04:00:00-05:00", latest="2025-01-03T19:55:00-05:00",
    )


def test_checkpoint_roundtrip(tmp_path, provenance, passing_result):
    store = pf.CheckpointStore(tmp_path)
    store.write(passing_result, provenance=provenance, request_ids=["a", "b"])
    payload = store.verify("AAPL", provenance=provenance, request_ids=["a", "b"])
    assert payload is not None
    assert payload["status"] == pf.STATUS_COMPLETE
    assert payload["summary"]["bar_count"] == 10


def test_absent_checkpoint_returns_none(tmp_path, provenance):
    store = pf.CheckpointStore(tmp_path)
    assert store.verify("AAPL", provenance=provenance, request_ids=["a"]) is None


def test_failed_symbol_is_never_checkpointed(tmp_path, provenance):
    store = pf.CheckpointStore(tmp_path)
    failed = pf.SymbolResult(ticker="AAPL", status="FAIL")
    with pytest.raises(pf.CheckpointError, match="refusing to checkpoint"):
        store.write(failed, provenance=provenance, request_ids=["a"])


def test_checkpoint_write_is_atomic(tmp_path, provenance, passing_result):
    """No temp files survive, and the target appears complete or not at all."""
    store = pf.CheckpointStore(tmp_path)
    store.write(passing_result, provenance=provenance, request_ids=["a"])
    leftovers = [p for p in tmp_path.iterdir() if p.suffix == ".tmp"]
    assert leftovers == []
    assert json.loads(store.path_for("AAPL").read_text())["payload_sha256"]


def test_corrupted_checkpoint_json_is_rejected(tmp_path, provenance, passing_result):
    store = pf.CheckpointStore(tmp_path)
    store.write(passing_result, provenance=provenance, request_ids=["a"])
    store.path_for("AAPL").write_text("{ this is not json")
    with pytest.raises(pf.CheckpointError, match="corrupted"):
        store.verify("AAPL", provenance=provenance, request_ids=["a"])


def test_tampered_checkpoint_body_is_rejected(tmp_path, provenance, passing_result):
    """Editing the summary without recomputing the hash must be detected."""
    store = pf.CheckpointStore(tmp_path)
    store.write(passing_result, provenance=provenance, request_ids=["a"])
    payload = json.loads(store.path_for("AAPL").read_text())
    payload["summary"]["bar_count"] = 999_999
    store.path_for("AAPL").write_text(json.dumps(payload))
    with pytest.raises(pf.CheckpointError, match="payload_sha256 does not match"):
        store.verify("AAPL", provenance=provenance, request_ids=["a"])


def test_incomplete_checkpoint_is_rejected(tmp_path, provenance, passing_result):
    """status != complete is not resumable."""
    store = pf.CheckpointStore(tmp_path)
    store.write(passing_result, provenance=provenance, request_ids=["a"])
    payload = json.loads(store.path_for("AAPL").read_text())
    body = {k: v for k, v in payload.items() if k != "payload_sha256"}
    body["status"] = "in_progress"
    body["payload_sha256"] = pf.sha256_obj(body)
    store.path_for("AAPL").write_text(pf.canonical_json(body))
    with pytest.raises(pf.CheckpointError, match="incomplete"):
        store.verify("AAPL", provenance=provenance, request_ids=["a"])


def test_checkpoint_without_payload_hash_is_rejected(tmp_path, provenance,
                                                     passing_result):
    store = pf.CheckpointStore(tmp_path)
    store.write(passing_result, provenance=provenance, request_ids=["a"])
    payload = json.loads(store.path_for("AAPL").read_text())
    payload.pop("payload_sha256")
    store.path_for("AAPL").write_text(json.dumps(payload))
    with pytest.raises(pf.CheckpointError, match="no payload_sha256"):
        store.verify("AAPL", provenance=provenance, request_ids=["a"])


@pytest.mark.parametrize("key", pf.CheckpointStore.PROVENANCE_KEYS)
def test_each_provenance_hash_mismatch_is_rejected(
    tmp_path, provenance, passing_result, key
):
    """Manifest, universe, code, window, and config drift each block resume."""
    store = pf.CheckpointStore(tmp_path)
    store.write(passing_result, provenance=provenance, request_ids=["a"])
    drifted = {**provenance, key: "0" * 64}
    with pytest.raises(pf.CheckpointError, match=f"stale {key}"):
        store.verify("AAPL", provenance=drifted, request_ids=["a"])


def test_checkpoint_for_a_different_request_set_is_rejected(
    tmp_path, provenance, passing_result
):
    store = pf.CheckpointStore(tmp_path)
    store.write(passing_result, provenance=provenance, request_ids=["a", "b"])
    with pytest.raises(pf.CheckpointError, match="different request set"):
        store.verify("AAPL", provenance=provenance, request_ids=["a", "c"])


def test_config_hash_changes_with_extra_holidays(universe):
    base = pf.config_sha256()
    changed = pf.config_sha256(extra_holidays=[date(2025, 5, 1)])
    assert base != changed


# ---------------------------------------------------------------------------
# End-to-end runner
# ---------------------------------------------------------------------------


def test_full_run_passes_and_checkpoints(tmp_path, full_bars_dir, two_symbols):
    ck = tmp_path / "ck"
    report = pf.run_preflight(
        bars_dir=full_bars_dir, checkpoint_dir=ck, symbols=two_symbols
    )
    assert report["status"] == "PASS", report["findings_by_code"]
    assert report["exit_code"] == 0
    assert report["counts"]["passed"] == len(two_symbols)
    assert report["counts"]["resumed"] == 0
    assert report["authorizes_corpus_batch"] is False
    for ticker in two_symbols:
        assert (ck / f"{ticker}.json").is_file()


def test_missing_symbol_file_fails_closed(tmp_path, full_bars_dir, two_symbols,
                                          universe):
    absent = sorted(set(universe.tickers) - set(two_symbols))[0]
    report = pf.run_preflight(
        bars_dir=full_bars_dir,
        checkpoint_dir=tmp_path / "ck",
        symbols=[*two_symbols, absent],
    )
    assert report["status"] == "FAIL"
    assert report["exit_code"] == 1
    assert absent in report["failed_symbols"]
    assert "BAR_FILE_UNUSABLE" in report["findings_by_code"]


def test_resume_skips_completed_symbols(tmp_path, full_bars_dir, two_symbols):
    ck = tmp_path / "ck"
    pf.run_preflight(bars_dir=full_bars_dir, checkpoint_dir=ck, symbols=two_symbols)
    second = pf.run_preflight(
        bars_dir=full_bars_dir, checkpoint_dir=ck, symbols=two_symbols
    )
    assert second["status"] == "PASS"
    assert second["counts"]["resumed"] == len(two_symbols)


def test_no_resume_revalidates_everything(tmp_path, full_bars_dir, two_symbols):
    ck = tmp_path / "ck"
    pf.run_preflight(bars_dir=full_bars_dir, checkpoint_dir=ck, symbols=two_symbols)
    second = pf.run_preflight(
        bars_dir=full_bars_dir, checkpoint_dir=ck, symbols=two_symbols, resume=False
    )
    assert second["counts"]["resumed"] == 0
    assert second["status"] == "PASS"


def test_interrupted_run_resumes_deterministically(tmp_path, full_bars_dir,
                                                   two_symbols):
    """Interrupt after symbol 1, resume, and land on the same report content."""
    first, second = two_symbols
    ck = tmp_path / "ck"

    # Run 1 dies after completing only the first symbol.
    partial = pf.run_preflight(
        bars_dir=full_bars_dir, checkpoint_dir=ck, symbols=[first]
    )
    assert partial["status"] == "PASS"
    assert (ck / f"{first}.json").is_file()
    assert not (ck / f"{second}.json").is_file()

    resumed = pf.run_preflight(
        bars_dir=full_bars_dir, checkpoint_dir=ck, symbols=two_symbols
    )
    uninterrupted = pf.run_preflight(
        bars_dir=full_bars_dir, checkpoint_dir=tmp_path / "ck2", symbols=two_symbols
    )

    assert resumed["status"] == uninterrupted["status"] == "PASS"
    assert resumed["counts"]["resumed"] == 1

    def normalize(report):
        stripped = json.loads(json.dumps(report))
        stripped["counts"]["resumed"] = 0
        for sym in stripped["symbols"].values():
            sym["resumed"] = False
        return stripped

    assert normalize(resumed) == normalize(uninterrupted)


def test_stale_checkpoint_fails_the_run_rather_than_being_reused(
    tmp_path, full_bars_dir, two_symbols
):
    """Provenance drift must surface as a failure, not a silent revalidation."""
    ck = tmp_path / "ck"
    pf.run_preflight(bars_dir=full_bars_dir, checkpoint_dir=ck, symbols=two_symbols)

    target = ck / f"{two_symbols[0]}.json"
    payload = json.loads(target.read_text())
    body = {k: v for k, v in payload.items() if k != "payload_sha256"}
    body["provenance"]["code_sha256"] = "0" * 64
    body["payload_sha256"] = pf.sha256_obj(body)
    target.write_text(pf.canonical_json(body))

    report = pf.run_preflight(
        bars_dir=full_bars_dir, checkpoint_dir=ck, symbols=two_symbols
    )
    assert report["status"] == "FAIL"
    assert "CHECKPOINT_REJECTED" in report["findings_by_code"]
    assert two_symbols[0] in report["failed_symbols"]


def test_corrupt_checkpoint_fails_the_run(tmp_path, full_bars_dir, two_symbols):
    ck = tmp_path / "ck"
    pf.run_preflight(bars_dir=full_bars_dir, checkpoint_dir=ck, symbols=two_symbols)
    (ck / f"{two_symbols[0]}.json").write_text("{ corrupted")

    report = pf.run_preflight(
        bars_dir=full_bars_dir, checkpoint_dir=ck, symbols=two_symbols
    )
    assert report["status"] == "FAIL"
    assert "CHECKPOINT_REJECTED" in report["findings_by_code"]


def test_bad_bars_fail_the_run_and_leave_no_checkpoint(tmp_path, two_symbols):
    bars_dir = tmp_path / "bars"
    good, bad = two_symbols
    write_bars(bars_dir, good, make_bars(pf.expected_sessions()))
    # Drop one session for the second symbol.
    days = pf.expected_sessions()
    write_bars(bars_dir, bad, make_bars([d for d in days if d != days[100]]))

    ck = tmp_path / "ck"
    report = pf.run_preflight(
        bars_dir=bars_dir, checkpoint_dir=ck, symbols=two_symbols
    )
    assert report["status"] == "FAIL"
    assert bad in report["failed_symbols"]
    assert "MISSING_SESSION" in report["findings_by_code"]
    assert not (ck / f"{bad}.json").exists()
    assert (ck / f"{good}.json").is_file()


def test_partial_final_output_fails_the_run(tmp_path, two_symbols):
    bars_dir = tmp_path / "bars"
    write_bars(bars_dir, two_symbols[0], make_bars(pf.expected_sessions()),
               truncate=True)
    report = pf.run_preflight(
        bars_dir=bars_dir, checkpoint_dir=tmp_path / "ck", symbols=[two_symbols[0]]
    )
    assert report["status"] == "FAIL"
    assert "BAR_FILE_UNUSABLE" in report["findings_by_code"]


def test_unknown_symbol_is_rejected(tmp_path, full_bars_dir):
    with pytest.raises(pf.ProvenanceError, match="not in the frozen universe"):
        pf.run_preflight(
            bars_dir=full_bars_dir,
            checkpoint_dir=tmp_path / "ck",
            symbols=["NOT_A_REAL_TICKER"],
        )


def test_report_is_deterministic_and_carries_no_wall_clock(
    tmp_path, full_bars_dir, two_symbols
):
    a = pf.run_preflight(
        bars_dir=full_bars_dir, checkpoint_dir=tmp_path / "a", symbols=two_symbols
    )
    b = pf.run_preflight(
        bars_dir=full_bars_dir, checkpoint_dir=tmp_path / "b", symbols=two_symbols
    )
    assert pf.canonical_json(a) == pf.canonical_json(b)
    assert "generated_at" not in pf.canonical_json(a)


def test_report_never_reports_complete_with_warnings(tmp_path, two_symbols):
    """Any finding at all must flip the whole report to FAIL."""
    bars_dir = tmp_path / "bars"
    bars = make_bars(pf.expected_sessions())
    bars[500]["session"] = "OVERNIGHT"
    write_bars(bars_dir, two_symbols[0], bars)
    report = pf.run_preflight(
        bars_dir=bars_dir, checkpoint_dir=tmp_path / "ck", symbols=[two_symbols[0]]
    )
    assert report["status"] == "FAIL"
    assert report["exit_code"] != 0
    assert report["counts"]["passed"] == 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_exits_zero_on_pass_and_writes_artifacts(
    tmp_path, full_bars_dir, two_symbols, capsys
):
    report_path = tmp_path / "report.json"
    manifest_path = tmp_path / "manifest.json"
    rc = pf.main(
        [
            "--bars-dir", str(full_bars_dir),
            "--checkpoint-dir", str(tmp_path / "ck"),
            "--symbols", ",".join(two_symbols),
            "--report", str(report_path),
            "--manifest-out", str(manifest_path),
        ]
    )
    assert rc == 0
    written = json.loads(report_path.read_text())
    assert written["status"] == "PASS"
    assert json.loads(manifest_path.read_text())["symbol_count"] == 156
    assert "NOT GRANTED" in capsys.readouterr().out


def test_cli_exits_nonzero_on_failure(tmp_path, full_bars_dir, two_symbols,
                                      universe):
    absent = sorted(set(universe.tickers) - set(two_symbols))[0]
    rc = pf.main(
        [
            "--bars-dir", str(full_bars_dir),
            "--checkpoint-dir", str(tmp_path / "ck"),
            "--symbols", absent,
        ]
    )
    assert rc == 1


def test_cli_exits_two_on_provenance_abort(tmp_path, full_bars_dir, capsys):
    bogus = tmp_path / "universe.json"
    bogus.write_text("{}")
    rc = pf.main(
        [
            "--bars-dir", str(full_bars_dir),
            "--checkpoint-dir", str(tmp_path / "ck"),
            "--universe", str(bogus),
        ]
    )
    assert rc == 2
    assert "PREFLIGHT ABORTED" in capsys.readouterr().err


def test_cli_needs_no_api_key(monkeypatch, tmp_path, full_bars_dir, two_symbols):
    """No credential in the environment, and the run still completes."""
    monkeypatch.delenv("POLYGON_API_KEY", raising=False)
    rc = pf.main(
        [
            "--bars-dir", str(full_bars_dir),
            "--checkpoint-dir", str(tmp_path / "ck"),
            "--symbols", ",".join(two_symbols),
        ]
    )
    assert rc == 0
