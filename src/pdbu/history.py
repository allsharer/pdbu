"""Backup/restore operation history, stored in a local SQLite database."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from pdbu import paths

_SCHEMA = """
CREATE TABLE IF NOT EXISTS operations (
    operation_id TEXT PRIMARY KEY,
    operation_type TEXT NOT NULL,
    mode TEXT NOT NULL,
    dry_run INTEGER NOT NULL DEFAULT 0,
    start_time REAL NOT NULL,
    end_time REAL,
    source TEXT NOT NULL,
    destination TEXT NOT NULL,
    destination_identity TEXT,
    command_options TEXT,
    files_examined INTEGER,
    files_transferred INTEGER,
    bytes_transferred INTEGER,
    files_deleted INTEGER,
    warnings TEXT,
    errors TEXT,
    exit_code INTEGER,
    result TEXT NOT NULL DEFAULT 'in_progress',
    log_path TEXT
);
CREATE INDEX IF NOT EXISTS idx_operations_type_time
    ON operations (operation_type, start_time DESC);
"""


@dataclass
class OperationRecord:
    operation_id: str
    operation_type: str  # "backup" | "restore" | "verify"
    mode: str  # "drive_a" | "drive_b" | "ssh"
    dry_run: bool = False
    start_time: float = field(default_factory=time.time)
    end_time: float | None = None
    source: str = ""
    destination: str = ""
    destination_identity: str = ""
    command_options: list[str] = field(default_factory=list)
    files_examined: int | None = None
    files_transferred: int | None = None
    bytes_transferred: int | None = None
    files_deleted: int | None = None
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    exit_code: int | None = None
    result: str = "in_progress"  # success | partial | failed | cancelled | in_progress
    log_path: str = ""

    @property
    def duration_seconds(self) -> float | None:
        if self.end_time is None:
            return None
        return self.end_time - self.start_time

    def to_json_dict(self) -> dict:
        return {
            "operation_id": self.operation_id,
            "operation_type": self.operation_type,
            "mode": self.mode,
            "dry_run": self.dry_run,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration_seconds": self.duration_seconds,
            "source": self.source,
            "destination": self.destination,
            "destination_identity": self.destination_identity,
            "files_examined": self.files_examined,
            "files_transferred": self.files_transferred,
            "bytes_transferred": self.bytes_transferred,
            "files_deleted": self.files_deleted,
            "warnings": self.warnings,
            "errors": self.errors,
            "exit_code": self.exit_code,
            "result": self.result,
            "log_path": self.log_path,
        }


def new_operation_id() -> str:
    return f"{time.strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:8]}"


def _connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or paths.history_db()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    is_new = not path.exists()
    # check_same_thread=False: the GUI runs backups/restores on a worker
    # thread while HistoryStore is normally constructed on the main
    # thread. Access is serialized by HistoryStore's own lock below, and
    # by the fact that PDBU only ever runs one backup/restore at a time
    # (see safety.acquire_operation_lock).
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    conn.commit()
    if is_new:
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    return conn


def _row_to_record(row: sqlite3.Row) -> OperationRecord:
    return OperationRecord(
        operation_id=row["operation_id"],
        operation_type=row["operation_type"],
        mode=row["mode"],
        dry_run=bool(row["dry_run"]),
        start_time=row["start_time"],
        end_time=row["end_time"],
        source=row["source"],
        destination=row["destination"],
        destination_identity=row["destination_identity"] or "",
        command_options=json.loads(row["command_options"]) if row["command_options"] else [],
        files_examined=row["files_examined"],
        files_transferred=row["files_transferred"],
        bytes_transferred=row["bytes_transferred"],
        files_deleted=row["files_deleted"],
        warnings=json.loads(row["warnings"]) if row["warnings"] else [],
        errors=json.loads(row["errors"]) if row["errors"] else [],
        exit_code=row["exit_code"],
        result=row["result"],
        log_path=row["log_path"] or "",
    )


class HistoryStore:
    """Thin wrapper around the SQLite history database.

    Accepts an explicit ``db_path`` so tests can point it at a temporary
    file instead of the real ``$XDG_DATA_HOME/pdbu/history.sqlite3``.
    """

    def __init__(self, db_path: Path | None = None):
        self._db_path = db_path
        self._conn = _connect(db_path)
        self._lock = threading.Lock()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "HistoryStore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def save(self, record: OperationRecord) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO operations (
                    operation_id, operation_type, mode, dry_run, start_time, end_time,
                    source, destination, destination_identity, command_options,
                    files_examined, files_transferred, bytes_transferred, files_deleted,
                    warnings, errors, exit_code, result, log_path
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(operation_id) DO UPDATE SET
                    end_time=excluded.end_time,
                    files_examined=excluded.files_examined,
                    files_transferred=excluded.files_transferred,
                    bytes_transferred=excluded.bytes_transferred,
                    files_deleted=excluded.files_deleted,
                    warnings=excluded.warnings,
                    errors=excluded.errors,
                    exit_code=excluded.exit_code,
                    result=excluded.result,
                    log_path=excluded.log_path
                """,
                (
                    record.operation_id,
                    record.operation_type,
                    record.mode,
                    int(record.dry_run),
                    record.start_time,
                    record.end_time,
                    record.source,
                    record.destination,
                    record.destination_identity,
                    json.dumps(record.command_options),
                    record.files_examined,
                    record.files_transferred,
                    record.bytes_transferred,
                    record.files_deleted,
                    json.dumps(record.warnings),
                    json.dumps(record.errors),
                    record.exit_code,
                    record.result,
                    record.log_path,
                ),
            )
            self._conn.commit()

    def get(self, operation_id: str) -> OperationRecord | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM operations WHERE operation_id = ?", (operation_id,)
            ).fetchone()
        return _row_to_record(row) if row else None

    def list(
        self,
        *,
        operation_type: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[OperationRecord]:
        with self._lock:
            if operation_type:
                rows = self._conn.execute(
                    "SELECT * FROM operations WHERE operation_type = ? "
                    "ORDER BY start_time DESC LIMIT ? OFFSET ?",
                    (operation_type, limit, offset),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM operations ORDER BY start_time DESC LIMIT ? OFFSET ?",
                    (limit, offset),
                ).fetchall()
        return [_row_to_record(r) for r in rows]

    def last_successful_backup(self, mode: str | None = None) -> OperationRecord | None:
        with self._lock:
            if mode:
                row = self._conn.execute(
                    "SELECT * FROM operations WHERE operation_type='backup' AND result='success' "
                    "AND dry_run=0 AND mode=? ORDER BY end_time DESC LIMIT 1",
                    (mode,),
                ).fetchone()
            else:
                row = self._conn.execute(
                    "SELECT * FROM operations WHERE operation_type='backup' AND result='success' "
                    "AND dry_run=0 ORDER BY end_time DESC LIMIT 1"
                ).fetchone()
        return _row_to_record(row) if row else None

    def last_operation(self, operation_type: str | None = None) -> OperationRecord | None:
        results = self.list(operation_type=operation_type, limit=1)
        return results[0] if results else None

    def purge_older_than(self, retention_days: int) -> int:
        if retention_days <= 0:
            return 0
        cutoff = time.time() - retention_days * 86400
        with self._lock:
            cursor = self._conn.execute(
                "DELETE FROM operations WHERE start_time < ?", (cutoff,)
            )
            self._conn.commit()
            return cursor.rowcount
