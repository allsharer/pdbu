"""PDBU command-line interface.

Every subcommand here is a thin wrapper around :mod:`pdbu.service`
(:class:`~pdbu.service.PdbuService`) — the exact same layer the GTK GUI
uses — so CLI and GUI behaviour never diverge.
"""

from __future__ import annotations

import getpass
import json
import subprocess
import sys
import time

import click

from pdbu import (
    config as config_mod,
    devices,
    history as history_mod,
    luks,
    notifications,
    paths,
    restore as restore_mod,
    rsync_engine,
    safety,
    scheduler,
    secrets_store,
    service as service_mod,
    ssh_backend,
)
from pdbu import __version__

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_CONFIRMATION_REQUIRED = 3
EXIT_SAFETY = 4
EXIT_NOT_FOUND = 5


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _format_bytes(n: int | None) -> str:
    if n is None:
        return "unknown"
    value = float(n)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} TiB"


def _format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "unknown"
    seconds = int(seconds)
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes}m {secs}s"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def _format_timestamp(ts: float | None) -> str:
    if ts is None:
        return "never"
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


def _format_ago(ts: float | None) -> str:
    if ts is None:
        return "never"
    delta = time.time() - ts
    if delta < 60:
        return "just now"
    if delta < 3600:
        return f"{int(delta // 60)} minute(s) ago"
    if delta < 86400:
        return f"{delta / 3600:.1f} hour(s) ago"
    return f"{delta / 86400:.1f} day(s) ago"


def echo_err(message: str) -> None:
    click.secho(message, fg="red", err=True)


def echo_warn(message: str) -> None:
    click.secho(message, fg="yellow", err=True)


# ---------------------------------------------------------------------------
# Context
# ---------------------------------------------------------------------------

class CliContext:
    def __init__(self):
        self.verbose = False
        self.quiet = False
        self.assume_yes = False
        self._cfg = None

    @property
    def cfg(self) -> config_mod.Config:
        if self._cfg is None:
            self._cfg = config_mod.load()
        return self._cfg

    def service(self) -> service_mod.PdbuService:
        return service_mod.PdbuService(self.cfg)


pass_ctx = click.make_pass_decorator(CliContext)


@click.group()
@click.version_option(__version__, prog_name="pdbu")
@click.option("-v", "--verbose", is_flag=True, help="Show detailed output.")
@click.option("-q", "--quiet", is_flag=True, help="Only show errors.")
@click.option("-y", "--yes", "assume_yes", is_flag=True, help="Assume yes to confirmation prompts.")
@click.pass_context
def cli(ctx: click.Context, verbose: bool, quiet: bool, assume_yes: bool):
    """PDBU — Personal Directory Backup Utility.

    Backs up and restores a /home directory using rsync, to LUKS-encrypted
    external drives or an SSH remote.
    """
    from pdbu import logging_setup

    paths.ensure_dirs()
    logging_setup.setup_app_logging(verbose=verbose, quiet=quiet)
    obj = CliContext()
    obj.verbose = verbose
    obj.quiet = quiet
    obj.assume_yes = assume_yes
    ctx.obj = obj


# ---------------------------------------------------------------------------
# Drive readiness helper (shared by backup/restore/verify)
# ---------------------------------------------------------------------------

def _ensure_drive_ready(clictx: CliContext, svc: service_mod.PdbuService, drive_key: str):
    drive_cfg = getattr(svc.cfg, drive_key)
    status = svc.drive_statuses()[drive_key]
    if not status.connected:
        raise click.ClickException(
            f"{drive_cfg.name} is not connected. Plug it in and try again, or run "
            f"'pdbu drives' to check status."
        )
    if status.locked:
        secret_key = f"luks:{drive_key}"
        passphrase = secrets_store.lookup(secret_key)
        if not passphrase:
            if clictx.quiet or (not sys.stdin.isatty() and not clictx.assume_yes):
                raise click.ClickException(
                    f"{drive_cfg.name} is locked and no saved passphrase is available. "
                    "Run interactively to unlock it."
                )
            passphrase = getpass.getpass(f"Passphrase to unlock {drive_cfg.name}: ")
        try:
            svc.unlock_drive(drive_key, passphrase)
        except service_mod.ServiceError as exc:
            raise click.ClickException(str(exc)) from exc
        if not secrets_store.lookup(secret_key) and secrets_store.available() and sys.stdin.isatty():
            if click.confirm(
                "Save this passphrase in the desktop keyring for next time?", default=False
            ):
                try:
                    secrets_store.store(secret_key, passphrase, label=f"PDBU {drive_cfg.name} passphrase")
                except secrets_store.SecretStoreUnavailable as exc:
                    echo_warn(str(exc))
    status = svc.drive_statuses()[drive_key]
    if not status.mounted:
        try:
            svc.mount_drive(drive_key)
        except service_mod.ServiceError as exc:
            raise click.ClickException(str(exc)) from exc
    try:
        return svc.prepare_local_destination(drive_key)
    except service_mod.ServiceError as exc:
        raise click.ClickException(str(exc)) from exc


