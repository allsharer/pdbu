"""Desktop notifications for backup reminders and operation results.

Notifications are sent via ``notify-send`` (from ``libnotify-bin``, present
on stock Ubuntu desktops) rather than a custom D-Bus/GApplication
integration, so PDBU does not need to keep any process running for
reminders to work — a systemd user timer simply invokes a short-lived
``pdbu reminder-check`` process periodically (see
``packaging/systemd/pdbu-reminder.timer``).
"""

from __future__ import annotations

from dataclasses import dataclass

from pdbu import procutil

APP_NAME = "PDBU"


class NotificationsUnavailable(Exception):
    pass


@dataclass
class ReminderAction:
    id: str
    label: str


REMINDER_ACTIONS = [
    ReminderAction("backup", "Back Up Now"),
    ReminderAction("later", "Remind Me Later"),
    ReminderAction("open", "Open PDBU"),
    ReminderAction("dismiss", "Dismiss"),
]


def available() -> bool:
    return procutil.available("notify-send")


def notify(title: str, body: str, *, urgency: str = "normal", icon: str = "drive-harddisk") -> None:
    """Fire-and-forget notification (e.g. backup completed/failed)."""
    if not available():
        return
    procutil.run(
        ["notify-send", "--app-name", APP_NAME, f"--urgency={urgency}", "--icon", icon, title, body],
        timeout=10,
    )


def send_reminder_notification(
    body: str, *, timeout_seconds: int = 60
) -> str | None:
    """Show an interactive reminder with action buttons.

    Blocks (up to ``timeout_seconds``) until the user picks an action or
    the notification server dismisses it, returning the action id
    ("backup", "later", "open", "dismiss") or ``None`` if unavailable,
    dismissed, or timed out with no explicit choice.
    """
    if not available():
        return None

    args = ["notify-send", "--app-name", APP_NAME, "--urgency=normal", "--icon", "drive-harddisk"]
    for action in REMINDER_ACTIONS:
        args += ["--action", f"{action.id}={action.label}"]
    args += ["--wait", "PDBU: Backup reminder", body]

    result = procutil.run(args, timeout=timeout_seconds)
    output = result.stdout.strip()
    return output or None
