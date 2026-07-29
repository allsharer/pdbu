from __future__ import annotations

import pytest

from pdbu import config


def test_default_config_is_valid():
    cfg = config.default_config()
    warnings = config.validate(cfg)
    assert isinstance(warnings, list)


def test_round_trip_save_load(tmp_path):
    cfg = config.default_config()
    cfg.source.home_directory = "/home/example"
    cfg.ssh.enabled = True
    cfg.ssh.host = "example.com"
    cfg.ssh.destination = "/backups/home"
    cfg.backup.extra_rsync_options = ["--partial", "--progress"]
    cfg.exclusions.additional = ["Videos/", "*.iso"]

    path = tmp_path / "config.toml"
    config.save(cfg, path)
    loaded = config.load(path)

    assert loaded.source.home_directory == "/home/example"
    assert loaded.ssh.host == "example.com"
    assert loaded.backup.extra_rsync_options == ["--partial", "--progress"]
    assert loaded.exclusions.additional == ["Videos/", "*.iso"]
    assert loaded.exclusions.defaults_enabled["Downloads/"] is False


def test_exclusion_keys_with_special_chars_round_trip(tmp_path):
    """Exclusion patterns like '.cache/' aren't valid bare TOML keys."""
    cfg = config.default_config()
    path = tmp_path / "config.toml"
    config.save(cfg, path)
    text = path.read_text()
    assert '".cache/"' in text
    loaded = config.load(path)
    assert loaded.exclusions.defaults_enabled == cfg.exclusions.defaults_enabled


def test_load_missing_file_returns_defaults(tmp_path):
    cfg = config.load(tmp_path / "does-not-exist.toml")
    assert cfg.reminders.interval_days == 7


def test_load_invalid_toml_raises(tmp_path):
    path = tmp_path / "bad.toml"
    path.write_text("this is not [valid toml")
    with pytest.raises(config.ConfigError):
        config.load(path)


@pytest.mark.parametrize(
    "mutate,expected_snippet",
    [
        (lambda c: setattr(c.source, "home_directory", ""), "home_directory"),
        (lambda c: setattr(c.source, "home_directory", "relative/path"), "absolute"),
        (lambda c: setattr(c.backup, "delete_confirm_threshold", -1), "delete_confirm_threshold"),
        (lambda c: setattr(c.drive_a, "mount_point", "not/absolute"), "mount_point"),
        (lambda c: (setattr(c.ssh, "enabled", True), setattr(c.ssh, "port", 0)), "port"),
        (lambda c: setattr(c.reminders, "interval_days", 0), "interval_days"),
        (lambda c: setattr(c.gui, "theme", "rainbow"), "theme"),
    ],
)
def test_validation_catches_bad_values(mutate, expected_snippet):
    cfg = config.default_config()
    mutate(cfg)
    with pytest.raises(config.ConfigError) as exc_info:
        config.validate(cfg)
    assert expected_snippet in str(exc_info.value)


def test_same_drive_uuid_rejected():
    cfg = config.default_config()
    cfg.drive_a.filesystem_uuid = "SAME-UUID"
    cfg.drive_b.filesystem_uuid = "SAME-UUID"
    with pytest.raises(config.ConfigError):
        config.validate(cfg)


def test_ensure_default_config_creates_file(xdg_env):
    path = config.ensure_default_config()
    assert path.exists()
    assert oct(path.stat().st_mode)[-3:] == "600"
    # calling again must not clobber an edited file
    cfg = config.load(path)
    cfg.reminders.interval_days = 30
    config.save(cfg, path)
    config.ensure_default_config()
    assert config.load(path).reminders.interval_days == 30
