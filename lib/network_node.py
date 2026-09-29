"""
network_node.py - Hardware node definitions and path resolution.
Category: IMMUTABLE CORE (Append-only)
"""

from pathlib import Path
from config.gnss_config import RAW_BUFFER, LEGACY_STATIONS


class NetworkNode:
    def __init__(self, name, data):
        self.name = name.lower()
        self.host = data.get("host", "")
        self.mode = data.get("mode", "HOURLY").upper()
        self.dest_hourly = data.get("hourly_dest", [])
        self.dest_daily = data.get("daily_dest", [])

        if self.name in LEGACY_STATIONS:
            self.remote_path = data.get("legacy_path", "")
        else:
            self.remote_path = data.get("modern_path", "/home/tftdata/sept_log")
    
    def queue_fetch(self, tracker, date_obj, job_type: str):
        """
        Translates job requests into rsync remote targets and queues them in SQLite.

        Stream Mapping (Modern Septentrio):
          - HOURLY_RNX -> LOG1_<STATION> (_01H_ hourly RINEX 3)
          - DAILY_RNX  -> LOG2_<STATION> (_01D_ daily RINEX 3)
          - DAILY_BIN  -> LOG3_<STATION> (.26_ / .26_.gz raw SBF binary)
        """
        yy = date_obj.strftime("%y")
        yyyy = date_obj.strftime("%Y")
        doy = date_obj.strftime("%j")
        hh = date_obj.strftime("%H")
        doy_dir = f"{yy}{doy}"
        upper_name = self.name.upper()

        target_pattern = date_obj.strftime("%Y%j%H") if job_type == "HOURLY_RNX" else date_obj.strftime("%Y%j")

        # 1. Legacy Station Handling
        if self.name in LEGACY_STATIONS:
            filename = f"{self.name}{doy}0.{yy}_*"
            remote_src = f"{self.host}:{self.remote_path}/{doy_dir}/{filename}"

        # 2. Modern Septentrio Receiver Stream Mapping
        else:
            if job_type == "HOURLY_RNX":
                filename = f"{upper_name}00USA_R_{yyyy}{doy}{hh}00_01H_*.gz"
                remote_src = f"{self.host}:{self.remote_path}/LOG1_{upper_name}/{doy_dir}/{filename}"

            elif job_type == "DAILY_RNX":
                filename = f"{upper_name}00USA_R_{yyyy}{doy}0000_01D_*.gz"
                remote_src = f"{self.host}:{self.remote_path}/LOG2_{upper_name}/{doy_dir}/{filename}"

            elif job_type == "DAILY_BIN":
                filename = f"{upper_name}{doy}0.{yy}_*"
                remote_src = f"{self.host}:{self.remote_path}/LOG3_{upper_name}/{doy_dir}/{filename}"

            else:
                raise ValueError(f"Invalid fetch job_type: {job_type}")

        # Local scratch destination path
        local_dest = str(RAW_BUFFER / f"{self.name}_{target_pattern}")

        # Enqueue in SQLite ledger
        tracker.register_fetch(
            station=self.name,
            job_type=job_type,
            target_str=target_pattern,
            remote_src=remote_src,
            local_dest=local_dest
        )
