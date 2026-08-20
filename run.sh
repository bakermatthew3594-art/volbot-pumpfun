#!/bin/bash
# ══════════════════════════════════════════════════════════════
# VolBot Universal Launcher
# Auto-detects platform (Termux, proot-distro, PC) and runs correctly
# Usage: ./run.sh <args...>  (args passed to pumpfun_lifecycle_cli.py)
# ══════════════════════════════════════════════════════════════
set -e

# Resolve script directory (works with symlinks)
SOURCE="${BASH_SOURCE[0]}"
while [ -h "$SOURCE" ]; do
    DIR="$(cd -P "$(dirname "$SOURCE")" && pwd)"
    SOURCE="$(readlink "$SOURCE")"
    [[ $SOURCE != /* ]] && SOURCE="$DIR/$SOURCE"
done
SCRIPT_DIR="$(cd -P "$(dirname "$SOURCE")" && pwd)"
cd "$SCRIPT_DIR"

# ─── Platform Detection ───
# Check if running inside proot-distro (Hermes environment)
if [ -n "$PROOT_L2S_DIR" ] || [ -n "$PROOT_L2S_DIR" ]; then
    PLATFORM="proot-distro"
elif [ -n "$PREFIX" ] && echo "$PREFIX" | grep -q "termux"; then
    PLATFORM="termux"
elif [ -f "/data/data/com.termux/files/usr/bin/python3" ]; then
    PLATFORM="termux"
else
    PLATFORM="pc"
fi

echo "[run.sh] Detected platform: $PLATFORM"
echo "[run.sh] Working directory: $SCRIPT_DIR"

# ─── Set up environment ───
export PYTHONPATH="$SCRIPT_DIR:${PYTHONPATH:-}"
export PYTHONUNBUFFERED=1

# Find the right Python interpreter
if [ "$PLATFORM" = "pc" ]; then
    PYTHON_CMD="python3"
elif [ "$PLATFORM" = "termux" ]; then
    # Prefer python3.13 (where packages are installed)
    if [ -f "/data/data/com.termux/files/usr/bin/python3.13" ]; then
        PYTHON_CMD="/data/data/com.termux/files/usr/bin/python3.13"
    else
        PYTHON_CMD="python3"
    fi
else
    # proot-distro — check for python3.13 first
    if command -v python3.13 &>/dev/null; then
        PYTHON_CMD="python3.13"
    else
        PYTHON_CMD="python3"
    fi
fi

echo "[run.sh] Using Python: $PYTHON_CMD ($($PYTHON_CMD --version 2>&1))"

# Set up Node.js path (for wallet_utils.js)
if [ -d "/tmp/node-v22.11.0-linux-arm64/bin" ]; then
    export PATH="/tmp/node-v22.11.0-linux-arm64/bin:$PATH"
fi

# ─── Mode Selection ───
# If first arg is "dashboard", start the tmux dashboard
# If first arg is "telegram", start the telegram bot
# If first arg is "always-on", start everything in tmux
# Otherwise, pass all args to the lifecycle CLI
case "${1:-}" in
    dashboard)
        shift
        echo "[run.sh] Starting web dashboard..."
        if command -v python3.13 &>/dev/null; then
            python3.13 web_viz.py --port 8765
        else
            $PYTHON_CMD web_viz.py --port 8765
        fi
        ;;
    telegram)
        shift
        echo "[run.sh] Starting Telegram bot..."
        $PYTHON_CMD telegram_bot.py
        ;;
    always-on)
        shift
        exec "$SCRIPT_DIR/always-on.sh"
        ;;
    test|verify)
        shift
        echo "[run.sh] Running integration tests..."
        rm -f .lifecycle_state.json
        $PYTHON_CMD -u integration_test.py
        ;;
    *)
        # Pass all args to the lifecycle CLI
        echo "[run.sh] Running: pumpfun_lifecycle_cli.py $*"
        $PYTHON_CMD -u pumpfun_lifecycle_cli.py "$@"
        ;;
esac
