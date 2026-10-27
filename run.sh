#!/bin/bash
# vipbot - cPanel terminal runner
# USE: bash run.sh   (bot background start + logging bot.log)
cd "$(dirname "$0")"

# stop an old instance first
pkill -f "vipbot.py" 2>/dev/null
sleep 1

# python3 discover
PY="$(command -v python3 || command -v python)"
if [ -z "$PY" ]; then
  echo "python3 not found. Install the Python App using Setup Python App in cPanel."; exit 1
fi

nohup "$PY" -u vipbot.py > bot.log 2>&1 &
echo "Bot started (PID $!). Live log: tail -f bot.log"
