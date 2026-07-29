"""SSH backend tests.

These use a fake ``ssh`` binary placed first on PATH so no real network
connection is ever attempted, per the "mocked commands during tests"
requirement — nothing here can reach an actual remote host.
"""

from __future__ import annotations

import os
import stat

import pytest

from pdbu import ssh_backend as sb
from pdbu.rsync_engine import SSHOptions

FAKE_SSH_SCRIPT = r"""#!/bin/bash
mode="${FAKE_SSH_MODE:-ok}"
args="$*"
if [[ "$args" == *"PDBU_CONNECTION_OK"* ]]; then
    case "$mode" in
        hostkey)
            echo "Host key verification failed." >&2
            exit 255
            ;;
        authfail)
            echo "Permission denied (publickey,password)." >&2
            exit 255
            ;;
        *)
            echo "PDBU_CONNECTION_OK"
            exit 0
            ;;
    esac
elif [[ "$args" == *"df -Pk"* ]]; then
    # The real remote command pipes through `tail -1`; since this stub
    # doesn't actually execute the remote command string, emit only the
    # data line so remote_free_bytes() sees the same output it would
    # from a real host.
    echo "/dev/sdb1         1000000   200000    800000      21% /backups"
    exit 0
else
    exit 1
fi
"""


@pytest.fixture
def fake_ssh(tmp_path, monkeypatch):
    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir()
    script_path = bin_dir / "ssh"
    script_path.write_text(FAKE_SSH_SCRIPT)
    script_path.chmod(script_path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    return bin_dir


def test_parse_ssh_config_hosts(tmp_path):
    config_text = """
Host myserver
    HostName 192.168.1.5
    User alice

Host *.internal
    User bob

Host backup-nas
    Port 2222
"""
    path = tmp_path / "ssh_config"
    path.write_text(config_text)
    hosts = sb.parse_ssh_config_hosts(str(path))
    assert hosts == ["myserver", "backup-nas"]


def test_parse_ssh_config_hosts_missing_file(tmp_path):
    assert sb.parse_ssh_config_hosts(str(tmp_path / "nope")) == []


def test_connection_success(fake_ssh, monkeypatch):
    monkeypatch.setenv("FAKE_SSH_MODE", "ok")
    ssh = SSHOptions(host="example.com", username="alice")
    result = sb.test_connection(ssh, timeout=5)
    assert result.success


def test_connection_host_key_failure_detected(fake_ssh, monkeypatch):
    monkeypatch.setenv("FAKE_SSH_MODE", "hostkey")
    ssh = SSHOptions(host="example.com", username="alice")
    result = sb.test_connection(ssh, timeout=5)
    assert not result.success
    assert result.host_key_prompt


def test_connection_auth_failure_detected(fake_ssh, monkeypatch):
    monkeypatch.setenv("FAKE_SSH_MODE", "authfail")
    ssh = SSHOptions(host="example.com", username="alice")
    result = sb.test_connection(ssh, timeout=5)
    assert not result.success
    assert not result.host_key_prompt
    assert "auth" in result.message.lower() or "denied" in result.message.lower()


def test_remote_free_bytes_parses_df_output(fake_ssh, monkeypatch):
    monkeypatch.setenv("FAKE_SSH_MODE", "ok")
    ssh = SSHOptions(host="example.com", username="alice")
    free_bytes = sb.remote_free_bytes(ssh, "/backups")
    assert free_bytes == 800_000 * 1024


def test_shquote_handles_single_quotes():
    assert sb._shquote("it's a test") == "'it'\\''s a test'"


@pytest.mark.parametrize(
    "stderr_line,expected_label",
    [
        ("rsync: failed to set permissions on foo: Operation not supported (95)", "file permissions"),
        ("rsync: chgrp foo failed: Operation not permitted (1)", "ownership"),
        ("rsync: rsync_xal_set: lsetxattr(...) failed: Operation not supported", "extended attributes"),
    ],
)
def test_metadata_warning_patterns_detect_known_messages(stderr_line, expected_label):
    matched = [
        label for pattern, label in sb._METADATA_WARNING_PATTERNS if pattern.search(stderr_line)
    ]
    assert any(expected_label in m for m in matched)
