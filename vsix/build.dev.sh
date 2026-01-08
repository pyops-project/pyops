#!/bin/bash

# Build script for local debugging of PyOps extension
# Run from project root: ./vsix/build.dev.sh
# This starts continuous compilation; open vsix/ in VSCode and press F5 to debug.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" &> /dev/null && pwd)"
cd "$SCRIPT_DIR"

echo "Installing dependencies..."
npm install

echo "Starting TypeScript watch mode (continuous compilation)..."
npm run watch &

# Keep the script running to monitor watch process
wait
