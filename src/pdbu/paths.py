"""XDG Base Directory locations used by PDBU.

Nothing is written directly into the user's home directory root; all
application state lives under the standard XDG subdirectories.
"""

from __future__ import annotations

import os
from pathlib import Path


def _xdg(env_var: str, default_relative: str) -> Path:
    value = os.environ.get(env_var)
    if value:
        return Path(value)
    return Path.home() / default_relative


def config_home() -> Path:
    return _xdg("XDG_CONFIG_HOME", ".config")


def data_home() -> Path:
    return _xdg("XDG_DATA_HOME", ".local/share")


def state_home() -> Path:
    return _xdg("XDG_STATE_HOME", ".local/state")


def cache_home() -> Path:
    return _xdg("XDG_CACHE_HOME", ".cache")


def config_dir() -> Path:
    return config_home() / "pdbu"


def data_dir() -> Path:
    return data_home() / "pdbu"


def state_dir() -> Path:
    return state_home() / "pdbu"


def cache_dir() -> Path:
    return cache_home() / "pdbu"


def log_dir() -> Path:
    return state_dir() / "logs"


def config_file() -> Path:
    return config_dir() / "config.toml"


def history_db() -> Path:
    return data_dir() / "history.sqlite3"


def reminder_state_file() -> Path:
    return state_dir() / "reminder-state.json"


def operation_lock_file() -> Path:
    return state_dir() / "operation.lock"


def ensure_dirs() -> None:
    """Create all PDBU XDG directories with restrictive permissions."""
    for path in (config_dir(), data_dir(), state_dir(), cache_dir(), log_dir()):
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            os.chmod(path, 0o700)
        except OSError:
            pass
