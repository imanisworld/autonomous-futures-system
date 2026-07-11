"""Tests for scripts/polygon_stocks_backfill.py. No network: HTTP is
faked via httpx.MockTransport, matching the existing tests/test_polygon_client.py
and tests/test_options_polygon_historical.py convention."""

from __future__ import annotations

import csv
import json
import subprocess
from datetime import date, datetime, timezone
from pathlib import Path

import httpx
import pytest

import scripts.polygon_stocks_backfill as backfill
from stocks_advisory.csv_loader import load_bars_from_csv


def _fake_client(pages: list[dict], calls: list[str]) -> httpx.Client:
    responses = list(pages)

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        payload = responses.pop(0)
        return httpx.Response(200, json=payload)

    return httpx.Client(transport=httpx.MockTransport(handler))


def _row(t_ms: int, o=100.0, h=101.0, lo=99.0, c=100.5, v=10):
    return {"t": t_ms, "o": o, "h": h, "l": lo, "c": c, "v": v}


# ── PolygonAggsClient ───────────────────────────────────────────────────────
class TestPolygonAggsClient:
    def test_unconfigured_raises(self):
        client = backfill.PolygonAggsClient(api_key="")
        assert not client.configured
        with pytest.raises(backfill.PolygonBackfillError):
            client.fetch_all_bars("QQQ", "2025-01-01", "2025-01-02")

    def test_paginates_via_next_url(self):
        t1 = int(datetime(2025, 6, 10, 13, 30, tzinfo=timezone.utc).timestamp() * 1000)
        t2 = t1 + 5 * 60 * 1000
        pages = [
            {"results": [_row(t1)], "next_url": "https://api.polygon.io/v2/aggs/ticker/QQQ/range/5/minute/2025-06-10/2025-06-11?cursor=abc"},
            {"results": [_row(t2, c=101.0)]},
        ]
        calls: list[str] = []
        client = backfill.PolygonAggsClient(api_key="test-key", client=_fake_client(pages, calls))
        rows = client.fetch_all_bars("QQQ", "2025-06-10", "2025-06-11")
        assert len(calls) == 2
        assert len(rows) == 2

    def test_429_retries_then_succeeds(self):
        responses = [
            httpx.Response(429, headers={"Retry-After": "0"}),
            httpx.Response(200, json={"results": [_row(1)]}),
        ]

        def handler(request: httpx.Request) -> httpx.Response:
            return responses.pop(0)

        client = backfill.PolygonAggsClient(
            api_key="test-key",
            client=httpx.Client(transport=httpx.MockTransport(handler)),
            retry_sleep_seconds=0.01,
        )
        rows = client.fetch_all_bars("QQQ", "2025-06-10", "2025-06-10")
        assert len(rows) == 1 and not responses

    def test_http_error_raises_backfill_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(403, text="unauthorized")

        client = backfill.PolygonAggsClient(
            api_key="test-key",
            client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        with pytest.raises(backfill.PolygonBackfillError, match="403"):
            client.fetch_all_bars("QQQ", "2025-06-10", "2025-06-10")

    def test_api_key_never_in_repr(self):
        client = backfill.PolygonAggsClient(api_key="super-secret-value")
        assert "super-secret-value" not in repr(client)

    def test_api_key_never_in_error_message(self):
        client = backfill.PolygonAggsClient(api_key="super-secret-value")

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="boom")

        client._client = httpx.Client(transport=httpx.MockTransport(handler))
        with pytest.raises(backfill.PolygonBackfillError) as exc_info:
            client.fetch_all_bars("QQQ", "2025-06-10", "2025-06-10")
        assert "super-secret-value" not in str(exc_info.value)


