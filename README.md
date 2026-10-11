# Kodi USB Backup & Import

A Kodi service add-on for LibreELEC that turns attaching an external USB disk
into a TV-friendly workflow. The on-screen menu can prepare a dedicated backup
disk, create incremental snapshots, restore selected data, or import media.

The interface follows Kodi's language: German Kodi installations use German;
all other languages use English. The project targets Kodi 21 on LibreELEC
x86_64.

## Features

- Detects only whole disks reported by udev as `ID_BUS=usb`.
- Shows model, serial number, and capacity before any action.
- Uses German when Kodi is set to German and English for every other Kodi language.
- Requires two confirmations before formatting.
- Creates GPT plus one ext4 partition labeled `KODI_BACKUP`.
- Registers one backup disk by filesystem UUID.
- Backs up `/storage` and `/flash` with encrypted, deduplicated Restic snapshots.
- Retains the last 3, 7 daily, 8 weekly, and 12 monthly snapshots.
- Stores a recovery key and instructions on the backup disk.
- Restores Kodi configuration, videos, music, pictures, ROMs, or all of
  `/storage` from a selected snapshot.
- Imports only allow-listed media and ROM extensions; symlinks, hidden files,
  and executable formats are ignored.

## Safety model

Formatting is offered only for a whole USB disk. SATA and NVMe devices are not
eligible, which prevents the LibreELEC system disk and internal recovery source
from appearing in the format menu. The selected USB disk is still erased in
full, so the model, serial number, and size must be checked carefully.

The first full backup may take many hours over USB 2. Later snapshots transfer
only new or changed data. Kodi is stopped briefly while a consistent profile
archive is made, then restarted before the large media backup continues.

A full restore merges the snapshot into `/storage`: matching files may be
overwritten, while additional newer files are not deleted. Picture and full
restores are refused while PhotoRec is active.

## Install on LibreELEC

Copy or extract this repository onto the LibreELEC host, then run:

```sh
chmod +x install.sh
./install.sh
```

The installer downloads the official Restic 0.19.1 Linux amd64 binary and
verifies its SHA-256 checksum before installation. It then restarts Kodi so the
service add-on is discovered. If Kodi leaves the add-on disabled, enable
**USB Backup & Import** under **Add-ons → My add-ons → Services**.

## Use

Attach an external USB disk. Kodi opens a menu with the actions available for
that disk:

1. **Als Backup-Platte einrichten** formats and registers a new disk.
2. **Backup jetzt starten** writes a new incremental snapshot.
3. **Aus Backup wiederherstellen** selects a snapshot and restore scope.
4. **Medien importieren** selects a source folder and target media category.

Logs are stored in `/storage/.config/kodi-usb-backup/`.

## Disaster recovery

The backup disk contains `Kodi-Server-Backup/WIEDERHERSTELLUNG.txt`, the Restic
repository, and `.restic-password`. This makes the repository recoverable even
if the internal NVMe and its saved configuration are lost. Treat the backup
disk as sensitive: the recovery password is deliberately stored beside the
repository so a replacement Kodi installation can restore it without relying
on the failed server.

## Development

Syntax-check the Python files with:

```sh
python3 -m compileall -q service.py resources/lib
python3 -m unittest discover -s tests -v
ruff check service.py resources/lib tests
```

Install Ruff once with `uv tool install ruff` if it is not already available.

The add-on intentionally uses only Python's standard library plus Kodi's
`xbmc` and `xbmcgui` modules.

## Third-party software

Restic is downloaded at install time and is not included in this repository.
See [restic/restic](https://github.com/restic/restic) and the
[Restic documentation](https://restic.readthedocs.io/).

## License

MIT