def _resolve_backup_destination(clictx: CliContext, svc, drive_a, drive_b, use_ssh):
    chosen = sum([bool(drive_a), bool(drive_b), bool(use_ssh)])
    if chosen > 1:
        raise click.UsageError("Only one of --drive-a, --drive-b, --ssh may be given.")
    if drive_a:
        return _ensure_drive_ready(clictx, svc, "drive_a")
    if drive_b:
        return _ensure_drive_ready(clictx, svc, "drive_b")
    if use_ssh:
        try:
            return svc.prepare_ssh_destination()
        except service_mod.ServiceError as exc:
            raise click.ClickException(str(exc)) from exc

    dash = svc.dashboard_status()
    recommended = dash.recommended_drive
    if not clictx.quiet:
        click.echo(f"No destination specified; using recommended drive: {recommended}")
    return _ensure_drive_ready(clictx, svc, recommended)


def _print_progress(event: rsync_engine.ProgressEvent) -> None:
    if event.current_file:
        click.echo(f"  {event.current_file}")
    elif event.percent is not None:
        click.echo(
            f"  {event.percent:3d}%  {_format_bytes(event.bytes_transferred)}  "
            f"{event.speed or ''}  ETA {event.eta or '?'}",
            nl=False,
        )
        click.echo("\r", nl=False)


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--json", "as_json", is_flag=True, help="Output machine-readable JSON.")
@pass_ctx
def status(clictx: CliContext, as_json: bool):
    """Show a dashboard-style summary of backup status."""
    svc = clictx.service()
    dash = svc.dashboard_status()

    if as_json:
        data = {
            "last_backup": dash.last_backup.to_json_dict() if dash.last_backup else None,
            "next_due_at": dash.schedule.next_due_at,
            "overdue": dash.schedule.overdue,
            "due_now": dash.schedule.due_now,
            "recommended_drive": dash.recommended_drive,
            "ssh_configured": dash.ssh_configured,
            "drives": {
                key: {
                    "label": s.label,
                    "connected": s.connected,
                    "locked": s.locked,
                    "mounted": s.mounted,
                    "identity_verified": s.identity_verified,
                }
                for key, s in dash.drive_statuses.items()
            },
        }
        click.echo(json.dumps(data, indent=2))
        return

    click.echo("PDBU Status")
    click.echo("-----------")
    if dash.last_backup:
        click.echo(f"Last successful backup: {_format_timestamp(dash.last_backup.end_time)} ({_format_ago(dash.last_backup.end_time)})")
        click.echo(f"  Drive used: {dash.last_backup.mode}")
    else:
        click.echo("Last successful backup: never")
    click.echo(f"Next backup due: {_format_timestamp(dash.schedule.next_due_at)}")
    click.echo(f"Backup due now: {'yes' if dash.schedule.due_now else 'no'}")
    click.echo(f"Recommended next drive: {dash.recommended_drive}")
    click.echo()
    for key, s in dash.drive_statuses.items():
        state = "not connected"
        if s.connected:
            state = "locked" if s.locked else ("mounted" if s.mounted else "unlocked, not mounted")
        click.echo(f"{s.label} ({key}): {state}")
    click.echo(f"SSH backup configured: {'yes' if dash.ssh_configured else 'no'}")
    if dash.recent_operations:
        click.echo()
        click.echo("Recent operations:")
        for op in dash.recent_operations[:5]:
            click.echo(f"  {_format_timestamp(op.start_time)}  {op.operation_type:8s} {op.mode:8s} {op.result}")
    svc.close()