# ── row mapping ──────────────────────────────────────────────────────────────
class TestRowToBar:
    def test_mocked_response_maps_and_converts_to_et(self):
        # 2025-06-10 13:30:00 UTC == 2025-06-10 09:30:00-04:00 ET (EDT).
        t_ms = int(datetime(2025, 6, 10, 13, 30, tzinfo=timezone.utc).timestamp() * 1000)
        bar = backfill._row_to_bar(_row(t_ms, o=1.0, h=2.0, lo=0.5, c=1.5, v=100))
        assert bar is not None
        assert bar.timestamp.startswith("2025-06-10T09:30:00-04:00")
        assert (bar.open, bar.high, bar.low, bar.close, bar.volume) == (1.0, 2.0, 0.5, 1.5, 100)

    def test_dst_boundary_offset_is_correct(self):
        # 2025-01-10 (winter) should carry the -05:00 EST offset.
        t_ms = int(datetime(2025, 1, 10, 14, 30, tzinfo=timezone.utc).timestamp() * 1000)
        bar = backfill._row_to_bar(_row(t_ms))
        assert bar.timestamp.endswith("-05:00") or "-05:00" in bar.timestamp

    def test_malformed_row_skipped_not_fabricated(self):
        assert backfill._row_to_bar({"t": 123, "o": "junk", "h": 1, "l": 1, "c": 1}) is None
        assert backfill._row_to_bar({"no_t": True}) is None

    def test_missing_volume_defaults_to_zero_not_fabricated_nonzero(self):
        bar = backfill._row_to_bar({"t": 123000, "o": 1, "h": 1, "l": 1, "c": 1})
        assert bar.volume == 0


# ── dedupe ───────────────────────────────────────────────────────────────────
class TestDedupeBars:
    def test_exact_duplicate_dropped_and_counted(self):
        bar = backfill.Bar5m("2025-06-10T09:30:00-04:00", 1.0, 1.0, 1.0, 1.0, 10)
        deduped, dup_count, conflicts = backfill.dedupe_bars([bar, bar])
        assert len(deduped) == 1
        assert dup_count == 1
        assert conflicts == []

    def test_conflicting_duplicate_raises(self):
        bar1 = backfill.Bar5m("2025-06-10T09:30:00-04:00", 1.0, 1.0, 1.0, 1.0, 10)
        bar2 = backfill.Bar5m("2025-06-10T09:30:00-04:00", 2.0, 2.0, 2.0, 2.0, 20)
        with pytest.raises(backfill.ConflictingDuplicateTimestamp):
            backfill.dedupe_bars([bar1, bar2])

    def test_sorted_ascending_by_true_datetime_not_string(self):
        # Spans the DST fall-back boundary where naive string sort would
        # misorder -05:00 vs -04:00 offsets around the transition.
        early = backfill.Bar5m("2025-06-10T09:35:00-04:00", 1, 1, 1, 1, 1)
        late = backfill.Bar5m("2025-06-10T09:30:00-04:00", 1, 1, 1, 1, 1)
        deduped, _, _ = backfill.dedupe_bars([early, late])
        assert deduped[0].timestamp == late.timestamp
        assert deduped[1].timestamp == early.timestamp


# ── CSV round trip / loader compatibility ───────────────────────────────────
class TestCsvRoundTrip:
    def test_written_csv_is_readable_by_existing_stocks_advisory_loader(self, tmp_path):
        bars = [
            backfill.Bar5m("2025-06-10T09:30:00-04:00", 1.0, 2.0, 0.5, 1.5, 100),
            backfill.Bar5m("2025-06-10T04:00:00-04:00", 1.0, 1.0, 1.0, 1.0, 5),  # pre-market
            backfill.Bar5m("2025-06-10T16:30:00-04:00", 1.0, 1.0, 1.0, 1.0, 5),  # after-hours
        ]
        out = tmp_path / "QQQ_5min.csv"
        backfill.write_csv(out, bars)

        loaded = load_bars_from_csv(out)
        assert loaded.rows_read == 3
        assert len(loaded.all_bars) == 3
        # Only the 09:30 bar is inside RTH [9:30, 16:00).
        assert len(loaded.rth_bars) == 1
        assert loaded.rows_outside_regular_hours == 2

    def test_sha256_is_stable_and_deterministic(self, tmp_path):
        bars = [backfill.Bar5m("2025-06-10T09:30:00-04:00", 1.0, 1.0, 1.0, 1.0, 1)]
        out = tmp_path / "QQQ_5min.csv"
        backfill.write_csv(out, bars)
        h1 = backfill.sha256_of_file(out)
        h2 = backfill.sha256_of_file(out)
        assert h1 == h2
        assert len(h1) == 64


