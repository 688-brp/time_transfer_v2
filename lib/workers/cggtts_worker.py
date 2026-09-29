"""
cggtts_worker.py - Generates and stitches CGGTTS time-transfer tracks.
Category: MUTABLE CONNECTOR (< 200 lines)

Pure orchestration layer. Inherits subprocess timeout protection, 
logging, and database routing from BaseWorker.
"""

import sys
import os
import gzip
import shutil
import shlex
from datetime import timedelta, datetime, timezone
from pathlib import Path

from config.gnss_config import LOCAL_BASE_DIR, RAW_BUFFER, WORKSPACE, OUTBOUND_BUFFER
from config.gnss_config import SEPT_CGGTTS, GATE_SCRIPT
from lib.workers.base_worker import BaseWorker


class CggttsWorker(BaseWorker):
    
    def __init__(self, tracker, log, destinations=None):
        super().__init__(tracker, log)
        self.destinations = destinations or {}
        self.workspace = WORKSPACE
        self.outbound = OUTBOUND_BUFFER

    def _find_binary(self, src_id, base_name, date_obj):
        """
        Hunts for the binary using strict, explicitly constructed directory paths.
        Precedence:
          1. Uncompressed binary (_)
          2. Compressed binary (.gz)
        """
        yyyy = date_obj.strftime("%Y")
        yy = date_obj.strftime("%y")
        doy = date_obj.strftime("%j")
        doy_dir = f"{yy}{doy}"
        
        search_dirs = [
            RAW_BUFFER / f"{src_id.lower()}_{yyyy}{doy}",    # <--- Fetcher's dynamic path (e.g. nist_2026268)
            RAW_BUFFER / f"LOG3_{src_id.upper()}" / doy_dir, # Legacy / receiver-native path
            RAW_BUFFER / f"{src_id.lower()}" / doy_dir,
            RAW_BUFFER
        ]
        
        target_upper = base_name.upper()
        target_lower = base_name.lower()

        # 1. Check for uncompressed binary first
        for d in search_dirs:
            if not d.exists():
                continue
            for name in [target_upper, target_lower]:
                p = d / name
                if p.is_file():
                    return p

        # 2. Check for compressed binary (.gz) second
        for d in search_dirs:
            if not d.exists():
                continue
            for name in [f"{target_upper}.gz", f"{target_lower}.gz"]:
                p = d / name
                if p.is_file():
                    return p

        return None

    def _open_binary(self, path):
        """Helper to open either uncompressed or gzipped binary stream."""
        if str(path).endswith('.gz'):
            return gzip.open(path, 'rb')
        return open(path, 'rb')

    def _prepare_inputs(self, sandbox_dir, src_id, target_dt, is_daily, is_crossover):
        """Locates and stitches necessary binaries based on job type."""
        doy = target_dt.strftime("%j")
        yy = target_dt.strftime("%y")

        if is_daily:
            next_dt = target_dt + timedelta(days=1)
            yy2, doy2 = next_dt.strftime("%y"), next_dt.strftime("%j")
            work_binary = sandbox_dir / f"{src_id}{doy}0_2.{yy}_"
            
            bin1 = self._find_binary(src_id, f"{src_id}{doy}0.{yy}_", target_dt)
            bin2 = self._find_binary(src_id, f"{src_id}{doy2}0.{yy2}_", next_dt)
            
            if bin1 and bin2:
                with open(work_binary, 'wb') as wfd:
                    for b_path in [bin1, bin2]:
                        with self._open_binary(b_path) as fd:
                            shutil.copyfileobj(fd, wfd)
                self.log.info("  [CGGTTS] Stitched daily crossover files.")
            elif bin1:
                with open(work_binary, 'wb') as wfd, self._open_binary(bin1) as fd:
                    shutil.copyfileobj(fd, wfd)
                self.log.warning("  [CGGTTS] Missing tomorrow's file. Last track may be incomplete.")
            else:
                return None
                
            return work_binary

        elif is_crossover:
            yest_dt = target_dt - timedelta(days=1)
            y_yy, y_doy = yest_dt.strftime("%y"), yest_dt.strftime("%j")
            work_binary = sandbox_dir / f"{src_id}{y_doy}0_2.{y_yy}_"
            
            bin1 = self._find_binary(src_id, f"{src_id}{y_doy}0.{y_yy}_", yest_dt)
            bin2 = self._find_binary(src_id, f"{src_id}{doy}0.{yy}_", target_dt)
            
            if bin1 and bin2:
                with open(work_binary, 'wb') as wfd:
                    for b_path in [bin1, bin2]:
                        with self._open_binary(b_path) as fd:
                            shutil.copyfileobj(fd, wfd)
                self.log.info("  [CGGTTS] Stitched rapid crossover binaries.")
                return work_binary
            return None

        else: # Standard RAPID
            work_binary = sandbox_dir / f"{src_id}{doy}0.{yy}_"
            bin_src = self._find_binary(src_id, work_binary.name, target_dt)
            if bin_src: 
                if str(bin_src).endswith('.gz'):
                    with gzip.open(bin_src, 'rb') as f_in, open(work_binary, 'wb') as f_out:
                        shutil.copyfileobj(f_in, f_out)
                else:
                    os.symlink(bin_src, work_binary)
                self.log.info("  [CGGTTS] Prepared rapid binary.")
                return work_binary
            return None

    def _execute_generation(self, sandbox_dir, target_binary, out_id, params):
        """Runs sbf2cggtts and the quality control GATE_SCRIPT."""
        cmd = [str(SEPT_CGGTTS), "-f", str(target_binary)] + shlex.split(params)
        success, _ = self.run_subprocess(cmd, timeout=300, cwd=sandbox_dir)
        
        if not success:
            return False

        if GATE_SCRIPT.exists():
            gate_cmd = [sys.executable, str(GATE_SCRIPT), str(sandbox_dir), out_id]
            self.run_subprocess(gate_cmd, timeout=120, cwd=sandbox_dir)
            
        return True

    def _package_and_queue(self, sandbox_dir, out_id, src_id, target_mjd, target_dt, raw_tags, is_crossover, is_daily, qa_only=False):
        """Filters MJD, renames, quarantines if needed, and queues."""
        files_queued = 0
        
        # Check Quarantine
        q_dir = sandbox_dir / "quarantine"
        if q_dir.exists():
            self.log.warning(f"  [CGGTTS] GATE FAILED for {out_id}. Flagging QUARANTINE.")
            self.tracker.flag_quarantine(src_id, target_dt.strftime("%Y%j"))
            local_dest = LOCAL_BASE_DIR / "local_archive" / "quarantine_cggtts"
            local_dest.mkdir(parents=True, exist_ok=True)
            for q_file in q_dir.iterdir():
                shutil.copy2(q_file, local_dest / q_file.name)
            return 0

        for f in list(sandbox_dir.iterdir()):
            if not f.name.startswith(('GZ', 'CZ', 'EZ', 'RZ')): continue
            
            new_path = sandbox_dir / f.name.lower()
            f.rename(new_path)
            
            # File contents filtering (MJD / 23:58 track rescue)
            if is_crossover or is_daily:
                lines = []
                with open(new_path, 'r') as fp:
                    for line in fp:
                        parts = line.split()
                        if len(parts) >= 4 and len(parts[2]) == 5 and parts[2].isdigit():
                            if parts[2] == str(target_mjd) or (parts[2] == str(target_mjd + 1) and parts[3] == "235800"): 
                                lines.append(line)
                        else: 
                            lines.append(line)
                with open(new_path, 'w') as fp: fp.writelines(lines)
                
            # Rename Src -> Out (e.g., nist -> nsa1)
            if src_id.lower() in new_path.name:
                final_path = sandbox_dir / new_path.name.replace(src_id.lower(), out_id.lower())
                new_path.rename(final_path)
                new_path = final_path

            # Routing
            valid_tags = self.tracker.get_valid_tags_for_file(
                new_path.name, 
                raw_tags, 
                destinations_dict=self.destinations
            )
            if not valid_tags:
                self.log.info(f"  [CGGTTS] Discarding {new_path.name} (No matching tags)")
                continue

            final_dest = self.outbound / new_path.name
            shutil.copy2(str(new_path), str(final_dest))
            
            self.log.info(f"  [CGGTTS] Queued {final_dest.name} -> {valid_tags}")
            for tag in valid_tags:
                if not qa_only:
                    self.tracker.register_outbound(str(final_dest), tag)
                files_queued += 1
                
                return files_queued
                
    def process(self, out_id, src_id, params, is_crossover, target_dt, nodes_dict, is_daily=False, force_retry=False, qa_only=False):
        """Main Orchestration Loop."""
        mjd_epoch = datetime(1858, 11, 17, tzinfo=timezone.utc)
        if is_daily:
            target_mjd = (target_dt - mjd_epoch).days
        elif is_crossover:
            target_mjd = ((target_dt - timedelta(days=1)) - mjd_epoch).days
        else:
            target_mjd = (target_dt - mjd_epoch).days

        # Idempotency Check
        # DISABLED: Wildcard was accidentally matching Rapid intraday deliveries.
        # Daily runs must definitively overwrite rapid data.
        #if is_daily and not force_retry:
        #    mjd_str = str(target_mjd)
        #    mjd_pattern = f"{mjd_str[:2]}.{mjd_str[2:]}"
        #    query_str = f"%{out_id.lower()}{mjd_pattern}%"
        #    if self.tracker.is_already_processed(query_str):
        #        self.log.info(f"  [CGGTTS] Target {out_id} for MJD {target_mjd} already processed. Skipping.")
        #        return True

        job_type = "DAILY" if is_daily else "RAPID"
        self.log.info(f"[CGGTTS] Starting {out_id} ({job_type}) (Source: {src_id})")
        
        sandbox_dir = self.workspace / f"run_cggtts_{out_id}_{target_dt.strftime('%j%H')}"
        if sandbox_dir.exists(): shutil.rmtree(sandbox_dir)
        sandbox_dir.mkdir(parents=True)

        try:
            # 1. Inputs
            work_binary = self._prepare_inputs(sandbox_dir, src_id, target_dt, is_daily, is_crossover)
            if not work_binary:
                self.log.warning(f"  [CGGTTS] Missing binary inputs for {out_id}")
                return False

            target_binary = sandbox_dir / work_binary.name.replace(src_id, out_id)
            if not target_binary.exists(): os.symlink(work_binary, target_binary)

            # 2. Execute 
            if not self._execute_generation(sandbox_dir, target_binary, out_id, params):
                self.log.error(f"  [CGGTTS] Binary execution failed for {out_id}.")
                return False

            
            # 3. Package & Queue
            node = nodes_dict.get(src_id)

            # Pure config-driven routing inheritance
            raw_tags = node.dest_daily if is_daily else (node.dest_hourly if node else [])


            files_queued = self._package_and_queue(
                sandbox_dir, out_id, src_id, target_mjd, target_dt, raw_tags, is_crossover, is_daily
            )

            if files_queued == 0:
                self.log.warning(f"  [CGGTTS] No valid CGGTTS files generated for {out_id}")
                
            return files_queued > 0

        except Exception as e:
            self.log.error(f"  [CGGTTS] Processing exception: {e}", exc_info=True)
            return False
            
        finally:
            if sandbox_dir.exists():
                shutil.rmtree(sandbox_dir, ignore_errors=True)
