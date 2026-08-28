#!/usr/bin/env bash
# ============================================================
# Watchdog: keep the Indodax trading bot alive 24/7
# If the bot is not running, start it. Intended to be called
# from cron every few minutes.
# ============================================================
set -u
cd /opt/data/my-trade-bot || exit 1
mkdir -p data logs

WLOG=logs/watchdog.log
BLOG=logs/bot.log

if pgrep -f "bot.py run --config config.json" > /dev/null 2>&1; then
    # Bot is alive — nothing to do (log once per day max to avoid spam)
    TODAY=$(date '+%F')
    if [ ! -f "logs/.last_wd_ok_$TODAY" ]; then
        echo "[$(date '+%F %T')] OK: bot is running" >> "$WLOG"
        touch "logs/.last_wd_ok_$TODAY"
    fi
    exit 0
fi

# Bot is down — restart it
echo "[$(date '+%F %T')] Bot NOT running. Starting..." >> "$WLOG"
setsid nohup python3 bot.py run --config config.json >> "$BLOG" 2>&1 < /dev/null &

sleep 6

if pgrep -f "bot.py run --config config.json" > /dev/null 2>&1; then
    echo "[$(date '+%F %T')] Bot restarted OK" >> "$WLOG"
else
    echo "[$(date '+%F %T')] START FAILED" >> "$WLOG"
    exit 1
fi
