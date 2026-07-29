"""GTK4 application entry point (``pdbu-gui``)."""

from __future__ import annotations

import sys

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, Gtk

from pdbu import config as config_mod
from pdbu import logging_setup, paths
from pdbu import service as service_mod


class PdbuApplication(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="org.pdbu.PDBU", flags=Gio.ApplicationFlags.FLAGS_NONE)
        self.cfg: config_mod.Config = config_mod.default_config()
        self.svc: service_mod.PdbuService | None = None
        self.window = None

    def reload_config(self) -> None:
        self.cfg = config_mod.load()
        if self.svc is not None:
            self.svc.close()
        self.svc = service_mod.PdbuService(self.cfg)

    def do_startup(self) -> None:
        Gtk.Application.do_startup(self)
        paths.ensure_dirs()
        logging_setup.setup_app_logging()
        config_mod.ensure_default_config()
        self.reload_config()

    def do_activate(self) -> None:
        from pdbu.gui.window import MainWindow

        if self.window is None:
            self.window = MainWindow(application=self)
        self.window.present()


def main() -> int:
    app = PdbuApplication()
    return app.run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
