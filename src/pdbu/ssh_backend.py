"""SSH connectivity: config parsing, connection testing, and remote checks.

All actual data transfer for SSH backups/restores goes through
:mod:`pdbu.rsync_engine` (rsync's own ``-e ssh`` support). This module
covers everything else PDBU needs to know about an SSH destination:
available host aliases, whether the connection actually works, how much
free space is available remotely, and whether the remote filesystem can
preserve Linux ownership/permissions/ACLs/xattrs.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from pdbu import procutil
from pdbu.rsync_engine import RsyncOptions, SSHOptions, build_ssh_command_tokens


class SSHError(Exception):
    pass


# ---------------------------------------------------------------------------
# ~/.ssh/config parsing
# ---------------------------------------------------------------------------

def parse_ssh_config_hosts(config_path: str | None = None) -> list[str]:
    """Return concrete (non-wildcard) Host aliases from ~/.ssh/config."""
    path = Path(config_path or os.path.expanduser("~/.ssh/config"))
    if not path.exists():
        return []
    hosts: list[str] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = re.match(r"(?i)^Host\s+(.+)$", stripped)
        if not match:
            continue
        for token in match.group(1).split():
            if "*" not in token and "?" not in token and token not in hosts:
                hosts.append(token)
    return hosts


# ---------------------------------------------------------------------------
# Connection testing
# ---------------------------------------------------------------------------

@dataclass
class ConnectionTestResult:
    success: bool
    message: str
    host_key_prompt: bool = False


def test_connection(
    ssh: SSHOptions,
    *,
    use_password_auth: bool = False,
    password: str | None = None,
    timeout: float | None = None,
) -> ConnectionTestResult:
    """Attempt a minimal SSH round trip (``echo``) to validate connectivity."""
    tokens = build_ssh_command_tokens(ssh)
    target = f"{ssh.username}@{ssh.connect_target}" if ssh.username else ssh.connect_target
    args = [*tokens, "-o", "BatchMode=" + ("no" if use_password_auth else "yes"), target, "echo", "PDBU_CONNECTION_OK"]

    env = None
    stdin_input = None
    if use_password_auth:
        if not procutil.available("sshpass"):
            return ConnectionTestResult(
                success=False,
                message=(
                    "Password authentication requires the 'sshpass' package, which is "
                    "not installed. Install it or configure SSH key authentication instead."
                ),
            )
        if not password:
            return ConnectionTestResult(success=False, message="No password supplied")
        args = ["sshpass", "-e", *args]
        env = {**os.environ, "SSHPASS": password}

    result = procutil.run(args, timeout=timeout or ssh.connect_timeout_seconds + 10, env=env, input=stdin_input)
    if result.ok and "PDBU_CONNECTION_OK" in result.stdout:
        return ConnectionTestResult(success=True, message="Connection successful")

    combined = (result.stderr + result.stdout).lower()
    if "host key verification failed" in combined or "authenticity of host" in combined:
        return ConnectionTestResult(
            success=False,
            message=(
                "Host key could not be verified. Connect once with `ssh` interactively "
                "(or use the 'Fetch host key' option) to confirm and trust the host's "
                "fingerprint before enabling strict checking here."
            ),
            host_key_prompt=True,
        )
    if "permission denied" in combined:
        return ConnectionTestResult(success=False, message="Authentication failed (permission denied)")
    if "connection timed out" in combined or "connection refused" in combined:
        return ConnectionTestResult(success=False, message="Could not reach host (timed out or refused)")
    return ConnectionTestResult(success=False, message=result.stderr.strip() or "Connection failed")


def fetch_host_key_fingerprint(host: str, port: int = 22, timeout: float = 10) -> str:
    procutil.require("ssh-keyscan")
    result = procutil.run(["ssh-keyscan", "-p", str(port), host], timeout=timeout)
    if not result.ok or not result.stdout.strip():
        raise SSHError(f"Could not fetch host key for {host}: {result.stderr.strip()}")
    return result.stdout.strip()


# ---------------------------------------------------------------------------
# Remote free space
# ---------------------------------------------------------------------------

def remote_free_bytes(ssh: SSHOptions, remote_path: str, *, timeout: float | None = None) -> int:
    """Free space at (or at the nearest existing ancestor of) ``remote_path``."""
    tokens = build_ssh_command_tokens(ssh)
    target = f"{ssh.username}@{ssh.connect_target}" if ssh.username else ssh.connect_target
    remote_cmd = (
        f"mkdir -p -- {_shquote(remote_path)} && df -Pk -- {_shquote(remote_path)} | tail -1"
    )
    args = [*tokens, target, remote_cmd]
    result = procutil.run(args, timeout=timeout or ssh.connect_timeout_seconds + 20)
    if not result.ok:
        raise SSHError(f"Could not check remote free space: {result.stderr.strip() or result.stdout.strip()}")
    fields = result.stdout.split()
    if len(fields) < 4:
        raise SSHError(f"Unexpected 'df' output: {result.stdout!r}")
    try:
        available_kb = int(fields[3])
    except ValueError as exc:
        raise SSHError(f"Could not parse available space from: {result.stdout!r}") from exc
    return available_kb * 1024


def _shquote(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"


# ---------------------------------------------------------------------------
# Remote metadata-preservation probe
# ---------------------------------------------------------------------------

_METADATA_WARNING_PATTERNS = [
    (re.compile(r"failed to set permissions", re.I), "file permissions"),
    (re.compile(r"ch(?:own|grp).*failed|failed to set uid/gid|ownership", re.I), "ownership (uid/gid)"),
    (re.compile(r"set_acl|sys_acl_.*failed|ACLs are not supported", re.I), "ACLs"),
    (re.compile(r"xattr.*failed|Extended attributes are not supported|rsync_xal", re.I), "extended attributes"),
    (re.compile(r"failed to set times", re.I), "timestamps"),
]


def probe_remote_metadata_support(
    ssh: SSHOptions, remote_test_dir: str, *, timeout: float | None = None
) -> list[str]:
    """Best-effort check for whether the remote fs preserves Linux metadata.

    Transfers one throwaway file with full metadata preservation flags and
    inspects rsync's stderr for known "unsupported" warnings. This cannot
    catch every case (some filesystems silently drop attributes), so a
    clean result is reported as "no warnings observed," not a guarantee.
    """
    import tempfile

    from pdbu import rsync_engine

    warnings: list[str] = []
    with tempfile.TemporaryDirectory(prefix="pdbu-probe-") as tmp:
        probe_file = Path(tmp) / "pdbu-metadata-probe.txt"
        probe_file.write_text("pdbu metadata probe\n")
        os.chmod(probe_file, 0o640)

        options = RsyncOptions(delete=False, preserve_acls=True, preserve_xattrs=True)
        cmd = rsync_engine.build_rsync_command(
            str(tmp) + "/",
            remote_test_dir,
            options,
            ssh=ssh,
            dry_run=False,
            mirror_trailing_slash=False,
        )
        result = procutil.run(cmd, timeout=timeout or ssh.connect_timeout_seconds + 30)
        combined = result.stderr + result.stdout
        for pattern, label in _METADATA_WARNING_PATTERNS:
            if pattern.search(combined):
                warnings.append(f"Remote destination does not appear to preserve {label}")
        if not result.ok and not warnings:
            warnings.append(f"Could not complete metadata probe: {result.stderr.strip()}")
    return warnings
