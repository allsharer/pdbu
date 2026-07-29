from __future__ import annotations

import os

from pdbu import rsync_engine as re_


def test_build_command_basic_flags():
    opts = re_.RsyncOptions()
    cmd = re_.build_rsync_command("/src", "/dst", opts)
    assert cmd[0] == "rsync"
    assert "-a" in cmd
    assert "-H" in cmd
    assert "-A" in cmd
    assert "-X" in cmd
    assert "--numeric-ids" in cmd
    assert "--delete-during" in cmd
    assert cmd[-2] == "/src/"  # trailing slash for mirror semantics
    assert cmd[-1] == "/dst"


def test_build_command_without_delete():
    opts = re_.RsyncOptions(delete=False)
    cmd = re_.build_rsync_command("/src", "/dst", opts)
    assert "--delete-during" not in cmd


def test_exclusions_included_as_separate_flags():
    opts = re_.RsyncOptions(exclusions=[".cache/", "Downloads/"])
    cmd = re_.build_rsync_command("/src", "/dst", opts)
    assert "--exclude=.cache/" in cmd
    assert "--exclude=Downloads/" in cmd


def test_bandwidth_limit_flag():
    opts = re_.RsyncOptions(bandwidth_limit_kbps=500)
    cmd = re_.build_rsync_command("/src", "/dst", opts)
    assert "--bwlimit=500" in cmd


def test_bandwidth_limit_omitted_when_zero():
    opts = re_.RsyncOptions(bandwidth_limit_kbps=0)
    cmd = re_.build_rsync_command("/src", "/dst", opts)
    assert not any(a.startswith("--bwlimit") for a in cmd)


def test_extra_options_appended():
    opts = re_.RsyncOptions(extra_options=["--partial", "--checksum"])
    cmd = re_.build_rsync_command("/src", "/dst", opts)
    assert "--partial" in cmd
    assert "--checksum" in cmd


def test_ssh_push_uses_dash_e_and_remote_spec():
    opts = re_.RsyncOptions()
    ssh = re_.SSHOptions(host="example.com", port=2222, username="alice")
    cmd = re_.build_rsync_command("/src", "/remote/dst", opts, ssh=ssh)
    assert "-e" in cmd
    e_value = cmd[cmd.index("-e") + 1]
    assert "ssh -p 2222" in e_value
    assert cmd[-1] == "alice@example.com:/remote/dst"


def test_ssh_pull_reverses_roles():
    opts = re_.RsyncOptions()
    ssh = re_.SSHOptions(host="example.com", username="alice")
    cmd = re_.build_rsync_command("/remote/src", "/local/dst", opts, ssh=ssh, pull=True)
    assert cmd[-2] == "alice@example.com:/remote/src/"
    assert cmd[-1] == "/local/dst"


def test_remote_spec_escapes_spaces():
    ssh = re_.SSHOptions(host="example.com", username="alice")
    spec = re_.format_remote_spec(ssh, "/home/alice/my backups")
    assert spec == r"alice@example.com:/home/alice/my\ backups"


def test_host_alias_preferred_over_host():
    ssh = re_.SSHOptions(host="1.2.3.4", host_alias="myserver")
    assert ssh.connect_target == "myserver"


def test_strict_host_key_checking_maps_to_yes_or_accept_new():
    strict = re_.build_ssh_dash_e_value(re_.SSHOptions(host="h", strict_host_key_checking=True))
    lenient = re_.build_ssh_dash_e_value(re_.SSHOptions(host="h", strict_host_key_checking=False))
    assert "StrictHostKeyChecking=yes" in strict
    assert "StrictHostKeyChecking=accept-new" in lenient
    assert "StrictHostKeyChecking=no" not in lenient  # never fully disabled


def test_files_from_flag():
    opts = re_.RsyncOptions()
    cmd = re_.build_rsync_command("/src", "/dst", opts, files_from="/tmp/list.txt")
    assert "--files-from=/tmp/list.txt" in cmd


# -- dry-run / itemize parsing ------------------------------------------

ITEMIZE_SAMPLE = """\
sending incremental file list
>f+++++++++ newfile.txt
cd+++++++++ newdir/
>f+++++++++ newdir/inner.txt
>f.st...... changed.txt
.d..t...... unchanged_dir/
*deleting   removed.txt
*deleting   removed_dir/old.txt

Number of files: 10 (reg: 8, dir: 2)
Number of created files: 3
Number of deleted files: 2
Number of regular files transferred: 1
Total file size: 12,345 bytes
Total transferred file size: 999 bytes
Total bytes sent: 456
Total bytes received: 78
"""


def test_parse_dry_run_output_categorizes_changes():
    report = re_.parse_dry_run_output(ITEMIZE_SAMPLE)
    assert "newfile.txt" in report.added
    assert "newdir/" in report.added
    assert "newdir/inner.txt" in report.added
    assert "changed.txt" in report.updated
    assert "unchanged_dir/" not in report.added
    assert "unchanged_dir/" not in report.updated
    assert report.deleted == ["removed.txt", "removed_dir/old.txt"]
    assert report.delete_count == 2


def test_parse_stats_output():
    stats = re_.parse_stats_output(ITEMIZE_SAMPLE)
    assert stats.number_of_files == 10
    assert stats.number_of_created_files == 3
    assert stats.number_of_deleted_files == 2
    assert stats.total_file_size == 12345
    assert stats.total_transferred_file_size == 999


def test_parse_progress_line_percent_and_filename():
    progress = re_.parse_progress_line(
        "     123,456  43%   12.34MB/s    0:00:01 (xfr#12, to-chk=345/6789)"
    )
    assert progress.percent == 43
    assert progress.bytes_transferred == 123456
    assert progress.files_remaining == 345
    assert progress.files_total == 6789

    filename_event = re_.parse_progress_line("Documents/report.pdf")
    assert filename_event.current_file == "Documents/report.pdf"

    stats_line = re_.parse_progress_line("Total file size: 5 bytes")
    assert stats_line is None


# -- real rsync execution (uses the actual rsync binary against tmp dirs) --

def test_run_dry_run_against_real_rsync(home_and_backup):
    source, dest = home_and_backup
    opts = re_.RsyncOptions()
    result, report = re_.run_dry_run(str(source), str(dest), opts)
    assert result.ok
    assert "doc.txt" in report.added
    assert any("nested.txt" in p for p in report.added)


def test_run_live_actually_copies_and_deletes(home_and_backup):
    source, dest = home_and_backup
    opts = re_.RsyncOptions()

    result = re_.run_live(str(source), str(dest), opts)
    assert result.ok
    assert (dest / "doc.txt").read_text() == "hello world"

    os.remove(source / "doc.txt")
    result2 = re_.run_live(str(source), str(dest), opts)
    assert result2.ok
    assert not (dest / "doc.txt").exists()


def test_run_live_progress_callback_invoked(home_and_backup):
    source, dest = home_and_backup
    opts = re_.RsyncOptions()
    events = []
    re_.run_live(str(source), str(dest), opts, on_progress=events.append)
    assert any(e.current_file for e in events)
