"""Back Up Now view."""

from __future__ import annotations

import threading

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk

from pdbu import safety, service as service_mod
from pdbu.gui import dialogs
from pdbu.gui.workers import BackgroundTask


def _format_bytes(n: int | None) -> str:
    if n is None:
        return "—"
    value = float(n)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} TiB"


class BackupView(Gtk.Box):
    def __init__(self, window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.window = window
        self.task = BackgroundTask()
        self.set_margin_top(24)
        self.set_margin_bottom(24)
        self.set_margin_start(24)
        self.set_margin_end(24)

        title = Gtk.Label(label="Back Up Now")
        title.add_css_class("title-1")
        title.set_xalign(0)
        self.append(title)

        dest_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self.radio_a = Gtk.CheckButton(label="Backup Drive A")
        self.radio_b = Gtk.CheckButton(label="Backup Drive B")
        self.radio_b.set_group(self.radio_a)
        self.radio_ssh = Gtk.CheckButton(label="SSH destination")
        self.radio_ssh.set_group(self.radio_a)
        self.radio_a.set_active(True)
        for r in (self.radio_a, self.radio_b, self.radio_ssh):
            r.connect("toggled", lambda *_: self._update_paths())
            dest_box.append(r)
        self.append(dest_box)

        info_grid = Gtk.Grid(row_spacing=4, column_spacing=16)
        info_grid.attach(Gtk.Label(label="Source:", xalign=0), 0, 0, 1, 1)
        self.source_label = Gtk.Label(xalign=0)
        info_grid.attach(self.source_label, 1, 0, 1, 1)
        info_grid.attach(Gtk.Label(label="Destination:", xalign=0), 0, 1, 1, 1)
        self.dest_label = Gtk.Label(xalign=0)
        info_grid.attach(self.dest_label, 1, 1, 1, 1)
        self.append(info_grid)

        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.dry_run_btn = Gtk.Button(label="Dry Run")
        self.dry_run_btn.connect("clicked", self._on_dry_run)
        self.start_btn = Gtk.Button(label="Start Backup")
        self.start_btn.add_css_class("suggested-action")
        self.start_btn.connect("clicked", self._on_start)
        self.cancel_btn = Gtk.Button(label="Cancel")
        self.cancel_btn.set_sensitive(False)
        self.cancel_btn.connect("clicked", self._on_cancel)
        btn_box.append(self.dry_run_btn)
        btn_box.append(self.start_btn)
        btn_box.append(self.cancel_btn)
        self.append(btn_box)

        self.progress_bar = Gtk.ProgressBar()
        self.progress_bar.set_show_text(True)
        self.append(self.progress_bar)

        self.current_file_label = Gtk.Label(xalign=0)
        self.current_file_label.set_ellipsize(3)  # PANGO_ELLIPSIZE_END
        self.append(self.current_file_label)

        stats_grid = Gtk.Grid(row_spacing=4, column_spacing=16)
        stats_grid.attach(Gtk.Label(label="Transferred:", xalign=0), 0, 0, 1, 1)
        self.transferred_label = Gtk.Label(xalign=0)
        stats_grid.attach(self.transferred_label, 1, 0, 1, 1)
        stats_grid.attach(Gtk.Label(label="Speed:", xalign=0), 0, 1, 1, 1)
        self.speed_label = Gtk.Label(xalign=0)
        stats_grid.attach(self.speed_label, 1, 1, 1, 1)
        self.append(stats_grid)

        self.result_label = Gtk.Label(xalign=0)
        self.result_label.set_wrap(True)
        self.append(self.result_label)

    def refresh(self) -> None:
        self._update_paths()

    def _selected_drive_key(self) -> str | None:
        if self.radio_a.get_active():
            return "drive_a"
        if self.radio_b.get_active():
            return "drive_b"
        return None

    def _update_paths(self) -> None:
        svc = self.window.service
        if svc is None:
            return
        self.source_label.set_label(self.window.cfg.source.home_directory)
        if self.radio_ssh.get_active():
            cfg = self.window.cfg.ssh
            self.dest_label.set_label(
                f"{cfg.username}@{cfg.host_alias or cfg.host}:{cfg.destination}" if cfg.enabled else "SSH not configured"
            )
            return
        drive_key = self._selected_drive_key()
        status = svc.drive_statuses().get(drive_key)
        if status is None:
            self.dest_label.set_label("—")
            return
        if not status.connected:
            self.dest_label.set_label(f"{status.label}: not connected")
        elif status.locked:
            self.dest_label.set_label(f"{status.label}: locked")
        elif status.mounted:
            self.dest_label.set_label(status.mountpoint or "")
        else:
            self.dest_label.set_label(f"{status.label}: connected, not mounted")

    def _resolve_destination(self):
        svc = self.window.service
        if self.radio_ssh.get_active():
            return svc.prepare_ssh_destination()

        drive_key = self._selected_drive_key()
        drive_cfg = getattr(svc.cfg, drive_key)
        status = svc.drive_statuses()[drive_key]
        if not status.connected:
            raise service_mod.ServiceError(f"{drive_cfg.name} is not connected")
        if status.locked:
            passphrase = dialogs.prompt_passphrase(
                self.window, f"Unlock {drive_cfg.name}", f"Enter the passphrase for {drive_cfg.name}"
            )
            if passphrase is None:
                raise service_mod.ServiceError("Unlock cancelled")
            svc.unlock_drive(drive_key, passphrase)
        status = svc.drive_statuses()[drive_key]
        if not status.mounted:
            svc.mount_drive(drive_key)
        return svc.prepare_local_destination(drive_key)

    def _on_dry_run(self, *_args) -> None:
        svc = self.window.service
        try:
            dest = self._resolve_destination()
            result, report = svc.dry_run_backup(dest)
        except (service_mod.ServiceError, safety.SafetyError) as exc:
            dialogs.show_message(self.window, "Dry run failed", str(exc), is_error=True)
            return
        self.result_label.set_label(
            f"Dry run: {len(report.added)} to add, {len(report.updated)} to update, "
            f"{len(report.deleted)} to delete."
        )

    def _on_start(self, *_args) -> None:
        svc = self.window.service
        try:
            dest = self._resolve_destination()
        except (service_mod.ServiceError, safety.SafetyError) as exc:
            dialogs.show_message(self.window, "Cannot start backup", str(exc), is_error=True)
            return

        self.start_btn.set_sensitive(False)
        self.dry_run_btn.set_sensitive(False)
        self.cancel_btn.set_sensitive(True)
        self.progress_bar.set_fraction(0.0)
        self.result_label.set_label("Backup in progress…")
        self.task = BackgroundTask()

        def on_progress(event):
            BackgroundTask.marshal(self._apply_progress, event)

        def confirm_delete(n: int) -> bool:
            done = threading.Event()
            holder = {}

            def ask():
                holder["ok"] = dialogs.confirm(
                    self.window,
                    "Confirm file deletion",
                    f"This backup will delete {n} file(s) from the destination to mirror "
                    "the source. Continue?",
                    ok_label="Delete and continue",
                    destructive=True,
                )
                done.set()
                return False

            GLib.idle_add(ask)
            done.wait()
            return holder.get("ok", False)

        def work():
            return svc.run_backup(
                dest,
                confirm_delete=confirm_delete,
                on_progress=on_progress,
                cancel_event=self.task.cancel_event,
            )

        def on_done(record, error):
            self._on_backup_finished(dest, record, error)
            return False

        self.task.run(work, on_done=on_done)

    def _apply_progress(self, event) -> bool:
        if event.current_file:
            self.current_file_label.set_label(event.current_file)
        if event.percent is not None:
            self.progress_bar.set_fraction(min(event.percent / 100.0, 1.0))
            self.progress_bar.set_text(f"{event.percent}%")
        if event.bytes_transferred is not None:
            self.transferred_label.set_label(_format_bytes(event.bytes_transferred))
        if event.speed:
            self.speed_label.set_label(event.speed)
        return False

    def _on_cancel(self, *_args) -> None:
        self.task.cancel()
        self.result_label.set_label("Cancelling…")

    def _on_backup_finished(self, dest, record, error) -> bool:
        self.start_btn.set_sensitive(True)
        self.dry_run_btn.set_sensitive(True)
        self.cancel_btn.set_sensitive(False)

        svc = self.window.service
        if dest.kind in ("drive_a", "drive_b"):
            drive_cfg = getattr(svc.cfg, dest.kind)
            if drive_cfg.lock_after_backup:
                try:
                    svc.unmount_drive(dest.kind)
                except service_mod.ServiceError:
                    pass

        if error is not None:
            if isinstance(error, service_mod.ConfirmationRequired):
                self.result_label.set_label("Backup cancelled: confirmation was not given.")
            else:
                self.result_label.set_label(f"Backup failed: {error}")
            return False

        self.progress_bar.set_fraction(1.0)
        self.result_label.set_label(
            f"Result: {record.result}. {record.files_transferred or 0} file(s) transferred, "
            f"{_format_bytes(record.bytes_transferred)}, {record.files_deleted or 0} deleted."
        )
        return False
