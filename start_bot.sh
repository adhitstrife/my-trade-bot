#!/usr/bin/env bash
# ============================================================
# Start Indodax trading bot (detached, self-healing)
# Usage: ./start_bot.sh
# ============================================================
set -u
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$SCRIPT_DIR" || { echo "Dir not found"; exit 1; }
mkdir -p data logs

LOG=logs/bot.log

# Kill any stale instance of the bot
pkill -f "bot.py run --config config.json" 2>/dev/null
sleep 1

# Start detached so it survives the calling shell
setsid nohup python3 bot.py run --config config.json >> "$LOG" 2>&1 < /dev/null &
echo $! > data/bot.pid

sleep 4

if pgrep -f "bot.py run --config config.json" > /dev/null 2>&1; then
    echo "[$(date '+%F %T')] Bot started (pid $(cat data/bot.pid)). Log: $(pwd)/$LOG"
else
    echo "[$(date '+%F %T')] FAILED to start bot. Check $LOG" >&2
    exit 1
fi
