#!/bin/bash
# ══════════════════════════════════════════════════════════════
# VolBot GitHub Sync Script
# Initialize, commit, and push to GitHub from either platform.
#
# Usage:
#   ./github-sync.sh init       # Initialize repo + first commit
#   ./github-sync.sh sync       # Commit + push all changes
#   ./github-sync.sh pull       # Pull latest changes
#   ./github-sync.sh status     # Show git status
# ══════════════════════════════════════════════════════════════
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

ACTION="${1:-sync}"

# ─── Git setup ───
if ! command -v git &>/dev/null; then
    echo "ERROR: git not installed. Install with: pkg install git (Termux) or apt install git (PC)"
    exit 1
fi

# Configure git if needed
if [ -z "$(git config user.name 2>/dev/null)" ]; then
    git config user.name "Matthew A. Baker"
fi
if [ -z "$(git config user.email 2>/dev/null)" ]; then
    git config user.email "bakermatthew3594@gmail.com"
fi

# ─── .gitignore ───
if [ ! -f ".gitignore" ]; then
    cat > .gitignore << 'EOF'
# Python
__pycache__/
*.pyc
*.pyo
*.egg-info/
dist/
build/

# Environment
.env
.lifecycle_state.json
.env.local

# Node
node_modules/
npm-debug.log*

# OS
.DS_Store
Thumbs.db

# Temporary
*.tmp
*.temp
/tmp/vb-*

# IDE
.vscode/
.idea/
*.swp
*.swo
*~
EOF
fi

case "$ACTION" in
    init)
        echo "=== Initializing VolBot Git Repository ==="
        git init
        git add -A
        git commit -m "Initial VolBot commit

- Pump.fun token lifecycle CLI (7 phases: create → fund → buy → trade → take-profit → cash-out → close)
- Telegram trading bot with stealth mode, cooldowns, 14 commands
- Web dashboard with bubble risk gauge, wallet roles, TP cascade
- Trading orchestrator with bubble detection and natural buy response
- Three-tier budget system ($6/$10/$20/$50/$100 configurations)
- Integration test suite (74 tests)"
        echo ""
        echo "Repository initialized. To connect to GitHub:"
        echo "  git remote add origin https://github.com/yourusername/volbot.git"
        echo "  ./github-sync.sh sync"
        ;;

    sync)
        echo "=== Syncing VolBot to GitHub ==="
        
        # Check if remote is configured
        if ! git remote get-url origin &>/dev/null; then
            echo "ERROR: No GitHub remote configured."
            echo "  git remote add origin https://github.com/yourusername/volbot.git"
            exit 1
        fi

        # Remove deleted files from index
        git add -A
        
        # Check if there are changes
        if git diff --cached --quiet; then
            echo "No changes to commit."
        else
            CHANGES=$(git diff --cached --stat | wc -l)
            echo "Changes detected ($CHANGES files):"
            git diff --cached --stat
            git commit -m "Automated sync: $(date '+%Y-%m-%d %H:%M EDT')

Auto-sync from $(uname -m) platform"
        fi

        # Push to GitHub
        echo "Pushing to GitHub..."
        git push origin HEAD
        echo ""
        echo "Sync complete!"
        echo "Remote: $(git remote get-url origin)"
        ;;

    pull)
        echo "=== Pulling latest from GitHub ==="
        git pull origin HEAD
        echo "Pull complete."
        ;;

    status)
        echo "=== VolBot Git Status ==="
        echo "Remote: $(git remote get-url origin 2>/dev/null || echo 'not configured')"
        echo ""
        git status
        echo ""
        echo "Recent commits:"
        git log --oneline -5 2>/dev/null || echo "No commits yet."
        ;;

    *)
        echo "Usage: $0 {init|sync|pull|status}"
        echo ""
        echo "Commands:"
        echo "  init   - Initialize git repo with first commit"
        echo "  sync   - Commit and push all changes to GitHub"
        echo "  pull   - Pull latest changes from GitHub"
        echo "  status - Show git status and recent commits"
        exit 1
        ;;
esac
