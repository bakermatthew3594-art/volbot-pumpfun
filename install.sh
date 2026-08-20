#!/bin/bash
# Install dependencies for the volume bot (uses curl + tar, no npm install)
# All packages downloaded as tarballs directly from npm registry.
# You can audit this script before running - every line is readable.

set -e

echo "=== Installing Volume Bot Dependencies ==="
cd "$(dirname "$0")"
mkdir -p node_modules/@noble
mkdir -p /tmp/vb-download

# Helper: download + extract npm package tarball
# Args: package_name version tarball_filename
dl_pkg() {
    local pkg_name="$1"
    local version="$2"
    local tarball_name="$3"
    local url="https://registry.npmjs.org/${pkg_name}/-/${tarball_name}.tgz"
    echo "  Downloading ${pkg_name}@${version}..."

    # Check if already installed
    local dir_name
    if [[ "$pkg_name" == "@noble/"* ]]; then
        dir_name="@noble-$(echo "$pkg_name" | cut -d/ -f2)"
    else
        dir_name="$pkg_name"
    fi
    # For scoped packages like @noble/curves, also check @noble-curves
    if [[ "$pkg_name" == "@noble/"* ]]; then
        dir_name="@noble-$(basename "$pkg_name")"
    fi

    if [ -d "node_modules/${dir_name}" ] && [ -f "node_modules/${dir_name}/package.json" ]; then
        echo "  Already installed, skipping."
        return 0
    fi

    curl -sL --max-time 30 "$url" -o "/tmp/vb-download/pkg.tgz"
    # Verify gzip magic bytes (0x1f 0x8b)
    local magic
    magic=$(od -An -tx1 -N2 "/tmp/vb-download/pkg.tgz" 2>/dev/null | tr -d ' ')
    if [ "$magic" != "1f8b" ]; then
        echo "  ERROR: ${url} did not return valid gzip (got: ${magic})"
        echo "  Response size: $(wc -c < /tmp/vb-download/pkg.tgz) bytes"
        return 1
    fi
    rm -rf /tmp/vb-extract
    mkdir -p /tmp/vb-extract
    tar xzf "/tmp/vb-download/pkg.tgz" -C "/tmp/vb-extract"
    if [ -n "$dir_name" ]; then
        cp -r "/tmp/vb-extract/package" "node_modules/${dir_name}"
    fi
}

# 1. bs58@6.0.0 - base58 encode/decode (used by wallet_utils.js)
dl_pkg "bs58" "6.0.0" "bs58-6.0.0"

# 2. @noble/curves@1.4.2 - ed25519 elliptic curve crypto
dl_pkg "@noble/curves" "1.4.2" "curves-1.4.2"

# 3. @noble/hashes - dependency of @noble/curves (use 1.8.0, compatible)
dl_pkg "@noble/hashes" "1.8.0" "hashes-1.8.0"

# 4. base-x@5.0.1 - dependency of bs58
dl_pkg "base-x" "5.0.1" "base-x-5.0.1"
# Copy base-x into bs58's node_modules for resolution
if [ -d "node_modules/base-x" ]; then
    mkdir -p "node_modules/bs58/node_modules"
    cp -r "node_modules/base-x" "node_modules/bs58/node_modules/base-x"
fi

# Create symlinks for @noble namespace resolution
# @noble/curves requires('@noble/hashes') - Node resolves via @noble/hashes symlink
if [ -d "node_modules/@noble-hashes" ] && [ ! -L "node_modules/@noble/hashes" ]; then
    ln -sf ../@noble-hashes node_modules/@noble/hashes 2>/dev/null || true
fi
if [ -d "node_modules/@noble-curves" ] && [ ! -L "node_modules/@noble/curves" ]; then
    ln -sf ../@noble-curves node_modules/@noble/curves 2>/dev/null || true
fi

# Clean up
rm -rf /tmp/vb-download /tmp/vb-extract

echo ""
echo "=== Dependencies installed ==="
echo "node_modules contents:"
ls node_modules/
echo ""

# Test
echo "=== Testing wallet generation ==="
node wallet_utils.js generate 2>&1
echo ""
echo "=== Testing bot demo ==="
python3 bot.py 2>&1
echo ""
echo "=== Ready! ==="
echo "To start trading:"
echo "  1. cp .env.example .env"
echo "  2. Edit .env with your PRIVATE_KEY and TOKEN_MINT"
echo "  3. python3 bot.py --live"