# ── coverage report ──────────────────────────────────────────────────────────
class TestCoverageReport:
    def test_rth_dates_and_common_intersection(self):
        qqq = [
            backfill.Bar5m("2025-06-10T09:30:00-04:00", 1, 1, 1, 1, 1),
            backfill.Bar5m("2025-06-11T09:30:00-04:00", 1, 1, 1, 1, 1),
        ]
        tqqq = [backfill.Bar5m("2025-06-10T09:30:00-04:00", 1, 1, 1, 1, 1)]
        sqqq = [backfill.Bar5m("2025-06-10T09:30:00-04:00", 1, 1, 1, 1, 1)]
        report = backfill.coverage_report({"QQQ": qqq, "TQQQ": tqqq, "SQQQ": sqqq})
        assert report["common_rth_session_dates"] == 1
        assert report["missing_sessions_by_symbol"]["TQQQ"] == ["2025-06-11"]
        assert report["missing_sessions_by_symbol"]["SQQQ"] == ["2025-06-11"]
        assert "QQQ" not in report["missing_sessions_by_symbol"]

    def test_extended_hours_excluded_from_rth_dates(self):
        bars = [backfill.Bar5m("2025-06-10T04:00:00-04:00", 1, 1, 1, 1, 1)]
        dates = backfill.rth_dates(bars)
        assert dates == set()

    def test_rth_boundary_inclusive_start_exclusive_end(self):
        at_open = backfill.Bar5m("2025-06-10T09:30:00-04:00", 1, 1, 1, 1, 1)
        at_close = backfill.Bar5m("2025-06-10T16:00:00-04:00", 1, 1, 1, 1, 1)
        assert backfill._is_rth(at_open) is True
        assert backfill._is_rth(at_close) is False


