"""Configuration handling for PDBU.

Configuration is stored as human-readable TOML under
``$XDG_CONFIG_HOME/pdbu/config.toml``. This module defines the schema as
plain dataclasses, plus loading, saving and validation helpers. Only the
Python standard library is used (``tomllib`` for reading; a small dedicated
writer for saving) to avoid an extra runtime dependency.
"""

from __future__ import annotations

import copy
import os
import tomllib
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any

from pdbu import paths


class ConfigError(Exception):
    """Raised when configuration is invalid or cannot be read/written."""


DEFAULT_EXCLUSIONS: dict[str, bool] = {
    ".cache/": True,
    ".gvfs/": True,
    ".local/share/Trash/": True,
    "Downloads/": False,
}


@dataclass
class SourceConfig:
    home_directory: str = field(default_factory=lambda: str(Path.home()))


@dataclass
class BackupConfig:
    delete_removed_files: bool = True
    dry_run_first: bool = True
    preserve_acls: bool = True
    preserve_xattrs: bool = True
    preserve_hard_links: bool = True
    preserve_extended_attrs: bool = True
    numeric_ids: bool = True
    verify_destination: bool = True
    delete_confirm_threshold: int = 50
    bandwidth_limit_kbps: int = 0
    extra_rsync_options: list[str] = field(default_factory=list)


@dataclass
class ExclusionsConfig:
    defaults_enabled: dict[str, bool] = field(
        default_factory=lambda: dict(DEFAULT_EXCLUSIONS)
    )
    additional: list[str] = field(default_factory=list)

    def active_patterns(self) -> list[str]:
        patterns = [
            pattern
            for pattern, enabled in self.defaults_enabled.items()
            if enabled
        ]
        patterns.extend(self.additional)
        return patterns


@dataclass
class DriveConfig:
    name: str = "Backup Drive"
    luks_uuid: str = ""
    filesystem_uuid: str = ""
    mount_point: str = ""
    backup_subdir: str = "pdbuBackups"
    lock_after_backup: bool = True


@dataclass
class SSHConfig:
    enabled: bool = False
    host: str = ""
    port: int = 22
    username: str = ""
    destination: str = ""
    identity_file: str = ""
    host_alias: str = ""
    use_password_auth: bool = False
    strict_host_key_checking: bool = True
    connect_timeout_seconds: int = 10
    bandwidth_limit_kbps: int = 0


@dataclass
class RemindersConfig:
    interval_days: float = 7
    snooze_hours: int = 24
    notifications_enabled: bool = True


@dataclass
class LoggingConfig:
    retention_days: int = 90
    verbose: bool = False


@dataclass
class GuiConfig:
    theme: str = "system"  # system | light | dark


@dataclass
class Config:
    source: SourceConfig = field(default_factory=SourceConfig)
    backup: BackupConfig = field(default_factory=BackupConfig)
    exclusions: ExclusionsConfig = field(default_factory=ExclusionsConfig)
    drive_a: DriveConfig = field(
        default_factory=lambda: DriveConfig(name="Backup Drive A")
    )
    drive_b: DriveConfig = field(
        default_factory=lambda: DriveConfig(name="Backup Drive B")
    )
    ssh: SSHConfig = field(default_factory=SSHConfig)
    reminders: RemindersConfig = field(default_factory=RemindersConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    gui: GuiConfig = field(default_factory=GuiConfig)


_SECTION_TYPES = {
    "source": SourceConfig,
    "backup": BackupConfig,
    "exclusions": ExclusionsConfig,
    "drive_a": DriveConfig,
    "drive_b": DriveConfig,
    "ssh": SSHConfig,
    "reminders": RemindersConfig,
    "logging": LoggingConfig,
    "gui": GuiConfig,
}


def _dataclass_from_dict(cls: type, data: dict[str, Any]) -> Any:
    kwargs: dict[str, Any] = {}
    valid = {f.name for f in fields(cls)}
    for key, value in data.items():
        if key in valid:
            kwargs[key] = value
    return cls(**kwargs)


def config_from_dict(data: dict[str, Any]) -> Config:
    cfg = Config()
    for section_name, section_type in _SECTION_TYPES.items():
        if section_name in data and isinstance(data[section_name], dict):
            setattr(cfg, section_name, _dataclass_from_dict(section_type, data[section_name]))
    return cfg


def config_to_dict(cfg: Config) -> dict[str, Any]:
    return asdict(cfg)


def default_config() -> Config:
    return Config()


# ---------------------------------------------------------------------------
# Minimal TOML writer (stdlib has no writer; schema here is fixed & simple)
# ---------------------------------------------------------------------------

def _toml_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    raise ConfigError(f"Unsupported TOML scalar type: {type(value)!r}")


def _toml_value(value: Any) -> str:
    if isinstance(value, list):
        return "[" + ", ".join(_toml_scalar(v) for v in value) + "]"
    return _toml_scalar(value)


def _toml_key(key: str) -> str:
    if key and all(c.isalnum() or c == "_" or c == "-" for c in key):
        return key
    escaped = key.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _write_table(lines: list[str], table_path: str, data: dict[str, Any]) -> None:
    scalars = {k: v for k, v in data.items() if not isinstance(v, dict)}
    subtables = {k: v for k, v in data.items() if isinstance(v, dict)}

    lines.append(f"[{table_path}]")
    for key, value in scalars.items():
        lines.append(f"{_toml_key(key)} = {_toml_value(value)}")
    lines.append("")

    for key, value in subtables.items():
        _write_table(lines, f"{table_path}.{_toml_key(key)}", value)


def dumps_toml(data: dict[str, Any]) -> str:
    lines: list[str] = [
        "# PDBU configuration file",
        "# Generated by PDBU — edit with `pdbu config --edit` or by hand.",
        "",
    ]
    for section, value in data.items():
        _write_table(lines, section, value)
    return "\n".join(lines).rstrip() + "\n"


def loads_toml(text: str) -> dict[str, Any]:
    return tomllib.loads(text)


# ---------------------------------------------------------------------------
# Load / save
# ---------------------------------------------------------------------------

def load(path: Path | None = None) -> Config:
    """Load config from disk, returning defaults if no file exists yet."""
    config_path = path or paths.config_file()
    if not config_path.exists():
        return default_config()
    try:
        raw = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"Could not parse {config_path}: {exc}") from exc
    cfg = config_from_dict(raw)
    validate(cfg)
    return cfg


