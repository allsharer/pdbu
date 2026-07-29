"""Storage device detection and persistent drive identification.

PDBU never trusts device node names such as ``/dev/sdb1`` because they can
change between boots or reconnections. Instead, configured backup drives
are matched against currently-connected devices by their LUKS container
UUID or plain filesystem UUID, both of which are persistent identifiers
reported by ``lsblk``/``blkid``.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field

from pdbu import procutil
from pdbu.config import DriveConfig

LSBLK_COLUMNS = "NAME,PATH,UUID,FSTYPE,TYPE,MOUNTPOINT,LABEL,SIZE,RM,PKNAME,TRAN"


@dataclass
class BlockDevice:
    name: str
    path: str
    uuid: str
    fstype: str
    type: str
    mountpoint: str | None
    label: str
    size: int
    removable: bool
    pkname: str
    transport: str
    children: list["BlockDevice"] = field(default_factory=list)

    def walk(self):
        yield self
        for child in self.children:
            yield from child.walk()

    @property
    def is_luks(self) -> bool:
        return self.fstype == "crypto_LUKS"

    @property
    def is_crypt_mapping(self) -> bool:
        return self.type == "crypt"


def _to_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).lower() in ("1", "true", "yes")


def _to_int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _parse_device(raw: dict) -> BlockDevice:
    children = [_parse_device(c) for c in raw.get("children", []) or []]
    return BlockDevice(
        name=raw.get("name") or "",
        path=raw.get("path") or (f"/dev/{raw.get('name')}" if raw.get("name") else ""),
        uuid=raw.get("uuid") or "",
        fstype=raw.get("fstype") or "",
        type=raw.get("type") or "",
        mountpoint=raw.get("mountpoint") or None,
        label=raw.get("label") or "",
        size=_to_int(raw.get("size")),
        removable=_to_bool(raw.get("rm")),
        pkname=raw.get("pkname") or "",
        transport=raw.get("tran") or "",
        children=children,
    )


def list_block_devices() -> list[BlockDevice]:
    """Return the full block device tree as reported by ``lsblk``."""
    if not procutil.available("lsblk"):
        return []
    result = procutil.run(["lsblk", "-J", "-b", "-o", LSBLK_COLUMNS])
    if not result.ok or not result.stdout.strip():
        return []
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return []
    return [_parse_device(d) for d in data.get("blockdevices", [])]


def all_devices_flat() -> list[BlockDevice]:
    devices: list[BlockDevice] = []
    for root in list_block_devices():
        devices.extend(root.walk())
    return devices


def find_luks_partitions() -> list[BlockDevice]:
    return [d for d in all_devices_flat() if d.is_luks]


def find_crypt_children(luks_device: BlockDevice) -> list[BlockDevice]:
    return [d for d in luks_device.children if d.is_crypt_mapping]


# ---------------------------------------------------------------------------
# Drive status: matching configured drives to connected hardware
# ---------------------------------------------------------------------------

@dataclass
class DriveStatus:
    label: str
    configured: bool
    connected: bool
    is_luks: bool
    locked: bool | None
    luks_device_path: str | None
    mapped_device_path: str | None
    filesystem_uuid: str | None
    mountpoint: str | None
    mounted: bool
    identity_verified: bool
    free_bytes: int | None = None
    total_bytes: int | None = None


def detect_drive_status(drive: DriveConfig, devices: list[BlockDevice] | None = None) -> DriveStatus:
    """Determine whether ``drive`` is currently connected and its state.

    Matching order: a LUKS container whose UUID equals ``drive.luks_uuid``
    takes priority (encrypted drives), falling back to a plain filesystem
    matching ``drive.filesystem_uuid`` (unencrypted / already-unlocked
    drives configured that way).
    """
    configured = bool(drive.luks_uuid or drive.filesystem_uuid)
    flat = devices if devices is not None else all_devices_flat()

    if not configured:
        return DriveStatus(
            label=drive.name,
            configured=False,
            connected=False,
            is_luks=False,
            locked=None,
            luks_device_path=None,
            mapped_device_path=None,
            filesystem_uuid=None,
            mountpoint=None,
            mounted=False,
            identity_verified=False,
        )

    if drive.luks_uuid:
        luks_dev = next((d for d in flat if d.is_luks and d.uuid == drive.luks_uuid), None)
        if luks_dev is not None:
            crypt_children = find_crypt_children(luks_dev)
            if crypt_children:
                mapped = crypt_children[0]
                fs_matches = (
                    not drive.filesystem_uuid or mapped.uuid == drive.filesystem_uuid
                )
                return DriveStatus(
                    label=drive.name,
                    configured=True,
                    connected=True,
                    is_luks=True,
                    locked=False,
                    luks_device_path=luks_dev.path,
                    mapped_device_path=mapped.path,
                    filesystem_uuid=mapped.uuid or None,
                    mountpoint=mapped.mountpoint,
                    mounted=bool(mapped.mountpoint),
                    identity_verified=fs_matches,
                )
            return DriveStatus(
                label=drive.name,
                configured=True,
                connected=True,
                is_luks=True,
                locked=True,
                luks_device_path=luks_dev.path,
                mapped_device_path=None,
                filesystem_uuid=None,
                mountpoint=None,
                mounted=False,
                identity_verified=True,
            )

    if drive.filesystem_uuid:
        plain_dev = next(
            (d for d in flat if not d.is_luks and d.uuid == drive.filesystem_uuid), None
        )
        if plain_dev is not None:
            return DriveStatus(
                label=drive.name,
                configured=True,
                connected=True,
                is_luks=False,
                locked=False,
                luks_device_path=None,
                mapped_device_path=plain_dev.path,
                filesystem_uuid=plain_dev.uuid or None,
                mountpoint=plain_dev.mountpoint,
                mounted=bool(plain_dev.mountpoint),
                identity_verified=True,
            )

    return DriveStatus(
        label=drive.name,
        configured=True,
        connected=False,
        is_luks=bool(drive.luks_uuid),
        locked=None,
        luks_device_path=None,
        mapped_device_path=None,
        filesystem_uuid=None,
        mountpoint=None,
        mounted=False,
        identity_verified=False,
    )


def verify_mount_identity(status: DriveStatus, drive: DriveConfig) -> list[str]:
    """Critical check #5: the mounted filesystem must match the configured UUID."""
    problems: list[str] = []
    if not status.mounted:
        problems.append(f"{drive.name} is not mounted")
        return problems
    if drive.filesystem_uuid and status.filesystem_uuid != drive.filesystem_uuid:
        problems.append(
            f"{drive.name}: mounted filesystem UUID ({status.filesystem_uuid}) does not "
            f"match the configured UUID ({drive.filesystem_uuid}) — refusing to treat "
            "this as the expected backup drive"
        )
    if drive.mount_point and status.mountpoint and status.mountpoint != drive.mount_point:
        problems.append(
            f"{drive.name}: mounted at {status.mountpoint}, "
            f"not the last-known mount point {drive.mount_point} "
            "(this is only a warning if the UUID above matches)"
        )
    return problems


def disk_usage(mountpoint: str) -> tuple[int, int]:
    """Return (total_bytes, free_bytes) for a mounted path."""
    usage = shutil.disk_usage(mountpoint)
    return usage.total, usage.free


# ---------------------------------------------------------------------------
# Alternation recommendation
# ---------------------------------------------------------------------------

def recommend_next_drive(last_used_drive: str | None) -> str:
    """Recommend which configured drive ("drive_a"/"drive_b") to use next.

    Simple alternation: recommend whichever drive was *not* used for the
    last successful backup. With no history yet, default to drive_a.
    """
    if last_used_drive == "drive_a":
        return "drive_b"
    if last_used_drive == "drive_b":
        return "drive_a"
    return "drive_a"
