"""PDBU main application window: sidebar navigation over five views."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

from pdbu.gui.views.backup_view import BackupView
from pdbu.gui.views.dashboard_view import DashboardView
from pdbu.gui.views.history_view import HistoryView
from pdbu.gui.views.restore_view import RestoreView
from pdbu.gui.views.settings_view import SettingsView


class MainWindow(Gtk.ApplicationWindow):
    def __init__(self, application):
        super().__init__(application=application, title="PDBU — Personal Directory Backup Utility")
        self.app = application
        self.set_default_size(960, 640)

        header = Gtk.HeaderBar()
        self.set_titlebar(header)

        root = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.set_child(root)

        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.stack.set_hexpand(True)
        self.stack.set_vexpand(True)

        sidebar = Gtk.StackSidebar()
        sidebar.set_stack(self.stack)
        sidebar.set_size_request(180, -1)
        root.append(sidebar)
        root.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))
        root.append(self.stack)

        self.dashboard_view = DashboardView(self)
        self.backup_view = BackupView(self)
        self.restore_view = RestoreView(self)
        self.history_view = HistoryView(self)
        self.settings_view = SettingsView(self)

        self.stack.add_titled(self.dashboard_view, "dashboard", "Dashboard")
        self.stack.add_titled(self.backup_view, "backup", "Back Up Now")
        self.stack.add_titled(self.restore_view, "restore", "Restore")
        self.stack.add_titled(self.history_view, "history", "Backup History")
        self.stack.add_titled(self.settings_view, "settings", "Settings")

        self.stack.connect("notify::visible-child-name", self._on_page_changed)
        self.dashboard_view.refresh()

    def _on_page_changed(self, *_args) -> None:
        name = self.stack.get_visible_child_name()
        view = {
            "dashboard": self.dashboard_view,
            "backup": self.backup_view,
            "restore": self.restore_view,
            "history": self.history_view,
            "settings": self.settings_view,
        }.get(name)
        if view is not None and hasattr(view, "refresh"):
            view.refresh()

    @property
    def service(self):
        return self.app.svc

    @property
    def cfg(self):
        return self.app.cfg
