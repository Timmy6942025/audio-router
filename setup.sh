#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "==> Checking prerequisites..."

if ! command -v python3 &>/dev/null; then
    echo "ERROR: Python 3 not found."
    exit 1
fi

if [[ "$(uname -m)" != "arm64" ]]; then
    echo "ERROR: Only Apple Silicon (M1/M2/M3/M4) is supported."
    echo "For Intel Macs, build audiotee from source:"
    echo "  cd vendor/audiotee && swift build -c release"
    exit 1
fi

echo "==> Installing Python dependencies..."
pip3 install -r "$REPO_DIR/requirements.txt"

echo ""
echo "==> Setup complete!"
echo ""
echo "Web GUI (recommended):"
echo "  python3 $REPO_DIR/web/app.py"
echo ""
echo "CLI:"
echo "  python3 $REPO_DIR/bin/audio_router.py --list"
echo "  python3 $REPO_DIR/bin/audio_router.py --full 1 --bass 2"
