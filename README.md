# PDBU — Personal Directory Backup Utility

PDBU backs up and restores a Linux `/home/<username>` directory using
[`rsync`](https://rsync.samba.org/), to a local LUKS-encrypted external drive
or a remote host over SSH. It ships a GTK4 desktop application (`pdbu-gui`)
and a full command-line interface (`pdbu`), both built on the same backup
and restore engine, so they always behave identically.

Targets Ubuntu Linux, but should work on any modern Linux desktop with
`rsync`, `ssh`, `cryptsetup`, `lsblk`, `udisksctl`, and (optionally)
GTK4/PyGObject installed.

## What PDBU does

- Mirrors your home directory to a backup destination with `rsync -aHAX
  --numeric-ids`, preserving permissions, ownership, timestamps, symlinks,
  hard links, ACLs, extended attributes, and hidden files.
- Detects, unlocks, and mounts LUKS-encrypted backup drives, identifying
  them by UUID (not device name), and recommends alternating between two
  labelled drives (**Backup Drive A** / **Backup Drive B**).
- Backs up over SSH with host-key verification, bandwidth limiting, and
  connection testing.
- Restores a full backup or hand-picked files/folders, to the original
  location or somewhere else, with configurable conflict handling.
- Reminds you when a backup is overdue via desktop notifications, driven by
  a `systemd --user` timer — no need to keep PDBU running.
- Never runs as root. Drive unlock/mount goes through `udisksctl`
  (PolicyKit-authenticated); no LUKS passphrase or SSH password is ever
  written to disk in plain text — they go through the desktop keyring
  (`secret-tool`) if you choose to save them.

## What a "one-to-one mirrored backup" means

After a backup, the destination looks exactly like the source: same files,
same structure, same permissions/ownership/timestamps where the destination
filesystem supports them — **and nothing extra**. If a file is deleted from
your home directory, the next backup deletes it from the destination too
(this is `rsync --delete`). That's what "mirror" means here, as opposed to
an incremental archive that keeps old versions around.

### The risk of `rsync --delete`

`--delete` is powerful and dangerous: if PDBU is pointed at the wrong
destination (or the destination is only *supposed* to be your backup drive
but isn't actually mounted there), a mirror backup will delete files at that
destination to match your (possibly wrong, possibly empty) source. PDBU
guards against this with the checks in
[Recovering from interrupted operations](#recovering-from-interrupted-or-incomplete-operations)
and below, but you should still understand what you're enabling:

- Before every deleting backup, PDBU checks that the destination is
  actually mounted, matches the drive's configured UUID, isn't the
  filesystem root, isn't your home directory, and isn't nested inside (or
  containing) the source.
- If a backup would delete more files than `backup.delete_confirm_threshold`
  (default 50), PDBU asks for explicit confirmation before proceeding —
  in the GUI as a dialog, on the CLI as a prompt (or refuses outright
  without `--yes` in non-interactive use).
- You can disable `--delete` entirely (`backup.delete_removed_files =
  false` in config, or the matching Settings toggle) if you'd rather PDBU
  only ever add/update files and never remove anything from the backup.
- `pdbu backup --dry-run` (or the GUI's "Dry Run" button) always shows
  exactly what would be added, updated, and deleted before anything runs.

## Configuring the two encrypted drives

PDBU expects two LUKS-encrypted external drives, used alternately (one
connected while the other is stored elsewhere). Configure each with:

```bash
pdbu config --edit
```

See [docs/example-config.toml](docs/example-config.toml) for a fully
annotated example of every setting, including the two drives and SSH.

For each of `[drive_a]` / `[drive_b]`, set:

- `name` — a label shown in the UI (e.g. "Backup Drive A").
- `luks_uuid` — the LUKS container's UUID (`sudo cryptsetup luksUUID
  /dev/sdXN`, or `pdbu drives` once the drive is connected).
- `filesystem_uuid` — the UUID of the filesystem *inside* the unlocked
  container (shown once you've unlocked it at least once, via `lsblk -o
  NAME,UUID` or `pdbu drives`).
- `backup_subdir` — a subdirectory under the mount point PDBU backs up
  into (default `pdbuBackups`), rather than mirroring straight to the
  drive's root. This keeps the drive free for other uses — e.g. a large
  disk could also hold `/media/trevor/PDBU-Drive-B/PermFiles/` alongside
  `/media/trevor/PDBU-Drive-B/pdbuBackups/`. Inside that subdirectory,
  PDBU further namespaces the backup by this machine's hostname (e.g.
  `pdbuBackups/fhdt/`), so the same drive can hold backups from several
  machines without them overwriting each other. `--delete` only ever
  applies inside `<backup_subdir>/<hostname>/`. Set `backup_subdir` to
  an empty string to restore the old whole-drive-mirror behaviour (no
  subdirectory, no hostname namespacing).
- `lock_after_backup` — whether to unmount and re-lock the LUKS container
  automatically after each backup.

PDBU identifies drives by these UUIDs, never by device name (`/dev/sdb1`
can and will change between connections). If a drive is connected but its
mounted filesystem UUID doesn't match what's configured, PDBU refuses to
back up to it rather than risk mirroring onto the wrong disk.

### How drive alternation works

`pdbu drives` (or the Dashboard) shows which of Drive A / Drive B is
currently connected, which one was used for the last successful backup,
and recommends the *other* one for the next backup — so you naturally keep
one drive off-site while backing up to the other. `pdbu backup` with no
destination flag uses this recommendation automatically.

### Unlocking a drive

When a configured drive is connected but locked, PDBU prompts for its
passphrase (GUI dialog, or an interactive CLI prompt) and unlocks it via
`udisksctl` — the same mechanism used by the GNOME/KDE file manager, backed
by PolicyKit, so PDBU itself never needs root. You can optionally save the
passphrase to your desktop keyring (via `secret-tool`, part of
`libsecret-tools`) so future backups don't ask again; it is never stored in
PDBU's own config or state files.

## Configuring SSH backups

In `[ssh]` (via `pdbu config --edit` or Settings → SSH):

- `enabled = true`
- `host` (or `host_alias` to reuse a `Host` entry from `~/.ssh/config`)
- `port`, `username`, `destination` (remote path)
- `identity_file` for key-based auth, or `use_password_auth = true` (this
  requires `sshpass` to be installed; SSH keys are recommended instead)
- `strict_host_key_checking` — **on by default, and PDBU never disables
  host-key checking silently.** Connect once with `ssh` interactively (or
  use "Test Connection" in Settings) to confirm and trust a new host's
  fingerprint first.
- `connect_timeout_seconds`, `bandwidth_limit_kbps`

Use Settings → SSH → **Test Connection** (or `pdbu drives`/a manual `ssh`
check) before your first backup. PDBU also checks remote free space before
backing up, and warns you if the remote filesystem can't preserve Linux
ownership, permissions, ACLs, or extended attributes (common on non-Linux
NAS filesystems like FAT/exFAT/some SMB shares).

## Performing a backup

GUI: **Back Up Now** → choose Drive A / Drive B / SSH → **Start Backup**.

CLI:

```bash
pdbu backup                 # uses the recommended (alternating) drive
pdbu backup --drive-a
pdbu backup --drive-b
pdbu backup --ssh
pdbu backup --dry-run        # preview only, changes nothing
pdbu -y backup --drive-a     # non-interactive (auto-confirm deletions etc.)
```

## Performing a restore

GUI: **Restore** → choose a source → browse/search and select files (or
leave nothing selected for a full restore) → choose a destination and
conflict mode → **Preview Restore** → **Start Restore**.

CLI:

```bash
pdbu restore --source drive-a                              # full restore, to original location
pdbu restore --source drive-a --path Documents/report.pdf  # restore just this file
pdbu restore --source ssh --destination /tmp/restore-test  # restore to an alternative location
pdbu restore --source drive-b --conflict skip               # keep existing files at the destination
```

`--conflict` modes: `overwrite`, `skip` (keep existing files), `newer`
(only restore files newer than what's there), `ask` (interactive, or
overwrite-all/skip-all for large selections in a single non-interactive
prompt), `rename` (the *existing* conflicting file at the destination is
renamed aside with a timestamp suffix so both versions survive — rsync has
no way to rename the incoming file instead, only the one being replaced).

A full, destructive restore always shows a summary and asks for
confirmation first (`--yes` to skip that in scripts).

## Dry runs

```bash
pdbu backup --dry-run
pdbu restore --source drive-a --dry-run
```

Shows files to be added/updated/deleted and the estimated data to transfer
— nothing is written. The GUI has the same via "Dry Run" / "Preview
Restore" buttons. `backup.dry_run_first` in config additionally makes a
real GUI backup always start with an automatic preview.

## Verifying a backup

```bash
pdbu verify --drive-a
pdbu verify --ssh --json
```

Compares source and destination without modifying either (it's a dry run
under the hood) and reports any differences. Run this any time you want to
confirm a backup is actually complete and accurate, independent of what
the last backup's exit code said.

## How reminders work

Set an interval in `[reminders]` or with:

```bash
pdbu schedule --interval 7d      # also accepts e.g. 24h, or a bare number of days
pdbu schedule                    # show current schedule status
```

Reminders are based on the last **successful** backup only — a failed or
cancelled attempt doesn't reset the countdown. A `systemd --user` timer
(`pdbu-reminder.timer`, installed by `install.sh`) runs `pdbu
reminder-check` periodically; that command decides whether a notification
is actually due and shows one with **Back Up Now**, **Remind Me Later**,
**Open PDBU**, and **Dismiss** actions if so. Notifications are throttled
(not repeated more than every few hours) even if the timer fires more
often, and PDBU does not need to be running for this to work.

```bash
systemctl --user status pdbu-reminder.timer
pdbu reminder-check          # run the check manually at any time
```

## Inspecting logs

Every backup/restore/verify gets its own log file plus a row in the local
history database:

```bash
pdbu history                       # table of past operations
pdbu history --json
pdbu logs                          # list recent operations and their log paths
pdbu logs <operation-id>           # show that operation's full log
pdbu logs <operation-id> --tail 50
```

The GUI's **Backup History** tab lists the same operations with a **View
Log** button. Log files live under `~/.local/state/pdbu/logs/` and are
pruned automatically after `logging.retention_days` (default 90).

## Recovering from interrupted or incomplete operations

If PDBU is killed mid-backup (crash, power loss, `kill -9`), it leaves a
lock file at `~/.local/state/pdbu/operation.lock` recording the operation
that was in progress. The **next** backup or restore detects this,
reports it, and refuses to silently continue as if nothing happened —
in the GUI as a warning dialog, on the CLI as a prompt (`--yes` to
acknowledge and proceed non-interactively). The interrupted transfer
itself is safe to just re-run: rsync mirrors are idempotent, so a rerun
finishes whatever was left, and `pdbu verify` afterwards confirms
consistency. Any log file from the interrupted run is still available via
`pdbu logs` for diagnosis before you retry.

## Diagnostics

```bash
pdbu status --json      # machine-readable dashboard summary
```

To share a diagnostic report, export your config and recent logs but
double-check first that no secrets are embedded in custom `extra_rsync_options`
or similar free-text fields — PDBU itself never writes passwords or
passphrases to config/log files, but be mindful of anything you've typed
into them yourself.

## Installation (Ubuntu)

```bash
sudo apt install rsync openssh-client cryptsetup util-linux udisks2 \
                 python3 python3-pip python3-gi gir1.2-gtk-4.0 \
                 libnotify-bin libsecret-tools
git clone <this repository> pdbu   # or unpack the source tarball
cd pdbu
./install.sh
```

`install.sh` installs the `pdbu`/`pdbu-gui` package for your user
(`pip install --user`), a `.desktop` launcher, icon, man page, bash
completion, and the `pdbu-reminder` systemd user timer, and checks for all
of the above dependencies (warning, not failing, if optional ones like the
GUI toolkit or `notify-send` are missing — the CLI still works without
them). Make sure `~/.local/bin` is on your `PATH` (the installer tells you
if it isn't).

After installing, configure PDBU:

```bash
pdbu config --edit
```

### Upgrading

Pull/unpack the new source and re-run `./install.sh` — it reinstalls the
package and refreshes desktop/systemd integration in place. Your
configuration, history, and logs are untouched.

### Uninstalling

```bash
./uninstall.sh            # removes the package, desktop/man/completion files, systemd timer
./uninstall.sh --purge    # also deletes ~/.config/pdbu, ~/.local/share/pdbu, ~/.local/state/pdbu, ~/.cache/pdbu
```

## Project layout

```
src/pdbu/
  config.py, paths.py       configuration (TOML) and XDG directory handling
  safety.py                 dangerous-path / nesting / free-space / stale-lock checks
  devices.py, luks.py       drive detection (lsblk) and LUKS unlock/mount (udisksctl)
  rsync_engine.py           rsync command construction, execution, progress/dry-run parsing
  restore.py                restore engine (conflict modes, selective restore)
  ssh_backend.py            SSH config parsing, connection testing, remote free space
  history.py, logging_setup.py   SQLite operation history and per-operation log files
  scheduler.py, notifications.py reminder scheduling and desktop notifications
  secrets_store.py          desktop keyring integration (secret-tool)
  service.py                shared orchestration layer used by BOTH front ends below
  cli.py                    command-line interface (Click)
  gui/                      GTK4 interface (PyGObject)
tests/                      pytest suite (mocked commands, temp dirs only)
packaging/                  .desktop, icon, man page, systemd units
install.sh, uninstall.sh
```

The CLI and GUI both call into `pdbu.service.PdbuService` for every
backup/restore/verify/drive operation — neither has its own copy of that
logic, so they can't drift out of sync with each other.

## Testing

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/pytest
```

Tests use `tmp_path` fixtures and (for SSH) a fake `ssh` binary injected
onto `PATH`; nothing in the suite touches your real home directory,
configuration, or attached drives.

## Troubleshooting

See [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md).

## License

MIT.
