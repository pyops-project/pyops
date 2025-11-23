#!/bin/bash

# Build script for deployment-ready PyOps extension (.vsix package)
# Run from project root: ./vsix/build.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" &> /dev/null && pwd)"
cd "$SCRIPT_DIR"

echo "Installing dependencies..."
npm install

echo "Compiling TypeScript..."
npm run compile

echo "Packaging extension..."
npx --yes @vscode/vsce package

echo "Build complete. Find pyops-*.vsix in $SCRIPT_DIR"
