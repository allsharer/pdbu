"""rsync command construction, execution, and output parsing.

This is the single place that knows how to turn PDBU options into an
``rsync`` argument vector, run it, and interpret its output (live
progress, ``--dry-run`` itemized reports, and ``--stats`` summaries).
Both the backup engine and the restore engine build on top of this
module so their command construction and parsing stay consistent.
"""

from __future__ import annotations

import re
import shlex
import subprocess
import threading
from dataclasses import dataclass, field
from typing import Callable, Iterable

from pdbu import procutil


@dataclass
class SSHOptions:
    host: str
    port: int = 22
    username: str = ""
    identity_file: str = ""
    host_alias: str = ""
    strict_host_key_checking: bool = True
    connect_timeout_seconds: int = 10

    @property
    def connect_target(self) -> str:
        return self.host_alias or self.host


@dataclass
class RsyncOptions:
    delete: bool = True
    preserve_acls: bool = True
    preserve_xattrs: bool = True
    preserve_hard_links: bool = True
    numeric_ids: bool = True
    bandwidth_limit_kbps: int = 0
    exclusions: list[str] = field(default_factory=list)
    extra_options: list[str] = field(default_factory=list)
    delete_excluded: bool = False


def build_ssh_command_tokens(ssh: SSHOptions) -> list[str]:
    tokens = ["ssh"]
    if ssh.port and ssh.port != 22:
        tokens += ["-p", str(ssh.port)]
    if ssh.identity_file:
        tokens += ["-i", ssh.identity_file]
    tokens += ["-o", f"ConnectTimeout={ssh.connect_timeout_seconds}"]
    if ssh.strict_host_key_checking:
        tokens += ["-o", "StrictHostKeyChecking=yes"]
    else:
        tokens += ["-o", "StrictHostKeyChecking=accept-new"]
    return tokens


def build_ssh_dash_e_value(ssh: SSHOptions) -> str:
    return shlex.join(build_ssh_command_tokens(ssh))


def format_remote_spec(ssh: SSHOptions, path: str) -> str:
    escaped_path = path.replace(" ", r"\ ")
    target = ssh.connect_target
    userhost = f"{ssh.username}@{target}" if ssh.username else target
    return f"{userhost}:{escaped_path}"


def _with_trailing_slash(path: str) -> str:
    return path if path.endswith("/") else path + "/"


def build_rsync_command(
    source: str,
    destination: str,
    options: RsyncOptions,
    *,
    ssh: SSHOptions | None = None,
    dry_run: bool = False,
    itemize: bool = True,
    stats: bool = True,
    live_progress: bool = False,
    mirror_trailing_slash: bool = True,
    pull: bool = False,
    files_from: str | None = None,
) -> list[str]:
    """Build an rsync argument vector for a backup or restore transfer.

    Normally (``pull=False``) ``source`` is a local path and, when ``ssh``
    is given, ``destination`` is a remote path (push — used for backups).
    With ``pull=True`` the roles reverse: ``source`` is a remote path and
    ``destination`` is local (pull — used to restore from an SSH backup).
    """
    cmd = ["rsync", "-a"]
    if options.preserve_hard_links:
        cmd.append("-H")
    if options.preserve_acls:
        cmd.append("-A")
    if options.preserve_xattrs:
        cmd.append("-X")
    if options.numeric_ids:
        cmd.append("--numeric-ids")
    if options.delete:
        cmd.append("--delete-during")
        if options.delete_excluded:
            cmd.append("--delete-excluded")
    if dry_run:
        cmd.append("--dry-run")
    if itemize:
        cmd.append("-i")
    if stats:
        cmd.append("--stats")
    if live_progress:
        cmd += ["-v", "--progress"]
    if options.bandwidth_limit_kbps > 0:
        cmd.append(f"--bwlimit={options.bandwidth_limit_kbps}")
    for pattern in options.exclusions:
        cmd.append(f"--exclude={pattern}")
    if ssh is not None:
        cmd += ["-e", build_ssh_dash_e_value(ssh)]
    if files_from is not None:
        cmd.append(f"--files-from={files_from}")
        mirror_trailing_slash = True
    cmd.extend(options.extra_options)

    if pull:
        remote_src = format_remote_spec(ssh, source) if ssh else source
        src = _with_trailing_slash(remote_src) if mirror_trailing_slash else remote_src
        cmd.append(src)
        cmd.append(destination)
    else:
        src = _with_trailing_slash(source) if mirror_trailing_slash else source
        cmd.append(src)
        if ssh is not None:
            cmd.append(format_remote_spec(ssh, destination))
        else:
            cmd.append(destination)
    return cmd


