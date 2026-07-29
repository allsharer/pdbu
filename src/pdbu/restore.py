"""Restore engine: full or selected restore from a local or SSH backup.

Builds on :mod:`pdbu.rsync_engine` for the actual transfer and on
:mod:`pdbu.safety` for destination validation, so the CLI and GUI share
one implementation of "what restoring actually does."
"""

from __future__ import annotations

import tempfile
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from pdbu import rsync_engine, safety
from pdbu.rsync_engine import ProgressEvent, RsyncOptions, RunResult, SSHOptions


class ConflictMode(str, Enum):
    OVERWRITE = "overwrite"
    SKIP_EXISTING = "skip_existing"
    NEWER_ONLY = "newer_only"
    ASK = "ask"
    RENAME_EXISTING = "rename_existing"


class RestoreError(Exception):
    pass


@dataclass
class RestoreRequest:
    backup_root: str
    destination: str
    selected_paths: list[str] = field(default_factory=list)  # empty => full restore
    conflict_mode: ConflictMode = ConflictMode.ASK
    mirror_delete: bool = False
    exclusions: list[str] = field(default_factory=list)
    ssh: SSHOptions | None = None  # set when restoring from an SSH backup
    bandwidth_limit_kbps: int = 0


def validate_restore_request(request: RestoreRequest) -> safety.SafetyReport:
    """Apply the same dangerous-path checks used for backups to restores.

    A restore destination is just as capable of catastrophic data loss
    (e.g. restoring on top of ``/`` or the backup root itself) as a
    backup destination, so it goes through the same validator.
    """
    if request.ssh is not None:
        # Source lives on a remote host; only the local destination can be
        # validated here (existence, writability, not a protected path).
        report = safety.SafetyReport()
        dest = safety.resolve_path(request.destination)
        if safety.is_dangerous_path(dest):
            report.errors.append(
                f"Refusing to restore onto a protected system path: {dest}"
            )
        root = Path(dest.anchor or "/")
        if dest == root:
            report.errors.append("Restore destination must not be the filesystem root")
        return report
    return safety.validate_backup_paths(
        request.backup_root, request.destination, require_dest_exists=False
    )


def _validate_selected_paths(paths: list[str]) -> list[str]:
    """Reject any relative path that could escape the backup root."""
    problems = []
    for rel in paths:
        if not rel or rel.startswith("/") or rel == "." or ".." in Path(rel).parts:
            problems.append(f"Refusing unsafe selected path: {rel!r}")
    return problems


def _conflict_options(mode: ConflictMode) -> tuple[list[str], bool]:
    """Return (extra rsync args, needs_dry_run_preview_first) for a conflict mode."""
    if mode == ConflictMode.OVERWRITE:
        return [], False
    if mode == ConflictMode.SKIP_EXISTING:
        return ["--ignore-existing"], False
    if mode == ConflictMode.NEWER_ONLY:
        return ["--update"], False
    if mode == ConflictMode.RENAME_EXISTING:
        suffix = f".bak-{time.strftime('%Y%m%dT%H%M%S')}"
        return ["--backup", f"--suffix={suffix}"], False
    if mode == ConflictMode.ASK:
        # Conflicts must be enumerated by the caller (CLI/GUI) via
        # preview_restore() and resolved into an explicit path selection
        # before a real run — rsync itself has no "ask" mode.
        return [], True
    raise RestoreError(f"Unknown conflict mode: {mode}")


def _write_files_from(paths: list[str]) -> str:
    handle = tempfile.NamedTemporaryFile(
        mode="w", prefix="pdbu-restore-", suffix=".list", delete=False
    )
    with handle:
        for rel in paths:
            handle.write(rel + "\n")
    return handle.name


def _build_options(request: RestoreRequest) -> RsyncOptions:
    extra_args, _ = _conflict_options(request.conflict_mode)
    return RsyncOptions(
        delete=request.mirror_delete,
        exclusions=request.exclusions,
        extra_options=extra_args,
        bandwidth_limit_kbps=request.bandwidth_limit_kbps,
    )


def preview_restore(request: RestoreRequest, *, timeout: float | None = None):
    """Dry-run the restore and return the parsed change report.

    For :attr:`ConflictMode.ASK`, the "updated" list in the returned
    report is exactly the set of conflicting files a caller should
    present to the user for a per-file decision.
    """
    problems = _validate_selected_paths(request.selected_paths)
    if problems:
        raise RestoreError("; ".join(problems))

    options = _build_options(request)
    files_from = None
    if request.selected_paths:
        files_from = _write_files_from(request.selected_paths)
    try:
        pull = request.ssh is not None
        result, report = rsync_engine.run_dry_run(
            request.backup_root,
            request.destination,
            options,
            ssh=request.ssh,
            timeout=timeout,
            pull=pull,
            files_from=files_from,
        )
    finally:
        if files_from:
            Path(files_from).unlink(missing_ok=True)
    return result, report


def run_restore(
    request: RestoreRequest,
    *,
    on_progress=None,
    cancel_event=None,
) -> RunResult:
    problems = _validate_selected_paths(request.selected_paths)
    if problems:
        raise RestoreError("; ".join(problems))

    options = _build_options(request)
    files_from = None
    if request.selected_paths:
        files_from = _write_files_from(request.selected_paths)
    try:
        pull = request.ssh is not None
        return rsync_engine.run_live(
            request.backup_root,
            request.destination,
            options,
            ssh=request.ssh,
            on_progress=on_progress,
            cancel_event=cancel_event,
            pull=pull,
            files_from=files_from,
        )
    finally:
        if files_from:
            Path(files_from).unlink(missing_ok=True)