# ---------------------------------------------------------------------------
# backup
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--drive-a", is_flag=True, help="Back up to Backup Drive A.")
@click.option("--drive-b", is_flag=True, help="Back up to Backup Drive B.")
@click.option("--ssh", "use_ssh", is_flag=True, help="Back up to the configured SSH destination.")
@click.option("--dry-run", is_flag=True, help="Show what would happen without changing anything.")
@pass_ctx
def backup(clictx: CliContext, drive_a: bool, drive_b: bool, use_ssh: bool, dry_run: bool):
    """Run a backup of the configured home directory."""
    svc = clictx.service()

    stale = svc.check_stale_lock()
    if stale is not None and not clictx.assume_yes:
        echo_warn(
            f"A previous operation ({stale.kind}, started {_format_timestamp(stale.started_at)}) "
            "did not finish cleanly."
        )
        if not click.confirm("Continue anyway?", default=False):
            sys.exit(EXIT_CONFIRMATION_REQUIRED)

    dest = _resolve_backup_destination(clictx, svc, drive_a, drive_b, use_ssh)

    if dry_run:
        try:
            result, report = svc.dry_run_backup(dest)
        except safety.SafetyError as exc:
            for issue in exc.issues:
                echo_err(f"  - {issue}")
            sys.exit(EXIT_SAFETY)
        _print_dry_run_report(report)
        sys.exit(EXIT_OK if result.ok else EXIT_ERROR)

    def confirm_delete(n: int) -> bool:
        if clictx.assume_yes:
            return True
        if clictx.quiet or not sys.stdin.isatty():
            return False
        return click.confirm(
            f"This backup will delete {n} file(s) from the destination to mirror the "
            "source. Continue?",
            default=False,
        )

    on_progress = None if clictx.quiet else _print_progress

    try:
        record = svc.run_backup(
            dest,
            confirm_delete=confirm_delete,
            on_progress=on_progress,
            force=clictx.assume_yes,
        )
    except service_mod.ConfirmationRequired as exc:
        echo_err(str(exc))
        sys.exit(EXIT_CONFIRMATION_REQUIRED)
    except safety.SafetyError as exc:
        for issue in exc.issues:
            echo_err(f"  - {issue}")
        sys.exit(EXIT_SAFETY)
    except service_mod.ServiceError as exc:
        echo_err(str(exc))
        sys.exit(EXIT_ERROR)
    finally:
        if dest.kind in ("drive_a", "drive_b"):
            drive_cfg = getattr(svc.cfg, dest.kind)
            if drive_cfg.lock_after_backup:
                try:
                    svc.unmount_drive(dest.kind)
                except service_mod.ServiceError as exc:
                    echo_warn(str(exc))

    if not clictx.quiet:
        click.echo()
        click.echo(f"Result: {record.result}")
        click.echo(f"Files transferred: {record.files_transferred}")
        click.echo(f"Data transferred: {_format_bytes(record.bytes_transferred)}")
        click.echo(f"Files deleted: {record.files_deleted}")
        click.echo(f"Duration: {_format_duration(record.duration_seconds)}")
        if record.errors:
            echo_err(f"{len(record.errors)} error line(s); see 'pdbu logs {record.operation_id}'")

    svc.close()
    sys.exit(EXIT_OK if record.result == "success" else EXIT_ERROR)


def _print_dry_run_report(report: rsync_engine.DryRunReport) -> None:
    click.echo(f"Would add:    {len(report.added)} file(s)/dir(s)")
    click.echo(f"Would update: {len(report.updated)} file(s)")
    click.echo(f"Would delete: {len(report.deleted)} file(s)")
    if report.stats and report.stats.total_transferred_file_size is not None:
        click.echo(f"Estimated data to transfer: {_format_bytes(report.stats.total_transferred_file_size)}")
    if report.warnings:
        click.echo(f"Warnings: {len(report.warnings)}")
        for w in report.warnings[:10]:
            echo_warn(f"  {w}")


# ---------------------------------------------------------------------------
# restore
# ---------------------------------------------------------------------------

