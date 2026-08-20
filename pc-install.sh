#!/bin/bash
# ══════════════════════════════════════════════════════════════
# VolBot PC Installation Script (Linux/macOS/Windows WSL)
# Usage: bash pc-install.sh
# ══════════════════════════════════════════════════════════════
set -e

echo "=== VolBot PC Installation ==="
echo ""

SCRIPT_DIR="$(cd "$(dirname "$BASH_SOURCE[0]")" && pwd)"
cd "$SCRIPT_DIR"

# ─── 1. Check Python ───
echo "[1/5] Checking Python..."
if command -v python3 &>/dev/null; then
    PYTHON_CMD="python3"
elif command -v python &>/dev/null; then
    PYTHON_CMD="python"
else
    echo "  ERROR: Python 3 not found. Install from https://python.org"
    exit 1
fi
echo "  Python: $($PYTHON_CMD --version)"

# ─── 2. Check Node.js ───
echo "[2/5] Checking Node.js..."
if ! command -v node &>/dev/null; then
    echo "  Node.js not found. Installing..."
    if [ "$(uname)" = "Darwin" ] || [ "$(uname)" = "Linux" ]; then
        # Try to install via package manager
        if command -v brew &>/dev/null; then
            brew install node
        elif command -v apt &>/dev/null; then
            sudo apt update && sudo apt install -y nodejs npm
        elif command -v yum &>/dev/null; then
            sudo yum install -y nodejs npm
        else
            echo "  Please install Node.js from https://nodejs.org"
            exit 1
        fi
    fi
fi
echo "  Node.js: $(node --version)"

# ─── 3. Install Python packages ───
echo "[3/5] Installing Python packages..."
$PYTHON_CMD -m pip install --upgrade pip 2>/dev/null || true
$PYTHON_CMD -m pip install construct base58 2>&1 | tail -5

# ─── 4. Install Node.js dependencies ───
echo "[4/5] Installing Node.js dependencies..."
if [ -f "package.json" ]; then
    npm install 2>&1 | tail -5
fi

# ─── 5. Optional: Install Solana CLI (for real trading) ───
echo "[5/5] Checking Solana CLI (optional, for mainnet trading)..."
if ! command -v solana &>/dev/null; then
    echo "  Solana CLI not found (optional)."
    echo "  Install with: sh -c \"\$(curl -sSfL https://release.solana.com/v1.18.26/install)\""
    echo "  Or use Docker: ./run.sh always-on (will detect Solana CLI)"
else
    echo "  Solana CLI: $(solana --version)"
fi

# ─── Docker option ───
if command -v docker &>/dev/null; then
    echo ""
    echo "  Docker is available! You can also run:"
    echo "    docker build -t volbot . && docker run -it volbot"
fi

# ─── Configure ───
if [ ! -f ".env" ]; then
    cp .env.example .env
    echo ""
    echo "Created .env from template. Edit it with your settings."
fi

# Create symlink
ln -sf "$SCRIPT_DIR/run.sh" /usr/local/bin/volbot 2>/dev/null || true

echo ""
echo "=== Installation Complete ==="
echo ""
echo "Quick start (dry-run):"
echo "  ./run.sh --devnet --dry-run --full --budget-usd 6 --wallets 5 --auto --test-mode"
echo ""
echo "Live trading (requires funded wallet + Solana CLI):"
echo "  ./run.sh --mainnet --full --budget-usd 20 --wallets 8 --auto"
echo ""
echo "Always-on mode (bot + dashboard + telegram):"
echo "  ./always-on.sh start"
echo ""
echo "Run tests:"
echo "  ./run.sh test"
