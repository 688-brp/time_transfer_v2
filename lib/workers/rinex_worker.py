"""
rinex_worker.py - Generates and routes RINEX 2 & 3 observation files.
Category: MUTABLE CONNECTOR (< 200 lines)

Pure orchestration layer. Inherits subprocess timeout protection, 
logging, and database routing from BaseWorker.
"""

import re
import shutil
from pathlib import Path

from config.gnss_config import LOCAL_BASE_DIR, RAW_BUFFER, WORKSPACE, OUTBOUND_BUFFER
from config.gnss_config import SEPT_RINEX, GFZRNX_BIN, RNX2CRX_BIN, LEGACY_STATIONS
from lib.workers.base_worker import BaseWorker


class RinexWorker(BaseWorker):
    
    def __init__(self, tracker, log, destinations=None):
        super().__init__(tracker, log)
        self.destinations = destinations or {}

    def _is_valid_r3(self, file_name, node_name, target_str, job_type):
        """Deterministically validates a file based on node, target string, and duration."""
        if node_name.upper() not in file_name.upper(): return False
        if target_str.lower() not in file_name.lower(): return False
        
        if "_R_" in file_name.upper():
            if job_type == "DAILY" and "_01D_" not in file_name.upper(): return False
            if job_type == "HOURLY" and "_01H_" not in file_name.upper(): return False
        return True

    def _find_inputs(self, node_name, target_str, yy, doy, job_type):
        """
        Hunts down raw SBF binaries or native RINEX files across known buffer schemas.
        Precedence for binary resolution:
          1. Uncompressed binary file (`_`)
          2. Compressed binary file (`.gz`)
        """
        doy_dir = f"{yy}{doy}"
        bin_lower = f"{node_name.lower()}{doy}0.{yy}_"
        bin_upper = f"{node_name.upper()}{doy}0.{yy}_"
        
        # The new dynamic directory created by the rsync fetcher
        rsync_dir = RAW_BUFFER / f"{node_name.lower()}_{target_str}"
        
        # 1. Find Raw SBF Binary (Uncompressed `_` first, then compressed `.gz`)
        bin_dirs = [
            rsync_dir,
            RAW_BUFFER / f"{node_name.lower()}" / doy_dir,
            RAW_BUFFER / f"LOG3_{node_name.upper()}" / doy_dir,
            RAW_BUFFER
        ]
        
        binary_path = None
        for b_dir in bin_dirs:
            if not b_dir.exists():
                continue
            
            # Explicit search order: Uncompressed first, Compressed second
            uncompressed = [f for f in b_dir.glob(f"{bin_upper}*") if not f.name.endswith('.gz')] + \
                           [f for f in b_dir.glob(f"{bin_lower}*") if not f.name.endswith('.gz')]
            compressed   = [f for f in b_dir.glob(f"{bin_upper}*.gz")] + \
                           [f for f in b_dir.glob(f"{bin_lower}*.gz")]
            
            matches = uncompressed or compressed
            if matches:
                binary_path = matches[0]
                break

        # 2. Find Native RINEX Candidates
        native_candidates = []
        strict_dirs = [
            rsync_dir,
            RAW_BUFFER / f"LOG1_{node_name.upper()}" / doy_dir,
            RAW_BUFFER / f"LOG2_{node_name.upper()}" / doy_dir,
            RAW_BUFFER / f"LOG3_{node_name.upper()}" / doy_dir,
            RAW_BUFFER / f"{node_name.lower()}" / doy_dir,
            RAW_BUFFER 
        ]
        
        for explicit_dir in strict_dirs:
            if not explicit_dir.exists(): 
                continue
            for f in explicit_dir.iterdir():
                if f.is_file() and self._is_valid_r3(f.name, node_name, target_str, job_type):
                    native_candidates.append(f)
                    
        return binary_path, native_candidates
    
    def _prepare_binary_in_sandbox(self, sandbox_dir, binary_path):
        """Copies binary to sandbox and uncompresses if .gz so sbf2rin can process it."""
        if not binary_path or not binary_path.exists():
            return None
        
        local_bin = sandbox_dir / binary_path.name
        shutil.copy2(binary_path, local_bin)
        
        if local_bin.name.endswith('.gz'):
            self.run_subprocess(["gzip", "-d", "-f", str(local_bin)], timeout=120, cwd=sandbox_dir)
            unzipped_name = local_bin.name[:-3]
            local_bin = sandbox_dir / unzipped_name
            
        return local_bin if local_bin.exists() else None

    def _generate_rinex3(self, sandbox_dir, prepared_binary, native_candidates, node_name, job_type, is_legacy):
        """Handles legacy sbf2rin execution and gfzrnx splitting, or native passthrough."""
        if is_legacy:
            if not prepared_binary:
                return False
            self.log.info(f"  [RINEX] Executing legacy sbf2rin (RINEX 3) for {node_name}")
            cmd_sbf = [str(SEPT_RINEX), "-f", str(prepared_binary), "-m", node_name.upper(), "-i30", "-R3", "-nONPE"]
            if not self.run_subprocess(cmd_sbf, timeout=300, cwd=sandbox_dir)[0]: return False
            
            duration = "3600" if job_type == "HOURLY" else "86400"
            rnx3_files = [f for f in sandbox_dir.iterdir() if re.search(r'(\.\d{2}[onplgem]$)\vert{}(\.rnx$)', f.name, re.IGNORECASE)]
            
            for src in rnx3_files:
                cmd_gfz = [str(GFZRNX_BIN), "-finp", str(src), "-f", "-fout", f"{sandbox_dir}/::RX3::00,USA", "-split", duration, "-vo", "3.03"]
                self.run_subprocess(cmd_gfz, timeout=120, cwd=sandbox_dir)
        else:
            self.log.info(f"  [RINEX] Passthrough native RINEX 3 for {node_name}")
            for f_src in native_candidates:
                shutil.copy2(f_src, sandbox_dir / f_src.name)
        return True

    def _generate_rinex2(self, sandbox_dir, prepared_binary, node_name):
        """Handles daily RINEX 2 generation from raw SBF binary."""
        if not prepared_binary:
            self.log.warning(f"  [RINEX] Cannot generate RINEX 2: No raw SBF binary available for {node_name}")
            return False
        self.log.info(f"  [RINEX] Executing sbf2rin (RINEX 2) for {node_name}")
        cmd = [str(SEPT_RINEX), "-f", str(prepared_binary), "-m", node_name.upper(), "-i30", "-R2", "-nONPE"]
        return self.run_subprocess(cmd, timeout=300, cwd=sandbox_dir)[0]

    def _package_and_queue(self, sandbox_dir, node, target_str, job_type, date_obj):
        """Filters, compresses (Hatanaka/Gzip), checks quarantine, and registers with tracker."""
        yy, doy = date_obj.strftime("%y"), date_obj.strftime("%j")
        r2_target_base = f"{node.name.lower()}{doy}0.{yy}"
        is_bad = self.tracker.is_quarantined(node.name, date_obj.strftime("%Y%j"))
        raw_tags = node.dest_daily if job_type == "DAILY" else node.dest_hourly
        
        files_queued = 0

        for f in list(sandbox_dir.iterdir()):
            if not f.is_file(): continue
            
            is_r3 = self._is_valid_r3(f.name, node.name, target_str, job_type)
            is_r2 = (job_type == "DAILY") and (r2_target_base.lower() in f.name.lower())
            
            if not (is_r3 or is_r2): 
                continue # Ignore files that don't match strict patterns
                
            out_file = f
            
            # Hatanaka & GZIP for R3
            if is_r3 and f.suffix.lower() in ['.rnx', '.crx']:
                if f.suffix.lower() == '.rnx':
                    self.run_subprocess([str(RNX2CRX_BIN), "-f", str(f)], timeout=60, cwd=sandbox_dir)
                    crx_file = f.with_suffix('.crx')
                    if crx_file.exists(): out_file = crx_file
                
                self.run_subprocess(["gzip", "-f", str(out_file)], timeout=60, cwd=sandbox_dir)
                out_file = out_file.with_suffix(out_file.suffix + '.gz')

            # Lowercase R2
            if is_r2 and out_file.name != out_file.name.lower():
                lower_file = sandbox_dir / out_file.name.lower()
                out_file.rename(lower_file)
                out_file = lower_file

            if not out_file.exists(): continue

            # Routing
            if is_bad:
                local_dest = LOCAL_BASE_DIR / "local_archive" / "quarantine_rinex" / out_file.name
                local_dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(out_file), str(local_dest))
                self.log.warning(f"  [RINEX] QUARANTINED: {out_file.name}")
                files_queued += 1
            else:
                valid_tags = self.tracker.get_valid_tags_for_file(
                    out_file.name, 
                    raw_tags, 
                    destinations_dict=self.destinations
                )
                if not valid_tags:
                    self.log.info(f"  [RINEX] Discarding {out_file.name} (No matching destination filters)")
                    continue
                    
                final_dest = OUTBOUND_BUFFER / out_file.name
                shutil.copy2(str(out_file), str(final_dest))
                self.log.info(f"  [RINEX] Queued {final_dest.name} -> {valid_tags}")
                
                for tag in valid_tags:
                    self.tracker.register_outbound(str(final_dest), tag)
                files_queued += 1
                
        return files_queued > 0

    def process(self, node, job_type, target_str, date_obj, skip_rnx2=False ):
        """Main Orchestration Loop."""
        self.log.info(f"[RINEX] Starting {node.name.upper()} ({job_type}) for {target_str}")
        
        # 1. Idempotency Check
        duration_tag = "_01D_" if job_type == "DAILY" else "_01H_"
        query_str = f"%{node.name.upper()}%{target_str}%{duration_tag}%"
        if self.tracker.is_already_processed(query_str):
            self.log.info(f"  [RINEX] Target already in database. Skipping.")
            return True

        # 2. Input Resolution
        yy, doy = date_obj.strftime("%y"), date_obj.strftime("%j")
        binary_path, native_cands = self._find_inputs(node.name, target_str, yy, doy, job_type)
        is_legacy = node.name.lower() in LEGACY_STATIONS
        
        if is_legacy and not binary_path:
            self.log.warning(f"  [RINEX] Missing raw binary for legacy {node.name}")
            return False
        if not is_legacy and not native_cands:
            self.log.warning(f"  [RINEX] Missing native RINEX for {node.name}")
            return False

        # 3. Sandbox Setup
        sandbox_dir = WORKSPACE / f"run_rinex_{node.name}_{target_str}"
        if sandbox_dir.exists(): shutil.rmtree(sandbox_dir)
        sandbox_dir.mkdir(parents=True)

        try:
            # Copy and uncompress binary into sandbox if found
            prepared_binary = self._prepare_binary_in_sandbox(sandbox_dir, binary_path) if binary_path else None

            # 4. RINEX 3 Generation
            self._generate_rinex3(sandbox_dir, prepared_binary, native_cands, node.name, job_type, is_legacy)
           
            # 5. RINEX 2 Generation (Daily Only)
            if job_type == "DAILY":
                if skip_rnx2:
                    self.log.info(f"  [RINEX] Skipping RINEX 2 generation for {node.name} (--skip-rnx2 flag passed).")
                elif prepared_binary:
                    self._generate_rinex2(sandbox_dir, prepared_binary, node.name)
                elif is_legacy:
                    # Legitimate warning: Legacy stations MUST have a binary to make RINEX 2
                    self.log.warning(f"  [RINEX] Cannot generate RINEX 2 for {node.name}: Required raw SBF binary is missing.")
                else:
                    # Quiet bypass: Native stations without a binary don't need a warning
                    self.log.info(f"  [RINEX] Bypassing RINEX 2 generation for {node.name} (Native station, no raw binary provided).")
                
            # 6. Packaging, Filtering, and Tracking
            success = self._package_and_queue(sandbox_dir, node, target_str, job_type, date_obj)
            
            if not success:
                self.log.warning(f"  [RINEX] Zero valid files generated for {node.name}")
            return success
            
        except Exception as e:
            self.log.error(f"  [RINEX] Processing exception: {e}", exc_info=True)
            return False
            
        finally:
            # 7. Guaranteed Cleanup
            if sandbox_dir.exists():
                shutil.rmtree(sandbox_dir, ignore_errors=True)
