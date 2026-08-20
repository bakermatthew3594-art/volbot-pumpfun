#!/bin/bash
# Solana Volume Bot - One-command startup
# Usage: volbot                       # Full interactive CLI
#        volbot --demo               # Quick bot.py demo
#        volbot --wallet             # Generate a new wallet
#        volbot --tip <lamports> <seed>  # Build tip transfer tx
#        volbot --test               # Run verification suite
#        volbot --web                # Start web visualization server

set -e

# Resolve the actual script directory (works with symlinks)
SOURCE="${BASH_SOURCE[0]}"
while [ -h "$SOURCE" ]; do
    DIR="$(cd -P "$(dirname "$SOURCE")" && pwd)"
    SOURCE="$(readlink "$SOURCE")"
    [[ $SOURCE != /* ]] && SOURCE="$DIR/$SOURCE"
done
BOT_DIR="$(cd -P "$(dirname "$SOURCE")" && pwd)"
cd "$BOT_DIR"

export PYTHONPATH="$BOT_DIR:$PYTHONPATH"
export PYTHONUNBUFFERED=1

case "${1:-cli}" in
    --demo)
        echo "=== Running bot.py demo ==="
        python3 bot.py
        ;;
    --wallet)
        echo "=== Generating new wallet ==="
        node wallet_utils.js generate | python3 -m json.tool
        ;;
    --tip)
        if [ -z "$2" ]; then
            echo "Usage: volbot --tip <lamports> <seed_b58>"
            exit 1
        fi
        echo "=== Building tip transfer ==="
        node sign_sender.js tip_transfer "$2" "$3" | python3 -m json.tool
        ;;
    --test)
        echo "=== Running verification suite ==="
        python3 -c "
import py_compile, sys
sys.path.insert(0, '$BOT_DIR')

# Syntax checks
for f in ['cli.py', 'bot.py', 'liquidity.py', 'backtest.py', 'strategies.py',
          'strategies_advanced.py', 'config.py', 'bundle_bot.py', 'trading_engine.py',
          'onchain_monitor.py']:
    py_compile.compile(f, doraise=True)
print('Syntax: All 10 Python files OK')

# Import check
import cli, bot, liquidity, backtest, strategies, config
import bundle_bot, trading_engine, onchain_monitor
print('Imports: All modules OK')
print('Ready to run!')
"
        ;;
    --web)
        echo "=== Starting web visualization server ==="
        echo "Open http://localhost:8765 in your browser"
        python3.13 web_viz.py || python3 web_viz.py
        ;;
    *)
        echo "=== Starting Solana Volume Bot CLI ==="
        echo "Tip: Use --demo, --wallet, --tip, --web, or --test for quick commands"
        echo ""
        python3 cli.py
        ;;
esac
