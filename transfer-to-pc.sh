#!/bin/bash
# ══════════════════════════════════════════════════════════════
# VolBot Transfer Script
# Packages all files, configs, and documentation for transfer
# to another machine (PC Hermes desktop).
#
# Usage: ./transfer-to-pc.sh [github|scp|package]
#   github  — Push to GitHub (default, requires git remote)
#   scp     — Copy via SCP to a remote host
#   package — Create a self-contained zip/tarball
# ══════════════════════════════════════════════════════════════
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

METHOD="${1:-github}"
TARGET="${2:-}"

case "$METHOD" in
    github)
        echo "=== Transferring to GitHub ==="
        git add -A
        if ! git diff --cached --quiet; then
            git commit -m "Transfer sync: $(date '+%Y-%m-%d %H:%M EDT')"
        fi
        git push origin main
        git push origin pc 2>/dev/null || true
        echo ""
        echo "Transferred to: https://github.com/bakermatthew3594-art/volbot-pumpfun"
        echo ""
        echo "On the PC, clone with:"
        echo "  git clone https://github.com/bakermatthew3594-art/volbot-pumpfun.git"
        echo "  cd volbot-pumpfun"
        echo "  bash pc-install.sh"
        ;;

    scp)
        if [ -z "$TARGET" ]; then
            echo "Usage: $0 scp <user@host:path>"
            exit 1
        fi
        echo "=== Transferring via SCP to $TARGET ==="
        # Include node_modules if they exist
        rsync -avz --progress \
            --include="*.py" \
            --include="*.sh" \
            --include="*.js" \
            --include="*.json" \
            --include="*.md" \
            --include="Dockerfile" \
            --include=".env.example" \
            --include=".gitignore" \
            --include="node_modules/**" \
            --exclude=".git/**" \
            --exclude="*.pyc" \
            --exclude="__pycache__/**" \
            "$SCRIPT_DIR/" "$TARGET"
        echo "Transfer complete."
        ;;

    package)
        echo "=== Creating self-contained package ==="
        PACKAGE_NAME="volbot-pumpfun-$(date +%Y%m%d-%H%M).tar.gz"
        # Create tar.gz excluding .git and __pycache__
        tar --exclude='.git' \
            --exclude='__pycache__' \
            --exclude='*.pyc' \
            --exclude='.lifecycle_state.json' \
            --exclude='.env' \
            -czf "/tmp/$PACKAGE_NAME" .
        echo "Package created: /tmp/$PACKAGE_NAME"
        echo "Size: $(du -h /tmp/$PACKAGE_NAME | cut -f1)"
        echo ""
        echo "To install on PC:"
        echo "  scp /tmp/$PACKAGE_NAME user@pc:/tmp/"
        echo "  ssh user@pc 'cd /tmp && tar xzf $PACKAGE_NAME && cd volbot && bash pc-install.sh'"
        ;;

    *)
        echo "Usage: $0 {github|scp|package} [target]"
        echo ""
        echo "Methods:"
        echo "  github       Push to GitHub (default)"
        echo "  scp <target> Copy via rsync to user@host:/path"
        echo "  package      Create tar.gz archive in /tmp/"
        exit 1
        ;;
esac
