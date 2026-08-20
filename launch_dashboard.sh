#!/bin/bash
# ╔══════════════════════════════════════════════════════════════╗
# ║     Solana Volume Bot - tmux Dashboard Launcher             ║
# ║     No tmux knowledge required — just run this script!         ║
# ╚══════════════════════════════════════════════════════════════╝
#
# Starts a 4-pane tmux dashboard:
#   ┌──────────────┬──────────────┐
#   │  Live Charts │  Trade Log   │
#   │  (web viz)   │  (real-time) │
#   ├──────────────┼──────────────┤
#   │  Wallets     │  Controls    │
#   │  (balances)  │  (menu/help)  │
#   └──────────────┴──────────────┘
#
# Usage: ./launch_dashboard.sh
# After startup: tmux attach -t volbot
# Quick keys: Ctrl+B then arrow keys to navigate panes
#             Ctrl+B then z to zoom a pane
#             Ctrl+B then d to detach (session keeps running)
#              Ctrl+B then x to close a pane
#              Ctrl+B then c to create a new pane

set -e

# ─── Resolve bot directory ──
SOURCE="${BASH_SOURCE[0]}"
while [ -h "$SOURCE" ]; do
    DIR="$(cd -P "$(dirname "$SOURCE")" && pwd)"
    SOURCE="$(readlink "$SOURCE")"
    [[ $SOURCE != /* ]] && SOURCE="$DIR/$SOURCE"
done
BOT_DIR="$(cd -P "$(dirname "$SOURCE")" && pwd)"
cd "$BOT_DIR"

SESSION="volbot"
PORT=8765

# ─── Check prerequisites ──
if ! command -v tmux &> /dev/null; then
    echo "ERROR: tmux is not installed."
    echo "Install with: pkg install tmux (Termux) or apt install tmux (Linux)"
    exit 1
fi

if ! command -v python3.13 &> /dev/null; then
    echo "WARNING: python3.13 not found (needed for Flask web dashboard)"
    echo "Falling back to python3..."
    PYTHON_CMD="python3"
else
    PYTHON_CMD="python3.13"
fi

# ─── Kill existing session if running ──
if tmux has-session -t "$SESSION" 2>/dev/null; then
    echo "Session '$SESSION' already exists. Killing it first..."
    tmux kill-session -t "$SESSION"
fi

echo "Starting VolBot tmux dashboard..."
echo "  Bot directory: $BOT_DIR"
echo "  Dashboard URL: http://localhost:$PORT"
echo "  tmux session:  $SESSION"
echo ""

# ─── Create tmux session with 4 panes ──

# Pane 1 (top-left): Web visualization dashboard
tmux new-session -d -s "$SESSION" \
    -n "Dashboard" \
    "$PYTHON_CMD web_viz.py --port $PORT; echo 'Dashboard stopped. Press any key...'; read"

# Split top-right (Pane 2)
tmux split-window -h -t "$SESSION:0" \
    "while true; do echo 'Trade Log - Real-time Activity'; echo ''; echo 'Waiting for trades...'; sleep 2; clear; done; echo 'Done'; read"

# Split bottom-left (Pane 3) - first split the left side vertically
tmux select-pane -t "$SESSION:0.0"
tmux split-window -v -t "$SESSION:0" \
    "$PYTHON_CMD -c \"
import sys, os, time, json
sys.path.insert(0, '$BOT_DIR')
from trading_engine import get_price_feed
from trading_engine import WRAPPED_SOL_MINT, USDC_MINT, BONK_MINT
print('Wallet Balance Monitor')
print('='*40)
print()
while True:
    try:
        prices = get_multiple_prices_safe()
        if prices:
            for mint, data in prices.items():
                symbol = {'So11111111111111111111111111111111111111112':'SOL',
                         'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v':'USDC'}.get(mint, mint[:8])
                if isinstance(data, dict):
                    price = data.get('usdPrice', data.get('price', 'N/A'))
                    print(f'  {symbol}: \${price:.6f}' if isinstance(price, (int,float)) else f'  {symbol}: {price}')
        else:
            print('  (waiting for price data...)')
        print()
        print(f'  Last update: {time.strftime(\"%H:%M:%S\")}')
        time.sleep(5)
    except KeyboardInterrupt:
        print('  Stopped.')
        break
    except Exception as e:
        print(f'  Error: {e}')
        time.sleep(5)
\"; echo 'Stopped. Press any key...'; read"

# Split bottom-right (Pane 4) - this splits the right side vertically
tmux select-pane -t "$SESSION:0.1"
tmux split-window -v -t "$SESSION:0" \
    "while true; do echo 'Controls & Help'; echo ''; echo '┌──────────────────────────────────┐'; echo '│ VolBot tmux Dashboard            │'; echo '├──────────────────────────────────┤'; echo '│ Navigating:                      │'; echo '│   Ctrl+B then arrows = move      │'; echo '│   Ctrl+B then z = zoom/unzoom    │'; echo '│   Ctrl+B then d = detach         │'; echo '│   Ctrl+B then x = close pane     │'; echo '│                                    │'; echo '│ Quick Commands:                  │'; echo '│   volbot --test                  │'; echo '│   volbot --demo                  │'; echo '│   volbot --web                   │'; echo '│   volbot (full CLI)              │'; echo '│                                    │'; echo '│ To exit: tmux kill-session -t volbot'; echo '└──────────────────────────────────┘'; echo ''; echo 'Press Ctrl+C to exit this pane.'; sleep 1; done"

# ─── Set pane titles ──
tmux select-pane -t "$SESSION:0.0" -T "Live Charts"
tmux select-pane -t "$SESSION:0.1" -T "Trade Log"
tmux select-pane -t "$SESSION:0.2" -T "Wallet Balances"
tmux select-pane -t "$SESSION:0.3" -T "Controls & Help"

# ─── Balance panes ──
tmux select-layout -t "$SESSION:0" tiled

# ─── Start the web server ──
echo ""
echo "Dashboard starting..."
echo "Opening http://localhost:$PORT in your browser..."
sleep 2

# Try to auto-open browser (works on Android Termux + Linux)
if command -v termux-open-url &> /dev/null; then
    termux-open-url "http://localhost:$PORT" 2>/dev/null || true
elif command -v xdg-open &> /dev/null; then
    xdg-open "http://localhost:$PORT" 2>/dev/null || true
fi

echo ""
echo "═══════════════════════════════════════════════════════════════"
echo "  VolBot tmux Dashboard is running!"
echo "═══════════════════════════════════════════════════════════════"
echo ""
echo "  Dashboard URL:  http://localhost:$PORT"
echo "  tmux session:   $SESSION"
echo ""
echo "  To view the dashboard:"
echo "    1. Open http://localhost:$PORT in your browser"
echo "    2. In another terminal: tmux attach -t $SESSION"
echo ""
echo "  tmux navigation cheat sheet:"
echo "    Ctrl+B → ↑↓←→   = move between panes"
echo "    Ctrl+B → z      = zoom current pane"
echo "    Ctrl+B → d      = detach (leave session running)"
echo "    Ctrl+B → x      = close current pane"
echo "    tmux kill-session -t $SESSION  = stop everything"
echo ""
echo "  Or just type: tmux attach -t $SESSION"
