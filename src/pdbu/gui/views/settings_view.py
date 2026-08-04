"""Settings view: full configuration editor."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk

from pdbu import config as config_mod
from pdbu import devices, ssh_backend
from pdbu.gui import dialogs


def _row(grid: Gtk.Grid, row: int, label_text: str, widget: Gtk.Widget) -> None:
    label = Gtk.Label(label=label_text, xalign=0)
    label.add_css_class("dim-label")
    grid.attach(label, 0, row, 1, 1)
    widget.set_hexpand(True)
    grid.attach(widget, 1, row, 1, 1)


def _entry(value: str) -> Gtk.Entry:
    e = Gtk.Entry()
    e.set_text(value or "")
    return e


def _spin(value: float, lower: float, upper: float, step: float = 1) -> Gtk.SpinButton:
    adjustment = Gtk.Adjustment(value=value, lower=lower, upper=upper, step_increment=step)
    return Gtk.SpinButton(adjustment=adjustment, numeric=True)


def _switch(active: bool) -> Gtk.Switch:
    s = Gtk.Switch()
    s.set_active(active)
    s.set_halign(Gtk.Align.START)
    return s


class SettingsView(Gtk.Box):
    def __init__(self, window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.window = window
        self.set_margin_top(24)
        self.set_margin_bottom(24)
        self.set_margin_start(24)
        self.set_margin_end(24)

        title = Gtk.Label(label="Settings")
        title.add_css_class("title-1")
        title.set_xalign(0)
        self.append(title)

        self.notebook = Gtk.Notebook()
        self.notebook.set_vexpand(True)
        self.append(self.notebook)

        save_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.status_label = Gtk.Label(xalign=0)
        save_btn = Gtk.Button(label="Save Settings")
        save_btn.add_css_class("suggested-action")
        save_btn.connect("clicked", self._on_save)
        save_box.append(save_btn)
        save_box.append(self.status_label)
        self.append(save_box)

        self._built = False

    def refresh(self) -> None:
        if self._built:
            return
        self._built = True
        cfg = self.window.cfg
        self.notebook.append_page(self._build_general_tab(cfg), Gtk.Label(label="General"))
        self.notebook.append_page(self._build_drives_tab(cfg), Gtk.Label(label="Drives"))
        self.notebook.append_page(self._build_ssh_tab(cfg), Gtk.Label(label="SSH"))
        self.notebook.append_page(self._build_exclusions_tab(cfg), Gtk.Label(label="Exclusions"))
        self.notebook.append_page(self._build_advanced_tab(cfg), Gtk.Label(label="Advanced"))

    # -- General ------------------------------------------------------

    def _build_general_tab(self, cfg) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_margin_top(16)
        box.set_margin_bottom(16)
        box.set_margin_start(16)
        box.set_margin_end(16)
        grid = Gtk.Grid(row_spacing=10, column_spacing=16)
        box.append(grid)

        source_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.source_entry = _entry(cfg.source.home_directory)
        source_box.append(self.source_entry)
        browse_btn = Gtk.Button(label="Browse…")
        browse_btn.connect("clicked", self._on_browse_source)
        source_box.append(browse_btn)
        _row(grid, 0, "Home directory to back up", source_box)

        self.interval_spin = _spin(cfg.reminders.interval_days, 0.1, 365, 1)
        _row(grid, 1, "Backup reminder interval (days)", self.interval_spin)

        self.snooze_spin = _spin(cfg.reminders.snooze_hours, 1, 720, 1)
        _row(grid, 2, "Snooze duration (hours)", self.snooze_spin)

        self.notifications_switch = _switch(cfg.reminders.notifications_enabled)
        _row(grid, 3, "Enable desktop notifications", self.notifications_switch)

        self.theme_dropdown = Gtk.DropDown.new_from_strings(["system", "light", "dark"])
        theme_index = {"system": 0, "light": 1, "dark": 2}.get(cfg.gui.theme, 0)
        self.theme_dropdown.set_selected(theme_index)
        _row(grid, 4, "Theme", self.theme_dropdown)

        self.retention_spin = _spin(cfg.logging.retention_days, 0, 3650, 1)
        _row(grid, 5, "Log retention (days)", self.retention_spin)

        return box

    def _on_browse_source(self, *_args) -> None:
        dialog = Gtk.FileDialog(title="Choose home directory")

        def on_response(dlg, result):
            try:
                folder = dlg.select_folder_finish(result)
            except GLib.Error:
                return
            if folder is not None:
                self.source_entry.set_text(folder.get_path())

        dialog.select_folder(self.window, None, on_response)

    # -- Drives --------------------------------------------------------

    def _build_drive_grid(self, drive_cfg, key: str) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_top(16)
        box.set_margin_bottom(16)
        box.set_margin_start(16)
        box.set_margin_end(16)
        grid = Gtk.Grid(row_spacing=10, column_spacing=16)
        box.append(grid)

        name_entry = _entry(drive_cfg.name)
        _row(grid, 0, "Label", name_entry)

        luks_entry = _entry(drive_cfg.luks_uuid)
        _row(grid, 1, "LUKS UUID", luks_entry)

        fs_entry = _entry(drive_cfg.filesystem_uuid)
        _row(grid, 2, "Filesystem UUID", fs_entry)

        mount_entry = _entry(drive_cfg.mount_point)
        _row(grid, 3, "Last-known mount point", mount_entry)

        subdir_entry = _entry(drive_cfg.backup_subdir)
        _row(grid, 4, "Backup subdirectory", subdir_entry)

        lock_switch = _switch(drive_cfg.lock_after_backup)
        _row(grid, 5, "Unmount and lock after backup", lock_switch)

        detect_btn = Gtk.Button(label="Detect from connected drives…")
        detect_btn.connect("clicked", lambda *_: self._on_detect_drive(luks_entry, fs_entry, mount_entry))
        box.append(detect_btn)

        setattr(self, f"{key}_name_entry", name_entry)
        setattr(self, f"{key}_luks_entry", luks_entry)
        setattr(self, f"{key}_fs_entry", fs_entry)
        setattr(self, f"{key}_mount_entry", mount_entry)
        setattr(self, f"{key}_subdir_entry", subdir_entry)
        setattr(self, f"{key}_lock_switch", lock_switch)
        return box

    def _build_drives_tab(self, cfg) -> Gtk.Widget:
        notebook = Gtk.Notebook()
        notebook.append_page(self._build_drive_grid(cfg.drive_a, "drive_a"), Gtk.Label(label="Backup Drive A"))
        notebook.append_page(self._build_drive_grid(cfg.drive_b, "drive_b"), Gtk.Label(label="Backup Drive B"))
        return notebook

    def _on_detect_drive(self, luks_entry, fs_entry, mount_entry) -> None:
        partitions = devices.find_luks_partitions()
        dialog = Gtk.Dialog(title="Select a connected LUKS drive", transient_for=self.window, modal=True)
        dialog.set_default_size(420, 300)
        content = dialog.get_content_area()
        content.set_margin_top(12)
        content.set_margin_bottom(12)
        content.set_margin_start(12)
        content.set_margin_end(12)

        if not partitions:
            content.append(Gtk.Label(label="No LUKS-encrypted partitions are currently connected."))
            dialog.add_button("Close", Gtk.ResponseType.CLOSE)
            dialogs.run_dialog_sync(dialog)
            return

        selection_holder: dict = {}
        listbox = Gtk.ListBox()
        listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        for part in partitions:
            row = Gtk.ListBoxRow()
            row.set_child(Gtk.Label(label=f"{part.path}  (UUID: {part.uuid})", xalign=0))
            row.partition = part
            listbox.append(row)
        listbox.connect(
            "row-selected",
            lambda _lb, row: selection_holder.__setitem__("partition", row.partition if row else None),
        )
        content.append(listbox)
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dialog.add_button("Select", Gtk.ResponseType.OK)

        response = dialogs.run_dialog_sync(dialog)
        selected = selection_holder.get("partition")
        if response == Gtk.ResponseType.OK and selected is not None:
            luks_entry.set_text(selected.uuid)
            children = devices.find_crypt_children(selected)
            fs_entry.set_text(children[0].uuid if children else "")
            if children and children[0].mountpoint:
                mount_entry.set_text(children[0].mountpoint)

    # -- SSH --------------------------------------------------------

    def _build_ssh_tab(self, cfg) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_top(16)
        box.set_margin_bottom(16)
        box.set_margin_start(16)
        box.set_margin_end(16)
        grid = Gtk.Grid(row_spacing=10, column_spacing=16)
        box.append(grid)

        self.ssh_enabled_switch = _switch(cfg.ssh.enabled)
        _row(grid, 0, "Enable SSH backup", self.ssh_enabled_switch)

        self.ssh_host_entry = _entry(cfg.ssh.host)
        _row(grid, 1, "Host or IP address", self.ssh_host_entry)

        aliases = ssh_backend.parse_ssh_config_hosts()
        self.ssh_alias_dropdown = Gtk.DropDown.new_from_strings(["(none)"] + aliases)
        if cfg.ssh.host_alias in aliases:
            self.ssh_alias_dropdown.set_selected(aliases.index(cfg.ssh.host_alias) + 1)
        _row(grid, 2, "SSH config host alias", self.ssh_alias_dropdown)

        self.ssh_port_spin = _spin(cfg.ssh.port, 1, 65535, 1)
        _row(grid, 3, "Port", self.ssh_port_spin)

        self.ssh_user_entry = _entry(cfg.ssh.username)
        _row(grid, 4, "Username", self.ssh_user_entry)

        self.ssh_dest_entry = _entry(cfg.ssh.destination)
        _row(grid, 5, "Remote destination path", self.ssh_dest_entry)

        identity_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.ssh_identity_entry = _entry(cfg.ssh.identity_file)
        identity_box.append(self.ssh_identity_entry)
        identity_browse = Gtk.Button(label="Browse…")
        identity_browse.connect("clicked", self._on_browse_identity)
        identity_box.append(identity_browse)
        _row(grid, 6, "Private key file", identity_box)

        self.ssh_password_switch = _switch(cfg.ssh.use_password_auth)
        _row(grid, 7, "Use password authentication", self.ssh_password_switch)

        self.ssh_strict_switch = _switch(cfg.ssh.strict_host_key_checking)
        _row(grid, 8, "Strict host key checking", self.ssh_strict_switch)

        self.ssh_timeout_spin = _spin(cfg.ssh.connect_timeout_seconds, 1, 300, 1)
        _row(grid, 9, "Connection timeout (seconds)", self.ssh_timeout_spin)

        self.ssh_bwlimit_spin = _spin(cfg.ssh.bandwidth_limit_kbps, 0, 1_000_000, 100)
        _row(grid, 10, "Bandwidth limit (KB/s, 0 = unlimited)", self.ssh_bwlimit_spin)

        test_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        test_btn = Gtk.Button(label="Test Connection")
        test_btn.connect("clicked", self._on_test_ssh)
        self.ssh_test_label = Gtk.Label(xalign=0)
        test_box.append(test_btn)
        test_box.append(self.ssh_test_label)
        box.append(test_box)

        return box

    def _on_browse_identity(self, *_args) -> None:
        dialog = Gtk.FileDialog(title="Choose SSH private key")

        def on_response(dlg, result):
            try:
                file = dlg.open_finish(result)
            except GLib.Error:
                return
            if file is not None:
                self.ssh_identity_entry.set_text(file.get_path())

        dialog.open(self.window, None, on_response)

    def _on_test_ssh(self, *_args) -> None:
        self._apply_form_to_config(self.window.cfg)
        try:
            result = self.window.service.test_ssh_connection()
        except Exception as exc:  # noqa: BLE001 - surfaced directly to the user
            self.ssh_test_label.set_label(f"Error: {exc}")
            return
        self.ssh_test_label.set_label(("OK: " if result.success else "Failed: ") + result.message)

    # -- Exclusions ----------------------------------------------------

    def _build_exclusions_tab(self, cfg) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_top(16)
        box.set_margin_bottom(16)
        box.set_margin_start(16)
        box.set_margin_end(16)

        box.append(Gtk.Label(label="Default exclusions", xalign=0))
        self.default_exclusion_switches: dict[str, Gtk.Switch] = {}
        for pattern, enabled in cfg.exclusions.defaults_enabled.items():
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            row.append(Gtk.Label(label=pattern, xalign=0, hexpand=True))
            switch = _switch(enabled)
            row.append(switch)
            box.append(row)
            self.default_exclusion_switches[pattern] = switch

        box.append(Gtk.Separator())
        box.append(Gtk.Label(label="Additional exclusions", xalign=0))

        self.additional_list = Gtk.ListBox()
        for pattern in cfg.exclusions.additional:
            self.additional_list.append(self._exclusion_row(pattern))
        box.append(self.additional_list)

        add_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.new_exclusion_entry = Gtk.Entry(hexpand=True, placeholder_text="e.g. Videos/ or *.iso")
        add_btn = Gtk.Button(label="Add")
        add_btn.connect("clicked", self._on_add_exclusion)
        add_box.append(self.new_exclusion_entry)
        add_box.append(add_btn)
        box.append(add_box)

        return box

    def _exclusion_row(self, pattern: str) -> Gtk.ListBoxRow:
        row = Gtk.ListBoxRow()
        hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        hbox.append(Gtk.Label(label=pattern, xalign=0, hexpand=True))
        remove_btn = Gtk.Button(label="Remove")
        remove_btn.connect("clicked", lambda *_: self.additional_list.remove(row))
        hbox.append(remove_btn)
        row.set_child(hbox)
        row.pattern = pattern
        return row

    def _on_add_exclusion(self, *_args) -> None:
        pattern = self.new_exclusion_entry.get_text().strip()
        if not pattern:
            return
        self.additional_list.append(self._exclusion_row(pattern))
        self.new_exclusion_entry.set_text("")

    # -- Advanced ------------------------------------------------------

    def _build_advanced_tab(self, cfg) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_top(16)
        box.set_margin_bottom(16)
        box.set_margin_start(16)
        box.set_margin_end(16)
        grid = Gtk.Grid(row_spacing=10, column_spacing=16)
        box.append(grid)

        self.delete_switch = _switch(cfg.backup.delete_removed_files)
        _row(grid, 0, "Delete files removed from source (--delete)", self.delete_switch)

        self.dry_run_first_switch = _switch(cfg.backup.dry_run_first)
        _row(grid, 1, "Run a dry run before every real backup", self.dry_run_first_switch)

        self.verify_dest_switch = _switch(cfg.backup.verify_destination)
        _row(grid, 2, "Verify destination availability before backup", self.verify_dest_switch)

        self.preserve_acls_switch = _switch(cfg.backup.preserve_acls)
        _row(grid, 3, "Preserve ACLs", self.preserve_acls_switch)

        self.preserve_xattrs_switch = _switch(cfg.backup.preserve_xattrs)
        _row(grid, 4, "Preserve extended attributes", self.preserve_xattrs_switch)

        self.preserve_hardlinks_switch = _switch(cfg.backup.preserve_hard_links)
        _row(grid, 5, "Preserve hard links", self.preserve_hardlinks_switch)

        self.delete_threshold_spin = _spin(cfg.backup.delete_confirm_threshold, 0, 100000, 1)
        _row(grid, 6, "Confirm before deleting more than N files", self.delete_threshold_spin)

        self.bwlimit_spin = _spin(cfg.backup.bandwidth_limit_kbps, 0, 1_000_000, 100)
        _row(grid, 7, "Local backup bandwidth limit (KB/s, 0 = unlimited)", self.bwlimit_spin)

        self.extra_options_entry = _entry(" ".join(cfg.backup.extra_rsync_options))
        _row(grid, 8, "Additional rsync options (space-separated)", self.extra_options_entry)

        return box

    # -- save ------------------------------------------------------

    def _apply_form_to_config(self, cfg) -> None:
        cfg.source.home_directory = self.source_entry.get_text()
        cfg.reminders.interval_days = self.interval_spin.get_value()
        cfg.reminders.snooze_hours = int(self.snooze_spin.get_value())
        cfg.reminders.notifications_enabled = self.notifications_switch.get_active()
        cfg.gui.theme = ["system", "light", "dark"][self.theme_dropdown.get_selected()]
        cfg.logging.retention_days = int(self.retention_spin.get_value())

        cfg.drive_a.name = self.drive_a_name_entry.get_text()
        cfg.drive_a.luks_uuid = self.drive_a_luks_entry.get_text()
        cfg.drive_a.filesystem_uuid = self.drive_a_fs_entry.get_text()
        cfg.drive_a.mount_point = self.drive_a_mount_entry.get_text()
        cfg.drive_a.backup_subdir = self.drive_a_subdir_entry.get_text()
        cfg.drive_a.lock_after_backup = self.drive_a_lock_switch.get_active()

        cfg.drive_b.name = self.drive_b_name_entry.get_text()
        cfg.drive_b.luks_uuid = self.drive_b_luks_entry.get_text()
        cfg.drive_b.filesystem_uuid = self.drive_b_fs_entry.get_text()
        cfg.drive_b.mount_point = self.drive_b_mount_entry.get_text()
        cfg.drive_b.backup_subdir = self.drive_b_subdir_entry.get_text()
        cfg.drive_b.lock_after_backup = self.drive_b_lock_switch.get_active()

        cfg.ssh.enabled = self.ssh_enabled_switch.get_active()
        cfg.ssh.host = self.ssh_host_entry.get_text()
        alias_idx = self.ssh_alias_dropdown.get_selected()
        model = self.ssh_alias_dropdown.get_model()
        cfg.ssh.host_alias = "" if alias_idx == 0 else model.get_string(alias_idx)
        cfg.ssh.port = int(self.ssh_port_spin.get_value())
        cfg.ssh.username = self.ssh_user_entry.get_text()
        cfg.ssh.destination = self.ssh_dest_entry.get_text()
        cfg.ssh.identity_file = self.ssh_identity_entry.get_text()
        cfg.ssh.use_password_auth = self.ssh_password_switch.get_active()
        cfg.ssh.strict_host_key_checking = self.ssh_strict_switch.get_active()
        cfg.ssh.connect_timeout_seconds = int(self.ssh_timeout_spin.get_value())
        cfg.ssh.bandwidth_limit_kbps = int(self.ssh_bwlimit_spin.get_value())

        for pattern, switch in self.default_exclusion_switches.items():
            cfg.exclusions.defaults_enabled[pattern] = switch.get_active()
        additional = []
        row = self.additional_list.get_row_at_index(0)
        index = 0
        while True:
            row = self.additional_list.get_row_at_index(index)
            if row is None:
                break
            additional.append(row.pattern)
            index += 1
        cfg.exclusions.additional = additional

        cfg.backup.delete_removed_files = self.delete_switch.get_active()
        cfg.backup.dry_run_first = self.dry_run_first_switch.get_active()
        cfg.backup.verify_destination = self.verify_dest_switch.get_active()
        cfg.backup.preserve_acls = self.preserve_acls_switch.get_active()
        cfg.backup.preserve_xattrs = self.preserve_xattrs_switch.get_active()
        cfg.backup.preserve_hard_links = self.preserve_hardlinks_switch.get_active()
        cfg.backup.delete_confirm_threshold = int(self.delete_threshold_spin.get_value())
        cfg.backup.bandwidth_limit_kbps = int(self.bwlimit_spin.get_value())
        cfg.backup.extra_rsync_options = [
            opt for opt in self.extra_options_entry.get_text().split() if opt
        ]

    def _on_save(self, *_args) -> None:
        cfg = self.window.cfg
        self._apply_form_to_config(cfg)
        try:
            config_mod.save(cfg)
        except config_mod.ConfigError as exc:
            self.status_label.set_label(f"Invalid configuration: {exc}")
            return
        self.window.app.reload_config()
        self.status_label.set_label("Settings saved.")
