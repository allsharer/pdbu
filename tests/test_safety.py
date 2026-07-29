from __future__ import annotations

import os

import pytest

from pdbu import safety


def test_valid_backup_paths_pass(home_and_backup):
    source, dest = home_and_backup
    report = safety.validate_backup_paths(source, dest)
    assert report.ok, report.errors


def test_destination_equal_to_source_rejected(home_and_backup):
    source, _ = home_and_backup
    report = safety.validate_backup_paths(source, source)
    assert not report.ok
    assert any("same as the source" in e for e in report.errors)


def test_destination_inside_source_rejected(home_and_backup):
    source, _ = home_and_backup
    nested = source / "nested_backup"
    nested.mkdir()
    report = safety.validate_backup_paths(source, nested)
    assert not report.ok
    assert any("inside source" in e for e in report.errors)


def test_source_inside_destination_rejected(home_and_backup):
    source, dest = home_and_backup
    nested_source = dest / "home_copy"
    nested_source.mkdir()
    report = safety.validate_backup_paths(nested_source, dest)
    assert not report.ok
    assert any("inside destination" in e for e in report.errors)


@pytest.mark.parametrize("dangerous", ["/", "/home", "/etc", "/root", "/proc"])
def test_dangerous_destinations_rejected(home_and_backup, dangerous):
    source, _ = home_and_backup
    report = safety.validate_backup_paths(source, dangerous)
    assert not report.ok


def test_dangerous_source_rejected(tmp_path):
    dest = tmp_path / "dest"
    dest.mkdir()
    report = safety.check_source("/etc")
    assert not report.ok


def test_missing_source_rejected(tmp_path):
    report = safety.check_source(tmp_path / "does-not-exist")
    assert not report.ok


def test_destination_must_exist_unless_allowed(home_and_backup):
    source, dest = home_and_backup
    missing_dest = dest / "does-not-exist"
    report = safety.check_destination(missing_dest, source=source, require_exists=True)
    assert not report.ok

    report_ok = safety.check_destination(
        missing_dest, source=source, require_exists=True, allow_create=True
    )
    assert report_ok.ok


def test_check_free_space_detects_insufficient(home_and_backup, monkeypatch):
    source, dest = home_and_backup
    huge = 10**18  # absurdly large requirement
    report = safety.check_free_space(dest, huge)
    assert not report.ok


def test_check_free_space_ok_for_small_requirement(home_and_backup):
    source, dest = home_and_backup
    report = safety.check_free_space(dest, 10)
    assert report.ok


def test_delete_confirmation_threshold():
    assert safety.check_delete_confirmation_required(51, 50) is True
    assert safety.check_delete_confirmation_required(50, 50) is False
    assert safety.check_delete_confirmation_required(0, 0) is False


def test_operation_lock_lifecycle(xdg_env):
    assert safety.read_stale_lock() is None
    safety.acquire_operation_lock("op1", "backup", "/src", "/dst")
    # our own pid is alive, so this is *not* considered stale
    assert safety.read_stale_lock() is None
    safety.release_operation_lock()
    assert safety.read_stale_lock() is None


def test_stale_lock_detected_for_dead_pid(xdg_env):
    import json

    from pdbu import paths

    safety.acquire_operation_lock("op1", "backup", "/src", "/dst")
    lock_path = paths.operation_lock_file()
    data = json.loads(lock_path.read_text())
    data["pid"] = 999999999  # not a real process
    lock_path.write_text(json.dumps(data))

    stale = safety.read_stale_lock()
    assert stale is not None
    assert stale.operation_id == "op1"


def test_is_subpath():
    from pathlib import Path

    assert safety.is_subpath(Path("/a/b/c"), Path("/a/b"))
    assert safety.is_subpath(Path("/a/b"), Path("/a/b"))
    assert not safety.is_subpath(Path("/a/c"), Path("/a/b"))
