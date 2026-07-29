from __future__ import annotations

from pdbu import devices
from pdbu.config import DriveConfig


def _make_tree():
    """Simulate lsblk output for one connected LUKS drive (unlocked)."""
    return [
        devices.BlockDevice(
            name="sdb", path="/dev/sdb", uuid="", fstype="", type="disk",
            mountpoint=None, label="", size=1_000_000_000, removable=True,
            pkname="", transport="usb",
            children=[
                devices.BlockDevice(
                    name="sdb1", path="/dev/sdb1", uuid="LUKS-UUID-A",
                    fstype="crypto_LUKS", type="part", mountpoint=None, label="",
                    size=999_000_000, removable=True, pkname="sdb", transport="usb",
                    children=[
                        devices.BlockDevice(
                            name="dm-0", path="/dev/dm-0", uuid="FS-UUID-A",
                            fstype="ext4", type="crypt", mountpoint="/media/user/PDBU-A",
                            label="PDBU-A", size=999_000_000, removable=False,
                            pkname="sdb1", transport="",
                        )
                    ],
                )
            ],
        )
    ]


def _flat(tree):
    out = []
    for d in tree:
        out.extend(d.walk())
    return out


def test_detect_mounted_unlocked_drive():
    flat = _flat(_make_tree())
    drive = DriveConfig(
        name="Backup Drive A", luks_uuid="LUKS-UUID-A", filesystem_uuid="FS-UUID-A",
        mount_point="/media/user/PDBU-A",
    )
    status = devices.detect_drive_status(drive, flat)
    assert status.connected
    assert status.locked is False
    assert status.mounted
    assert status.identity_verified
    assert status.mountpoint == "/media/user/PDBU-A"


def test_detect_locked_drive():
    tree = _make_tree()
    tree[0].children[0].children = []  # no crypt child => locked
    flat = _flat(tree)
    drive = DriveConfig(name="Backup Drive A", luks_uuid="LUKS-UUID-A")
    status = devices.detect_drive_status(drive, flat)
    assert status.connected
    assert status.locked is True
    assert not status.mounted


def test_detect_not_connected():
    flat = _flat(_make_tree())
    drive = DriveConfig(name="Backup Drive B", luks_uuid="LUKS-UUID-B")
    status = devices.detect_drive_status(drive, flat)
    assert not status.connected


def test_detect_unconfigured_drive():
    drive = DriveConfig(name="Backup Drive B")  # no UUIDs set
    status = devices.detect_drive_status(drive, [])
    assert not status.configured
    assert not status.connected


def test_uuid_mismatch_flagged():
    flat = _flat(_make_tree())
    drive = DriveConfig(
        name="Backup Drive A", luks_uuid="LUKS-UUID-A", filesystem_uuid="WRONG-UUID",
        mount_point="/media/user/PDBU-A",
    )
    status = devices.detect_drive_status(drive, flat)
    assert status.connected
    assert status.mounted
    assert not status.identity_verified

    problems = devices.verify_mount_identity(status, drive)
    assert any("does not match" in p for p in problems)


def test_recommend_next_drive_alternates():
    assert devices.recommend_next_drive(None) == "drive_a"
    assert devices.recommend_next_drive("drive_a") == "drive_b"
    assert devices.recommend_next_drive("drive_b") == "drive_a"


def test_find_luks_partitions_and_crypt_children():
    tree = _make_tree()
    partitions = [d for d in _flat(tree) if d.is_luks]
    assert len(partitions) == 1
    children = devices.find_crypt_children(partitions[0])
    assert len(children) == 1
    assert children[0].uuid == "FS-UUID-A"