# ---------------------------------------------------------------------------
# Dry-run / itemized output parsing
# ---------------------------------------------------------------------------

_ITEMIZE_RE = re.compile(r"^([<>ch.*])([fdLDS])(\S{9})\s(.+)$")


@dataclass
class DryRunReport:
    added: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    other_changes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    stats: "StatsReport | None" = None

    @property
    def delete_count(self) -> int:
        return len(self.deleted)

    @property
    def total_changes(self) -> int:
        return len(self.added) + len(self.updated) + len(self.deleted) + len(self.other_changes)


def parse_dry_run_output(stdout: str, stderr: str = "") -> DryRunReport:
    report = DryRunReport()
    for line in stdout.splitlines():
        if not line:
            continue
        if line.startswith("*deleting"):
            report.deleted.append(line[len("*deleting"):].strip())
            continue
        match = _ITEMIZE_RE.match(line)
        if not match:
            continue
        update_type, file_type, flags, path = match.groups()
        if file_type == "d" and flags == "+++++++++":
            report.added.append(path)  # new directory
            continue
        if file_type == "d":
            continue  # directory attribute-only churn is noise for a summary
        if flags == "+++++++++":
            report.added.append(path)
        elif update_type in (">", "c", "<"):
            report.updated.append(path)
        else:
            report.other_changes.append(path)

    report.stats = parse_stats_output(stdout)
    for line in stderr.splitlines():
        line = line.strip()
        if line:
            report.warnings.append(line)
    return report


# ---------------------------------------------------------------------------
# --stats summary parsing
# ---------------------------------------------------------------------------

@dataclass
class StatsReport:
    number_of_files: int | None = None
    number_of_created_files: int | None = None
    number_of_deleted_files: int | None = None
    number_of_regular_files_transferred: int | None = None
    total_file_size: int | None = None
    total_transferred_file_size: int | None = None
    total_bytes_sent: int | None = None
    total_bytes_received: int | None = None


_STAT_PATTERNS: dict[str, re.Pattern] = {
    "number_of_files": re.compile(r"^Number of files:\s*([\d,]+)"),
    "number_of_created_files": re.compile(r"^Number of created files:\s*([\d,]+)"),
    "number_of_deleted_files": re.compile(r"^Number of deleted files:\s*([\d,]+)"),
    "number_of_regular_files_transferred": re.compile(
        r"^Number of regular files transferred:\s*([\d,]+)"
    ),
    "total_file_size": re.compile(r"^Total file size:\s*([\d,]+)"),
    "total_transferred_file_size": re.compile(r"^Total transferred file size:\s*([\d,]+)"),
    "total_bytes_sent": re.compile(r"^Total bytes sent:\s*([\d,]+)"),
    "total_bytes_received": re.compile(r"^Total bytes received:\s*([\d,]+)"),
}


def parse_stats_output(stdout: str) -> StatsReport:
    report = StatsReport()
    for line in stdout.splitlines():
        line = line.strip()
        for field_name, pattern in _STAT_PATTERNS.items():
            match = pattern.match(line)
            if match:
                setattr(report, field_name, int(match.group(1).replace(",", "")))
    return report


# ---------------------------------------------------------------------------
# Live progress parsing (for GUI / verbose CLI streaming)
# ---------------------------------------------------------------------------

_PROGRESS_RE = re.compile(
    r"^\s*(?P<bytes>[\d,]+)\s+(?P<percent>\d+)%\s+(?P<speed>[\d.]+\w+/s)\s+"
    r"(?P<eta>[\d:]+)(?:\s+\(xfr#(?P<xfr>\d+),\s+to-chk=(?P<tochk>\d+)/(?P<total>\d+)\))?"
)


@dataclass
class ProgressEvent:
    current_file: str | None = None
    bytes_transferred: int | None = None
    percent: int | None = None
    speed: str | None = None
    eta: str | None = None
    files_transferred: int | None = None
    files_remaining: int | None = None
    files_total: int | None = None


