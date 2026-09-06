"""Keep operator host addresses out of the repository.

The box address reached this public repo four separate times -- a functional
default in ops/ and scripts/, prose in docs/, and (least expected) a test
asserting that the address was absent, written by naming it. Scrubbing those
one at a time does not stop the next one, so assert the property directly:
no routable IPv4 literal in tracked source, docs or scripts.

Host identity belongs in the operator's environment -- AFS_BOX for ssh
targets, AFS_API_BASE / WEBHOOK_URL for service URLs -- matching
scripts/atomic_release.sh and scripts/afs-drift-gate.sh.
"""

import ipaddress
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SCANNED_SUFFIXES = {".py", ".sh", ".md"}

# tests/ carries deliberately synthetic addresses as fixtures (ALLOWED_IPS
# samples, a log line exercising redaction). Those are not operator identity
# and must stay literal for the assertions to mean anything.
SKIPPED_PREFIXES = ("tests/",)

_IPV4 = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")


def _is_operator_identity(text):
    """True for addresses that could identify a real host.

    Loopback, unspecified, broadcast and RFC1918 addresses are configuration
    rather than identity: they name "this machine" or a LAN, and reveal
    nothing. Anything globally routable is treated as a real host.
    """
    try:
        address = ipaddress.IPv4Address(text)
    except ipaddress.AddressValueError:
        return False  # e.g. a four-part version string, not an address
    return address.is_global


def _tracked_files():
    listing = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    for rel in listing.split("\0"):
        if not rel or rel.startswith(SKIPPED_PREFIXES):
            continue
        if Path(rel).suffix in SCANNED_SUFFIXES:
            yield rel


def test_no_routable_host_address_is_committed():
    offenders = []
    for rel in _tracked_files():
        path = ROOT / rel
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            for candidate in _IPV4.findall(line):
                if _is_operator_identity(candidate):
                    offenders.append(f"{rel}:{number}: {candidate}")

    assert not offenders, (
        "Routable host address(es) committed to the repository. Take the host "
        "from the environment instead (AFS_BOX for ssh, AFS_API_BASE / "
        "WEBHOOK_URL for service URLs):\n  " + "\n  ".join(offenders)
    )
