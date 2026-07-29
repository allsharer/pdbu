"""Path and operation safety validation.

This module implements the critical safety checks described in the PDBU
specification: refusing dangerous destinations, detecting nested
source/destination paths, verifying free space, and detecting leftover
state from an interrupted previous operation. Nothing here executes
``rsync`` — it only decides whether it would be safe to do so.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from pdbu import paths

# Paths that must never be used as a mirrored (``--delete``) backup
# destination or as an unconstrained restore target. This is a defense in
# depth measure on top of the explicit source/self/nesting checks below.
DANGEROUS_DESTINATIONS: frozenset[str] = frozenset(
    {
        "/",
        "/home",
        "/root",
        "/etc",
        "/bin",
        "/sbin",
        "/usr",
        "/usr/local",
        "/boot",
        "/proc",
        "/sys",
        "/dev",
        "/lib",
        "/lib64",
        "/var",
        "/opt",
        "/run",
        "/mnt",
        "/media",
    }
)


class SafetyError(Exception):
    """Raised when one or more safety checks fail.

    ``issues`` contains every problem found so the caller can report them
    all at once rather than failing on the first one.
    """

    def __init__(self, issues: list[str]):
        self.issues = issues
        super().__init__("; ".join(issues))


@dataclass
class SafetyReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def raise_if_errors(self) -> None:
        if self.errors:
            raise SafetyError(self.errors)


def resolve_path(path: str | Path) -> Path:
    """Expand ``~`` and resolve to an absolute, symlink-free path.

    ``strict=False`` is used deliberately: the destination may not exist
    yet (e.g. an unmounted drive), and callers must not treat a
    "path does not exist" condition as a symlink-resolution crash.
    """
    return Path(os.path.expanduser(str(path))).resolve(strict=False)


def is_subpath(child: Path, parent: Path) -> bool:
    """True if ``child`` is ``parent`` itself or lies underneath it."""
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def is_dangerous_path(path: Path) -> bool:
    return str(path) in DANGEROUS_DESTINATIONS


def check_source(source: str | Path) -> SafetyReport:
    report = SafetyReport()
    resolved = resolve_path(source)

    if not resolved.exists():
        report.errors.append(f"Source directory does not exist: {resolved}")
        return report
    if not resolved.is_dir():
        report.errors.append(f"Source is not a directory: {resolved}")
        return report
    if not os.access(resolved, os.R_OK | os.X_OK):
        report.errors.append(f"Source directory is not readable: {resolved}")
    if is_dangerous_path(resolved):
        report.errors.append(f"Refusing to use a system path as the source: {resolved}")
    return report


def check_destination(
    destination: str | Path,
    *,
    source: str | Path,
    require_exists: bool = True,
    allow_create: bool = False,
) -> SafetyReport:
    """Validate a backup or restore destination against ``source``."""
    report = SafetyReport()
    dest = resolve_path(destination)
    src = resolve_path(source)

    if is_dangerous_path(dest):
        report.errors.append(
            f"Refusing to use a protected system path as the destination: {dest}"
        )

    if dest == src:
        report.errors.append("Destination must not be the same as the source directory")

    if is_subpath(src, dest) and src != dest:
        report.errors.append(
            f"Source ({src}) is located inside destination ({dest}); refusing to proceed"
        )
    if is_subpath(dest, src) and src != dest:
        report.errors.append(
            f"Destination ({dest}) is located inside source ({src}); refusing to proceed"
        )

    root = Path(dest.anchor or "/")
    if dest == root:
        report.errors.append("Destination must not be the filesystem root")

    if not dest.exists():
        if require_exists and not allow_create:
            report.errors.append(f"Destination does not exist: {dest}")
    elif not dest.is_dir():
        report.errors.append(f"Destination exists but is not a directory: {dest}")
    elif not os.access(dest, os.W_OK | os.X_OK):
        report.errors.append(f"Destination directory is not writable: {dest}")

    return report


def validate_backup_paths(
    source: str | Path,
    destination: str | Path,
    *,
    require_dest_exists: bool = True,
) -> SafetyReport:
    report = check_source(source)
    dest_report = check_destination(
        destination, source=source, require_exists=require_dest_exists
    )
    report.errors.extend(dest_report.errors)
    report.warnings.extend(dest_report.warnings)
    return report


def check_free_space(destination: str | Path, required_bytes: int) -> SafetyReport:
    report = SafetyReport()
    dest = resolve_path(destination)
    try:
        usage = shutil.disk_usage(dest)
    except OSError as exc:
        report.errors.append(f"Could not determine free space at {dest}: {exc}")
        return report

    # Small safety margin so a backup doesn't fail (or corrupt state) by
    # running the destination completely out of space mid-transfer.
    margin = max(int(required_bytes * 0.05), 100 * 1024 * 1024)
    if usage.free < required_bytes + margin:
        report.errors.append(
            "Insufficient free space at destination: "
            f"{usage.free / (1024**3):.2f} GiB free, "
            f"need approximately {(required_bytes + margin) / (1024**3):.2f} GiB"
        )
    return report


def check_delete_confirmation_required(deleted_count: int, threshold: int) -> bool:
    """True when the number of files to delete exceeds the safety threshold."""
    return deleted_count > threshold


# ---------------------------------------------------------------------------
# Incomplete-operation detection
# ---------------------------------------------------------------------------

@dataclass
class OperationLock:
    operation_id: str
    kind: str
    pid: int
    started_at: float
    source: str
    destination: str


def _lock_path() -> Path:
    return paths.operation_lock_file()


def read_stale_lock() -> OperationLock | None:
    """Return details of a leftover lock file from a previous run, if any.

    A lock is considered stale (i.e. worth reporting) when its file exists
    and the recorded PID is no longer a running process.
    """
    lock_path = _lock_path()
    if not lock_path.exists():
        return None
    try:
        data = json.loads(lock_path.read_text(encoding="utf-8"))
        lock = OperationLock(**data)
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return OperationLock(
            operation_id="unknown",
            kind="unknown",
            pid=-1,
            started_at=0.0,
            source="",
            destination="",
        )

    if _pid_alive(lock.pid):
        return None
    return lock


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def acquire_operation_lock(operation_id: str, kind: str, source: str, destination: str) -> None:
    lock_path = _lock_path()
    lock_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = OperationLock(
        operation_id=operation_id,
        kind=kind,
        pid=os.getpid(),
        started_at=time.time(),
        source=source,
        destination=destination,
    )
    lock_path.write_text(json.dumps(lock.__dict__), encoding="utf-8")
    os.chmod(lock_path, 0o600)


def release_operation_lock() -> None:
    lock_path = _lock_path()
    try:
        lock_path.unlink()
    except FileNotFoundError:
        pass
