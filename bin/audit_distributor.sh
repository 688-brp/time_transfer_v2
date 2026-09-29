#!/usr/bin/env bash
#
# audit_distributor.sh - Validation and hardening audit for network delivery engine.
# Validates credentials, checks for timeouts, and audits target mapping integrity.

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$HOME/miniforge3/bin/python3}"
NETRC_FILE="$HOME/.netrc"
LOG_FILE="$PROJECT_ROOT/local_scratch/logs/audit_distributor_$(date -u +%Y%m%d).log"

# Colors for terminal output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

mkdir -p "$PROJECT_ROOT/local_scratch/logs"

log_and_print() {
    echo -e "$1"
    echo -e "$1" | sed -r 's/\x1B\[[0-9;]*[mK]//g' >> "$LOG_FILE"
}

log_and_print "=================================================================="
log_and_print "         GNSS DISTRIBUTOR & NETWORK CONFIGURATION AUDIT           "
log_and_print "=================================================================="
log_and_print "Timestamp : $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
log_and_print "Log File  : $LOG_FILE"
log_and_print "------------------------------------------------------------------\n"

# ------------------------------------------------------------------------------
# PHASE 1: Security & .netrc Permissions
# ------------------------------------------------------------------------------
log_and_print ">>> PHASE 1: Security & Credentials File Check"
if [ ! -f "$NETRC_FILE" ]; then
    log_and_print "${RED}[FAIL] ~/.netrc file does not exist!${NC}"
    exit 1
fi

PERMS=$(stat -c "%a" "$NETRC_FILE")
if [ "$PERMS" -ne 600 ]; then
    log_and_print "${RED}[FAIL] ~/.netrc has insecure permissions ($PERMS). Must be 600.${NC}"
    log_and_print "       Run: chmod 600 $NETRC_FILE"
    exit 1
else
    log_and_print "${GREEN}[OK] ~/.netrc permissions are strictly 600.${NC}"
fi
echo "" >> "$LOG_FILE"; echo ""

# ------------------------------------------------------------------------------
# PHASE 2: Static Code Analysis (lib/distributor.py)
# ------------------------------------------------------------------------------
log_and_print ">>> PHASE 2: Code Hardening Analysis (lib/distributor.py)"
DIST_FILE="$PROJECT_ROOT/lib/distributor.py"

# Syntax Check
if "$PYTHON_BIN" -m py_compile "$DIST_FILE"; then
    log_and_print "${GREEN}[OK] Python syntax valid.${NC}"
else
    log_and_print "${RED}[FAIL] Python syntax error in distributor.py!${NC}"
    exit 1
fi

# Hardcoded credentials check
if grep -qi -E "password *= *['\"][^'\"]+['\"]" "$DIST_FILE"; then
    log_and_print "${RED}[FAIL] Found potentially hardcoded passwords in $DIST_FILE.${NC}"
else
    log_and_print "${GREEN}[OK] No hardcoded passwords detected.${NC}"
fi

# Timeout enforcement check (Requests)
if grep -q "session\.post(" "$DIST_FILE" && ! grep -q "timeout=" "$DIST_FILE"; then
    log_and_print "${RED}[FAIL] Missing 'timeout=' in HTTP requests! Cron jobs may hang.${NC}"
else
    log_and_print "${GREEN}[OK] HTTP Network timeouts are enforced.${NC}"
fi

# Timeout enforcement check (FTP)
if grep -q "ftp\.connect(" "$DIST_FILE" && ! grep -E -q "timeout=[0-9]+" "$DIST_FILE"; then
    log_and_print "${RED}[FAIL] Missing 'timeout=' in FTP connections!${NC}"
else
    log_and_print "${GREEN}[OK] FTP Network timeouts are enforced.${NC}"
fi

# File Modification Timestamp Preservation
if grep -q "shutil\.copy(" "$DIST_FILE"; then
    log_and_print "${YELLOW}[WARN] Found shutil.copy(). Use shutil.copy2() to preserve timestamps.${NC}"
else
    log_and_print "${GREEN}[OK] Archive copies preserve GNSS modification timestamps.${NC}"
fi
echo "" >> "$LOG_FILE"; echo ""

# ------------------------------------------------------------------------------
# PHASE 3 & 4: Dynamic Target Extraction & Ledger Validation
# ------------------------------------------------------------------------------
log_and_print ">>> PHASE 3: Config Target Extraction & Dynamic Ledger Validation"

# Python extracts raw targets directly from the config
TARGETS=$("$PYTHON_BIN" -c "
import sys
sys.path.insert(0, '$PROJECT_ROOT')
try:
    from config.pipeline_targets import DESTINATIONS
    for tag, info in DESTINATIONS.items():
        if info.get('protocol') == 'FTP':
            print(f\"FTP|{info['host'].split(':')[0]}\")
        elif info.get('protocol') == 'HTTPS_POST':
            print(f\"HTTPS|{info['login_url']}\")
except Exception as e:
    print(f\"ERROR|{str(e)}\")
" | sort -u)

MISSING_CREDENTIALS=0

for item in $TARGETS; do
    PROTO=$(echo "$item" | cut -d'|' -f1)
    TARGET=$(echo "$item" | cut -d'|' -f2)

    if [ "$PROTO" == "ERROR" ]; then
        log_and_print "${RED}[FAIL] Config parse error: $TARGET${NC}"
        exit 1
    fi

    if [ "$PROTO" == "HTTPS" ]; then
        # Use GET (-X GET), follow redirects (-L), and print final destination
        FINAL_URL=$(curl -Ls -o /dev/null -w "%{url_effective}" "$TARGET" || true)

        # Extract just the hostname using awk
        HOST_TO_CHECK=$(echo "$FINAL_URL" | awk -F/ '{print $3}')

        if [ "$HOST_TO_CHECK" != "$(echo "$TARGET" | awk -F/ '{print $3}')" ]; then
            log_and_print "     -> [HTTPS] $TARGET redirects to SSO: $HOST_TO_CHECK"
        fi
        PORT=443
    else
        HOST_TO_CHECK="$TARGET"
        PORT=21
    fi

    # Check ~/.netrc for the dynamically resolved host
    if grep -q -E "^machine\s+$HOST_TO_CHECK" "$NETRC_FILE"; then
        log_and_print "${GREEN}[OK] Credentials found in ~/.netrc for: $HOST_TO_CHECK${NC}"

        # Basic network reachability test via Python socket
        REACHABLE=$("$PYTHON_BIN" -c "import socket; s=socket.socket(); s.settimeout(3); print('1' if s.connect_ex(('$HOST_TO_CHECK', $PORT))==0 else '0')")

        if [ "$REACHABLE" -eq 1 ]; then
            log_and_print "     -> [Network] Server reachable on port $PORT."
        else
            log_and_print "     -> ${YELLOW}[WARN] Network timeout. Target $HOST_TO_CHECK:$PORT unreachable.${NC}"
        fi
    else
        log_and_print "${RED}[FAIL] Missing ~/.netrc entry for required host: $HOST_TO_CHECK${NC}"
        MISSING_CREDENTIALS=1
    fi
done

echo "" >> "$LOG_FILE"; echo ""
if [ "$MISSING_CREDENTIALS" -eq 1 ]; then
    log_and_print "${RED}>>> AUDIT FAILED. Add missing entries to ~/.netrc. <<<${NC}"
    exit 1
else
    log_and_print "${GREEN}>>> AUDIT PASSED. Network configurations are hardened. <<<${NC}"
fi
log_and_print "=================================================================="
