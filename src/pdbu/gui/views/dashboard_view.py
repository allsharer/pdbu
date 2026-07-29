"""Dashboard view: at-a-glance backup status."""

from __future__ import annotations

import time

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk


def _format_ts(ts: float | None) -> str:
    if ts is None:
        return "Never"
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


def _format_ago(ts: float | None) -> str:
    if ts is None:
        return "—"
    delta = time.time() - ts
    if delta < 60:
        return "just now"
    if delta < 3600:
        return f"{int(delta // 60)} minute(s) ago"
    if delta < 86400:
        return f"{delta / 3600:.1f} hour(s) ago"
    return f"{delta / 86400:.1f} day(s) ago"


class DashboardView(Gtk.Box):
    def __init__(self, window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.window = window
        self.set_margin_top(24)
        self.set_margin_bottom(24)
        self.set_margin_start(24)
        self.set_margin_end(24)

        title = Gtk.Label(label="Dashboard")
        title.add_css_class("title-1")
        title.set_xalign(0)
        self.append(title)

        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        refresh_btn = Gtk.Button(label="Refresh")
        refresh_btn.connect("clicked", lambda *_: self.refresh())
        backup_btn = Gtk.Button(label="Back Up Now")
        backup_btn.add_css_class("suggested-action")
        backup_btn.connect("clicked", lambda *_: window.stack.set_visible_child_name("backup"))
        toolbar.append(refresh_btn)
        toolbar.append(backup_btn)
        self.append(toolbar)

        self.grid = Gtk.Grid(row_spacing=8, column_spacing=24)
        self.grid.set_margin_top(12)
        self.append(self.grid)

        self._value_labels: dict[str, Gtk.Label] = {}
        rows = [
            "last_backup",
            "elapsed",
            "next_due",
            "last_drive",
            "recommended_drive",
            "drive_a_status",
            "drive_b_status",
            "ssh_status",
            "last_result",
            "warnings_errors",
            "schedule",
        ]
        labels = {
            "last_backup": "Last successful backup",
            "elapsed": "Time since last backup",
            "next_due": "Next backup due",
            "last_drive": "Last-used backup drive",
            "recommended_drive": "Recommended next drive",
            "drive_a_status": "Backup Drive A",
            "drive_b_status": "Backup Drive B",
            "ssh_status": "SSH destination",
            "last_result": "Most recent backup result",
            "warnings_errors": "Warnings / errors",
            "schedule": "Backup schedule",
        }
        for i, key in enumerate(rows):
            key_label = Gtk.Label(label=labels[key])
            key_label.set_xalign(0)
            key_label.add_css_class("dim-label")
            value_label = Gtk.Label(label="—")
            value_label.set_xalign(0)
            self.grid.attach(key_label, 0, i, 1, 1)
            self.grid.attach(value_label, 1, i, 1, 1)
            self._value_labels[key] = value_label

    def refresh(self) -> None:
        svc = self.window.service
        if svc is None:
            return
        dash = svc.dashboard_status()

        self._value_labels["last_backup"].set_label(
            f"{_format_ts(dash.last_backup.end_time if dash.last_backup else None)}"
        )
        self._value_labels["elapsed"].set_label(
            _format_ago(dash.last_backup.end_time if dash.last_backup else None)
        )
        self._value_labels["next_due"].set_label(_format_ts(dash.schedule.next_due_at))
        self._value_labels["last_drive"].set_label(dash.last_backup.mode if dash.last_backup else "—")
        self._value_labels["recommended_drive"].set_label(dash.recommended_drive)

        for key in ("drive_a", "drive_b"):
            status = dash.drive_statuses[key]
            if not status.configured:
                text = "Not configured"
            elif not status.connected:
                text = "Not connected"
            elif status.locked:
                text = "Connected — locked"
            elif not status.mounted:
                text = "Connected — unlocked, not mounted"
            else:
                text = f"Mounted at {status.mountpoint}"
            self._value_labels[f"{key}_status"].set_label(text)

        self._value_labels["ssh_status"].set_label(
            "Configured" if dash.ssh_configured else "Not configured"
        )

        if dash.last_backup:
            self._value_labels["last_result"].set_label(dash.last_backup.result)
            warn_count = len(dash.last_backup.warnings)
            err_count = len(dash.last_backup.errors)
            self._value_labels["warnings_errors"].set_label(f"{warn_count} warning(s), {err_count} error(s)")
        else:
            self._value_labels["last_result"].set_label("—")
            self._value_labels["warnings_errors"].set_label("—")

        due_text = "due now" if dash.schedule.due_now else "not due yet"
        self._value_labels["schedule"].set_label(
            f"Every {self.window.cfg.reminders.interval_days:g} day(s) — {due_text}"
        )
