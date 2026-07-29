"""CLI integration tests: exit codes and end-to-end backup/restore flows.

Drive detection is monkeypatched to a fake connected/mounted drive so
these tests never touch real block devices, and the XDG env is
redirected into tmp_path so nothing touches the real ~/.config/pdbu.
"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from pdbu import cli as cli_mod
from pdbu import config, devices


@pytest.fixture
def cli_env(tmp_path, xdg_env, monkeypatch):
    source = tmp_path / "home" / "user"
    source.mkdir(parents=True)
    (source / "doc.txt").write_text("hello")
    drive_mount = tmp_path / "media" / "PDBU-A"
    drive_mount.mkdir(parents=True)

    cfg = config.default_config()
    cfg.source.home_directory = str(source)
    cfg.drive_a.filesystem_uuid = "FAKE-FS-UUID-A"
    cfg.drive_a.mount_point = str(drive_mount)
    config.save(cfg)

    fake_status_a = devices.DriveStatus(
        label="Backup Drive A", configured=True, connected=True, is_luks=False,
        locked=False, luks_device_path=None, mapped_device_path="/dev/fake1",
        filesystem_uuid="FAKE-FS-UUID-A", mountpoint=str(drive_mount), mounted=True,
        identity_verified=True,
    )
    not_connected = devices.DriveStatus(
        label="Backup Drive B", configured=False, connected=False, is_luks=False,
        locked=None, luks_device_path=None, mapped_device_path=None,
        filesystem_uuid=None, mountpoint=None, mounted=False, identity_verified=False,
    )

    def fake_detect(drive_cfg, devs=None):
        if drive_cfg.name == "Backup Drive A":
            return fake_status_a
        return not_connected

    monkeypatch.setattr(devices, "detect_drive_status", fake_detect)

    return {"source": source, "drive_mount": drive_mount}


@pytest.fixture
def runner():
    return CliRunner()


def test_status_exit_code_ok(cli_env, runner):
    result = runner.invoke(cli_mod.cli, ["status"])
    assert result.exit_code == cli_mod.EXIT_OK


def test_backup_and_verify_and_history(cli_env, runner):
    result = runner.invoke(cli_mod.cli, ["-q", "backup", "--drive-a"])
    assert result.exit_code == cli_mod.EXIT_OK, result.output
    assert (cli_env["drive_mount"] / "doc.txt").read_text() == "hello"

    verify_result = runner.invoke(cli_mod.cli, ["verify", "--drive-a", "--json"])
    assert verify_result.exit_code == cli_mod.EXIT_OK
    payload = json.loads(verify_result.output)
    assert payload["identical"] is True

    history_result = runner.invoke(cli_mod.cli, ["history", "--json"])
    records = json.loads(history_result.output)
    assert records[0]["result"] == "success"
    assert records[0]["mode"] == "drive_a"


def test_backup_writes_operation_log(cli_env, runner):
    runner.invoke(cli_mod.cli, ["-q", "backup", "--drive-a"])
    logs_result = runner.invoke(cli_mod.cli, ["logs"])
    op_id = logs_result.output.split()[0]
    log_detail = runner.invoke(cli_mod.cli, ["logs", op_id])
    assert "Backup started" in log_detail.output
    assert "Result: success" in log_detail.output


def test_restore_full_and_selected(cli_env, runner):
    runner.invoke(cli_mod.cli, ["-q", "backup", "--drive-a"])

    alt_dest = cli_env["source"].parent / "restore_dest"
    alt_dest.mkdir()
    result = runner.invoke(
        cli_mod.cli, ["-y", "restore", "--source", "drive-a", "--destination", str(alt_dest)]
    )
    assert result.exit_code == cli_mod.EXIT_OK, result.output
    assert (alt_dest / "doc.txt").read_text() == "hello"


def test_restore_to_dangerous_destination_exits_with_safety_code(cli_env, runner):
    result = runner.invoke(
        cli_mod.cli, ["-y", "restore", "--source", "drive-a", "--destination", "/etc"]
    )
    assert result.exit_code == cli_mod.EXIT_SAFETY


def test_backup_to_unconfigured_drive_fails_cleanly(cli_env, runner):
    result = runner.invoke(cli_mod.cli, ["backup", "--drive-b"])
    assert result.exit_code != cli_mod.EXIT_OK


def test_drives_json_shape(cli_env, runner):
    result = runner.invoke(cli_mod.cli, ["drives", "--json"])
    assert result.exit_code == cli_mod.EXIT_OK
    payload = json.loads(result.output)
    assert payload["drive_a"]["connected"] is True
    assert payload["recommended_next"] in ("drive_a", "drive_b")


def test_schedule_set_and_show(cli_env, runner):
    set_result = runner.invoke(cli_mod.cli, ["schedule", "--interval", "14d"])
    assert set_result.exit_code == cli_mod.EXIT_OK
    show_result = runner.invoke(cli_mod.cli, ["schedule"])
    assert "14" in show_result.output


def test_config_show_and_json(cli_env, runner):
    result = runner.invoke(cli_mod.cli, ["config", "--json"])
    assert result.exit_code == cli_mod.EXIT_OK
    payload = json.loads(result.output)
    assert "source" in payload and "ssh" in payload


def test_dry_run_backup_does_not_write(cli_env, runner):
    (cli_env["drive_mount"] / "sentinel_should_not_appear.txt").unlink(missing_ok=True)
    result = runner.invoke(cli_mod.cli, ["backup", "--drive-a", "--dry-run"])
    assert result.exit_code == cli_mod.EXIT_OK
    assert not (cli_env["drive_mount"] / "doc.txt").exists()


def test_delete_confirmation_required_without_yes(cli_env, runner, monkeypatch):
    runner.invoke(cli_mod.cli, ["-q", "backup", "--drive-a"])
    (cli_env["source"] / "doc.txt").unlink()

    cfg = config.load()
    cfg.backup.delete_confirm_threshold = 0
    config.save(cfg)

    # non-interactive (no --yes, no tty) => confirm_delete returns False => confirmation required
    result = runner.invoke(cli_mod.cli, ["backup", "--drive-a"], input="")
    assert result.exit_code == cli_mod.EXIT_CONFIRMATION_REQUIRED


def test_delete_confirmed_with_yes_flag(cli_env, runner):
    runner.invoke(cli_mod.cli, ["-q", "backup", "--drive-a"])
    (cli_env["source"] / "doc.txt").unlink()

    cfg = config.load()
    cfg.backup.delete_confirm_threshold = 0
    config.save(cfg)

    result = runner.invoke(cli_mod.cli, ["-y", "backup", "--drive-a"])
    assert result.exit_code == cli_mod.EXIT_OK
    assert not (cli_env["drive_mount"] / "doc.txt").exists()
