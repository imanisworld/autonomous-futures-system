#!/usr/bin/env python3
"""Pytest port of the options-audit mock harness.

The original test_mock.py checks are preserved (renamed check_* so pytest does
not collect them). Sample transcripts are not written into the repo. Scripts
are loaded from ops/audit/options and only their ROOT line is patched.
"""
import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
HERE = os.path.join(_REPO, "ops", "audit", "options")
SHIM = os.path.join(_REPO, "tests", "fixtures", "options_audit", "scripts_feed_gap_alarm_shim.py")
SCRIPTS = ("scanner_alerts_today.py", "scanner_suppression_detail.py",
           "scanner_signa_summary.py", "scanner_script_drift.py")
DB_SCRIPTS = SCRIPTS[:3]
UTC = timezone.utc
FIXED_NOW = "2026-09-24T20:05:00+00:00"          # 16:05 ET
SENT = "SENTINEL_SECRET_"                         # must never appear in output
DAY_START = datetime(2026, 9, 24, 4, 0, tzinfo=UTC)
DAY_END = datetime(2026, 9, 25, 4, 0, tzinfo=UTC)
PY = [sys.executable, "-I", "-B"]

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print("%s %s%s" % ("PASS" if cond else "FAIL", name, (" -- " + detail) if detail and not cond else ""))


# --------------------------------------------------------------------------
# repo schemas (verbatim CREATE statements from the repo @47ae01ac)
# --------------------------------------------------------------------------
SCHEMA_STORAGE = [
    """CREATE TABLE IF NOT EXISTS scans (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    source TEXT NOT NULL,
                    ticker TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    score INTEGER NOT NULL,
                    pattern TEXT NOT NULL,
                    components_json TEXT NOT NULL,
                    raw_json TEXT NOT NULL,
                    alert_sent INTEGER NOT NULL,
                    alert_suppression_reason TEXT NOT NULL
                )""",
    "CREATE INDEX IF NOT EXISTS idx_scans_alert_key ON scans (ticker, direction, pattern, timestamp)",
    """CREATE TABLE IF NOT EXISTS options_shadow_journal (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    scan_id INTEGER NOT NULL,
                    ticker TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    score INTEGER NOT NULL,
                    pattern TEXT NOT NULL,
                    status TEXT NOT NULL,
                    setup_inputs_json TEXT NOT NULL,
                    provider_snapshot_json TEXT NOT NULL,
                    selected_contract_json TEXT NOT NULL,
                    outcome_json TEXT NOT NULL
                )""",
    "CREATE INDEX IF NOT EXISTS idx_options_shadow_journal_scan ON options_shadow_journal (scan_id, ticker, timestamp)",
    """CREATE TABLE IF NOT EXISTS options_episode_blocks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticker TEXT NOT NULL,
                    episode_key TEXT NOT NULL UNIQUE,
                    reason TEXT NOT NULL,
                    blocked_at TEXT NOT NULL,
                    scan_id INTEGER
                )""",
    """CREATE TABLE IF NOT EXISTS options_selector_evidence (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    ticker TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    decision_ts TEXT NOT NULL,
                    status TEXT NOT NULL,
                    reason_code TEXT NOT NULL,
                    selector_rule_sha256 TEXT NOT NULL,
                    selector_input_sha256 TEXT NOT NULL,
                    evidence_sha256 TEXT NOT NULL,
                    evidence_json TEXT NOT NULL
                )""",
    "CREATE INDEX IF NOT EXISTS idx_options_selector_evidence_decision ON options_selector_evidence (ticker, decision_ts, id)",
]
SCHEMA_SIGNA = [
    """CREATE TABLE IF NOT EXISTS options_signa_context (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    ticker TEXT,
                    source TEXT NOT NULL,
                    endpoint TEXT,
                    direction TEXT,
                    status TEXT NOT NULL,
                    candidate_key TEXT NOT NULL UNIQUE,
                    observation_only INTEGER NOT NULL,
                    trade_authority INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    timeframe TEXT,
                    data_as_of TEXT,
                    provider_timestamp TEXT,
                    consumers_json TEXT NOT NULL DEFAULT '[]'
                )""",
    """CREATE TABLE IF NOT EXISTS signa_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    snapshot_id TEXT NOT NULL UNIQUE,
                    source TEXT NOT NULL,
                    endpoint TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    params_hash TEXT NOT NULL,
                    snapshot_bucket TEXT NOT NULL,
                    retrieved_at TEXT NOT NULL,
                    data_as_of TEXT,
                    status TEXT NOT NULL,
                    http_status INTEGER,
                    payload_sha256 TEXT NOT NULL,
                    observation_only INTEGER NOT NULL,
                    trade_authority INTEGER NOT NULL,
                    payload_json TEXT NOT NULL
                )""",
]
# Column lists as read from alert_ranker/contract_marks.py and v1_diagnostics.py
# (the audit scripts only need these tables to exist).
SCHEMA_OTHER = [
    """CREATE TABLE IF NOT EXISTS options_contract_marks (
        id INTEGER PRIMARY KEY AUTOINCREMENT, shadow_id INTEGER NOT NULL, timestamp TEXT NOT NULL,
        option_symbol TEXT NOT NULL, bid REAL, ask REAL, mid REAL, volume INTEGER, open_interest INTEGER,
        delta REAL, gamma REAL, theta REAL, implied_volatility REAL, quote_timestamp TEXT,
        error TEXT NOT NULL DEFAULT '', raw_json TEXT NOT NULL DEFAULT '{}')""",
    """CREATE TABLE IF NOT EXISTS options_v1_diagnostic_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT, shadow_id INTEGER NOT NULL, timestamp TEXT NOT NULL,
        event TEXT NOT NULL, underlying_price REAL, setup_entry_trigger REAL, option_bid REAL,
        option_ask REAL, option_mid REAL, quote_timestamp TEXT, delta REAL, gamma REAL, theta REAL,
        implied_volatility REAL, error TEXT NOT NULL DEFAULT '', setup_type TEXT, setup_timeframe TEXT,
        paper_evidence_lane TEXT NOT NULL DEFAULT 'ACTIVE', raw_json TEXT NOT NULL DEFAULT '{}',
        UNIQUE(shadow_id, timestamp, event))""",
]


# --------------------------------------------------------------------------
# synthetic data
# --------------------------------------------------------------------------
WATCH = ("AAPL", "NVDA", "SPY", "TSLA", "XOM")


def et(h, m, s=0, day=24, micro=0):
    """UTC datetime for 2026-09-<day> h:m ET (EDT = UTC-4)."""
    return datetime(2026, 9, day, h, m, s, micro, tzinfo=UTC) + timedelta(hours=4)


def iso(dt):
    return dt.astimezone(UTC).isoformat()


class Gen:
    def __init__(self):
        self.scans = []          # dicts inserted in order; id = index+1
        self.evidence = []
        self.last_fetch = {}

    def add(self, ts, source, ticker, direction, pattern, reason, sent, raw=None, comp=None, score=70):
        row = dict(ts=ts, source=source, ticker=ticker, direction=direction, pattern=pattern,
                   reason=reason, sent=sent, raw=raw or {}, comp=comp if comp is not None else {"signa": 0, "pattern": 20},
                   score=score)
        self.scans.append(row)
        return len(self.scans)

    def evid(self, ts, ticker, direction, doc):
        self.evidence.append((ts, ticker, direction, doc))
        return len(self.evidence)

    def signa(self, ticker, t):
        """Signa raw fields for a scheduled RTH row (15-min client TTL model)."""
        raw = {"signa_symbol": "XO" if ticker == "XOM" else ticker,
               "signa_raw_payload": {"api_key": SENT + "apikey", "url": "https://signa.example/x?token=" + SENT}}
        et_t = t - timedelta(hours=4)
        hm = (et_t.hour, et_t.minute)
        if ticker in ("NVDA", "TSLA") and (10, 30) <= hm <= (10, 40):
            raw.update(signa_error="http_429", signa_client_cached=False, signa_cached=False, signa_retrieved_at=None)
            return raw
        if (10, 45) <= hm <= (11, 10):
            raw.update(signa_error="account_backoff_active", signa_client_cached=False, signa_cached=False,
                       signa_retrieved_at=None)
            return raw
        if ticker == "SPY" and hm == (15, 0):
            raw.update(signa_error="missing_api_key", signa_client_cached=False, signa_retrieved_at=None)
            return raw
        last = self.last_fetch.get(ticker)
        if last is None or (t - last) >= timedelta(minutes=15):
            self.last_fetch[ticker] = t + timedelta(seconds=2)
            raw.update(signa_client_cached=False, signa_cached=(ticker == "SPY"), signa_error=None)
        else:
            raw.update(signa_client_cached=True, signa_cached=False, signa_error=None)
        raw["signa_retrieved_at"] = iso(self.last_fetch[ticker])
        return raw


