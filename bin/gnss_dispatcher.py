#!/home/denali/miniforge3/bin/python3
"""
gnss_dispatcher.py - CLI entry point, argument router, and process locker.
Category: MUTABLE CONNECTOR (< 100 lines)
"""

import os
import sys
import fcntl
import logging
import argparse
from pathlib import Path
from datetime import datetime, timezone

# Enforce project root in sys.path BEFORE importing local packages
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import config.pipeline_targets as pipeline_config
import config.gnss_config as gnss_config
from config.gnss_config import LOG_DIR
from lib.tracker import PipelineTracker
from lib.engine import run_pipeline, _parse_time_str
from lib.config_inspector import ConfigInspector

# Logging setup
log_file = LOG_DIR / f"gnss_dispatcher_{datetime.now(timezone.utc).strftime('%Y%m%d')}.log"
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.FileHandler(log_file), logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger("Dispatcher")

# Lock file environment isolation
env_suffix = f"_{os.environ.get('GNSS_ENV')}" if os.environ.get("GNSS_ENV") else ""
LOCK_FILE = f"/tmp/gnss_dispatcher{env_suffix}.lock"


def build_parser() -> argparse.ArgumentParser:
    description_text = """
================================================================================
        GNSS DATA PROCESSING & TIME-TRANSFER DISPATCHER ENGINE
================================================================================

OPERATIONAL PHILOSOPHY:
  1. AUTOMATED STEADY-STATE (CRON):
     Routine background tasks operate unattended without manual flags. They auto-
     target active operational windows, read/write state to 'pipeline_queue.db',
     and handle end-to-end ingestion, processing, and remote distribution.

  2. EXPLICIT MANUAL OVERRIDES:
     Manual backfills, dry runs, or pipeline repairs REQUIRE explicit flags
     (e.g., --range, --no-fetch, --local-only). Passing a manual flag triggers 
     target-scoped state resets, clears local ledger locks, and auto-reconciles
     missing scratch buffer entries without leaving database debt behind.

  3. AUDIT TRAIL & LOGGING:
     All pipeline operations maintain detailed date-stamped log files in LOG_DIR.
     Every successful file download and remote upload is permanently recorded
     in the SQLite 'delivery_audit' ledger.
"""

    epilog_text = """
EXEMPLARY USAGE SCENARIOS:

  [1] Routine Cron Execution (Unattended Background Operations)
      # Hourly RINEX ingestion & CDDIS upload (runs at :10 past every hour)
      $ gnss_dispatcher.py --rinex-hourly

      # Official daily CGGTTS generation with Day N+1 boundary stitching
      $ gnss_dispatcher.py --cggtts-daily

  [2] Targeted Manual Backfills & Historical Repairs
      # Single-hour backfill for September 28, 2026 @ 23:00 UTC
      $ gnss_dispatcher.py --rinex-hourly --range 2026-09-28T23:00:00

      # Multi-day range backfill skipping legacy RINEX 2 generation for speed
      $ gnss_dispatcher.py --rinex-daily --range 2026-09-20 2026-09-28 --skip-rnx2

  [3] Dry-Runs, Local Testing & Network Bypasses
      # Process local binary logs already in scratch without fetching or uploading
      $ gnss_dispatcher.py --rinex-hourly --range 2026-09-28T12:00:00 --no-fetch --local-only

      # Process RINEX hourly files and update local archives, but bypass CDDIS
      $ gnss_dispatcher.py --rinex-hourly --range 2026-09-28T18:00:00 --skip-cddis

  [4] Pipeline Queue & State Administration
      # Display active queue counts across inbound, outbound, and audit stores
      $ gnss_dispatcher.py --status

      # Reset FAILED attempts back to PENDING for a targeted date window
      $ gnss_dispatcher.py --reset-retries --range 2026-09-28

LOGGING & AUDIT REFERENCE:
  Logs Directory : /home/denali/programs/gnss/time_transfer_v2/logs/
  SQLite Ledger  : /home/denali/programs/gnss/time_transfer_v2/data_base/pipeline_queue.db
================================================================================
"""

    parser = argparse.ArgumentParser(
        prog="gnss_dispatcher.py",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=description_text,
        epilog=epilog_text
    )

    # --- Group 1: Cron Steady-State Operations ---
    cron_grp = parser.add_argument_group("Automated Tasks (Cron Steady-State)")
    cron_grp.add_argument(
        "--rinex-hourly", action="store_true",
        help="Execute hourly RINEX 3/2 processing and deliver products to CDDIS."
    )
    cron_grp.add_argument(
        "--rinex-daily", action="store_true",
        help="Execute 24-hour daily RINEX packaging for long-term archiving."
    )
    cron_grp.add_argument(
        "--cggtts", action="store_true",
        help="Execute rapid intraday CGGTTS time-transfer generation (no stitching)."
    )
    cron_grp.add_argument(
        "--cggtts-daily", action="store_true",
        help="Execute official daily CGGTTS generation with Day N+1 boundary stitching."
    )

    # --- Group 2: Manual Overrides & Backfills ---
    man_grp = parser.add_argument_group("Manual Routing & Backfills (Override Mode)")
    man_grp.add_argument(
        "--range", nargs='+', type=str, metavar='START [STOP]',
        help="Manual target window: ISO timestamp (YYYY-MM-DDTHH:MM:SS) or date (YYYY-MM-DD). Clears target locks."
    )
    man_grp.add_argument(
        "--no-fetch", action="store_true",
        help="Bypass remote rsync downloads; process binary logs already staging in scratch."
    )
    man_grp.add_argument(
        "--local-only", action="store_true",
        help="Bypass all remote network transmissions; stage products in local archives only."
    )
    man_grp.add_argument(
        "--skip-cddis", action="store_true",
        help="Bypass transmission to CDDIS while maintaining local and secondary distributions."
    )
    man_grp.add_argument(
        "--skip-rnx2", action="store_true",
        help="Bypass legacy RINEX 2 compression to accelerate high-volume backfill passes."
    )
    man_grp.add_argument(
        "--manual", action="store_true",
        help="Explicitly flag run as a manual intervention to trigger state auto-reconciliation."
    )

    # --- Group 3: Administrative Operations ---
    admin_grp = parser.add_argument_group("Administrative Operations")
    admin_grp.add_argument(
        "--status", action="store_true",
        help="Query SQLite queue state, print execution summary table, and exit immediately."
    )
    admin_grp.add_argument(
        "--reset-retries", action="store_true",
        help="Reset all FAILED attempts to 0 for a target window. (Requires --range)."
    )

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    # =========================================================================
    # PRE-FLIGHT & CONFIGURATION
    # =========================================================================
    inspector = ConfigInspector()
    inspector.validate_cli_args(args, sys.argv)

    # Instantiate Tracker using from_args to route live vs shadow database correctly
    tracker = PipelineTracker.from_args(args)

    # =========================================================================
    # ADMINISTRATIVE OPERATIONS (Immediate Exit)
    # =========================================================================
    if args.status:
        print("\n=== GNSS PIPELINE QUEUE STATUS ===")
        summary = tracker.get_status_summary()
        if not summary:
            print("  Queue is currently empty.")
        else:
            for stat, count in summary:
                print(f"  {stat:<15}: {count}")
        print("===================================\n")
        sys.exit(0)

    if args.reset_retries:
        if not args.range:
            parser.error("Admin Fault: --reset-retries requires a target window via --range.")
        target_dt = _parse_time_str(args.range[0])
        pattern = target_dt.strftime("%Y%j")
        count = tracker.reset_failed_jobs(pattern)
        print(f"[ADMIN] Reset {count} FAILED jobs for pattern {pattern}")
        sys.exit(0)

    # =========================================================================
    # OS FILE LOCKING & PIPELINE EXECUTION
    # =========================================================================
    lock_fd = open(LOCK_FILE, 'w')
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (IOError, BlockingIOError):
        log.warning("Dispatcher already running. Aborting overlapping instance.")
        sys.exit(0)

    try:
        run_pipeline(args, tracker, log, pipeline_config)
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        lock_fd.close()


if __name__ == "__main__":
    main()