def parse_progress_line(line: str) -> ProgressEvent | None:
    line = line.rstrip("\n")
    if not line.strip():
        return None
    match = _PROGRESS_RE.match(line)
    if match:
        groups = match.groupdict()
        return ProgressEvent(
            bytes_transferred=int(groups["bytes"].replace(",", "")),
            percent=int(groups["percent"]),
            speed=groups["speed"],
            eta=groups["eta"],
            files_transferred=int(groups["xfr"]) if groups["xfr"] else None,
            files_remaining=int(groups["tochk"]) if groups["tochk"] else None,
            files_total=int(groups["total"]) if groups["total"] else None,
        )
    if line[0] in "<>ch.*":
        # An itemized change line (e.g. ">f+++++++++ path/to/file") is how
        # rsync announces the file it's about to transfer when -i and -v
        # are combined (our live-run command uses both); extract the path
        # so the UI can show a current-file name, not just percentages.
        itemize_match = _ITEMIZE_RE.match(line)
        if itemize_match and itemize_match.group(2) == "f":
            return ProgressEvent(current_file=itemize_match.group(4))
        return None
    if line.startswith((" ", "\t")):
        return None
    if line.endswith((
        "sending incremental file list",
        "receiving incremental file list",
    )):
        return None
    if _STATS_LINE_RE.match(line) or line.startswith(("sent ", "total size")):
        return None
    return ProgressEvent(current_file=line)


_STATS_LINE_RE = re.compile(
    r"^(Number of [\w /]+|Total file size|Total transferred file size|Literal data|"
    r"Matched data|File list size|File list generation time|File list transfer "
    r"time|Total bytes sent|Total bytes received):"
)


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------

@dataclass
class RunResult:
    args: list[str]
    returncode: int
    stdout: str
    stderr: str
    cancelled: bool = False

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.cancelled


def run_dry_run(
    source: str,
    destination: str,
    options: RsyncOptions,
    *,
    ssh: SSHOptions | None = None,
    timeout: float | None = None,
    pull: bool = False,
    files_from: str | None = None,
) -> tuple[RunResult, DryRunReport]:
    cmd = build_rsync_command(
        source,
        destination,
        options,
        ssh=ssh,
        dry_run=True,
        itemize=True,
        stats=True,
        pull=pull,
        files_from=files_from,
    )
    procutil.require("rsync")
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, shell=False)
    result = RunResult(args=cmd, returncode=proc.returncode, stdout=proc.stdout, stderr=proc.stderr)
    report = parse_dry_run_output(proc.stdout, proc.stderr)
    return result, report


def run_live(
    source: str,
    destination: str,
    options: RsyncOptions,
    *,
    ssh: SSHOptions | None = None,
    dry_run: bool = False,
    on_progress: Callable[[ProgressEvent], None] | None = None,
    cancel_event: threading.Event | None = None,
    pull: bool = False,
    files_from: str | None = None,
) -> RunResult:
    """Run rsync, streaming stdout line-by-line to ``on_progress``.

    ``cancel_event`` lets a caller (GUI "Cancel" button, CLI SIGINT
    handler) terminate an in-progress transfer cleanly.
    """
    cmd = build_rsync_command(
        source,
        destination,
        options,
        ssh=ssh,
        dry_run=dry_run,
        itemize=True,
        stats=True,
        live_progress=True,
        pull=pull,
        files_from=files_from,
    )
    procutil.require("rsync")

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        shell=False,
    )

    stdout_lines: list[str] = []
    stderr_lines: list[str] = []
    cancelled = False

    def _drain_stderr():
        assert process.stderr is not None
        for line in process.stderr:
            stderr_lines.append(line.rstrip("\n"))

    stderr_thread = threading.Thread(target=_drain_stderr, daemon=True)
    stderr_thread.start()

    assert process.stdout is not None
    for line in process.stdout:
        stdout_lines.append(line.rstrip("\n"))
        if on_progress is not None:
            event = parse_progress_line(line)
            if event is not None:
                on_progress(event)
        if cancel_event is not None and cancel_event.is_set():
            cancelled = True
            process.terminate()
            break

    process.wait(timeout=30 if cancelled else None)
    stderr_thread.join(timeout=5)

    return RunResult(
        args=cmd,
        returncode=process.returncode if process.returncode is not None else -1,
        stdout="\n".join(stdout_lines),
        stderr="\n".join(stderr_lines),
        cancelled=cancelled,
    )


def iter_exclusion_lines(patterns: Iterable[str]) -> list[str]:
    return [f"--exclude={p}" for p in patterns]
