#!/usr/bin/env python3
"""AFS futures collection watcher — READ-ONLY, detached, self-running.

Scope (operator instruction 2026-09-02): observe the production futures runtime
and the forward A/B campaign evidence for release b3d72f8 / epoch
2026-09-02T01:15:19Z.  It never changes the trading system.

Hard read-only guarantees, in layers:
  * runs inside a private mount namespace where /root, /etc, /opt and
    /usr/local are bind-mounted READ-ONLY (see run_ro.sh);
  * every production file is opened read-only (O_RDONLY) — the only writes go
    to STATE_DIR under /tmp;
  * the only commands executed are read-only introspection (systemctl show /
    is-active / list-units, journalctl, readlink, df, pgrep) plus the release's
    own read-only report scripts, run with PYTHONDONTWRITEBYTECODE=1;
  * HTTP: GET only, to the local status API on 127.0.0.1:8000.  The single
    outbound POST is a Discord notification on an already-configured route;
  * no broker library is imported; no Tradovate endpoint is contacted;
  * no code path references service restart/stop, deploy, env edit, or order
    placement.  `static_selfcheck()` refuses to start if such tokens appear.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import traceback
import urllib.request
from datetime import datetime, timedelta, timezone, date
from pathlib import Path
from zoneinfo import ZoneInfo

from watcher_memory_guard import MemoryReading, evaluate_memory, sample_process_memory
try:
    import watcher_triage  # optional read-only advisory lane; a missing copy must never stop the watcher
except ImportError:  # pragma: no cover — exercised only by a partial install
    watcher_triage = None

# ── fixed facts ──────────────────────────────────────────────────────────────
RELEASE_LINK = Path("/root/autonomous-futures-system")
SHARED = Path("/root/afs-shared")
LOG_DIR = SHARED / "logs"
ENV_FILE = SHARED / ".env"

# The release and epoch this watcher checks against are NOT literals. They used
# to be, and nothing in the deploy path updated them: every sanctioned release
# left the watcher reporting BLOCKED until someone hand-edited this file and
# restarted the unit. An alarm that fires on every correct deploy is one people
# learn to clear without reading.
#
# The release wrapper already pins its identity into the shared .env during the
# atomic promote (EXPECTED_LIVE_COMMIT, EXPECTED_RELEASE_FINGERPRINT) and the
# epoch step pins the evidence window twice. Those pins are the authoritative
# deploy-written record, so reading them here means a sanctioned deploy re-arms
# this watcher by doing what it already does. A deploy that bypasses the wrapper
# does not re-pin, so it still BLOCKS — which is the whole reason this runs.
COMMIT_PIN = "EXPECTED_LIVE_COMMIT"
FINGERPRINT_PIN = "EXPECTED_RELEASE_FINGERPRINT"
EPOCH_PIN = "MNQ_ORB_BREAKOUT_INVERSE_EPOCH_START"
EPOCH_PROOF_PIN = "EXPECTED_PROOF_MNQ_ORB_BREAKOUT_INVERSE_EPOCH_START"


def read_env_pins(env_path: Path) -> dict[str, str]:
    """Parse `KEY=VALUE` lines. Later assignments win, matching shell `.env`."""
    values: dict[str, str] = {}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key:
            values[key] = value.strip().strip("'\"")
    return values


def load_deploy_pins(env_path: Path) -> tuple[dict[str, str] | None, str | None]:
    """Read the deploy's pinned identity. Returns `(None, reason)` on any doubt.

    The two epoch pins must agree: the service's own startup guard refuses to
    boot when they diverge, so a divergence seen here means one was rewritten
    while the service was already running.
    """
    try:
        values = read_env_pins(env_path)
    except OSError as exc:
        return None, f"cannot read the deploy's pinned identity at {env_path}: {exc}"

    wanted = (COMMIT_PIN, FINGERPRINT_PIN, EPOCH_PIN, EPOCH_PROOF_PIN)
    missing = [k for k in wanted if not values.get(k)]
    if missing:
        return None, f"deploy pins missing or blank in {env_path}: {', '.join(missing)}"
    if values[EPOCH_PIN] != values[EPOCH_PROOF_PIN]:
        return None, (f"epoch pins disagree: {EPOCH_PIN}={values[EPOCH_PIN]} but "
                      f"{EPOCH_PROOF_PIN}={values[EPOCH_PROOF_PIN]} — the startup "
                      "guard would refuse to boot on this configuration")
    try:
        datetime.strptime(values[EPOCH_PIN], "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None, f"{EPOCH_PIN}={values[EPOCH_PIN]!r} is not an ISO-8601 UTC instant"
    return {k: values[k] for k in wanted}, None


def manifest_identity(release_dir: Path) -> tuple[str | None, str | None]:
    """`(commit, fingerprint)` from a release manifest; `None` on any failure."""
    try:
        data = json.loads((release_dir / "release_manifest.json").read_text(encoding="utf-8"))
        repo = data.get("repo")
        commit = repo.get("commit") if isinstance(repo, dict) else None
        fingerprint = data.get("fingerprint_sha256")
    except (OSError, ValueError, AttributeError):
        return None, None
    return (commit if isinstance(commit, str) else None,
            fingerprint if isinstance(fingerprint, str) else None)


def resolve_release_dir(link: Path, pins: dict[str, str]) -> tuple[Path | None, str | None]:
    """Resolve the release link, then VERIFY it before it is ever used.

    Resolve-then-verify, and only once: this directory is where the watcher
    executes a Python interpreter from, so it must never follow a link to
    something the deploy did not pin. A directory whose manifest does not match
    both the pinned commit and the pinned fingerprint is refused outright rather
    than adopted and reported on — being wrong about which release is live is
    the one error this process cannot recover from.
    """
    try:
        resolved = Path(os.path.realpath(link))
    except OSError as exc:
        return None, f"cannot resolve release link {link}: {exc}"
    if not resolved.is_dir():
        return None, f"release link {link} does not resolve to a directory ({resolved})"

    commit, fingerprint = manifest_identity(resolved)
    if commit is None or fingerprint is None:
        return None, f"release manifest under {resolved} is unreadable — cannot verify identity"
    if not _sha_match(commit, pins[COMMIT_PIN]):
        return None, (f"release at {resolved} is commit {commit[:12]}, but the deploy "
                      f"pinned {pins[COMMIT_PIN][:12]} — refusing to run against an "
                      "unpinned release")
    if fingerprint.strip().lower() != pins[FINGERPRINT_PIN].strip().lower():
        return None, (f"release at {resolved} has fingerprint {fingerprint[:16]}…, but the "
                      f"deploy pinned {pins[FINGERPRINT_PIN][:16]}… — source does not match "
                      "the pinned release")
    return resolved, None


def _sha_match(a: str, b: str) -> bool:
    """Compare commit ids recorded at different lengths; never on a colliding prefix."""
    a, b = a.strip().lower(), b.strip().lower()
    n = min(len(a), len(b))
    return bool(a) and bool(b) and n >= 12 and a[:n] == b[:n]


# Resolved once, at import, and then treated as fixed for the life of the
# process. `main()` refuses to start when either is unresolved, so every use
# below runs against a release the deploy pinned and this process verified.
DEPLOY_PINS, PINS_ERROR = load_deploy_pins(ENV_FILE)
if DEPLOY_PINS is not None:
    RELEASE_DIR, RELEASE_ERROR = resolve_release_dir(RELEASE_LINK, DEPLOY_PINS)
    RELEASE_SHA = DEPLOY_PINS[COMMIT_PIN]
    RELEASE_FINGERPRINT = DEPLOY_PINS[FINGERPRINT_PIN]
    EPOCH = datetime.strptime(DEPLOY_PINS[EPOCH_PIN], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    INTERIM_AT = EPOCH + timedelta(days=14)
else:
    RELEASE_DIR, RELEASE_ERROR = None, PINS_ERROR
    RELEASE_SHA = RELEASE_FINGERPRINT = ""
    EPOCH = INTERIM_AT = None

CAMPAIGN_ID = "forward_ab_2026_08_v1"
CAMPAIGN_JSONL = LOG_DIR / f"{CAMPAIGN_ID}.jsonl"
CAMPAIGN_STATE = LOG_DIR / f"{CAMPAIGN_ID}_state.json"
FEED_STATE = LOG_DIR / "feed_gap_alarm_state.json"
CAMPAIGN_CONFIG = (RELEASE_DIR / "config" / "forward_evidence_campaign.json") if RELEASE_DIR else None
EXPECTED_POPULATIONS = [
    ("vwap_hold", "control"), ("vwap_hold", "modified"),
    ("orb_reclaim", "control"), ("orb_reclaim", "modified"),
    ("vwap_rejection", "observer"),
]
GATE_MIN_FILLED = 30
GATE_MIN_DAYS = 20
SERVICE = "futures-bot"
API = "http://127.0.0.1:8000"
ET = ZoneInfo("America/New_York")
# Session boundary from webhook/state_builder.detect_session @ b3d72f8:
# new_york = 09:30–16:59 ET; 17:00–17:59 ET maintenance halt.  Daily
# reconciliation runs once the New York session has closed (17:08 ET, Mon–Fri).
NY_CLOSE_ET = (17, 0)
DAILY_RUN_ET = (17, 8)
TICK_SECONDS = 300
TICK_OFFSET = 75            # run ~75 s after each 5-minute boundary (bars land first)
BLOCKED_REMINDER_S = 6 * 3600
JOURNAL_STALL_MIN = 20       # > one full 15-minute bar interval with alerts arriving
# Isolated hypothetical-ledger lanes (Daily 2-2, wide-stop 4HR / 3-2-2, MES 1-2-2).
# Every MNQ lane RESOLVES on the 5-minute stream under LOG_DIR/tf5m, so a 15m-only
# freshness view can read FRESH while no lane can close a position. The lane
# state/journal files below are the only heartbeats those collectors have.
LEDGER_DIR = LOG_DIR / "hypothetical_ledger"
FIVE_MIN_DIR = LOG_DIR / "tf5m"
LANE_STALL_MIN = 20          # 15m bar arrived this long after the newest 5m bar => 5m feed stalled
WIDE_STOP_MODE_PIN = "WIDE_STOP_LEDGER_MODE"
WIDE_STOP_EPOCH_PIN = "WIDE_STOP_LEDGER_EPOCH_START"
MES_122_MODE_PIN = "MES_122_PAPER_MODE"
MES_122_EPOCH_PIN = "MES_122_PAPER_EPOCH_START"
LANE_CENSUS_NAMES = ("bars MNQ 5m", "daily_22 swing state", "mes_122 lane journal")
# Existing lane semantics: Daily 2-2 is explicitly a swing ledger and may carry
# a paper position beyond the New York close. This does not create a hold limit.
EXPECTED_OVERNIGHT_LANES = {"daily_22_5k"}
MEMORY_HISTORY_SAMPLES = 7
MEMORY_WARNING_ROUTE = "DISCORD_ROUTE_ERROR"

# ── fixed-threshold memory / swap / OOM checks (additive, 2026-09-03) ────────
# The dynamic guard (watcher_memory_guard) derives RSS and headroom budgets from
# capacity.  These fixed checks cover only what it does not: swap in use and
# paging activity, the kernel OOM count, swap persistence, and a fixed-window
# sustained-growth rule on the process FOOTPRINT (VmRSS + VmSwap, so swap-out
# cannot mask growth).  Values come from the 2026-09-02 one-hour baseline
# (RSS band 516-612 MB, MemAvailable ~600 MB, OOM at anon RSS 824 MB / 79 MB
# headroom, 2 GB /swapfile added 20:07Z).  Alert-only: never kills or restarts.
MEM_FIXED_WINDOW_TICKS = 24            # ~2 h at the 5-min cadence
MEM_FIXED_MIN_SAMPLES = 12             # >= ~1 h of same-pid samples before growth rules apply
MEM_FIXED_ABSOLUTE_CHECKS = False      # the dynamic guard already enforces ~748/948 MB RSS and 401/200 MB headroom
MEM_FIXED_THRESHOLDS = {
    "rss_warn_mb": 750, "rss_crit_mb": 950,             # footprint (enforced only if MEM_FIXED_ABSOLUTE_CHECKS)
    "avail_warn_mb": 250, "avail_crit_mb": 120,         # MemAvailable (enforced only if MEM_FIXED_ABSOLUTE_CHECKS)
    "swap_warn_mb": 1400, "swap_crit_mb": 1800,         # swap in use, of 2048 MB
    "swap_activity_warn_mb_tick": 100, "swap_activity_crit_mb_tick": 300,  # MB paged in+out per tick
    "swap_activity_sustained_ticks": 3,                 # consecutive >=warn ticks that count as "sustained" paging
    "growth_warn_mb_2h": 150,                           # WARN: footprint rose this much over the window, mostly rising
    "growth_crit_mb_2h": 250,                           # CRITICAL: footprint rose this much over the window (any profile)
}
SWAP_PATH = "/swapfile"

STATE_DIR = Path(os.environ.get("AFS_WATCHER_STATE_DIR", "/tmp/afs_watcher"))
MEM_LOG = STATE_DIR / "memory.jsonl"      # one fixed-check sample per tick (append-only)
STATE_FILE = STATE_DIR / "state.json"
LOG_FILE = STATE_DIR / "watcher.log"
EVENTS_FILE = STATE_DIR / "events.jsonl"
LATEST_FILE = STATE_DIR / "latest_tick.json"
SNAP_DIR = STATE_DIR / "snapshots"
DAILY_DIR = STATE_DIR / "daily"
INTERIM_DIR = STATE_DIR / "interim"
NOTIFY_PREFIX = "[AFS WATCHER · read-only]"

READ_ONLY_COMMANDS = ("systemctl", "journalctl", "readlink", "df", "pgrep")
FORBIDDEN_TOKENS = (
    "systemctl restart", "systemctl stop", "systemctl start", "systemctl kill",
    "systemctl reload", "atomic_release", "afs-deploy", "ln -sfn", "/order/",
    "placeorder", "cancelorder", "liquidateposition", "flatten", "tradovate_broker",
    "TradovateBroker", "/admin/", "requests.post", "os.remove", "unlink(",
    "truncate(", "shutil.rmtree", "rename(", "write_text(", '"w"', "'w'",
    '"a"', "'a'",
)
# NOTE: this file's own writes use explicit helpers below that append/replace
# ONLY under STATE_DIR via explicit os.open flags (no bare write/append mode strings).


# ── tiny IO helpers (state dir only) ─────────────────────────────────────────
def _ensure_dirs() -> None:
    for d in (STATE_DIR, SNAP_DIR, DAILY_DIR, INTERIM_DIR):
        d.mkdir(parents=True, exist_ok=True)


def _under_state(p: Path) -> Path:
    p = Path(p).resolve()
    if STATE_DIR.resolve() not in p.parents and p != STATE_DIR.resolve():
        raise RuntimeError(f"refusing to write outside STATE_DIR: {p}")
    return p


def state_replace(p: Path, text: str) -> None:
    p = _under_state(p)
    tmp = p.with_suffix(p.suffix + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wt", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, p)


def state_append(p: Path, text: str) -> None:
    p = _under_state(p)
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "wt", encoding="utf-8") as fh:
        fh.write(text)


def read_prod_bytes(p: Path, max_bytes: int | None = None) -> bytes:
    fd = os.open(p, os.O_RDONLY)
    try:
        with os.fdopen(fd, "rb") as fh:
            return fh.read() if max_bytes is None else fh.read(max_bytes)
    finally:
        pass


def read_prod_text(p: Path) -> str:
    return read_prod_bytes(p).decode("utf-8", errors="replace")


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None) -> str | None:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z") if dt else None


def log(msg: str) -> None:
    line = f"{iso(now_utc())} {msg}\n"
    state_append(LOG_FILE, line)
    sys.stdout.write(line)
    sys.stdout.flush()


def run(cmd: list[str], timeout: int = 60, env: dict | None = None, cwd: str | None = None) -> tuple[int, str]:
    exe = os.path.basename(cmd[0])
    if exe not in READ_ONLY_COMMANDS:
        # Only the interpreter inside the VERIFIED release may run. With no
        # verified release there is no such path, so this refuses everything
        # outside the read-only allowlist rather than falling back.
        if RELEASE_DIR is None or not cmd[0].startswith(str(RELEASE_DIR / ".venv")):
            raise RuntimeError(f"command not in read-only allowlist: {cmd}")
    if exe == "systemctl" and cmd[1] not in ("show", "is-active", "list-units"):
        raise RuntimeError(f"systemctl verb not allowed: {cmd}")
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env, cwd=cwd)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, "TIMEOUT"


def http_get_json(path: str, timeout: int = 25) -> tuple[dict | None, str | None]:
    try:
        req = urllib.request.Request(API + path, method="GET", headers={"User-Agent": "afs-watcher-readonly"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8")), None
    except Exception as exc:  # noqa: BLE001
        return None, f"{type(exc).__name__}: {exc}"


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


HEX_DIGITS = "0123456789abcdef"
RELEASE_HISTORY = Path("/root/afs-shared/release_history.txt")


def _hex_sha(token: str) -> str:
    """Normalise a token to a lowercase hex SHA, or empty if it is not one."""
    head = token.strip().lower()
    if len(head) >= 12 and all(c in HEX_DIGITS for c in head):
        return head
    return ""


def release_history_shas(path: Path | None = None) -> set[str]:
    """Commit SHAs from the durable history the deploy appends to at promote time.

    The releases root is NOT a history: the deploy keeps only the most recent few
    directories and prunes the rest, so a release drops off it after a couple of
    promotes. This file is never pruned, which is what makes it the durable
    record. Missing or unreadable is not an error - the directory scan still
    covers releases promoted before this file existed.
    """
    out: set[str] = set()
    target = path if path is not None else RELEASE_HISTORY
    try:
        raw = target.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return out
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        sha = _hex_sha(stripped.split()[0])
        if sha:
            out.add(sha)
    return out


def known_release_shas(release_dir: Path | None) -> set[str]:
    """Commit SHAs this box has run: the durable history plus what is still on disk.

    Two sources, because neither alone is sufficient. This process is git-free at
    runtime, so ancestry cannot answer "was this a real release?". The release
    directories look like a history but are a ROLLING WINDOW - the deploy prunes
    all but the most recent few - so relying on them alone re-broke this check
    the moment three promotes happened in one evening. The history file is the
    durable record; the directory scan still covers anything promoted before it
    existed.
    """
    out: set[str] = release_history_shas()
    if release_dir is None:
        return out
    try:
        children = list(release_dir.parent.iterdir())
    except OSError:
        return out
    for child in children:
        try:
            if not child.is_dir():
                continue
        except OSError:
            continue
        sha = _hex_sha(child.name.split("-", 1)[0])
        if sha:
            out.add(sha)
    return out


def sha_is_known_release(sha: str, known: set[str]) -> bool:
    """True when `sha` is the live release, or one this box demonstrably ran.

    Evidence stamped with an EARLIER release is honest evidence that predates the
    current deploy, not a provenance defect - the previous rule compared every
    post-epoch row against the live SHA alone, so any deploy retroactively marked
    correctly-stamped rows bad. A SHA that was never deployed here is still a
    real defect and still blocks.
    """
    s = str(sha).strip().lower()
    if len(s) < 12:
        return False
    if RELEASE_SHA.startswith(s) or s.startswith(RELEASE_SHA[:12]):
        return True
    return any(s.startswith(k) or k.startswith(s) for k in known)


# ── static self-check ────────────────────────────────────────────────────────
def static_selfcheck() -> None:
    src = Path(__file__).read_text(encoding="utf-8")
    body = src.split("FORBIDDEN_TOKENS = (", 1)[1].split(")\n", 1)[1]  # skip the list itself
    hits = [t for t in FORBIDDEN_TOKENS if t in body]
    if hits:
        raise SystemExit(f"static self-check FAILED — forbidden tokens present: {hits}")


# ── state ────────────────────────────────────────────────────────────────────
def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            log("WARN state.json unreadable — starting fresh (old copy kept as state.json.corrupt)")
            shutil.copy2(STATE_FILE, STATE_DIR / "state.json.corrupt")
    return {
        "created_utc": iso(now_utc()), "release_sha": RELEASE_SHA, "epoch_utc": iso(EPOCH),
        "baseline": None, "files": {}, "events_seen": {}, "notified": {},
        "blocked": {}, "blocked_last_notified": {}, "daily_done": {}, "interim_done": False,
        "ticks": 0, "last_tick_utc": None, "last_journalctl_cursor_utc": None,
        "arm_milestones": {}, "first_fire": {},
    }


def save_state(state: dict) -> None:
    state_replace(STATE_FILE, json.dumps(state, indent=1, sort_keys=True))


def emit_event(state: dict, kind: str, key: str, payload: dict, notify_route: str | None) -> bool:
    """Record an event once per key.  Returns True if it was new."""
    if key in state["events_seen"]:
        return False
    state["events_seen"][key] = iso(now_utc())
    rec = {"utc": iso(now_utc()), "kind": kind, "key": key, **payload}
    state_append(EVENTS_FILE, json.dumps(rec, sort_keys=True) + "\n")
    log(f"EVENT {kind} {key} :: {payload.get('summary', '')}")
    if notify_route:
        notify(state, notify_route, f"{kind} — {payload.get('summary', key)}", key)
    return True


# ── notification (existing Discord routes; secrets never logged) ─────────────
def _env_value(key: str) -> str | None:
    try:
        for line in read_prod_text(ENV_FILE).splitlines():
            if line.startswith(key + "="):
                v = line.split("=", 1)[1].strip().strip('"').strip("'")
                return v or None
    except Exception:  # noqa: BLE001
        return None
    return None


def notify(state: dict, route: str, text: str, dedupe_key: str) -> None:
    if state["notified"].get(dedupe_key):
        return
    url = _env_value(route)
    if not url or not url.startswith("https://discord.com/api/webhooks/"):
        log(f"NOTIFY(unavailable route {route}) {text}")
        return
    body = json.dumps({"content": f"{text}\n-# {NOTIFY_PREFIX}"[:1900]}).encode("utf-8")
    try:
        req = urllib.request.Request(url, data=body, method="POST",
                                     headers={"Content-Type": "application/json", "User-Agent": "afs-watcher-readonly"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            code = resp.status
        state["notified"][dedupe_key] = iso(now_utc())
        log(f"NOTIFY sent via {route} (HTTP {code}): {text[:160]}")
    except Exception as exc:  # noqa: BLE001
        log(f"NOTIFY FAILED via {route}: {type(exc).__name__}: {exc} :: {text[:160]}")


# ── findings ─────────────────────────────────────────────────────────────────
class Findings:
    def __init__(self) -> None:
        self.items: list[dict] = []

    def add(self, level: str, key: str, summary: str, **detail) -> None:
        self.items.append({"level": level, "key": key, "summary": summary, "detail": detail})

    def blocked(self) -> list[dict]:
        return [f for f in self.items if f["level"] == "BLOCKED"]

    def warns(self) -> list[dict]:
        return [f for f in self.items if f["level"] == "WARN"]


# ── runtime checks ───────────────────────────────────────────────────────────
def _verified_release_identity() -> dict:
    """The release this process verified at startup — what a baseline is taken against."""
    return {"commit": RELEASE_SHA, "fingerprint": RELEASE_FINGERPRINT, "release_dir": str(RELEASE_DIR)}


def _baseline_record(props: dict, pid: str) -> dict:
    return {"ActiveEnterTimestamp": props.get("ActiveEnterTimestamp"), "NRestarts": props.get("NRestarts"),
            "ExecMainPID": pid, "recorded_utc": iso(now_utc()), "release": _verified_release_identity()}


def sanctioned_restart_reason(base: dict, props: dict, pid: str, cwd: str | None,
                              live_pins: dict | None, release_link: str | None,
                              other_blocked: list[str]) -> str | None:
    """Return `None` only when this restart is PROVABLY the sanctioned deploy's own.

    Every service restart is unexplained until shown otherwise. The one restart
    the watcher may adopt on its own is the atomic promote's: the wrapper
    switched the release link, re-pinned `.env`, and restarted the service —
    and this process was (re)armed from those same pins. The proof therefore
    has to tie the new process to a release CHANGE, not merely to a healthy
    box: a hand restart of the service on the same release looks identical in
    every other respect and must still BLOCK.

    `other_blocked` is every other BLOCKED key of the whole tick's runtime
    check — the decision is taken last, so nothing found later can be missed.
    Any doubt returns a reason, and the caller BLOCKS as before. Nothing here
    relaxes `service_crash_restart`, which the caller evaluates separately.
    """
    prev = base.get("release")
    if not isinstance(prev, dict) or not all(prev.get(k) for k in ("commit", "fingerprint", "release_dir")):
        return "baseline predates release-identity tracking"
    if prev == _verified_release_identity():
        return "same release as the baseline — no deploy explains this restart"
    if other_blocked:
        return "tick is BLOCKED on " + ", ".join(sorted(set(other_blocked)))
    if live_pins is None:
        return "deploy pins unreadable"
    if not _sha_match(live_pins[COMMIT_PIN], RELEASE_SHA) or \
            live_pins[FINGERPRINT_PIN].strip().lower() != RELEASE_FINGERPRINT.strip().lower():
        return "deploy pins do not name the release this watcher verified"
    if live_pins[EPOCH_PIN] != iso(EPOCH):
        return "epoch pin does not match the armed epoch"
    if release_link != str(RELEASE_DIR):
        return "release link is not the verified release"
    if props.get("ActiveState") != "active":
        return f"service ActiveState={props.get('ActiveState')}"
    if not pid or pid == "0" or pid == base.get("ExecMainPID"):
        return "no new main pid"
    if props.get("ActiveEnterTimestamp") == base.get("ActiveEnterTimestamp") or not props.get("ActiveEnterTimestamp"):
        return "ActiveEnterTimestamp did not move"
    if cwd != str(RELEASE_DIR):
        return f"live pid cwd={cwd} is not the verified release"
    try:
        if int(props.get("NRestarts", "")) != int(base.get("NRestarts", "")):
            return f"NRestarts changed {base.get('NRestarts')} → {props.get('NRestarts')}"
    except (TypeError, ValueError):
        return "NRestarts unreadable"
    return None


def settle_baseline(state: dict, f: Findings, rt: dict, base: dict | None, restart: dict | None,
                    props: dict, pid: str, cwd: str | None, live_pins: dict | None) -> None:
    """Last step of the runtime check, once every finding of the tick is in.

    `restart` is the provisional `unexpected_restart` finding already in `f`
    (added the moment the restart was seen, so an exception anywhere in the
    runtime check leaves the BLOCK standing). It is withdrawn ONLY when the
    restart is proven sanctioned; otherwise it stays, annotated with why not.
    """
    if base is None:
        return
    identity = _verified_release_identity()
    if restart is None:
        if not isinstance(base.get("release"), dict) and not f.blocked() and cwd == str(RELEASE_DIR):
            # Same process as the baseline, recorded before release identity was
            # tracked, on a clean tick: stamp the release it was taken against,
            # so the next sanctioned deploy has something to compare with.
            base["release"] = identity
        return
    other_blocked = [b["key"] for b in f.blocked() if b is not restart]
    why = sanctioned_restart_reason(base, props, pid, cwd, live_pins, rt.get("release_link"), other_blocked)
    if why is not None:
        restart["summary"] += f" (not adopted: {why})"
        restart["detail"]["not_adopted"] = why
        return
    # The sanctioned deploy's own restart: the process now running is the one
    # the pins name, from the release this watcher verified. Adopt it so the
    # alarm keeps meaning something; keep the old baseline in the record so
    # the adoption stays auditable. Record the event BEFORE mutating state.
    previous = {k: base.get(k) for k in ("ActiveEnterTimestamp", "ExecMainPID", "NRestarts", "release")}
    summary = restart["summary"]
    state.setdefault("events_seen", {})
    state.setdefault("notified", {})
    emit_event(state, "REBASELINED", f"sanctioned_restart:{RELEASE_SHA[:12]}:{pid}",
               {"summary": f"{summary} — adopted: sanctioned release {RELEASE_SHA[:12]} "
                           f"(was {str((previous.get('release') or {}).get('commit'))[:12]})"},
               "DISCORD_ROUTE_ERROR")
    f.items.remove(restart)
    state["baseline"] = {**_baseline_record(props, pid), "adopted_from": previous}
    rt["baseline_adopted"] = summary
    log(f"baseline adopted after sanctioned release {RELEASE_SHA[:12]}: {summary}")


def _is_webhook_process_line(line: str) -> bool:
    """Return True only for the actual futures-bot uvicorn process.

    pgrep -af can also return parent shell commands whose argument text
    happens to contain the service ExecStart string during a deployment.
    Count only the concrete python -m uvicorn webhook.app:app argv shape so
    a sanctioned promote shell is never mistaken for a second webhook server.
    """
    parts = line.strip().split()
    if len(parts) < 5 or not parts[0].isdigit():
        return False
    executable = os.path.basename(parts[1])
    if not executable.startswith("python"):
        return False
    return parts[2:5] == ["-m", "uvicorn", "webhook.app:app"]


def check_runtime(state: dict, f: Findings, tick: dict) -> None:
    rt: dict = {}
    tick["runtime"] = rt
    try:
        rt["release_link"] = os.path.realpath(RELEASE_LINK)
    except Exception as exc:  # noqa: BLE001
        rt["release_link"] = None
        f.add("BLOCKED", "release_link_unreadable", f"cannot resolve {RELEASE_LINK}: {exc}")
    live_pins, live_pins_err = load_deploy_pins(ENV_FILE)
    rt["deploy_pins_error"] = live_pins_err
    if live_pins is None:
        f.add("BLOCKED", "deploy_pins_unreadable",
              f"the deploy's pinned identity is unusable, so nothing can be verified: {live_pins_err}")
    elif live_pins[EPOCH_PIN] != iso(EPOCH):
        f.add("BLOCKED", "epoch_drift",
              f"evidence epoch pin is now {live_pins[EPOCH_PIN]}, but this watcher was armed "
              f"for {iso(EPOCH)} — the evidence window was redefined under a running watcher")

    if rt.get("release_link") and rt["release_link"] != str(RELEASE_DIR):
        live_commit, _ = manifest_identity(Path(rt["release_link"]))
        if live_pins is not None and live_commit and _sha_match(live_commit, live_pins[COMMIT_PIN]):
            # Sanctioned: the wrapper promoted and re-pinned. This process still
            # executes from the release it verified at startup, so it must be
            # restarted to adopt the new one — it must not follow the link.
            f.add("BLOCKED", "watcher_release_stale",
                  f"a sanctioned release is live ({live_commit[:12]}) but this watcher is still "
                  f"armed for {RELEASE_SHA[:12]}; restart the watcher to adopt it")
        else:
            f.add("BLOCKED", "unexpected_deploy",
                  f"release symlink now → {rt['release_link']}, which the deploy did not pin "
                  f"(armed for {RELEASE_SHA[:12]})")

    rc, out = run(["systemctl", "show", SERVICE, "-p", "ActiveState", "-p", "SubState", "-p", "ExecMainPID",
                   "-p", "NRestarts", "-p", "ActiveEnterTimestamp"])
    props = dict(l.split("=", 1) for l in out.splitlines() if "=" in l)
    rt["service"] = props
    if props.get("ActiveState") != "active":
        f.add("BLOCKED", "service_not_active", f"{SERVICE} ActiveState={props.get('ActiveState')} SubState={props.get('SubState')}")
    pid = props.get("ExecMainPID", "0")
    cwd = None
    if pid and pid != "0":
        try:
            cwd = os.readlink(f"/proc/{pid}/cwd")
        except Exception as exc:  # noqa: BLE001
            f.add("BLOCKED", "service_pid_cwd_unreadable", f"cannot read /proc/{pid}/cwd: {exc}")
    rt["service_cwd"] = cwd
    if cwd and cwd != str(RELEASE_DIR):
        f.add("BLOCKED", "service_wrong_release", f"live pid {pid} cwd={cwd}, expected {RELEASE_DIR}")

    base = state.get("baseline")
    restart: dict | None = None
    if base is None and props.get("ActiveState") == "active":
        state["baseline"] = _baseline_record(props, pid)
        log(f"baseline recorded: {state['baseline']}")
    elif base:
        if props.get("ActiveEnterTimestamp") != base["ActiveEnterTimestamp"] or pid != base["ExecMainPID"]:
            # Provisional BLOCK, settled at the end of this check (see settle_baseline).
            f.add("BLOCKED", "unexpected_restart",
                  f"{SERVICE} restarted: ActiveEnter {base['ActiveEnterTimestamp']} → {props.get('ActiveEnterTimestamp')}, pid {base['ExecMainPID']} → {pid}")
            restart = f.items[-1]
        try:
            if int(props.get("NRestarts", "0")) > int(base["NRestarts"]):
                f.add("BLOCKED", "service_crash_restart", f"NRestarts {base['NRestarts']} → {props.get('NRestarts')}")
        except ValueError:
            pass

    rc, out = run(["systemctl", "list-units", "--all", "--no-pager", "--no-legend", "--plain", "afs-candidate-*"])
    active_cands = [l.split()[0] for l in out.splitlines() if l.strip() and " active " in " " + l + " " and "running" in l]
    rt["candidate_units_active"] = active_cands
    if active_cands:
        f.add("BLOCKED", "deploy_candidate_running", f"deploy-candidate unit(s) running: {active_cands}")
    rc, out = run(["pgrep", "-af", "uvicorn webhook.app"])
    procs = [l for l in out.splitlines() if _is_webhook_process_line(l)]
    rt["webhook_processes"] = procs
    if len(procs) != 1:
        f.add("BLOCKED", "webhook_process_count", f"expected exactly 1 webhook process, found {len(procs)}", procs=procs)

    # feed freshness — reuse the box's own feed-gap alarm (no invented rules)
    try:
        st = os.stat(FEED_STATE)
        feed = json.loads(read_prod_text(FEED_STATE))
        age_min = (time.time() - st.st_mtime) / 60
        rt["feed"] = {"age_min": round(age_min, 1), "instruments": feed.get("instruments")}
        for inst, d in (feed.get("instruments") or {}).items():
            if d.get("status") != "healthy":
                f.add("BLOCKED", f"feed_{inst}_{d.get('status')}", f"feed-gap alarm: {inst} status={d.get('status')} stale_since={d.get('stale_since')}")
        if age_min > 30:
            f.add("BLOCKED", "feed_alarm_stale", f"feed_gap_alarm_state.json not updated for {age_min:.0f} min")
    except Exception as exc:  # noqa: BLE001
        f.add("BLOCKED", "feed_state_unreadable", f"{FEED_STATE}: {exc}")

    # alerts + service log since last tick
    since = state.get("last_journalctl_cursor_utc") or iso(now_utc() - timedelta(minutes=10))
    since_local = datetime.fromisoformat(since.replace("Z", "+00:00")).astimezone().strftime("%Y-%m-%d %H:%M:%S")
    rc, out = run(["journalctl", "-u", SERVICE, "--since", since_local, "--no-pager", "-q", "-o", "short-iso"], timeout=90)
    posts: dict[str, int] = {}
    non200: list[str] = []
    tracebacks: list[str] = []
    errors: list[str] = []
    for l in out.splitlines():
        if "POST /webhook/alert" in l:
            m = re.search(r'HTTP/1\.[01]" (\d{3})', l)
            code = m.group(1) if m else "?"
            posts[code] = posts.get(code, 0) + 1
            if code != "200":
                non200.append(l[-160:])
        low = l.lower()
        if "traceback" in low:
            tracebacks.append(l[-200:])
        elif "error" in low or "critical" in low:
            errors.append(l[-200:])
    rt["alerts_since_last_tick"] = posts
    rt["log_lines"] = len(out.splitlines())
    if non200:
        f.add("BLOCKED", "alert_non200", f"{len(non200)} non-200 alert responses since {since}", samples=non200[:5], counts=posts)
    if tracebacks:
        f.add("BLOCKED", "service_traceback", f"{len(tracebacks)} traceback line(s) in {SERVICE} log", samples=tracebacks[:5])
    evid_err = [e for e in errors if re.search(r"journal|campaign|evidence|oserror|write|disk|permission", e, re.I)]
    if evid_err:
        f.add("BLOCKED", "evidence_write_error", f"{len(evid_err)} evidence/write error line(s)", samples=evid_err[:5])
    elif errors:
        f.add("WARN", "service_error_lines", f"{len(errors)} error-level line(s) (non-evidence)", samples=errors[:3])
    state["last_journalctl_cursor_utc"] = iso(now_utc())

    # journal progress: if alerts arrived, the newest journal must have advanced
    journals = sorted(LOG_DIR.glob("journal_*.jsonl"))
    newest = journals[-1] if journals else None
    rt["journal_newest"] = str(newest) if newest else None
    if newest is None:
        f.add("BLOCKED", "journal_missing", f"no journal_*.jsonl under {LOG_DIR}")
    else:
        st = os.stat(newest)
        rt["journal_newest_mtime"] = iso(datetime.fromtimestamp(st.st_mtime, timezone.utc))
        rt["journal_newest_size"] = st.st_size
        # Only flag when alerts keep arriving AND the newest journal has not grown
        # for longer than one full 15-minute bar interval.  5-minute alerts
        # (:05/:10/...) only write tf5m/bar files; the journal advances on
        # 15-minute bars, so a single unchanged tick is normal, not a stall.
        # The journal advances on the 15-MINUTE decision path only. A 5-minute
        # alert with nothing armed returns FIVE_MIN_CONTEXT in webhook/runner.py
        # BEFORE any decision is journaled -- it is recorded on the 5m lane
        # (logs/tf5m/) instead. Counting every inbound alert therefore made this
        # fire on any quiet stretch: 5m alerts keep arriving every 5 minutes
        # while the 15m stream is legitimately idle (between bars, after the
        # session's last 15m bar, or overnight), so alerts_since_advance climbs
        # and stalled_min passes JOURNAL_STALL_MIN with nothing actually wrong.
        # Observed 2026-09-03 22:06Z: "2 alerts received but journal unchanged
        # for 60 min" while logs/tf5m/ was advancing normally and the feed-gap
        # alarm independently reported both instruments healthy.
        #
        # A real stall is causal: a 15-MINUTE DECISION-PATH BAR ARRIVED and
        # no main-journal row followed. Only MNQ/MES enter the decision path.
        # M2K/MGC/MCL/MBT are collection-only and deliberately bypass
        # process_alert / the main journal, so their top-level 15m bar files
        # must not be allowed to manufacture a journal-stall finding.
        try:
            _decision_bar_paths = [
                *LOG_DIR.glob("bars_MNQ*.jsonl"),
                *LOG_DIR.glob("bars_MES*.jsonl"),
            ]
            _bar_mtimes = [os.stat(b).st_mtime for b in _decision_bar_paths]
            newest_bar_mtime = max(_bar_mtimes) if _bar_mtimes else 0.0
        except OSError:
            newest_bar_mtime = 0.0
        rt["newest_15m_bar_mtime"] = (
            iso(datetime.fromtimestamp(newest_bar_mtime, timezone.utc)) if newest_bar_mtime else None
        )
        jp = state.get("journal_progress") or {}
        alerts_now = sum(posts.values())
        if jp.get("path") != str(newest) or st.st_size != jp.get("size") or st.st_mtime != jp.get("mtime"):
            jp = {"path": str(newest), "size": st.st_size, "mtime": st.st_mtime,
                  "last_advanced_utc": iso(now_utc()), "alerts_since_advance": 0,
                  "bar_mtime_at_advance": newest_bar_mtime}
        jp.setdefault("bar_mtime_at_advance", newest_bar_mtime)
        jp["alerts_since_advance"] = int(jp.get("alerts_since_advance") or 0) + alerts_now
        state["journal_progress"] = jp
        last_adv = _ts(jp.get("last_advanced_utc"))
        stalled_min = (now_utc() - last_adv).total_seconds() / 60 if last_adv else 0.0
        rt["journal_stalled_min"] = round(stalled_min, 1)
        # Strictly greater: the 15m bar write and the journal row for that same
        # bar happen within seconds of each other, so equality means "no new 15m
        # bar since the journal last advanced".
        bar_since_advance = newest_bar_mtime > float(jp.get("bar_mtime_at_advance") or 0.0)
        rt["fifteen_min_bar_since_journal_advance"] = bar_since_advance
        if jp["alerts_since_advance"] > 0 and stalled_min > JOURNAL_STALL_MIN and bar_since_advance:
            f.add("BLOCKED", "journal_not_advancing",
                  f"a 15m bar arrived but {newest.name} did not grow for {stalled_min:.0f} min "
                  f"({jp['alerts_since_advance']} alerts since last advance, size {st.st_size})")
        # validate the last COMPLETE line (rows can exceed 4 KB; read a wide tail
        # and only judge a line that is fully contained in the window)
        tail = read_prod_bytes_tail(newest, 1_048_576)
        if tail and not tail.endswith(b"\n"):
            rt["journal_tail_partial_write"] = True   # a row mid-write is normal; judged next tick
        lines = [l for l in tail.split(b"\n") if l.strip()]
        last_line = lines[-1] if tail.endswith(b"\n") and lines else (lines[-2] if len(lines) >= 2 else b"")
        if last_line and (st.st_size > len(tail) and last_line is lines[0]):
            last_line = b""  # window did not contain a complete line boundary — skip judgement
        if last_line:
            try:
                json.loads(last_line)
            except Exception:
                f.add("BLOCKED", "journal_tail_corrupt", f"last complete line of {newest.name} is not valid JSON", head=last_line[:120].decode("utf-8", "replace"))

    # Tradovate reliability + broker account (GET, read-only)
    tr, err = http_get_json("/status/tradovate-reliability")
    rt["tradovate"] = tr or {"error": err}
    if tr is None:
        f.add("BLOCKED", "status_api_unreachable", f"GET /status/tradovate-reliability failed: {err}")
    else:
        if tr.get("state") != "HEALTHY" or not tr.get("ready"):
            lvl = "BLOCKED" if tr.get("market_active") else "WARN"
            f.add(lvl, f"tradovate_{tr.get('state')}", f"Tradovate reliability state={tr.get('state')} ready={tr.get('ready')} reason={tr.get('failure_reason')}")
    ba, err = http_get_json("/status/broker-account")
    rt["broker"] = {k: (ba or {}).get(k) for k in ("ok", "env", "position", "open_pnl", "realized_pnl", "message")} if ba else {"error": err}
    if ba is None:
        f.add("WARN", "broker_status_unreachable", f"GET /status/broker-account failed: {err}")
    else:
        if ba.get("env") != "demo":
            f.add("BLOCKED", "broker_env_not_demo", f"broker env={ba.get('env')}")
        if ba.get("position"):
            f.add("BLOCKED", "unexpected_broker_position", f"broker reports an open position: {ba.get('position')}")
        if ba.get("ok") is False:
            f.add("WARN", "broker_account_not_ok", f"broker-account ok=false: {ba.get('message')}")
    td, err = http_get_json("/status/today")
    if td:
        rt["today"] = {k: td.get(k) for k in ("date", "trade_count", "wins", "losses", "has_open_position", "open_position", "realized_pnl_dollars", "live_trading_enabled", "paper_mode")}
        if td.get("live_trading_enabled"):
            f.add("BLOCKED", "live_trading_enabled", "status/today reports live_trading_enabled=true")

    settle_baseline(state, f, rt, base, restart, props, pid, cwd, live_pins)


def _reading_from_dict(row: dict) -> MemoryReading:
    fields = {
        "observed_utc", "pid", "service_rss_bytes", "mem_total_bytes",
        "mem_available_bytes", "cgroup_limit_bytes", "cgroup_current_bytes",
    }
    return MemoryReading(**{key: row.get(key) for key in fields})


def check_memory(state: dict, f: Findings, tick: dict) -> None:
    """Sample memory and publish the existing watcher's operational block."""
    pid_text = str(tick.get("runtime", {}).get("service", {}).get("ExecMainPID") or "0")
    if not pid_text.isdigit() or int(pid_text) <= 0:
        f.add("WARN", "memory_sample_unavailable", "futures-bot has no readable main pid")
        return
    pid = int(pid_text)
    reading = sample_process_memory(pid)
    history_rows = [
        row for row in (state.get("memory_history") or [])
        if isinstance(row, dict) and row.get("pid") == pid
    ][-(MEMORY_HISTORY_SAMPLES - 1):]
    history = []
    for row in history_rows:
        try:
            history.append(_reading_from_dict(row))
        except (TypeError, ValueError):
            continue
    observed_oom_headroom = (state.get("oom_observation") or {}).get("headroom_bytes")
    status = evaluate_memory(
        reading,
        recent_readings=history,
        observed_oom_headroom_bytes=observed_oom_headroom,
    )
    status_dict = status.to_dict()
    prior = state.get("memory_guard") or {}
    if prior.get("reading", {}).get("pid") not in (None, pid):
        state["memory_post_restart"] = {
            "pid": pid,
            "started_utc": reading.observed_utc,
            "initial_rss_bytes": reading.service_rss_bytes,
        }
        history = []
        status = evaluate_memory(
            reading,
            observed_oom_headroom_bytes=observed_oom_headroom,
        )
        status_dict = status.to_dict()
    state["memory_history"] = [
        *history_rows,
        {
            "observed_utc": reading.observed_utc,
            "pid": pid,
            "service_rss_bytes": reading.service_rss_bytes,
            "mem_total_bytes": reading.mem_total_bytes,
            "mem_available_bytes": reading.mem_available_bytes,
            "cgroup_limit_bytes": reading.cgroup_limit_bytes,
            "cgroup_current_bytes": reading.cgroup_current_bytes,
        },
    ][-MEMORY_HISTORY_SAMPLES:]
    state["memory_guard"] = status_dict
    tick["memory_guard"] = status_dict

    summary = (
        f"{status.level}: futures-bot RSS={reading.service_rss_bytes // 1048576}MiB "
        f"available={reading.effective_headroom_bytes // 1048576}MiB "
        f"capacity={reading.effective_capacity_bytes // 1048576}MiB; {status.reason}"
    )
    if status.level == "CRITICAL":
        f.add("BLOCKED", "memory_critical", summary, memory=status_dict)
    elif status.level == "WARNING":
        f.add("WARN", "memory_warning", summary, memory=status_dict)

    post = state.get("memory_post_restart") or {}
    if (
        post.get("pid") == pid
        and status.rss_growth_bytes_per_minute is not None
        and status.rss_growth_bytes_per_minute > 0
        and status.level in {"WARNING", "CRITICAL"}
    ):
        f.add(
            "WARN", "memory_growing_after_restart",
            f"futures-bot memory is still growing after pid changed to {pid}: {summary}",
            memory=status_dict,
        )


