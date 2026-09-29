"""
base_tracker.py - Generic SQLite State Engine for Automated Pipelines.
Category: IMMUTABLE CORE
"""

import sqlite3
import logging
from pathlib import Path
from datetime import datetime, timezone, timedelta

log = logging.getLogger(__name__)

class PipelineTrackerBase:
    """Generic state tracker for handling pipeline idempotency and retries."""
    
    def __init__(self, db_path: Path, max_retries: int = 10):
        self.db_path = Path(db_path)
        self.max_retries = max_retries
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_connection(self):
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._get_connection() as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            
            # 1. Generic Outbound Queue
            conn.execute("""
                CREATE TABLE IF NOT EXISTS outbound_queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_path TEXT NOT NULL,
                    destination TEXT NOT NULL,
                    status TEXT CHECK(status IN ('PENDING', 'SUCCESS', 'FAILED', 'FAILED_MISSING', 'QUARANTINED')) DEFAULT 'PENDING',
                    retry_count INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(file_path, destination)
                );
            """)
            
            # 2. Generic Inbound Queue
            conn.execute("""
                CREATE TABLE IF NOT EXISTS inbound_queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_id TEXT NOT NULL,
                    job_type TEXT NOT NULL,
                    target_ref TEXT NOT NULL,
                    remote_src TEXT NOT NULL,
                    local_dest TEXT NOT NULL,
                    status TEXT CHECK(status IN ('PENDING', 'FETCHED', 'FAILED')) DEFAULT 'PENDING',
                    retry_count INTEGER DEFAULT 0,
                    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(remote_src, target_ref)
                );
            """)
            
            # 3. Quarantine Log
            conn.execute("""
                CREATE TABLE IF NOT EXISTS quarantine_log (
                    source_id TEXT NOT NULL,
                    target_ref TEXT NOT NULL,
                    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(source_id, target_ref)
                );
            """)

            # 4. Immutable Delivery Audit
            conn.execute("""
                CREATE TABLE IF NOT EXISTS delivery_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_path TEXT NOT NULL,
                    destination TEXT NOT NULL,
                    delivered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(file_path, destination)
                );
            """)
            conn.commit()

    # --- INBOUND METHODS ---
    def register_inbound(self, source_id: str, job_type: str, target_ref: str, remote_src: str, local_dest: str):
        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO inbound_queue (source_id, job_type, target_ref, remote_src, local_dest, status) 
                VALUES (?, ?, ?, ?, ?, 'PENDING')
                ON CONFLICT(remote_src, target_ref) DO UPDATE SET 
                    status = CASE WHEN inbound_queue.status = 'FETCHED' THEN 'FETCHED' ELSE 'PENDING' END, 
                    retry_count = CASE WHEN inbound_queue.status = 'FETCHED' THEN inbound_queue.retry_count ELSE 0 END
            """, (source_id, job_type, target_ref, remote_src, str(local_dest)))
            conn.commit()
    
    def get_pending_inbound(self, job_type=None, target_pattern=None):
        conn = self._get_connection()
        query = "SELECT * FROM inbound_queue WHERE status = 'PENDING'"
        params = []
        
        if job_type:
            query += " AND job_type = ?"
            params.append(job_type)
        if target_pattern:
            query += " AND target_ref LIKE ?"
            params.append(f"%{target_pattern}%")
            
        query += " ORDER BY id ASC"
        return [dict(r) for r in conn.execute(query, params).fetchall()]

    def mark_inbound(self, job_id: int, success: bool = True):
        with self._get_connection() as conn:
            if success:
                conn.execute("UPDATE inbound_queue SET status = 'FETCHED' WHERE id = ?", (job_id,))
            else:
                conn.execute("""
                    UPDATE inbound_queue
                    SET retry_count = retry_count + 1,
                        status = CASE WHEN retry_count + 1 >= ? THEN 'FAILED' ELSE 'PENDING' END
                    WHERE id = ?
                """, (self.max_retries, job_id))
            conn.commit()

    # --- OUTBOUND METHODS ---
    def register_outbound(self, file_path: str, destination: str):
        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO outbound_queue (file_path, destination, status, retry_count)
                VALUES (?, ?, 'PENDING', 0)
                ON CONFLICT(file_path, destination) DO UPDATE SET
                    status = 'PENDING',
                    retry_count = 0,
                    updated_at = CURRENT_TIMESTAMP;
            """, (str(file_path), str(destination)))
            conn.commit()

    def get_pending_outbound(self, target_pattern=None):
        """Standard wildcard retrieval. Subclasses can override this for complex logic."""
        with self._get_connection() as conn:
            if not target_pattern:
                query = "SELECT * FROM outbound_queue WHERE status = 'PENDING' AND retry_count < ?"
                params = [self.max_retries]
            else:
                query = "SELECT * FROM outbound_queue WHERE status = 'PENDING' AND retry_count < ? AND file_path LIKE ?"
                params = [self.max_retries, f"%{target_pattern}%"]
            return [dict(r) for r in conn.execute(query, params).fetchall()]
    
    def mark_success(self, file_path: str, destination: str):
        with self._get_connection() as conn:
            conn.execute("""
                UPDATE outbound_queue 
                SET status = 'SUCCESS', updated_at = CURRENT_TIMESTAMP 
                WHERE file_path = ? AND destination = ?
            """, (str(file_path), str(destination)))
            
            conn.execute("""
                INSERT OR IGNORE INTO delivery_audit (file_path, destination)
                VALUES (?, ?)
            """, (str(file_path), str(destination)))
            conn.commit()

    def increment_retry(self, file_path: str, destination: str):
        with self._get_connection() as conn:
            conn.execute("""
                UPDATE outbound_queue 
                SET retry_count = retry_count + 1,
                    status = CASE WHEN retry_count + 1 >= ? THEN 'FAILED' ELSE 'PENDING' END,
                    updated_at = CURRENT_TIMESTAMP
                WHERE file_path = ? AND destination = ?
            """, (self.max_retries, str(file_path), str(destination)))
            conn.commit()

    # --- HOUSEKEEPING ---
    def prune_ledger(self, days_to_keep: int) -> int:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days_to_keep)).strftime("%Y-%m-%d %H:%M:%S")
        with self._get_connection() as conn:
            c1 = conn.execute("DELETE FROM outbound_queue WHERE status = 'SUCCESS' AND updated_at < ?", (cutoff,))
            c2 = conn.execute("DELETE FROM inbound_queue WHERE status = 'FETCHED' AND added_at < ?", (cutoff,))
            c3 = conn.execute("DELETE FROM delivery_audit WHERE delivered_at < ?", (cutoff,))
            conn.commit()
            return c1.rowcount + c2.rowcount + c3.rowcount
