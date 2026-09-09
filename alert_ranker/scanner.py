"""Options scanner public module.

The original single-setup implementation is preserved byte-for-byte in
``scanner_legacy``.  This facade re-exports its public/private module surface
for compatibility, then installs the options-only multi-setup paper collector,
V1 evidence hardening, fail-closed scheduled-collection preflight, and
append-only diagnostics capture. No futures or broker execution code is
imported here.
"""

from . import scanner_legacy as _legacy

for _name in dir(_legacy):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_legacy, _name)

from .multisetup_scanner import build_multisetup_scanner as _build_multisetup_scanner
from .v1_diagnostics import build_v1_diagnostics_capture as _build_v1_diagnostics_capture
from .v1_evidence_hardening import build_v1_evidence_hardening as _build_v1_evidence_hardening
from .v1_runtime_preflight import build_v1_runtime_preflight as _build_v1_runtime_preflight

OptionsScanner = _build_multisetup_scanner(_legacy.OptionsScanner)
OptionsScanner = _build_v1_evidence_hardening(OptionsScanner)
OptionsScanner = _build_v1_runtime_preflight(OptionsScanner)
OptionsScanner = _build_v1_diagnostics_capture(OptionsScanner)
OptionsScanner.__module__ = __name__
