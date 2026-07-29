#!/usr/bin/env bash
# Uninstall PDBU for the current user. Configuration, history and logs
# under ~/.config/pdbu, ~/.local/share/pdbu and ~/.local/state/pdbu are
# left in place unless --purge is given, so a reinstall doesn't lose
# backup history or settings by accident.
set -euo pipefail

DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"
STATE_HOME="${XDG_STATE_HOME:-$HOME/.local/state}"
CACHE_HOME="${XDG_CACHE_HOME:-$HOME/.cache}"
PURGE=0

for arg in "$@"; do
    case "$arg" in
        --purge) PURGE=1 ;;
        *) echo "Unknown option: $arg" >&2; exit 2 ;;
    esac
done

echo "== PDBU uninstaller =="

if command -v systemctl >/dev/null 2>&1 && systemctl --user status >/dev/null 2>&1; then
    systemctl --user disable --now pdbu-reminder.timer 2>/dev/null || true
    systemctl --user daemon-reload || true
fi
rm -f "$HOME/.config/systemd/user/pdbu-reminder.service" "$HOME/.config/systemd/user/pdbu-reminder.timer"

rm -f "$DATA_HOME/applications/pdbu.desktop"
rm -f "$DATA_HOME/icons/hicolor/scalable/apps/pdbu.svg"
rm -f "$DATA_HOME/man/man1/pdbu.1" "$DATA_HOME/man/man1/pdbu.1.gz"
rm -f "$DATA_HOME/bash-completion/completions/pdbu"
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$DATA_HOME/applications" || true

echo "Removing the pdbu Python package..."
if ! pip3 uninstall -y pdbu 2>/tmp/pdbu-pip-uninstall-error.log; then
    if grep -qi "externally-managed-environment" /tmp/pdbu-pip-uninstall-error.log; then
        pip3 uninstall -y --break-system-packages pdbu || true
    else
        cat /tmp/pdbu-pip-uninstall-error.log >&2
    fi
fi
rm -f /tmp/pdbu-pip-uninstall-error.log

if [ "$PURGE" -eq 1 ]; then
    echo "Purging configuration, history and logs..."
    rm -rf "$CONFIG_HOME/pdbu" "$DATA_HOME/pdbu" "$STATE_HOME/pdbu" "$CACHE_HOME/pdbu"
else
    echo "Configuration, history and logs were left in place. Re-run with --purge to remove them:"
    echo "  $CONFIG_HOME/pdbu  $DATA_HOME/pdbu  $STATE_HOME/pdbu  $CACHE_HOME/pdbu"
fi

echo "== PDBU uninstalled =="
