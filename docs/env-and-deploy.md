# `.env` & Deploy — operator reference

Two things move on **different tracks**:

- **Code** auto-syncs. Push to `main` → the GitHub Action (`deploy.yml`) SSHes to
  Hetzner and runs `git pull` + pip install + `systemctl restart futures-bot`.
- **`.env` never syncs.** It is **gitignored** (`.gitignore`: `.env`, `.env.*`;
  only `.env.example` is tracked). `git push`/`pull` never carries it. Each
  machine has its own `.env`, edited by hand.

So: **code change = push. env change = SSH + edit + restart.** Different actions.

---

## Why the two `.env` files are NOT merged

1. **Secrets.** `.env` holds broker passwords + API keys (`TRADOVATE_*`,
   `ALPACA_*`, `TASTYTRADE_*`, `WEBHOOK_SECRET`, `TELEGRAM_*`, …). The repo is on
   GitHub — committing `.env` leaks all of it.
2. **They must differ** on safety-critical keys. Blindly copying local → Hetzner
   could disable demo execution (or worse). The split is a safety feature.

---

## Updating the Hetzner `.env` (the only place that affects live trading)

```bash
ssh root@5.78.84.223
cd /root/autonomous-futures-system

cp .env .env.bak.$(date +%Y%m%d-%H%M%S)   # ALWAYS back up first
nano .env                                  # change the one key you mean to
sudo systemctl restart futures-bot         # REQUIRED — .env loads only at startup

curl -s http://5.78.84.223/health          # confirm it's up + value is live
curl -s http://5.78.84.223/status/diagnostics   # for safety-critical flags
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

**Shared secrets — same value both boxes, only ever edited by hand:**

`WEBHOOK_SECRET` · `TRADOVATE_*` · `ALPACA_*` · `TASTYTRADE_*` ·
`DISCORD_WEBHOOK_URL` · `SIGNA_API_KEY` · `TELEGRAM_*` · `PUBLIC_API_KEY`

---

## Gotchas

- **Deploy only fires on `main`.** A feature branch (e.g.
  `fix/false-chop-and-session-cutoff`) is NOT deployed until merged to `main`.
- **Don't `git pull` Hetzner blindly** if the box's `main` has diverged / has
  uncommitted changes (e.g. a hand-edited `app.py`). Check `git status` on the
  box first.
- **Back up before editing** `.env` (the `.env.bak.*` pattern) so a bad edit is
  one `cp` away from recovery.
