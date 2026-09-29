"""
housekeeper.py - Automated scratch storage and database ledger maintainer.
Category: IMMUTABLE INFRASTRUCTURE
"""

import os
import time
import shutil
from pathlib import Path
from datetime import datetime, timezone, timedelta

from config.gnss_config import (
    RAW_BUFFER,
    WORKSPACE,
    LOG_DIR,
    RAW_RETENTION_DAYS,
    LOG_RETENTION_DAYS,
    DB_RETENTION_DAYS,
)

def _purge_directory_by_age(dir_path: Path, days: int, log) -> int:
    """Deletes files in dir_path older than threshold and cleans empty parent directories."""
    if not dir_path.exists():
        return 0
    
    cutoff_sec = time.time() - (days * 86400)
    purged_count = 0

    # 1. Purge expired files
    for item in dir_path.glob("**/*"):
        if item.is_file():
            try:
                if item.stat().st_mtime < cutoff_sec:
                    item.unlink()
                    purged_count += 1
            except Exception as e:
                log.warning(f"  [HOUSEKEEPER] Failed to remove file {item}: {e}")

    # 2. Prune newly emptied subdirectories (bottom-up)
    for sub_dir in sorted(dir_path.glob("**/*"), reverse=True):
        if sub_dir.is_dir():
            try:
                # rmdir fails safely if directory is not empty
                sub_dir.rmdir()
            except OSError:
                pass  # Directory still contains valid unexpired files

    return purged_count


def run_housekeeper(log, tracker=None):
    """Pre-flight housekeeper pass executed prior to engine workflow steps."""
    log.info("[HOUSEKEEPER] Running system scratch cleanup...")

    # 1. Clean stale temporary workspace subdirectories older than 24 hours
    workspace_cutoff = time.time() - (5*86400)
    if WORKSPACE.exists():
        for ws_item in WORKSPACE.iterdir():
            if ws_item.is_dir() and ws_item.name.startswith("run_"):
                if ws_item.stat().st_mtime < workspace_cutoff:
                    try:
                        shutil.rmtree(ws_item)
                        log.info(f"  [HOUSEKEEPER] Cleared orphaned workspace: {ws_item.name}")
                    except Exception as e:
                        log.warning(f"  [HOUSEKEEPER] Failed to clear workspace {ws_item.name}: {e}")

    # 2. Purge raw SBF binaries beyond RAW_RETENTION_DAYS
    raw_purged = _purge_directory_by_age(RAW_BUFFER, RAW_RETENTION_DAYS, log)
    if raw_purged > 0:
        log.info(f"  [HOUSEKEEPER] Purged {raw_purged} raw binary files older than {RAW_RETENTION_DAYS} days.")

    # 3. Purge operational log files beyond LOG_RETENTION_DAYS
    logs_purged = _purge_directory_by_age(LOG_DIR, LOG_RETENTION_DAYS, log)
    if logs_purged > 0:
        log.info(f"  [HOUSEKEEPER] Purged {logs_purged} log files older than {LOG_RETENTION_DAYS} days.")

    # 4. Prune SQLite ledger entries beyond DB_RETENTION_DAYS
    if tracker:
        try:
            pruned_count = tracker.prune_ledger(days_to_keep=DB_RETENTION_DAYS)
            if pruned_count > 0:
                log.info(f"  [HOUSEKEEPER] Pruned {pruned_count} ledger entries older than {DB_RETENTION_DAYS} days.")
        except Exception as e:
            log.warning(f"  [HOUSEKEEPER] DB ledger pruning warning: {e}")
