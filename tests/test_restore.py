from __future__ import annotations

import os

import pytest

from pdbu import restore as r


@pytest.fixture
def backup_and_dest(tmp_path):
    backup = tmp_path / "backup"
    backup.mkdir()
    (backup / "a.txt").write_text("from backup")
    (backup / "docs").mkdir()
    (backup / "docs" / "b.txt").write_text("doc b")

    dest = tmp_path / "restore_dest"
    dest.mkdir()
    return backup, dest


def test_full_restore_overwrite(backup_and_dest):
    backup, dest = backup_and_dest
    req = r.RestoreRequest(backup_root=str(backup), destination=str(dest), conflict_mode=r.ConflictMode.OVERWRITE)
    assert r.validate_restore_request(req).ok
    result = r.run_restore(req)
    assert result.ok
    assert (dest / "a.txt").read_text() == "from backup"
    assert (dest / "docs" / "b.txt").read_text() == "doc b"


def test_selected_restore_only_copies_chosen_paths(backup_and_dest):
    backup, dest = backup_and_dest
    req = r.RestoreRequest(backup_root=str(backup), destination=str(dest), selected_paths=["docs/b.txt"])
    result = r.run_restore(req)
    assert result.ok
    assert (dest / "docs" / "b.txt").exists()
    assert not (dest / "a.txt").exists()


@pytest.mark.parametrize("bad_path", ["../../etc/passwd", "/etc/passwd", "..", "a/../../b"])
def test_unsafe_selected_paths_rejected(backup_and_dest, bad_path):
    backup, dest = backup_and_dest
    req = r.RestoreRequest(backup_root=str(backup), destination=str(dest), selected_paths=[bad_path])
    with pytest.raises(r.RestoreError):
        r.run_restore(req)


def test_conflict_skip_existing_preserves_local_edit(backup_and_dest):
    backup, dest = backup_and_dest
    (dest / "a.txt").write_text("local edit")
    req = r.RestoreRequest(backup_root=str(backup), destination=str(dest), conflict_mode=r.ConflictMode.SKIP_EXISTING)
    r.run_restore(req)
    assert (dest / "a.txt").read_text() == "local edit"


def test_conflict_overwrite_replaces_local_edit(backup_and_dest):
    backup, dest = backup_and_dest
    (dest / "a.txt").write_text("local edit")
    req = r.RestoreRequest(backup_root=str(backup), destination=str(dest), conflict_mode=r.ConflictMode.OVERWRITE)
    r.run_restore(req)
    assert (dest / "a.txt").read_text() == "from backup"


def test_conflict_newer_only_skips_newer_local_file(backup_and_dest):
    import time

    backup, dest = backup_and_dest
    (dest / "a.txt").write_text("local edit")
    # ensure the local file's mtime is clearly newer than the backup copy
    future = time.time() + 100
    os.utime(dest / "a.txt", (future, future))
    req = r.RestoreRequest(backup_root=str(backup), destination=str(dest), conflict_mode=r.ConflictMode.NEWER_ONLY)
    r.run_restore(req)
    assert (dest / "a.txt").read_text() == "local edit"


def test_conflict_rename_existing_keeps_both_versions(backup_and_dest):
    backup, dest = backup_and_dest
    (dest / "a.txt").write_text("local edit")
    req = r.RestoreRequest(backup_root=str(backup), destination=str(dest), conflict_mode=r.ConflictMode.RENAME_EXISTING)
    r.run_restore(req)
    assert (dest / "a.txt").read_text() == "from backup"
    backups = [f for f in os.listdir(dest) if f.startswith("a.txt.bak-")]
    assert len(backups) == 1
    assert (dest / backups[0]).read_text() == "local edit"


def test_dangerous_restore_destination_rejected(backup_and_dest):
    backup, _ = backup_and_dest
    req = r.RestoreRequest(backup_root=str(backup), destination="/etc")
    report = r.validate_restore_request(req)
    assert not report.ok


def test_restore_destination_equal_to_backup_root_rejected(backup_and_dest):
    backup, _ = backup_and_dest
    req = r.RestoreRequest(backup_root=str(backup), destination=str(backup))
    report = r.validate_restore_request(req)
    assert not report.ok


def test_preview_restore_is_dry_run_and_does_not_modify(backup_and_dest):
    backup, dest = backup_and_dest
    req = r.RestoreRequest(backup_root=str(backup), destination=str(dest))
    result, report = r.preview_restore(req)
    assert result.ok
    assert "a.txt" in report.added
    assert not (dest / "a.txt").exists()  # dry run must not write anything
