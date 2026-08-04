"""Shared backup/restore orchestration used by both the CLI and the GUI.

This is the one place that wires together configuration, safety checks,
drive detection/unlocking, the rsync engine, and history/logging into a
complete "do a backup" or "do a restore" operation. Neither the CLI nor
the GUI implement any of that logic themselves — they only call into
:class:`BackupService` / :class:`RestoreService`, so behaviour is
guaranteed to match between the two front ends.
"""

from __future__ import annotations

import logging
import os
import socket
import time
from dataclasses import dataclass, field
from typing import Callable

from pdbu import devices, history, logging_setup, luks, restore, rsync_engine, safety, scheduler, ssh_backend
from pdbu.config import Config, DriveConfig
from pdbu.rsync_engine import ProgressEvent, RsyncOptions, SSHOptions

logger = logging.getLogger("pdbu.service")


class ServiceError(Exception):
    pass


class ConfirmationRequired(ServiceError):
    """Raised when an operation needs explicit user confirmation to proceed."""

    def __init__(self, message: str, *, detail: object = None):
        super().__init__(message)
        self.detail = detail


@dataclass
class DestinationSpec:
    kind: str  # "drive_a" | "drive_b" | "ssh"
    local_path: str | None = None
    ssh: SSHOptions | None = None
    drive_status: devices.DriveStatus | None = None
    destination_identity: str = ""


def ssh_options_from_config(cfg: Config) -> SSHOptions:
    return SSHOptions(
        host=cfg.ssh.host,
        port=cfg.ssh.port,
        username=cfg.ssh.username,
        identity_file=cfg.ssh.identity_file,
        host_alias=cfg.ssh.host_alias,
        strict_host_key_checking=cfg.ssh.strict_host_key_checking,
        connect_timeout_seconds=cfg.ssh.connect_timeout_seconds,
    )


def _drive_config(cfg: Config, drive_key: str) -> DriveConfig:
    if drive_key == "drive_a":
        return cfg.drive_a
    if drive_key == "drive_b":
        return cfg.drive_b
    raise ServiceError(f"Unknown drive key: {drive_key!r}")


def backup_hostname() -> str:
    """Short hostname of this machine, used to namespace backups on a drive.

    Lets one drive hold backups from several machines side by side
    (``<mount>/<backup_subdir>/<hostname>/``) instead of only ever
    supporting a single source machine per drive.
    """
    return socket.gethostname().split(".")[0]


def local_backup_dir(mountpoint: str, drive_cfg: DriveConfig) -> str:
    """Where backups for this drive actually live under its mount point.

    Empty ``backup_subdir`` opts back out into mirroring the mount point
    itself (the pre-hostnamed, whole-drive behaviour).
    """
    if not drive_cfg.backup_subdir:
        return mountpoint
    return os.path.join(mountpoint, drive_cfg.backup_subdir, backup_hostname())


class DashboardStatus:
    def __init__(
        self,
        last_backup: history.OperationRecord | None,
        schedule: "scheduler.ScheduleStatus",
        recommended_drive: str,
        drive_statuses: dict[str, devices.DriveStatus],
        ssh_configured: bool,
        recent_operations: list[history.OperationRecord],
    ):
        self.last_backup = last_backup
        self.schedule = schedule
        self.recommended_drive = recommended_drive
        self.drive_statuses = drive_statuses
        self.ssh_configured = ssh_configured
        self.recent_operations = recent_operations


