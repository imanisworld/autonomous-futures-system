"""Options scanner public module.

The original single-setup implementation is preserved byte-for-byte in
``scanner_legacy``.  This facade re-exports its public/private module surface
for compatibility, then installs the options-only multi-setup paper collector.
No futures or broker execution code is imported here.
"""

from . import scanner_legacy as _legacy

for _name in dir(_legacy):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_legacy, _name)

from .multisetup_scanner import build_multisetup_scanner as _build_multisetup_scanner

OptionsScanner = _build_multisetup_scanner(_legacy.OptionsScanner)
OptionsScanner.__module__ = __name__
