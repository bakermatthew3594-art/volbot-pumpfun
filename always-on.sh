#!/bin/bash
# ══════════════════════════════════════════════════════════════
# VolBot Always-On Supervisor
# Keeps the bot engine, web dashboard, telegram bot, and status
# monitor running in a persistent tmux session.
#
# Usage: ./always-on.sh [start|stop|restart|status]
# ══════════════════════════════════════════════════════════════
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

SESSION="volbot"
export PYTHONPATH="$SCRIPT_DIR:${PYTHONPATH:-}"
export PYTHONUNBUFFERED=1

# Find Python
if command -v python3.13 &>/dev/null; then
    PYTHON_CMD="python3.13"
else
    PYTHON_CMD="python3"
fi

# Ensure node is in path
if [ -d "/tmp/node-v22.11.0-linux-arm64/bin" ]; then
    export PATH="/tmp/node-v22.11.0-linux-arm64/bin:$PATH"
fi

ACTION="${1:-start}"

case "$ACTION" in
    stop)
        echo "[always-on] Stopping..."
        tmux kill-session -t "$SESSION" 2>/dev/null || echo "  No session."
        exit 0
        ;;
    restart)
        "$SCRIPT_DIR/always-on.sh" stop 2>/dev/null || true
        sleep 1
        "$SCRIPT_DIR/always-on.sh" start
        exit 0
        ;;
    status)
        if tmux has-session -t "$SESSION" 2>/dev/null; then
            echo "[always-on] RUNNING"
            tmux list-panes -t "$SESSION" -F "  pane=#{pane_index}: #{pane_title} [PID #{pane_pid}]"
        else
            echo "[always-on] NOT running"
        fi
        exit 0
        ;;
esac

# ─── Start ──
tmux kill-session -t "$SESSION" 2>/dev/null || true
sleep 0.5

echo "[always-on] Python: $PYTHON_CMD"
echo "[always-on] Session: $SESSION | Dashboard: http://localhost:8765"

# ─── Create tmux session with 4 panes ───
# Layout:
#   ┌──────────────┬──────────────┐
#   │  Bot Engine  │  Web Dashbrd │
#   ├──────────────┼──────────────┤
#   │  Telegram    │  Status      │
#   └──────────────┴──────────────┘
#
# After select-layout rearranges panes, we set titles by pane ID
# (which is stable) rather than pane index (which changes).
#
# Creation order: %0=Bot, %1=Web, %2=Status, %3=Telegram
# After layout:  %0=Bot(index0), %1=Web(index2), %2=Status(index3), %3=Telegram(index1)

# Pane 0: Bot Engine (creates session, pane ID %0)
tmux new-session -d -s "$SESSION" \
    "while true; do $PYTHON_CMD -u pumpfun_lifecycle_cli.py --devnet --dry-run --full --budget-usd 6 --wallets 5 --auto --test-mode; echo '--- Bot cycle done ---'; sleep 5; done"

# Pane 1: Web Dashboard (split right, pane ID %1)
tmux split-window -h -t "$SESSION:0.0" \
    "while true; do $PYTHON_CMD web_viz.py --port 8765; echo '--- Dashboard stopped ---'; sleep 5; done"

# Pane 2: Status Monitor (split bottom of Web Dashboard, pane ID %2)
tmux split-window -v -t "$SESSION:0.1" \
    "$PYTHON_CMD -u status_monitor.py"

# Pane 3: Telegram Bot (split bottom of Bot Engine, pane ID %3)
tmux select-pane -t "$SESSION:0.0"
tmux split-window -v -t "$SESSION:0.0" \
    "while true; do if [ -f .env ] && grep -q 'TELEGRAM_BOT_TOKEN' .env; then $PYTHON_CMD -u telegram_bot.py; else echo 'Telegram Bot - not configured'; echo 'Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env'; echo 'Press Ctrl+B to detach'; sleep 60; fi; done"

# Apply layout
tmux select-layout -t "$SESSION:0" tiled

# Set titles by pane ID (stable after layout rearrangement)
# After tiled layout: %0=index0, %3=index1, %1=index2, %2=index3
tmux select-pane -t "%0" -T "Bot Engine"
tmux select-pane -t "%1" -T "Web Dashboard"
tmux select-pane -t "%2" -T "Status Monitor"
tmux select-pane -t "%3" -T "Telegram Bot"

sleep 2
echo ""
echo "[always-on] System started!"
echo "  Attach:  tmux attach -t $SESSION"
echo "  Web:     http://localhost:8765"
echo "  Quit:    tmux kill-session -t $SESSION"
