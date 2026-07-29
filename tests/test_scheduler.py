from __future__ import annotations

import time

import pytest

from pdbu import scheduler
from pdbu.config import RemindersConfig


def test_never_backed_up_is_due_now():
    r = RemindersConfig(interval_days=7)
    sched = scheduler.compute_schedule(r, None)
    assert sched.overdue
    assert sched.due_now
    assert sched.seconds_until_due == 0.0


def test_recent_backup_not_due():
    r = RemindersConfig(interval_days=7)
    now = time.time()
    sched = scheduler.compute_schedule(r, now - 3 * 86400, now=now)
    assert not sched.overdue
    assert not sched.due_now
    assert sched.seconds_until_due == pytest.approx(4 * 86400, abs=5)


def test_old_backup_is_overdue():
    r = RemindersConfig(interval_days=7)
    now = time.time()
    sched = scheduler.compute_schedule(r, now - 8 * 86400, now=now)
    assert sched.overdue
    assert sched.due_now


def test_snooze_suppresses_due_now_but_not_overdue():
    r = RemindersConfig(interval_days=7)
    now = time.time()
    state = scheduler.ReminderState(snoozed_until=now + 3600)
    sched = scheduler.compute_schedule(r, now - 8 * 86400, now=now, state=state)
    assert sched.overdue
    assert not sched.due_now


def test_state_persistence_round_trip(tmp_path):
    path = tmp_path / "reminder-state.json"
    state = scheduler.ReminderState(last_notified_at=123.0, snoozed_until=456.0)
    scheduler.save_state(state, path)
    loaded = scheduler.load_state(path)
    assert loaded.last_notified_at == 123.0
    assert loaded.snoozed_until == 456.0


def test_load_state_missing_file_returns_empty(tmp_path):
    state = scheduler.load_state(tmp_path / "nope.json")
    assert state.last_notified_at is None
    assert state.snoozed_until is None


def test_snooze_sets_future_timestamp(tmp_path):
    path = tmp_path / "state.json"
    r = RemindersConfig(snooze_hours=24)
    now = time.time()
    scheduler.snooze(r, now=now, path=path)
    state = scheduler.load_state(path)
    assert abs(state.snoozed_until - (now + 24 * 3600)) < 1


def test_should_renotify_throttles_repeats():
    r = RemindersConfig(interval_days=7)
    now = time.time()
    sched = scheduler.compute_schedule(r, None, now=now)  # due now

    never_notified = scheduler.ReminderState()
    assert scheduler.should_renotify(sched, never_notified, now=now)

    just_notified = scheduler.ReminderState(last_notified_at=now - 60)
    assert not scheduler.should_renotify(
        sched, just_notified, renotify_interval_seconds=4 * 3600, now=now
    )

    notified_long_ago = scheduler.ReminderState(last_notified_at=now - 5 * 3600)
    assert scheduler.should_renotify(
        sched, notified_long_ago, renotify_interval_seconds=4 * 3600, now=now
    )


def test_should_renotify_false_when_not_due():
    r = RemindersConfig(interval_days=7)
    now = time.time()
    sched = scheduler.compute_schedule(r, now - 1 * 86400, now=now)
    assert not sched.due_now
    assert not scheduler.should_renotify(sched, scheduler.ReminderState(), now=now)