@cli.command()
@click.option(
    "--source",
    "source_choice",
    type=click.Choice(["drive-a", "drive-b", "ssh"]),
    required=True,
    help="Where to restore from.",
)
@click.option("--path", "selected_paths", multiple=True, help="Restore only this relative path (repeatable).")
@click.option("--destination", "destination", help="Restore destination (default: original home directory).")
@click.option(
    "--conflict",
    type=click.Choice(["overwrite", "skip", "newer", "ask", "rename"]),
    default="ask",
    help="How to handle files that already exist at the destination.",
)
@click.option("--mirror-delete", is_flag=True, help="Also delete files at the destination absent from the backup.")
@click.option("--dry-run", is_flag=True, help="Preview the restore without changing anything.")
@pass_ctx
def restore(
    clictx: CliContext,
    source_choice: str,
    selected_paths: tuple[str, ...],
    destination: str | None,
    conflict: str,
    mirror_delete: bool,
    dry_run: bool,
):
    """Restore files from a backup."""
    svc = clictx.service()
    cfg = svc.cfg

    drive_key = {"drive-a": "drive_a", "drive-b": "drive_b"}.get(source_choice)
    ssh_opts = None
    if drive_key:
        dest_spec = _ensure_drive_ready(clictx, svc, drive_key)
        backup_root = dest_spec.local_path
    else:
        if not cfg.ssh.enabled:
            raise click.ClickException("SSH is not configured.")
        ssh_opts = service_mod.ssh_options_from_config(cfg)
        backup_root = cfg.ssh.destination

    conflict_mode = {
        "overwrite": restore_mod.ConflictMode.OVERWRITE,
        "skip": restore_mod.ConflictMode.SKIP_EXISTING,
        "newer": restore_mod.ConflictMode.NEWER_ONLY,
        "ask": restore_mod.ConflictMode.ASK,
        "rename": restore_mod.ConflictMode.RENAME_EXISTING,
    }[conflict]

    final_destination = destination or cfg.source.home_directory

    request = restore_mod.RestoreRequest(
        backup_root=backup_root,
        destination=final_destination,
        selected_paths=list(selected_paths),
        conflict_mode=(
            restore_mod.ConflictMode.OVERWRITE if conflict_mode == restore_mod.ConflictMode.ASK else conflict_mode
        ),
        mirror_delete=mirror_delete,
        ssh=ssh_opts,
    )

    try:
        report_check = restore_mod.validate_restore_request(request)
        report_check.raise_if_errors()
    except safety.SafetyError as exc:
        for issue in exc.issues:
            echo_err(f"  - {issue}")
        sys.exit(EXIT_SAFETY)

    try:
        preview_result, preview_report = svc.dry_run_restore(request)
    except restore_mod.RestoreError as exc:
        raise click.ClickException(str(exc)) from exc

    if conflict_mode == restore_mod.ConflictMode.ASK and preview_report.updated:
        conflicts = preview_report.updated
        click.echo(f"{len(conflicts)} file(s) already exist at the destination and differ.")
        if len(conflicts) <= 20 and sys.stdin.isatty() and not clictx.assume_yes:
            keep = []
            for path in conflicts:
                if click.confirm(f"Overwrite {path}?", default=False):
                    keep.append(path)
            skipped = [p for p in conflicts if p not in keep]
            if skipped:
                request.exclusions = list(request.exclusions) + skipped
        elif clictx.assume_yes:
            pass  # overwrite everything
        else:
            if not click.confirm("Overwrite all conflicting files?", default=False):
                request.conflict_mode = restore_mod.ConflictMode.SKIP_EXISTING
        request.conflict_mode = restore_mod.ConflictMode.OVERWRITE

    if dry_run:
        _print_dry_run_report(preview_report)
        sys.exit(EXIT_OK)

    click.echo(f"About to restore {len(preview_report.added) + len(preview_report.updated)} file(s) "
               f"to {final_destination}.")
    if not clictx.assume_yes:
        if not click.confirm("Proceed with restore?", default=False):
            sys.exit(EXIT_CONFIRMATION_REQUIRED)

    on_progress = None if clictx.quiet else _print_progress
    try:
        record = svc.run_restore(request, on_progress=on_progress)
    except restore_mod.RestoreError as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo(f"Result: {record.result}")
    svc.close()
    sys.exit(EXIT_OK if record.result == "success" else EXIT_ERROR)


