"""
engine.py - Core pipeline sequence manager.
Category: MUTABLE CONNECTOR 
"""

import sys
import subprocess
import shutil
from pathlib import Path
from datetime import datetime, timezone, timedelta

from lib.housekeeper import run_housekeeper
from lib.fetcher import drain_fetch_queue
from lib.workers.rinex_worker import RinexWorker
from lib.workers.cggtts_worker import CggttsWorker
from lib.workers.qa_gatekeeper import QAGatekeeper
from lib.distributor import Distributor
from lib.network_node import NetworkNode
from config.gnss_config import ROOT_DIR, RAW_BUFFER, OUTBOUND_BUFFER

def mjd_to_dt(mjd: int) -> datetime:
    """Converts Modified Julian Date to datetime."""
    return datetime(1858, 11, 17, tzinfo=timezone.utc) + timedelta(days=int(mjd))

def _parse_time_str(time_str: str) -> datetime:
    """Flexibly parses MJD, ISO Date, or ISO Timestamp strings."""
    clean_str = str(time_str).strip().replace("T", " ")
    if clean_str.isdigit():
        if len(clean_str) <= 5:
            return mjd_to_dt(int(clean_str))
        elif len(clean_str) == 8:
            return datetime.strptime(clean_str, "%Y%m%d").replace(tzinfo=timezone.utc)
            
    try:
        return datetime.strptime(clean_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return datetime.strptime(clean_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)

def parse_target_dates(args) -> list:
    """Generates target datetimes dynamically based on active flags and system clock."""
    is_hourly_rinex = bool(getattr(args, 'rinex_hourly', False))

    # 1. Manual Flag-driven range override (Forces Execution)
    if getattr(args, 'range', None):
        raw_range = args.range
        start_str = raw_range[0]
        # Allow single-date range by falling back to start_str if STOP is missing
        stop_str = raw_range[1] if len(raw_range) > 1 else start_str

        start_dt = _parse_time_str(start_str)
        stop_dt = _parse_time_str(stop_str)
        
        # Step by hour if hourly flag, otherwise step by day
        step = timedelta(hours=1) if is_hourly_rinex else timedelta(days=1)
        
        targets = []
        current_dt = start_dt
        while current_dt <= stop_dt:
            targets.append(current_dt)
            current_dt += step
        return targets
        
    # 2. Default cron steady-state logic
    now_utc = datetime.now(timezone.utc)
    if is_hourly_rinex:
        # RINEX hourly targets previous completed hour
        return [(now_utc - timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)]
        
    if getattr(args, 'rinex_daily', False) or getattr(args, 'cggtts_daily', False):
        # Daily RINEX or Boundary-Stitched CGGTTS targets yesterday
        return [(now_utc - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)]

    # CGGTTS rapid default: targets active current UTC day
    return [now_utc.replace(hour=0, minute=0, second=0, microsecond=0)]

def execute_pass(args, target_dt, tracker, log, pipeline_config):
    """Executes a single pass for a target date: Fetch -> Process -> Distribute."""
    now_utc = datetime.now(timezone.utc)
    
    is_hourly_rinex = bool(getattr(args, 'rinex_hourly', False))
    is_daily_rinex = bool(getattr(args, 'rinex_daily', False))
    is_any_rinex = is_hourly_rinex or is_daily_rinex
    
    is_cggtts_rapid = bool(getattr(args, 'cggtts', False))
    is_cggtts_daily = bool(getattr(args, 'cggtts_daily', False))
    is_any_cggtts = is_cggtts_rapid or is_cggtts_daily

    is_historical_day = target_dt.date() < now_utc.date()

    target_str = target_dt.strftime("%Y%j%H") if is_hourly_rinex else target_dt.strftime("%Y%j")
    target_year = target_dt.strftime("%Y")
    
    scope_label = "HOURLY_RNX" if is_hourly_rinex else ("HISTORICAL_DAY" if is_historical_day else "INTRADAY")
    
    log.info(f"\n=== PIPELINE PASS FOR TARGET: {target_dt.strftime('%Y-%m-%d %H:%M:%S')} UTC ({scope_label}) ===")

    # -------------------------------------------------------------------------
    # 0. AUTOMATIC MANUAL OVERRIDE & TARGET RECONCILIATION
    # -------------------------------------------------------------------------
    is_manual = any([
        getattr(args, 'skip_rnx2', False),
        getattr(args, 'force', False),
        getattr(args, 'range', None),
        getattr(args, 'manual', False),
    ])

    if is_manual:
        log.info(f"  [ENGINE] Manual override active for target [{target_str}].")

        # 1. Re-queue inbound fetch and processing status for requested target_str ONLY
        tracker.reset_status(target_pattern=target_str)

        try:
            with tracker._get_connection() as conn:
                cursor = conn.cursor()

                # Clear quarantine audit records for this specific target
                cursor.execute("DELETE FROM quarantine_log WHERE date_str = ?", (target_str,))

                # Reset quarantined or failed outbound jobs matching target_str ONLY
                short_target_str = target_str.replace(target_year, '')
                cursor.execute(
                    """
                    UPDATE outbound_queue 
                    SET retry_count = 0, status = 'PENDING', updated_at = CURRENT_TIMESTAMP 
                    WHERE status IN ('QUARANTINED', 'FAILED') 
                      AND (file_path LIKE ? OR file_path LIKE ?)
                    """,
                    (f"%{target_str}%", f"%{short_target_str}%")
                )

                # 2. AUTO-RECONCILE: Immediately fast-fail any PENDING jobs whose physical buffer files are missing on disk
                cursor.execute("SELECT id, file_path FROM outbound_queue WHERE status = 'PENDING'")
                pending_outbound = cursor.fetchall()
                reconciled_count = 0

                for row_id, fpath in pending_outbound:
                    if not Path(fpath).exists():
                        cursor.execute(
                            """
                            UPDATE outbound_queue 
                            SET status = 'FAILED_MISSING', retry_count = 10, updated_at = CURRENT_TIMESTAMP 
                            WHERE id = ?
                            """,
                            (row_id,)
                        )
                        reconciled_count += 1

                if reconciled_count > 0:
                    log.info(f"  [ENGINE] Auto-reconciled {reconciled_count} stale queue entries (missing buffer files).")

                conn.commit()

        except Exception as e:
            log.warning(f"  [ENGINE] Warning during manual override setup for {target_str}: {e}")

    # -------------------------------------------------------------------------
    # 1. INSTANTIATE NODES
    # -------------------------------------------------------------------------
    stations = pipeline_config.STATIONS
    destinations = pipeline_config.DESTINATIONS
    nodes = {s_name: NetworkNode(s_name, s_data) for s_name, s_data in stations.items()}
    
    # -------------------------------------------------------------------------
    # 2. INGESTION PHASE (Gated by CLI Flags)
    # -------------------------------------------------------------------------
    if not getattr(args, 'no_fetch', False):
        log.info("  [ENGINE] Queueing remote fetch tasks...")
        
        if is_cggtts_rapid and not is_historical_day:
            tracker.reset_status(target_pattern=target_str, destination_tag="DAILY_BIN")

        for node in nodes.values():
            if is_hourly_rinex:
                node.queue_fetch(tracker, target_dt, "HOURLY_RNX")
            if is_daily_rinex:
                node.queue_fetch(tracker, target_dt, "DAILY_RNX")
                
            needs_rnx2 = is_any_cggtts or (is_any_rinex and not getattr(args, 'skip_rnx2', False))
            if needs_rnx2:
                node.queue_fetch(tracker, target_dt, "DAILY_BIN")
                
            if is_cggtts_daily:
                next_day_dt = target_dt + timedelta(days=1)
                node.queue_fetch(tracker, next_day_dt, "DAILY_BIN")

        active_fetch_types = set()
        if is_hourly_rinex:
            active_fetch_types.add("HOURLY_RNX")
        if is_daily_rinex:
            active_fetch_types.add("DAILY_RNX")
        if is_any_cggtts or (is_any_rinex and not getattr(args, 'skip_rnx2', False)):
            active_fetch_types.add("DAILY_BIN")
        
        for jt in active_fetch_types:
            # Drain today's files
            drain_fetch_queue(tracker, job_type=jt, target_pattern=target_str)

            # If doing daily CGGTTS, explicitly drain tomorrow's files for boundary stitching
            if is_cggtts_daily:
                next_day_str = (target_dt + timedelta(days=1)).strftime("%Y%j")
                drain_fetch_queue(tracker, job_type=jt, target_pattern=next_day_str)
                
    # -------------------------------------------------------------------------
    # 3. PROCESSING PHASE
    # -------------------------------------------------------------------------
    rinex_worker = RinexWorker(tracker, log, destinations=destinations)
    cggtts_worker = CggttsWorker(tracker, log, destinations=destinations)
    cggtts_jobs = getattr(pipeline_config, 'CGGTTS_JOBS', {})
    distributor = Distributor(tracker, log, destinations_dict=destinations)

    skip_rnx2_flag = getattr(args, 'skip_rnx2', False)

    for node in nodes.values():
        if is_daily_rinex:
            rinex_worker.process(node, "DAILY", target_str, target_dt, skip_rnx2=skip_rnx2_flag)
        if is_hourly_rinex:
            rinex_worker.process(node, "HOURLY", target_str, target_dt)

    if is_any_cggtts:
        for out_id, job_cfg in cggtts_jobs.items():
            cggtts_worker.process(
                out_id=out_id,
                src_id=job_cfg["source"],
                params=job_cfg["params"],
                is_crossover=False,
                target_dt=target_dt,
                nodes_dict=nodes,
                is_daily=is_cggtts_daily, 
                force_retry=is_manual
            )

    # -------------------------------------------------------------------------
    # 3.5 DATA INTEGRITY QA GATE (Controls External Delivery)
    # -------------------------------------------------------------------------
    gate_script = ROOT_DIR / "bin" / "gate_check_cggtts.py"
    gatekeeper = QAGatekeeper(tracker)

    if gate_script.exists():
        log.info("  [ENGINE] Running CGGTTS Quality Assurance Gate...")

        if is_any_rinex and not is_any_cggtts:
            log.info("  [ENGINE] Pure RINEX run detected. Generating on-the-fly CGGTTS for QA validation...")
            for cggtts_id, job_cfg in cggtts_jobs.items():
                cggtts_worker.process(
                    out_id=cggtts_id, src_id=job_cfg["source"], params=job_cfg["params"],
                    is_crossover=False, target_dt=target_dt, nodes_dict=nodes,
                    is_daily=False, force_retry=True 
                )
        
        for cggtts_id, job_cfg in cggtts_jobs.items():
            try:
                # ADDED: timeout=120 to prevent pipeline from hanging indefinitely 
                result = subprocess.run(
                    [sys.executable, str(gate_script), str(OUTBOUND_BUFFER), cggtts_id],
                    capture_output=True, text=True, check=True, timeout=120
                )

                src_node = job_cfg["source"]

                if "[FAIL]" in result.stdout:
                    fail_lines = [line.strip() for line in result.stdout.splitlines() if "[FAIL]" in line]
                    fail_msg = " | ".join(fail_lines) if fail_lines else result.stdout.strip()

                    log.error(f"  [GATE] Integrity FAILED for {cggtts_id.upper()} (Source: {src_node.upper()}): {fail_msg}")
                    tracker.flag_quarantine(src_node, target_str)

                    pending_files = tracker.get_pending_outbound(target_pattern=target_str)
                    q_dir = RAW_BUFFER / "quarantine"
                    q_dir.mkdir(parents=True, exist_ok=True)

                    quarantined_paths = set()

                    for job in pending_files:
                        file_path = Path(job['file_path'])
                        file_name_upper = file_path.name.upper()

                        if src_node.upper() in file_name_upper:
                            if file_path.exists() and file_path not in quarantined_paths:
                                dest_path = q_dir / file_path.name
                                shutil.move(str(file_path), str(dest_path))
                                quarantined_paths.add(file_path)
                                log.warning(f"  [GATE] QUARANTINED: {file_path.name} -> {q_dir.name}/ (Awaiting manual review)")

                            tracker.increment_retry(str(file_path), job['destination'], max_retries=0)

                elif "[PASS]" in result.stdout:
                    raw_output = result.stdout.replace('\n', ' ').strip()
                    log.info(f"  [GATE] QA Integrity for {cggtts_id.upper()}: {raw_output}")

                else:
                    log.warning(f"  [GATE] Unexpected output for {cggtts_id.upper()}: {result.stdout.strip()}")

            except subprocess.TimeoutExpired:
                log.error(f"  [GATE] QA Script TIMEOUT for {cggtts_id.upper()} after 120s.")
            except subprocess.CalledProcessError as e:
                log.error(f"  [GATE] QA Script crashed for {cggtts_id.upper()}: {e.stderr or e.stdout}")
    
    # -------------------------------------------------------------------------
    # 4. DISTRIBUTOR / DELIVERY PHASE
    # -------------------------------------------------------------------------
    log.info("  [ENGINE] Running Distributor Phase...")

    allowed_destinations = set()

    if is_any_cggtts:
        cggtts_cfg = getattr(pipeline_config, 'CGGTTS_JOBS', {})
        for job_id, cfg in cggtts_cfg.items():
            allowed_destinations.update(cfg.get('destinations', []))

    if is_any_rinex:
        rinex_cfg = getattr(pipeline_config, 'RINEX_JOBS', getattr(pipeline_config, 'TARGETS', {}))
        for job_id, cfg in rinex_cfg.items():
            allowed_destinations.update(cfg.get('destinations', []))

    distributor.process_outbound_queue(
        target_pattern=None if is_manual else target_str,
        allowed_destinations=allowed_destinations
    )

def run_pipeline(args, tracker, log, pipeline_config):
    """Main Orchestrator Entrypoint."""
    run_housekeeper(log, tracker)

    targets = parse_target_dates(args)
    for target_dt in targets:
        execute_pass(args, target_dt, tracker, log, pipeline_config)

    from config.gnss_config import OUTBOUND_BUFFER
    purged_count = tracker.cleanup_outbound_buffer(OUTBOUND_BUFFER)
    if purged_count > 0:
        log.info(f"  [ENGINE] Global cleanup: Purged {purged_count} fully delivered artifacts from buffer.")

    log.info("\n[ENGINE] Pipeline run complete.")
