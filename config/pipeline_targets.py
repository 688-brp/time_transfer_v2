"""
Unified Configuration for GNSS Pipeline
Contains Destinations, Network Stations, and CGGTTS parameters.
"""

# =============================================================================
# TEMPLATES
# =============================================================================
BIPM_IP = "5.144.141.242"
LOCAL_BASE = "/data/hourly_transfer"
LOCAL_BASE_RNX2 = "/data/rinex2"
WEB_BASE = "/home/denali/web_temp/NIST_GPS"

def bipm(path, filters):
    return {"protocol": "FTP", "host": BIPM_IP, "path": path, "filters": filters}

# --- NEW: Added symlink_to logic ---
def local(path, filters, symlink_to=None):
    cfg = {"protocol": "LOCAL", "host": "localhost", "path": path, "filters": filters}
    if symlink_to:
        cfg["symlink_to"] = symlink_to
    return cfg

# =============================================================================
# DESTINATIONS
# =============================================================================
DESTINATIONS = {
    # --- JPL (Receives both Hourly and Daily Observation + Navigation) ---
    "JPL": {"protocol": "FTP", "host": "54.213.227.197:21000", "path": "/igspush", 
            "filters": [
                "*01H*MO.crx.gz",  "*01H*MN.rnx.gz",
                "*01D*MO.crx.gz",  "*01D*MN.rnx.gz"
            ]},
    # --- CDDIS (Receives both Hourly and Daily Observation + Navigation) ---
    "CDDIS": {"protocol": "HTTPS_POST",
        "login_url": "https://depot.cddis.eosdis.nasa.gov/CDDIS_FileUpload/login",
              "path": "https://depot.cddis.eosdis.nasa.gov/CDDIS_FileUpload/upload/",
              "filters": [
                  "*01H*MO.crx.gz",  "*01H*MN.rnx.gz",
                  "*01D*MO.crx.gz",  "*01D*MN.rnx.gz"
              ]},
    # --- BIPM RINEX 3 (Strictly DAILY _01D_ ONLY) ---
    "BIPM-utc":  bipm("/data/UTC/NIST/links/rinex/", ["*01D*MO.crx.gz"]),
    "BIPM-utcr": bipm("/data/UTCr/NIST/RINEX/",      ["*01D*MO.crx.gz"]),

    # --- BIPM CGGTTS ---
    "BIPM-utc-cggtts":  bipm("/data/UTC/NIST/links/cggtts/", ["gznisx*", "gznisg*"]),
    "BIPM-utcr-cggtts": bipm("/data/UTCr/NIST/CGGTTS/",      ["gznisx*", "gznisg*"]),

    # --- LOCAL ARCHIVES (Strictly DAILY _01D_) ---
    "LOCAL_ARCHIVE_RINEX":  local(f"{LOCAL_BASE}/daily_archive",        ["*01D*MO.crx.gz", "*01D*MN.rnx.gz"]),
    "LOCAL_ARCHIVE_CGGTTS": local(f"{LOCAL_BASE}/daily_archive_cggtts", ["gz*", "ez*", "cz*", "rz*"]),

    # --- LOCAL ARCHIVES HRLY (for testing _01H_) ---
    "LOCAL_ARCHIVE_RINEX_HRLY":  local(f"{LOCAL_BASE}/daily_archive_hrly", ["*01H*MO.crx.gz", "*01H*MN.rnx.gz"]),

    # --- UNCOMPRESSED RINEX 2 w/ SYMLINKS ---
    "LOCAL_RINEX2-nist": local(LOCAL_BASE_RNX2, ["nist*o", "nist*n", "nist*p"], symlink_to="/data/ggtts/nist/rinex"),
    "LOCAL_RINEX2-nisg": local(LOCAL_BASE_RNX2, ["nisg*o", "nisg*n", "nisg*p"], symlink_to="/data/ggtts/nisg/rinex"),
    "LOCAL_RINEX2-nisk": local(LOCAL_BASE_RNX2, ["nisk*o", "nisk*n", "nisk*p"], symlink_to="/data/ggtts/nisk/rinex"),

    # --- WEB STAGING ARCHIVES (Strictly DAILY _01D_) ---
    "WEB_CGGTTS_NISX": local(f"{WEB_BASE}/nisx/cggtts", ["*gznisx*"]),
    "WEB_RINEX_NISX":  local(f"{WEB_BASE}/nisx/rinex",  ["NIST*01D*MO.crx.gz"]),

    "WEB_CGGTTS_NISG": local(f"{WEB_BASE}/nisg/cggtts", ["*gznisg*"]),
    "WEB_RINEX_NISG":  local(f"{WEB_BASE}/nisg/rinex",  ["NISG*01D*MO.crx.gz"])

    # --- FUTURE WEB TARGETS (Uncomment when ready) ---
    # "WEB_CGGTTS_NISK": local(f"{WEB_BASE}/nisk/cggtts", ["*gznisk*"]),
    # "WEB_RINEX_NISK":  local(f"{WEB_BASE}/nisk/rinex",  ["NISK*01D*MO.crx.gz"])
}

