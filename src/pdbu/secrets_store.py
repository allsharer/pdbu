"""Desktop-keyring backed secret storage.

LUKS passphrases and SSH passwords are never written to PDBU's own
configuration or state files in plain text. When the user opts to save a
secret, it is handed to the freedesktop.org Secret Service (GNOME
Keyring / KWallet) via the ``secret-tool`` command from ``libsecret-tools``.
If that tool is unavailable, saving is simply refused — PDBU will prompt
for the secret again rather than fall back to insecure storage.
"""

from __future__ import annotations

from pdbu import procutil

_SCHEMA = "org.pdbu.Secret"


class SecretStoreUnavailable(Exception):
    """Raised when the desktop keyring integration cannot be used."""


def available() -> bool:
    return procutil.available("secret-tool")


def _require_secret_tool() -> None:
    if not available():
        raise SecretStoreUnavailable(
            "The 'secret-tool' command is not installed (package: libsecret-tools). "
            "PDBU cannot store this secret in the desktop keyring; "
            "you will be prompted for it each time instead."
        )


def store(key: str, secret: str, *, label: str) -> None:
    """Store ``secret`` under ``key`` in the desktop keyring."""
    _require_secret_tool()
    result = procutil.run(
        [
            "secret-tool",
            "store",
            "--label",
            label,
            "pdbu-schema",
            _SCHEMA,
            "pdbu-key",
            key,
        ],
        input=secret,
        timeout=30,
    )
    if not result.ok:
        raise SecretStoreUnavailable(f"Failed to store secret in keyring: {result.stderr.strip()}")


def lookup(key: str) -> str | None:
    """Retrieve a previously stored secret, or None if not present."""
    if not available():
        return None
    result = procutil.run(
        ["secret-tool", "lookup", "pdbu-schema", _SCHEMA, "pdbu-key", key],
        timeout=30,
    )
    if not result.ok:
        return None
    value = result.stdout
    return value[:-1] if value.endswith("\n") else value or None


def clear(key: str) -> None:
    if not available():
        return
    procutil.run(
        ["secret-tool", "clear", "pdbu-schema", _SCHEMA, "pdbu-key", key],
        timeout=30,
    )
