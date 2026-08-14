# `.env` & Deploy — operator reference

Code and runtime configuration move on **different tracks**:

- **Code does not auto-deploy.** The repository currently runs CI only. The
  Hetzner worktree is intentionally divergent and must not receive a broad
  `git pull`. Deploy reviewed files with a timestamped backup, then compile and
  restart explicitly.
- **`.env` never syncs.** It is **gitignored** (`.gitignore`: `.env`, `.env.*`;
  only `.env.example` is tracked). Each machine has its own `.env`, edited by
  hand.

So: **merge is not deployment**, and `.env` changes always require a deliberate
box edit plus restart.

---

## Why the two `.env` files are NOT merged

1. **Secrets.** `.env` holds broker passwords + API keys (`TRADOVATE_*`,
   `ALPACA_*`, `TASTYTRADE_*`, `WEBHOOK_SECRET`, `TELEGRAM_*`, …). The repo is on
   GitHub — committing `.env` leaks all of it.
2. **They must differ** on safety-critical keys. Blindly copying local → Hetzner
   could disable demo execution (or worse). The split is a safety feature.

---

## Updating the Hetzner `.env` (the only place that affects live trading)

The box address is deliberately not recorded in this repository. Set `AFS_BOX`
in your own shell, the same variable `scripts/atomic_release.sh` and
`scripts/afs-drift-gate.sh` take:

```bash
ssh "$AFS_BOX"                             # e.g. AFS_BOX=root@host
cd /root/autonomous-futures-system

cp .env .env.bak.$(date +%Y%m%d-%H%M%S)   # ALWAYS back up first
nano .env                                  # change the one key you mean to
sudo systemctl restart futures-bot         # REQUIRED — .env loads only at startup

# Run from the box (you are already on it). The service binds loopback, so
# these read it directly rather than going back out through the proxy.
curl -s http://127.0.0.1:8000/health              # confirm it's up + value is live
curl -s http://127.0.0.1:8000/status/diagnostics  # for safety-critical flags
curl -s http://127.0.0.1:8000/status/today        # use journal_path as runtime truth
```

Runtime evidence is frozen to the active box/API:

```text
/root/autonomous-futures-system/logs/journal_YYYY-MM-DD.jsonl
/root/autonomous-futures-system/logs/errors.log
/status/today
/status/broker-account
```

After config freeze, count the next 30 resolved MNQ live/demo-paper trades only
from the active box journal. Replay output, local ignored logs, Discord messages
alone, and Tradovate P&L alone are not end-to-end trade proof.

First-class read-only report endpoint:

```bash
curl -s "http://127.0.0.1:8000/status/proof/mnq-30?freeze_ts=2026-06-23T17:00:00Z"
```

Equivalent CLI wrapper:

```bash
cd /root/autonomous-futures-system
python3 scripts/proof_30_mnq.py --freeze-ts 2026-06-23T17:00:00+00:00
```

Why the restart is mandatory: `config/settings.py` calls `load_dotenv()` inside
`load_config()`, and `webhook/app.py` runs `_config = load_config()` **once at
import (startup)**. `load_dotenv` defaults to `override=False`, so the running
process keeps its startup values until restarted — editing the file on disk does
nothing to the live process.

---

## Which keys live where

**Box-specific — set per machine, never copy local → Hetzner:**

`PAPER_MODE` · `LIVE_TRADING_ENABLED` · `BROKER` · `TRADOVATE_ENV` ·
`STARTING_BALANCE` · `EXPO_PUBLIC_RISK_API_URL` · `HOST` · `PORT` · `LOG_DIR` ·
`SITE_ACCESS_CODE`

Hetzner live values: `PAPER_MODE=false`, `BROKER=tradovate`, `TRADOVATE_ENV=demo`,
`LIVE_TRADING_ENABLED=false` (leave this last one alone).

**Secrets — edited by hand and never copied blindly between machines:**

`WEBHOOK_SECRET` · `TRADINGVIEW_WEBHOOK_SECRET` ·
`TRADINGVIEW_WEBHOOK_SECRET_NEXT` · `TRADOVATE_*` · `ALPACA_*` ·
`TASTYTRADE_*` · `DISCORD_WEBHOOK_URL` · `SIGNA_API_KEY` · `TELEGRAM_*` ·
`PUBLIC_API_KEY`

---

## Gotchas

- **Merging to `main` does not deploy.** CI validates the merge; deployment is a
  separate, operator-controlled action.
- **Don't `git pull` Hetzner blindly** if the box's `main` has diverged / has
  uncommitted changes (e.g. a hand-edited `app.py`). Check `git status` on the
  box first.
- **Pin live-box expectations before validation.** Set `EXPECTED_LIVE_BRANCH`,
  `EXPECTED_LIVE_COMMIT`, `EXPECTED_RISK_RULES_SHA256`,
  `EXPECTED_LIVE_REPO_ROOT`, and `EXPECTED_RUNTIME_JOURNAL_DIR` on the active
  box after config freeze. Pin active proof-critical environment overrides as
  `EXPECTED_PROOF_<NAME>=<value>`; use `<unset>` to assert that an override
  remains absent. Then check `python3 scripts/doctor.py --strict` or
  `/status/diagnostics` before `/admin/live-preflight/run`.
- **Check security runtime state without revealing credentials.** Doctor and
  `/status/diagnostics` report whether the loaded config makes
  `/webhook/manual` inert and whether a distinct webhook rotation secret is
  staged. They expose env names, booleans, and counts only—not secret values,
  hashes, prefixes, or lengths. This cannot prove which service/proxy instance
  receives traffic or whether TradingView has adopted the staged credential.
- **Back up before editing** `.env` (the `.env.bak.*` pattern) so a bad edit is
  one `cp` away from recovery.
