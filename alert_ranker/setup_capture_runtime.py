"""Setup-capture runtime surface.

The scanner-attached APScheduler jobs were removed. Capture runs as a oneshot
systemd timer (same pattern as options-122-prospective), not inside the
options-scanner event loop.
"""

from __future__ import annotations

from .setup_capture_engine import (
    SetupCaptureEngine,
    assert_no_forbidden_imports,
    module_has_no_execution_imports,
)

SetupCaptureRuntime = SetupCaptureEngine

__all__ = [
    "SetupCaptureEngine",
    "SetupCaptureRuntime",
    "assert_no_forbidden_imports",
    "module_has_no_execution_imports",
]
