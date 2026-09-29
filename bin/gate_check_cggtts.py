#!/home/denali/miniforge3/bin/python3
import sys
import os
import shutil
import functools
from pathlib import Path

# Force output to appear immediately (unbuffered for real-time logging/pipes)
print = functools.partial(print, flush=True)

# Dynamically locate project root (time_transfer_v2) regardless of current working directory
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.pipeline_targets import GATE_LIMITS

# --- MATH LOGIC ---
def check_file_metrics(filepath, limits, sv_match=1):
    if not limits: return True, "No limits"
    
    column_map = {}
    valid_starts = ('G',) # GPS satellite IDs for 'gz' files

    try:
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()

        # 1. FIND HEADER
        header_found = False
        for line in lines:
            u_line = line.upper()
            if 'SAT' in u_line and 'MJD' in u_line and ('STTIME' in u_line or 'TRKL' in u_line):
                headers = line.strip().split()
                for idx, col in enumerate(headers):
                    column_map[col.upper()] = idx
                header_found = True
                break
        
        if not header_found:
            print(f"   [WARN] No standard header. Using fallback mapping.")
            column_map = {'MJD': 2, 'STTIME': 3, 'REFSYS': 9, 'SRSRS': 10, 'DSG': 11}

        # Ensure we have the time columns to define an epoch
        if 'MJD' not in column_map or 'STTIME' not in column_map:
            return False, "CRITICAL: MJD or STTIME columns missing. Cannot group by epoch."

        mjd_idx = column_map['MJD']
        sttime_idx = column_map['STTIME']

        # 2. PARSE DATA & GROUP BY EPOCH
        row_count = 0
        epoch_violations = {} 
        
        max_observed = {k: 0.0 for k in limits}
        peak_sv_fails = {k: 0 for k in limits}

        for line in lines:
            line = line.strip()
            if not line: continue
            
            cols = line.split()
            if len(cols) < 8: continue
            
            if not cols[0].startswith(valid_starts):
                continue

            row_count += 1
            
            try:
                epoch_key = f"{cols[mjd_idx]}_{cols[sttime_idx]}"
            except IndexError:
                continue # Malformed row

            if epoch_key not in epoch_violations:
                epoch_violations[epoch_key] = {k: 0 for k in limits}

            for metric, limit in limits.items():
                candidates = [metric.upper(), metric]
                col_idx = -1
                for c in candidates:
                    if c in column_map:
                        col_idx = column_map[c]
                        break
                
                if col_idx != -1 and col_idx < len(cols):
                    try:
                        # Convert CGGTTS 0.1 ns units to standard ns
                        val = float(cols[col_idx]) * 0.1
                        abs_val = abs(val)
                        
                        if abs_val > max_observed[metric]:
                            max_observed[metric] = abs_val
                            
                        if abs_val > limit:
                            epoch_violations[epoch_key][metric] += 1
                    except ValueError: 
                        continue

        # 3. CALCULATE FAILURES AGAINST SV_MATCH
        if row_count == 0:
            return False, "File contains no valid SV data rows."

        failed = False
        reasons = []
        
        for epoch, metric_counts in epoch_violations.items():
            for metric, fail_count in metric_counts.items():
                
                if fail_count > peak_sv_fails[metric]:
                    peak_sv_fails[metric] = fail_count
                    
                if fail_count >= sv_match:
                    failed = True
                    reasons.append(f"{metric} > {limits[metric]} for {fail_count} SVs at epoch {epoch}")
                    
                    try:
                        mjd, sttime = epoch.split('_', 1)
                        print(f"[ANOMALY_EPOCH] {mjd} {sttime}")
                    except ValueError:
                        print(f"[WARN] Malformed epoch key '{epoch}'. Could not parse MJD and STTIME for Gatekeeper.")

        stats_str = "(" + " | ".join([f"{k} Max: {max_observed[k]:.1f} (Lim: {limits[k]}), SVs > Lim: {peak_sv_fails[k]}/{sv_match}" for k in limits]) + ")"

        if failed:
            error_msg = " | ".join(reasons[:3]) + ("..." if len(reasons) > 3 else "")
            return False, f"{error_msg} {stats_str}"

        return True, f"OK {stats_str}"

    except Exception as e:
        return False, f"Read Error: {e}"


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("[FAIL] Missing arguments. Usage: gate_check.py <dir> <id>")
        sys.exit(1)

    work_dir = sys.argv[1]
    out_id = sys.argv[2].lower()

    if out_id not in GATE_LIMITS:
        print(f"[PASS] BYPASSED (No limits configured for {out_id.upper()})")
        sys.exit(0)

    limits = GATE_LIMITS[out_id].get("metrics", {})
    sv_match = GATE_LIMITS[out_id].get("sv_match", 1)

    # Exact pattern match: GPS CGGTTS files start strictly with 'gz' + out_id (e.g. 'gznisx')
    target_prefix = f"gz{out_id}".lower()

    if not os.path.exists(work_dir):
        print(f"[FAIL] Directory missing: {work_dir}")
        sys.exit(1)

    files = [
        os.path.join(work_dir, f) for f in os.listdir(work_dir)
        if f.lower().startswith(target_prefix) and not f.endswith('_') and not f.startswith('.')
    ]

    if not files:
        print(f"[PASS] BYPASSED (No GPS CGGTTS files found matching prefix '{target_prefix}' in {work_dir})")
        sys.exit(0)

    # Process matching files
    final_reason = ""
    for filepath in files:
        passed, reason = check_file_metrics(filepath, limits, sv_match)
        if not passed:
            print(f"[FAIL] {reason}")
            sys.exit(0)  # Fail immediately on first bad file
        final_reason = reason

    print(f"[PASS] {final_reason}")
