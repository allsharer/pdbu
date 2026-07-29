"""Backup reminder scheduling.

Reminders are based on the last *successful* backup, never merely the
last attempted one. This module is pure calculation — no I/O — so it is
trivial to unit test; :mod:`pdbu.notifications` and the systemd timer
integration call into it.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass

from pdbu import paths
from pdbu.config import RemindersConfig


@dataclass
class ReminderState:
    last_notified_at: float | None = None
    snoozed_until: float | None = None


@dataclass
class ScheduleStatus:
    last_successful_backup: float | None
    interval_seconds: float
    next_due_at: float | None
    overdue: bool
    seconds_until_due: float | None
    snoozed_until: float | None = None

    @property
    def due_now(self) -> bool:
        if self.snoozed_until and time.time() < self.snoozed_until:
            return False
        return self.overdue


def interval_seconds(reminders: RemindersConfig) -> float:
    return reminders.interval_days * 86400


def compute_schedule(
    reminders: RemindersConfig,
    last_successful_backup: float | None,
    *,
    now: float | None = None,
    state: ReminderState | None = None,
) -> ScheduleStatus:
    now = now if now is not None else time.time()
    interval = interval_seconds(reminders)

    if last_successful_backup is None:
        # Never backed up: due immediately.
        return ScheduleStatus(
            last_successful_backup=None,
            interval_seconds=interval,
            next_due_at=now,
            overdue=True,
            seconds_until_due=0.0,
            snoozed_until=state.snoozed_until if state else None,
        )

    next_due_at = last_successful_backup + interval
    overdue = now >= next_due_at
    return ScheduleStatus(
        last_successful_backup=last_successful_backup,
        interval_seconds=interval,
        next_due_at=next_due_at,
        overdue=overdue,
        seconds_until_due=None if overdue else next_due_at - now,
        snoozed_until=state.snoozed_until if state else None,
    )


# ---------------------------------------------------------------------------
# Persisted reminder state (snooze / last-notification timestamps)
# ---------------------------------------------------------------------------

def load_state(path=None) -> ReminderState:
    state_path = path or paths.reminder_state_file()
    if not state_path.exists():
        return ReminderState()
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ReminderState()
    return ReminderState(
        last_notified_at=data.get("last_notified_at"),
        snoozed_until=data.get("snoozed_until"),
    )


def save_state(state: ReminderState, path=None) -> None:
    state_path = path or paths.reminder_state_file()
    state_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    state_path.write_text(
        json.dumps({"last_notified_at": state.last_notified_at, "snoozed_until": state.snoozed_until}),
        encoding="utf-8",
    )


def snooze(reminders: RemindersConfig, *, now: float | None = None, path=None) -> ReminderState:
    now = now if now is not None else time.time()
    state = load_state(path)
    state.snoozed_until = now + reminders.snooze_hours * 3600
    save_state(state, path)
    return state


def mark_notified(*, now: float | None = None, path=None) -> ReminderState:
    now = now if now is not None else time.time()
    state = load_state(path)
    state.last_notified_at = now
    save_state(state, path)
    return state


def clear_snooze(path=None) -> ReminderState:
    state = load_state(path)
    state.snoozed_until = None
    save_state(state, path)
    return state


def should_renotify(
    schedule: ScheduleStatus,
    state: ReminderState,
    *,
    renotify_interval_seconds: float = 4 * 3600,
    now: float | None = None,
) -> bool:
    """Avoid excessive repeat notifications while still nagging periodically."""
    if not schedule.due_now:
        return False
    now = now if now is not None else time.time()
    if state.last_notified_at is None:
        return True
    return (now - state.last_notified_at) >= renotify_interval_seconds
