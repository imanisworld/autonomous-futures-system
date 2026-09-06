#!/usr/bin/env bash
# scripts/install_timers.sh
#
# Installs systemd timers on the Hetzner server:
#   2. calendar-sync   — Auto-update news_blackout_dates on the 1st of each month
#   4. feed-watchdog   — Alert to Discord if TradingView webhooks stop arriving
#                        during an active session (dead-man's-switch, every 5 min)
#
# Run as root on the Hetzner server:
#   bash scripts/install_timers.sh

set -euo pipefail

REPO="/root/autonomous-futures-system"
VENV="$REPO/.venv/bin/python3"

echo "Installing systemd timers..."

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

# ─── 4. Feed watchdog (ingestion dead-man's-switch) ───────────────────────────
cat > /etc/systemd/system/feed-watchdog.service << EOF
[Unit]
Description=RiskSentinel TradingView ingestion watchdog
After=network.target

[Service]
Type=oneshot
WorkingDirectory=$REPO
EnvironmentFile=$REPO/.env
ExecStart=$VENV $REPO/scripts/feed_watchdog.py
StandardOutput=journal
StandardError=journal
EOF

cat > /etc/systemd/system/feed-watchdog.timer << EOF
[Unit]
Description=Check TradingView ingestion freshness every 5 minutes

[Timer]
OnBootSec=3min
OnUnitActiveSec=5min

[Install]
WantedBy=timers.target
EOF

# ─── Enable all ──────────────────────────────────────────────────────────────
systemctl daemon-reload
systemctl enable --now calendar-sync.timer
systemctl enable --now feed-watchdog.timer

echo ""
echo "✓ Timers installed and active:"
systemctl list-timers calendar-sync.timer feed-watchdog.timer --no-pager
