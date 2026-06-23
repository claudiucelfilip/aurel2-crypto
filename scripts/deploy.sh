#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
DEPLOY_DIR="${AUREL2_CRYPTO_DEPLOY_DIR:-/opt/aurel2-crypto}"

echo "=== Aurel2-Crypto Deploy ==="

# Run tests when a test suite exists
echo "Running tests..."
cd "$SOURCE_DIR"
if [ -d tests ]; then
    if [ -x .venv/bin/pytest ]; then
        .venv/bin/pytest tests/ -x -q
    elif python3 -c "import pytest" >/dev/null 2>&1; then
        python3 -m pytest tests/ -x -q
    else
        echo "pytest not installed, skipping tests..."
    fi
else
    echo "No tests yet, skipping..."
fi

# Sync source
echo "Syncing to $DEPLOY_DIR..."
mkdir -p "$DEPLOY_DIR"
rsync -a --delete \
    --exclude='.git' \
    --exclude='.venv' \
    --exclude='__pycache__' \
    --exclude='/data/' \
    --exclude='.env' \
    "$SOURCE_DIR/" "$DEPLOY_DIR/"

# Copy .env if it doesn't exist at deploy target
if [ ! -f "$DEPLOY_DIR/docker/.env" ]; then
    echo "WARNING: No .env file at $DEPLOY_DIR/docker/.env"
    if [ -f "$DEPLOY_DIR/docker/.env.example" ]; then
        echo "Copy from .env.example and fill in your Binance credentials"
        cp "$DEPLOY_DIR/docker/.env.example" "$DEPLOY_DIR/docker/.env"
    else
        echo "Create $DEPLOY_DIR/docker/.env with Binance credentials before starting containers."
        exit 1
    fi
fi

# Rebuild and restart
echo "Rebuilding containers..."
cd "$DEPLOY_DIR/docker"
docker compose build
docker compose up -d

echo "Done! Status:"
docker compose ps
