#!/usr/bin/env bash
#
# inspect_mapping.sh - Direct invocation of ConfigInspector for GNSS pipeline mappings
# Generates stdout console output and appends timestamped records to local scratch logs.

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$HOME/miniforge3/bin/python3}"

# Resolve log directory based on active environment (sandbox vs production)
if [ "${GNSS_ENV:-production}" = "sandbox" ]; then
    LOG_DIR="$PROJECT_ROOT/sandbox_env/local_scratch/logs"
else
    LOG_DIR="$PROJECT_ROOT/local_scratch/logs"
fi

mkdir -p "$LOG_DIR"

TIMESTAMP=$(date -u '+%Y%m%d_%H%M%S')
LOG_FILE="$LOG_DIR/route_matrix_${TIMESTAMP}.log"

cd "$PROJECT_ROOT"

{
    echo "=================================================================="
    echo "         GNSS TIME-TRANSFER V2: ROUTING MATRIX INSPECTION         "
    echo "=================================================================="
    echo "Timestamp   : $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
    echo "Active Mode : ${GNSS_ENV:-production}"
    echo "Project Root: $PROJECT_ROOT"
    echo "------------------------------------------------------------------"
    echo ""

    "$PYTHON_BIN" -c "
import sys
from pathlib import Path

ROOT_DIR = Path('$PROJECT_ROOT')
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from lib.config_inspector import ConfigInspector

inspector = ConfigInspector()
inspector.print_route_matrix()
"
} | tee "$LOG_FILE"

echo ""
echo "[LOGGED] Mapping audit successfully saved to: $LOG_FILE"
