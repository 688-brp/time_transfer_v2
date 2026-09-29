#!/bin/bash
# gnss_wrapper.sh - Master execution script for cron jobs
# Category: OS/Network Hook

# Trap Ctrl+C (SIGINT) to kill the entire wrapper instantly
trap 'echo -e "\n[$(date +%Y-%m-%d\ %H:%M:%S)] [ABORT] User pressed Ctrl+C. Exiting entire script immediately."; exit 1' INT

APP_DIR="/home/denali/programs/gnss/time_transfer_v2"
PY_SCRIPT="${APP_DIR}/bin/gnss_dispatcher.py"
PYTHON_BIN="/home/denali/miniforge3/bin/python3"

CG_SRC_CGGTTS="/data/hourly_transfer/daily_archive_cggtts"
CG_SRC_RINEX="/data/hourly_transfer/daily_archive"
CG_DEST="/media/jake/688gps/ggtts_sync_jake"

STATIONS=("nisx" "nisg" "nisk")
CONSTELLATIONS=("cz" "ez" "gz" "rz")

sync_to_jake() {
    if [ $1 -ne 0 ]; then 
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] [ERROR] Python dispatcher failed (Status: $1). Skipping sync to Jake."
        return 1
    fi

    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [INFO] Initiating 7-day rolling sync to Jake..."
    
    # 1. Quick OS-level mount check
    if ! timeout 5 ls "$CG_DEST" >/dev/null 2>&1; then
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] [ERROR] Jake SMB share unreachable."
        return 1
    fi

    # 2. SMB-Safe Batched Rsync (7-day window, no permissions sync, whole-file transfer)
    for st in "${STATIONS[@]}"; do

        if [ "$st" == "nisx" ]; then
            rnx_prefix="NIST"
        else
            rnx_prefix="${st^^}"
        fi

        # --- SYNC RINEX ---
        mkdir -p "$CG_DEST/rinex_${st}"
        rnx_files=()
        # Find files modified in the last 7 days and safely push them into an array
        while IFS= read -r -d $'\0' file; do
            rnx_files+=("$file")
        done < <(find "$CG_SRC_RINEX" -maxdepth 1 -type f -name "${rnx_prefix}*" -mtime -7 -print0)

        if [ ${#rnx_files[@]} -gt 0 ]; then
            # -rtuvW: recursive, preserve times, update, verbose, Whole-file (SMB safe)
            rsync -rtuvW "${rnx_files[@]}" "$CG_DEST/rinex_${st}/"
        fi

        # --- SYNC CGGTTS ---
        for const in "${CONSTELLATIONS[@]}"; do
            mkdir -p "$CG_DEST/cggtts_${st}/${const}"
            cggtts_files=()
            
            while IFS= read -r -d $'\0' file; do
                cggtts_files+=("$file")
            done < <(find "$CG_SRC_CGGTTS" -maxdepth 1 -type f -name "${const}${st}*" -mtime -7 -print0)

            if [ ${#cggtts_files[@]} -gt 0 ]; then
                rsync -rtuvW "${cggtts_files[@]}" "$CG_DEST/cggtts_${st}/${const}/"
            fi
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

if [[ "$*" == *"--cggtts"* ]] || [[ "$*" == *"--cggtts-daily"* ]] || [[ "$*" == *"--rinex-daily"* ]]; then
    sync_to_jake $PY_STATUS
else
    if [ $PY_STATUS -ne 0 ]; then
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] [ERROR] Dispatcher execution failed."
    fi
fi
