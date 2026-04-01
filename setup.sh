#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "==> Checking prerequisites..."

if ! command -v swift &>/dev/null; then
    echo "ERROR: Swift not found. Install Xcode Command Line Tools:"
    echo "  xcode-select --install"
    exit 1
fi

if ! command -v python3 &>/dev/null; then
    echo "ERROR: Python 3 not found."
    exit 1
fi

echo "==> Building audiotee (Swift)..."
cd "$REPO_DIR/vendor/audiotee"
swift build -c release

echo "==> Installing Python dependencies..."
pip3 install -r "$REPO_DIR/requirements.txt"

echo ""
echo "==> Setup complete!"
echo ""
echo "List your audio devices:"
echo "  python3 $REPO_DIR/bin/audio_router.py --list"
echo ""
echo "Start routing (replace device IDs):"
echo "  python3 $REPO_DIR/bin/audio_router.py --full 1 --bass 2"