def build_data():
    g = Gen()
    common = {"discord_webhook_url": "https://discord.com/api/webhooks/123/" + SENT + "hook",
              "market_data_raw": {"access_token": SENT + "md"}, "market_data_error": None,
              "market_data_stale": False}
    # history (sends since 2026-09-08 ET)
    for ts, pat in (("2026-09-08T03:30:00+00:00", "hist_excl_0907"), ("2026-09-08T04:30:00+00:00", "hist_0908"),
                    ("2026-09-15T15:00:00+00:00", "hist_0915a"), ("2026-09-15T16:00:00+00:00", "hist_0915b"),
                    ("2026-09-23T14:00:00+00:00", "hist_0923a"), ("2026-09-23T15:00:00+00:00", "hist_0923b"),
                    ("2026-09-24T02:00:00+00:00", "hist_0923c_2200ET")):
        g.add(ts, "scheduled", "AAPL", "long", pat, "", 1, dict(common))
    # boundary / format rows
    for ts, pat in (("2026-09-24T03:59:59.999999+00:00", "bnd_excl_before"),
                    ("2026-09-24T04:00:00+00:00", "bnd_incl_start"),
                    ("2026-09-24T01:00:00-03:00", "bnd_incl_offset"),
                    ("2026-09-24T23:59:59Z", "bnd_incl_zulu"),
                    ("2026-09-25T03:59:59.999999+00:00", "bnd_incl_end"),
                    ("2026-09-25T04:00:00+00:00", "bnd_excl_after"),
                    ("2026-09-24 not-a-timestamp", "bnd_unparseable")):
        g.add(ts, "webhook", "BNDRY", "long", pat, "", 1, dict(common))
    # pre-market scheduled ticks
    for (h, m), reason in (((8, 0), "session_not_started"), ((8, 30), "no_session_bars")):
        t = et(h, m, 5, micro=714371)
        for tk in WATCH:
            g.add(iso(t), "scheduled", tk, "long", "none", reason, 0,
                  dict(common, signa_symbol="XO" if tk == "XOM" else tk))
    # RTH ticks 09:30..15:55 every 5 min, 11:00 missing entirely, TSLA missing at 10:15
    t = et(9, 30, 3, micro=123456)
    last_aapl_send = None
    riskcap_toggle = 0
    while t <= et(15, 55, 3, micro=123456):
        e = t - timedelta(hours=4)
        hm = (e.hour, e.minute)
        if hm == (11, 0):
            t += timedelta(minutes=5)
            continue
        for tk in WATCH:
            if tk == "TSLA" and hm == (10, 15):
                continue
            raw = dict(common)
            raw.update(g.signa(tk, t))
            raw.update(paper_evidence_lane="ACTIVE", setup_status="setup_ready", status="ready")
            comp = {"signa": 0, "pattern": 20, "trend": 10}
            pattern, direction, reason, sent = "none", "long", "counterfactual_observer_only", 0
            if tk == "AAPL":
                pattern = "2-1-2 bullish"
                if (9, 40) <= hm <= (12, 0):
                    if last_aapl_send and (t - last_aapl_send) <= timedelta(minutes=30):
                        reason = "duplicate_30m"
                    elif hm == (11, 25):
                        reason = "discord_error"
                    else:
                        reason, sent, last_aapl_send = "", 1, t
                    raw.update(contract="AAPL 2026-10-02 C 255", paper_policy_status="OPEN_ACTIVE")
                if hm == (13, 0):
                    comp["signa"] = 2          # must be flagged: components.signa != 0
            elif tk == "NVDA":
                direction, pattern = "short", "2-2 bearish"
                reason = "discord_not_configured" if hm == (9, 35) else "no_setup:sequence_not_212"
                if hm == (13, 0):
                    del comp["signa"]
                if hm == (14, 30):
                    raw.pop("paper_evidence_lane")
                    raw["lane"] = "COUNTERFACTUAL"     # plain 'lane' key fallback
            elif tk == "SPY":
                if hm == (14, 0):
                    reason = ""                        # anomaly: empty reason but not sent
            elif tk == "TSLA":
                pattern = "3-1-2 bullish"
                if (10, 0) <= hm <= (10, 30):
                    amount = ("1250.00", "1387.50")[riskcap_toggle % 2]
                    riskcap_toggle += 1
                    ask = float(amount) / 25.0
                    eid = g.evid(iso(t), "TSLA", "long", {
                        "ticker": "TSLA", "production_direction": "long", "decision_ts": iso(t),
                        "status": "DATA_BLOCKED", "reason_code": "planned_risk_outside_v1_cap",
                        "chosen_expiration": "2026-10-02",
                        "production_selection": {"status": "SELECTED", "reason": "selected",
                                                 "contract": {"symbol": "TSLA261002C00255000", "ask": ask,
                                                              "bid": ask - 0.4, "mid": ask - 0.2, "delta": 0.55,
                                                              "strike": 255.0, "spread_percent": 0.8}},
                        "provider_auth": {"bearer": SENT + "evidence"}})
                    raw["selector_evidence_id"] = eid
                    reason = "DATA_INVALID:planned_risk_outside_v1_cap:" + amount
                elif hm == (13, 0):
                    raw.update(contract="TSLA 2026-10-09 C 260", option_ask=55.5, option_bid=54.9,
                               option_mark=55.2, delta=0.51, dte=15, expiry="2026-10-09", strike=260.0)
                    reason = "DATA_INVALID:planned_risk_outside_v1_cap:1387.50"
                elif hm == (14, 30):
                    reason = "DATA_INVALID:aggregate_risk_cap_exceeded:1210.00"
                    raw["aggregate_open_planned_risk_before"] = 950.0
                else:
                    reason = "setup_forming"
            elif tk == "XOM":
                direction, pattern = "short", "2-1-2 bearish"
                if (9, 30) <= hm <= (9, 50):
                    eid = g.evid(iso(t), "XOM", "short", {
                        "ticker": "XOM", "production_direction": "short", "decision_ts": iso(t),
                        "status": "DATA_BLOCKED", "reason_code": "no_liquid_contract",
                        "chosen_expiration": "2026-10-16",
                        "production_selection": {"status": "NO_CONTRACT", "reason": "spread_too_wide"}})
                    raw["selector_evidence_id"] = eid
                    reason = "DATA_INVALID:no_liquid_contract[spread_pct:35.0,oi:12]"
                elif hm == (10, 0):
                    reason = "ENTRY_LATE:episode_blocked_after_entry_late"
                elif hm == (10, 5):
                    reason = "entry_late_counterfactual_only:remaining_rr_0.8_below_1.5"
                elif hm == (10, 10):
                    reason = "entry_late_counterfactual_only:remaining_rr_0.6_below_1.5"
                else:
                    reason = "setup_proof_incomplete:daily_targets"
            g.add(iso(t), "scheduled", tk, direction, pattern, reason, sent, raw, comp)
        if hm == (10, 0):      # daily-timeframe rows share the cycle's `now`
            for tk in ("AAPL", "XOM"):
                g.add(iso(t), "scheduled:daily", tk, "long", "daily_32", "setup_forming:daily_32", 0,
                      dict(common, signa_symbol="XO" if tk == "XOM" else tk))
        t += timedelta(minutes=5)
    # webhook-sourced send
    g.add(iso(et(12, 34, 56)), "webhook", "NVDA", "short", "2-2 bearish", "", 1,
          dict(common, signa_symbol="NVDA", signa_client_cached=True, signa_retrieved_at=iso(et(12, 30, 5))))
    return g


def _order_key(ts, seq):
    """Sort key so AUTOINCREMENT ids follow parsed time. Unparseable sorts last."""
    text = "" if ts is None else str(ts).strip()
    parse = text[:-1] + "+00:00" if text[-1:] in "Zz" else text
    try:
        stamp = datetime.fromisoformat(parse) if parse else None
    except ValueError:
        stamp = None
    if stamp is None:
        stamp = datetime.max.replace(tzinfo=UTC)
    elif stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    else:
        stamp = stamp.astimezone(UTC)
    return (stamp, seq)


