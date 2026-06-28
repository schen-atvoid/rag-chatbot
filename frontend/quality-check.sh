#!/bin/bash
# Frontend quality checks — run from the frontend/ directory or project root
#
# Usage:
#   cd frontend && ./quality-check.sh          # check only
#   cd frontend && ./quality-check.sh --fix    # auto-format
#
# Or via npm (from frontend/):
#   npm run format          (auto-format)
#   npm run format:check    (check only)
#   npm run quality         (alias for format:check)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "=== Frontend Quality Checks ==="
echo ""

# Check if dependencies are installed
if [ ! -d "node_modules" ]; then
    echo "Installing frontend dependencies..."
    npm install
    echo ""
fi

if [ "${1:-}" = "--fix" ]; then
    echo "Running Prettier auto-format..."
    npx prettier --write "*.html" "*.js" "*.css"
    echo ""
    echo "✓ All files formatted."
else
    echo "Running Prettier format check..."
    npx prettier --check "*.html" "*.js" "*.css"
    echo ""
    echo "✓ Format check complete."
    echo ""
    echo "Tip: run './quality-check.sh --fix' to auto-format files."
fi
