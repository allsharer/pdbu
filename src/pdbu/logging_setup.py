"""Application and per-operation logging.

PDBU keeps two kinds of logs, both under ``$XDG_STATE_HOME/pdbu/logs``:
a general application log for diagnostics, and one dedicated log file
per backup/restore/verify operation (referenced from its history entry).
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import time
from pathlib import Path

from pdbu import paths

_APP_LOGGER_NAME = "pdbu"
_configured = False


def setup_app_logging(*, verbose: bool = False, quiet: bool = False) -> logging.Logger:
    global _configured
    logger = logging.getLogger(_APP_LOGGER_NAME)
    if _configured:
        return logger

    paths.ensure_dirs()
    logger.setLevel(logging.DEBUG)

    console_level = logging.ERROR if quiet else (logging.DEBUG if verbose else logging.INFO)
    console = logging.StreamHandler()
    console.setLevel(console_level)
    console.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(console)

    app_log_path = paths.log_dir() / "pdbu.log"
    file_handler = logging.handlers.RotatingFileHandler(
        app_log_path, maxBytes=5 * 1024 * 1024, backupCount=3
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s")
    )
    logger.addHandler(file_handler)
    try:
        os.chmod(app_log_path, 0o600)
    except OSError:
        pass

    _configured = True
    return logger


def operation_log_path(operation_id: str) -> Path:
    return paths.log_dir() / f"{operation_id}.log"


def get_operation_logger(operation_id: str) -> tuple[logging.Logger, logging.Handler]:
    """A dedicated logger + file handler for one backup/restore run.

    Callers must call ``logger.removeHandler(handler); handler.close()``
    when the operation finishes so file handles don't accumulate.
    """
    paths.ensure_dirs()
    log_path = operation_log_path(operation_id)
    logger = logging.getLogger(f"{_APP_LOGGER_NAME}.op.{operation_id}")
    logger.setLevel(logging.DEBUG)
    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-8s %(message)s"))
    logger.addHandler(handler)
    try:
        os.chmod(log_path, 0o600)
    except OSError:
        pass
    return logger, handler


def purge_old_logs(retention_days: int, *, now: float | None = None) -> list[Path]:
    """Delete operation log files older than ``retention_days``. Returns removed paths."""
    if retention_days <= 0:
        return []
    cutoff = (now if now is not None else time.time()) - retention_days * 86400
    removed = []
    log_directory = paths.log_dir()
    if not log_directory.exists():
        return []
    for entry in log_directory.iterdir():
        if entry.name == "pdbu.log" or entry.name.startswith("pdbu.log."):
            continue
        try:
            if entry.is_file() and entry.stat().st_mtime < cutoff:
                entry.unlink()
                removed.append(entry)
        except OSError:
            continue
    return removed