def create_db(path, g, nullable_reason=False, extra_null_rows=False):
    conn = sqlite3.connect(path)
    for sql in SCHEMA_STORAGE + SCHEMA_SIGNA + SCHEMA_OTHER:
        if nullable_reason and "CREATE TABLE IF NOT EXISTS scans" in sql:
            sql = sql.replace("alert_suppression_reason TEXT NOT NULL", "alert_suppression_reason TEXT")
        conn.execute(sql)
    scans = list(g.scans)
    if extra_null_rows:
        # Merged before the id assignment so a NULL-reason row is not appended
        # after a later timestamp (that would be an id/timestamp inversion).
        for ts, tk, sent in ((iso(et(15, 10, 1)), "NVDA", 1), (iso(et(15, 20, 1)), "SPY", 0)):
            scans.append(dict(ts=ts, source="webhook", ticker=tk, direction="long", pattern="null_reason_case",
                              reason=None, sent=sent, raw={}, comp={"signa": 0}, score=60))
    scans = [row for _, row in sorted(enumerate(scans), key=lambda item: _order_key(item[1]["ts"], item[0]))]
    for r in scans:
        comp = json.dumps(r["comp"], sort_keys=True)
        raw = json.dumps(r["raw"], sort_keys=True, default=str)
        if r["reason"] is None:
            conn.execute("INSERT INTO scans (timestamp, source, ticker, direction, score, pattern, components_json, "
                         "raw_json, alert_sent, alert_suppression_reason) VALUES (?,?,?,?,?,?,?,?,?,NULL)",
                         (r["ts"], r["source"], r["ticker"], r["direction"], r["score"], r["pattern"],
                          comp, raw, r["sent"]))
        else:
            conn.execute("INSERT INTO scans (timestamp, source, ticker, direction, score, pattern, components_json, "
                         "raw_json, alert_sent, alert_suppression_reason) VALUES (?,?,?,?,?,?,?,?,?,?)",
                         (r["ts"], r["source"], r["ticker"], r["direction"], r["score"], r["pattern"],
                          comp, raw, r["sent"], r["reason"]))
    for ts, tk, direction, doc in g.evidence:
        conn.execute("INSERT INTO options_selector_evidence (timestamp, ticker, direction, decision_ts, status, "
                     "reason_code, selector_rule_sha256, selector_input_sha256, evidence_sha256, evidence_json) "
                     "VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (ts, tk, direction, doc["decision_ts"], doc["status"], doc["reason_code"], "r" * 64, "i" * 64,
                      "e" * 64, json.dumps(doc, sort_keys=True, separators=(",", ":"))))
    journal = [
        (iso(et(10, 20, 3)), 101, "AAPL", "long", "OPEN",
         {"paper_policy_id": "OPTIONS_PAPER_V1", "paper_evidence_lane": "ACTIVE", "contract": "AAPL 2026-10-02 C 255",
          "planned_risk_dollars": 237.5, "option_mark": 9.5, "dte": 8, "expiry": "2026-10-02",
          "setup_type": "2-1-2", "setup_timeframe": "15m", "risk_budget_consumed": True}),
        (iso(et(10, 5, 3)), 102, "XOM", "short", "OPEN",
         {"paper_policy_id": "OPTIONS_PAPER_V1", "paper_evidence_lane": "COUNTERFACTUAL",
          "contract": "XOM 2026-10-16 P 110", "planned_risk_dollars": 180.0, "option_mark": 7.2,
          "risk_budget_consumed": False, "expiry": "2026-10-16", "dte": 22}),
        (iso(et(14, 0, 0, day=23)), 90, "NVDA", "short", "OPEN",
         {"paper_policy_id": "OPTIONS_PAPER_V1", "paper_evidence_lane": "ACTIVE", "contract": "NVDA 2026-10-02 P 170",
          "planned_risk_dollars": 410.0, "option_mark": 16.4}),
        (iso(et(11, 0, 0)), 103, "SPY", "long", "WIN", {"paper_evidence_lane": "ACTIVE"}),
        (iso(et(12, 0, 0)), 104, "TSLA", "long", "REJECTED", {}),
    ]
    journal = [row for _, row in sorted(enumerate(journal), key=lambda item: _order_key(item[1][0], item[0]))]
    for ts, scan_id, tk, direction, status, sel in journal:
        conn.execute("INSERT INTO options_shadow_journal (timestamp, scan_id, ticker, direction, score, pattern, "
                     "status, setup_inputs_json, provider_snapshot_json, selected_contract_json, outcome_json) "
                     "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                     (ts, scan_id, tk, direction, 70, "2-1-2", status,
                      json.dumps({"setup_type": "2-1-2", "setup_timeframe": "15m"}, sort_keys=True),
                      json.dumps({"api_key": SENT + "provider"}, sort_keys=True),
                      json.dumps(sel, sort_keys=True), "{}"))
    contexts = [
        (iso(et(9, 31)), "AAPL", "scan", "SIGNA_CONTEXT", {"http_status": 200, "request_ok": True, "cached": False}),
        (iso(et(9, 46)), "AAPL", "options_flow", "SIGNA_CONTEXT", {"http_status": 200, "request_ok": True, "cached": True}),
        (iso(et(10, 31)), "NVDA", "action_card", "SIGNA_ERROR", {"error": "http_429", "http_status": 429, "request_ok": False}),
        (iso(et(10, 50)), "TSLA", "scan", "SIGNA_ERROR", {"error": "account_backoff_active", "backoff_active": True,
                                                          "request_ok": False}),
        (iso(et(15, 0, day=23)), "SPY", "scan", "SIGNA_CONTEXT", {"http_status": 200, "request_ok": True}),
    ]
    contexts = [row for _, row in sorted(enumerate(contexts), key=lambda item: _order_key(item[1][0], item[0]))]
    for i, (ts, tk, source, status, payload) in enumerate(contexts):
        payload = dict(payload, ticker=tk, source=source, status=status, token=SENT + "ctx")
        conn.execute("INSERT INTO options_signa_context (timestamp, ticker, source, endpoint, direction, status, "
                     "candidate_key, observation_only, trade_authority, payload_json) VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (ts, tk, source, "v1/" + source, None, status, "ck%d" % i, 1, 0,
                      json.dumps(payload, sort_keys=True, separators=(",", ":"))))
    snaps = [("2026-09-24T13:31:02Z", "AAPL", "OK", 200), ("2026-09-24T14:31:00Z", "NVDA", "ERROR", 429),
             ("2026-09-24T14:46:00Z", "XO", "OK", 200), ("2026-09-23T19:00:00Z", "SPY", "OK", 200)]
    snaps = [row for _, row in sorted(enumerate(snaps), key=lambda item: _order_key(item[1][0], item[0]))]
    for i, (ts, sym, status, http) in enumerate(snaps):
        conn.execute("INSERT INTO signa_snapshots (snapshot_id, source, endpoint, symbol, timeframe, params_hash, "
                     "snapshot_bucket, retrieved_at, data_as_of, status, http_status, payload_sha256, "
                     "observation_only, trade_authority, payload_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     ("signa_%d" % i, "signa", "scan", sym, "15m", "p" * 16, ts, ts, None, status, http, "s" * 64,
                      1, 0, json.dumps({"key": SENT + "snap"})))
    conn.execute("INSERT INTO options_episode_blocks (ticker, episode_key, reason, blocked_at, scan_id) "
                 "VALUES ('XOM','ep1','ENTRY_LATE',?,1)", (iso(et(10, 0)),))
    conn.execute("INSERT INTO options_contract_marks (shadow_id, timestamp, option_symbol) VALUES (1, ?, 'AAPL')",
                 (iso(et(10, 30)),))
    conn.execute("INSERT INTO options_v1_diagnostic_snapshots (shadow_id, timestamp, event) VALUES (1, ?, 'entry')",
                 (iso(et(10, 20)),))
    conn.commit()
    conn.close()


def expected(g):
    def ts(r):
        try:
            d = datetime.fromisoformat(r["ts"].replace("Z", "+00:00"))
        except ValueError:
            return None
        return d.astimezone(UTC)
    today = [r for r in g.scans if ts(r) is not None and DAY_START <= ts(r) < DAY_END]
    since = datetime(2026, 9, 8, 4, 0, tzinfo=UTC)
    return {
        "rows_today": len(today),
        "sent_today": sum(r["sent"] for r in today),
        "dup_today": sum(1 for r in today if r["reason"] == "duplicate_30m"),
        "discord_today": sum(1 for r in today if r["reason"].startswith("discord")),
        "since": sum(r["sent"] for r in g.scans if ts(r) is not None and since <= ts(r) < DAY_END),
        "riskcap": sum(1 for r in today if "planned_risk_outside_v1_cap" in r["reason"]),
        "noliq": sum(1 for r in today if "no_liquid_contract" in r["reason"]),
        "xo_rows": sum(1 for r in today if r["raw"].get("signa_symbol") == "XO"),
        "e429": sum(1 for r in today if r["raw"].get("signa_error") == "http_429"),
        "aapl_sends": sorted(ts(r) for r in today if r["ticker"] == "AAPL" and r["sent"]),
    }


# --------------------------------------------------------------------------
# harness
# --------------------------------------------------------------------------
def patched_copy(name, root, dest_dir, extra=()):
    src = open(os.path.join(HERE, name)).read()
    line = 'ROOT = "/root"\n'
    check("%s: ROOT constant line occurs exactly once" % name, src.count(line) == 1)
    text = src.replace(line, 'ROOT = %r\n' % root)
    for old, new in extra:
        check("%s: patch target present (%s)" % (name, old.strip()), text.count(old) == 1)
        text = text.replace(old, new)
    os.makedirs(dest_dir, exist_ok=True)
    path = os.path.join(dest_dir, name)
    open(path, "w").write(text)
    return path


