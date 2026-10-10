"""Session prints for the six-micro paper journal. No network."""
from __future__ import annotations

import json
import os
import subprocess
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from research.six_micro_daily_tsmom_collect import (
    UNSCHEDULED_ROOTS,
    collect_listed,
    collect_scheduled,
    completed_through,
    listed_front,
    load_polygon_env,
    main,
    prints_for_listed_front,
    session_prints,
    verify_release_sha,
)
from research.six_micro_daily_tsmom_paper import PaperLedger
from sources.polygon_client import PolygonBar


def _bar(local: str, *, price: float = 100.0, ticker: str = "MNQU6") -> PolygonBar:
    ts = datetime.fromisoformat(local).replace(tzinfo=timezone.utc)
    # Tests pass UTC stamps that are already the New York wall clock shifted.
    # 13:30 UTC is 09:30 ET in October (EDT, UTC-4).
    return PolygonBar(ts=ts, open=price, high=price, low=price, close=price + 1, volume=1, ticker=ticker)


def test_completed_through_saturday_is_friday():
    now = datetime(2026, 10, 10, 15, 0, tzinfo=timezone.utc)
    assert completed_through(now) == date(2026, 10, 9)


def test_session_print_uses_the_open_and_the_1545_close():
    bars = [
        _bar("2026-10-09T13:30:00"),
        _bar("2026-10-09T19:45:00", price=10),
    ]
    prints = session_prints("MNQ", bars, start=date(2026, 6, 29), end=date(2026, 10, 9))
    assert len(prints) == 1
    assert prints[0].rth_open == bars[0].open
    assert prints[0].session_close == bars[1].close
    assert prints[0].contract == "MNQU6"


def test_missing_open_is_not_stored():
    bars = [_bar("2026-10-09T19:45:00")]
    assert session_prints("MNQ", bars, start=date(2026, 6, 29), end=date(2026, 10, 9)) == []


def test_missing_close_is_not_stored():
    bars = [_bar("2026-10-09T13:30:00")]
    assert session_prints("MNQ", bars, start=date(2026, 6, 29), end=date(2026, 10, 9)) == []


def test_gold_crude_and_bitcoin_are_not_in_the_scheduled_fetch():
    assert UNSCHEDULED_ROOTS == ("MGC", "MCL", "MBT")


def test_listed_front_is_the_soonest_expiry_still_open():
    rows = [
        {"ticker": "MGCZ6", "first_trade_date": "2025-12-01", "last_trade_date": "2026-12-28"},
        {"ticker": "MGCV6", "first_trade_date": "2025-10-01", "last_trade_date": "2026-10-28"},
    ]
    assert listed_front("MGC", rows, date(2026, 10, 9)) == "MGCV6"


def test_two_contracts_keep_the_sooner_expiry():
    bars = [
        _bar("2026-10-09T13:30:00", ticker="MGCZ6"),
        _bar("2026-10-09T19:45:00", ticker="MGCZ6", price=10),
        _bar("2026-10-09T13:30:00", ticker="MGCV6", price=20),
        _bar("2026-10-09T19:45:00", ticker="MGCV6", price=30),
    ]
    rows = [
        {"ticker": "MGCZ6", "first_trade_date": "2025-12-01", "last_trade_date": "2026-12-28"},
        {"ticker": "MGCV6", "first_trade_date": "2025-10-01", "last_trade_date": "2026-10-28"},
    ]
    prints = prints_for_listed_front(
        "MGC", bars, rows, start=date(2026, 6, 29), end=date(2026, 10, 9),
    )
    assert len(prints) == 1
    assert prints[0].contract == "MGCV6"


def test_listed_front_uses_last_trade_date_not_month_code():
    # Month code puts V (October) ahead of Z (December). Last trade does not.
    rows = [
        {"ticker": "MGCV6", "first_trade_date": "2025-10-01", "last_trade_date": "2026-11-20"},
        {"ticker": "MGCZ6", "first_trade_date": "2025-12-01", "last_trade_date": "2026-10-20"},
    ]
    assert listed_front("MGC", rows, date(2026, 10, 9)) == "MGCZ6"


