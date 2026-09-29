#!/usr/bin/env bash
#
# test_env.sh - Pre-flight environment and module diagnostic suite
# Checks Python dependencies, internal module imports, binary paths, and queue state.

set -u

PYTHON_BIN="$HOME/miniforge3/bin/python3"
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cd "$PROJECT_ROOT"

echo "=================================================================="
echo "          GNSS PIPELINE V2: PRE-FLIGHT ENVIRONMENT TEST          "
echo "=================================================================="
echo "Project Root : $PROJECT_ROOT"
echo "Python Bin   : $PYTHON_BIN"
echo "Active Mode  : ${GNSS_ENV:-production}"
echo "------------------------------------------------------------------"

# --- TEST 1: DEPENDENCIES ---
echo -n "[TEST 1/3] Checking core Python dependencies... "
if "$PYTHON_BIN" -c "import sqlite3, requests, fcntl, pathlib, logging, argparse" 2>/dev/null; then
    echo "[OK]"
else
    echo "[FAILED]"
    echo "CRITICAL: Missing core libraries in $PYTHON_BIN"
    exit 1
fi

# --- TEST 2: MODULE IMPORTS ---
echo -n "[TEST 2/3] Importing project modules (config/lib/workers)... "
if "$PYTHON_BIN" -c "
from config.gnss_config import ROOT_DIR, LIVE_DB_PATH, SHADOW_DB_PATH
from config.pipeline_targets import STATIONS, DESTINATIONS, CGGTTS_JOBS
from lib.tracker import PipelineTracker
from lib.config_inspector import ConfigInspector
from lib.fetcher import drain_fetch_queue
from lib.distributor import Distributor
from lib.housekeeper import run_housekeeper
from lib.engine import run_pipeline
from lib.workers.rinex_worker import RinexWorker
from lib.workers.cggtts_worker import CggttsWorker
" 2>/dev/null; then
    echo "[OK]"
else
    echo "[FAILED]"
    echo "CRITICAL: Import error detected. Check PYTHONPATH or module syntax."
    exit 1
fi

# --- TEST 3: EXTERNAL BINARIES ---
echo "[TEST 3/3] Verifying external processing executables:"
"$PYTHON_BIN" -c "
import sys
from config.gnss_config import SEPT_RINEX, SEPT_CGGTTS, GFZRNX_BIN, RNX2CRX_BIN, GATE_SCRIPT
tools = {
    'sbf2rin': SEPT_RINEX,
    'sbf2cggtts': SEPT_CGGTTS,
    'gfzrnx': GFZRNX_BIN,
    'RNX2CRX': RNX2CRX_BIN,
    'gate_check': GATE_SCRIPT
}
all_found = True
for name, path in tools.items():
    exists = path.exists()
    status = 'EXISTS' if exists else 'MISSING'
    if not exists:
        all_found = False
    print(f'  - {name:<12}: [{status:<7}] ({path})')

sys.exit(0 if all_found else 1)
"
BIN_STATUS=$?

echo "------------------------------------------------------------------"

# --- DISPATCHER PRE-FLIGHT ---
if [ $BIN_STATUS -eq 0 ]; then
    echo "=== DISPATCHER QUEUE CHECK ==="
    "$PYTHON_BIN" bin/gnss_dispatcher.py --status
    echo "------------------------------------------------------------------"
    echo "STATUS: Pipeline environment is fully validated and ready."
else
    echo "WARNING: One or more processing binaries are missing."
    echo "Verify paths in config/gnss_config.py before launching data passes."
fi
