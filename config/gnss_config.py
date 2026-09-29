"""
gnss_config.py - Centralized configuration and environment routing.
Category: IMMUTABLE CORE (Append-only)
"""

import os
import sys
from pathlib import Path

# --- 1. ENVIRONMENT DETECTION ---
GNSS_ENV = os.getenv("GNSS_ENV", "production").lower()
IS_SANDBOX = (GNSS_ENV == "sandbox")

# --- 2. DYNAMIC PATHING ---
# Resolves to root project folder (e.g., /home/denali/programs/gnss/time_transfer_v2)
ROOT_DIR = Path(__file__).resolve().parent.parent
BASE_DIR = ROOT_DIR  # Legacy alias for backward compatibility

if IS_SANDBOX:
    LOCAL_BASE_DIR = ROOT_DIR / "sandbox_env"
else:
    LOCAL_BASE_DIR = ROOT_DIR

# --- 3. STRUCTURAL DIRECTORIES ---
DATA_BASE_DIR = LOCAL_BASE_DIR / "data_base"
LOCAL_SCRATCH = LOCAL_BASE_DIR / "local_scratch"

LOG_DIR = LOCAL_SCRATCH / "logs"
RAW_BUFFER = LOCAL_SCRATCH / "raw_buffer"
OUTBOUND_BUFFER = LOCAL_SCRATCH / "outbound_buffer"
WORKSPACE = LOCAL_SCRATCH / "workspace"
LOCAL_ARCHIVE = LOCAL_BASE_DIR / "local_archive"

# Ensure config directory is in python path for standalone worker imports
CONFIG_DIR = ROOT_DIR / "config"
if str(CONFIG_DIR) not in sys.path:
    sys.path.insert(0, str(CONFIG_DIR))

# --- 4. DATABASE TARGETS (Live Cron vs Manual Shadow) ---
LIVE_DB_PATH = DATA_BASE_DIR / "pipeline_queue.db"
SHADOW_DB_PATH = DATA_BASE_DIR / "pipeline_shadow.db"
DB_FILE = LIVE_DB_PATH  # Legacy alias

# --- 5. EXECUTABLES & BINARIES ---
# Primary binary search paths (Checks project-local 'bin/' first, falls back to central '/home/denali/programs/gnss/bin/')
CENTRAL_BIN = Path("/home/denali/programs/gnss/time_transfer/bin")

SEPT_RINEX = CENTRAL_BIN / "sept_bin" / "sbf2rin" if (CENTRAL_BIN / "sept_bin" / "sbf2rin").exists() else ROOT_DIR / "bin/sept_bin/sbf2rin"
SEPT_CGGTTS = CENTRAL_BIN / "sept_bin" / "sbf2cggtts" if (CENTRAL_BIN / "sept_bin" / "sbf2cggtts").exists() else ROOT_DIR / "bin/sept_bin_26/sbf2cggtts"

GFZRNX_BIN = CENTRAL_BIN / "gfzrnx" / "gfzrnx_lx" if (CENTRAL_BIN / "gfzrnx" / "gfzrnx_lx").exists() else ROOT_DIR / "bin/gfzrnx/gfzrnx_lx"
RNX2CRX_BIN = CENTRAL_BIN / "hatanaka" / "RNX2CRX" if (CENTRAL_BIN / "hatanaka" / "RNX2CRX").exists() else ROOT_DIR / "bin/hatanaka/RNX2CRX"
CRX2RNX_BIN = CENTRAL_BIN / "hatanaka" / "CRX2RNX" if (CENTRAL_BIN / "hatanaka" / "CRX2RNX").exists() else ROOT_DIR / "bin/hatanaka/CRX2RNX"

GATE_SCRIPT = ROOT_DIR / "bin/gate_check_cggtts.py"

# --- 6. STATION CONFIGURATION ---
# Stations listed here use legacy directory/file naming conventions instead of modern Septentrio LOG1/LOG2/LOG3 paths
LEGACY_STATIONS = set()  # Populate with e.g. {"nist"} if legacy path resolution is required

# ==============================================================================
# RETENTION POLICIES (DAYS)
# ==============================================================================
RAW_RETENTION_DAYS = 10     # Retain raw binary buffer for manual shadow backfills
LOG_RETENTION_DAYS = 30     # Retain dispatcher and execution logs
DB_RETENTION_DAYS  = 90     # Retain processed queue records in SQLite ledger

# --- 7. AUTO-INITIALIZATION ---
def init_filesystem():
    """Ensures all structural directories exist before pipeline execution."""
    dirs = [
        DATA_BASE_DIR, LOCAL_SCRATCH, LOG_DIR, 
        RAW_BUFFER, OUTBOUND_BUFFER, WORKSPACE, LOCAL_ARCHIVE
    ]
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)

# Run initialization on import
init_filesystem()