# =============================================================================
# STATIONS
# =============================================================================
STATIONS = {
    "nist": {
        "host": "tftdata@688feynman.nist.gov",
        "legacy_path": "/home/tftdata/GPS/nist",       # Old path
        "modern_path": "/home/tftdata/sept_log",       # Upgraded path
        "hourly_mode": "BOTH",  # Runs RINEX + CGGTTS
        "daily_mode":  "BOTH",  # Runs RINEX + CGGTTS
        "hourly_dest": ["CDDIS", "LOCAL_ARCHIVE_CGGTTS"],
        "daily_dest": ["CDDIS", "BIPM-utc", "BIPM-utcr", "BIPM-utc-cggtts", "BIPM-utcr-cggtts", "LOCAL_ARCHIVE_RINEX", "LOCAL_ARCHIVE_CGGTTS", "LOCAL_RINEX2-nist", "WEB_CGGTTS_NISX", "WEB_RINEX_NISX"]
    },
    "nisg": {
        "host": "tftdata@688galileo.bw.nist.gov", 
        "legacy_path": "/home/tftdata/GPS/nisg",
        "modern_path": "/home/tftdata/sept_log",
        "hourly_mode": "BOTH",   # Skips RINEX, runs QC if "CGGTTS" only
        "daily_mode":  "BOTH",   # Runs RINEX + CGGTTS
        "hourly_dest": ["CDDIS", "LOCAL_ARCHIVE_CGGTTS"],
        "daily_dest": ["CDDIS", "BIPM-utc", "BIPM-utcr", "BIPM-utc-cggtts", "BIPM-utcr-cggtts", "LOCAL_ARCHIVE_RINEX", "LOCAL_ARCHIVE_CGGTTS", "LOCAL_RINEX2-nisg", "WEB_CGGTTS_NISG", "WEB_RINEX_NISG"]
    },
    "nisk": {
        "host": "tftdata@688maxwell.nist.gov", 
        "legacy_path": "/home/tftdata/data", 
        "modern_path": "/home/tftdata/sept_log",
        "hourly_mode": "BOTH",   # Skips RINEX, runs QC
        "daily_mode":  "BOTH",   # Runs RINEX + CGGTTS
        # To enable web routing later, add "WEB_CGGTTS_NISK" and "WEB_RINEX_NISK" to these lists
        "hourly_dest": ["LOCAL_ARCHIVE_CGGTTS"],
        "daily_dest": ["LOCAL_ARCHIVE_RINEX", "LOCAL_ARCHIVE_CGGTTS", "LOCAL_RINEX2-nisk"]
    }
}


# =============================================================================
# CGGTTS JOBS
# =============================================================================
CGGTTS_JOBS = {
    "nisx": {
        "source": "nist",
        "params": "-px-1288398.60 -py-4721697.05 -pz4078625.45 -revdate 2025-05-02 -comment \"BIPM Cal_Id name: NISX\" -tref \"UTC(NIST)\" -calid \"1001-2022\" -dl1 28.8 -dl2 26.6 -de1 31.1 -de5a 32.0 -dcab 275.5 -dref 115.3 -elm 10 -labid \"ni\" -rxid \"st\" -ls 18"
    },
    "nisg": {
        "source": "nisg",
        "params": "-px-1288547.20 -py-4721701.17 -pz4078586.53 -revdate 2025-05-02 -comment \"BIPM Cal_Id name: NISG\" -tref \"UTC(NIST:regen)\" -calid \"1001-2022\" -dl1 30.8 -dl2 29.3 -de1 33.2 -de5a 33.2 -dcab 298.5 -dref 1584.5 -elm 10 -labid \"ni\" -rxid \"sg\" -ls 18"
    },
    "nisk": {
        "source": "nisk",
        "params": "-px-1288542.18 -py-4721698.67 -pz4078590.81 -revdate 2023-05-30 -comment \"BIPM Cal_Id name: NISK\" -tref \"UTC(NIST:KGA)\" -calid \"1001-2022\" -dl1 24.3 -dl2 24.2 -de1 27.0 -de5a 26.1 -dcab 298.9 -dref 95.8 -elm 10 -labid \"ni\" -rxid \"sk\" -ls 18"
    }
}

# =============================================================================
# QC GATE LIMITS
# Defines absolute limits for CGGTTS metrics to trigger quarantine.
# =============================================================================
GATE_LIMITS = {
    # NIST Primary: Check REFSYS < 100ns
    "nisx": {
        "source": "nist",
        "constellation_id": "gz",
        "metrics": {"REFSYS": 50}
    },

    # NISG: Bias-Corr RCVR
    "nisg": {
        "source": "nisg",
        "constellation_id": "gz",
        "sv_match": 3,
        "metrics": {"REFSYS": 50}
    },

    # KGA: Strict REFSYS check only
    "nisk": {
        "source": "nisk",
        "constellation_id": "gz",
        "sv_match": 3,
        "metrics": {"REFSYS": 100}
    }
}

# =============================================================================
# DYNAMIC SANDBOX SINKHOLE
# Automatically activates when GNSS_ENV=sandbox or SANDBOX_MODE env var is set
# =============================================================================
import os
from pathlib import Path

SANDBOX_MODE = os.getenv("GNSS_ENV", "").lower() == "sandbox" or os.getenv("SANDBOX_MODE", "").lower() == "true"
SANDBOX_BASE = Path(__file__).resolve().parent.parent / "sandbox_env" / "mock_destinations"

if SANDBOX_MODE:
    print(f"\n[!!!] CRITICAL: SANDBOX MODE ENABLED [!!!]")
    print(f"[!!!] All external network traffic hijacked to: {SANDBOX_BASE}\n")

    for key, config in DESTINATIONS.items():
        safe_key = key.replace("-", "_").lower()
        mock_path = str(SANDBOX_BASE / safe_key)

        os.makedirs(mock_path, exist_ok=True)

        # Override network protocols to local disk copies
        config["protocol"] = "LOCAL"
        config["host"] = "localhost"
        config["path"] = mock_path

        if "login_url" in config:
            config["login_url"] = ""

        if "symlink_to" in config:
            config["symlink_to"] = str(SANDBOX_BASE / "fuse_links" / safe_key)
            os.makedirs(config["symlink_to"], exist_ok=True)
