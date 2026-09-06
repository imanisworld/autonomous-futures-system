"""Path-contract tests for the options companion SQLite ledger."""

from __future__ import annotations

from pathlib import Path

from config.settings import load_config, options_companion_sqlite_path
from options_companion import daily_report


def test_config_default_resolves_companion_sqlite_under_log_dir(monkeypatch, tmp_path):
    shared = tmp_path / "shared-logs"
    monkeypatch.setenv("LOG_DIR", str(shared))
    # An empty exported value also prevents a developer .env from supplying an
    # override during this test; empty means "use the default".
    monkeypatch.setenv("OPTIONS_COMPANION_SQLITE_PATH", "")

    config = load_config()

    assert options_companion_sqlite_path() == shared / "options_companion.sqlite"
    assert Path(config.options_companion_sqlite_path) == shared / "options_companion.sqlite"


def test_explicit_companion_sqlite_path_takes_precedence(monkeypatch, tmp_path):
    shared = tmp_path / "shared-logs"
    explicit = tmp_path / "custom" / "companion.db"
    monkeypatch.setenv("LOG_DIR", str(shared))
    monkeypatch.setenv("OPTIONS_COMPANION_SQLITE_PATH", str(explicit))

    assert options_companion_sqlite_path() == explicit
    assert Path(load_config().options_companion_sqlite_path) == explicit


def test_daily_report_default_writes_under_log_dir_not_release_cwd(
    monkeypatch, tmp_path
):
    release_root = tmp_path / "release"
    release_logs = release_root / "logs"
    release_logs.mkdir(parents=True)
    shared = tmp_path / "shared-logs"
    monkeypatch.chdir(release_root)
    monkeypatch.setenv("LOG_DIR", str(shared))
    monkeypatch.setenv("OPTIONS_COMPANION_SQLITE_PATH", "")
    monkeypatch.setattr(daily_report, "notify_companion_daily_report", lambda _report: False)

    assert daily_report.main() == 0

    assert (shared / "options_companion.sqlite").exists()
    assert list(release_logs.iterdir()) == []


def test_weekly_review_uses_explicit_companion_sqlite_override(monkeypatch, tmp_path):
    from scripts import weekly_review

    explicit = tmp_path / "custom" / "companion.db"
    monkeypatch.setenv("LOG_DIR", str(tmp_path / "shared-logs"))
    monkeypatch.setenv("OPTIONS_COMPANION_SQLITE_PATH", str(explicit))
    seen: list[Path] = []
    monkeypatch.setattr(
        weekly_review,
        "load_option_rows",
        lambda path, _monday, _sunday: seen.append(Path(path)) or [],
    )
    monkeypatch.setattr(weekly_review, "load_journal", lambda *_args: [])
    monkeypatch.setattr(weekly_review, "collect_health", lambda *_args: {})
    monkeypatch.setenv("WEEKLY_REVIEW_DATE", "2026-06-27")
    monkeypatch.delenv("DISCORD_ROUTE_DAILY_REPORT", raising=False)
    monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)

    assert weekly_review.main() == 0
    assert seen == [explicit]