# ---------------------------------------------------------------------------
# drives
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--json", "as_json", is_flag=True)
@pass_ctx
def drives(clictx: CliContext, as_json: bool):
    """Show connected/configured backup drive status."""
    svc = clictx.service()
    cfg = svc.cfg
    statuses = svc.drive_statuses()
    last = svc.history.last_successful_backup()
    recommended = devices.recommend_next_drive(last.mode if last else None)

    if as_json:
        data = {}
        for key, s in statuses.items():
            drive_cfg = cfg.drive_a if key == "drive_a" else cfg.drive_b
            backup_path = (
                service_mod.local_backup_dir(s.mountpoint, drive_cfg)
                if s.mounted and s.mountpoint and drive_cfg.backup_subdir
                else None
            )
            data[key] = {
                "label": s.label,
                "configured": s.configured,
                "connected": s.connected,
                "locked": s.locked,
                "mounted": s.mounted,
                "mountpoint": s.mountpoint,
                "backup_path": backup_path,
                "identity_verified": s.identity_verified,
            }
        data["recommended_next"] = recommended
        data["last_used"] = last.mode if last else None
        click.echo(json.dumps(data, indent=2))
        svc.close()
        return

    for key, s in statuses.items():
        marker = " (recommended next)" if key == recommended else ""
        marker += " (used last time)" if last and last.mode == key else ""
        click.echo(f"{s.label} [{key}]{marker}")
        if not s.configured:
            click.echo("  Not configured (no UUID set). Run 'pdbu config --edit'.")
            continue
        click.echo(f"  Connected: {s.connected}")
        if s.connected:
            click.echo(f"  Encrypted (LUKS): {s.is_luks}")
            click.echo(f"  Locked: {s.locked}")
            click.echo(f"  Mounted: {s.mounted}" + (f" at {s.mountpoint}" if s.mounted else ""))
            drive_cfg = cfg.drive_a if key == "drive_a" else cfg.drive_b
            if s.mounted and s.mountpoint and drive_cfg.backup_subdir:
                backup_path = service_mod.local_backup_dir(s.mountpoint, drive_cfg)
                click.echo(f"  Backup path: {backup_path}")
            click.echo(f"  Identity verified: {s.identity_verified}")
    svc.close()


# ---------------------------------------------------------------------------
# history
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--json", "as_json", is_flag=True)
@click.option("--limit", default=20, show_default=True)
@click.option("--type", "op_type", type=click.Choice(["backup", "restore", "verify"]), default=None)
@pass_ctx
def history(clictx: CliContext, as_json: bool, limit: int, op_type: str | None):
    """Show backup/restore history."""
    svc = clictx.service()
    records = svc.history.list(operation_type=op_type, limit=limit)

    if as_json:
        click.echo(json.dumps([r.to_json_dict() for r in records], indent=2))
        svc.close()
        return

    if not records:
        click.echo("No history yet.")
        svc.close()
        return

    for r in records:
        click.echo(
            f"{_format_timestamp(r.start_time)}  {r.operation_type:8s} {r.mode:8s} "
            f"{r.result:10s} {_format_duration(r.duration_seconds):>10s}  "
            f"{r.files_transferred or 0} files, {_format_bytes(r.bytes_transferred)}"
        )
    svc.close()


# ---------------------------------------------------------------------------
# logs
# ---------------------------------------------------------------------------

@cli.command()
@click.argument("operation_id", required=False)
@click.option("--tail", default=100, show_default=True, help="Number of lines to show.")
@pass_ctx
def logs(clictx: CliContext, operation_id: str | None, tail: int):
    """Show the log file for an operation, or list recent operations' logs."""
    from pdbu import logging_setup

    svc = clictx.service()
    if operation_id is None:
        records = svc.history.list(limit=20)
        for r in records:
            click.echo(f"{r.operation_id}  {r.operation_type}  {r.result}  {logging_setup.operation_log_path(r.operation_id)}")
        svc.close()
        return

    log_path = logging_setup.operation_log_path(operation_id)
    if not log_path.exists():
        raise click.ClickException(f"No log file found for operation {operation_id}")
    lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    for line in lines[-tail:]:
        click.echo(line)
    svc.close()


# ---------------------------------------------------------------------------
# verify
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--drive-a", is_flag=True)
@click.option("--drive-b", is_flag=True)
@click.option("--ssh", "use_ssh", is_flag=True)
@click.option("--json", "as_json", is_flag=True)
@pass_ctx
def verify(clictx: CliContext, drive_a: bool, drive_b: bool, use_ssh: bool, as_json: bool):
    """Compare source and destination without modifying either."""
    svc = clictx.service()
    dest = _resolve_backup_destination(clictx, svc, drive_a, drive_b, use_ssh)
    result, report = svc.verify(dest)

    if as_json:
        click.echo(json.dumps({
            "added": report.added,
            "updated": report.updated,
            "deleted": report.deleted,
            "total_changes": report.total_changes,
            "identical": report.total_changes == 0,
        }, indent=2))
        svc.close()
        sys.exit(EXIT_OK)

    if report.total_changes == 0:
        click.echo("Source and destination are identical.")
    else:
        _print_dry_run_report(report)
    svc.close()
    sys.exit(EXIT_OK)


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------

