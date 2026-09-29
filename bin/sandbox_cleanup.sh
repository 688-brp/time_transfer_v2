#!/usr/bin/env bash
#
# clean_sandbox.sh - Wipes sandbox buffers, mock endpoints, and shadow database ledger.
# Safely ignores production paths.

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SANDBOX_DIR="$PROJECT_ROOT/sandbox_env"

if [ ! -d "$SANDBOX_DIR" ]; then
    echo "[!] Sandbox directory does not exist at: $SANDBOX_DIR"
    exit 0
fi

echo "=================================================================="
echo "               WIPING SANDBOX ENVIRONMENT STATE                   "
echo "=================================================================="
echo "Target Path: $SANDBOX_DIR"

# 1. Clear scratch buffers and mock delivery directories
rm -rf "$SANDBOX_DIR/local_scratch/raw_buffer/"*
rm -rf "$SANDBOX_DIR/local_scratch/workspace/"*
rm -rf "$SANDBOX_DIR/local_scratch/outbound_buffer/"*
rm -rf "$SANDBOX_DIR/local_scratch/logs/"*
rm -rf "$SANDBOX_DIR/mock_destinations/"*

# 2. Reset the shadow SQLite database
rm -f "$SANDBOX_DIR/data_base/pipeline_shadow.db"*

# 3. Re-create required directory skeleton
mkdir -p "$SANDBOX_DIR/local_scratch/raw_buffer" \
         "$SANDBOX_DIR/local_scratch/workspace" \
         "$SANDBOX_DIR/local_scratch/outbound_buffer" \
         "$SANDBOX_DIR/local_scratch/logs" \
         "$SANDBOX_DIR/mock_destinations" \
         "$SANDBOX_DIR/data_base"

echo "[OK] Sandbox environment successfully reset to fresh state."
echo "=================================================================="
