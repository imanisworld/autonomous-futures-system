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

# ── fixed facts ──────────────────────────────────────────────────────────────
RELEASE_SHA = "b3d72f87d53409289cbd2f1499eda6baa7737b47"
EPOCH = datetime(2026, 9, 2, 1, 15, 19, tzinfo=timezone.utc)
INTERIM_AT = EPOCH + timedelta(days=14)          # 2026-09-16T01:15:19Z
RELEASE_LINK = Path("/root/autonomous-futures-system")
RELEASE_DIR = Path(f"/root/afs-releases/{RELEASE_SHA}")
SHARED = Path("/root/afs-shared")
LOG_DIR = SHARED / "logs"
ENV_FILE = SHARED / ".env"
CAMPAIGN_ID = "forward_ab_2026_08_v1"
CAMPAIGN_JSONL = LOG_DIR / f"{CAMPAIGN_ID}.jsonl"
CAMPAIGN_STATE = LOG_DIR / f"{CAMPAIGN_ID}_state.json"
FEED_STATE = LOG_DIR / "feed_gap_alarm_state.json"
CAMPAIGN_CONFIG = RELEASE_DIR / "config" / "forward_evidence_campaign.json"
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
    if exe not in READ_ONLY_COMMANDS and not cmd[0].startswith(str(RELEASE_DIR / ".venv")):
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
    body = json.dumps({"content": f"{NOTIFY_PREFIX} {text}"[:1900]}).encode("utf-8")
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
def check_runtime(state: dict, f: Findings, tick: dict) -> None:
    rt: dict = {}
    tick["runtime"] = rt
    try:
        rt["release_link"] = os.path.realpath(RELEASE_LINK)
    except Exception as exc:  # noqa: BLE001
        rt["release_link"] = None
        f.add("BLOCKED", "release_link_unreadable", f"cannot resolve {RELEASE_LINK}: {exc}")
    if rt.get("release_link") and rt["release_link"] != str(RELEASE_DIR):
        f.add("BLOCKED", "unexpected_deploy", f"release symlink now → {rt['release_link']} (expected {RELEASE_SHA[:7]})")

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
    if base is None and props.get("ActiveState") == "active":
        state["baseline"] = {"ActiveEnterTimestamp": props.get("ActiveEnterTimestamp"), "NRestarts": props.get("NRestarts"),
                             "ExecMainPID": pid, "recorded_utc": iso(now_utc())}
        log(f"baseline recorded: {state['baseline']}")
    elif base:
        if props.get("ActiveEnterTimestamp") != base["ActiveEnterTimestamp"] or pid != base["ExecMainPID"]:
            f.add("BLOCKED", "unexpected_restart",
                  f"{SERVICE} restarted: ActiveEnter {base['ActiveEnterTimestamp']} → {props.get('ActiveEnterTimestamp')}, pid {base['ExecMainPID']} → {pid}")
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
    procs = [l for l in out.splitlines() if l.strip()]
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
        jp = state.get("journal_progress") or {}
        alerts_now = sum(posts.values())
        if jp.get("path") != str(newest) or st.st_size != jp.get("size") or st.st_mtime != jp.get("mtime"):
            jp = {"path": str(newest), "size": st.st_size, "mtime": st.st_mtime,
                  "last_advanced_utc": iso(now_utc()), "alerts_since_advance": 0}
        jp["alerts_since_advance"] = int(jp.get("alerts_since_advance") or 0) + alerts_now
        state["journal_progress"] = jp
        last_adv = _ts(jp.get("last_advanced_utc"))
        stalled_min = (now_utc() - last_adv).total_seconds() / 60 if last_adv else 0.0
        rt["journal_stalled_min"] = round(stalled_min, 1)
        if jp["alerts_since_advance"] > 0 and stalled_min > JOURNAL_STALL_MIN:
            f.add("BLOCKED", "journal_not_advancing", f"{jp['alerts_since_advance']} alerts received but {newest.name} unchanged for {stalled_min:.0f} min (size {st.st_size})")
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
    bad_sha = []
    for r in post:
        s = str(r.get("generating_git_sha") or "")
        if not s or not r.get("provenance_status"):
            bad_sha.append((r.get("candidate_id"), r.get("record_type"), "MISSING"))
        elif not RELEASE_SHA.startswith(s) and not s.startswith(RELEASE_SHA[:12]):
            bad_sha.append((r.get("candidate_id"), r.get("record_type"), s))
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
        if (state.get("memory_warning") or {}).get("active"):
            log("memory WARNING cleared after derived headroom returned healthy")
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
        f"WARNING memory_warning: {warning['summary']} | diagnostics: {snap}",
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
               f"WARNING {key}: {warning['summary']} | smallest fix: {smallest_fix(key)} | diagnostics: {snap}",
               f"memory-fixed-warning:{key}:{iso(now_utc())[:13]}")


