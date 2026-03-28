"""
_com_interface.py
─────────────────
Simplified COM wrapper for local SuperPro Designer use.

Adapted from pisces-superpro/api/com_interface.py (the cloud backend).
Cloud-only concerns removed: no interactive-session recovery, no NSSM
service cross-session logic, no scheduled dialog-watcher task.

For local use the user IS the interactive session, so Designer.exe opens
normally.  The watchdog and basic retry logic are kept to handle the common
case of Designer.exe showing a crash-recovery dialog on startup.

IMPORTANT: All calls must come from the same OS thread (COM STA model).
"""

import gc
import logging
import os
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

log = logging.getLogger(__name__)

# Seconds to wait for GetObject() before killing Designer.exe and retrying.
_GETOBJECT_TIMEOUT_S = 90
# Retry budget for generic COM failures (process still exiting, RPC error, etc.)
_MAX_RETRIES = 2
_RETRY_DELAY_S = 3


class SuperProCOMError(Exception):
    """Raised when SuperPro COM interaction fails."""


def _kill_designer() -> None:
    """Kill Designer.exe and any crash-dialog processes (best-effort)."""
    for proc in ("Designer.exe", "WerFault.exe", "WerFaultSecure.exe"):
        try:
            subprocess.run(
                ["taskkill", "/F", "/IM", proc],
                capture_output=True, timeout=5,
            )
        except Exception:
            pass


class LocalSuperProInstance:
    """
    Thin COM wrapper for a single local SuperPro .spf conversion.

    Usage::

        inst = LocalSuperProInstance()
        try:
            inst.open_spf(path_to_spf)
            inst.run_balances()   # optional but recommended
            doc = inst.document
            # ... call data_extraction.extract_all(doc) ...
        finally:
            inst.close()
    """

    def __init__(self) -> None:
        try:
            import pythoncom
            import win32com.client  # noqa: F401  (confirm importable)
            self._pythoncom = pythoncom
        except ImportError as exc:
            raise SuperProCOMError(
                "pywin32 is not installed. Install it with:\n"
                "  pip install pywin32\n"
                "(Windows only — SuperPro Designer must also be installed.)"
            ) from exc

        self._pythoncom.CoInitialize()
        self._doc = None
        self._tmp_dir: str | None = None
        log.info("COM apartment initialised.")

    # ── Public interface ───────────────────────────────────────────────────

    @property
    def document(self):
        if self._doc is None:
            raise SuperProCOMError("No document open. Call open_spf() first.")
        return self._doc

    def open_spf(self, filepath: str | Path) -> None:
        """
        Open a .spf file via COM.

        Copies the file to a temp directory first so the original is never
        modified.  Retries up to _MAX_RETRIES times on failure.
        """
        import win32com.client

        filepath = str(filepath)
        if not os.path.exists(filepath):
            raise SuperProCOMError(f"File not found: {filepath}")

        self._close_document()

        self._tmp_dir = tempfile.mkdtemp(prefix="superpro_sff_")
        tmp_path = os.path.join(self._tmp_dir, Path(filepath).name)
        shutil.copy2(filepath, tmp_path)
        log.info("Opening .spf: %s", tmp_path)

        # Kill any lingering Designer.exe from a previous run before attempting
        # GetObject() — eliminates a DCOM race condition on quick re-opens.
        _kill_designer()
        time.sleep(1)

        last_exc: Exception | None = None
        for attempt in range(1, _MAX_RETRIES + 1):
            # Watchdog: if GetObject() hangs (Designer.exe shows a dialog and
            # blocks COM registration), kill it after _GETOBJECT_TIMEOUT_S so
            # the loop can retry.
            done = threading.Event()

            def _watchdog(event=done):
                if not event.wait(timeout=_GETOBJECT_TIMEOUT_S):
                    log.warning(
                        "GetObject() watchdog: Designer.exe did not respond within %ds "
                        "(attempt %d). Killing Designer.exe to unblock.",
                        _GETOBJECT_TIMEOUT_S, attempt,
                    )
                    _kill_designer()

            threading.Thread(target=_watchdog, daemon=True).start()

            try:
                self._doc = win32com.client.GetObject(tmp_path)
                done.set()
                log.info("Document opened successfully.")
                return
            except Exception as exc:
                done.set()
                last_exc = exc
                log.warning(
                    "GetObject failed (attempt %d/%d): %s",
                    attempt, _MAX_RETRIES, exc,
                )
                if attempt < _MAX_RETRIES:
                    _kill_designer()
                    time.sleep(_RETRY_DELAY_S)

        raise SuperProCOMError(
            f"Failed to open '{tmp_path}' after {_MAX_RETRIES} attempts.\n"
            f"Last error: {last_exc}\n\n"
            "Troubleshooting tips:\n"
            "  • Make sure SuperPro Designer is installed and licensed.\n"
            "  • Try opening the .spf file in SuperPro manually first.\n"
            "  • If Designer.exe is showing a crash dialog, dismiss it and retry."
        ) from last_exc

    def run_balances(self) -> None:
        """
        Run mass/energy balances and economic calculations.

        Call this after open_spf() to ensure all results are up-to-date.
        Errors are logged as warnings (the extraction will use whatever
        results are cached in the file).
        """
        if self._doc is None:
            raise SuperProCOMError("No document open.")
        log.info("Running mass/energy balances...")
        try:
            self._doc._oleobj_.InvokeTypes(58, 0, 1, (11, 0), ((16396, 0),), None)
        except Exception as exc:
            log.warning("DoMEBalances raised (extraction will use cached results): %s", exc)
        log.info("Running economic calculations...")
        try:
            self._doc._oleobj_.InvokeTypes(59, 0, 1, (11, 0), ())
        except Exception as exc:
            log.warning("DoEconomicCalculations raised: %s", exc)

    def close(self) -> None:
        """Close the document and release COM resources."""
        self._close_document()
        gc.collect()
        self._pythoncom.CoUninitialize()
        log.info("COM apartment released.")

    # ── Internal ──────────────────────────────────────────────────────────

    def _close_document(self) -> None:
        if self._doc is not None:
            try:
                self._doc.CloseDoc(False)
            except Exception as exc:
                log.warning("Error closing document: %s", exc)
            finally:
                self._doc = None
        if self._tmp_dir and os.path.exists(self._tmp_dir):
            shutil.rmtree(self._tmp_dir, ignore_errors=True)
            self._tmp_dir = None