# ── BATS overlap sanity check ───────────────────────────────────────────────
class TestBatsOverlap:
    def _write_bats_csv(self, tmp_path: Path, name: str, rows: list[tuple]) -> None:
        path = tmp_path / name
        with path.open("w", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["time", "open", "high", "low", "close", "Bar Type 1 Label", "Bar Type 2 Label", "Bar Type 3 Label", "Volume"])
            for row in rows:
                writer.writerow(list(row) + [0, 1, 0])

    def test_load_bats_csvs_matches_symbol_glob(self, tmp_path):
        self._write_bats_csv(
            tmp_path, "BATS_QQQ, 5.csv",
            [("2025-06-10T09:30:00-04:00", 100.0, 101.0, 99.0, 100.5, 1000)],
        )
        self._write_bats_csv(
            tmp_path, "BATS_QQQ, 5 (1).csv",
            [("2025-06-11T09:30:00-04:00", 100.0, 101.0, 99.0, 100.5, 1000)],
        )
        self._write_bats_csv(
            tmp_path, "BATS_TQQQ, 5.csv",
            [("2025-06-10T09:30:00-04:00", 50.0, 51.0, 49.0, 50.5, 500)],
        )
        bars = backfill.load_bats_csvs("QQQ", tmp_path)
        assert len(bars) == 2
        assert bars[0].timestamp < bars[1].timestamp

    def test_compare_overlap_identical_bars_is_perfect_correlation(self):
        bars = [backfill.Bar5m(f"2025-06-10T09:{30+i}:00-04:00", 100 + i, 101 + i, 99 + i, 100.5 + i, 1000) for i in range(5)]
        overlap = backfill.compare_overlap(bars, bars)
        assert overlap["matched_bars"] == 5
        assert overlap["close_correlation"] == pytest.approx(1.0)
        assert overlap["median_abs_bps_diff"] == 0.0
        passed, reasons = backfill.overlap_verdict(overlap)
        assert passed and not reasons

    def test_compare_overlap_zero_matches_fails_verdict(self):
        polygon_bars = [backfill.Bar5m("2025-06-10T09:30:00-04:00", 1, 1, 1, 1, 1)]
        bats_bars = [backfill.Bar5m("2099-01-01T09:30:00-04:00", 1, 1, 1, 1, 1)]
        overlap = backfill.compare_overlap(polygon_bars, bats_bars)
        assert overlap["matched_bars"] == 0
        passed, reasons = backfill.overlap_verdict(overlap)
        assert not passed
        assert reasons

    def test_compare_overlap_small_venue_drift_still_passes(self):
        polygon_bars = [
            backfill.Bar5m(f"2025-06-10T09:{30+i}:00-04:00", 100 + i, 101 + i, 99 + i, 100.5 + i, 1000)
            for i in range(10)
        ]
        bats_bars = [
            backfill.Bar5m(b.timestamp, b.open + 0.02, b.high + 0.01, b.low - 0.01, b.close + 0.01, int(b.volume * 0.99))
            for b in polygon_bars
        ]
        overlap = backfill.compare_overlap(polygon_bars, bats_bars)
        passed, reasons = backfill.overlap_verdict(overlap)
        assert passed and not reasons

    def test_compare_overlap_single_bar_no_correlation_does_not_force_fail(self):
        # A too-small sample can't produce a correlation (statistics, not
        # a defect) -- the verdict must fall through to bps checks rather
        # than treating "correlation is None" as an automatic failure.
        polygon_bars = [backfill.Bar5m("2025-06-10T09:30:00-04:00", 100.0, 101.0, 99.0, 100.5, 1000)]
        bats_bars = [backfill.Bar5m("2025-06-10T09:30:00-04:00", 100.02, 101.01, 98.99, 100.51, 990)]
        overlap = backfill.compare_overlap(polygon_bars, bats_bars)
        assert overlap["close_correlation"] is None
        passed, reasons = backfill.overlap_verdict(overlap)
        assert passed and not reasons

    def test_compare_overlap_large_drift_fails_verdict(self):
        polygon_bars = [backfill.Bar5m("2025-06-10T09:30:00-04:00", 100.0, 101.0, 99.0, 100.5, 1000)]
        bats_bars = [backfill.Bar5m("2025-06-10T09:30:00-04:00", 150.0, 151.0, 149.0, 150.5, 1000)]
        overlap = backfill.compare_overlap(polygon_bars, bats_bars)
        passed, reasons = backfill.overlap_verdict(overlap)
        assert not passed and reasons

    def test_rth_only_gating_ignores_extended_hours_noise(self):
        # Real-world finding (2026-07-11 backfill run): thin pre-market
        # bars can legitimately swing >500bps between a single venue
        # (BATS) and the consolidated tape, while the SAME symbol's RTH
        # bars -- the only ones csv_loader.py actually feeds the
        # backtest -- match closely. The gate must be scored on
        # RTH-matched bars only, so noisy extended-hours bars don't
        # trip a false failure.
        clean_rth = [
            backfill.Bar5m(f"2025-06-10T09:{30+i}:00-04:00", 100 + i, 101 + i, 99 + i, 100.5 + i, 1000)
            for i in range(10)
        ]
        noisy_premarket = [
            backfill.Bar5m("2025-06-10T04:00:00-04:00", 80.67, 80.67, 80.67, 80.67, 1_000_000),
        ]
        bats_clean_rth = [
            backfill.Bar5m(b.timestamp, b.open + 0.01, b.high + 0.01, b.low - 0.01, b.close + 0.01, int(b.volume * 0.9))
            for b in clean_rth
        ]
        bats_noisy_premarket = [
            backfill.Bar5m("2025-06-10T04:00:00-04:00", 76.57, 76.57, 76.57, 76.57, 30_000),
        ]

        polygon_all = clean_rth + noisy_premarket
        bats_all = bats_clean_rth + bats_noisy_premarket

        full_session_overlap = backfill.compare_overlap(polygon_all, bats_all)
        full_passed, full_reasons = backfill.overlap_verdict(full_session_overlap)
        assert not full_passed and full_reasons  # extended-hours noise trips the full-session gate

        rth_only_polygon = [b for b in polygon_all if backfill._is_rth(b)]
        rth_only_bats = [b for b in bats_all if backfill._is_rth(b)]
        rth_overlap = backfill.compare_overlap(rth_only_polygon, rth_only_bats)
        rth_passed, rth_reasons = backfill.overlap_verdict(rth_overlap)
        assert rth_passed and not rth_reasons  # RTH-only gate passes cleanly


