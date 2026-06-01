#!/usr/bin/env bash
# scripts/install_timers.sh
#
# Installs three systemd timers on the Hetzner server:
#   1. daily-digest    — EOD P&L summary to Discord at 21:00 UTC (16:00 ET + 1h buffer)
#   2. calendar-sync   — Auto-update news_blackout_dates on the 1st of each month
#   3. ibkr-watchdog   — Check IB Gateway health every 15 minutes
#
# Run as root on the Hetzner server:
#   bash scripts/install_timers.sh

set -euo pipefail

REPO="/root/autonomous-futures-system"
VENV="$REPO/.venv/bin/python3"

echo "Installing systemd timers..."

# ─── 1. Daily digest ─────────────────────────────────────────────────────────
cat > /etc/systemd/system/daily-digest.service << EOF
[Unit]
Description=RiskSentinel Daily EOD Digest
After=network.target

[Service]
Type=oneshot
WorkingDirectory=$REPO
EnvironmentFile=$REPO/.env
ExecStart=$VENV $REPO/scripts/daily_digest.py
StandardOutput=journal
StandardError=journal
EOF

cat > /etc/systemd/system/daily-digest.timer << EOF
[Unit]
Description=Run daily digest at 21:00 UTC (16:00 ET)

[Timer]
OnCalendar=*-*-* 21:00:00 UTC
Persistent=true

[Install]
WantedBy=timers.target
EOF

# ─── 2. Calendar sync ─────────────────────────────────────────────────────────
cat > /etc/systemd/system/calendar-sync.service << EOF
[Unit]
Description=RiskSentinel News Calendar Sync
After=network.target

[Service]
Type=oneshot
WorkingDirectory=$REPO
EnvironmentFile=$REPO/.env
ExecStart=/bin/bash -c 'cd $REPO && git pull && $VENV $REPO/scripts/sync_news_calendar.py --apply && systemctl restart futures-bot'
StandardOutput=journal
StandardError=journal
EOF

cat > /etc/systemd/system/calendar-sync.timer << EOF
[Unit]
Description=Sync economic calendar on the 1st of each month

[Timer]
OnCalendar=*-*-01 06:00:00 UTC
Persistent=true

[Install]
WantedBy=timers.target
EOF

# ─── 3. IBKR watchdog ────────────────────────────────────────────────────────
cat > /etc/systemd/system/ibkr-watchdog.service << EOF
[Unit]
Description=IB Gateway Health Watchdog
After=docker.service

[Service]
Type=oneshot
WorkingDirectory=$REPO
EnvironmentFile=$REPO/.env
ExecStart=$VENV $REPO/scripts/ibkr_watchdog.py
StandardOutput=journal
StandardError=journal
EOF

cat > /etc/systemd/system/ibkr-watchdog.timer << EOF
[Unit]
Description=Check IB Gateway every 15 minutes

[Timer]
OnBootSec=5min
OnUnitActiveSec=15min

[Install]
WantedBy=timers.target
EOF

# ─── Enable all ──────────────────────────────────────────────────────────────
systemctl daemon-reload
systemctl enable --now daily-digest.timer
systemctl enable --now calendar-sync.timer
systemctl enable --now ibkr-watchdog.timer

echo ""
echo "✓ Timers installed and active:"
systemctl list-timers daily-digest.timer calendar-sync.timer ibkr-watchdog.timer --no-pager
