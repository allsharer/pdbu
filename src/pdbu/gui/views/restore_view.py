"""Restore view: browse a backup, select content, and restore it."""

from __future__ import annotations

import os
import threading

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk

from pdbu import restore as restore_mod
from pdbu import safety, service as service_mod
from pdbu.gui import dialogs
from pdbu.gui.workers import BackgroundTask

_CONFLICT_CHOICES = [
    ("Overwrite existing files", restore_mod.ConflictMode.OVERWRITE),
    ("Skip existing files", restore_mod.ConflictMode.SKIP_EXISTING),
    ("Restore only newer files", restore_mod.ConflictMode.NEWER_ONLY),
    ("Ask before overwriting", restore_mod.ConflictMode.ASK),
    ("Rename restored conflicting files", restore_mod.ConflictMode.RENAME_EXISTING),
]

_MAX_SEARCH_RESULTS = 200


class RestoreView(Gtk.Box):
    def __init__(self, window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.window = window
        self.task = BackgroundTask()
        self.backup_root: str | None = None
        self.selected_paths: dict[str, bool] = {}
        self.set_margin_top(24)
        self.set_margin_bottom(24)
        self.set_margin_start(24)
        self.set_margin_end(24)

        title = Gtk.Label(label="Restore")
        title.add_css_class("title-1")
        title.set_xalign(0)
        self.append(title)

        source_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.radio_a = Gtk.CheckButton(label="Backup Drive A")
        self.radio_b = Gtk.CheckButton(label="Backup Drive B")
        self.radio_b.set_group(self.radio_a)
        self.radio_ssh = Gtk.CheckButton(label="SSH destination")
        self.radio_ssh.set_group(self.radio_a)
        self.radio_a.set_active(True)
        for r in (self.radio_a, self.radio_b, self.radio_ssh):
            source_box.append(r)
        self.load_btn = Gtk.Button(label="Load Backup")
        self.load_btn.connect("clicked", self._on_load)
        source_box.append(self.load_btn)
        self.append(source_box)

        search_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.search_entry = Gtk.SearchEntry(placeholder_text="Search for files…")
        self.search_entry.connect("activate", self._on_search)
        search_btn = Gtk.Button(label="Search")
        search_btn.connect("clicked", self._on_search)
        search_box.append(self.search_entry)
        search_box.append(search_btn)
        self.append(search_box)

        tree_scroller = Gtk.ScrolledWindow()
        tree_scroller.set_vexpand(True)
        tree_scroller.set_min_content_height(220)
        self.store = Gtk.TreeStore(bool, str, str)  # selected, display name, relative path ("" for dirs = placeholder)
        self.tree_view = Gtk.TreeView(model=self.store)
        self.tree_view.connect("row-expanded", self._on_row_expanded)

        toggle_renderer = Gtk.CellRendererToggle()
        toggle_renderer.connect("toggled", self._on_toggle)
        toggle_col = Gtk.TreeViewColumn("Select", toggle_renderer, active=0)
        self.tree_view.append_column(toggle_col)

        name_renderer = Gtk.CellRendererText()
        name_col = Gtk.TreeViewColumn("Name", name_renderer, text=1)
        name_col.set_expand(True)
        self.tree_view.append_column(name_col)

        tree_scroller.set_child(self.tree_view)
        self.append(tree_scroller)

        self.selection_label = Gtk.Label(xalign=0)
        self.selection_label.set_label("No files selected (full restore).")
        self.append(self.selection_label)

        dest_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        dest_box.append(Gtk.Label(label="Restore to:"))
        self.dest_entry = Gtk.Entry(hexpand=True)
        dest_box.append(self.dest_entry)
        browse_btn = Gtk.Button(label="Browse…")
        browse_btn.connect("clicked", self._on_browse_destination)
        dest_box.append(browse_btn)
        self.append(dest_box)

        options_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        options_box.append(Gtk.Label(label="On conflict:"))
        self.conflict_dropdown = Gtk.DropDown.new_from_strings([c[0] for c in _CONFLICT_CHOICES])
        options_box.append(self.conflict_dropdown)
        self.mirror_delete_check = Gtk.CheckButton(label="Also delete files absent from backup")
        options_box.append(self.mirror_delete_check)
        self.append(options_box)

        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.preview_btn = Gtk.Button(label="Preview Restore")
        self.preview_btn.connect("clicked", self._on_preview)
        self.start_btn = Gtk.Button(label="Start Restore")
        self.start_btn.add_css_class("suggested-action")
        self.start_btn.connect("clicked", self._on_start)
        self.cancel_btn = Gtk.Button(label="Cancel")
        self.cancel_btn.set_sensitive(False)
        self.cancel_btn.connect("clicked", self._on_cancel)
        btn_box.append(self.preview_btn)
        btn_box.append(self.start_btn)
        btn_box.append(self.cancel_btn)
        self.append(btn_box)

        self.progress_bar = Gtk.ProgressBar()
        self.progress_bar.set_show_text(True)
        self.append(self.progress_bar)

        self.result_label = Gtk.Label(xalign=0)
        self.result_label.set_wrap(True)
        self.append(self.result_label)

    def refresh(self) -> None:
        if not self.dest_entry.get_text():
            self.dest_entry.set_text(self.window.cfg.source.home_directory)

    # -- loading the backup root --------------------------------------

    def _on_load(self, *_args) -> None:
        svc = self.window.service
        try:
            if self.radio_ssh.get_active():
                dest = svc.prepare_ssh_destination()
                self.backup_root = None
                dialogs.show_message(
                    self.window,
                    "SSH backups",
                    "Browsing is only available for local backup drives. Use "
                    "'--path' restores from the CLI for SSH backups, or type "
                    "relative paths manually once support is added.",
                )
                return
            drive_key = "drive_a" if self.radio_a.get_active() else "drive_b"
            drive_cfg = getattr(svc.cfg, drive_key)
            status = svc.drive_statuses()[drive_key]
            if not status.connected:
                raise service_mod.ServiceError(f"{drive_cfg.name} is not connected")
            if status.locked:
                passphrase = dialogs.prompt_passphrase(
                    self.window, f"Unlock {drive_cfg.name}", f"Enter the passphrase for {drive_cfg.name}"
                )
                if passphrase is None:
                    return
                svc.unlock_drive(drive_key, passphrase)
            status = svc.drive_statuses()[drive_key]
            if not status.mounted:
                svc.mount_drive(drive_key)
            dest = svc.prepare_local_destination(drive_key)
            self.backup_root = dest.local_path
        except (service_mod.ServiceError, safety.SafetyError) as exc:
            dialogs.show_message(self.window, "Could not load backup", str(exc), is_error=True)
            return

        self.store.clear()
        self.selected_paths.clear()
        self._update_selection_label()
        if self.backup_root:
            self._populate_children(None, self.backup_root, "")

    def _populate_children(self, parent_iter, abs_dir: str, rel_prefix: str) -> None:
        try:
            entries = sorted(os.scandir(abs_dir), key=lambda e: (not e.is_dir(), e.name.lower()))
        except OSError as exc:
            dialogs.show_message(self.window, "Could not read directory", str(exc), is_error=True)
            return
        for entry in entries:
            rel_path = f"{rel_prefix}{entry.name}"
            row_iter = self.store.append(parent_iter, [False, entry.name, rel_path])
            if entry.is_dir(follow_symlinks=False):
                self.store.append(row_iter, [False, "", ""])  # lazy-load placeholder

    def _on_row_expanded(self, tree_view, tree_iter, path) -> None:
        first_child = self.store.iter_children(tree_iter)
        if first_child is not None and self.store.get_value(first_child, 2) == "" and self.store.get_value(first_child, 1) == "":
            self.store.remove(first_child)
            rel_path = self.store.get_value(tree_iter, 2)
            abs_dir = os.path.join(self.backup_root, rel_path)
            self._populate_children(tree_iter, abs_dir, rel_path + "/")

    def _on_toggle(self, _renderer, path_str) -> None:
        tree_iter = self.store.get_iter(path_str)
        new_value = not self.store.get_value(tree_iter, 0)
        self.store.set_value(tree_iter, 0, new_value)
        rel_path = self.store.get_value(tree_iter, 2)
        if rel_path:
            if new_value:
                self.selected_paths[rel_path] = True
            else:
                self.selected_paths.pop(rel_path, None)
        self._update_selection_label()

    def _update_selection_label(self) -> None:
        n = len(self.selected_paths)
        if n == 0:
            self.selection_label.set_label("No files selected (full restore).")
        else:
            self.selection_label.set_label(f"{n} item(s) selected for restore.")

    # -- search ----------------------------------------------------------

    def _on_search(self, *_args) -> None:
        if not self.backup_root:
            dialogs.show_message(self.window, "Load a backup first", "Click 'Load Backup' before searching.")
            return
        query = self.search_entry.get_text().strip().lower()
        if not query:
            return
        matches = []
        for dirpath, dirnames, filenames in os.walk(self.backup_root):
            rel_dir = os.path.relpath(dirpath, self.backup_root)
            for name in filenames:
                if query in name.lower():
                    rel = name if rel_dir == "." else f"{rel_dir}/{name}"
                    matches.append(rel)
                    if len(matches) >= _MAX_SEARCH_RESULTS:
                        break
            if len(matches) >= _MAX_SEARCH_RESULTS:
                break

        for rel in matches:
            self.selected_paths[rel] = True
        self._update_selection_label()
        dialogs.show_message(
            self.window,
            "Search results",
            f"Found {len(matches)} matching file(s) (showing up to {_MAX_SEARCH_RESULTS}); "
            "all matches have been added to the restore selection.",
        )

    # -- destination -------------------------------------------------

    def _on_browse_destination(self, *_args) -> None:
        dialog = Gtk.FileDialog(title="Choose restore destination")

        def on_response(dlg, result):
            try:
                folder = dlg.select_folder_finish(result)
            except GLib.Error:
                return
            if folder is not None:
                self.dest_entry.set_text(folder.get_path())

        dialog.select_folder(self.window, None, on_response)

    # -- preview / run -------------------------------------------------

    def _build_request(self) -> restore_mod.RestoreRequest:
        conflict_index = self.conflict_dropdown.get_selected()
        conflict_mode = _CONFLICT_CHOICES[conflict_index][1]
        return restore_mod.RestoreRequest(
            backup_root=self.backup_root,
            destination=self.dest_entry.get_text() or self.window.cfg.source.home_directory,
            selected_paths=list(self.selected_paths.keys()),
            conflict_mode=(
                restore_mod.ConflictMode.OVERWRITE
                if conflict_mode == restore_mod.ConflictMode.ASK
                else conflict_mode
            ),
            mirror_delete=self.mirror_delete_check.get_active(),
        )

    def _on_preview(self, *_args) -> None:
        if not self.backup_root:
            dialogs.show_message(self.window, "Load a backup first", "Click 'Load Backup' before previewing.")
            return
        try:
            request = self._build_request()
            restore_mod.validate_restore_request(request).raise_if_errors()
            result, report = self.window.service.dry_run_restore(request)
        except (restore_mod.RestoreError, safety.SafetyError) as exc:
            dialogs.show_message(self.window, "Preview failed", str(exc), is_error=True)
            return
        self.result_label.set_label(
            f"Preview: {len(report.added)} to add, {len(report.updated)} to update, "
            f"{len(report.deleted)} to delete (only shown if 'also delete' is enabled)."
        )

    def _on_start(self, *_args) -> None:
        if not self.backup_root:
            dialogs.show_message(self.window, "Load a backup first", "Click 'Load Backup' before restoring.")
            return
        try:
            request = self._build_request()
            restore_mod.validate_restore_request(request).raise_if_errors()
        except safety.SafetyError as exc:
            dialogs.show_message(self.window, "Cannot restore", str(exc), is_error=True)
            return

        confirmed = dialogs.confirm(
            self.window,
            "Confirm restore",
            f"Restore to {request.destination}? This may overwrite existing files "
            "depending on the selected conflict mode.",
            ok_label="Restore",
            destructive=True,
        )
        if not confirmed:
            return

        self.start_btn.set_sensitive(False)
        self.preview_btn.set_sensitive(False)
        self.cancel_btn.set_sensitive(True)
        self.progress_bar.set_fraction(0.0)
        self.result_label.set_label("Restore in progress…")
        self.task = BackgroundTask()

        def on_progress(event):
            BackgroundTask.marshal(self._apply_progress, event)

        def work():
            return self.window.service.run_restore(
                request, on_progress=on_progress, cancel_event=self.task.cancel_event
            )

        def on_done(record, error):
            self._on_restore_finished(record, error)
            return False

        self.task.run(work, on_done=on_done)

    def _apply_progress(self, event) -> bool:
        if event.percent is not None:
            self.progress_bar.set_fraction(min(event.percent / 100.0, 1.0))
            self.progress_bar.set_text(f"{event.percent}%")
        return False

    def _on_cancel(self, *_args) -> None:
        self.task.cancel()
        self.result_label.set_label("Cancelling…")

    def _on_restore_finished(self, record, error) -> bool:
        self.start_btn.set_sensitive(True)
        self.preview_btn.set_sensitive(True)
        self.cancel_btn.set_sensitive(False)
        if error is not None:
            self.result_label.set_label(f"Restore failed: {error}")
            return False
        self.progress_bar.set_fraction(1.0)
        self.result_label.set_label(f"Result: {record.result}. {record.files_transferred or 0} file(s) restored.")
        return False
