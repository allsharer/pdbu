"""Small modal dialog helpers.

GTK4 removed the old synchronous ``gtk_dialog_run``; the documented
replacement for "block the caller until the user responds" is a nested
``GLib.MainLoop``, which is what :func:`run_dialog_sync` does. This keeps
call sites in the views simple (``if confirm(...): ...``) without forcing
every caller to restructure into callback chains.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GLib, Gtk


def run_dialog_sync(dialog: Gtk.Dialog) -> int:
    loop = GLib.MainLoop()
    state = {"id": Gtk.ResponseType.CANCEL}

    def on_response(_dialog, response_id):
        state["id"] = response_id
        loop.quit()

    dialog.connect("response", on_response)
    dialog.present()
    loop.run()
    dialog.destroy()
    return state["id"]


def confirm(parent: Gtk.Window, heading: str, body: str, *, ok_label: str = "Confirm", destructive: bool = False) -> bool:
    dialog = Gtk.Dialog(title=heading, transient_for=parent, modal=True)
    dialog.set_default_size(420, -1)
    content = dialog.get_content_area()
    content.set_margin_top(16)
    content.set_margin_bottom(16)
    content.set_margin_start(16)
    content.set_margin_end(16)
    content.set_spacing(8)

    heading_label = Gtk.Label(label=heading)
    heading_label.add_css_class("title-3")
    heading_label.set_xalign(0)
    content.append(heading_label)

    body_label = Gtk.Label(label=body)
    body_label.set_wrap(True)
    body_label.set_xalign(0)
    content.append(body_label)

    dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
    ok_button = dialog.add_button(ok_label, Gtk.ResponseType.OK)
    if destructive:
        ok_button.add_css_class("destructive-action")

    return run_dialog_sync(dialog) == Gtk.ResponseType.OK


def prompt_passphrase(parent: Gtk.Window, title: str, message: str) -> str | None:
    dialog = Gtk.Dialog(title=title, transient_for=parent, modal=True)
    dialog.set_default_size(380, -1)
    content = dialog.get_content_area()
    content.set_margin_top(16)
    content.set_margin_bottom(16)
    content.set_margin_start(16)
    content.set_margin_end(16)
    content.set_spacing(8)

    label = Gtk.Label(label=message)
    label.set_wrap(True)
    label.set_xalign(0)
    content.append(label)

    entry = Gtk.PasswordEntry()
    entry.set_show_peek_icon(True)
    entry.set_activates_default(True)
    content.append(entry)

    dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
    ok_button = dialog.add_button("Unlock", Gtk.ResponseType.OK)
    dialog.set_default_widget(ok_button)

    response = run_dialog_sync(dialog)
    if response == Gtk.ResponseType.OK:
        return entry.get_text()
    return None


def show_message(parent: Gtk.Window, heading: str, body: str, *, is_error: bool = False) -> None:
    dialog = Gtk.Dialog(title=heading, transient_for=parent, modal=True)
    content = dialog.get_content_area()
    content.set_margin_top(16)
    content.set_margin_bottom(16)
    content.set_margin_start(16)
    content.set_margin_end(16)
    content.set_spacing(8)

    heading_label = Gtk.Label(label=heading)
    heading_label.add_css_class("title-3")
    if is_error:
        heading_label.add_css_class("error")
    heading_label.set_xalign(0)
    content.append(heading_label)

    body_label = Gtk.Label(label=body)
    body_label.set_wrap(True)
    body_label.set_xalign(0)
    content.append(body_label)

    dialog.add_button("OK", Gtk.ResponseType.OK)
    run_dialog_sync(dialog)
