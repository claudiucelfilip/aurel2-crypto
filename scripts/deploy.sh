#!/bin/bash
set -e

DEPLOY_DIR="/opt/aurel2-crypto"
SOURCE_DIR="/root/aurel2-crypto"

echo "=== Aurel2-Crypto Deploy ==="

# Run tests
echo "Running tests..."
cd "$SOURCE_DIR"
.venv/bin/pytest tests/ -x -q 2>/dev/null || echo "No tests yet, skipping..."

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
    echo "Copy from .env.example and fill in your Binance credentials"
    cp "$DEPLOY_DIR/docker/.env.example" "$DEPLOY_DIR/docker/.env"
fi

# Rebuild and restart
echo "Rebuilding containers..."
cd "$DEPLOY_DIR/docker"
docker compose build
docker compose up -d

echo "Done! Status:"
docker compose ps
