"""
distributor.py - Network delivery engine for HTTP/FTP/Local transfers.
Category: MUTABLE CONNECTOR (< 150 lines)
"""

import os
import shutil
import ftplib
import netrc
import fnmatch
import requests
from pathlib import Path


class Distributor:
    def __init__(self, tracker, log, destinations_dict):
        self.tracker = tracker
        self.log = log
        self.destinations = destinations_dict

    def process_outbound_queue(self, skip_ftp=False, skip_cddis=False, target_pattern=None, file_filter=None, allowed_destinations=None):
        """Drains the outbound queue and routes files according to pipeline_targets."""
        pending = self.tracker.get_pending_outbound(target_pattern)
        if not pending:
            return

        is_sandbox = os.environ.get("GNSS_ENV") == "sandbox"
        cddis_session = None

        # Build normalized lookups to fix '-' vs '_' mismatches
        dest_map = {str(k).replace("-", "_").lower(): v for k, v in self.destinations.items()}
        allowed_set = {str(d).replace("-", "_").lower() for d in allowed_destinations} if allowed_destinations else None

        try:
            for job in pending:
                file_id, dest_tag = job['id'], job['destination']
                local_path = Path(job['file_path'])
                safe_tag = str(dest_tag).replace("-", "_").lower()

                # 1. Pipeline Mode Authority Filter (e.g. CGGTTS vs RINEX runs)
                if allowed_set is not None and safe_tag not in allowed_set:
                    continue

                # 2. File Filter (e.g. *z* files)
                if file_filter and not fnmatch.fnmatch(local_path.name, file_filter):
                    continue

                # 3. Buffer Check
                if not local_path.exists():
                    self.log.error(f"  [DISTRIBUTOR] Missing buffer file: {local_path.name}")
                    self.tracker.increment_retry(str(local_path), dest_tag)
                    continue

                # 4. Destination Check
                if safe_tag not in dest_map:
                    self.log.error(f"  [DISTRIBUTOR] Unknown destination config for '{dest_tag}'")
                    self.tracker.increment_retry(str(local_path), dest_tag)
                    continue

                dest_info = dest_map[safe_tag]
                protocol = dest_info.get('protocol', 'LOCAL')

                # CLI Network Bypasses
                if skip_ftp and protocol == 'FTP':
                    continue
                if skip_cddis and protocol == 'HTTPS_POST':
                    continue

                # 5. Execute Transfer
                try:
                    if protocol == 'LOCAL':
                        self._transfer_local(local_path, dest_info['path'], dest_info.get('symlink_to'))
                        self.log.info(f"  [DISTRIBUTOR] SUCCESS: {local_path.name} -> [{safe_tag}]")
                    
                    elif protocol == 'FTP':
                        self._transfer_ftp(local_path, dest_info['host'], dest_info['path'])
                        self.log.info(f"  [DISTRIBUTOR] FTP SUCCESS: {local_path.name} -> [{safe_tag}]")
                    
                    elif protocol == 'HTTPS_POST':
                        if not cddis_session:
                            cddis_session = requests.Session()
                            self._authenticate_cddis(cddis_session, dest_info['login_url'])
                        self._transfer_cddis(cddis_session, local_path, dest_info['path'])
                        self.log.info(f"  [DISTRIBUTOR] HTTPS SUCCESS: {local_path.name} -> [{safe_tag}]")

                    # Log receipt in Ledger
                    self.tracker.mark_success(str(local_path), dest_tag)

                except Exception as e:
                    self.log.error(f"  [DISTRIBUTOR] {protocol} Failed for {local_path.name} to {dest_tag}: {e}")
                    self.tracker.increment_retry(str(local_path), dest_tag)

                    # Drop session on failure to force clean auth next retry
                    if protocol == 'HTTPS_POST' and cddis_session:
                        cddis_session.close()
                        cddis_session = None

        finally:
            if cddis_session:
                cddis_session.close()

    # --- PROTOCOL IMPLEMENTATIONS ---

    def _transfer_local(self, local_path, remote_path, symlink_to=None):
        fuse_dir = Path(remote_path)
        fuse_dir.mkdir(parents=True, exist_ok=True)
        final_fuse = fuse_dir / local_path.name

        # Preserves GNSS file modification timestamps
        shutil.copy2(str(local_path), str(final_fuse))

        if symlink_to:
            sym_dir = Path(symlink_to)
            sym_dir.mkdir(parents=True, exist_ok=True)
            sym_link = sym_dir / local_path.name
            if sym_link.exists() or sym_link.is_symlink():
                sym_link.unlink()
            os.symlink(str(final_fuse), str(sym_link))

    def _transfer_ftp(self, local_path, host_port, remote_path):
        host, port = (host_port.split(':') + [21])[:2]

        auth = netrc.netrc().authenticators(host)
        if not auth:
            raise RuntimeError(f"FTP auth failed: No entry for '{host}' in ~/.netrc")
        user, _, passwd = auth

        with ftplib.FTP() as ftp:
            ftp.connect(host, int(port), timeout=60)
            ftp.login(user, passwd)
            ftp.set_pasv(True)
            ftp.cwd(remote_path)
            with open(local_path, 'rb') as f:
                ftp.storbinary(f"STOR {local_path.name}", f)

    def _authenticate_cddis(self, session, login_url):
        """Authenticates with NASA Earthdata URS using credentials stored in ~/.netrc."""
        auth = netrc.netrc().authenticators("urs.earthdata.nasa.gov")
        if auth:
            session.auth = (auth[0], auth[2])
            
        res = session.get(login_url, timeout=30)
        res.raise_for_status()
        if not session.cookies:
            raise RuntimeError("CDDIS Auth failed. No session cookies returned from NASA URS.")

    def _transfer_cddis(self, session, local_path, upload_url):
        with open(local_path, 'rb') as file_payload:
            form_data = {'fileType': 'GNSS', 'fileContentType': 'Data'}
            form_files = {'file[]': (local_path.name, file_payload)}
            res = session.post(upload_url, data=form_data, files=form_files, timeout=120)
            res.raise_for_status()

        resp_text = res.text.strip()
        if "Successful upload" not in resp_text and "Successfully uploaded" not in resp_text:
            raise RuntimeError(f"CDDIS rejected payload {local_path.name}. Output: {resp_text[:150]}")
