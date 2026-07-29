from __future__ import annotations

import time

from pdbu import history


def test_save_and_get(tmp_path):
    db = tmp_path / "hist.sqlite3"
    with history.HistoryStore(db) as store:
        rec = history.OperationRecord(
            operation_id=history.new_operation_id(),
            operation_type="backup",
            mode="drive_a",
            source="/home/user",
            destination="/media/user/PDBU-A",
            destination_identity="FS-UUID-A",
            files_transferred=10,
            bytes_transferred=1024,
            files_deleted=0,
            result="success",
            exit_code=0,
            end_time=time.time(),
        )
        store.save(rec)
        got = store.get(rec.operation_id)
        assert got is not None
        assert got.destination_identity == "FS-UUID-A"
        assert got.files_transferred == 10


def test_db_file_permissions(tmp_path):
    db = tmp_path / "hist.sqlite3"
    with history.HistoryStore(db):
        pass
    assert oct(db.stat().st_mode)[-3:] == "600"


def test_last_successful_backup_filters_correctly(tmp_path):
    db = tmp_path / "hist.sqlite3"
    with history.HistoryStore(db) as store:
        store.save(history.OperationRecord(
            operation_id="1", operation_type="backup", mode="drive_a",
            result="failed", end_time=time.time() - 100,
        ))
        store.save(history.OperationRecord(
            operation_id="2", operation_type="backup", mode="drive_b",
            result="success", end_time=time.time() - 50,
        ))
        store.save(history.OperationRecord(
            operation_id="3", operation_type="backup", mode="drive_a",
            result="success", dry_run=True, end_time=time.time() - 10,
        ))

        last = store.last_successful_backup()
        assert last.operation_id == "2"  # dry runs and failures excluded

        last_a = store.last_successful_backup(mode="drive_a")
        assert last_a is None  # only drive_b succeeded for real


def test_list_ordering_and_limit(tmp_path):
    db = tmp_path / "hist.sqlite3"
    with history.HistoryStore(db) as store:
        for i in range(5):
            store.save(history.OperationRecord(
                operation_id=f"op{i}", operation_type="backup", mode="drive_a",
                result="success", start_time=time.time() + i,
            ))
        results = store.list(limit=3)
        assert len(results) == 3
        assert results[0].operation_id == "op4"  # newest first


def test_purge_older_than(tmp_path):
    db = tmp_path / "hist.sqlite3"
    with history.HistoryStore(db) as store:
        store.save(history.OperationRecord(
            operation_id="old", operation_type="backup", mode="drive_a",
            result="success", start_time=time.time() - 200 * 86400,
        ))
        store.save(history.OperationRecord(
            operation_id="new", operation_type="backup", mode="drive_a",
            result="success", start_time=time.time(),
        ))
        removed = store.purge_older_than(90)
        assert removed == 1
        assert store.get("old") is None
        assert store.get("new") is not None


def test_cross_thread_access_does_not_raise(tmp_path):
    """GUI runs backups on a worker thread; HistoryStore must tolerate that."""
    import threading

    db = tmp_path / "hist.sqlite3"
    store = history.HistoryStore(db)
    errors = []

    def worker():
        try:
            store.save(history.OperationRecord(
                operation_id="threaded", operation_type="backup", mode="drive_a",
                result="success", end_time=time.time(),
            ))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    t = threading.Thread(target=worker)
    t.start()
    t.join()
    store.close()
    assert not errors
