from datetime import datetime, timezone
from core.base_tracker import PipelineTrackerBase

class GNSSTracker(PipelineTrackerBase):
    """Extends the generic tracker with GNSS-specific MJD and DOY parsing rules."""
    
    def get_pending_outbound(self, target_pattern=None):
        """Overrides generic wildcard matching to inject MJD/DOY regex rules."""
        if not target_pattern:
            return super().get_pending_outbound()
            
        with self._get_connection() as conn:
            patterns = [f"%{target_pattern}%"]
            
            # GNSS-specific logic: Inject RINEX and CGGTTS format patterns
            if len(target_pattern) >= 7:
                yyyy, yy, doy = target_pattern[:4], target_pattern[2:4], target_pattern[4:7]
                patterns.append(f"%{doy}0.{yy}%")
                
                try:
                    dt = datetime.strptime(f"{yyyy}{doy}", "%Y%j").replace(tzinfo=timezone.utc)
                    mjd = str((dt - datetime(1858, 11, 17, tzinfo=timezone.utc)).days)
                    patterns.append(f"%{mjd[:2]}.{mjd[2:]}%")
                except ValueError:
                    pass

            where_clause = " OR ".join(["file_path LIKE ?"] * len(patterns))
            query = f"""
                SELECT * FROM outbound_queue 
                WHERE status = 'PENDING' AND retry_count < ? AND ({where_clause})
            """
            
            # Inject max_retries into the front of the params list
            return [dict(r) for r in conn.execute(query, [self.max_retries] + patterns).fetchall()]

    def reset_status(self, target_pattern: str, destination_tag: str = None) -> int:
        """Custom manual override logic specific to GNSS manual flag sweeps."""
        outbound_query = "UPDATE outbound_queue SET status = 'PENDING', retry_count = 0 WHERE file_path LIKE ?"
        params = [f"%{target_pattern}%"]

        if destination_tag:
            outbound_query += " AND destination = ?"
            params.append(destination_tag)

        with self._get_connection() as conn:
            c1 = conn.execute(outbound_query, params)
            c2 = conn.execute("""
                UPDATE inbound_queue SET status = 'PENDING', retry_count = 0
                WHERE remote_src LIKE ? OR target_ref LIKE ?
            """, (f"%{target_pattern}%", f"%{target_pattern}%"))
            conn.commit()
            return c1.rowcount + c2.rowcount
