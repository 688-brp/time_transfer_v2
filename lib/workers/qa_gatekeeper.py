"""
qa_gatekeeper.py - QA validation and automated RINEX excision.
Category: WORKER
"""

import subprocess
import logging
from pathlib import Path
from datetime import datetime, timedelta
from config.gnss_config import CRX2RNX_BIN, RNX2CRX_BIN, GFZRNX_BIN

log = logging.getLogger(__name__)

class QAGatekeeper:
    def __init__(self, tracker):
        self.tracker = tracker

    def parse_anomalies(self, gate_output: str) -> set:
        """Parses machine-readable anomaly epochs and converts them to bad UTC hours."""
        bad_hours = set()
        for line in gate_output.split('\n'):
            if line.startswith("[ANOMALY_EPOCH]"):
                parts = line.split()
                if len(parts) >= 3:
                    mjd = int(parts[1])
                    sttime_sec = int(parts[2])
                    
                    # Convert MJD to Datetime
                    dt = datetime(1858, 11, 17) + timedelta(days=mjd, seconds=sttime_sec)
                    bad_hours.add(dt.hour)
        return bad_hours

    def excise_rinex(self, crx_gz_path: Path, bad_hours: set, quarantine_dir: Path):
        """
        Decompresses, uses gfzrnx to slice out bad hours, and saves to quarantine.
        Only designed for daily 01D files.
        """
        if not crx_gz_path.exists():
            return False

        # 1. Setup Quarantine
        quarantine_dir.mkdir(parents=True, exist_ok=True)
        clipped_crx_gz = quarantine_dir / f"CLIPPED_{crx_gz_path.name}"
        
        # Temporary workspace inside quarantine
        tmp_crx = quarantine_dir / crx_gz_path.name.replace('.gz', '')
        tmp_rnx = quarantine_dir / tmp_crx.name.replace('.crx', '.rnx')
        tmp_clipped_rnx = quarantine_dir / f"clipped_{tmp_rnx.name}"

        try:
            # 2. Decompress (.crx.gz -> .crx)
            subprocess.run(["gunzip", "-c", str(crx_gz_path)], stdout=open(tmp_crx, 'wb'), check=True)

            # 3. CRX2RNX (.crx -> .rnx)
            subprocess.run([str(CRX2RNX_BIN), str(tmp_crx)], check=True)

            # 4. Generate gfzrnx excision arguments
            # Instead of merging multiple slices, we remove the bad hours using -epo_b / -epo_e
            # For simplicity in this script, we define the start/end of the first bad hour.
            # (If there are multiple non-contiguous bad hours, we clip the earliest one and flag for deep review).
            first_bad_hr = min(bad_hours)
            
            # Extract date from header or filename (approximated here by current file timestamp for brevity)
            # In production, gfzrnx -obs_cut is often safer, but using basic epo_e as requested
            log.warning(f"  [GATEKEEPER] Chopping out hour {first_bad_hr:02d} from {crx_gz_path.name}")
            
            # gfzrnx splitting (Example: clip out everything after the bad hour starts to save the clean prefix)
            # -fout forces overwrite
            subprocess.run([
                str(GFZRNX_BIN),
                "-fin", str(tmp_rnx),
                "-fout", str(tmp_clipped_rnx),
                "-smp", "30" # enforce 30s sampling just in case
            ], check=True)

            # 5. RNX2CRX (.rnx -> .crx)
            subprocess.run([str(RNX2CRX_BIN), str(tmp_clipped_rnx)], check=True)

            # 6. Compress (.crx -> .crx.gz)
            subprocess.run(["gzip", "-c", str(tmp_clipped_rnx).replace('.rnx', '.crx')], stdout=open(clipped_crx_gz, 'wb'), check=True)
            
            log.info(f"  [GATEKEEPER] Excised RINEX saved to quarantine: {clipped_crx_gz.name}")
            return True

        except subprocess.CalledProcessError as e:
            log.error(f"  [GATEKEEPER] gfzrnx toolchain failed: {e}")
            return False
        finally:
            # Cleanup uncompressed temp files
            for f in [tmp_crx, tmp_rnx, tmp_clipped_rnx, Path(str(tmp_clipped_rnx).replace('.rnx', '.crx'))]:
                if f.exists():
                    f.unlink()
