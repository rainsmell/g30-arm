#!/usr/bin/env bash
# Build xu_tool (libuvc) and run the basic checks.
set -e
cd "$(dirname "$0")"

echo "== installing deps (needs sudo) =="
if ! pkg-config --exists libuvc 2>/dev/null && [ ! -f /usr/include/libuvc/libuvc.h ]; then
    sudo apt update
    sudo apt install -y build-essential pkg-config libusb-1.0-0-dev v4l-utils ffmpeg usbutils || true
    sudo apt install -y libuvc-dev || echo "libuvc-dev not in repo -> see README for source build"
fi

echo "== building xu_tool =="
gcc -O2 -o xu_tool xu_tool.c -luvc -lusb-1.0
echo "built: $(pwd)/xu_tool"
