"""Backup History view."""

from __future__ import annotations

import time

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

from pdbu.gui import dialogs

_COLUMNS = [
    "Date/Time",
    "Type",
    "Destination",
    "Duration",
    "Files",
    "Data",
    "Deleted",
    "Result",
    "Warnings",
    "Errors",
]


def _format_bytes(n: int | None) -> str:
    if n is None:
        return "—"
    value = float(n)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} TiB"


def _format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    seconds = int(seconds)
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


class HistoryView(Gtk.Box):
    def __init__(self, window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.window = window
        self._records = []
        self.set_margin_top(24)
        self.set_margin_bottom(24)
        self.set_margin_start(24)
        self.set_margin_end(24)

        title = Gtk.Label(label="Backup History")
        title.add_css_class("title-1")
        title.set_xalign(0)
        self.append(title)

        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        refresh_btn = Gtk.Button(label="Refresh")
        refresh_btn.connect("clicked", lambda *_: self.refresh())
        view_log_btn = Gtk.Button(label="View Log")
        view_log_btn.connect("clicked", self._on_view_log)
        toolbar.append(refresh_btn)
        toolbar.append(view_log_btn)
        self.append(toolbar)

        self.store = Gtk.ListStore(*([str] * len(_COLUMNS)))
        self.tree_view = Gtk.TreeView(model=self.store)
        for i, col_name in enumerate(_COLUMNS):
            renderer = Gtk.CellRendererText()
            column = Gtk.TreeViewColumn(col_name, renderer, text=i)
            column.set_resizable(True)
            self.tree_view.append_column(column)

        scroller = Gtk.ScrolledWindow()
        scroller.set_vexpand(True)
        scroller.set_child(self.tree_view)
        self.append(scroller)

    def refresh(self) -> None:
        svc = self.window.service
        if svc is None:
            return
        self._records = svc.history.list(limit=200)
        self.store.clear()
        for r in self._records:
            self.store.append([
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r.start_time)),
                r.operation_type,
                f"{r.mode}: {r.destination}",
                _format_duration(r.duration_seconds),
                str(r.files_transferred or 0),
                _format_bytes(r.bytes_transferred),
                str(r.files_deleted or 0),
                r.result,
                str(len(r.warnings)),
                str(len(r.errors)),
            ])

    def _on_view_log(self, *_args) -> None:
        selection = self.tree_view.get_selection()
        model, tree_iter = selection.get_selected()
        if tree_iter is None:
            dialogs.show_message(self.window, "No row selected", "Select an operation first.")
            return
        path = model.get_path(tree_iter)
        index = path.get_indices()[0]
        record = self._records[index]
        if not record.log_path:
            dialogs.show_message(self.window, "No log available", "This operation has no log file.")
            return
        try:
            with open(record.log_path, encoding="utf-8", errors="replace") as fh:
                content = fh.read()
        except OSError as exc:
            dialogs.show_message(self.window, "Could not read log", str(exc), is_error=True)
            return

        dialog = Gtk.Dialog(title=f"Log: {record.operation_id}", transient_for=self.window, modal=True)
        dialog.set_default_size(700, 500)
        content_area = dialog.get_content_area()
        scroller = Gtk.ScrolledWindow()
        scroller.set_vexpand(True)
        scroller.set_hexpand(True)
        text_view = Gtk.TextView()
        text_view.set_editable(False)
        text_view.set_monospace(True)
        text_view.get_buffer().set_text(content)
        scroller.set_child(text_view)
        content_area.append(scroller)
        dialog.add_button("Close", Gtk.ResponseType.CLOSE)
        dialogs.run_dialog_sync(dialog)