def _meminfo_kb() -> dict:
    out: dict = {}
    for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
        key, _, raw = line.partition(":")
        parts = raw.split()
        if parts and parts[0].isdigit():
            out[key] = int(parts[0])
    return out


def check_memory_fixed(state: dict, f: Findings, tick: dict) -> None:
    """Fixed-threshold swap / OOM / sustained-growth checks (additive to the
    dynamic guard in check_memory).  Read-only and alert-only."""
    mem: dict = {}
    tick["memory_fixed"] = mem
    th = MEM_FIXED_THRESHOLDS
    pid = str((tick.get("runtime", {}).get("service") or {}).get("ExecMainPID") or "0")
    rss_mb = None
    proc_swap_mb = 0.0
    if pid.isdigit() and int(pid) > 0:
        try:
            for line in Path(f"/proc/{pid}/status").read_text(encoding="utf-8").splitlines():
                if line.startswith("VmRSS:"):
                    rss_mb = round(int(line.split()[1]) / 1024, 1)
                elif line.startswith("VmSwap:"):
                    proc_swap_mb = round(int(line.split()[1]) / 1024, 1)
        except Exception as exc:  # noqa: BLE001
            f.add("WARN", "memory_fixed_rss_unreadable", f"/proc/{pid}/status: {exc}")
    # resident + swapped = the process's real footprint (swap-out masks RSS growth)
    footprint_mb = round(rss_mb + proc_swap_mb, 1) if rss_mb is not None else None
    # kernel paging since the last tick (pages -> MB) from /proc/vmstat
    vm: dict = {}
    for line in Path("/proc/vmstat").read_text(encoding="utf-8").splitlines():
        k, _, v = line.partition(" ")
        if k in ("pswpin", "pswpout"):
            vm[k] = int(v)
    prev_vm = state.get("memory_vmstat_prev") or {}
    swapin_mb = round((vm.get("pswpin", 0) - prev_vm.get("pswpin", vm.get("pswpin", 0))) * 4 / 1024, 1)
    swapout_mb = round((vm.get("pswpout", 0) - prev_vm.get("pswpout", vm.get("pswpout", 0))) * 4 / 1024, 1)
    state["memory_vmstat_prev"] = vm
    mi = _meminfo_kb()
    avail_mb = round(mi.get("MemAvailable", 0) / 1024)
    swap_total_mb = round(mi.get("SwapTotal", 0) / 1024)
    swap_used_mb = round((mi.get("SwapTotal", 0) - mi.get("SwapFree", 0)) / 1024)
    rc, out = run(["journalctl", "-k", "--no-pager", "-q"], timeout=60)
    oom_total = sum(1 for l in out.splitlines() if "Out of memory" in l)
    nrestarts = (tick.get("runtime", {}).get("service") or {}).get("NRestarts")
    sample = {"utc": iso(now_utc()), "pid": pid, "rss_mb": rss_mb, "proc_swap_mb": proc_swap_mb, "footprint_mb": footprint_mb,
              "avail_mb": avail_mb, "swap_used_mb": swap_used_mb, "swap_total_mb": swap_total_mb,
              "swapin_mb_since_last_tick": swapin_mb, "swapout_mb_since_last_tick": swapout_mb,
              "oom_total": oom_total, "nrestarts": nrestarts}
    mem.update(sample)
    state_append(MEM_LOG, json.dumps(sample, sort_keys=True) + "\n")

    # swap must stay active and reboot-persistent
    swaps = Path("/proc/swaps").read_text(encoding="utf-8")
    fstab_ok = any(l.split()[:1] == [SWAP_PATH] for l in Path("/etc/fstab").read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#"))
    mem["swap_active"] = SWAP_PATH in swaps
    mem["swap_in_fstab"] = fstab_ok
    if SWAP_PATH not in swaps:
        f.add("BLOCKED", "swap_inactive", f"{SWAP_PATH} is not an active swap device")
    if not fstab_ok:
        f.add("BLOCKED", "swap_not_persistent", f"{SWAP_PATH} missing from /etc/fstab")

    # kernel OOM events, new since this check was armed
    base_oom = state.get("memory_oom_baseline")
    if base_oom is None:
        state["memory_oom_baseline"] = oom_total
    elif oom_total > base_oom:
        f.add("BLOCKED", "oom_kill_new", f"kernel OOM events rose {base_oom} -> {oom_total} (see `journalctl -k`)")

    # fixed absolute thresholds (off by default: the dynamic guard covers these)
    if MEM_FIXED_ABSOLUTE_CHECKS:
        if footprint_mb is not None:
            if footprint_mb >= th["rss_crit_mb"]:
                f.add("BLOCKED", "memory_rss_critical", f"futures-bot footprint {footprint_mb} MB (rss {rss_mb} + swap {proc_swap_mb}) >= critical {th['rss_crit_mb']} MB")
            elif footprint_mb >= th["rss_warn_mb"]:
                f.add("WARN", "memory_rss_warning", f"futures-bot footprint {footprint_mb} MB (rss {rss_mb} + swap {proc_swap_mb}) >= warning {th['rss_warn_mb']} MB")
        if avail_mb <= th["avail_crit_mb"]:
            f.add("BLOCKED", "memory_avail_critical", f"MemAvailable {avail_mb} MB <= critical {th['avail_crit_mb']} MB")
        elif avail_mb <= th["avail_warn_mb"]:
            f.add("WARN", "memory_avail_warning", f"MemAvailable {avail_mb} MB <= warning {th['avail_warn_mb']} MB")
    # swap PRESSURE = the kernel actively paging, not idle pages parked in swap.
    # Magnitude alone is not reliable: a reclaim burst right after a process
    # restart can page hundreds of MB while MemAvailable is healthy and rising
    # (proven false positive 2026-09-03T01:36Z: 354 MB out while MemAvailable
    # rose 557->840 MB). CRITICAL now additionally requires corroboration —
    # low headroom, or paging sustained across consecutive ticks — magnitude
    # alone only ever reaches WARNING.
    paging_mb = swapin_mb + swapout_mb
    streak = state.get("memory_fixed_paging_streak", 0)
    streak = streak + 1 if paging_mb >= th["swap_activity_warn_mb_tick"] else 0
    state["memory_fixed_paging_streak"] = streak
    mem["swap_activity_streak"] = streak
    low_headroom = avail_mb <= th["avail_warn_mb"]
    sustained = streak >= th["swap_activity_sustained_ticks"]
    if paging_mb >= th["swap_activity_crit_mb_tick"] and (low_headroom or sustained):
        corroboration = " and ".join(
            s for s, ok in (
                (f"MemAvailable {avail_mb} MB <= warning {th['avail_warn_mb']} MB", low_headroom),
                (f"paging sustained {streak} consecutive ticks >= warning {th['swap_activity_warn_mb_tick']} MB", sustained),
            ) if ok
        )
        f.add("BLOCKED", "swap_pressure_critical",
              f"swap activity {swapin_mb} MB in / {swapout_mb} MB out since last tick >= critical "
              f"{th['swap_activity_crit_mb_tick']} MB, corroborated by {corroboration}")
    elif paging_mb >= th["swap_activity_warn_mb_tick"]:
        f.add("WARN", "swap_pressure_warning", f"swap activity {swapin_mb} MB in / {swapout_mb} MB out since last tick >= warning {th['swap_activity_warn_mb_tick']} MB")
    if swap_used_mb >= th["swap_crit_mb"]:
        f.add("BLOCKED", "swap_used_critical", f"swap used {swap_used_mb} MB of {swap_total_mb} >= critical {th['swap_crit_mb']} MB")
    elif swap_used_mb >= th["swap_warn_mb"]:
        f.add("WARN", "swap_used_warning", f"swap used {swap_used_mb} MB of {swap_total_mb} >= warning {th['swap_warn_mb']} MB")

    # sustained growth of the footprint over the rolling window (same pid only)
    hist = state.setdefault("memory_fixed_samples", [])
    hist.append({"utc": sample["utc"], "pid": pid, "footprint_mb": footprint_mb})
    del hist[:-MEM_FIXED_WINDOW_TICKS]
    same = [h for h in hist if h.get("pid") == pid and h.get("footprint_mb") is not None]
    mem["window_samples"] = len(same)
    if len(same) >= MEM_FIXED_MIN_SAMPLES and footprint_mb is not None:
        first = same[0]["footprint_mb"]
        delta = round(footprint_mb - first, 1)
        rising = sum(1 for a, b in zip(same, same[1:]) if b["footprint_mb"] >= a["footprint_mb"])
        mem["window_first_footprint_mb"] = first
        mem["window_delta_mb"] = delta
        mem["window_nondecreasing_steps"] = rising
        span = f"{len(same)} samples (~{len(same) * TICK_SECONDS // 60} min)"
        if delta >= th["growth_crit_mb_2h"]:
            f.add("BLOCKED", "memory_rss_growth_critical", f"futures-bot footprint +{delta} MB over {span} ({first} -> {footprint_mb} MB) >= critical {th['growth_crit_mb_2h']} MB")
        elif delta >= th["growth_warn_mb_2h"] and rising >= 0.7 * (len(same) - 1):
            f.add("WARN", "memory_rss_growth", f"futures-bot footprint +{delta} MB over {span} ({first} -> {footprint_mb} MB), mostly rising, >= warning {th['growth_warn_mb_2h']} MB")


def _paper_continuity_manifest(tick: dict) -> dict:
    active = []
    errors = []
    for pattern in ("mnq_*_state.json", "mes_*_state.json"):
        for p in sorted(LOG_DIR.glob(pattern)):
            try:
                raw = read_prod_bytes(p)
                data = json.loads(raw)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{p.name}: {type(exc).__name__}: {exc}")
                continue
            for field in ("position", "pending_order"):
                value = data.get(field) if isinstance(data, dict) else None
                if not value:
                    continue
                entry = value.get("actual_entry", value.get("entry")) if isinstance(value, dict) else None
                required = {
                    "direction": value.get("direction") if isinstance(value, dict) else None,
                    "entry": entry,
                    "stop": value.get("stop") if isinstance(value, dict) else None,
                    "target": value.get("target") if isinstance(value, dict) else None,
                    "paper_order_id": value.get("paper_order_id") if isinstance(value, dict) else None,
                }
                restorable = all(v is not None for v in required.values()) and str(required["paper_order_id"]).startswith("PAPER-")
                active.append({
                    "path": str(p), "field": field, "sha256": sha256_bytes(raw),
                    "restorable": restorable, "identity": required,
                })

    rt = tick.get("runtime") or {}
    today = rt.get("today") or {}
    broker = rt.get("broker") or {}
    live_disabled = today.get("live_trading_enabled") is False
    broker_flat = broker.get("position") in (None, {}) and not broker.get("error")
    working_orders_zero = False
    preflight_path = LOG_DIR / "live_preflight_state.json"
    try:
        preflight = json.loads(read_prod_text(preflight_path))
        checked = _ts(preflight.get("last_preflight_at"))
        checks = {row.get("name"): row for row in preflight.get("checks", []) if isinstance(row, dict)}
        working_orders_zero = bool(
            checked
            and (now_utc() - checked).total_seconds() <= 600
            and checks.get("no_working_orders", {}).get("ok") is True
            and checks.get("no_open_positions", {}).get("ok") is True
        )
    except Exception:  # noqa: BLE001
        pass
    continuity_proven = not errors and all(row["restorable"] for row in active)
    automated_recovery_allowed = bool(
        live_disabled and broker_flat and working_orders_zero and continuity_proven
    )
    return {
        "captured_utc": iso(now_utc()),
        "active_paper_states": active,
        "errors": errors,
        "status_today_has_open_position": today.get("has_open_position"),
        "status_today_open_position": today.get("open_position"),
        "live_trading_disabled": live_disabled,
        "broker_flat": broker_flat,
        "working_orders_zero_fresh": working_orders_zero,
        "continuity_proven": continuity_proven,
        "automated_recovery_allowed": automated_recovery_allowed,
    }


def read_prod_bytes_tail(p: Path, n: int) -> bytes:
    fd = os.open(p, os.O_RDONLY)
    try:
        size = os.fstat(fd).st_size
        os.lseek(fd, max(0, size - n), os.SEEK_SET)
        return os.read(fd, n)
    finally:
        os.close(fd)


# ── hypothetical-ledger lanes (read-only) ────────────────────────────────────
def _newest_mtime(paths) -> float:
    best = 0.0
    for p in paths:
        try:
            best = max(best, os.stat(p).st_mtime)
        except OSError:
            continue
    return best


def _newest_bar_ts(paths) -> datetime | None:
    """Timestamp of the last complete bar ROW in the newest bar file (content, not mtime).

    Used to establish the causal condition "a bar has actually arrived after the
    pinned epoch" before a missing lane file may be called a defect. A campaign
    that is fresh (pinned, but no eligible bar processed yet) has nothing to
    show and must stay quiet.
    """
    files = sorted(paths)
    if not files:
        return None
    for path in reversed(files):
        try:
            tail = read_prod_bytes_tail(Path(path), 65536)
        except Exception:  # noqa: BLE001
            continue
        for line in reversed(tail.split(b"\n")):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except Exception:  # noqa: BLE001
                continue
            if isinstance(row, dict):
                stamp = _ts(row.get("ts") or row.get("timestamp"))
                if stamp is not None:
                    return stamp
        break
    return None


def _lane_json(path: Path) -> dict | None:
    try:
        data = json.loads(read_prod_text(path))
    except Exception:  # noqa: BLE001
        return None
    return data if isinstance(data, dict) else None


def _position_brief(position) -> dict | None:
    if not isinstance(position, dict):
        return None
    keys = ("strategy", "direction", "entry", "stop", "target", "entry_time", "paper_order_id")
    return {k: position.get(k) for k in keys if position.get(k) is not None}


def _mes_lane_open_position(lane_dir: Path) -> tuple[dict | None, float]:
    """(open strat_122 position, newest lane journal mtime) from the lane's own journals.

    TRADE + APPROVED opens a paper_order_id; an OUTCOME with the same id closes it.
    Only the last 8 day files are read (the runner's carry lookup is 7 calendar days).
    """
    paths = sorted(lane_dir.glob("journal_*.jsonl"))[-8:]
    open_trades: dict[str, dict] = {}
    for p in paths:
        try:
            text = read_prod_text(p)
        except Exception:  # noqa: BLE001
            continue
        for line in text.splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except Exception:  # noqa: BLE001
                continue
            if not isinstance(row, dict):
                continue
            if row.get("decision") == "TRADE" and (row.get("risk_check") or {}).get("result") == "APPROVED":
                oid = str(row.get("paper_order_id") or "")
                setup = row.get("setup") if isinstance(row.get("setup"), dict) else {}
                if oid:
                    open_trades[oid] = {**setup, "paper_order_id": oid, "entry_time": row.get("ts")}
            elif row.get("type") == "OUTCOME":
                open_trades.pop(str((row.get("outcome") or {}).get("paper_order_id") or ""), None)
    position = _position_brief(open_trades[list(open_trades)[-1]]) if open_trades else None
    return position, _newest_mtime(paths)


def check_lanes(state: dict, f: Findings, tick: dict) -> None:
    """Heartbeat, epoch and exposure checks for the isolated paper lanes.

    Adds nothing the collectors do not already persist: it reads their own state
    files and journals. A lane that is merely quiet (no candidate) is NOT a
    finding; a lane whose heartbeat stopped while bars keep arriving, a 5-minute
    stream that stopped while the 15-minute stream continues, a state file from a
    different accounting epoch than the deploy pinned, or an OPEN paper position
    with no fresh bars to resolve against, IS.
    """
    lanes: dict = {}
    tick["lanes"] = lanes
    try:
        pins = read_env_pins(ENV_FILE)
    except OSError as exc:
        pins = {}
        f.add("WARN", "lane_pins_unreadable", f"cannot read lane pins from {ENV_FILE}: {exc}")
    wide_active = pins.get(WIDE_STOP_MODE_PIN, "").strip().lower() == "paper_sim"
    mes_active = pins.get(MES_122_MODE_PIN, "").strip().lower() == "paper_sim"
    lanes["wide_stop_paper_sim"] = wide_active
    lanes["mes_122_paper_sim"] = mes_active
    lanes["wide_stop_epoch_pin"] = pins.get(WIDE_STOP_EPOCH_PIN)
    lanes["mes_122_epoch_pin"] = pins.get(MES_122_EPOCH_PIN)

    # 5-minute stream vs 15-minute stream (same causal rule as journal_not_advancing:
    # a NEW 15m bar landed but no 5m bar has for longer than one 15m interval).
    newest_15m = _newest_mtime(LOG_DIR.glob("bars_MNQ_*.jsonl"))
    newest_5m = _newest_mtime(FIVE_MIN_DIR.glob("bars_MNQ_*.jsonl")) if FIVE_MIN_DIR.is_dir() else 0.0
    lanes["newest_15m_mnq_bar_mtime"] = iso(datetime.fromtimestamp(newest_15m, timezone.utc)) if newest_15m else None
    lanes["newest_5m_mnq_bar_mtime"] = iso(datetime.fromtimestamp(newest_5m, timezone.utc)) if newest_5m else None
    newest_mes_15m_mtime = _newest_mtime(LOG_DIR.glob("bars_MES_*.jsonl"))
    lanes["newest_mes_15m_bar_mtime"] = iso(datetime.fromtimestamp(newest_mes_15m_mtime, timezone.utc)) if newest_mes_15m_mtime else None
    five_min_stalled = False
    if wide_active:
        if not newest_5m:
            five_min_stalled = True
            f.add("BLOCKED", "five_min_feed_missing",
                  f"{WIDE_STOP_MODE_PIN}=paper_sim but no 5-minute MNQ bar file exists under {FIVE_MIN_DIR}")
        elif newest_15m and (newest_15m - newest_5m) > LANE_STALL_MIN * 60:
            five_min_stalled = True
            f.add("BLOCKED", "five_min_feed_stalled",
                  f"a 15m MNQ bar arrived {int((newest_15m - newest_5m) // 60)} min after the newest 5m bar "
                  f"(newest 5m {lanes['newest_5m_mnq_bar_mtime']}) — every MNQ paper lane resolves on 5m bars")
    lanes["five_min_feed_stalled"] = five_min_stalled

    inventory: dict = {}
    lanes["inventory"] = inventory
    open_positions: list[str] = []

    # Daily 2-2 swing lane: state rewritten on every processed MNQ 5m bar.
    d22_dir = LEDGER_DIR / "daily_22_5k"
    d22_state_path = d22_dir / "swing_state.json"
    d22 = {"exists": d22_dir.is_dir(), "state_mtime": None, "open_position": None, "halted": None, "epoch": None}
    inventory["daily_22_5k"] = d22
    if d22_state_path.exists():
        try:
            d22["state_mtime"] = iso(datetime.fromtimestamp(os.stat(d22_state_path).st_mtime, timezone.utc))
        except OSError:
            pass
        data = _lane_json(d22_state_path)
        if data is None:
            f.add("BLOCKED", "daily_22_state_unreadable", f"{d22_state_path} is not valid JSON — the Daily lane fails closed on every bar")
        else:
            d22["open_position"] = _position_brief(data.get("position"))
            d22["halted"] = data.get("halted")
            d22["epoch"] = data.get("epoch")
            if d22["open_position"]:
                open_positions.append("daily_22_5k")
            if data.get("halted"):
                f.add("BLOCKED", "daily_22_halted", "Daily 2-2 hypothetical ledger reports hard drawdown halt")
            pin = lanes["wide_stop_epoch_pin"]
            if wide_active and pin and data.get("epoch"):
                pin_dt, st_dt = _ts(pin), _ts(data.get("epoch"))
                if pin_dt and st_dt and pin_dt != st_dt:
                    f.add("BLOCKED", "daily_22_state_epoch_mismatch",
                          f"swing_state.json epoch {data.get('epoch')} != pinned {WIDE_STOP_EPOCH_PIN}={pin} — "
                          "the Daily state-integrity guard refuses this state on every bar")
            if wide_active and newest_5m:
                try:
                    st_m = os.stat(d22_state_path).st_mtime
                except OSError:
                    st_m = 0.0
                if newest_5m - st_m > LANE_STALL_MIN * 60:
                    f.add("BLOCKED", "daily_22_collector_stalled",
                          f"5m MNQ bars keep arriving but swing_state.json has not been touched for "
                          f"{int((newest_5m - st_m) // 60)} min — the Daily 2-2 collector is not running on them")

    elif wide_active:
        # No swing_state.json. The Daily collector writes it on the FIRST MNQ 5m
        # bar it processes after the epoch (flat or not), so its absence is a
        # defect only once such a bar demonstrably exists. Before that the
        # campaign is merely fresh.
        pin_dt = _ts(lanes["wide_stop_epoch_pin"])
        bar_ts = _newest_bar_ts(FIVE_MIN_DIR.glob("bars_MNQ_*.jsonl")) if FIVE_MIN_DIR.is_dir() else None
        d22["newest_5m_bar_ts"] = iso(bar_ts)
        if pin_dt is not None and bar_ts is not None and bar_ts > pin_dt:
            f.add("BLOCKED", "daily_22_state_missing_while_feed_active",
                  f"{WIDE_STOP_MODE_PIN}=paper_sim, an MNQ 5m bar at {iso(bar_ts)} arrived after the pinned epoch "
                  f"{lanes['wide_stop_epoch_pin']}, but {d22_state_path} does not exist — the Daily 2-2 collector is not "
                  "processing bars (or its state was removed)")
        else:
            d22["fresh_campaign"] = True

    # Wide-stop ledgers: created lazily, state written only on candidates (no heartbeat).
    for name in ("wide_stop_4k", "wide_stop_6k"):
        ldir = LEDGER_DIR / name
        row = {"exists": ldir.is_dir(), "state_mtime": None, "open_position": None, "filled_count": None, "heartbeat": "none (candidate-driven)"}
        inventory[name] = row
        spath = ldir / "forward_collector_state.json"
        if spath.exists():
            try:
                row["state_mtime"] = iso(datetime.fromtimestamp(os.stat(spath).st_mtime, timezone.utc))
            except OSError:
                pass
            data = _lane_json(spath) or {}
            row["open_position"] = _position_brief(data.get("position"))
            row["filled_count"] = data.get("filled_count")
            if row["open_position"]:
                open_positions.append(name)

    # MES 1-2-2 lane: its own journal grows on every MES 15m bar.
    mes_dir = LEDGER_DIR / "mes_122_1500"
    mes = {"exists": mes_dir.is_dir(), "journal_mtime": None, "open_position": None}
    inventory["mes_122_1500"] = mes
    mes_journals = sorted(mes_dir.glob("journal_*.jsonl")) if mes_dir.is_dir() else []
    mes["journal_files"] = len(mes_journals)
    if mes_journals:
        position, newest_lane = _mes_lane_open_position(mes_dir)
        mes["open_position"] = position
        mes["journal_mtime"] = iso(datetime.fromtimestamp(newest_lane, timezone.utc)) if newest_lane else None
        if position:
            open_positions.append("mes_122_1500")
        newest_mes_15m = _newest_mtime(LOG_DIR.glob("bars_MES_*.jsonl"))
        if mes_active and newest_mes_15m and newest_lane and (newest_mes_15m - newest_lane) > LANE_STALL_MIN * 60:
            f.add("BLOCKED", "mes_122_lane_stalled",
                  f"a MES 15m bar arrived {int((newest_mes_15m - newest_lane) // 60)} min after the MES 1-2-2 "
                  "lane journal last grew — the lane is not evaluating MES alerts")
    elif mes_active:
        # The lane journals a BAR_CLAIM + decision on the first MES 15m bar after
        # its epoch. Absence is a defect only once such a bar demonstrably exists
        # in the box's own MES bar history; before that the lane is merely fresh.
        pin_dt = _ts(lanes["mes_122_epoch_pin"])
        bar_ts = _newest_bar_ts(LOG_DIR.glob("bars_MES_*.jsonl"))
        mes["newest_mes_15m_bar_ts"] = iso(bar_ts)
        what = f"{mes_dir} does not exist" if not mes_dir.is_dir() else f"{mes_dir} has no journal_*.jsonl"
        if pin_dt is not None and bar_ts is not None and bar_ts > pin_dt:
            f.add("BLOCKED", "mes_122_lane_missing_while_feed_active",
                  f"{MES_122_MODE_PIN}=paper_sim, a MES 15m bar at {iso(bar_ts)} arrived after the pinned epoch "
                  f"{lanes['mes_122_epoch_pin']}, but {what} — the MES 1-2-2 lane is not evaluating MES alerts")
        else:
            mes["fresh_campaign"] = True
            f.add("WARN", "mes_122_lane_dir_missing",
                  f"{MES_122_MODE_PIN}=paper_sim but {what} yet (no eligible post-epoch MES bar seen; fresh campaign)")

    lanes["open_positions"] = open_positions
    for name in open_positions:
        pos = inventory[name]["open_position"] or {}
        emit_event(state, "FIRST_FIRE", f"hypothetical_position_open:{name}:{pos.get('paper_order_id') or pos.get('entry_time')}",
                   {"summary": f"{name} hypothetical position OPEN: {pos.get('direction')} @ {pos.get('entry')} "
                               f"stop {pos.get('stop')} target {pos.get('target')} since {pos.get('entry_time')} (paper, observe only)",
                    "position": pos}, "DISCORD_ROUTE_DAILY_REPORT")
    mnq_exposed = [n for n in open_positions if n != "mes_122_1500"]
    if mnq_exposed and five_min_stalled:
        f.add("BLOCKED", "hypothetical_position_exposed_stale_bars",
              f"paper position OPEN on {mnq_exposed} but the 5-minute MNQ stream has stalled — stop/target "
              "cannot be resolved; do not assume neither was touched")


# ── storage integrity ────────────────────────────────────────────────────────
def check_storage(state: dict, f: Findings, tick: dict) -> None:
    stg: dict = {}
    tick["storage"] = stg
    for label, path in (("root", "/root"), ("tmp", str(STATE_DIR))):
        try:
            u = shutil.disk_usage(path)
            pct = round(100 * (u.total - u.free) / u.total, 1)
            stg[f"disk_{label}_used_pct"] = pct
            if pct >= 90:
                f.add("BLOCKED", f"disk_{label}_critical", f"{path} {pct}% used")
            elif pct >= 80:
                f.add("WARN", f"disk_{label}_high", f"{path} {pct}% used")
        except Exception as exc:  # noqa: BLE001
            f.add("WARN", f"disk_{label}_unreadable", str(exc))

    # production filesystem must still be mounted rw for the SERVICE (we observe
    # init's mount table — our own namespace is deliberately read-only)
    try:
        rw_ok = None
        for line in Path("/proc/1/mounts").read_text().splitlines():
            parts = line.split()
            if len(parts) >= 4 and parts[1] == "/":
                rw_ok = parts[3].split(",")[0] == "rw"
        stg["root_fs_rw_for_service"] = rw_ok
        if rw_ok is False:
            f.add("BLOCKED", "root_fs_readonly", "/ is mounted read-only in the service's namespace — evidence writes will fail")
    except Exception as exc:  # noqa: BLE001
        f.add("WARN", "mounts_unreadable", str(exc))

    for p in (LOG_DIR, CAMPAIGN_JSONL, CAMPAIGN_STATE):
        if not p.exists():
            f.add("BLOCKED", f"path_missing_{p.name}", f"evidence path disappeared: {p}")
        else:
            st = os.stat(p)
            if not (st.st_mode & 0o200):
                f.add("BLOCKED", f"path_not_writable_{p.name}", f"{p} owner write bit missing (mode {oct(st.st_mode & 0o777)})")

    # append-only / immutability tracking
    tracked = [CAMPAIGN_JSONL] + sorted(LOG_DIR.glob("journal_*.jsonl"))[-8:]
    changed = []
    for p in tracked:
        if not p.exists():
            continue
        st = os.stat(p)
        data = read_prod_bytes(p)
        rec = {"size": st.st_size, "mtime": st.st_mtime, "sha256": sha256_bytes(data), "checked_utc": iso(now_utc())}
        prev = state["files"].get(str(p))
        if prev:
            if st.st_size < prev["size"]:
                f.add("BLOCKED", f"file_shrank_{p.name}", f"{p.name} shrank {prev['size']} → {st.st_size} bytes")
                changed.append(p)
            elif sha256_bytes(data[:prev["size"]]) != prev["sha256"]:
                f.add("BLOCKED", f"history_rewritten_{p.name}", f"{p.name}: first {prev['size']} bytes no longer match the previously observed content (rewrite/truncate)")
                changed.append(p)
            elif p != CAMPAIGN_JSONL and p != tracked[-1] and st.st_size > prev["size"]:
                f.add("WARN", f"prior_day_journal_grew_{p.name}", f"{p.name} (not newest) grew {prev['size']} → {st.st_size}")
        state["files"][str(p)] = rec
    stg["tracked_files"] = len(tracked)
    stg["changed_files"] = [str(p) for p in changed]
    try:
        json.loads(read_prod_text(CAMPAIGN_STATE))
    except Exception as exc:  # noqa: BLE001
        f.add("BLOCKED", "campaign_state_corrupt", f"{CAMPAIGN_STATE.name} not valid JSON: {exc}")


# ── campaign provenance + populations ────────────────────────────────────────
def load_campaign_rows(f: Findings) -> list[dict]:
    rows = []
    try:
        raw = read_prod_text(CAMPAIGN_JSONL)
    except Exception as exc:  # noqa: BLE001
        f.add("BLOCKED", "campaign_unreadable", f"{CAMPAIGN_JSONL}: {exc}")
        return rows
    for i, line in enumerate(raw.splitlines(), 1):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            f.add("BLOCKED", f"campaign_corrupt_line_{i}", f"{CAMPAIGN_JSONL.name} line {i} is not valid JSON")
    return rows


def _ts(v) -> datetime | None:
    if not v:
        return None
    try:
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def run_release_report() -> tuple[dict | None, str | None]:
    py = str(RELEASE_DIR / ".venv" / "bin" / "python")
    env = {"PATH": "/usr/bin:/bin", "PYTHONPATH": str(RELEASE_DIR), "PYTHONDONTWRITEBYTECODE": "1", "HOME": "/tmp"}
    rc, out = run([py, "ops/forward_campaign_report.py", "--log-dir", str(LOG_DIR)], timeout=120, env=env, cwd=str(RELEASE_DIR))
    if rc != 0:
        return None, f"rc={rc}: {out[-400:]}"
    try:
        return json.loads(out), None
    except Exception as exc:  # noqa: BLE001
        return None, f"report not JSON: {exc}: {out[-300:]}"


def check_campaign(state: dict, f: Findings, tick: dict) -> None:
    camp: dict = {}
    tick["campaign"] = camp
    try:
        cfg = json.loads(read_prod_text(CAMPAIGN_CONFIG))
        cfg_pops = [(p["strategy"], p["variant"]) for p in cfg.get("populations", [])]
        camp["configured_populations"] = cfg_pops
        if sorted(cfg_pops) != sorted(EXPECTED_POPULATIONS):
            f.add("BLOCKED", "population_config_changed", f"release campaign config populations {cfg_pops} != expected {EXPECTED_POPULATIONS}")
    except Exception as exc:  # noqa: BLE001
        f.add("BLOCKED", "campaign_config_unreadable", f"{CAMPAIGN_CONFIG}: {exc}")

    rows = load_campaign_rows(f)
    camp["rows"] = len(rows)
    post = [r for r in rows if (_ts(r.get("observed_at")) or datetime.min.replace(tzinfo=timezone.utc)) >= EPOCH]
    camp["rows_after_epoch"] = len(post)
    shas: dict[str, int] = {}
    for r in rows:
        s = str(r.get("generating_git_sha") or "MISSING")
        shas[s] = shas.get(s, 0) + 1
    camp["shas_all"] = shas
    known_shas = known_release_shas(RELEASE_DIR)
    camp["known_release_shas"] = len(known_shas)
    bad_sha = []
    for r in post:
        s = str(r.get("generating_git_sha") or "")
        if not s or not r.get("provenance_status"):
            bad_sha.append((r.get("candidate_id"), r.get("record_type"), "MISSING"))
        elif not sha_is_known_release(s, known_shas):
            bad_sha.append((r.get("candidate_id"), r.get("record_type"), s))
    post_shas = {
        str(r.get("generating_git_sha")).strip().lower()[:12]
        for r in post
        if r.get("generating_git_sha")
    }
    if len(post_shas) > 1:
        f.add("WARN", "post_epoch_spans_releases",
              f"post-epoch evidence spans {len(post_shas)} releases: {sorted(post_shas)}")
    if bad_sha:
        f.add("BLOCKED", "post_epoch_wrong_sha", f"{len(bad_sha)} post-epoch row(s) with wrong/missing generating SHA", rows=bad_sha[:10])
    unexpected = sorted({(str(r.get("strategy")), str(r.get("variant"))) for r in rows} - set(EXPECTED_POPULATIONS))
    if unexpected:
        f.add("BLOCKED", "unexpected_population", f"unexpected campaign population(s): {unexpected}")

    # duplicate / conflicting ids
    for rtype in ("CANDIDATE", "OUTCOME"):
        seen: dict[str, str] = {}
        conflicts, dups = [], []
        for r in rows:
            if r.get("record_type") != rtype:
                continue
            cid = str(r.get("candidate_id"))
            digest = sha256_bytes(json.dumps(r, sort_keys=True).encode())
            if cid in seen:
                (dups if seen[cid] == digest else conflicts).append(cid)
            else:
                seen[cid] = digest
        if conflicts:
            f.add("BLOCKED", f"conflicting_{rtype.lower()}_ids", f"{len(conflicts)} conflicting duplicate {rtype} id(s)", ids=sorted(set(conflicts))[:10])
        if dups:
            f.add("WARN", f"identical_duplicate_{rtype.lower()}_ids", f"{len(dups)} identical duplicate {rtype} row(s)", ids=sorted(set(dups))[:10])

    # per-population tracking (own computation, mirrors ops/forward_campaign_report.py)
    outcomes = {str(r.get("candidate_id")): r for r in rows if r.get("record_type") == "OUTCOME"}
    pops: dict[str, dict] = {}
    for strat, var in EXPECTED_POPULATIONS:
        cands = [r for r in rows if r.get("record_type") == "CANDIDATE" and r.get("strategy") == strat and r.get("variant") == var]
        outs = [outcomes[str(r.get("candidate_id"))] for r in cands if str(r.get("candidate_id")) in outcomes]
        filled = [o for o in outs if o.get("fillable_state") == "FILLED" and str(o.get("terminal_state") or "OPEN") != "OPEN" and o.get("gross_pnl_dollars") is not None]
        days = sorted({str(r.get("signal_timestamp", ""))[:10] for r in cands if r.get("signal_timestamp")})
        filled_days = sorted({str(o.get("signal_timestamp", ""))[:10] for o in filled if o.get("signal_timestamp")})
        post_c = [r for r in cands if (_ts(r.get("observed_at")) or datetime.min.replace(tzinfo=timezone.utc)) >= EPOCH]
        key = f"{strat}/{var}"
        pops[key] = {
            "candidates": len(cands), "outcomes": len(outs), "resolved_filled_economic": len(filled),
            "distinct_trading_days": len(days), "filled_trading_days": len(filled_days),
            "latest_candidate_observed_at": max((str(r.get("observed_at") or "") for r in cands), default=None),
            "latest_outcome_exit": max((str(o.get("exit_timestamp") or "") for o in outs), default=None),
            "post_epoch_candidates": len(post_c),
            "post_epoch_first_candidate": min((str(r.get("observed_at") or "") for r in post_c), default=None),
            "shas": sorted({str(r.get("generating_git_sha") or "MISSING")[:12] for r in cands}),
            "post_epoch_shas": sorted({str(r.get("generating_git_sha") or "MISSING")[:12] for r in post_c}),
            "gate_ready": len(filled) >= GATE_MIN_FILLED and len(days) >= GATE_MIN_DAYS,
            "gate": {"min_filled": GATE_MIN_FILLED, "min_days": GATE_MIN_DAYS},
        }
        if pops[key]["gate_ready"]:
            emit_event(state, "MILESTONE", f"ready_for_audit:{key}",
                       {"summary": f"{key} READY FOR AUDIT — {len(filled)} resolved FILLED economic outcomes over {len(days)} trading days (never auto-promoted)",
                        "population": pops[key]}, "DISCORD_ROUTE_DAILY_REPORT")
        if post_c:
            first = min(post_c, key=lambda r: str(r.get("observed_at") or ""))
            emit_event(state, "FIRST_FIRE", f"first_post_epoch_candidate:{key}",
                       {"summary": f"first post-epoch {key} candidate {first.get('candidate_id')} observed {first.get('observed_at')} sha={str(first.get('generating_git_sha'))[:12]}",
                        "row": {k: first.get(k) for k in ("candidate_id", "event_id", "instrument", "session", "signal_timestamp", "observed_at", "generating_git_sha", "provenance_status")}},
                       "DISCORD_ROUTE_DAILY_REPORT")
    camp["populations"] = pops
    if post:
        first = min(post, key=lambda r: str(r.get("observed_at") or ""))
        emit_event(state, "FIRST_FIRE", "first_post_epoch_campaign_row",
                   {"summary": f"first post-epoch campaign row: {first.get('record_type')} {first.get('strategy')}/{first.get('variant')} observed {first.get('observed_at')} sha={str(first.get('generating_git_sha'))[:12]} ({'OK' if str(first.get('generating_git_sha','')).startswith(RELEASE_SHA[:12]) else 'SHA MISMATCH'})"},
                   "DISCORD_ROUTE_DAILY_REPORT")

    # orb_reclaim pairing: every event_id with an orb_reclaim candidate must carry BOTH arms
    orb = [r for r in rows if r.get("record_type") == "CANDIDATE" and r.get("strategy") == "orb_reclaim"]
    if orb:
        by_evt: dict[str, set] = {}
        for r in orb:
            by_evt.setdefault(str(r.get("event_id")), set()).add(str(r.get("variant")))
        unpaired = [e for e, v in by_evt.items() if v != {"control", "modified"}]
        camp["orb_reclaim_events"] = len(by_evt)
        camp["orb_reclaim_unpaired_events"] = unpaired[:10]
        emit_event(state, "FIRST_FIRE", "first_orb_reclaim_candidate",
                   {"summary": f"first orb_reclaim candidate observed ({len(by_evt)} event(s)); unpaired events: {len(unpaired)}"}, "DISCORD_ROUTE_DAILY_REPORT")
        if unpaired:
            f.add("BLOCKED", "orb_reclaim_unpaired", f"{len(unpaired)} orb_reclaim event(s) missing control or modified arm", events=unpaired[:10])

    # release's own report (read-only) — integrity flag + cross-check
    rep, err = run_release_report()
    if rep is None:
        f.add("WARN", "release_report_failed", f"ops/forward_campaign_report.py failed: {err}")
    else:
        camp["report"] = {
            "candidate_rows": rep.get("candidate_rows"), "outcome_rows": rep.get("outcome_rows"),
            "evidence_integrity": rep.get("evidence_integrity"), "unexpected_populations": rep.get("unexpected_populations"),
            "populations": {f"{p['strategy']}/{p['variant']}": {k: p.get(k) for k in ("candidates", "resolved_filled_outcomes", "trading_days", "code_shas", "review_eligible")} for p in rep.get("populations", [])},
        }
        ei = rep.get("evidence_integrity") or {}
        if ei.get("ok") is False:
            f.add("BLOCKED", "report_evidence_integrity", f"release report evidence_integrity.ok=false: {ei}")
        if rep.get("unexpected_populations"):
            f.add("BLOCKED", "report_unexpected_population", f"release report unexpected_populations={rep.get('unexpected_populations')}")
        for k, p in camp["report"]["populations"].items():
            mine = pops.get(k)
            if mine and (p.get("resolved_filled_outcomes") != mine["resolved_filled_economic"] or p.get("trading_days") != mine["distinct_trading_days"]):
                f.add("WARN", f"report_mismatch_{k}", f"{k}: report filled/days={p.get('resolved_filled_outcomes')}/{p.get('trading_days')} vs watcher {mine['resolved_filled_economic']}/{mine['distinct_trading_days']}")


def check_failed_reclaim(state: dict, f: Findings, tick: dict) -> None:
    """First vwap_failed_reclaim=true after the epoch (journal context.vwap.failed_reclaim)."""
    if state["first_fire"].get("vwap_failed_reclaim_true"):
        tick["failed_reclaim_true_seen"] = state["first_fire"]["vwap_failed_reclaim_true"]
        return
    hits = []
    for p in sorted(LOG_DIR.glob("journal_*.jsonl"))[-3:]:
        try:
            for line in read_prod_text(p).splitlines():
                if '"failed_reclaim": true' not in line and '"failed_reclaim":true' not in line:
                    continue
                r = json.loads(line)
                t = _ts(r.get("ts"))
                if t and t >= EPOCH:
                    hits.append({"file": p.name, "ts": r.get("ts"), "instrument": r.get("instrument"), "session": r.get("session")})
        except Exception as exc:  # noqa: BLE001
            f.add("WARN", f"journal_scan_{p.name}", f"could not scan {p.name}: {exc}")
    tick["failed_reclaim_true_hits"] = len(hits)
    if hits:
        first = sorted(hits, key=lambda h: str(h["ts"]))[0]
        state["first_fire"]["vwap_failed_reclaim_true"] = first
        emit_event(state, "FIRST_FIRE", "first_vwap_failed_reclaim_true",
                   {"summary": f"first vwap_failed_reclaim=true after epoch: {first}", "hit": first}, "DISCORD_ROUTE_DAILY_REPORT")


# ── snapshot + blocked handling ──────────────────────────────────────────────
def capture_snapshot(reason: str, tick: dict, findings: Findings) -> Path:
    d = SNAP_DIR / f"{iso(now_utc()).replace(':', '')}_{re.sub(r'[^A-Za-z0-9_.-]', '_', reason)[:60]}"
    d.mkdir(parents=True, exist_ok=True)
    state_replace(d / "findings.json", json.dumps(findings.items, indent=1, sort_keys=True, default=str))
    state_replace(d / "tick.json", json.dumps(tick, indent=1, sort_keys=True, default=str))
    rc, out = run(["journalctl", "-u", SERVICE, "-n", "300", "--no-pager", "-q", "-o", "short-iso"], timeout=60)
    state_replace(d / "journalctl_tail.txt", out)
    memory = tick.get("memory_guard") or {}
    pid = str((memory.get("reading") or {}).get("pid") or "")
    for source, name in (
        (Path("/proc/meminfo"), "proc_meminfo.txt"),
        (Path("/proc/swaps"), "proc_swaps.txt"),
        (Path("/proc/pressure/memory"), "proc_pressure_memory.txt"),
        (Path(f"/proc/{pid}/status"), "service_proc_status.txt"),
        (Path(f"/proc/{pid}/smaps_rollup"), "service_smaps_rollup.txt"),
        (Path(f"/proc/{pid}/cgroup"), "service_cgroup.txt"),
    ):
        try:
            state_replace(d / name, read_prod_text(source))
        except Exception as exc:  # noqa: BLE001
            state_replace(d / (name + ".error"), str(exc))
    continuity = _paper_continuity_manifest(tick)
    state_replace(d / "paper_position_continuity.json", json.dumps(continuity, indent=1, sort_keys=True, default=str))
    for row in continuity["active_paper_states"]:
        try:
            source = Path(row["path"])
            state_replace(d / ("paper_state_" + source.name), read_prod_text(source))
        except Exception as exc:  # noqa: BLE001
            state_replace(d / ("paper_state_" + Path(row["path"]).name + ".error"), str(exc))
    for ep in ("/health", "/status/tradovate-reliability", "/status/broker-account", "/status/today"):
        j, err = http_get_json(ep)
        state_replace(d / (ep.strip("/").replace("/", "_") + ".json"), json.dumps(j if j is not None else {"error": err}, indent=1, default=str))
    try:
        state_replace(d / "campaign_tail.jsonl", read_prod_bytes_tail(CAMPAIGN_JSONL, 64_000).decode("utf-8", "replace"))
    except Exception as exc:  # noqa: BLE001
        state_replace(d / "campaign_tail.error", str(exc))
    for p in [SNAP_DIR.parent / "latest_tick.json"]:
        if p.exists():
            shutil.copy2(p, d / "previous_latest_tick.json")
    inv = []
    for p in sorted(LOG_DIR.glob("*")):
        try:
            st = os.stat(p)
            inv.append(f"{st.st_size:>12} {iso(datetime.fromtimestamp(st.st_mtime, timezone.utc))} {p.name}")
        except Exception:
            pass
    state_replace(d / "log_dir_inventory.txt", "\n".join(inv) + "\n")
    return d


def handle_memory_warning(state: dict, findings: Findings, tick: dict) -> None:
    warning = next((row for row in findings.warns() if row["key"] == "memory_warning"), None)
    if warning is None:
        previous = state.get("memory_warning") or {}
        if previous.get("active"):
            log("memory WARNING cleared after derived headroom returned healthy")
            notify(
                state, MEMORY_WARNING_ROUTE,
                _memory_discord_text("RECOVERED", "memory_warning", previous, tick),
                f"memory-recovered:memory_warning:{iso(now_utc())}",
            )
        state["memory_warning"] = {"active": False}
        return
    current = state.get("memory_warning") or {}
    if current.get("active"):
        return
    snap = capture_snapshot("WARNING_memory", tick, findings)
    state["memory_warning"] = {
        "active": True, "first_utc": iso(now_utc()), "snapshot": str(snap),
        "summary": warning["summary"],
    }
    state_append(EVENTS_FILE, json.dumps({
        "utc": iso(now_utc()), "kind": "WARNING", "key": "memory_warning",
        "summary": warning["summary"], "snapshot": str(snap),
    }, sort_keys=True) + "\n")
    notify(
        state, MEMORY_WARNING_ROUTE,
        _memory_discord_text("WARNING", "memory_warning", warning, tick, str(snap)),
        f"memory-warning:{iso(now_utc())[:13]}",
    )


MEM_FIXED_WARNING_KEYS = ("memory_rss_growth", "swap_used_warning", "swap_pressure_warning")


def handle_memory_fixed_warnings(state: dict, findings: Findings, tick: dict) -> None:
    """Route the fixed-check WARNINGs to Discord the same way the dynamic guard's
    memory_warning is routed: one snapshot + one notification per episode."""
    active = state.setdefault("memory_fixed_warnings", {})
    present = {row["key"]: row for row in findings.warns() if row["key"] in MEM_FIXED_WARNING_KEYS}
    for key in list(active):
        if key not in present and active[key].get("active"):
            log(f"memory WARNING cleared: {key}")
            notify(
                state, MEMORY_WARNING_ROUTE,
                _memory_discord_text("RECOVERED", key, active[key], tick),
                f"memory-fixed-recovered:{key}:{iso(now_utc())}",
            )
            active[key] = {"active": False}
    for key, warning in present.items():
        if (active.get(key) or {}).get("active"):
            continue
        snap = capture_snapshot(f"WARNING_{key}", tick, findings)
        active[key] = {"active": True, "first_utc": iso(now_utc()), "snapshot": str(snap), "summary": warning["summary"]}
        state_append(EVENTS_FILE, json.dumps({
            "utc": iso(now_utc()), "kind": "WARNING", "key": key,
            "summary": warning["summary"], "snapshot": str(snap),
        }, sort_keys=True) + "\n")
        notify(state, MEMORY_WARNING_ROUTE,
               _memory_discord_text("WARNING", key, warning, tick, str(snap)),
               f"memory-fixed-warning:{key}:{iso(now_utc())[:13]}")


# Plain-English titles for finding keys (prefix match). Anything not listed falls
# back to the key with underscores turned into spaces.
_FINDING_TITLES = {
    "watcher_release_stale": "Watcher needs restart",
    "unexpected_restart": "Unexpected futures-bot restart",
    "unexpected_deploy": "Unexpected deploy",
    "service_not_active": "futures-bot is not running",
    "service_crash_restart": "futures-bot crashed and restarted",
    "service_traceback": "Bot logged a traceback",
    "alert_non200": "Webhook post rejected",
    "webhook_process_count": "Wrong number of webhook processes",
    "journal_not_advancing": "Journal stopped growing",
    "evidence_write_error": "Evidence write failed",
    "unexpected_broker_position": "Unexpected broker position",
    "post_epoch_wrong_sha": "Evidence rows from the wrong release",
    "post_epoch_spans_releases": "Post-epoch evidence spans several releases",
    "orb_reclaim_unpaired": "orb_reclaim pairing defect",
    "deploy_candidate_running": "Leftover deploy verifier still running",
    "feed_alarm_stale": "Feed-gap alarm is stale",
    "oom_kill_new": "Kernel OOM-killed a process",
    "memory_rss_growth_critical": "futures-bot memory growing fast",
    "memory_rss_critical": "futures-bot memory near OOM level",
    "memory_avail_critical": "Box nearly out of memory",
    "memory_rss_growth": "futures-bot memory rising",
    "swap_pressure_critical": "Heavy swap paging",
    "swap_pressure_warning": "Kernel is paging to swap",
    "swap_used_critical": "Swap nearly exhausted",
    "swap_used_warning": "Swap filling up",
    "swap_inactive": "Swap is off",
    "swap_not_persistent": "Swap missing from fstab",
    "five_min_feed_stalled": "5-minute MNQ bar stream stalled",
    "five_min_feed_missing": "5-minute MNQ bar stream missing",
    "daily_22_collector_stalled": "Daily 2-2 lane stopped processing bars",
    "daily_22_state_epoch_mismatch": "Daily 2-2 state is from another epoch",
    "daily_22_state_unreadable": "Daily 2-2 state file unreadable",
    "daily_22_halted": "Daily 2-2 ledger hit its hard halt",
    "mes_122_lane_stalled": "MES 1-2-2 lane stopped evaluating bars",
    "hypothetical_position_exposed_stale_bars": "Open paper position with no fresh bars",
    "daily_22_state_missing_while_feed_active": "Daily 2-2 state missing while 5m bars flow",
    "mes_122_lane_missing_while_feed_active": "MES 1-2-2 lane missing while MES bars flow",
}

# A one-sentence explanation for the findings whose summary is too terse to read cold.
_FINDING_EXPLAIN = {
    "watcher_release_stale": "New release is live, but the watcher is still tracking the previous release.",
    "unexpected_restart": "The bot restarted and no approved deployment explains it.",
}


def _largest_rss_process() -> tuple[str, float] | None:
    """Return the current largest RSS process for display only; fail open."""
    rc, out = run(["ps", "-eo", "comm=,rss=", "--sort=-rss"], timeout=10)
    if rc != 0:
        return None
    for line in out.splitlines():
        parts = line.rsplit(None, 1)
        if len(parts) != 2:
            continue
        try:
            return parts[0], round(int(parts[1]) / 1024, 1)
        except ValueError:
            continue
    return None


def _memory_discord_text(status: str, key: str, finding: dict | None, tick: dict,
                         snapshot: str | None = None) -> str:
    """Present already-collected memory evidence without changing alert logic."""
    mem = tick.get("memory_fixed") or {}
    swap_in = mem.get("swapin_mb_since_last_tick")
    swap_out = mem.get("swapout_mb_since_last_tick")
    paging_known = swap_in is not None and swap_out is not None
    paging_active = paging_known and (float(swap_in) + float(swap_out) > 0)
    recovered = status == "RECOVERED"
    largest = _largest_rss_process()
    lines = [
        f"{'✅' if recovered else '⚠️'} **STATUS: {status}**",
        f"**ISSUE:** {_finding_title(key)}",
        ("**CURRENT STATE:** Earlier pressure triggered the warning; current memory pressure is clear."
         if recovered else f"**CURRENT STATE:** {str((finding or {}).get('summary') or 'Memory pressure is active.')}"),
        "**KEY EVIDENCE:**",
        f"• RAM available: {mem.get('avail_mb', 'unknown')} MiB",
        f"• Swap used: {mem.get('swap_used_mb', 'unknown')} MiB",
        (f"• Active paging: {'YES' if paging_active else 'NO'} ({swap_in} MiB in / {swap_out} MiB out)"
         if paging_known else "• Active paging: unknown"),
        f"• futures-bot RSS: {mem.get('rss_mb', 'unknown')} MiB",
        (f"• Largest RSS: {largest[0]} {largest[1]} MiB" if largest else "• Largest RSS: unavailable"),
        ("**ACTION:** None — continue monitoring."
         if recovered else f"**ACTION:** {smallest_fix(key).removeprefix('operator: ')}"),
    ]
    if snapshot:
        lines.append(f"-# Snapshot: `{snapshot}`")
    lines.append(f"-# `{key} · {SERVICE} · release {RELEASE_SHA[:8]}`")
    return "\n".join(lines)


def _finding_title(key: str) -> str:
    for prefix, title in _FINDING_TITLES.items():
        if key.startswith(prefix):
            return title
    return key.replace("_", " ").strip().capitalize()


def _finding_discord_text(level: str, key: str, finding: dict | None = None, snapshot: str | None = None) -> str:
    """Render one finding for a human reading Discord on a phone: headline first,
    the specific evidence next, then what to check. Never changes the finding."""
    status = "CRITICAL" if level == "BLOCKED" else level
    icon = "🛑" if status == "CRITICAL" else "⚠️"
    lines = [f"{icon} **STATUS: {status}**", f"**ISSUE:** {_finding_title(key)}"]
    explain = _FINDING_EXPLAIN.get(key)
    summary = (finding or {}).get("summary")
    if explain:
        lines.append(f"**CURRENT STATE:** {explain}")
    elif summary:
        lines.append(f"**CURRENT STATE:** {summary}")
    else:
        lines.append("**CURRENT STATE:** Condition is active.")
    detail = (finding or {}).get("detail") or {}
    samples = detail.get("samples") or []
    if samples:
        # the first offending log line is usually the whole answer (who / what / status)
        lines.append(f"↳ `{str(samples[0]).strip()[-160:]}`")
    action = smallest_fix(key)
    if action.startswith("operator: "):
        action = action[len("operator: "):]
    lines.append(f"**ACTION:** {action}")
    if snapshot:
        lines.append(f"-# Snapshot: `{snapshot}`")
    lines.append(f"-# `{key} · {SERVICE} · release {RELEASE_SHA[:8]}`")
    return "\n".join(lines)


def _blocked_discord_text(key: str, finding: dict | None = None, snapshot: str | None = None) -> str:
    return _finding_discord_text("BLOCKED", key, finding, snapshot)


def _cleared_discord_text(key: str, first_utc: str | None, tick: dict | None = None) -> str:
    since = ""
    if first_utc:
        mins = int((now_utc() - _ts(first_utc)).total_seconds() // 60)
        since = f" (was blocked {mins} min)"
    if key.startswith(("memory_", "swap_", "oom_")):
        return _memory_discord_text("RECOVERED", key, None, tick or {})
    return "\n".join([
        "✅ **STATUS: RECOVERED**",
        f"**ISSUE:** {_finding_title(key)}",
        f"**CURRENT STATE:** Condition cleared{since}.",
        "**ACTION:** None — continue monitoring.",
        f"-# `{key} · {SERVICE} · release {RELEASE_SHA[:8]}`",
    ])


# ── ACTION REQUIRED cards ────────────────────────────────────────────────────
# 2026-09-14: a 5h20m TradingView webhook outage (401s) left the Daily 2-2 paper
# short un-evaluable.  The watcher detected it (feed_MNQ_stale / feed_MES_stale
# BLOCKED at 16:51Z / 17:06Z, cleared 22:01Z) and posted every transition, but the
# posts read like routine telemetry and nobody acted for five hours.  Findings that
# need a human NOW are therefore rendered as a small fixed card that looks nothing
# like the informational stream.  This layer only changes Discord text: it never
# adds, removes or re-grades a finding, and the watcher stays read-only.
#
# Whitelist (operator ruling 09-15): feed stale while exposure exists, broker/auth
# ambiguity, unresolved order state, release-integrity failure, or a critical
# BLOCKED condition on the bot process itself.  Everything else keeps the plain
# BLOCKED/cleared text.
_ACTION_ALWAYS_PREFIXES = (
    "service_not_active", "service_crash_restart", "unexpected_restart", "unexpected_deploy",
    "webhook_process_count", "oom_kill_new",                       # bot process
    "unexpected_broker_position", "tradovate_",                    # broker / auth / order state
    "post_epoch_wrong_sha", "history_rewritten", "file_shrank",    # release / evidence integrity
    "daily_22_state_unreadable", "daily_22_halted",                # lane state integrity
)
# Only ACTION REQUIRED when a paper/demo position is open (otherwise informational).
_ACTION_IF_EXPOSED_PREFIXES = (
    "feed_", "five_min_feed_", "alert_non200", "journal_not_advancing", "hypothetical_position_exposed",
)
_LANE_LABELS = {"daily_22_5k": "Daily 2-2", "mes_122_1500": "MES 1-2-2", "wide_stop_6k": "4HR/3-2-2", "wide_stop_4k": "4HR/3-2-2"}
# Which open lanes a finding key actually exposes (feed_MES_* does not expose an MNQ lane).
_LANE_INSTRUMENT = {"mes_122_1500": "MES"}


def _open_lane_positions(tick: dict | None) -> list[tuple[str, dict]]:
    lanes = (tick or {}).get("lanes") or {}
    inv = lanes.get("inventory") or {}
    out = []
    for name in lanes.get("open_positions") or []:
        pos = (inv.get(name) or {}).get("open_position") or {}
        out.append((name, pos))
    return out


def _exposed_lanes_for(key: str, tick: dict | None) -> list[tuple[str, dict]]:
    """Open lane positions that this finding leaves un-evaluable."""
    positions = _open_lane_positions(tick)
    m = re.match(r"feed_([A-Z]+)_", key)
    if m:
        inst = m.group(1)
        positions = [(n, p) for n, p in positions if _LANE_INSTRUMENT.get(n, "MNQ") == inst]
    elif key.startswith("five_min_feed_"):
        positions = [(n, p) for n, p in positions if _LANE_INSTRUMENT.get(n, "MNQ") == "MNQ"]
    return positions


def action_required(key: str, tick: dict | None) -> bool:
    """True when this finding warrants an ACTION REQUIRED card (never alters the finding)."""
    if key.startswith(_ACTION_ALWAYS_PREFIXES):
        return True
    if key.startswith(_ACTION_IF_EXPOSED_PREFIXES):
        return bool(_exposed_lanes_for(key, tick))
    return False


def _exposure_line(key: str, tick: dict | None) -> str:
    lanes = _exposed_lanes_for(key, tick) if key.startswith(_ACTION_IF_EXPOSED_PREFIXES) else _open_lane_positions(tick)
    if not lanes:
        return "no open paper/demo position"
    parts = []
    for name, pos in lanes:
        label = _LANE_LABELS.get(name, name)
        direction = str(pos.get("direction") or "position").upper()
        parts.append(f"{label} paper {direction} is OPEN" + (f" @ {pos.get('entry')}" if pos.get("entry") is not None else ""))
    return "; ".join(parts)


def _bar_age_minutes(inst: str, tick: dict | None) -> int | None:
    lanes = (tick or {}).get("lanes") or {}
    ts = lanes.get("newest_5m_mnq_bar_mtime") if inst == "MNQ" else lanes.get("newest_mes_15m_bar_mtime")
    if not ts and inst == "MNQ":
        ts = lanes.get("newest_15m_mnq_bar_mtime")
    d = _ts(ts)
    return int((now_utc() - d).total_seconds() // 60) if d else None


def _action_headline_problem_impact(key: str, finding: dict | None, tick: dict | None) -> tuple[str, str, str]:
    summary = str((finding or {}).get("summary") or "").strip()
    m = re.match(r"feed_([A-Z]+)_", key)
    if m:
        inst = m.group(1)
        age = _bar_age_minutes(inst, tick)
        problem = f"No {inst} bars for {age} min" if age is not None else f"{inst} bar feed is stale"
        return (f"{inst} FEED STALE", problem, "Stop/target cannot be evaluated while feed is down")
    if key.startswith("five_min_feed_"):
        age = _bar_age_minutes("MNQ", tick)
        problem = f"No MNQ 5m bars for {age} min while 15m bars keep arriving" if age is not None else "MNQ 5m stream stopped while 15m continues"
        return ("MNQ 5M FEED STALLED", problem, "Every MNQ paper lane resolves on 5m bars — stop/target cannot be evaluated")
    if key.startswith("alert_non200"):
        return ("WEBHOOK POSTS REJECTED", summary or "TradingView posts are being rejected by the bot", "Bars are not reaching the decision engine; open positions are not being evaluated")
    if key.startswith("journal_not_advancing"):
        return ("JOURNAL STOPPED", summary or "A 15m bar arrived but the journal did not grow", "The bot is receiving bars but not processing them")
    if key.startswith("hypothetical_position_exposed"):
        return ("POSITION EXPOSED TO STALE BARS", summary, "Stop/target cannot be resolved; do not assume neither was touched")
    if key.startswith(("service_not_active", "service_crash_restart")):
        return (_finding_title(key).upper(), summary or "futures-bot is down", "Nothing is being evaluated; any open position is unmanaged")
    if key.startswith(("unexpected_restart", "unexpected_deploy", "webhook_process_count")):
        return (_finding_title(key).upper(), summary, "Runtime identity is uncertain; evidence and position state may not be what you think")
    if key.startswith(("unexpected_broker_position", "tradovate_")):
        return (_finding_title(key).upper(), summary or "Broker state does not match what the bot believes", "Broker/order state is ambiguous — verify the account by hand before anything else")
    if key.startswith(("post_epoch_wrong_sha", "history_rewritten", "file_shrank")):
        return (_finding_title(key).upper(), summary, "Evidence integrity is compromised until reconciled")
    if key.startswith("daily_22_"):
        return (_finding_title(key).upper(), summary, "The Daily 2-2 lane fails closed until its state is repaired")
    return (_finding_title(key).upper(), summary, "Operator judgement needed")


def _et_clock(utc_iso: str | None) -> str:
    d = _ts(utc_iso) or now_utc()
    return d.astimezone(ET).strftime("%-I:%M %p ET")


def _action_card_text(key: str, finding: dict | None, tick: dict | None, first_utc: str | None) -> str:
    headline, problem, impact = _action_headline_problem_impact(key, finding, tick)
    do_now = smallest_fix(key)
    if do_now.startswith("operator: "):
        do_now = do_now[len("operator: "):]
    if key.startswith("feed_") or key.startswith("alert_non200"):
        do_now = "Check TradingView alerts / webhook delivery"
    return "\n".join([
        f"🛑 **ACTION REQUIRED — {headline}**",
        f"**Exposure:** {_exposure_line(key, tick)}",
        f"**Problem:** {problem}",
        f"**Impact:** {impact}",
        f"**Do now:** {do_now}",
        f"**Since:** {_et_clock(first_utc)}",
        f"`{key} · read-only`",
    ])


def _resolved_card_text(key: str, first_utc: str | None, tick: dict | None) -> str:
    mins = None
    if first_utc and _ts(first_utc):
        mins = int((now_utc() - _ts(first_utc)).total_seconds() // 60)
    m = re.match(r"feed_([A-Z]+)_", key)
    if m:
        headline = f"{m.group(1)} FEED RECOVERED"
        body = f"Bars flowing again after {mins} min." if mins is not None else "Bars flowing again."
    elif key.startswith("five_min_feed_"):
        headline = "MNQ 5M FEED RECOVERED"
        body = f"5m bars flowing again after {mins} min." if mins is not None else "5m bars flowing again."
    else:
        headline = f"{_finding_title(key).upper()} CLEARED"
        body = f"Condition cleared after {mins} min." if mins is not None else "Condition cleared."
    lines = [f"✅ **RESOLVED — {headline}**", body]
    exposed = _exposed_lanes_for(key, tick) if key.startswith(_ACTION_IF_EXPOSED_PREFIXES) else _open_lane_positions(tick)
    for name, pos in exposed:
        lines.append(f"{_LANE_LABELS.get(name, name)} paper position remains OPEN"
                     + (f" ({str(pos.get('direction') or '').upper()} @ {pos.get('entry')})." if pos.get("entry") is not None else "."))
    lines.append(f"`{key} · read-only`")
    return "\n".join(lines)


def handle_blocked(state: dict, findings: Findings, tick: dict) -> None:
    blocked = findings.blocked()
    current = {b["key"]: b for b in blocked}
    new_keys = [k for k in current if k not in state["blocked"]]
    cleared = [k for k in state["blocked"] if k not in current]
    for k in cleared:
        log(f"BLOCKED cleared: {k}")
        was = state["blocked"].pop(k, None) or {}
        state["blocked_last_notified"].pop(k, None)
        state["notified"].pop(f"blocked:{k}", None)
        # a condition raised as an ACTION REQUIRED card is resolved as one too
        text = (_resolved_card_text(k, was.get("first_utc"), tick) if was.get("action_required")
                else _cleared_discord_text(k, was.get("first_utc"), tick))
        notify(state, "DISCORD_ROUTE_ERROR", text, f"blocked-cleared:{k}:{iso(now_utc())}")
    if not blocked:
        return
    snap = None
    if new_keys:
        snap = capture_snapshot("BLOCKED_" + "_".join(new_keys)[:40], tick, findings)
    for k, b in current.items():
        if k not in state["blocked"]:
            state["blocked"][k] = {"first_utc": iso(now_utc()), "summary": b["summary"], "snapshot": str(snap) if snap else None,
                                   "action_required": action_required(k, tick)}
            state_append(EVENTS_FILE, json.dumps({"utc": iso(now_utc()), "kind": "BLOCKED", "key": k, "summary": b["summary"], "detail": b["detail"], "snapshot": str(snap) if snap else None}, sort_keys=True, default=str) + "\n")
            log(f"BLOCKED {k}: {b['summary']} (snapshot {snap})")
        rec = state["blocked"][k]
        # exposure can appear after the condition was first raised (feed dies, then a
        # lane opens on the last bar): promote to a card once, never demote.
        if not rec.get("action_required") and action_required(k, tick):
            rec["action_required"] = True
            state["blocked_last_notified"].pop(k, None)
        last = state["blocked_last_notified"].get(k)
        remind = (not last) or (now_utc() - _ts(last)).total_seconds() >= BLOCKED_REMINDER_S
        if remind:
            state["notified"].pop(f"blocked:{k}", None)
            text = (_action_card_text(k, b, tick, rec.get("first_utc")) if rec.get("action_required")
                    else _blocked_discord_text(k, b, rec.get("snapshot")))
            notify(state, "DISCORD_ROUTE_ERROR", text, f"blocked:{k}")
            state["blocked_last_notified"][k] = iso(now_utc())
            if rec.get("action_required"):
                _maybe_triage(state, k, b, tick, rec.get("first_utc"))


def _recent_events(n: int = 8) -> list[dict]:
    """Last n rows of the watcher's own events file (its state, under /tmp) — never production."""
    try:
        lines = EVENTS_FILE.read_text(encoding="utf-8").splitlines()[-n:]
        return [json.loads(l) for l in lines if l.strip()]
    except Exception:  # noqa: BLE001
        return []


def _maybe_triage(state: dict, key: str, finding: dict, tick: dict, first_utc: str | None) -> None:
    """Read-only LLM advisory for an ACTION REQUIRED condition (watcher_triage).

    Runs AFTER the Discord card is posted, once per raise, only when the operator
    has configured a key; every failure is a log line. Zero authority anywhere."""
    if watcher_triage is None:
        return
    try:
        headline = _action_headline_problem_impact(key, finding, tick)[0]
        watcher_triage.maybe_triage(
            key=key, finding=finding, tick=tick, state=state, first_utc=first_utc, headline=headline,
            release_sha=RELEASE_SHA, service=SERVICE, now=now_utc(),
            env_value=_env_value, notify=notify, log=log, recent_events=_recent_events(),
        )
    except Exception as exc:  # noqa: BLE001 — advisory lane must never break the tick
        log(f"TRIAGE FAILED {key}: {type(exc).__name__}: {exc}")


def smallest_fix(key: str) -> str:
    m = {
        "watcher_release_stale": "operator: restart `afs-watcher` only — the bot is fine, the watcher is pinned to the old release",
        "unexpected_deploy": "operator: confirm the deploy was intended; re-arm the watcher on the new release (no auto-fix)",
        "service_not_active": "operator: inspect `journalctl -u futures-bot`; restart only by operator decision",
        "unexpected_restart": "operator: confirm who restarted futures-bot — a sanctioned --release re-baselines on its own, and this restart could not be tied to one (see not_adopted); re-baseline by hand only if intended",
        "service_crash_restart": "operator: read the crash traceback in the snapshot before any restart",
        "deploy_candidate_running": "operator: stop the leftover afs-candidate unit (systemctl, operator-run) after confirming it is a stale verifier",
        "webhook_process_count": "operator: identify the extra/missing uvicorn process in the snapshot",
        "feed_alarm_stale": "operator: check the feed-gap cron on the box",
        "alert_non200": "operator: read the rejected alert lines in the snapshot (secret/rate-limit/payload)",
        "service_traceback": "operator: read the traceback in the snapshot; no fix is applied automatically",
        "evidence_write_error": "operator: check disk/permissions on /root/afs-shared/logs; see snapshot",
        "journal_not_advancing": "operator: a 15m bar arrived but the journal did not grow — real stall (5m-only traffic no longer triggers this); check the service log in the snapshot",
        "unexpected_broker_position": "operator: verify the demo account manually; the watcher never touches broker state",
        "post_epoch_wrong_sha": "operator: evidence provenance defect — quarantine the rows listed in the snapshot",
        "orb_reclaim_unpaired": "operator: pairing defect — audit the listed event_ids",
        "oom_kill_new": "operator: read `journalctl -k` for the victim; free headroom (swap/resident processes); never auto-restart",
        "memory_rss_growth_critical": "operator: futures-bot footprint grew >= 250 MB in ~2 h — inspect /tmp/afs_watcher/memory.jsonl and the snapshot before any restart decision",
        "memory_rss_critical": "operator: futures-bot footprint near the OOM level — inspect /tmp/afs_watcher/memory.jsonl before any restart decision",
        "memory_avail_critical": "operator: box nearly out of memory — identify the largest resident processes (ps --sort=-rss)",
        "swap_pressure_critical": "operator: heavy paging — the box is over-committed; reduce resident processes",
        "swap_used_critical": "operator: swap nearly exhausted — identify what is parked in swap (smem/ps) before it OOMs",
        "swap_inactive": "operator: `swapon /swapfile` (persisted in fstab)",
        "swap_not_persistent": "operator: restore the /swapfile line in /etc/fstab",
        "memory_rss_growth": "operator: footprint rising ~150 MB/2 h — watch the next ticks; CRITICAL fires at +250 MB",
        "swap_used_warning": "operator: swap filling — check what is parked in swap before it reaches 1800 MB",
        "swap_pressure_warning": "operator: the kernel is paging — check for a memory spike in ps --sort=-rss",
        "five_min_feed": "operator: check the TradingView 5m MNQ alert and `GET /status/five-min`; every MNQ paper lane resolves on this stream",
        "daily_22_collector_stalled": "operator: read the service log in the snapshot — the 5m hook raises before the Daily collector (state-integrity or wide-stop error)",
        "daily_22_state_epoch_mismatch": "operator: the persisted swing state predates the pinned epoch — do NOT delete it; decide epoch vs state by hand",
        "daily_22_state_unreadable": "operator: inspect swing_state.json in the snapshot; the lane fails closed until it is valid",
        "daily_22_halted": "operator: hard paper halt reached — evidence collection for this ledger is over; no automatic reset",
        "mes_122_lane_stalled": "operator: read the service log for `mes_122 paper lane hook skipped` lines",
        "hypothetical_position_exposed_stale_bars": "operator: a paper position cannot be resolved without bars — restore the 5m feed; never infer the outcome from later OHLC",
        "daily_22_state_missing_while_feed_active": "operator: post-epoch MNQ 5m bars exist but swing_state.json does not — check the service log for the 5m hook failing closed; never recreate the state by hand",
        "mes_122_lane_missing_while_feed_active": "operator: post-epoch MES 15m bars exist but the MES 1-2-2 lane wrote nothing — check `mes_122 paper lane hook skipped` in the service log",
    }
    for k, v in m.items():
        if key.startswith(k):
            return v
    if key.startswith("feed_"):
        return "operator: check TradingView alert delivery / chart alerts (feed-gap alarm says stale)"
    if key.startswith(("file_shrank", "history_rewritten", "path_missing", "campaign_corrupt", "campaign_state_corrupt")):
        return "operator: evidence integrity defect — compare against the watcher snapshot and the box backups before any repair"
    if key.startswith("disk_"):
        return "operator: free disk space (never delete evidence)"
    if key.startswith("tradovate_"):
        return "operator: inspect /status/tradovate-reliability; no recovery is triggered by the watcher"
    return "operator: inspect the snapshot; no automatic fix"


# ── daily reconciliation (after New York close) ──────────────────────────────
def maybe_daily(state: dict, tick: dict, findings: Findings) -> None:
    now_et = now_utc().astimezone(ET)
    if now_et.weekday() > 4:
        return
    if (now_et.hour, now_et.minute) < DAILY_RUN_ET:
        return
    key = now_et.strftime("%Y-%m-%d")
    if key in state["daily_done"]:
        return
    log(f"DAILY reconciliation starting for ET date {key}")
    disc: list[str] = []
    rep: dict = {"et_date": key, "utc": iso(now_utc()), "release": RELEASE_SHA, "epoch": iso(EPOCH)}
    today, err = http_get_json("/status/today")
    rep["status_today"] = {k: (today or {}).get(k) for k in ("date", "trade_count", "wins", "losses", "no_trades", "has_open_position", "open_position", "realized_pnl_dollars", "live_trading_enabled", "paper_mode", "top_no_trade_reasons")} if today else {"error": err}
    if today is None:
        disc.append(f"/status/today unreachable: {err}")
    elif today.get("has_open_position"):
        disc.append(f"journal still shows an open position after New York close: {today.get('open_position')}")
    ba, err = http_get_json("/status/broker-account")
    rep["broker_account"] = {k: (ba or {}).get(k) for k in ("ok", "env", "position", "open_pnl", "realized_pnl", "message")} if ba else {"error": err}
    if ba is None:
        disc.append(f"/status/broker-account unreachable: {err}")
    elif ba.get("position"):
        disc.append(f"broker reports an open position after New York close: {ba.get('position')}")
    proof, err = http_get_json(f"/status/proof/mnq-30?freeze_ts={iso(EPOCH)}", timeout=60)
    rep["proof_mnq30_since_epoch"] = {k: (proof or {}).get(k) for k in ("ok", "journal_pnl_dollars", "broker_realized_pnl", "resolved_trades", "resolved_mnq_trades", "unmatched_outcomes", "unmatched_mnq_outcomes", "reconciler_touched_count", "warnings", "broker_account_error", "journal_read_errors")} if proof else {"error": err}
    if proof is None:
        disc.append(f"/status/proof/mnq-30 unreachable: {err}")
    else:
        if proof.get("ok") is False:
            disc.append(f"proof report ok=false: warnings={proof.get('warnings')}")
        if proof.get("unmatched_outcomes"):
            disc.append(f"{proof.get('unmatched_outcomes')} journal outcome(s) unmatched to a trade since epoch")
        if proof.get("journal_read_errors"):
            disc.append(f"journal read errors: {proof.get('journal_read_errors')}")
        try:
            jp, bp = float(proof.get("journal_pnl_dollars") or 0), float(proof.get("broker_realized_pnl") or 0)
            if abs(jp - bp) > 0.01 and proof.get("broker_account_error") is None and proof.get("resolved_trades"):
                disc.append(f"journal P&L since epoch {jp} != broker realized {bp}")
        except (TypeError, ValueError):
            pass
    # collector census (release's read-only script)
    py = str(RELEASE_DIR / ".venv" / "bin" / "python")
    env = {"PATH": "/usr/bin:/bin", "PYTHONPATH": str(RELEASE_DIR), "PYTHONDONTWRITEBYTECODE": "1", "HOME": "/tmp"}
    rc, out = run([py, "ops/collector_census.py", "--log-dir", str(LOG_DIR), "--json"], timeout=150, env=env, cwd=str(RELEASE_DIR))
    try:
        census = json.loads(out)
        rep["census"] = [{k: c.get(k) for k in ("name", "status", "age_minutes", "last")} for c in census.get("collectors", [])]
        for c in census.get("collectors", []):
            if c.get("name") in ("futures journal", "bars MNQ", "bars MES", "strategy context", "feed gap alarm") and c.get("status") != "FRESH":
                disc.append(f"collector '{c.get('name')}' is {c.get('status')} (age {c.get('age_minutes')} min)")
        lane_disc, lane_skipped = _lane_census_discrepancies(census.get("collectors", []), tick.get("lanes") or {}, now_et.date())
        disc.extend(lane_disc)
        rep["lane_census_skipped_no_bar_today"] = lane_skipped
        rep["hypothetical_lanes"] = census.get("hypothetical_lanes")
    except Exception as exc:  # noqa: BLE001
        rep["census"] = {"error": f"rc={rc} {out[-300:]} {exc}"}
        disc.append("collector census could not be produced")
    # alert + error tally for the ET day, from the service log
    start_local = now_et.replace(hour=0, minute=0, second=0, microsecond=0).astimezone().strftime("%Y-%m-%d %H:%M:%S")
    rc, out = run(["journalctl", "-u", SERVICE, "--since", start_local, "--no-pager", "-q"], timeout=120)
    posts: dict[str, int] = {}
    tb = 0
    for l in out.splitlines():
        if "POST /webhook/alert" in l:
            m = re.search(r'HTTP/1\.[01]" (\d{3})', l)
            code = m.group(1) if m else "?"
            posts[code] = posts.get(code, 0) + 1
        if "traceback" in l.lower():
            tb += 1
    rep["alerts_today"] = posts
    rep["tracebacks_today"] = tb
    if any(k != "200" for k in posts):
        disc.append(f"non-200 alert responses today: {posts}")
    if tb:
        disc.append(f"{tb} traceback line(s) in the service log today")
    rep["campaign"] = tick.get("campaign", {}).get("populations")
    rep["campaign_rows_after_epoch"] = tick.get("campaign", {}).get("rows_after_epoch")
    rep["lanes"] = tick.get("lanes")
    expected_after_close, after_close_disc = _after_close_position_status(tick.get("lanes") or {})
    rep["expected_open_positions_after_close"] = expected_after_close
    disc.extend(after_close_disc)
    rep["open_blockers"] = dict(state["blocked"])
    if state["blocked"]:
        disc.append(f"open BLOCKED conditions: {sorted(state['blocked'])}")
    verdict = "DAILY PASS" if not disc else "DAILY BLOCKED"
    rep["verdict"] = verdict
    rep["discrepancies"] = disc
    state_replace(DAILY_DIR / f"{key}.json", json.dumps(rep, indent=1, sort_keys=True, default=str))
    if disc:
        snap = capture_snapshot(f"DAILY_BLOCKED_{key}", tick, findings)
        rep["snapshot"] = str(snap)
        state_replace(DAILY_DIR / f"{key}.json", json.dumps(rep, indent=1, sort_keys=True, default=str))
    state["daily_done"][key] = {"utc": iso(now_utc()), "verdict": verdict, "discrepancies": disc}
    pops = rep.get("campaign") or {}
    arm_line = "; ".join(f"{k}: {v['candidates']}c/{v['resolved_filled_economic']}f/{v['distinct_trading_days']}d" for k, v in pops.items())
    summary = f"{verdict} {key} — alerts {posts} — post-epoch campaign rows {rep['campaign_rows_after_epoch']} — {arm_line}"
    if disc:
        summary += " — discrepancies: " + "; ".join(disc)
    state_append(EVENTS_FILE, json.dumps({"utc": iso(now_utc()), "kind": "DAILY", "key": key, "verdict": verdict, "discrepancies": disc}, sort_keys=True) + "\n")
    log(f"DAILY {summary}")
    notify(state, "DISCORD_ROUTE_ERROR" if disc else "DISCORD_ROUTE_DAILY_REPORT",
           _daily_discord_text(verdict, key, posts, rep["campaign_rows_after_epoch"], pops, disc, str(DAILY_DIR / (key + ".json")), tick.get("lanes")),
           f"daily:{key}")


# Which bar stream proves each lane heartbeat could have moved today. A lane file
# is only allowed to count as a DAILY discrepancy when its own stream delivered a
# bar during the ET day being reconciled — a holiday or a closed session leaves
# every lane file legitimately untouched and must not read as a failure.
LANE_CENSUS_STREAM = {
    "bars MNQ 5m": "newest_5m_mnq_bar_mtime",
    "daily_22 swing state": "newest_5m_mnq_bar_mtime",
    "mes_122 lane journal": "newest_mes_15m_bar_mtime",
}


def _after_close_position_status(lanes: dict) -> tuple[list[dict], list[str]]:
    """Classify existing lane positions using only established lane semantics."""
    expected: list[dict] = []
    discrepancies: list[str] = []
    inventory = lanes.get("inventory") or {}
    for name in lanes.get("open_positions") or []:
        pos = (inventory.get(name) or {}).get("open_position") or {}
        if name in EXPECTED_OVERNIGHT_LANES:
            expected.append({"lane": name, "position": pos})
        else:
            discrepancies.append(
                f"hypothetical lane {name} holds an OPEN paper position after New York close: "
                f"{pos.get('direction')} @ {pos.get('entry')} since {pos.get('entry_time')}"
            )
    return expected, discrepancies


def _lane_census_discrepancies(collectors: list, lanes: dict, et_day: date) -> tuple[list[str], list[str]]:
    """Lane census rows that are real discrepancies today, and those skipped (no bar today)."""
    disc: list[str] = []
    skipped: list[str] = []
    for c in collectors:
        name = c.get("name")
        if name not in LANE_CENSUS_NAMES or c.get("status") == "FRESH":
            continue
        stamp = _ts(lanes.get(LANE_CENSUS_STREAM.get(name, "")))
        if stamp is None or stamp.astimezone(ET).date() != et_day:
            skipped.append(f"{name}: {c.get('status')} but its bar stream delivered nothing on {et_day} (market closed / fresh)")
            continue
        disc.append(f"collector '{name}' is {c.get('status')} (age {c.get('age_minutes')} min) while its bar stream was active today")
    return disc, skipped


def _daily_discord_text(verdict: str, day: str, posts: dict, rows_after_epoch, pops: dict, disc: list, path: str, lanes: dict | None = None) -> str:
    icon = "✅" if verdict == "DAILY PASS" else "🛑"
    total = sum(posts.values()) if posts else 0
    non200 = {k: v for k, v in posts.items() if k != "200"}
    posts_line = f"Webhook posts: {total}" + (" (all 200)" if total and not non200 else (f" — rejected: {non200}" if non200 else ""))
    status = "HEALTHY" if verdict == "DAILY PASS" else "CRITICAL"
    lines = [f"{icon} **STATUS: {status}**", "**ISSUE:** Daily watcher reconciliation",
             f"**CURRENT STATE:** {verdict} for {day}", "**KEY EVIDENCE:**", f"• {posts_line}",
             f"• Post-epoch campaign rows: {rows_after_epoch}"]
    reporting = sum(f"{strategy}/{variant}" in pops for strategy, variant in EXPECTED_POPULATIONS)
    lines.append(f"• Forward campaign: {reporting}/{len(EXPECTED_POPULATIONS)} arms reporting")
    for strategy, variant in EXPECTED_POPULATIONS:
        key = f"{strategy}/{variant}"
        value = pops.get(key)
        if value is None:
            lines.append(f"  • {key}: MISSING")
            continue
        candidates = value.get("candidates", 0)
        filled = value.get("resolved_filled_economic", 0)
        days = value.get("distinct_trading_days", 0)
        state = "0 candidates" if candidates == 0 else "OK"
        lines.append(f"  • {key}: {state} · {candidates} cand · {filled} filled · {days} days")
    paired = [pops.get(f"{strategy}/{variant}", {}) for strategy, variant in EXPECTED_POPULATIONS if variant in {"control", "modified"}]
    gate_days = min((p.get("distinct_trading_days", 0) for p in paired), default=0)
    gate_filled = min((p.get("resolved_filled_economic", 0) for p in paired), default=0)
    lines.append(f"• Gate: {gate_days}/{GATE_MIN_DAYS} days · {gate_filled}/{GATE_MIN_FILLED} resolved filled per control/modified arm")
    for d in disc:
        lines.append(f"⚠️ {d}")
    if lanes:
        for name, row in sorted((lanes.get("inventory") or {}).items()):
            pos = row.get("open_position") or {}
            if pos:
                expected = name in EXPECTED_OVERNIGHT_LANES
                instrument = "MES" if name == "mes_122_1500" else "MNQ"
                lines.append(f"• {_LANE_LABELS.get(name, name)} · {instrument} · {pos.get('direction')} @ {pos.get('entry')} · "
                             f"OPEN · overnight {'EXPECTED' if expected else 'NOT EXPECTED'}")
        newest = lanes.get("newest_5m_mnq_bar_mtime")
        stamp = _ts(newest)
        age = int((now_utc() - stamp).total_seconds() // 60) if stamp else None
        feed = "STALE" if lanes.get("five_min_feed_stalled") else "HEALTHY"
        lines.extend([f"• Feed: {feed}", f"• Newest 5m bar: {newest or 'unavailable'}",
                      f"• Bar age: {age if age is not None else 'unknown'} min"])
        if feed == "STALE":
            lines.append(f"• Threshold: {LANE_STALL_MIN} min (existing lane-stall threshold)")
    lines.append("**ACTION:** None — continue monitoring." if not disc else "**ACTION:** Review the listed active problems.")
    lines.append(f"-# File: `{path}`")
    return "\n".join(lines)


# ── interim two-week audit ───────────────────────────────────────────────────
def maybe_interim(state: dict, tick: dict) -> None:
    if state.get("interim_done") or now_utc() < INTERIM_AT:
        return
    pops = tick.get("campaign", {}).get("populations") or {}
    report = {
        "title": "INTERIM EVIDENCE AUDIT (two weeks after epoch) — NOT a gate decision",
        "utc": iso(now_utc()), "epoch": iso(EPOCH), "release": RELEASE_SHA,
        "gate": {"min_resolved_filled_economic_outcomes": GATE_MIN_FILLED, "min_distinct_trading_days": GATE_MIN_DAYS},
        "note": "Two calendar weeks contain at most 10 trading days; the >=20 trading-day gate cannot be satisfied at this checkpoint by construction. Monitoring continues.",
        "populations": pops,
        "campaign_rows_after_epoch": tick.get("campaign", {}).get("rows_after_epoch"),
        "shas_all": tick.get("campaign", {}).get("shas_all"),
        "release_report": tick.get("campaign", {}).get("report"),
        "first_fire": state.get("first_fire"), "events_seen": state.get("events_seen"),
        "open_blockers": state.get("blocked"), "daily_verdicts": state.get("daily_done"),
        "runtime": tick.get("runtime"), "storage": tick.get("storage"),
    }
    path = INTERIM_DIR / f"interim_audit_{now_utc().strftime('%Y-%m-%dT%H%MZ')}.json"
    state_replace(path, json.dumps(report, indent=1, sort_keys=True, default=str))
    md = [f"# {report['title']}", f"UTC {report['utc']} · epoch {report['epoch']} · release {RELEASE_SHA[:12]}", "", report["note"], "", "| arm | candidates | resolved FILLED econ | trading days | post-epoch cands | SHAs | gate |", "|---|---|---|---|---|---|---|"]
    for k, v in pops.items():
        md.append(f"| {k} | {v['candidates']} | {v['resolved_filled_economic']} | {v['distinct_trading_days']} | {v['post_epoch_candidates']} | {','.join(v['shas'])} | {'READY FOR AUDIT' if v['gate_ready'] else 'NOT MET'} |")
    md += ["", f"Open blockers: {sorted(state.get('blocked') or [])}", f"Daily verdicts: {json.dumps({k: v['verdict'] for k, v in (state.get('daily_done') or {}).items()})}"]
    state_replace(path.with_suffix(".md"), "\n".join(md) + "\n")
    state["interim_done"] = True
    state_append(EVENTS_FILE, json.dumps({"utc": iso(now_utc()), "kind": "INTERIM_AUDIT", "path": str(path)}) + "\n")
    log(f"INTERIM EVIDENCE AUDIT written: {path}")
    evidence_lines = []
    for k, v in sorted(pops.items()):
        evidence_lines.append(
            f"**{k}** — {v['candidates']} candidates · "
            f"{v['resolved_filled_economic']} resolved filled · "
            f"{v['distinct_trading_days']} trading days"
        )
    status = "Not a gate decision · collection continues"
    message = "\n".join([
        "**Read-only daily pass**",
        "",
        "**Status**",
        status,
        "",
        "**Evidence audit**",
        "Two weeks post-epoch checkpoint",
        *(evidence_lines or ["No populations available in the watcher tick"]),
        "",
        "**Action**",
        "No rule change · no deploy · no restart",
        "",
        f"**Artifact**\n`{path}`",
    ])
    notify(state, "DISCORD_ROUTE_DAILY_REPORT", message, "interim")


# ── one tick ─────────────────────────────────────────────────────────────────
def tick_once(state: dict) -> dict:
    f = Findings()
    tick: dict = {"utc": iso(now_utc()), "tick": state["ticks"] + 1}
    for name, fn in (("runtime", check_runtime), ("memory", check_memory), ("memory_fixed", check_memory_fixed), ("storage", check_storage), ("lanes", check_lanes), ("campaign", check_campaign), ("failed_reclaim", check_failed_reclaim)):
        try:
            fn(state, f, tick)
        except Exception as exc:  # noqa: BLE001
            f.add("WARN", f"watcher_check_error_{name}", f"{type(exc).__name__}: {exc}", tb=traceback.format_exc()[-800:])
    handle_memory_warning(state, f, tick)
    handle_memory_fixed_warnings(state, f, tick)
    handle_blocked(state, f, tick)
    try:
        maybe_daily(state, tick, f)
    except Exception as exc:  # noqa: BLE001
        log(f"WARN daily reconciliation error: {type(exc).__name__}: {exc}\n{traceback.format_exc()[-600:]}")
    try:
        maybe_interim(state, tick)
    except Exception as exc:  # noqa: BLE001
        log(f"WARN interim audit error: {type(exc).__name__}: {exc}")
    tick["findings"] = f.items
    tick["verdict"] = "BLOCKED" if f.blocked() else ("WARN" if f.warns() else "OK")
    tick["open_blockers"] = sorted(state["blocked"])
    state["ticks"] += 1
    state["last_tick_utc"] = tick["utc"]
    state_replace(LATEST_FILE, json.dumps(tick, indent=1, sort_keys=True, default=str))
    save_state(state)
    pops = tick.get("campaign", {}).get("populations") or {}
    arm = " ".join(f"{k.split('/')[0][:4]}/{k.split('/')[1][:3]}={v['candidates']}c/{v['resolved_filled_economic']}f/{v['distinct_trading_days']}d" for k, v in pops.items())
    rt = tick.get("runtime", {})
    log(f"TICK {tick['tick']} {tick['verdict']} svc={rt.get('service', {}).get('ActiveState')} alerts={rt.get('alerts_since_last_tick')} "
        f"trad={rt.get('tradovate', {}).get('state')} feed={[d.get('status') for d in (rt.get('feed', {}).get('instruments') or {}).values()]} "
        f"post_epoch_rows={tick.get('campaign', {}).get('rows_after_epoch')} {arm} "
        f"lanes_open={tick.get('lanes', {}).get('open_positions')} 5m_stalled={tick.get('lanes', {}).get('five_min_feed_stalled')} "
        f"blocked={tick['open_blockers']} warns={[w['key'] for w in f.warns()]}")
    return tick


STARTUP_API_GRACE_SECONDS = 30
STARTUP_API_POLL_SECONDS = 1


def wait_for_status_api_ready(
    *,
    grace_seconds: float = STARTUP_API_GRACE_SECONDS,
    poll_seconds: float = STARTUP_API_POLL_SECONDS,
) -> bool:
    """Bounded startup gate for the local futures status API.

    systemd can report futures-bot active before uvicorn has bound port 8000.
    Waiting here avoids manufacturing a false CRITICAL/BLOCKED first tick after
    a sanctioned reboot/deploy. This never masks a persistent outage: once the
    grace window expires, the normal tick runs and the existing fail-closed
    status_api_unreachable finding is emitted unchanged.
    """
    deadline = time.monotonic() + max(0.0, float(grace_seconds))
    while True:
        payload, _err = http_get_json("/health", timeout=2)
        if payload is not None and payload.get("ok") is True:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(max(0.01, min(float(poll_seconds), deadline - time.monotonic())))


def sleep_until_next_slot() -> None:
    now = time.time()
    slot = (int(now) // TICK_SECONDS + 1) * TICK_SECONDS + TICK_OFFSET
    time.sleep(max(1, slot - now))


def main(argv: list[str]) -> int:
    if DEPLOY_PINS is None or RELEASE_DIR is None:
        sys.stderr.write(
            "refusing to start: cannot establish what the box should be running.\n"
            f"  {RELEASE_ERROR}\n"
            "A watcher that cannot state its expectation would report OK for a box\n"
            "it never verified, which reads as coverage. Fix the pins or the release\n"
            "link, then start it again.\n"
        )
        return 4
    static_selfcheck()
    _ensure_dirs()
    lock_fd = os.open(STATE_DIR / "lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        sys.stderr.write("another watcher holds the lock — exiting\n")
        return 3
    state = load_state()
    log(f"watcher start pid={os.getpid()} release={RELEASE_SHA[:12]} epoch={iso(EPOCH)} interim_at={iso(INTERIM_AT)} cadence={TICK_SECONDS}s state={STATE_DIR}")
    if "--once" not in argv:
        if wait_for_status_api_ready():
            log("startup readiness: futures status API /health is ready")
        else:
            log(
                f"WARN startup readiness: futures status API did not become ready "
                f"within {STARTUP_API_GRACE_SECONDS}s; first tick will enforce normal fail-closed checks"
            )
    if "--once" in argv:
        t = tick_once(state)
        print(json.dumps({"verdict": t["verdict"], "blocked": t["open_blockers"], "findings": [(x["level"], x["key"], x["summary"]) for x in t["findings"]]}, indent=1))
        return 0
    while True:
        try:
            tick_once(state)
        except Exception as exc:  # noqa: BLE001
            log(f"ERROR tick failed: {type(exc).__name__}: {exc}\n{traceback.format_exc()[-1200:]}")
        sleep_until_next_slot()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
