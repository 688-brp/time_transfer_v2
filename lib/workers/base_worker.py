"""
base_worker.py - Abstract contract and shared utilities for data processors.
Category: IMMUTABLE CORE (Append-only)
"""

import subprocess
import logging
from abc import ABC, abstractmethod
from pathlib import Path

class BaseWorker(ABC):
    def __init__(self, tracker, logger=None):
        self.tracker = tracker
        self.log = logger or logging.getLogger(self.__class__.__name__)

    @abstractmethod
    def process(self, *args, **kwargs):
        """
        The core execution loop for the worker.
        Must be implemented by all child classes.
        """
        pass

    def run_subprocess(self, cmd: list, timeout: int = 120, cwd: Path = None):
        """
        A hardened wrapper for executing Bash, AWK, and binary utilities (crx2rnx, etc.).
        Captures output, handles timeouts, and enforces strict error checking.
        
        Returns:
            tuple: (success_boolean, output_or_error_string)
        """
        cmd_str = " ".join(str(c) for c in cmd)
        self.log.debug(f"[{self.__class__.__name__}] Exec: {cmd_str}")
        
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=cwd,
                check=False
            )
            
            if result.returncode != 0:
                err_msg = result.stderr.strip() if result.stderr else "Unknown error/exit code"
                self.log.error(f"[{self.__class__.__name__}] Subprocess failed (Code {result.returncode}): {err_msg}")
                self.log.error(f"[{self.__class__.__name__}] Failed Command: {cmd_str}")
                return False, err_msg
                
            return True, result.stdout.strip()
            
        except subprocess.TimeoutExpired:
            self.log.error(f"[{self.__class__.__name__}] Subprocess timed out after {timeout}s: {cmd_str}")
            return False, "Timeout"
        except Exception as e:
            self.log.error(f"[{self.__class__.__name__}] Subprocess execution exception: {e}", exc_info=True)
            return False, str(e)
