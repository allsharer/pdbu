"""LUKS unlock/lock and mount/unmount via ``udisksctl``.

``udisksctl`` talks to the UDisks2 D-Bus service, which in turn uses
PolicyKit for authorization. This lets PDBU unlock and mount an
encrypted backup drive as a normal user — authenticated through the
desktop's existing polkit agent — instead of requiring PDBU itself to
run as root.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from pdbu import procutil

_UNLOCK_RE = re.compile(r"Unlocked .* as (?P<device>/dev/\S+?)\.?\s*$", re.MULTILINE)
_MOUNT_RE = re.compile(r"Mounted .* at (?P<path>.+?)\.?\s*$", re.MULTILINE)


class LuksError(Exception):
    pass


@dataclass
class UnlockResult:
    mapped_device_path: str


@dataclass
class MountResult:
    mountpoint: str


def unlock(luks_device_path: str, passphrase: str, *, timeout: float = 60) -> UnlockResult:
    """Unlock a LUKS container, returning the mapped ``/dev/dm-N`` path.

    The passphrase is streamed to ``udisksctl`` over its own stdin (via
    ``--key-file /dev/stdin``) rather than as a command-line argument or a
    temporary file, so it never appears in ``/proc/*/cmdline``, shell
    history, or on disk.
    """
    procutil.require("udisksctl")
    result = procutil.run(
        [
            "udisksctl",
            "unlock",
            "--block-device",
            luks_device_path,
            "--key-file",
            "/dev/stdin",
            "--no-user-interaction",
        ],
        input=passphrase,
        timeout=timeout,
    )
    if not result.ok:
        raise LuksError(f"Failed to unlock {luks_device_path}: {result.stderr.strip() or result.stdout.strip()}")
    match = _UNLOCK_RE.search(result.stdout)
    if not match:
        raise LuksError(f"Unlocked {luks_device_path} but could not parse mapped device from: {result.stdout!r}")
    return UnlockResult(mapped_device_path=match.group("device"))


def lock(luks_device_path: str, *, timeout: float = 30) -> None:
    procutil.require("udisksctl")
    result = procutil.run(
        ["udisksctl", "lock", "--block-device", luks_device_path],
        timeout=timeout,
    )
    if not result.ok:
        raise LuksError(f"Failed to lock {luks_device_path}: {result.stderr.strip() or result.stdout.strip()}")


def mount(mapped_device_path: str, *, timeout: float = 30) -> MountResult:
    procutil.require("udisksctl")
    result = procutil.run(
        ["udisksctl", "mount", "--block-device", mapped_device_path],
        timeout=timeout,
    )
    if not result.ok:
        if "AlreadyMounted" in result.stderr:
            raise LuksError(f"{mapped_device_path} is already mounted")
        raise LuksError(f"Failed to mount {mapped_device_path}: {result.stderr.strip() or result.stdout.strip()}")
    match = _MOUNT_RE.search(result.stdout)
    if not match:
        raise LuksError(f"Mounted {mapped_device_path} but could not parse mount point from: {result.stdout!r}")
    return MountResult(mountpoint=match.group("path").strip())


def unmount(mapped_device_path: str, *, timeout: float = 60) -> None:
    procutil.require("udisksctl")
    result = procutil.run(
        ["udisksctl", "unmount", "--block-device", mapped_device_path],
        timeout=timeout,
    )
    if not result.ok and "NotMounted" not in result.stderr:
        raise LuksError(f"Failed to unmount {mapped_device_path}: {result.stderr.strip() or result.stdout.strip()}")


def unlock_and_mount(luks_device_path: str, passphrase: str) -> MountResult:
    unlocked = unlock(luks_device_path, passphrase)
    return mount(unlocked.mapped_device_path)


def unmount_and_lock(mapped_device_path: str, luks_device_path: str, *, also_lock: bool = True) -> None:
    unmount(mapped_device_path)
    if also_lock:
        lock(luks_device_path)
