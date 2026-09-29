#!/bin/bash
# gnss_wrapper.sh - Master execution script for cron jobs
# Category: OS/Network Hook

# 1. CRON SAFETY: Force a standard PATH so commands don't randomly fail
export PATH=/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin

# Trap Ctrl+C (SIGINT) for manual aborts
trap 'echo -e "\n[$(date +%Y-%m-%d\ %H:%M:%S)] [ABORT] User pressed Ctrl+C. Exiting."; exit 1' INT

APP_DIR="/home/denali/programs/gnss/time_transfer_v2"
PY_SCRIPT="${APP_DIR}/bin/gnss_dispatcher.py"
PYTHON_BIN="/home/denali/miniforge3/bin/python3"

CG_SRC_CGGTTS="/data/hourly_transfer/daily_archive_cggtts"
CG_SRC_RINEX="/data/hourly_transfer/daily_archive"
CG_DEST="/media/jake/688gps/ggtts_sync_jake"

STATIONS=("nisx" "nisg" "nisk")
CONSTELLATIONS=("cz" "ez" "gz" "rz")

sync_to_jake() {
    local status_code=$1
    if [ "$status_code" -ne 0 ]; then 
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] [ERROR] Python dispatcher failed (Status: $status_code). Skipping sync to Jake."
        return 1
    fi

    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [INFO] Initiating 7-day rolling sync to Jake..."
    
    # SMB HANG PREVENTION: Timeout guard
    if ! timeout 5 ls "$CG_DEST" >/dev/null 2>&1; then
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] [ERROR] Jake SMB share unreachable. Aborting sync."
        return 1
    fi

    for st in "${STATIONS[@]}"; do
        local rnx_prefix="${st^^}"
        [ "$st" == "nisx" ] && rnx_prefix="NIST"

        # --- SYNC RINEX ---
        mkdir -p "$CG_DEST/rinex_${st}"
        find "$CG_SRC_RINEX" -maxdepth 1 -type f -name "${rnx_prefix}*" -mtime -7 -exec rsync -rtuW {} "$CG_DEST/rinex_${st}/" \;

        # --- SYNC CGGTTS ---
        for const in "${CONSTELLATIONS[@]}"; do
            mkdir -p "$CG_DEST/cggtts_${st}/${const}"
            find "$CG_SRC_CGGTTS" -maxdepth 1 -type f -name "${const}${st}*" -mtime -7 -exec rsync -rtuW {} "$CG_DEST/cggtts_${st}/${const}/" \;
        done
    done

    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [SUCCESS] Sync to Jake completed."
}

# =========================================================
# EXECUTION & ROUTING
# =========================================================

if [ $# -eq 0 ]; then
    echo "Usage: $0 [any valid gnss_dispatcher.py flags]"
    exit 1
fi

echo "[$(date '+%Y-%m-%d %H:%M:%S')] [INFO] Launching dispatcher with flags: $@"
GNSS_ENV=prod $PYTHON_BIN $PY_SCRIPT "$@"
PY_STATUS=$?

# 3. REGEX MATCHING: '--cggtts' matches both standard and '--cggtts-daily'
if [[ "$*" =~ --cggtts ]] || [[ "$*" =~ --rinex-daily ]]; then
    sync_to_jake $PY_STATUS
else
    if [ $PY_STATUS -ne 0 ]; then
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] [ERROR] Dispatcher execution failed."
    fi
fi