def run(path, now=FIXED_NOW, args=()):
    if now is None:
        cmd = PY + [path] + list(args)
    else:
        code = ("import importlib.util as u;from datetime import datetime as d;"
                "s=u.spec_from_file_location('audit_mod',%r);m=u.module_from_spec(s);s.loader.exec_module(m);"
                "m.run(lambda: m.main(now=d.fromisoformat(%r)))" % (path, now))
        cmd = PY + ["-c", code]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=120)
    return proc.returncode, proc.stdout, proc.stderr


def snapshot(directory):
    state = {}
    for name in sorted(os.listdir(directory)):
        p = os.path.join(directory, name)
        st = os.stat(p)
        state[name] = (st.st_size, st.st_mtime_ns, hashlib.sha256(open(p, "rb").read()).hexdigest()
                       if stat.S_ISREG(st.st_mode) and os.access(p, os.R_OK) else None)
    return state


def no_secrets(label, text):
    bad = [s for s in (SENT, "discord.com", "https://", "http://") if s in text]
    check("%s: no secrets/URLs in output" % label, not bad, "found %s" % bad)


def save(name, text):
    """Sample transcripts stay out of the repo. The original script wrote them."""
    return None


def grab(text, prefix):
    return [l for l in text.splitlines() if l.startswith(prefix)]


def kv(line, key):
    for part in line.split():
        if part.startswith(key + "="):
            return part[len(key) + 1:]
    return None


# --------------------------------------------------------------------------
# tests
# --------------------------------------------------------------------------
def check_static():
    blocks = []
    for name in SCRIPTS:
        src = open(os.path.join(HERE, name)).read()
        a = src.index("# ===== BEGIN COMMON BLOCK v1")
        b = src.index("# ===== END COMMON BLOCK v1 =====")
        blocks.append(src[a:b])
        # code only: skip the header docstring (everything before the ROOT constant) and comment lines
        body = "\n".join(l for l in src[src.index('ROOT = "/root"'):].splitlines()
                         if not l.strip().startswith("#"))
        for needle in ("os.environ", "getenv", "subprocess", "sys.argv", "urlopen", "socket", "tempfile",
                       "shutil", "mode=rw", "mode=rwc", ".write_text", "os.remove", "os.unlink", "chmod"):
            check("%s: does not use %s" % (name, needle), needle not in body)
        import re
        check("%s: no open() in write/append mode" % name,
              not re.search(r"open\([^)]*,\s*['\"][^'\"]*[wax+]", src))
        check("%s: every sqlite3.connect uses a mode=ro URI" % name,
              all("uri=True" in l for l in src.splitlines() if "sqlite3.connect(" in l))
        check("%s: no combined min(id), max(id) (would force a full-table scan)" % name,
              "min(id), max(id)" not in body)
        check("%s: has header docstring with READ-ONLY and FIELDS PRINTED" % name,
              "READ-ONLY GUARANTEES" in src[:4000] and "FIELDS PRINTED" in src[:4000])
    check("common block byte-identical in all four scripts", len(set(blocks)) == 1)