def test_expired_listing_is_not_used():
    rows = [
        {"ticker": "MGCV6", "first_trade_date": "2025-10-01", "last_trade_date": "2026-09-28"},
        {"ticker": "MGCZ6", "first_trade_date": "2025-12-01", "last_trade_date": "2026-12-28"},
    ]
    assert listed_front("MGC", rows, date(2026, 10, 9)) == "MGCZ6"


def test_journal_under_research_evidence_is_refused(tmp_path):
    with pytest.raises(ValueError):
        PaperLedger(tmp_path / "research-evidence" / "journal")


def test_collect_refuses_a_session_that_has_not_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "research.six_micro_daily_tsmom_collect.completed_through",
        lambda _now: date(2026, 10, 9),
    )

    class Client:
        def fetch_bars(self, *_args, **_kwargs):
            raise AssertionError("client must not be called")

        def fetch_contract_listings(self, *_args, **_kwargs):
            raise AssertionError("client must not be called")

    with pytest.raises(ValueError, match="has not closed"):
        collect_scheduled(tmp_path, Client(), end=date(2026, 10, 12))
    with pytest.raises(ValueError, match="has not closed"):
        collect_listed(tmp_path, Client(), end=date(2026, 10, 12))


def test_collect_listed_uses_last_trade_date(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "research.six_micro_daily_tsmom_collect.completed_through",
        lambda _now: date(2026, 10, 9),
    )
    rows = [
        {"ticker": "MGCV6", "first_trade_date": "2025-10-01", "last_trade_date": "2026-11-20"},
        {"ticker": "MGCZ6", "first_trade_date": "2025-12-01", "last_trade_date": "2026-10-20"},
    ]

    class Client:
        def __init__(self):
            self.listings = []

        def fetch_bars(self, ticker, _start, _end, _minutes):
            if ticker not in {"MGCV6", "MGCZ6"}:
                return []
            price = 50.0 if ticker == "MGCZ6" else 1.0
            return [
                _bar("2026-10-09T13:30:00", ticker=ticker, price=price),
                _bar("2026-10-09T19:45:00", ticker=ticker, price=price + 10),
            ]

        def fetch_contract_listings(self, root, _tickers):
            self.listings.append(root)
            return rows if root == "MGC" else []

    client = Client()
    collect_listed(
        tmp_path,
        client,
        start=date(2026, 10, 9),
        end=date(2026, 10, 9),
    )
    assert "MGC" in client.listings
    lines = [
        json.loads(line)
        for line in (tmp_path / "round_turns.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    mgc = [row for row in lines if row["root"] == "MGC"]
    assert mgc[-1]["contract"] == "MGCZ6"
    assert mgc[-1]["kind"] == "PRE_REGISTRATION"


def test_release_sha_must_match_the_running_commit(tmp_path, monkeypatch):
    sha = "f0cfd3960e1b0cf8ee79c13f653d8b0df0721791"
    (tmp_path / "release_manifest.json").write_text(
        json.dumps({"repo": {"commit": sha}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("AFS_RELEASE_SHA", sha)
    assert verify_release_sha(tmp_path) == sha
    monkeypatch.setenv("AFS_RELEASE_SHA", "b" * 40)
    with pytest.raises(SystemExit, match="release SHA pin failed"):
        verify_release_sha(tmp_path)


def test_missing_release_sha_exits(tmp_path, monkeypatch):
    monkeypatch.delenv("AFS_RELEASE_SHA", raising=False)
    with pytest.raises(SystemExit, match="release SHA pin failed"):
        verify_release_sha(tmp_path)


def test_release_directory_name_is_the_running_sha(tmp_path, monkeypatch):
    sha = "c" * 40
    repo = tmp_path / "afs-releases" / sha
    repo.mkdir(parents=True)
    monkeypatch.setenv("AFS_RELEASE_SHA", sha)
    assert verify_release_sha(repo) == sha


def test_main_verify_sha_uses_git_head(monkeypatch):
    sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], text=True,
    ).strip()
    monkeypatch.setenv("AFS_RELEASE_SHA", sha)
    assert main(["--verify-sha"]) is None
    monkeypatch.setenv("AFS_RELEASE_SHA", "d" * 40)
    with pytest.raises(SystemExit, match="release SHA pin failed"):
        main(["--verify-sha"])


def test_main_refuses_the_shared_env_file(monkeypatch, tmp_path):
    sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], text=True,
    ).strip()
    monkeypatch.setenv("AFS_RELEASE_SHA", sha)
    shared = tmp_path / ".env"
    shared.write_text(
        "POLYGON_API_KEY=polygon-test\nTRADOVATE_API_KEY_SECRET=secret-value\n",
        encoding="utf-8",
    )
    with pytest.raises(SystemExit, match="polygon-only") as caught:
        main(["--env-file", str(shared), "--journal", str(tmp_path / "journal")])
    assert "secret-value" not in str(caught.value)
    polygon = tmp_path / "six-micro-polygon.env"
    polygon.write_text(
        "POLYGON_API_KEY=polygon-test\nTRADOVATE_PASSWORD=secret-value\n",
        encoding="utf-8",
    )
    with pytest.raises(SystemExit, match="polygon-only") as caught:
        load_polygon_env(polygon)
    assert "secret-value" not in str(caught.value)


def test_polygon_env_does_not_load_the_repo_dotenv(monkeypatch, tmp_path):
    sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], text=True,
    ).strip()
    monkeypatch.setenv("AFS_RELEASE_SHA", sha)
    monkeypatch.delenv("POLYGON_API_KEY", raising=False)
    env = tmp_path / "six-micro-polygon.env"
    env.write_text(
        "POLYGON_API_KEY=polygon-test\nPOLYGON_BASE_URL=https://example.invalid\n",
        encoding="utf-8",
    )

    class DummyLedger:
        def __init__(self, _journal):
            self.state = {"closes": {}}
            self.scoring_round_turns = 0

    monkeypatch.setattr(
        "research.six_micro_daily_tsmom_collect.PolygonFuturesClient",
        lambda **_kwargs: object(),
    )
    monkeypatch.setattr(
        "research.six_micro_daily_tsmom_collect.PaperLedger",
        DummyLedger,
    )
    monkeypatch.setattr(
        "research.six_micro_daily_tsmom_collect.collect_scheduled",
        lambda *_args, **_kwargs: type("Result", (), {"sessions": {}})(),
    )
    monkeypatch.setattr(
        "research.six_micro_daily_tsmom_collect.collect_listed",
        lambda *_args, **_kwargs: {},
    )
    main(["--env-file", str(env), "--journal", str(tmp_path / "journal")])
    assert os.environ["POLYGON_API_KEY"] == "polygon-test"
    text = Path("research/six_micro_daily_tsmom_collect.py").read_text(encoding="utf-8")
    assert "load_dotenv" not in text


