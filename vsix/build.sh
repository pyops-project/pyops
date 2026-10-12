#!/bin/bash

# Build script for deployment-ready PyOps extension (.vsix package)
# Run from project root: ./vsix/build.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" &> /dev/null && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." &> /dev/null && pwd)"
cd "$SCRIPT_DIR"

VERSION_FILE="$PROJECT_ROOT/VERSION"
if [[ ! -f "$VERSION_FILE" ]]; then
    echo "VERSION file not found at $VERSION_FILE" >&2
    exit 1
fi
VERSION="$(tr -d '[:space:]' < "$VERSION_FILE")"
if [[ -z "$VERSION" ]]; then
    echo "VERSION file is empty" >&2
    exit 1
fi

echo "Syncing package.json version to $VERSION..."
node -e "const fs=require('fs');const p='package.json';const j=JSON.parse(fs.readFileSync(p));j.version=process.argv[1];fs.writeFileSync(p,JSON.stringify(j,null,2)+'\n');" "$VERSION"

echo "Installing dependencies..."
npm install

echo "Compiling TypeScript..."
npm run compile

# No bundler: out/src/extension.js require()s its runtime dependencies from
# node_modules/, so each one must be packaged. `vsce ls` lists the files
# `vsce package` collects (.vscodeignore applies to dependencies as well).
echo "Checking that runtime dependencies are packaged..."
PACKAGED_FILES="$(npx --yes @vscode/vsce ls)"
RUNTIME_DEPS="$(node -p "Object.keys(require('./package.json').dependencies || {}).join('\n')")"
for DEP in $RUNTIME_DEPS; do
    if ! grep -qxF "node_modules/$DEP/package.json" <<< "$PACKAGED_FILES"; then
        echo "Runtime dependency $DEP would not be packaged (node_modules/$DEP/package.json not listed by 'vsce ls'); check .vscodeignore" >&2
        exit 1
    fi
done

echo "Packaging extension..."
npx --yes @vscode/vsce package

echo "Build complete. Find pyops-*.vsix in $SCRIPT_DIR"