def check_dst(bin_dir, work):
    sys.path.insert(0, bin_dir)
    import importlib.util
    spec = importlib.util.spec_from_file_location("a_mod", os.path.join(bin_dir, "scanner_alerts_today.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for now, hours, start, end in (
            ("2026-03-08T17:00:00+00:00", 23.0, "2026-03-08T05:00:00+00:00", "2026-03-09T04:00:00+00:00"),
            ("2026-11-01T17:00:00+00:00", 25.0, "2026-11-01T04:00:00+00:00", "2026-11-02T05:00:00+00:00"),
            ("2026-09-24T03:30:00+00:00", 24.0, "2026-09-23T04:00:00+00:00", "2026-09-24T04:00:00+00:00"),
            ("2026-09-24T20:05:00+00:00", 24.0, "2026-09-24T04:00:00+00:00", "2026-09-25T04:00:00+00:00")):
        d, s, e = mod.et_day_bounds(datetime.fromisoformat(now))
        check("et_day_bounds(%s) = [%s, %s) %sh" % (now, start, end, hours),
              s.isoformat() == start and e.isoformat() == end and (e - s).total_seconds() / 3600 == hours,
              "%s %s %s" % (d, s, e))
    # end-to-end on DST days: rows just inside/outside the ET day
    for label, now, rows in (
            ("dst_nov", "2026-11-01T17:00:00+00:00",
             [("2026-11-01T03:59:59+00:00", "out_before"), ("2026-11-01T04:00:00+00:00", "in_start_edt"),
              ("2026-11-02T04:30:00+00:00", "in_2330_est"), ("2026-11-02T04:59:59.999999+00:00", "in_end_est"),
              ("2026-11-02T05:00:00+00:00", "out_after")]),
            ("dst_mar", "2026-03-08T17:00:00+00:00",
             [("2026-03-08T04:59:59+00:00", "out_before"), ("2026-03-08T05:00:00+00:00", "in_start_est"),
              ("2026-03-09T03:59:59+00:00", "in_end_edt"), ("2026-03-09T04:00:00+00:00", "out_after")])):
        root = os.path.join(work, label, "root")
        os.makedirs(root + "/afs-shared/logs")
        g = Gen()
        for ts, pat in rows:
            g.add(ts, "webhook", "DST", "long", pat, "", 1)
        create_db(root + "/afs-shared/logs/options_scanner.sqlite", g)
        path = patched_copy("scanner_alerts_today.py", root, os.path.join(work, label, "bin"))
        code, outp, err = run(path, now)
        pats = set(kv(l, "pattern") for l in grab(outp, "ALERT "))
        want = set(p for _, p in rows if p.startswith("in_"))
        check("%s: exit 0, exactly the in-day rows selected" % label, code == 0 and pats == want and not err,
              "code=%s pats=%s err=%s" % (code, pats, err[:200]))
        check("%s: header day_hours" % label, ("day_hours=%s" % ("25.0" if "nov" in label else "23.0")) in outp)
        save("dst_%s_scanner_alerts_today.txt" % label.split("_")[1], outp)
    while bin_dir in sys.path:
        sys.path.remove(bin_dir)


def check_main_runs(g, exp, root, bin_dir, variant):
    logs = root + "/afs-shared/logs"
    db = logs + "/options_scanner.sqlite"
    outputs = {}
    for name in DB_SCRIPTS:
        path = os.path.join(bin_dir, name)
        before = snapshot(logs)
        code, outp, err = run(path)
        after = snapshot(logs)
        outputs[name] = outp
        check("[%s] %s exit 0, empty stderr" % (variant, name), code == 0 and not err, "code=%s err=%s" % (code, err[:300]))
        check("[%s] %s ends with end_of_report exit=0" % (variant, name), outp.rstrip().endswith("end_of_report exit=0"))
        check("[%s] %s leaves DB dir byte-identical (names/size/mtime/sha256)" % (variant, name), before == after,
              "before=%s after=%s" % (sorted(before), sorted(after)))
        check("[%s] %s prints ET date and UTC bounds" % (variant, name),
              "et_date=2026-09-24 day_start_utc=2026-09-24T04:00:00+00:00 day_end_utc=2026-09-25T04:00:00+00:00" in outp)
        no_secrets("[%s] %s" % (variant, name), outp)
        check("[%s] %s under 4000 lines" % (variant, name), len(outp.splitlines()) < 4000)
        floors = [kv(l, "id_floor") for l in outp.splitlines() if l.startswith("SCANS_TODAY")]
        check("[%s] %s uses a rowid floor (no full-table scan)" % (variant, name),
              floors and all(f not in (None, "-") for f in floors), str(floors))
    return outputs


def assert_content(outputs, exp):
    a = outputs["scanner_alerts_today.py"]
    check("alerts: alerts_sent_today matches", "TOTAL alerts_sent_today=%d" % exp["sent_today"] in a)
    check("alerts: duplicate_30m_today matches", "TOTAL duplicate_30m_today=%d" % exp["dup_today"] in a)
    check("alerts: discord_errors_today matches (discord_error + discord_not_configured)",
          "TOTAL discord_errors_today=%d discord_error=1 discord_not_configured=1" % exp["discord_today"] in a)
    check("alerts: alerts_sent_since_2026-09-08 matches", "TOTAL alerts_sent_since_2026-09-08=%d " % exp["since"] in a)
    pats = set(kv(l, "pattern") for l in grab(a, "ALERT "))
    check("alerts: boundary rows included", {"bnd_incl_start", "bnd_incl_offset", "bnd_incl_zulu", "bnd_incl_end"} <= pats)
    check("alerts: boundary rows excluded", not ({"bnd_excl_before", "bnd_excl_after", "bnd_unparseable"} & pats))
    check("alerts: unparseable timestamp counted", "unparseable_ts=1" in a)
    check("alerts: future-dated send warned", "alerts_sent_with_future_et_date=1" in a)
    check("alerts: 09-07 ET send excluded from SENT_BY_DAY start", "SENT_BY_DAY et_date=2026-09-08 sent=1" in a)
    aapl = [l for l in grab(a, "GROUP ") if "ticker=AAPL" in l and "2-1-2_bullish" in l]
    gaps = [round((b - c).total_seconds() / 60, 1) for c, b in zip(exp["aapl_sends"], exp["aapl_sends"][1:])]
    check("alerts: AAPL re-alert group shows sends and 35-min gaps",
          len(aapl) == 1 and kv(aapl[0], "sends") == str(len(exp["aapl_sends"])) and "min_gap_min=35.0" in aapl[0],
          aapl[0] if aapl else "missing (gaps %s)" % gaps)
    check("alerts: empty-reason-but-unsent anomaly counted", "anomaly_empty_or_null_reason_but_not_sent=1" in a)
    opens = grab(a, "OPEN ")
    check("alerts: 3 OPEN journal rows incl. ACTIVE + COUNTERFACTUAL",
          len(opens) == 3 and any("lane=COUNTERFACTUAL" in l for l in opens) and any("lane=ACTIVE" in l for l in opens))
    check("alerts: active V1 planned-risk sum", "open_active_v1_planned_risk_sum=647.5" in a)
    check("alerts: source=webhook label not masked", "source=webhook sent=1" in a)
    check("alerts: contract/lane from raw_json shown", "contract=AAPL_2026-10-02_C_255" in a and "lane=ACTIVE" in a)

    s = outputs["scanner_suppression_detail.py"]
    rc = [l for l in grab(s, "REASON ") if "planned_risk_outside_v1_cap:*" in l]
    check("suppr: riskcap bucket aggregated with count", len(rc) == 1 and kv(rc[0], "count") == str(exp["riskcap"]),
          rc[0] if rc else "missing")
    check("suppr: individual riskcap amounts listed",
          "value=1250.00" in s and "value=1387.50" in s)
    check("suppr: RISKCAP rows = riskcap count", len(grab(s, "RISKCAP ")) == exp["riskcap"])
    check("suppr: contract detail recovered from selector evidence",
          "contract=TSLA261002C00255000" in s and "src=selector_evidence" in s)
    check("suppr: contract detail from raw_json + DTE", "contract=TSLA_2026-10-09_C_260" in s and "dte=15" in s)
    check("suppr: computed DTE from evidence expiry", "dte=8 dte_src=computed" in s)
    check("suppr: NOLIQ rows", len(grab(s, "NOLIQ ")) == exp["noliq"] and "sel_reason=spread_too_wide" in s)
    check("suppr: remaining_rr bucket with values", "remaining_rr_*" in s and "value=0.8_below_1.5" in s)
    check("suppr: watchlist derived", "WATCHLIST_DERIVED source=scheduled* symbols=AAPL,NVDA,SPY,TSLA,XOM" in s)
    check("suppr: TSLA missing tick flagged", any("missing=TSLA" in l for l in grab(s, "TICK_INCOMPLETE ")))
    check("suppr: 11:00 gap flagged", any("after_et=2026-09-24T10:55:03-04:00" in l for l in grab(s, "TICK_GAP ")))
    check("suppr: SYMBOL_TICKS min is TSLA", any(l.startswith("SYMBOL_TICKS_SUMMARY") and "min_symbols=TSLA" in l
                                                  for l in s.splitlines()))

    z = outputs["scanner_signa_summary.py"]
    check("signa: XOM->XO mismatch flagged", "SIGNA_MISMATCH ticker=XOM signa_symbol=XO rows=%d" % exp["xo_rows"] in z)
    check("signa: http_429 count", "signa_error_http_429=%d" % exp["e429"] in z)
    check("signa: missing_api_key label not masked", "value=missing_api_key" in z)
    check("signa: cached flags counted", "CLIENT_CACHED signa_client_cached true=" in z and "missing=" in z)
    check("signa: components not all zero detected", "components_signa_all_zero=NO" in z)
    check("signa: first RTH retrieval", "FIRST_RTH_SIGNA id=" in z and "time_et=2026-09-24T09:30:03-04:00" in z)
    check("signa: context + snapshot histograms", "SIGNA_CONTEXT_STATUS status=SIGNA_ERROR rows=2" in z
          and "SIGNA_SNAPSHOT_HTTP_STATUS http_status=429 rows=1" in z)
    check("signa: SIGNA_CALLS per symbol", len(grab(z, "SIGNA_CALLS ")) >= 5)


def check_null_variant(g, work):
    root = os.path.join(work, "nullvar", "root")
    os.makedirs(root + "/afs-shared/logs")
    create_db(root + "/afs-shared/logs/options_scanner.sqlite", g, nullable_reason=True, extra_null_rows=True)
    bind = os.path.join(work, "nullvar", "bin")
    for name in ("scanner_alerts_today.py", "scanner_suppression_detail.py"):
        code, outp, err = run(patched_copy(name, root, bind))
        check("[null-reason schema] %s exit 0" % name, code == 0 and not err, err[:200])
        if name == "scanner_alerts_today.py":
            check("[null-reason schema] NULL reasons counted/shown", "reason_null_today=2" in outp
                  and "reason=<null>" in outp and "anomaly_empty_or_null_reason_but_not_sent=2" in outp)
        else:
            check("[null-reason schema] <null> bucket", "bucket=<null>" in outp)
        save("nullreason_%s.txt" % name[:-3], outp)


def check_wal(g, work):
    root = os.path.join(work, "wal", "root")
    logs = root + "/afs-shared/logs"
    os.makedirs(logs)
    db = logs + "/options_scanner.sqlite"
    create_db(db, g)
    bind = os.path.join(work, "wal", "bin")
    paths = dict((n, patched_copy(n, root, bind)) for n in DB_SCRIPTS)
    # The late boundary rows sort after the RTH rows. Move them just before the
    # appended WAL row so id order stays non-decreasing and the row is still
    # inside the ET day (day ends 2026-09-25T04:00:00Z).
    fixer = sqlite3.connect(db)
    fixer.execute("UPDATE scans SET timestamp = ? WHERE pattern = 'bnd_incl_end'", ("2026-09-25T03:30:00+00:00",))
    fixer.execute("UPDATE scans SET timestamp = ? WHERE pattern = 'bnd_excl_after'", ("2026-09-25T03:40:00+00:00",))
    fixer.execute("UPDATE scans SET timestamp = ? WHERE pattern = 'bnd_unparseable'", ("2026-09-25T03:45:00+00:00",))
    fixer.commit()
    fixer.close()
    writer = sqlite3.connect(db)
    writer.execute("PRAGMA journal_mode=wal")
    writer.execute("PRAGMA wal_autocheckpoint=0")
    writer.execute("INSERT INTO scans (timestamp, source, ticker, direction, score, pattern, components_json, raw_json, "
                   "alert_sent, alert_suppression_reason) VALUES (?, 'webhook', 'WALROW', 'long', 1, 'uncheckpointed', "
                   "'{}', '{}', 1, '')", ("2026-09-25T03:50:00+00:00",))
    writer.commit()
    check("[wal] sidecars present while writer open", os.path.exists(db + "-wal") and os.path.exists(db + "-shm"))
    for name, path in paths.items():
        pre_main = snapshot(logs)
        code, outp, err = run(path)
        post_main = snapshot(logs)
        check("[wal+writer] %s exit 0" % name, code == 0 and not err, err[:200])
        check("[wal+writer] %s main DB and -wal unchanged, no new files" % name,
              set(pre_main) == set(post_main) and pre_main[os.path.basename(db)] == post_main[os.path.basename(db)]
              and pre_main[os.path.basename(db) + "-wal"] == post_main[os.path.basename(db) + "-wal"])
        check("[wal+writer] %s opened plain mode=ro (no immutable)" % name, "open_mode=ro " in outp)
        if name == "scanner_alerts_today.py":
            check("[wal+writer] reader sees uncheckpointed row", "ticker=WALROW" in outp)
            save("wal_writer_open_scanner_alerts_today.txt", outp)
    writer.close()          # last connection: checkpoints and deletes -wal/-shm
    check("[wal] sidecars gone after writer closed", not os.path.exists(db + "-wal") and not os.path.exists(db + "-shm"))
    for label, dir_mode in (("dir_writable", 0o755), ("dir_readonly", 0o555)):
        os.chmod(logs, dir_mode)
        try:
            for name, path in paths.items():
                before = snapshot(logs)
                code, outp, err = run(path)
                after = snapshot(logs)
                check("[wal-no-sidecars,%s] %s fallback disabled -> exit 3 ERROR, no best-effort counts" % (label, name),
                      code == 3 and "ERROR: WAL database without -wal/-shm" in outp
                      and "RESULT_INCOMPLETE=true" in outp
                      and "immutable_fallback_used" not in outp
                      and "ticker=WALROW" not in outp
                      and "alerts_sent_today" not in outp
                      and not err,
                      "code=%s out=%s err=%s" % (code, outp[-400:], err[:200]))
                check("[wal-no-sidecars,%s] %s created no files, DB unchanged" % (label, name), before == after)
        finally:
            os.chmod(logs, 0o755)
    # Finding (documented in INSTALL.md): a *plain* mode=ro open of a WAL DB without sidecars
    before = set(os.listdir(logs))
    c = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
    c.execute("SELECT count(*) FROM scans").fetchone()
    c.close()
    created = sorted(set(os.listdir(logs)) - before)
    print("FINDING plain mode=ro on WAL DB without sidecars, writable dir: created=%s" % created)
    for f in created:
        os.remove(os.path.join(logs, f))
    os.chmod(logs, 0o555)
    try:
        c = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
        c.execute("SELECT count(*) FROM scans").fetchone()
        c.close()
        print("FINDING plain mode=ro, read-only dir: opened OK")
    except sqlite3.Error as exc:
        print("FINDING plain mode=ro on WAL DB without sidecars, read-only dir: %s: %s" % (type(exc).__name__, exc))
    finally:
        os.chmod(logs, 0o755)


def check_errors(g, work):
    cases = []
    root = os.path.join(work, "err_missing", "root")
    os.makedirs(root + "/afs-shared/logs")
    cases.append(("db_missing", root, 2, "ERROR: database file not found"))
    root = os.path.join(work, "err_notsqlite", "root")
    os.makedirs(root + "/afs-shared/logs")
    open(root + "/afs-shared/logs/options_scanner.sqlite", "wb").write(b"this is not sqlite" * 20)
    cases.append(("not_sqlite", root, 3, "ERROR: file is not an SQLite 3 database"))
    root = os.path.join(work, "err_empty", "root")
    os.makedirs(root + "/afs-shared/logs")
    open(root + "/afs-shared/logs/options_scanner.sqlite", "wb").close()
    cases.append(("zero_byte_file", root, 3, "ERROR: file is not an SQLite 3 database"))
    root = os.path.join(work, "err_notable", "root")
    os.makedirs(root + "/afs-shared/logs")
    c = sqlite3.connect(root + "/afs-shared/logs/options_scanner.sqlite")
    c.execute("CREATE TABLE other (x)")
    c.commit()
    c.close()
    cases.append(("no_scans_table", root, 4, "ERROR: required table(s) missing: scans"))
    root = os.path.join(work, "err_unreadable", "root")
    os.makedirs(root + "/afs-shared/logs")
    create_db(root + "/afs-shared/logs/options_scanner.sqlite", g)
    os.chmod(root + "/afs-shared/logs/options_scanner.sqlite", 0)
    cases.append(("unreadable", root, 3, "ERROR: database not readable"))
    for label, root, want, msg in cases:
        bind = os.path.join(work, "err_" + label, "bin")
        for name in DB_SCRIPTS:
            if label == "unreadable" and os.geteuid() == 0:
                check("[%s] skipped (running as root)" % label, True)
                continue
            code, outp, err = run(patched_copy(name, root, bind))
            check("[%s] %s exit %d with ERROR line" % (label, name, want),
                  code == want and msg in outp and "RESULT_INCOMPLETE=true" in outp and not err,
                  "code=%s out=%s err=%s" % (code, outp[-300:], err[:200]))
            if name == "scanner_alerts_today.py":
                save("error_%s.txt" % label, outp)
    os.chmod(os.path.join(work, "err_unreadable", "root/afs-shared/logs/options_scanner.sqlite"), 0o644)


def check_argv_and_realnow(root, bin_dir):
    path = os.path.join(bin_dir, "scanner_alerts_today.py")
    c1, o1, e1 = run(path, now=None)
    c2, o2, e2 = run(path, now=None, args=["--help", "; rm -rf /", "x"])
    strip = lambda o: [l for l in o.splitlines() if not l.startswith("generated_at")]
    check("CLI (real clock) exit 0", c1 == 0 and not e1, e1[:200])
    check("CLI ignores argv (same output with junk args)", c2 == 0 and strip(o1) == strip(o2))


DRIFT_FEEDGAP = '''"""Mock feed-gap alarm (test fixture)."""
import os
INSTRUMENTS = ("MNQ", "MES")
WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK", "https://discord.com/api/webhooks/1/%sfg")
API_KEY = "%sapikey"
SYMBOL_FEED_TOKEN = "%ssymtok"   # matches the symbol grep AND a secret word -> must be skipped
# symbols are futures only; no options coverage here
def bars_path(symbol):
    return "/root/afs-shared/data/bars_%%s.jsonl" %% symbol
''' % (SENT, SENT, SENT)


def check_drift(work):
    root = os.path.join(work, "drift", "root")
    shared = root + "/afs-shared"
    logs = shared + "/logs"
    repo = root + "/autonomous-futures-system"
    rel_real = root + "/afs-releases/real_47ae"
    os.makedirs(logs)
    os.makedirs(repo + "/ops")
    os.makedirs(repo + "/scripts")
    os.makedirs(repo + "/.git/refs/heads")
    os.makedirs(rel_real + "/ops")
    os.makedirs(rel_real + "/scripts")
    os.symlink(rel_real, root + "/afs-releases/47ae01acdbedd544d4a72abbf63dc933d506d2c4")
    open(shared + "/feed_gap_alarm.py", "w").write(DRIFT_FEEDGAP)
    open(shared + "/options_daily_pnl_report.py", "w").write("print('pnl v2')\n")
    open(repo + "/ops/feed_gap_alarm.py", "w").write(DRIFT_FEEDGAP)
    open(repo + "/ops/options_daily_pnl_report.py", "w").write("print('pnl v1')\n")
    shutil.copy(SHIM, repo + "/scripts/feed_gap_alarm.py")
    shutil.copy(SHIM, rel_real + "/scripts/feed_gap_alarm.py")
    open(rel_real + "/ops/feed_gap_alarm.py", "w").write(DRIFT_FEEDGAP.replace('"MES")', '"MES", "M2K")'))
    open(repo + "/.git/HEAD", "w").write("ref: refs/heads/main\n")
    open(repo + "/.git/packed-refs", "w").write("# pack-refs with: peeled\n34177183d2a2abbb2442b4bd2dee3f6579875140 refs/heads/main\n")
    open(repo + "/.git/config", "w").write("[remote]\n url = https://x:%stok@github.com/x\n" % SENT)
    lines = ["2026-09-24 20:00:01 INFO options daily pnl day=2026-09-24 paper_net=12.50 trades=3"] * 45
    lines[-3] = "2026-09-24 20:00:02 INFO posted to https://discord.com/api/webhooks/9/%shook" % SENT
    lines[-2] = "2026-09-24 20:00:03 DEBUG token=%sabc" % SENT
    lines[-1] = "2026-09-24 20:00:04 INFO done " + "x" * 400
    open(logs + "/options_daily_pnl.log", "w").write("\n".join(lines) + "\n")
    doc = {"generated_at": "2026-09-24T20:00:00+00:00", "day": "2026-09-24", "cost_model": "per_contract_0.65",
           "authority": "paper_only", "paper": {"net_pnl": 12.5, "trades": 3, "status": "ok", "note": "free text"},
           "scans": {"count": 950, "alerts_sent": 7}, "webhook_url": "https://discord.com/" + SENT,
           "blocked": {"count": 40, "list": [1, 2, 3]}}
    for name in ("options_daily_pnl_latest.json", "options_daily_pnl_2026-09-24.json", "options_daily_pnl_2026-09-23.json"):
        open(logs + "/" + name, "w").write(json.dumps(doc))
    bind = os.path.join(work, "drift", "bin")
    path = patched_copy("scanner_script_drift.py", root, bind)
    before = snapshot(logs)
    code, outp, err = run(path)
    check("[drift] exit 0", code == 0 and not err, err[:300] + outp[-300:])
    check("[drift] tree unchanged", snapshot(logs) == before)
    no_secrets("[drift]", outp)
    check("[drift] release constant + realpath printed",
          "release_dir_realpath=%s" % rel_real in outp and "release_dir_constant=%s/afs-releases/47ae01ac" % root in outp)
    data = DRIFT_FEEDGAP.encode()
    blob = hashlib.sha1(b"blob %d\x00" % len(data) + data).hexdigest()
    try:
        git = subprocess.run(["git", "hash-object", shared + "/feed_gap_alarm.py"], stdout=subprocess.PIPE,
                             universal_newlines=True).stdout.strip()
    except OSError:
        git = blob
    check("[drift] git blob sha1 == git hash-object", ("git_blob_sha1=%s" % git) in outp and git == blob)
    check("[drift] sha256 printed", hashlib.sha256(data).hexdigest() in outp)
    check("[drift] real GitHub blob recognised (scripts/feed_gap_alarm.py shim)",
          "known_as=scripts/feed_gap_alarm.py@main+47ae01ac" in outp)
    check("[drift] missing shared shadow script reported",
          any("name=shadow_daily_pnl_report.py" in l and "exists=no" in l for l in grab(outp, "FILE group=SHARED")))
    check("[drift] feedgap lines shown, secret lines skipped",
          'INSTRUMENTS = ("MNQ", "MES")' in outp and "skipped_secretish=1" in outp and "API_KEY" not in outp
          and "SYMBOL_FEED_TOKEN" not in outp)
    check("[drift] release feed_gap copy (different blob) also grepped", "FEEDGAP| release" in outp)
    check("[drift] log tail 40 lines, 2 masked", len(grab(outp, "LOG| ")) == 40 and "masked=2" in outp)
    check("[drift] long log line truncated", "...(+" in "".join(grab(outp, "LOG| ")))
    check("[drift] JSON scalars printed, free text omitted, secret key skipped",
          "paper.net_pnl=12.5" in outp and "paper.note=<string omitted>" in outp
          and "skipped_secretish_keys=1" in outp and "day=2026-09-24" in outp)
    check("[drift] repo HEAD from packed-refs", "commit=34177183d2a2abbb2442b4bd2dee3f6579875140" in outp)
    check("[drift] .git/config never read", SENT + "tok" not in outp)
    save("scanner_script_drift.txt", outp)
    # permission error -> exit 3; missing base dir -> exit 2
    os.chmod(shared + "/options_daily_pnl_report.py", 0)
    code, outp, err = run(path)
    os.chmod(shared + "/options_daily_pnl_report.py", 0o644)
    if os.geteuid() != 0:
        check("[drift] unreadable file -> exit 3 ERROR", code == 3 and "ERROR: permission/read errors" in outp)
        save("error_drift_unreadable.txt", outp)
    root2 = os.path.join(work, "drift_missing", "root")
    os.makedirs(root2)
    code, outp, err = run(patched_copy("scanner_script_drift.py", root2, os.path.join(work, "drift_missing", "bin")))
    check("[drift] missing shared dir -> exit 2 ERROR", code == 2 and "ERROR: shared directory not found" in outp)


def _reset_tree(work):
    if os.path.exists(work):
        for dirpath, dirnames, filenames in os.walk(work):
            os.chmod(dirpath, 0o755)
            for f in filenames:
                p = os.path.join(dirpath, f)
                if not os.path.islink(p):
                    os.chmod(p, 0o644)
        shutil.rmtree(work)
    os.makedirs(work)


def run_harness():
    """Run every preserved mock check. Returns the list of failures."""
    global RESULTS
    RESULTS = []
    work = tempfile.mkdtemp(prefix="afs_audit_mock_")
    try:
        print("python=%s sqlite=%s euid=%d" % (sys.version.split()[0], sqlite3.sqlite_version, os.geteuid()))
        g = build_data()
        exp = expected(g)
        print("expected=%s" % dict((k, v) for k, v in exp.items() if k != "aapl_sends"))
        check_static()
        root = os.path.join(work, "main", "root")
        os.makedirs(root + "/afs-shared/logs")
        create_db(root + "/afs-shared/logs/options_scanner.sqlite", g)
        bin_dir = os.path.join(work, "main", "bin")
        for name in SCRIPTS:
            patched_copy(name, root, bin_dir)
        outputs = check_main_runs(g, exp, root, bin_dir, "rollback")
        for name, outp in outputs.items():
            save(name[:-3] + ".txt", outp)
        assert_content(outputs, exp)
        check_argv_and_realnow(root, bin_dir)
        check_dst(bin_dir, work)
        check_null_variant(g, work)
        check_wal(g, work)
        check_errors(g, work)
        check_drift(work)
        failed = [r for r in RESULTS if not r[1]]
        print("SUMMARY passed=%d failed=%d" % (len(RESULTS) - len(failed), len(failed)))
        for name, _ok, detail in failed:
            print("FAILED %s -- %s" % (name, detail))
        return failed
    finally:
        _reset_tree(work)


def test_mock_harness_preserves_all_checks():
    failed = run_harness()
    assert not failed, "\n".join("%s -- %s" % (name, detail) for name, _ok, detail in failed)




# --------------------------------------------------------------------------
# Operator review conditions (named pytest tests)
# --------------------------------------------------------------------------
_ROWID_ERROR = (
    "ERROR: ROWID_TIMESTAMP_ORDER_NOT_MONOTONIC\n"
    "RESULT_INCOMPLETE=true\n"
)
_SCANS_DDL = """CREATE TABLE scans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    source TEXT NOT NULL,
    ticker TEXT NOT NULL,
    direction TEXT NOT NULL,
    score INTEGER NOT NULL,
    pattern TEXT NOT NULL,
    components_json TEXT NOT NULL,
    raw_json TEXT NOT NULL,
    alert_sent INTEGER NOT NULL,
    alert_suppression_reason TEXT NOT NULL
)"""
_SCANS_INDEX = (
    "CREATE INDEX idx_scans_alert_key ON scans (ticker, direction, pattern, timestamp)"
)
_FORBIDDEN = (
    re.compile(r"\bsubprocess\b"),
    re.compile(r"\bos\.system\b"),
    re.compile(r"\bimportlib\b"),
    re.compile(r"\bsocket\b"),
    re.compile(r"\burllib\b"),
    re.compile(r"(?<![\w])eval\s*\("),
    re.compile(r"(?<![\w])exec\s*\("),
    re.compile(r"\bhttp\b"),
    re.compile(r"\burlopen\b"),
)


def _mini_root(tmp):
    root = os.path.join(str(tmp), "root")
    os.makedirs(root + "/afs-shared/logs")
    return root


def _insert_scan(conn, ts, pattern, sent=0, reason="counterfactual_observer_only", ticker="AAPL",
                 components='{"signa": 0}'):
    conn.execute(
        "INSERT INTO scans (timestamp, source, ticker, direction, score, pattern, components_json, "
        "raw_json, alert_sent, alert_suppression_reason) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (ts, "scheduled", ticker, "long", 70, pattern, components, "{}", sent, reason))


def _open_scans(path, with_index=True):
    conn = sqlite3.connect(path)
    conn.execute(_SCANS_DDL)
    if with_index:
        conn.execute(_SCANS_INDEX)
    return conn


def _assert_rowid_closed(code, outp, err):
    assert err == "", err
    assert code == 6, (code, outp)
    assert outp == _ROWID_ERROR, outp
    assert "alerts_sent" not in outp
    assert "rows_in_window" not in outp
    assert "end_of_report" not in outp


def test_immutable_fallback_disabled_fails_closed(tmp_path):
    """WAL without sidecars exits 3. The immutable=1 branch stays, and does not run."""
    for name in SCRIPTS:
        text = open(os.path.join(HERE, name)).read()
        assert text.count("ALLOW_IMMUTABLE_FALLBACK = False\n") == 1
        assert "ALLOW_IMMUTABLE_FALLBACK = True" not in text
    for name in DB_SCRIPTS:
        text = open(os.path.join(HERE, name)).read()
        assert "use_immutable = True" in text
        assert "immutable=1" in text
    root = _mini_root(tmp_path)
    db = root + "/afs-shared/logs/options_scanner.sqlite"
    conn = _open_scans(db)
    _insert_scan(conn, "2026-09-24T16:00:00+00:00", "kept")
    conn.commit()
    conn.execute("PRAGMA journal_mode=wal")
    conn.execute("PRAGMA wal_autocheckpoint=0")
    conn.close()
    for suffix in ("-wal", "-shm"):
        sidecar = db + suffix
        if os.path.exists(sidecar):
            os.remove(sidecar)
    before = snapshot(root + "/afs-shared/logs")
    for name in DB_SCRIPTS:
        code, outp, err = run(patched_copy(name, root, os.path.join(str(tmp_path), "bin_" + name)))
        assert code == 3, (name, code, outp, err)
        assert err == ""
        assert "ERROR: WAL database without -wal/-shm" in outp
        assert "RESULT_INCOMPLETE=true" in outp
        assert "immutable_fallback_used" not in outp
        assert "alerts_sent_today" not in outp
        assert "end_of_report exit=3" in outp
        assert snapshot(root + "/afs-shared/logs") == before


def test_rowid_order_clean_case(tmp_path):
    root = _mini_root(tmp_path)
    conn = _open_scans(root + "/afs-shared/logs/options_scanner.sqlite")
    _insert_scan(conn, "2026-09-23T12:00:00+00:00", "yesterday")
    _insert_scan(conn, "2026-09-24T15:00:00+00:00", "in_a", sent=1, reason="")
    _insert_scan(conn, "2026-09-24T16:00:00+00:00", "in_b", sent=1, reason="")
    conn.commit()
    conn.close()
    code, outp, err = run(patched_copy("scanner_alerts_today.py", root, os.path.join(str(tmp_path), "bin")))
    assert code == 0 and err == "", (code, err, outp[-400:])
    assert "ROWID_TIMESTAMP_ORDER_NOT_MONOTONIC" not in outp
    assert "pattern=in_a" in outp and "pattern=in_b" in outp
    assert "pattern=yesterday" not in outp
    assert "end_of_report exit=0" in outp
    assert "TOTAL alerts_sent_today=2" in outp


def test_rowid_order_backfill_low_id_fails_closed(tmp_path):
    """Today's timestamp on a low id, with older rows after it, must not be skipped.

    The binary search walks forward when it sees the old texts, so the floor
    lands above the backfill. The small table is proved by reading the prefix.
    A second copy lowers the span budget so only the index proof can see it.
    """
    def build(path, with_index):
        conn = _open_scans(path, with_index=with_index)
        _insert_scan(conn, "2026-09-24T16:00:00+00:00", "backfill_today", sent=1, reason="")
        for i in range(6):
            _insert_scan(conn, "2020-01-01T00:00:00+00:00", "old_%d" % i)
        _insert_scan(conn, "2026-09-24T17:00:00+00:00", "later_today", sent=1, reason="")
        conn.commit()
        conn.close()

    root = _mini_root(tmp_path)
    build(root + "/afs-shared/logs/options_scanner.sqlite", True)
    code, outp, err = run(patched_copy("scanner_alerts_today.py", root, os.path.join(str(tmp_path), "bin_small")))
    _assert_rowid_closed(code, outp, err)

    root2 = _mini_root(os.path.join(str(tmp_path), "indexed"))
    build(root2 + "/afs-shared/logs/options_scanner.sqlite", True)
    code, outp, err = run(patched_copy(
        "scanner_alerts_today.py", root2, os.path.join(str(tmp_path), "bin_idx"),
        extra=(("_PROOF_ID_SPAN_MAX = 8192\n", "_PROOF_ID_SPAN_MAX = 2\n"),)))
    _assert_rowid_closed(code, outp, err)


def test_rowid_order_inversion_within_window_fails_closed(tmp_path):
    root = _mini_root(tmp_path)
    conn = _open_scans(root + "/afs-shared/logs/options_scanner.sqlite")
    _insert_scan(conn, "2026-09-24T16:00:00+00:00", "later_first", sent=1, reason="")
    _insert_scan(conn, "2026-09-24T15:00:00+00:00", "earlier_second", sent=1, reason="")
    conn.commit()
    conn.close()
    code, outp, err = run(patched_copy("scanner_alerts_today.py", root, os.path.join(str(tmp_path), "bin")))
    _assert_rowid_closed(code, outp, err)


def test_rowid_order_indexed_monotonic_when_prefix_span_exceeds_budget(tmp_path):
    """History below the floor is larger than the prefix budget. The covering
    index proves the lexical window, so a monotonic day still reports."""
    root = _mini_root(tmp_path)
    conn = _open_scans(root + "/afs-shared/logs/options_scanner.sqlite", with_index=True)
    for i in range(5):
        _insert_scan(conn, "2020-01-%02dT00:00:00+00:00" % (i + 1), "old_%d" % i)
    _insert_scan(conn, "2026-09-24T15:00:00+00:00", "in_a", sent=1, reason="")
    _insert_scan(conn, "2026-09-24T16:00:00+00:00", "in_b", sent=1, reason="")
    conn.commit()
    conn.close()
    code, outp, err = run(patched_copy(
        "scanner_alerts_today.py", root, os.path.join(str(tmp_path), "bin"),
        extra=(("_PROOF_ID_SPAN_MAX = 8192\n", "_PROOF_ID_SPAN_MAX = 2\n"),)))
    assert code == 0 and err == "", (code, err, outp[-500:])
    assert "pattern=in_a" in outp and "pattern=in_b" in outp
    assert "TOTAL alerts_sent_today=2" in outp
    assert "ROWID_TIMESTAMP_ORDER_NOT_MONOTONIC" not in outp


def test_rowid_order_unprovable_without_index_fails_closed(tmp_path):
    """Same monotonic rows, but no timestamp index and a span over the budget.

    A full prefix read would hold a rollback-journal SHARED lock long enough
    to block scanner commits, so the script refuses it and does not scan.
    """
    root = _mini_root(tmp_path)
    conn = _open_scans(root + "/afs-shared/logs/options_scanner.sqlite", with_index=False)
    for i in range(5):
        _insert_scan(conn, "2020-01-%02dT00:00:00+00:00" % (i + 1), "old_%d" % i)
    _insert_scan(conn, "2026-09-24T15:00:00+00:00", "in_a", sent=1, reason="")
    _insert_scan(conn, "2026-09-24T16:00:00+00:00", "in_b", sent=1, reason="")
    conn.commit()
    conn.close()
    code, outp, err = run(patched_copy(
        "scanner_alerts_today.py", root, os.path.join(str(tmp_path), "bin"),
        extra=(("_PROOF_ID_SPAN_MAX = 8192\n", "_PROOF_ID_SPAN_MAX = 2\n"),)))
    _assert_rowid_closed(code, outp, err)


def test_signa_summary_evidence_only_no_989_verdict(tmp_path):
    src = open(os.path.join(HERE, "scanner_signa_summary.py")).read()
    assert "EVIDENCE ONLY" in src
    assert "does not issue an authoritative" in src
    assert "Humans or agents" in src
    assert "#989" in src
    root = _mini_root(tmp_path)
    conn = _open_scans(root + "/afs-shared/logs/options_scanner.sqlite")
    _insert_scan(conn, "2026-09-24T16:00:00+00:00", "nonzero", components='{"signa": 2}')
    conn.commit()
    conn.close()
    code, outp, err = run(patched_copy("scanner_signa_summary.py", root, os.path.join(str(tmp_path), "bin")))
    assert code == 0 and err == "", (code, err, outp[-400:])
    assert "components_signa_all_zero=NO" in outp
    for line in outp.splitlines():
        assert not re.search(r"(?i)(#989|verdict|\bPASS\b|\bFAIL\b)", line), line


def test_script_drift_never_imports_or_executes_target(tmp_path):
    sentinel = tmp_path / "import_sentinel"
    bomb = (
        "import pathlib\n"
        "pathlib.Path(%r).write_text('executed')\n"
        "raise RuntimeError('must-not-import')\n"
        'INSTRUMENTS = ("MNQ", "MES")\n'
    ) % str(sentinel)
    root = tmp_path / "root"
    shared = root / "afs-shared"
    (shared / "logs").mkdir(parents=True)
    repo = root / "autonomous-futures-system"
    (repo / "ops").mkdir(parents=True)
    (repo / "scripts").mkdir()
    (repo / ".git" / "refs" / "heads").mkdir(parents=True)
    (shared / "feed_gap_alarm.py").write_text(bomb)
    (repo / "ops" / "feed_gap_alarm.py").write_text(bomb)
    (repo / "scripts" / "feed_gap_alarm.py").write_text(bomb)
    (repo / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    (repo / ".git" / "refs" / "heads" / "main").write_text("34177183d2a2abbb2442b4bd2dee3f6579875140\n")
    code, outp, err = run(patched_copy(
        "scanner_script_drift.py", str(root), os.path.join(str(tmp_path), "bin")))
    assert not sentinel.exists()
    assert "must-not-import" not in outp
    assert "must-not-import" not in err
    assert 'INSTRUMENTS = ("MNQ", "MES")' in outp
    assert code == 0 and err == "", (code, err, outp[-400:])


def test_scripts_forbid_subprocess_os_system_exec_eval_importlib_socket_urllib_http():
    sums = open(os.path.join(HERE, "SHA256SUMS")).read()
    for name in SCRIPTS:
        path = os.path.join(HERE, name)
        text = open(path).read()
        for rx in _FORBIDDEN:
            match = rx.search(text)
            assert match is None, "%s matches %s at %r" % (name, rx.pattern, match.group(0) if match else "")
        digest = hashlib.sha256(open(path, "rb").read()).hexdigest()
        assert ("%s  %s\n" % (digest, name)) in sums
        assert text.count('ROOT = "/root"\n') == 1
        assert "os.environ" not in text
        assert "getenv" not in text
