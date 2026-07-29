"""Background-thread helper so long-running operations never block the UI.

GTK widgets may only be touched from the main thread, so results and
progress events produced on a worker thread are marshalled back via
``GLib.idle_add``.
"""

from __future__ import annotations

import threading
from typing import Callable

import gi

gi.require_version("GLib", "2.0")
from gi.repository import GLib


class BackgroundTask:
    def __init__(self):
        self.cancel_event = threading.Event()
        self._thread: threading.Thread | None = None

    def run(
        self,
        work: Callable[[], object],
        *,
        on_progress: Callable[[object], None] | None = None,
        on_done: Callable[[object, Exception | None], None] | None = None,
    ) -> None:
        def target():
            error = None
            result = None
            try:
                result = work()
            except Exception as exc:  # noqa: BLE001 - surfaced to the UI thread
                error = exc
            if on_done is not None:
                GLib.idle_add(on_done, result, error)

        self._thread = threading.Thread(target=target, daemon=True)
        self._thread.start()

    def cancel(self) -> None:
        self.cancel_event.set()

    @staticmethod
    def marshal(callback: Callable[..., None], *args) -> None:
        GLib.idle_add(callback, *args)
