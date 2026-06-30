"""Redact the webhook secret from access logs.

While query-string auth is still enabled (`ALLOW_SECRET_IN_QUERY`, default on),
TradingView sends `?secret=<value>` on every alert and uvicorn's access logger
writes the full URL to stdout/journald in plaintext — so the live secret sits in
`journalctl` on every bar. This installs a logging filter on the `uvicorn.access`
logger (and the root) that rewrites `secret=<value>` → `secret=[REDACTED]` before
the line is emitted. Pure logging hygiene: it does NOT change authentication
(the real value still flows to `_resolve_inbound_secret`), and is the interim
mitigation until alerts migrate to body/header auth and query auth is disabled.
"""

from __future__ import annotations

import logging
import re

_SECRET_RE = re.compile(r"(secret=)[^&\s\"'}]+", re.IGNORECASE)
_FILTER_FLAG = "_secret_redaction_installed"


def _redact(value: str) -> str:
    return _SECRET_RE.sub(r"\1[REDACTED]", value)


class SecretRedactingFilter(logging.Filter):
    """Scrub `secret=...` from a log record's message and string args."""

    def filter(self, record: logging.LogRecord) -> bool:  # always passes the record through
        try:
            if isinstance(record.msg, str) and "secret=" in record.msg.lower():
                record.msg = _redact(record.msg)
            if record.args:
                if isinstance(record.args, dict):
                    record.args = {
                        k: (_redact(v) if isinstance(v, str) else v)
                        for k, v in record.args.items()
                    }
                else:
                    record.args = tuple(
                        _redact(a) if isinstance(a, str) else a for a in record.args
                    )
        except Exception:
            # Logging must never raise — pass the record through untouched on error.
            pass
        return True


def install_secret_redaction() -> None:
    """Idempotently attach the redaction filter to the loggers that emit URLs."""
    flt = SecretRedactingFilter()
    for name in ("uvicorn.access", "uvicorn", ""):  # "" = root, catches app loggers too
        lg = logging.getLogger(name)
        if not getattr(lg, _FILTER_FLAG, False):
            lg.addFilter(flt)
            setattr(lg, _FILTER_FLAG, True)


# Self-install on import so a single `import webhook.log_redaction` is enough.
install_secret_redaction()
