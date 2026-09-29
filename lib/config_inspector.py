"""
config_inspector.py - Pre-flight validation and CLI argument assistant.
Category: MUTABLE CONNECTOR (< 150 lines)
"""

import sys
from pathlib import Path

from config.gnss_config import ROOT_DIR
from config.pipeline_targets import STATIONS, DESTINATIONS, CGGTTS_JOBS


class ConfigInspector:
    def __init__(self, stations=STATIONS, destinations=DESTINATIONS):
        self.stations = stations
        self.destinations = destinations
        self._validate_config_integrity()

    def _validate_config_integrity(self):
        """Hard validation: Ensure every destination referenced by stations exists in DESTINATIONS."""
        missing = []
        for st, cfg in self.stations.items():
            for mode in ["hourly_dest", "daily_dest"]:
                for dest in cfg.get(mode, []):
                    if dest not in self.destinations:
                        missing.append(f"Station '{st}' ({mode}) -> Missing Destination '{dest}'")
        if missing:
            print("\n[CRITICAL CONFIG ERROR] pipeline_targets.py has broken references:")
            for err in missing:
                print(f"  - {err}")
            sys.exit(1)

    def get_active_destinations(self, rinex_hourly=False, rinex_daily=False, cggtts=False, cggtts_daily=False):
        """Resolves active destination keys for a given set of requested tasks."""
        active_dests = set()
        
        for st, cfg in self.stations.items():
            if rinex_hourly or cggtts:
                active_dests.update(cfg.get("hourly_dest", []))
            if rinex_daily or cggtts_daily:
                active_dests.update(cfg.get("daily_dest", []))
                
        return active_dests

    def validate_cli_args(self, args, raw_argv):
        """Cross-references CLI flags against active tasks and enforces system constraints."""
        dispatcher_path = ROOT_DIR / "bin" / "gnss_dispatcher.py"
        issues = []
        suggested_flags = [arg for arg in raw_argv[1:]]

        task_flags = [
            getattr(args, 'cggtts', False),
            getattr(args, 'cggtts_daily', False),
            getattr(args, 'rinex_hourly', False),
            getattr(args, 'rinex_daily', False),
            getattr(args, 'status', False),
            getattr(args, 'reset_retries', False)
        ]

        # 1. Enforce at least one operational task selected
        if not any(task_flags):
            print("\n=================== CLI VALIDATION ERROR ===================")
            print("[ERROR] No active operation selected.")
            print("Please supply a task flag (e.g., --cggtts, --cggtts-daily, --rinex-hourly, --rinex-daily, --status).")
            print("============================================================\n")
            sys.exit(1)

        # 2. Prevent conflicting task modes
        core_tasks = [
            getattr(args, 'cggtts', False),
            getattr(args, 'cggtts_daily', False),
            getattr(args, 'rinex_hourly', False),
            getattr(args, 'rinex_daily', False)
        ]
        if sum(bool(x) for x in core_tasks) > 1:
            print("\n=================== CLI VALIDATION ERROR ===================")
            print("[ERROR] Multiple core task modes selected simultaneously.")
            print("Please specify exactly ONE task flag per command.")
            print("============================================================\n")
            sys.exit(1)

        # 3. Resolve active destinations for requested tasks
        active_dests = self.get_active_destinations(
            rinex_hourly=getattr(args, 'rinex_hourly', False),
            rinex_daily=getattr(args, 'rinex_daily', False),
            cggtts=getattr(args, 'cggtts', False),
            cggtts_daily=getattr(args, 'cggtts_daily', False)
        )

        # 4. Check CDDIS bypass validity (--skip-cddis)
        skip_cddis = getattr(args, 'skip_cddis', False)
        has_cddis_target = "CDDIS" in active_dests
        if skip_cddis and not has_cddis_target:
            issues.append(
                "[MISCONFIGURATION] CDDIS skip flag was passed, but none of the requested tasks target CDDIS.\n"
                "  -> CGGTTS and local archives do not route to CDDIS."
            )
            if "--skip-cddis" in suggested_flags:
                suggested_flags.remove("--skip-cddis")

        # 5. Check --local-only validity
        remote_protocols = {"FTP", "HTTPS_POST", "SFTP"}
        active_protocols = {self.destinations[d].get("protocol") for d in active_dests if d in self.destinations}
        has_remote = bool(active_protocols.intersection(remote_protocols))

        local_only = getattr(args, 'local_only', False)
        if local_only and not has_remote:
            issues.append(
                "[REDUNDANT FLAG] Local-only flag was passed, but active tasks only target local disk destinations."
            )
            if "--local-only" in suggested_flags:
                suggested_flags.remove("--local-only")

        # Output diagnostics and corrected command if issues exist
        if issues:
            print("\n=================== CLI VALIDATION ASSISTANT ===================")
            for issue in issues:
                print(f"{issue}\n")
            
            corrected_cmd = f"{dispatcher_path} {' '.join(suggested_flags)}"
            print("SUGGESTED CORRECT COMMAND:")
            print(f"  {corrected_cmd}")
            print("================================================================\n")

    def print_route_matrix(self):
        """Read-only breakdown of task-to-destination mappings derived from config."""
        print("\n================ CONFIGURATION ROUTING MATRIX ================")
        for st, cfg in self.stations.items():
            print(f"\nSTATION: {st.upper()} ({cfg['host']})")
            print(f"  Hourly Dest ({cfg['hourly_mode']}): {', '.join(cfg['hourly_dest'])}")
            print(f"  Daily Dest  ({cfg['daily_mode']}):  {', '.join(cfg['daily_dest'])}")
            
        print("\nDESTINATION PROTOCOLS:")
        for dest, cfg in self.destinations.items():
            print(f"  - {dest:<25} -> Protocol: {cfg['protocol']:<10} Path: {cfg['path']}")
        print("================================================ me===============\n")
