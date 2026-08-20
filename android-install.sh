#!/data/data/com.termux/files/usr/bin/bash
# ══════════════════════════════════════════════════════════════
# VolBot Android/Termux Installation Script
# Run inside proot-distro Ubuntu (where Hermes runs)
# Usage: bash android-install.sh
# ══════════════════════════════════════════════════════════════
set -e

echo "=== VolBot Android Installation ==="
echo "Platform: proot-distro Ubuntu (Hermes)"
echo ""

SCRIPT_DIR="$(cd "$(dirname "$BASH_SOURCE[0]")" && pwd)"
cd "$SCRIPT_DIR"

# ─── 1. Ensure Python packages ───
echo "[1/5] Checking Python packages..."
PIP_INSTALL=""
if python3 -m pip --version &>/dev/null; then
    PIP_INSTALL="python3 -m pip"
elif command -v pip3 &>/dev/null; then
    PIP_INSTALL="pip3"
elif python3.13 -m pip --version &>/dev/null; then
    PIP_INSTALL="python3.13 -m pip"
    python3 -m pip --version 2>/dev/null || true
fi

# Install Python packages with --break-system-packages (PEP 668)
PIP_INSTALL="$PIP_INSTALL install --break-system-packages"
echo "  Installing: construct base58"
$PIP_INSTALL construct base58 2>&1 | tail -5

# ─── 2. Ensure Node.js is available ───
echo "[2/5] Checking Node.js..."
if ! command -v node &>/dev/null; then
    if [ -d "/tmp/node-v22.11.0-linux-arm64/bin" ]; then
        export PATH="/tmp/node-v22.11.0-linux-arm64/bin:$PATH"
        echo "  Found Node.js in /tmp/node-v22.11.0-linux-arm64/"
    else
        echo "  ERROR: Node.js not found. Please install Node.js manually."
        echo "  Download from: https://nodejs.org/dist/v22.11.0/node-v22.11.0-linux-arm64.tar.xz"
        echo "  Extract to /tmp/ and the PATH will be set automatically."
        exit 1
    fi
fi
echo "  Node.js: $(node --version)"

# ─── 3. Install Node.js dependencies ──
echo "[3/5] Installing Node.js dependencies..."
# Check if node_modules already exists
if [ -d "node_modules" ] && [ -d "node_modules/@solana" ] && [ -d "node_modules/@noble-curves" ]; then
    echo "  node_modules already present. Skipping."
else
    echo "  Running install.sh (downloads tarballs directly)..."
    bash install.sh
fi

# ─── 4. Install tmux (for dashboard) ──
echo "[4/5] Checking tmux..."
if ! command -v tmux &>/dev/null; then
    echo "  Installing tmux..."
    if command -v apt &>/dev/null; then
        apt update -qq && apt install -y -qq tmux 2>&1 | tail -3
    elif command -v pkg &>/dev/null; then
        pkg install -y tmux 2>&1 | tail -3
    else
        echo "  WARNING: Could not install tmux. Dashboard will not work."
        echo "  The bot and telegram bot will still run."
    fi
fi
if command -v tmux &>/dev/null; then
    echo "  tmux: $(tmux -V)"
fi

# ─── 5. Configure environment ──
echo "[5/5] Configuring environment..."

# Create .env from template if not exists
if [ ! -f ".env" ]; then
    cp .env.example .env
    echo "  Created .env from template. Edit it with your settings."
    echo "  Key settings:"
    echo "    PRIVATE_KEY=         Your main wallet private key"
    echo "    TELEGRAM_BOT_TOKEN=  (optional) Bot token from @BotFather"
    echo "    TELEGRAM_CHAT_ID=    (optional) Your chat ID for alerts"
fi

# Add PATH to .bashrc if not already there
if ! grep -q "node-v22.11.0" ~/.bashrc 2>/dev/null; then
    echo 'export PATH="/tmp/node-v22.11.0-linux-arm64/bin:$PATH"' >> ~/.bashrc
    echo "  Added Node.js path to ~/.bashrc"
fi

# Create symlink for easy access
ln -sf "$SCRIPT_DIR/run.sh" /data/data/com.termux/files/usr/bin/volbot 2>/dev/null || true
echo "  Created 'volbot' symlink"

# ─── Verify ──
echo ""
echo "=== Verification ==="
python3 -m py_compile pumpfun_lifecycle_cli.py telegram_bot.py bot.py && echo "  Python: All files compile ✓"
node -e "require('./wallet_utils.js')" 2>/dev/null && echo "  Node.js: wallet_utils.js ✓"

echo ""
echo "=== Installation Complete ==="
echo ""
echo "Quick start:"
echo "  ./run.sh --devnet --dry-run --full --budget-usd 6 --wallets 5 --auto --test-mode"
echo "  ./always-on.sh start    # Start bot + dashboard + telegram"
echo "  ./run.sh test           # Run integration tests (74 tests)"
echo ""
echo "For Telegram bot:"
echo "  1. Get a bot token from @BotFather"
echo "  2. Get your chat ID (ask @userinfobot)"
echo "  3. Edit .env: TELEGRAM_BOT_TOKEN=your_token"
echo "  4. Edit .env: TELEGRAM_CHAT_ID=your_chat_id"