class PdbuService:
    """Facade combining status, backup, restore and verify operations."""

    def __init__(self, cfg: Config, history_store: history.HistoryStore | None = None):
        self.cfg = cfg
        self.history = history_store or history.HistoryStore()

    def close(self) -> None:
        self.history.close()

    # -- status -------------------------------------------------------

    def drive_statuses(self) -> dict[str, devices.DriveStatus]:
        flat = devices.all_devices_flat()
        return {
            "drive_a": devices.detect_drive_status(self.cfg.drive_a, flat),
            "drive_b": devices.detect_drive_status(self.cfg.drive_b, flat),
        }

    def dashboard_status(self) -> DashboardStatus:
        last = self.history.last_successful_backup()
        sched = scheduler.compute_schedule(
            self.cfg.reminders,
            last.end_time if last else None,
            state=scheduler.load_state(),
        )
        recommended = devices.recommend_next_drive(last.mode if last else None)
        return DashboardStatus(
            last_backup=last,
            schedule=sched,
            recommended_drive=recommended,
            drive_statuses=self.drive_statuses(),
            ssh_configured=self.cfg.ssh.enabled,
            recent_operations=self.history.list(limit=10),
        )

    # -- drive preparation ---------------------------------------------

    def unlock_drive(self, drive_key: str, passphrase: str) -> devices.DriveStatus:
        drive_cfg = _drive_config(self.cfg, drive_key)
        status = self.drive_statuses()[drive_key]
        if not status.connected:
            raise ServiceError(f"{drive_cfg.name} is not connected")
        if not status.locked:
            return status
        try:
            luks.unlock_and_mount(status.luks_device_path, passphrase)
        except luks.LuksError as exc:
            raise ServiceError(f"Could not unlock {drive_cfg.name}: {exc}") from exc
        return self.drive_statuses()[drive_key]

    def mount_drive(self, drive_key: str) -> devices.DriveStatus:
        drive_cfg = _drive_config(self.cfg, drive_key)
        status = self.drive_statuses()[drive_key]
        if not status.connected:
            raise ServiceError(f"{drive_cfg.name} is not connected")
        if status.locked:
            raise ServiceError(f"{drive_cfg.name} is locked; unlock it first")
        if status.mounted:
            return status
        if not status.mapped_device_path:
            raise ServiceError(f"{drive_cfg.name} has no accessible block device to mount")
        try:
            luks.mount(status.mapped_device_path)
        except luks.LuksError as exc:
            raise ServiceError(f"Could not mount {drive_cfg.name}: {exc}") from exc
        return self.drive_statuses()[drive_key]

    def unmount_drive(self, drive_key: str, *, lock_after: bool | None = None) -> None:
        drive_cfg = _drive_config(self.cfg, drive_key)
        status = self.drive_statuses()[drive_key]
        if not status.mounted or not status.mapped_device_path:
            return
        should_lock = drive_cfg.lock_after_backup if lock_after is None else lock_after
        try:
            luks.unmount_and_lock(
                status.mapped_device_path,
                status.luks_device_path or "",
                also_lock=should_lock and status.is_luks,
            )
        except luks.LuksError as exc:
            raise ServiceError(f"Could not unmount {drive_cfg.name}: {exc}") from exc

    def prepare_local_destination(self, drive_key: str) -> DestinationSpec:
        drive_cfg = _drive_config(self.cfg, drive_key)
        status = self.drive_statuses()[drive_key]

        if not status.connected:
            raise ServiceError(f"{drive_cfg.name} is not connected")
        if status.locked:
            raise ServiceError(f"{drive_cfg.name} is locked; unlock it before backing up")
        if not status.mounted or not status.mountpoint:
            raise ServiceError(
                f"{drive_cfg.name} is not mounted. Refusing to back up to an "
                "ordinary directory that might not actually be the external drive."
            )

        identity_problems = devices.verify_mount_identity(status, drive_cfg)
        blocking = [p for p in identity_problems if "does not match" in p]
        if blocking:
            raise ServiceError("; ".join(blocking))

        local_path = local_backup_dir(status.mountpoint, drive_cfg)
        if local_path != status.mountpoint:
            try:
                os.makedirs(local_path, exist_ok=True)
            except OSError as exc:
                raise ServiceError(
                    f"Could not create backup directory {local_path}: {exc}"
                ) from exc

        return DestinationSpec(
            kind=drive_key,
            local_path=local_path,
            drive_status=status,
            destination_identity=status.filesystem_uuid or drive_cfg.filesystem_uuid,
        )

    def prepare_ssh_destination(self) -> DestinationSpec:
        if not self.cfg.ssh.enabled:
            raise ServiceError("SSH backup is not enabled in configuration")
        ssh_opts = ssh_options_from_config(self.cfg)
        identity = ssh_opts.connect_target
        return DestinationSpec(
            kind="ssh",
            local_path=self.cfg.ssh.destination,
            ssh=ssh_opts,
            destination_identity=identity,
        )

    def test_ssh_connection(self, *, password: str | None = None) -> ssh_backend.ConnectionTestResult:
        ssh_opts = ssh_options_from_config(self.cfg)
        return ssh_backend.test_connection(
            ssh_opts, use_password_auth=self.cfg.ssh.use_password_auth, password=password
        )

    def ssh_free_bytes(self) -> int:
        ssh_opts = ssh_options_from_config(self.cfg)
        return ssh_backend.remote_free_bytes(ssh_opts, self.cfg.ssh.destination)

    # -- backup ----------------------------------------------------------

    def _rsync_options(self) -> RsyncOptions:
        return RsyncOptions(
            delete=self.cfg.backup.delete_removed_files,
            preserve_acls=self.cfg.backup.preserve_acls,
            preserve_xattrs=self.cfg.backup.preserve_xattrs,
            preserve_hard_links=self.cfg.backup.preserve_hard_links,
            numeric_ids=self.cfg.backup.numeric_ids,
            bandwidth_limit_kbps=self.cfg.backup.bandwidth_limit_kbps,
            exclusions=self.cfg.exclusions.active_patterns(),
            extra_options=list(self.cfg.backup.extra_rsync_options),
        )

    def check_stale_lock(self) -> safety.OperationLock | None:
        return safety.read_stale_lock()

    def dry_run_backup(self, dest: DestinationSpec):
        source = self.cfg.source.home_directory
        report = safety.validate_backup_paths(
            source, dest.local_path, require_dest_exists=dest.ssh is None
        )
        report.raise_if_errors()
        options = self._rsync_options()
        pull = False
        destination = dest.local_path if dest.ssh is None else dest.local_path
        return rsync_engine.run_dry_run(source, destination, options, ssh=dest.ssh, pull=pull)

    def run_backup(
        self,
        dest: DestinationSpec,
        *,
        dry_run: bool = False,
        confirm_delete: Callable[[int], bool] | None = None,
        on_progress: Callable[[ProgressEvent], None] | None = None,
        cancel_event=None,
        force: bool = False,
    ) -> history.OperationRecord:
        source = self.cfg.source.home_directory

        # --- critical safety checks (spec section 10) ---
        source_report = safety.check_source(source)
        source_report.raise_if_errors()

        if dest.ssh is None:
            dest_report = safety.validate_backup_paths(
                source, dest.local_path, require_dest_exists=True
            )
            dest_report.raise_if_errors()
        else:
            dest_report = safety.check_destination(
                dest.local_path or ".", source=source, require_exists=False, allow_create=True
            )
            # Remote paths cannot be locally stat()'d as a directory;
            # existence is instead confirmed by the SSH connection test /
            # actual rsync invocation, so only dangerous-path checks apply.

        stale = safety.read_stale_lock()
        if stale is not None and not force:
            raise ConfirmationRequired(
                "A previous PDBU operation did not finish cleanly and may have left "
                "an incomplete backup. Review it before starting a new one.",
                detail=stale,
            )

        options = self._rsync_options()

        # Free-space check (local drives only — remote checked via ssh_backend
        # by the caller before invoking run_backup, since it needs a network
        # round trip best done once, not on every retry of this method).
        if dest.ssh is None and self.cfg.backup.verify_destination:
            estimate = self._estimate_transfer_bytes(source, dest, options)
            space_report = safety.check_free_space(dest.local_path, estimate)
            space_report.raise_if_errors()

        # Delete-count confirmation threshold.
        if options.delete:
            _, dry_report = rsync_engine.run_dry_run(
                source, dest.local_path, options, ssh=dest.ssh
            )
            if safety.check_delete_confirmation_required(
                dry_report.delete_count, self.cfg.backup.delete_confirm_threshold
            ):
                if confirm_delete is None or not confirm_delete(dry_report.delete_count):
                    raise ConfirmationRequired(
                        f"This backup would delete {dry_report.delete_count} files from the "
                        "destination to mirror the source. Confirm to proceed.",
                        detail=dry_report,
                    )

        operation_id = history.new_operation_id()
        record = history.OperationRecord(
            operation_id=operation_id,
            operation_type="backup",
            mode=dest.kind,
            dry_run=dry_run,
            source=source,
            destination=dest.local_path or "",
            destination_identity=dest.destination_identity,
            command_options=rsync_engine.build_rsync_command(
                source, dest.local_path or "", options, ssh=dest.ssh, dry_run=dry_run
            ),
        )

        safety.acquire_operation_lock(operation_id, "backup", source, dest.local_path or "")
        op_logger, op_handler = logging_setup.get_operation_logger(operation_id)
        record.log_path = str(logging_setup.operation_log_path(operation_id))
        op_logger.info("Backup started: %s -> %s (mode=%s, dry_run=%s)", source, dest.local_path, dest.kind, dry_run)
        op_logger.info("Command: %s", " ".join(record.command_options))
        try:
            result = rsync_engine.run_live(
                source,
                dest.local_path,
                options,
                ssh=dest.ssh,
                dry_run=dry_run,
                on_progress=on_progress,
                cancel_event=cancel_event,
            )
            stats = rsync_engine.parse_stats_output(result.stdout)

            record.end_time = time.time()
            record.exit_code = result.returncode
            record.files_transferred = stats.number_of_regular_files_transferred
            record.bytes_transferred = stats.total_transferred_file_size
            record.files_deleted = stats.number_of_deleted_files
            record.errors = [line for line in result.stderr.splitlines() if line.strip()]
            record.warnings = []
            if result.cancelled:
                record.result = "cancelled"
            elif result.returncode == 0:
                record.result = "success"
            elif result.returncode in (23, 24):
                record.result = "partial"
            else:
                record.result = "failed"

            op_logger.info("rsync exit code: %s", result.returncode)
            op_logger.info(
                "Files transferred: %s, bytes: %s, deleted: %s",
                record.files_transferred, record.bytes_transferred, record.files_deleted,
            )
            for err in record.errors:
                op_logger.error(err)
            op_logger.info("Result: %s", record.result)
            return record
        finally:
            op_logger.removeHandler(op_handler)
            op_handler.close()
            safety.release_operation_lock()
            self.history.save(record)
            if self.cfg.logging.retention_days:
                self.history.purge_older_than(self.cfg.logging.retention_days)

    def _estimate_transfer_bytes(self, source, dest, options) -> int:
        try:
            _, report = rsync_engine.run_dry_run(source, dest.local_path, options, ssh=dest.ssh)
            if report.stats and report.stats.total_transferred_file_size:
                return report.stats.total_transferred_file_size
        except Exception:  # noqa: BLE001 - estimation is best-effort
            pass
        return 0

    # -- verify ------------------------------------------------------

    def verify(self, dest: DestinationSpec):
        """Compare source and destination without modifying either."""
        source = self.cfg.source.home_directory
        options = self._rsync_options()
        result, report = rsync_engine.run_dry_run(source, dest.local_path, options, ssh=dest.ssh)
        return result, report

    # -- restore -------------------------------------------------------

    def dry_run_restore(self, request: "restore.RestoreRequest"):
        restore.validate_restore_request(request).raise_if_errors()
        return restore.preview_restore(request)

    def run_restore(
        self,
        request: "restore.RestoreRequest",
        *,
        on_progress: Callable[[ProgressEvent], None] | None = None,
        cancel_event=None,
    ) -> history.OperationRecord:
        restore.validate_restore_request(request).raise_if_errors()

        operation_id = history.new_operation_id()
        mode = "ssh" if request.ssh else "local"
        record = history.OperationRecord(
            operation_id=operation_id,
            operation_type="restore",
            mode=mode,
            source=request.backup_root,
            destination=request.destination,
        )
        safety.acquire_operation_lock(operation_id, "restore", request.backup_root, request.destination)
        op_logger, op_handler = logging_setup.get_operation_logger(operation_id)
        record.log_path = str(logging_setup.operation_log_path(operation_id))
        op_logger.info(
            "Restore started: %s -> %s (conflict_mode=%s, selected=%d path(s))",
            request.backup_root, request.destination, request.conflict_mode, len(request.selected_paths),
        )
        try:
            result = restore.run_restore(request, on_progress=on_progress, cancel_event=cancel_event)
            stats = rsync_engine.parse_stats_output(result.stdout)
            record.end_time = time.time()
            record.exit_code = result.returncode
            record.files_transferred = stats.number_of_regular_files_transferred
            record.bytes_transferred = stats.total_transferred_file_size
            record.errors = [line for line in result.stderr.splitlines() if line.strip()]
            if result.cancelled:
                record.result = "cancelled"
            elif result.returncode == 0:
                record.result = "success"
            else:
                record.result = "failed"
            op_logger.info("rsync exit code: %s", result.returncode)
            for err in record.errors:
                op_logger.error(err)
            op_logger.info("Result: %s", record.result)
            return record
        finally:
            op_logger.removeHandler(op_handler)
            op_handler.close()
            safety.release_operation_lock()
            self.history.save(record)