def test_timer_is_1710_et_and_the_drop_in_is_not_root():
    timer = Path("ops/systemd/six-micro-daily-tsmom.timer").read_text(encoding="utf-8")
    service = Path("ops/systemd/six-micro-daily-tsmom.service").read_text(encoding="utf-8")
    drop_in = Path(
        "ops/systemd/six-micro-daily-tsmom.service.d/10-release.conf.template"
    ).read_text(encoding="utf-8")
    prereg = Path("docs/prereg-six-micro-daily-tsmom-forward-2026-10-10.md").read_text(
        encoding="utf-8",
    )
    assert "OnCalendar=Mon..Fri *-*-* 17:10:00 America/New_York" in timer
    assert "17:10 America/New_York" in prereg
    assert "4:10" not in timer
    assert "4:10" not in prereg
    assert "User=afs-paper" in service
    assert "User=afs-paper" in drop_in
    assert "AFS_RELEASE_SHA=@RELEASE_SHA@" in drop_in
    assert "--verify-sha" in drop_in
    assert "EnvironmentFile=/root/afs-shared/.env" not in drop_in
    assert "EnvironmentFile=/root/afs-shared/six-micro-polygon.env" in drop_in
    assert "--env-file /root/afs-shared/six-micro-polygon.env" in drop_in
    assert "ExecStart=/bin/false" in service
