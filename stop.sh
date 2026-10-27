#!/bin/bash
# vipbot stopper
pkill -f "vipbot.py" 2>/dev/null
sleep 1
if pgrep -f "vipbot.py" >/dev/null; then
  echo "Still running:"; pgrep -af "vipbot.py"
else
  echo "Bot stopped."
fi
tail -5 bot.log 2>/dev/null
