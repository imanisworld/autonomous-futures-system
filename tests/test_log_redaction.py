"""Tests for webhook/log_redaction.py — secret never reaches the access log."""

from __future__ import annotations

import logging

from webhook.log_redaction import SecretRedactingFilter, _redact


def test_redacts_query_secret_in_string():
    assert _redact('POST /webhook/alert?secret=abc123XYZ HTTP/1.0') == \
        'POST /webhook/alert?secret=[REDACTED] HTTP/1.0'


def test_redacts_amid_other_params():
    assert _redact("/webhook/alert?foo=1&secret=s3kr3t&bar=2") == \
        "/webhook/alert?foo=1&secret=[REDACTED]&bar=2"


def test_non_secret_text_untouched():
    assert _redact("GET /health HTTP/1.1") == "GET /health HTTP/1.1"


def test_filter_scrubs_uvicorn_access_style_args():
    flt = SecretRedactingFilter()
    rec = logging.LogRecord(
        name="uvicorn.access", level=logging.INFO, pathname="", lineno=0,
        msg='%s - "%s" %d',
        args=("1.2.3.4:0", "POST /webhook/alert?secret=topsecret HTTP/1.0", 200),
        exc_info=None,
    )
    assert flt.filter(rec) is True
    assert "topsecret" not in rec.getMessage()
    assert "secret=[REDACTED]" in rec.getMessage()


def test_filter_never_raises_on_bad_args():
    flt = SecretRedactingFilter()
    rec = logging.LogRecord("x", logging.INFO, "", 0, "msg", args=(object(),), exc_info=None)
    assert flt.filter(rec) is True  # non-str args pass through, no exception