def handle_blocked(state: dict, findings: Findings, tick: dict) -> None:
    blocked = findings.blocked()
    current = {b["key"]: b for b in blocked}
    new_keys = [k for k in current if k not in state["blocked"]]
    cleared = [k for k in state["blocked"] if k not in current]
    for k in cleared:
        log(f"BLOCKED cleared: {k}")
        state["blocked"].pop(k, None)
        state["blocked_last_notified"].pop(k, None)
        state["notified"].pop(f"blocked:{k}", None)
    if not blocked:
        return
    snap = None
    if new_keys:
        snap = capture_snapshot("BLOCKED_" + "_".join(new_keys)[:40], tick, findings)
    for k, b in current.items():
        if k not in state["blocked"]:
            state["blocked"][k] = {"first_utc": iso(now_utc()), "summary": b["summary"], "snapshot": str(snap) if snap else None}
            state_append(EVENTS_FILE, json.dumps({"utc": iso(now_utc()), "kind": "BLOCKED", "key": k, "summary": b["summary"], "detail": b["detail"], "snapshot": str(snap) if snap else None}, sort_keys=True, default=str) + "\n")
            log(f"BLOCKED {k}: {b['summary']} (snapshot {snap})")
        last = state["blocked_last_notified"].get(k)
        remind = (not last) or (now_utc() - _ts(last)).total_seconds() >= BLOCKED_REMINDER_S
        if remind:
            state["notified"].pop(f"blocked:{k}", None)
            notify(state, "DISCORD_ROUTE_ERROR",
                   f"BLOCKED {k}: {b['summary']} | smallest fix: {smallest_fix(k)} | snapshot: {state['blocked'][k]['snapshot']}",
                   f"blocked:{k}")
            state["blocked_last_notified"][k] = iso(now_utc())


def smallest_fix(key: str) -> str:
    m = {
        "unexpected_deploy": "operator: confirm the deploy was intended; re-arm the watcher on the new release (no auto-fix)",
        "service_not_active": "operator: inspect `journalctl -u futures-bot`; restart only by operator decision",
        "unexpected_restart": "operator: confirm who restarted futures-bot; re-baseline the watcher if intended",
        "service_crash_restart": "operator: read the crash traceback in the snapshot before any restart",
        "deploy_candidate_running": "operator: stop the leftover afs-candidate unit (systemctl, operator-run) after confirming it is a stale verifier",
        "webhook_process_count": "operator: identify the extra/missing uvicorn process in the snapshot",
        "feed_alarm_stale": "operator: check the feed-gap cron on the box",
        "alert_non200": "operator: read the rejected alert lines in the snapshot (secret/rate-limit/payload)",
        "service_traceback": "operator: read the traceback in the snapshot; no fix is applied automatically",
        "evidence_write_error": "operator: check disk/permissions on /root/afs-shared/logs; see snapshot",
        "journal_not_advancing": "operator: alerts arrive but the journal does not grow — check the service log in the snapshot",
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
    notify(state, "DISCORD_ROUTE_ERROR" if disc else "DISCORD_ROUTE_DAILY_REPORT", summary + f" | file: {DAILY_DIR / (key + '.json')}", f"daily:{key}")


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
    notify(state, "DISCORD_ROUTE_DAILY_REPORT", f"INTERIM EVIDENCE AUDIT (2 weeks post-epoch, not a gate) written: {path} | " + "; ".join(f"{k}: {v['candidates']}c/{v['resolved_filled_economic']}f/{v['distinct_trading_days']}d" for k, v in pops.items()), "interim")


# ── one tick ─────────────────────────────────────────────────────────────────
def tick_once(state: dict) -> dict:
    f = Findings()
    tick: dict = {"utc": iso(now_utc()), "tick": state["ticks"] + 1}
    for name, fn in (("runtime", check_runtime), ("memory", check_memory), ("memory_fixed", check_memory_fixed), ("storage", check_storage), ("campaign", check_campaign), ("failed_reclaim", check_failed_reclaim)):
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
        f"post_epoch_rows={tick.get('campaign', {}).get('rows_after_epoch')} {arm} blocked={tick['open_blockers']} warns={[w['key'] for w in f.warns()]}")
    return tick


def sleep_until_next_slot() -> None:
    now = time.time()
    slot = (int(now) // TICK_SECONDS + 1) * TICK_SECONDS + TICK_OFFSET
    time.sleep(max(1, slot - now))


def main(argv: list[str]) -> int:
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
