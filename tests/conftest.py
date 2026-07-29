"""Shared pytest fixtures.

Every test that touches configuration, history or logs uses the
``xdg_env`` fixture to redirect PDBU's XDG directories into a pytest
``tmp_path``, so tests never read or write the real
``~/.config/pdbu`` etc. Nothing here ever touches the developer's real
home directory or attached drives.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def xdg_env(tmp_path, monkeypatch):
    config_home = tmp_path / "config"
    data_home = tmp_path / "data"
    state_home = tmp_path / "state"
    cache_home = tmp_path / "cache"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    monkeypatch.setenv("XDG_DATA_HOME", str(data_home))
    monkeypatch.setenv("XDG_STATE_HOME", str(state_home))
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache_home))
    return {
        "config_home": config_home,
        "data_home": data_home,
        "state_home": state_home,
        "cache_home": cache_home,
    }


@pytest.fixture
def home_and_backup(tmp_path):
    """A fake source ("home") directory and an empty backup destination."""
    source = tmp_path / "home" / "user"
    source.mkdir(parents=True)
    (source / "doc.txt").write_text("hello world")
    (source / "sub").mkdir()
    (source / "sub" / "nested.txt").write_text("nested")

    destination = tmp_path / "backup_drive"
    destination.mkdir()
    return source, destination
