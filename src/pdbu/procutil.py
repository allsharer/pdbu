"""Safe subprocess execution helpers shared across PDBU.

Every external command in PDBU is invoked as an argument array — never
via a shell string — to avoid shell-injection risk. This module is the
single choke point for that invocation so the rule is enforced in one
place rather than re-implemented at every call site.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass


class CommandNotFoundError(Exception):
    def __init__(self, command: str):
        self.command = command
        super().__init__(f"Required command not found on PATH: {command}")


@dataclass
class CommandResult:
    args: list[str]
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def require(command: str) -> str:
    """Resolve ``command`` to an absolute path, raising if it's missing."""
    resolved = shutil.which(command)
    if resolved is None:
        raise CommandNotFoundError(command)
    return resolved


def available(command: str) -> bool:
    return shutil.which(command) is not None


def run(
    args: list[str],
    *,
    input: str | None = None,
    timeout: float | None = None,
    check: bool = False,
    env: dict[str, str] | None = None,
) -> CommandResult:
    """Run ``args`` (an argument vector — never a shell string) and capture output."""
    if not args:
        raise ValueError("args must be a non-empty argument vector")
    proc = subprocess.run(
        args,
        input=input,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
        shell=False,
    )
    result = CommandResult(
        args=args, returncode=proc.returncode, stdout=proc.stdout, stderr=proc.stderr
    )
    if check and not result.ok:
        raise subprocess.CalledProcessError(
            result.returncode, args, output=result.stdout, stderr=result.stderr
        )
    return result
