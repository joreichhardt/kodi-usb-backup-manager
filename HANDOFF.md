# Project handoff

## Purpose

`service.kodi.usbmanager` is a Kodi service add-on for LibreELEC x86_64. It
detects newly attached USB disks and presents a German, TV-friendly menu for:

- registering and formatting one dedicated backup disk;
- creating an encrypted, incremental Restic backup;
- restoring selected data from an existing backup;
- importing allow-listed media and ROM files into `/storage`.

The repository is public. Never commit server passwords, Kodi credentials,
private IP addresses, Restic passwords, recovery files, or real user media.

## Repository and deployment state

- Public repository: <https://github.com/joreichhardt/kodi-usb-backup-manager>
- Default branch: `main`
- Add-on ID: `service.kodi.usbmanager`
- Current add-on version: `1.0.1`
- Target: Kodi 21 on LibreELEC x86_64
- Runtime dependencies: Python 3, Kodi `xbmc`/`xbmcgui`, udev, udevil,
  parted, e2fsprogs, systemd, curl, bzip2, and Restic
- Restic version installed by `install.sh`: `0.19.1`, checksum pinned

The add-on was installed on the original target and its service-start message
was observed in the Kodi log. Restic was installed successfully. No suitable
external USB backup disk was available at handoff time, so formatting, a real
full backup, and a restore from physical backup media are not yet end-to-end
verified.

## Architecture

| File | Responsibility |
| --- | --- |
| `service.py` | Kodi service loop, USB hotplug detection, dialogs, format flow, and background job dispatch |
| `resources/lib/storage.py` | USB discovery through sysfs/udev, mount lookup, data models, and shared paths |
| `resources/lib/backup_runner.py` | operation lock, Kodi profile snapshot, Restic init/backup/retention, and recovery kit |
| `resources/lib/restore_runner.py` | snapshot restore by scope, Kodi profile restore, and PhotoRec guard |
| `resources/lib/import_runner.py` | allow-listed file import and Kodi library refresh |
| `install.sh` | LibreELEC installation and verified Restic download |
| `tests/test_storage.py` | unit tests for platform-independent storage helpers |

The foreground service polls `/sys/class/block`. A device is eligible only if
udev reports both `DEVTYPE=disk` and `ID_BUS=usb`. Partition operations and
long-running jobs execute outside Kodi's UI loop. Backup, restore, and import
share an advisory lock at:

```text
/storage/.config/kodi-usb-backup/operation.lock
```

## Persistent paths

On the Kodi server:

```text
/storage/.kodi/addons/service.kodi.usbmanager/   installed add-on
/storage/.config/kodi-usb-backup/uuid           registered filesystem UUID
/storage/.config/kodi-usb-backup/password       local Restic password
/storage/.config/kodi-usb-backup/logs/           captured runner output
/storage/.config/kodi-usb-backup/*-history.log   operation histories
/storage/.backup-snapshots/kodi-profile.tar     consistent Kodi profile archive
```

On the backup disk:

```text
Kodi-Server-Backup/repository/                   Restic repository
Kodi-Server-Backup/.restic-password              disaster-recovery password
Kodi-Server-Backup/WIEDERHERSTELLUNG.txt          recovery note
```

The password is intentionally copied beside the repository so the backup can
be restored after total loss of the Kodi server. This favors recoverability
over protection against theft of the complete backup disk. The disk must be
stored securely.

## User flows

### Attach a disk

On service start, and whenever a new whole USB disk appears, Kodi waits briefly
for mounting and opens a menu. Model, serial number, and capacity are shown.
Internal SATA and NVMe disks are excluded by the udev bus check.

### Register a backup disk

The format action requires two confirmations and repeats the USB-device check
immediately before mutation. It unmounts existing partitions, creates a GPT
with one ext4 partition, labels it `KODI_BACKUP`, mounts it, and saves its
filesystem UUID. Formatting destroys all data on the selected USB disk.

Only one UUID is registered at a time. Registering another disk replaces the
value in the UUID file; it does not erase an old Restic repository unless that
old disk is explicitly selected and formatted.

### Backup

Only the registered filesystem UUID receives the **Backup jetzt starten**
action. Before the large backup, Kodi is stopped briefly and `.kodi` is stored
in a tar archive, then Kodi starts again. Restic backs up `/storage` and
`/flash`, excluding transient Kodi data and `lost+found`.

Retention is currently:

- last 3 snapshots;
- 7 daily snapshots;
- 8 weekly snapshots;
- 12 monthly snapshots.

`restic forget` runs without `--prune`. Unreferenced data therefore remains in
the repository until an explicit prune is implemented or run administratively.

### Restore

Kodi lists snapshots from any attached disk containing a recognizable
repository. The available restore scopes are Kodi configuration, videos and TV
shows, music, pictures, ROMs, and all of `/storage`.

