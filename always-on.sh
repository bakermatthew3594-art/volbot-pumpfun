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
        $0 stop 2>/dev/null || true
        sleep 1
        $0 start
        exit 0
        ;;
    status)
        if tmux has-session -t "$SESSION" 2>/dev/null; then
            echo "[always-on] RUNNING"
            tmux list-panes -t "$SESSION" -F "  #{pane_index}: #{pane_title}"
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

# Pane 0: Bot Engine (full screen initially)
tmux new-session -d -s "$SESSION" \
    "while true; do $PYTHON_CMD -u pumpfun_lifecycle_cli.py --devnet --dry-run --full --budget-usd 6 --wallets 5 --auto --test-mode; echo '--- Bot cycle done ---'; sleep 5; done"

# Pane 1: Web Dashboard (split right)
tmux split-window -h -t "$SESSION:0.0" \
    "while true; do $PYTHON_CMD web_viz.py --port 8765; echo '--- Dashboard stopped ---'; sleep 5; done"

# Pane 2: Status Monitor (split bottom of right pane)
tmux split-window -v -t "$SESSION:0.1" \
    "$PYTHON_CMD -u status_monitor.py"

# Pane 3: Telegram Bot (split bottom of left pane)
tmux select-pane -t "$SESSION:0.0"
tmux split-window -v -t "$SESSION:0.0" \
    "while true; do echo 'Telegram Bot — not configured'; echo 'Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env'; echo 'Press Ctrl+C to exit'; sleep 60; done"

# Set pane titles
tmux select-pane -t "$SESSION:0.0" -T "Bot Engine"
tmux select-pane -t "$SESSION:0.1" -T "Web Dashboard"
tmux select-pane -t "$SESSION:0.2" -T "Status Monitor"
tmux select-pane -t "$SESSION:0.3" -T "Telegram Bot"

# Apply tiled layout for even distribution
tmux select-layout -t "$SESSION:0" tiled

echo ""
echo "[always-on] System started!"
echo "  Attach:  tmux attach -t $SESSION"
echo "  Web:     http://localhost:8765"
echo "  Quit:    tmux kill-session -t $SESSION"