@cli.command("config")
@click.option("--edit", "do_edit", is_flag=True, help="Open the config file in $EDITOR.")
@click.option("--show", "do_show", is_flag=True, help="Print the current configuration.")
@click.option("--json", "as_json", is_flag=True)
@pass_ctx
def config_cmd(clictx: CliContext, do_edit: bool, do_show: bool, as_json: bool):
    """Show or edit PDBU configuration."""
    config_path = config_mod.ensure_default_config()

    if do_edit:
        click.edit(filename=str(config_path))
        try:
            config_mod.load(config_path)
        except config_mod.ConfigError as exc:
            raise click.ClickException(str(exc)) from exc
        click.echo("Configuration is valid.")
        return

    cfg = config_mod.load(config_path)
    if as_json:
        click.echo(json.dumps(config_mod.config_to_dict(cfg), indent=2))
        return
    click.echo(config_path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# schedule
# ---------------------------------------------------------------------------

def _parse_interval(value: str) -> float:
    value = value.strip().lower()
    if value.endswith("d"):
        return float(value[:-1])
    if value.endswith("h"):
        return float(value[:-1]) / 24
    return float(value)


@cli.command()
@click.option("--interval", "interval_str", help="New reminder interval, e.g. 7d, 24h, or a number of days.")
@pass_ctx
def schedule(clictx: CliContext, interval_str: str | None):
    """Show or update the backup reminder schedule."""
    cfg = clictx.cfg
    if interval_str:
        try:
            days = _parse_interval(interval_str)
        except ValueError as exc:
            raise click.UsageError(f"Could not parse interval: {interval_str!r}") from exc
        cfg.reminders.interval_days = days
        config_mod.save(cfg)
        click.echo(f"Reminder interval set to {days} day(s).")
        return

    svc = clictx.service()
    last = svc.history.last_successful_backup()
    sched = scheduler.compute_schedule(cfg.reminders, last.end_time if last else None)
    click.echo(f"Interval: {cfg.reminders.interval_days} day(s)")
    click.echo(f"Last successful backup: {_format_timestamp(sched.last_successful_backup)}")
    click.echo(f"Next due: {_format_timestamp(sched.next_due_at)}")
    click.echo(f"Due now: {sched.due_now}")
    svc.close()


# ---------------------------------------------------------------------------
# reminder-check (invoked periodically by the systemd user timer)
# ---------------------------------------------------------------------------

@cli.command("reminder-check")
@pass_ctx
def reminder_check(clictx: CliContext):
    """Check whether a backup is due and show a desktop reminder if so.

    This is what packaging/systemd/pdbu-reminder.timer invokes. It is
    safe to run at any time: it only shows a notification, throttled to
    avoid repeats, and never runs a backup by itself.
    """
    cfg = clictx.cfg
    if not cfg.reminders.notifications_enabled:
        return
    svc = clictx.service()
    last = svc.history.last_successful_backup()
    state = scheduler.load_state()
    sched = scheduler.compute_schedule(cfg.reminders, last.end_time if last else None, state=state)

    if not scheduler.should_renotify(sched, state):
        svc.close()
        return

    if not notifications.available():
        if not clictx.quiet:
            echo_warn("notify-send is not available; cannot show a reminder notification.")
        svc.close()
        return

    body = (
        "No backup has been completed yet."
        if sched.last_successful_backup is None
        else f"It has been {_format_ago(sched.last_successful_backup)} since your last successful backup."
    )
    action = notifications.send_reminder_notification(body)
    scheduler.mark_notified()

    if action in ("backup", "open"):
        try:
            subprocess.Popen(["pdbu-gui"], start_new_session=True)
        except OSError as exc:
            echo_err(f"Could not launch pdbu-gui: {exc}")
    elif action == "later":
        scheduler.snooze(cfg.reminders)

    svc.close()


def main() -> None:
    try:
        cli(standalone_mode=True)
    except click.ClickException as exc:
        exc.show()
        sys.exit(exc.exit_code)


if __name__ == "__main__":
    main()