A full restore merges into `/storage`: matching paths can be overwritten, but
files created after the snapshot are not deleted. Kodi configuration is
restored from the consistent tar archive while Kodi is stopped. Picture and
full restores abort if a running PhotoRec process is detected.

### Import

The user chooses a directory on the mounted USB partition and one target:
movies, TV shows, music, pictures, or ROMs. The importer preserves the relative
directory layout, ignores hidden entries and symlinks, and copies only the
extensions listed in `resources/lib/import_runner.py`. The picture list
includes HEIC, HEIF, DNG, and MOV. Video and music imports request a Kodi
library update when `kodi-send` is available.

## Installation and update

On a LibreELEC x86_64 host, transfer or clone the repository and run as root:

```sh
chmod +x install.sh
./install.sh
```

The installer copies the add-on into Kodi's add-on directory, downloads the
pinned official Restic build if absent, verifies its SHA-256 checksum, and
restarts Kodi. Enable **USB Backup & Import** under **Add-ons → My add-ons →
Services** if Kodi does not enable it automatically.

For development deployment, replace the installed source files, preserve the
executable bits on the runner scripts, then restart Kodi. Do not copy local
credentials or recovery artifacts into the repository.

## Verification

The following checks passed before handoff:

```sh
python3 -m compileall -q service.py resources/lib
python3 -m unittest discover -s tests -v
shellcheck install.sh
```

The unit suite contains three storage-helper tests. There is no CI workflow at
present; the GitHub token used during initial publication did not have the
`workflow` permission, so the attempted workflow was removed rather than left
half-configured.

## Required hardware acceptance test

Use a disposable USB disk with no needed data. Do not perform this test against
the internal system disk or recovery source.

1. Attach the disk and verify the Kodi menu names the expected model, serial,
   and capacity.
2. Choose the format action, read both confirmations, and verify one GPT/ext4
   partition labeled `KODI_BACKUP` is created.
3. Confirm its UUID matches `/storage/.config/kodi-usb-backup/uuid`.
4. Run the first backup and watch `backup-history.log` until it reports success.
5. Run `restic check` against the repository.
6. Add a small test file in each restore scope and create a second snapshot.
7. Restore each scope independently and verify content and permissions.
8. Reboot LibreELEC, reconnect the same disk, and verify it is still recognized
   by UUID.
9. Import a representative media set, including one disallowed executable and
   one symlink; verify only allow-listed regular files are copied.
10. Confirm the Kodi video/music scans see the imported content.

Keep a second copy of any irreplaceable data while testing. A successful backup
log alone is not sufficient; at least one restore must be verified.

## Known limitations and next work

1. A registered disk opens the menu; backup is not yet started automatically.
   If true unattended behavior is desired, add a setting with a safe default
   and an on-screen cancellation window.
2. Long-running jobs only show start notifications. Completion and failure are
   recorded in logs but are not pushed back to Kodi's UI.
3. There is no free-space estimate before formatting, backup, restore, or
   import.
4. Retention does not reclaim repository space because prune is not run.
5. Import overwrites same-named destination files through `shutil.copy2` and
   has no conflict-selection UI.
6. Restore scope selection is path-based and has not been tested against every
   possible legacy LibreELEC directory layout.
7. Restic is pinned to Linux amd64; ARM LibreELEC devices are unsupported.
8. The service uses polling rather than an event-driven udev listener.
9. Coverage is minimal and Kodi UI behavior is not automated.

Recommended priority is the hardware acceptance test, followed by reliable
completion notifications and an opt-in unattended-backup mode.

## Troubleshooting

Start with the Kodi log and these files:

```text
/storage/.config/kodi-usb-backup/logs/backup_runner.log
/storage/.config/kodi-usb-backup/logs/restore_runner.log
/storage/.config/kodi-usb-backup/logs/import_runner.log
/storage/.config/kodi-usb-backup/backup-history.log
/storage/.config/kodi-usb-backup/restore-history.log
/storage/.config/kodi-usb-backup/import-history.log
```

Useful read-only checks on LibreELEC:

```sh
systemctl status kodi --no-pager
udevadm info --query=property --name=/dev/sdX
blkid
mount
tail -n 100 /storage/.kodi/temp/kodi.log
```

For repository diagnostics, set `RESTIC_REPOSITORY` and
`RESTIC_PASSWORD_FILE` to the paths on the mounted backup disk, then use
`restic snapshots` and `restic check`. Avoid repair commands until the backup
disk and repository paths have been positively identified.

## Release checklist

1. Update the version in `addon.xml` for code changes.
2. Run syntax checks, unit tests, and ShellCheck.
3. Review the diff for secrets and machine-specific details.
4. Test installation on LibreELEC x86_64.
5. Complete at least a backup and targeted restore on disposable media.
6. Commit, tag the version, and publish release notes describing migrations or
   destructive behavior changes.