# ── manifest ─────────────────────────────────────────────────────────────────
class TestManifest:
    def test_manifest_never_contains_api_key(self, tmp_path, monkeypatch):
        bars = [backfill.Bar5m("2025-06-10T09:30:00-04:00", 1, 1, 1, 1, 1)]
        out = tmp_path / "QQQ_5min.csv"
        backfill.write_csv(out, bars)
        coverage = backfill.coverage_report({"QQQ": bars})

        monkeypatch.setenv("POLYGON_API_KEY", "super-secret-manifest-test")
        manifest = backfill.build_manifest(
            symbols=("QQQ",),
            requested_start="2025-01-01",
            requested_end="2025-06-10",
            coverage=coverage,
            output_paths={"QQQ": out},
            row_counts={"QQQ": 1},
            repo_root=tmp_path,
            retrieved_at="2025-06-10T00:00:00+00:00",
        )
        blob = json.dumps(manifest)
        assert "super-secret-manifest-test" not in blob
        assert manifest["sha256"]["QQQ"] == backfill.sha256_of_file(out)
        assert manifest["source"] == "Polygon.io"
        assert manifest["adjusted"] is True

    def test_manifest_includes_script_sha256_regardless_of_commit_status(self, tmp_path):
        bars = [backfill.Bar5m("2025-06-10T09:30:00-04:00", 1, 1, 1, 1, 1)]
        out = tmp_path / "QQQ_5min.csv"
        backfill.write_csv(out, bars)
        coverage = backfill.coverage_report({"QQQ": bars})
        manifest = backfill.build_manifest(
            symbols=("QQQ",), requested_start="2025-01-01", requested_end="2025-06-10",
            coverage=coverage, output_paths={"QQQ": out}, row_counts={"QQQ": 1},
            repo_root=tmp_path, retrieved_at="2025-06-10T00:00:00+00:00",
        )
        # tmp_path isn't the real repo, so this is "unknown" -- but the field
        # must always exist and never silently be dropped.
        assert manifest["script_sha256"] == backfill.sha256_of_file(Path(backfill.__file__))
        assert "script_commit_status" in manifest
        assert "script_commit_sha" in manifest


