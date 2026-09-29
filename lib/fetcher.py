"""
fetcher.py - Rsync ingestion worker for raw binary and native logs.
Category: MUTABLE CONNECTOR (< 100 lines)
"""

import sys
import subprocess
import logging
from pathlib import Path

log = logging.getLogger(__name__)

# ONLY explicit force/sweep flags trigger a global queue sweep.
# Routine flags (--rinex-hourly, --rinex-daily, --range) remain strictly targeted.
SWEEP_FLAGS = {'--sweep', '--force', '-f'}


def execute_rsync_fetch(job: dict, tracker, timeout: int = 180) -> bool:
    """Executes a single rsync fetch job from inbound_queue."""
    job_id = job['id']
    remote_src = job['remote_src']
    local_dest = Path(job['local_dest'])
    
    local_dest.mkdir(parents=True, exist_ok=True)
    
    is_pattern = '*' in remote_src or '?' in remote_src
    
    if is_pattern and ':' in remote_src:
        base_dir, pattern = remote_src.rsplit('/', 1)
        base_dir += '/'
        cmd = [
            "rsync", "-az", "--partial", "--timeout=30",
            "--include", pattern,
            "--exclude", "*",
            base_dir, f"{local_dest}/"
        ]
    else:
        pattern = Path(remote_src).name
        cmd = ["rsync", "-az", "--partial", "--timeout=30", remote_src, f"{local_dest}/"]

    cmd_str = " ".join(cmd)
    
    try:
        log.debug(f"[FETCHER] Executing: {cmd_str}")
        res = subprocess.run(
            cmd, 
            capture_output=True, 
            text=True, 
            timeout=timeout, 
            check=False
        )
        
        if res.returncode == 0:
            if is_pattern:
                matched_files = list(local_dest.glob(pattern))
                if not matched_files:
                    log.info(f"  [FETCHER] Files not ready yet (silent pass): {pattern}")
                    tracker.mark_inbound(job_id, success=False)
                    return False

            tracker.mark_inbound(job_id, success=True)
            log.info(f"  [FETCHER] SUCCESS: Fetched {remote_src}")
            return True
        else:
            err = res.stderr.strip() if res.stderr else "Unknown rsync error"
            log.error(f"  [FETCHER] FAILED (Code {res.returncode}): {remote_src} -> {err}")
            tracker.mark_inbound(job_id, success=False)
            return False
            
    except subprocess.TimeoutExpired:
        log.error(f"  [FETCHER] TIMEOUT ({timeout}s): {remote_src}")
        tracker.mark_inbound(job_id, success=False)
        return False
    except Exception as e:
        log.error(f"  [FETCHER] Exception on {remote_src}: {e}")
        tracker.mark_inbound(job_id, success=False)
        return False


def drain_fetch_queue(tracker, job_type: str = None, target_pattern: str = None, sweep_all: bool = False):
    """
    Retrieves pending fetches matching active job_type and target_pattern.
    Targeted runs (including --range) strictly filter by target_pattern.
    Global queue sweeps are restricted to explicit sweep flags (--sweep, --force).
    """
    is_sweep = sweep_all or bool(SWEEP_FLAGS.intersection(set(sys.argv[1:])))

    if is_sweep:
        log.info("[FETCHER] Global sweep flag detected. Draining ALL pending queue items.")
        pending = tracker.get_pending_inbound()
    else:
        pending = tracker.get_pending_inbound(job_type=job_type, target_pattern=target_pattern)

    if not pending:
        label_parts = []
        if job_type:
            label_parts.append(f"job_type '{job_type}'")
        if target_pattern:
            label_parts.append(f"pattern '{target_pattern}'")
        label = f" for {' '.join(label_parts)}" if label_parts else ""
        log.info(f"[FETCHER] No pending fetch jobs in inbound queue{label}.")
        return

    target_label = " [GLOBAL SWEEP]" if is_sweep else (f" for target [{target_pattern}]" if target_pattern else "")
    log.info(f"[FETCHER] Draining {len(pending)} queued fetch jobs{target_label}...")
    for job in pending:
        execute_rsync_fetch(job, tracker)
