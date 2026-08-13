from alert_ranker.preflight import build_preflight_report


def test_preflight_reports_missing_configuration_without_values():
    report = build_preflight_report(environ={})

    assert report["status"] == "configuration_missing"
    assert report["ready_for_local_advisory_start"] is False
    assert report["missing_configuration"] == [
        "OPTIONS_SCANNER_ENABLED=true",
        "PUBLIC_API_SECRET_KEY",
        "PUBLIC_ACCOUNT_ID",
    ]
    assert report["configuration"]["PUBLIC_API_SECRET_KEY"] == "missing"
    assert report["configuration"]["PUBLIC_ACCOUNT_ID"] == "missing"
    assert report["boundary"]["network_called"] is False


def test_preflight_redacts_configuration_and_proves_read_only_boundary():
    secret = "never-print-this-secret"
    account = "never-print-this-account"
    report = build_preflight_report(
        environ={
            "OPTIONS_SCANNER_ENABLED": "true",
            "OPTIONS_MARKET_DATA_PROVIDER": "public",
            "PUBLIC_API_SECRET_KEY": secret,
            "PUBLIC_ACCOUNT_ID": account,
        }
    )

    rendered = str(report)
    assert report["status"] == "ok"
    assert report["ready_for_local_advisory_start"] is True
    assert report["boundary"]["ok"] is True
    assert report["boundary"]["trading_account_order_paths"] == "blocked"
    assert report["trading_reachable"] is False
    assert secret not in rendered
    assert account not in rendered
    assert "<redacted-account-pin>" in rendered


def test_preflight_accepts_legacy_secret_only_as_reported_fallback():
    report = build_preflight_report(
        environ={
            "OPTIONS_SCANNER_ENABLED": "true",
            "OPTIONS_MARKET_DATA_PROVIDER": "public",
            "PUBLIC_API_KEY": "legacy-secret",
            "PUBLIC_ACCOUNT_ID": "pinned-account",
        }
    )

    assert report["ready_for_local_advisory_start"] is True
    assert report["configuration"]["secret_source"] == "PUBLIC_API_KEY (legacy fallback)"
