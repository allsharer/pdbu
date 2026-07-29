#!/usr/bin/env bash
# Install PDBU for the current user (no root required for the Python
# package itself; GTK bindings and rsync/ssh/cryptsetup come from apt).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
BIN_DIR="$HOME/.local/bin"

echo "== PDBU installer =="

# --- 1. Check required external commands -----------------------------------
missing=()
for cmd in rsync ssh python3 pip3; do
    command -v "$cmd" >/dev/null 2>&1 || missing+=("$cmd")
done
if [ "${#missing[@]}" -gt 0 ]; then
    echo "Missing required commands: ${missing[*]}" >&2
    echo "On Ubuntu: sudo apt install rsync openssh-client python3 python3-pip" >&2
    exit 1
fi

for cmd in cryptsetup lsblk findmnt udisksctl; do
    command -v "$cmd" >/dev/null 2>&1 || echo "Warning: '$cmd' not found — LUKS drive features will not work until it is installed."
done
command -v notify-send >/dev/null 2>&1 || echo "Warning: 'notify-send' (libnotify-bin) not found — backup reminders will not be shown."
command -v secret-tool  >/dev/null 2>&1 || echo "Warning: 'secret-tool' (libsecret-tools) not found — passphrases cannot be saved to the desktop keyring."

if python3 -c "import gi; gi.require_version('Gtk','4.0'); from gi.repository import Gtk" >/dev/null 2>&1; then
    echo "GTK4 + PyGObject found — the GUI (pdbu-gui) will be available."
    GUI_OK=1
else
    echo "Warning: PyGObject/GTK4 not found — only the CLI will work."
    echo "  On Ubuntu: sudo apt install python3-gi gir1.2-gtk-4.0"
    GUI_OK=0
fi

# --- 2. Install the Python package -----------------------------------------
echo "Installing the pdbu Python package..."
if ! pip3 install --user "$SCRIPT_DIR" 2>/tmp/pdbu-pip-error.log; then
    if grep -qi "externally-managed-environment" /tmp/pdbu-pip-error.log; then
        echo "Detected an externally-managed Python environment; retrying with --break-system-packages."
        pip3 install --user --break-system-packages "$SCRIPT_DIR"
    else
        cat /tmp/pdbu-pip-error.log >&2
        exit 1
    fi
fi
rm -f /tmp/pdbu-pip-error.log

case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *) echo "Note: $BIN_DIR is not on your PATH. Add this to your shell profile:" ;
       echo "  export PATH=\"\$HOME/.local/bin:\$PATH\"" ;;
esac

# --- 3. Desktop integration --------------------------------------------------
mkdir -p "$DATA_HOME/applications" "$DATA_HOME/icons/hicolor/scalable/apps"
if [ "$GUI_OK" -eq 1 ]; then
    install -m 644 "$SCRIPT_DIR/packaging/pdbu.desktop" "$DATA_HOME/applications/pdbu.desktop"
    install -m 644 "$SCRIPT_DIR/packaging/icons/pdbu.svg" "$DATA_HOME/icons/hicolor/scalable/apps/pdbu.svg"
    command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$DATA_HOME/applications" || true
    command -v gtk-update-icon-cache >/dev/null 2>&1 && gtk-update-icon-cache -f -t "$DATA_HOME/icons/hicolor" >/dev/null 2>&1 || true
    echo "Installed desktop launcher and icon."
fi

# --- 4. Man page --------------------------------------------------------
MAN_DIR="$DATA_HOME/man/man1"
mkdir -p "$MAN_DIR"
install -m 644 "$SCRIPT_DIR/packaging/man/pdbu.1" "$MAN_DIR/pdbu.1"
gzip -f "$MAN_DIR/pdbu.1" 2>/dev/null || true
echo "Installed man page (try: man pdbu)."

# --- 5. Shell completion -------------------------------------------------
# Use the freshly-installed binary directly (not a PATH lookup): if another
# program named 'pdbu' already exists earlier on PATH, `command -v pdbu`
# would silently generate completion for the wrong program.
COMPLETION_DIR="$DATA_HOME/bash-completion/completions"
mkdir -p "$COMPLETION_DIR"
if [ -x "$BIN_DIR/pdbu" ]; then
    if _PDBU_COMPLETE=bash_source "$BIN_DIR/pdbu" > "$COMPLETION_DIR/pdbu" 2>/dev/null; then
        echo "Installed bash completion."
    else
        echo "Note: could not generate bash completion (non-fatal)."
    fi
fi

# --- 6. systemd user units for reminders ---------------------------------
SYSTEMD_USER_DIR="$HOME/.config/systemd/user"
mkdir -p "$SYSTEMD_USER_DIR"
install -m 644 "$SCRIPT_DIR/packaging/systemd/pdbu-reminder.service" "$SYSTEMD_USER_DIR/"
install -m 644 "$SCRIPT_DIR/packaging/systemd/pdbu-reminder.timer" "$SYSTEMD_USER_DIR/"
if command -v systemctl >/dev/null 2>&1 && systemctl --user status >/dev/null 2>&1; then
    if systemctl --user daemon-reload && systemctl --user enable --now pdbu-reminder.timer; then
        echo "Enabled the pdbu-reminder systemd user timer."
    else
        echo "Note: could not enable the systemd user timer automatically (non-fatal)."
        echo "  Run later with: systemctl --user enable --now pdbu-reminder.timer"
    fi
else
    echo "Note: could not enable the systemd user timer automatically (no user session bus)."
    echo "  Run later with: systemctl --user enable --now pdbu-reminder.timer"
fi

echo
echo "== PDBU installed =="
echo "Run 'pdbu config --edit' to configure your source directory and backup drives."
echo "Run 'pdbu status' to see the dashboard, or launch 'pdbu-gui' for the graphical interface."