# ── script_commit_status (isolated temp git repos, not this real repo --
# a test tied to whether THIS script happens to be committed yet would
# break the moment it actually gets committed) ──────────────────────────────
def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, timeout=10)


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "README.md").write_text("placeholder\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "initial")


class TestScriptCommitStatus:
    def test_unknown_when_not_a_git_repo(self, tmp_path):
        script = tmp_path / "fake_script.py"
        script.write_text("print('hi')\n")
        result = backfill.script_commit_status(tmp_path, script)
        assert result["status"] == "unknown"
        assert result["commit_sha"] is None

    def test_uncommitted_when_untracked(self, tmp_path):
        repo = tmp_path / "repo"
        _init_repo(repo)
        script = repo / "fake_script.py"
        script.write_text("print('new, never committed')\n")
        result = backfill.script_commit_status(repo, script)
        assert result["status"] == "uncommitted"
        assert result["commit_sha"] is None
        assert "head_sha_at_manifest_time" in result

    def test_uncommitted_when_locally_modified_after_commit(self, tmp_path):
        repo = tmp_path / "repo"
        _init_repo(repo)
        script = repo / "fake_script.py"
        script.write_text("print('v1')\n")
        _git(repo, "add", "fake_script.py")
        _git(repo, "commit", "-q", "-m", "add script")
        script.write_text("print('v2, edited after commit')\n")
        result = backfill.script_commit_status(repo, script)
        assert result["status"] == "uncommitted"
        assert result["commit_sha"] is None

    def test_committed_when_matches_head_exactly(self, tmp_path):
        repo = tmp_path / "repo"
        _init_repo(repo)
        script = repo / "fake_script.py"
        script.write_text("print('committed, unchanged')\n")
        _git(repo, "add", "fake_script.py")
        _git(repo, "commit", "-q", "-m", "add script")
        head_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
        result = backfill.script_commit_status(repo, script)
        assert result["status"] == "committed"
        assert result["commit_sha"] == head_sha
        assert len(head_sha) == 40


# ── CLI wiring ───────────────────────────────────────────────────────────────
class TestParseArgs:
    def test_defaults(self):
        args = backfill.parse_args([])
        assert args.symbols == "QQQ,TQQQ,SQQQ"
        assert args.months_back == 18

    def test_missing_api_key_blocks_before_any_request(self, monkeypatch, tmp_path):
        monkeypatch.delenv("POLYGON_API_KEY", raising=False)
        code = backfill.main(["--repo-root", str(tmp_path), "--as-of", "2025-06-10"])
        assert code == 2


# ── read_csv_bars + --no-fetch manifest rebuild ─────────────────────────────
class TestReadCsvBars:
    def test_round_trip(self, tmp_path):
        bars = [
            backfill.Bar5m("2025-06-10T09:30:00-04:00", 1.0, 2.0, 0.5, 1.5, 100),
            backfill.Bar5m("2025-06-10T09:35:00-04:00", 1.5, 2.5, 1.0, 2.0, 200),
        ]
        path = tmp_path / "QQQ_5min.csv"
        backfill.write_csv(path, bars)
        reread = backfill.read_csv_bars(path)
        assert reread == bars


class TestNoFetchMode:
    def _seed_prior_fetch(self, tmp_path, symbol="QQQ"):
        out_dir = tmp_path / "data" / "stocks_advisory_polygon_5m"
        bars = [
            backfill.Bar5m(f"2025-06-10T09:{30+i}:00-04:00", 100 + i, 101 + i, 99 + i, 100.5 + i, 1000)
            for i in range(5)
        ]
        path = out_dir / f"{symbol}_5min.csv"
        backfill.write_csv(path, bars)
        original_manifest = {
            "retrieval_timestamp_utc": "2025-06-10T00:00:00+00:00",
            "sha256": {symbol: backfill.sha256_of_file(path)},
        }
        (out_dir / "manifest.json").write_text(json.dumps(original_manifest), encoding="utf-8")
        return out_dir, path

    def test_blocks_if_csv_missing(self, tmp_path, monkeypatch):
        monkeypatch.delenv("POLYGON_API_KEY", raising=False)
        code = backfill.main([
            "--repo-root", str(tmp_path), "--symbols", "QQQ",
            "--no-fetch", "--skip-bats-check", "--as-of", "2025-06-10",
        ])
        assert code == 2

    def test_does_not_require_api_key(self, tmp_path, monkeypatch):
        monkeypatch.delenv("POLYGON_API_KEY", raising=False)
        self._seed_prior_fetch(tmp_path)
        code = backfill.main([
            "--repo-root", str(tmp_path), "--symbols", "QQQ",
            "--no-fetch", "--skip-bats-check", "--as-of", "2025-06-10",
        ])
        assert code == 0

    def test_preserves_csv_bytes_and_original_retrieval_timestamp(self, tmp_path, monkeypatch):
        monkeypatch.delenv("POLYGON_API_KEY", raising=False)
        out_dir, csv_path = self._seed_prior_fetch(tmp_path)
        hash_before = backfill.sha256_of_file(csv_path)

        code = backfill.main([
            "--repo-root", str(tmp_path), "--symbols", "QQQ",
            "--no-fetch", "--skip-bats-check", "--as-of", "2025-06-10",
        ])
        assert code == 0

        hash_after = backfill.sha256_of_file(csv_path)
        assert hash_after == hash_before  # CSV bytes untouched by a --no-fetch run

        manifest = json.loads((out_dir / "manifest.json").read_text())
        assert manifest["retrieval_timestamp_utc"] == "2025-06-10T00:00:00+00:00"  # preserved, not overwritten
        assert "manifest_rebuilt_at_utc" in manifest  # new field records when the rebuild happened
        assert manifest["sha256"]["QQQ"] == hash_after
        assert manifest["row_counts"]["QQQ"] == 5
