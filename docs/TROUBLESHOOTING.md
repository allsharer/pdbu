# Troubleshooting

## "Destination does not exist" / backup refuses to start

PDBU checks that the drive is *actually mounted* before backing up — it
will not create a backup inside an empty mount-point directory just
because the directory exists. Run `pdbu drives` to see whether the drive
is connected, locked, or mounted, and unlock/mount it (GUI: click the
drive in Back Up Now; CLI: `pdbu backup --drive-a` will prompt to unlock).

## "mounted filesystem UUID does not match the configured UUID"

The drive currently mounted at that path isn't the one PDBU expects —
often because a *different* drive is plugged into the same USB port, or
the wrong drive of the A/B pair is connected. Check `pdbu drives` to
confirm which UUID is actually present, and either connect the correct
drive or update `drive_a.filesystem_uuid`/`drive_b.filesystem_uuid` in
`pdbu config --edit` if the drive's filesystem was genuinely recreated.

## A backup would delete more files than expected

This is `rsync --delete` doing its job: files removed from your home
directory since the last backup get removed from the mirror too. Run
`pdbu backup --dry-run` first to see exactly what would be deleted. If
this number is unexpectedly large, check that `source.home_directory` in
config still points at the right place, and that no exclusion pattern
changed in a way that makes previously-backed-up files look "removed."

## PDBU says a previous operation didn't finish cleanly

A prior backup/restore was interrupted (crash, power loss, forced kill)
before it could clean up its lock file. Check `pdbu logs` for that
operation's log to see how far it got, then re-run the backup/restore —
rsync mirrors are idempotent, so re-running safely completes whatever was
left. Pass `--yes`/confirm the prompt to proceed past the warning.

## Unlocking a LUKS drive fails or the passphrase prompt never appears

- Confirm `udisksctl` is installed (`udisks2` package) and a PolicyKit
  agent is running in your desktop session (standard on GNOME/KDE/Unity;
  headless/minimal setups may need `polkit-gnome` or similar).
- `pdbu drives` shows whether PDBU sees the drive as locked at all — if it
  shows "not connected," the kernel hasn't seen the block device yet (try
  `lsblk` directly).

## Saved passphrases/passwords aren't offered again

Passphrase/password saving requires `secret-tool` (`libsecret-tools`
package) and a running Secret Service provider (GNOME Keyring by default).
If either is missing, PDBU just asks again each time rather than falling
back to insecure storage — install `libsecret-tools` if you want the
saved-passphrase convenience.

## SSH backup fails with a host-key error

PDBU deliberately never disables SSH host-key checking by default. Connect
once with plain `ssh <host>` (or use the future "fetch host key" flow) to
inspect and accept the host's fingerprint into `~/.ssh/known_hosts` before
using it from PDBU. If you intentionally want to auto-trust new hosts,
turn off `ssh.strict_host_key_checking` in config — this still verifies
against `known_hosts` afterward (`accept-new`), it does not disable
checking outright.

## SSH backup fails with a permissions/ACL/xattr warning afterward

The remote filesystem (common with some NAS/SMB/exFAT-backed destinations)
doesn't support Linux ownership, permissions, ACLs, or extended attributes.
PDBU can still back up file contents there, but a restore back to a normal
Linux filesystem won't recover that metadata. Use Settings → SSH → Test
Connection (or `pdbu`'s remote metadata probe) to check ahead of time, and
prefer a Linux-native remote filesystem when metadata fidelity matters.

## No desktop notifications appear when a backup is overdue

- `notify-send` must be installed (`libnotify-bin`) and
  `reminders.notifications_enabled` must be `true` in config.
- Check the timer is active: `systemctl --user status pdbu-reminder.timer`.
  If `install.sh` couldn't enable it automatically (no session bus at
  install time), run `systemctl --user enable --now pdbu-reminder.timer`
  yourself.
- Notifications are throttled to avoid spam — if one was already shown
  recently, `pdbu reminder-check` intentionally stays silent until the
  throttle window passes. Run `pdbu schedule` to confirm a backup is
  actually due.

## `pdbu` runs a different program than expected

If you have another program named `pdbu` earlier on your `PATH` (check
with `which -a pdbu`), it will shadow this one. Either remove/rename the
other script, reorder your `PATH` so `~/.local/bin` comes first, or invoke
this PDBU by its full path (`~/.local/bin/pdbu`).

## `pip install` fails with "externally-managed-environment"

Recent Debian/Ubuntu Python installs block plain `pip install --user` by
default (PEP 668). `install.sh`/`uninstall.sh` detect this and
automatically retry with `--break-system-packages`, which is safe here
since PDBU's only dependency is `click` (GTK bindings come from apt, not
pip). If installing by hand, add that flag yourself.

## GUI won't launch / `pdbu-gui` errors on import

Install the GTK4 PyGObject bindings: `sudo apt install python3-gi
gir1.2-gtk-4.0`. The CLI (`pdbu`) works independently of these and does
not require them.