def save(cfg: Config, path: Path | None = None) -> Path:
    validate(cfg)
    config_path = path or paths.config_file()
    config_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    text = dumps_toml(config_to_dict(cfg))
    tmp_path = config_path.with_suffix(".toml.tmp")
    tmp_path.write_text(text, encoding="utf-8")
    os.chmod(tmp_path, 0o600)
    tmp_path.replace(config_path)
    os.chmod(config_path, 0o600)
    return config_path


def ensure_default_config() -> Path:
    """Write a default config file if one does not already exist."""
    config_path = paths.config_file()
    if not config_path.exists():
        save(default_config(), config_path)
    return config_path


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate(cfg: Config) -> list[str]:
    """Validate a Config, raising ConfigError with all problems found.

    Returns the list of warnings (non-fatal issues) on success.
    """
    errors: list[str] = []
    warnings: list[str] = []

    if not cfg.source.home_directory:
        errors.append("source.home_directory must not be empty")
    elif not os.path.isabs(cfg.source.home_directory):
        errors.append("source.home_directory must be an absolute path")

    if cfg.backup.delete_confirm_threshold < 0:
        errors.append("backup.delete_confirm_threshold must be >= 0")
    if cfg.backup.bandwidth_limit_kbps < 0:
        errors.append("backup.bandwidth_limit_kbps must be >= 0")
    if not isinstance(cfg.backup.extra_rsync_options, list):
        errors.append("backup.extra_rsync_options must be a list of strings")

    for drive_name, drive in (("drive_a", cfg.drive_a), ("drive_b", cfg.drive_b)):
        if drive.mount_point and not os.path.isabs(drive.mount_point):
            errors.append(f"{drive_name}.mount_point must be an absolute path")
        if drive.backup_subdir:
            if os.path.isabs(drive.backup_subdir) or ".." in Path(drive.backup_subdir).parts:
                errors.append(
                    f"{drive_name}.backup_subdir must be a relative path with no '..' segments"
                )
        if not drive.luks_uuid and not drive.filesystem_uuid:
            warnings.append(
                f"{drive_name} has no luks_uuid or filesystem_uuid configured; "
                "it cannot be reliably identified yet"
            )

    if cfg.drive_a.mount_point and cfg.drive_a.mount_point == cfg.drive_b.mount_point:
        errors.append("drive_a.mount_point and drive_b.mount_point must differ")
    if (
        cfg.drive_a.filesystem_uuid
        and cfg.drive_a.filesystem_uuid == cfg.drive_b.filesystem_uuid
    ):
        errors.append("drive_a and drive_b must not share the same filesystem_uuid")

    if cfg.ssh.enabled:
        if not cfg.ssh.host and not cfg.ssh.host_alias:
            errors.append("ssh.host or ssh.host_alias must be set when ssh.enabled is true")
        if not (1 <= cfg.ssh.port <= 65535):
            errors.append("ssh.port must be between 1 and 65535")
        if not cfg.ssh.destination:
            errors.append("ssh.destination must be set when ssh.enabled is true")
        if cfg.ssh.connect_timeout_seconds <= 0:
            errors.append("ssh.connect_timeout_seconds must be > 0")
        if cfg.ssh.bandwidth_limit_kbps < 0:
            errors.append("ssh.bandwidth_limit_kbps must be >= 0")
        if cfg.ssh.identity_file and not os.path.isabs(
            os.path.expanduser(cfg.ssh.identity_file)
        ):
            errors.append("ssh.identity_file must be an absolute path (or start with ~)")

    if cfg.reminders.interval_days <= 0:
        errors.append("reminders.interval_days must be > 0")
    if cfg.reminders.snooze_hours <= 0:
        errors.append("reminders.snooze_hours must be > 0")

    if cfg.logging.retention_days < 0:
        errors.append("logging.retention_days must be >= 0")

    if cfg.gui.theme not in ("system", "light", "dark"):
        errors.append("gui.theme must be one of: system, light, dark")

    if errors:
        raise ConfigError("Invalid configuration:\n" + "\n".join(f"  - {e}" for e in errors))
    return warnings


def deep_copy(cfg: Config) -> Config:
    return copy.deepcopy(cfg)
