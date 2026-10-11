#!/usr/bin/env python3
import os
import json
import subprocess
import sys
import time
from pathlib import Path

import xbmc
import xbmcgui

ADDON_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ADDON_DIR / "resources" / "lib"))

from storage import (  # noqa: E402
    CONFIG_DIR,
    PASSWORD_FILE,
    REPO_NAME,
    RESTIC,
    UUID_FILE,
    UsbDisk,
    has_repository,
    list_usb_disks,
    partition_by_uuid,
    registered_uuid,
    run,
    udev_properties,
)


HEADING = "USB Backup & Import"
BACKUP_RUNNER = ADDON_DIR / "resources" / "lib" / "backup_runner.py"
RESTORE_RUNNER = ADDON_DIR / "resources" / "lib" / "restore_runner.py"
IMPORT_RUNNER = ADDON_DIR / "resources" / "lib" / "import_runner.py"


def notify(message, icon=xbmcgui.NOTIFICATION_INFO, milliseconds=5000):
    xbmcgui.Dialog().notification(HEADING, message, icon, milliseconds)


def mounted_partition(disk: UsbDisk):
    return next((part for part in disk.partitions if part.mountpoint), None)


def wait_for_partition(disk_name, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        current = next((item for item in list_usb_disks() if item.name == disk_name), None)
        if current:
            part = mounted_partition(current)
            if part and part.uuid:
                return current, part
        time.sleep(1)
    return None, None


def validate_usb_disk(device):
    name = os.path.basename(device)
    props = udev_properties(name)
    return props.get("DEVTYPE") == "disk" and props.get("ID_BUS") == "usb"


def format_as_backup(disk: UsbDisk):
    dialog = xbmcgui.Dialog()
    message = (
        f"{disk.title}\n\nAlle Daten auf {disk.device} werden unwiderruflich gelöscht. "
        "Die interne NVMe und SATA-Laufwerke sind von der Auswahl ausgeschlossen."
    )
    if not dialog.yesno(HEADING, message, yeslabel="Weiter", nolabel="Abbrechen"):
        return
    if not dialog.yesno(
        "Letzte Bestätigung",
        f"{disk.device} jetzt vollständig löschen und als KODI_BACKUP formatieren?",
        yeslabel="FORMATIEREN",
        nolabel="Abbrechen",
    ):
        return
    if not validate_usb_disk(disk.device):
        dialog.ok(HEADING, "Sicherheitsprüfung fehlgeschlagen: Das Gerät ist kein USB-Datenträger.")
        return

    progress = xbmcgui.DialogProgress()
    progress.create(HEADING, "USB-Platte wird vorbereitet …")
    try:
        for part in disk.partitions:
            run(["udevil", "--umount", part.device], check=False, timeout=20)
            run(["umount", part.device], check=False, timeout=20)
        progress.update(20, "Partitionstabelle wird erstellt …")
        run(["parted", "-s", disk.device, "mklabel", "gpt"], timeout=30)
        run(
            ["parted", "-s", "-a", "optimal", disk.device, "mkpart", "primary", "ext4", "1MiB", "100%"],
            timeout=30,
        )
        run(["partprobe", disk.device], check=False, timeout=20)
        run(["udevadm", "settle", "--timeout=20"], check=False, timeout=25)
        part_device = f"{disk.device}{'p' if disk.device[-1].isdigit() else ''}1"
        deadline = time.monotonic() + 20
        while not os.path.exists(part_device) and time.monotonic() < deadline:
            time.sleep(1)
        if not os.path.exists(part_device):
            raise RuntimeError("Die neue Partition ist nicht erschienen.")
        progress.update(45, "ext4-Dateisystem wird erstellt …")
        run(["mkfs.ext4", "-F", "-L", "KODI_BACKUP", part_device], timeout=600)
        run(["udevadm", "settle", "--timeout=20"], check=False, timeout=25)
        run(["udevil", "--mount", part_device], check=False, timeout=30)
        progress.update(70, "UUID und Sicherungsziel werden registriert …")
        current, partition = wait_for_partition(disk.name)
        if not current or not partition:
            raise RuntimeError("Die neue Partition wurde nicht automatisch eingehängt.")
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        UUID_FILE.write_text(partition.uuid + "\n", encoding="utf-8")
        os.chmod(UUID_FILE, 0o600)
        if not PASSWORD_FILE.exists():
            PASSWORD_FILE.write_text(os.urandom(32).hex() + "\n", encoding="utf-8")
            os.chmod(PASSWORD_FILE, 0o600)
        progress.update(100, "Einrichtung abgeschlossen")
    except Exception as exc:
        xbmc.log(f"USB manager format failed: {exc}", xbmc.LOGERROR)
        dialog.ok(HEADING, f"Einrichtung fehlgeschlagen:\n{exc}")
        return
    finally:
        progress.close()

    if dialog.yesno(HEADING, "Backup-Platte wurde eingerichtet. Erstes Vollbackup jetzt starten?"):
        start_background(BACKUP_RUNNER, partition.uuid)


def start_background(script, *args):
    log_dir = CONFIG_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"{Path(script).stem}.log"
    stream = open(log_file, "a", encoding="utf-8")
    try:
        subprocess.Popen(
            ["/usr/bin/python3", str(script), *args],
            stdin=subprocess.DEVNULL,
            stdout=stream,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )
    finally:
        stream.close()


def choose_import(disk: UsbDisk):
    part = mounted_partition(disk)
    if not part:
        xbmcgui.Dialog().ok(HEADING, "Das USB-Dateisystem ist nicht eingehängt.")
        return
    categories = ["Filme", "Serien", "Musik", "Bilder", "ROMs"]
    targets = ["/storage/videos/Movies", "/storage/tvshows", "/storage/music", "/storage/pictures", "/storage/roms"]
    choice = xbmcgui.Dialog().select("Import-Ziel wählen", categories)
    if choice < 0:
        return
    source = xbmcgui.Dialog().browse(0, "Quellordner auf USB wählen", "files", "", False, False, part.mountpoint)
    if not source:
        return
    source_real = os.path.realpath(source)
    mount_real = os.path.realpath(part.mountpoint)
    if source_real != mount_real and not source_real.startswith(mount_real + os.sep):
        xbmcgui.Dialog().ok(HEADING, "Der Quellordner liegt nicht auf der gewählten USB-Platte.")
        return
    target = targets[choice]
    if xbmcgui.Dialog().yesno(HEADING, f"Medien aus\n{source}\n\nnach\n{target}\n\nimportieren?"):
        start_background(IMPORT_RUNNER, source_real, target, categories[choice])
        notify("Import wurde im Hintergrund gestartet.")


def choose_snapshot(partition):
    base = Path(partition.mountpoint) / REPO_NAME
    repo = base / "repository"
    password = PASSWORD_FILE if PASSWORD_FILE.exists() else base / ".restic-password"
    if not repo.is_dir() or not password.is_file() or not RESTIC.is_file():
        xbmcgui.Dialog().ok(HEADING, "Repository, Restic oder Wiederherstellungsschlüssel fehlt.")
        return None
    env = os.environ.copy()
    env["RESTIC_REPOSITORY"] = str(repo)
    env["RESTIC_PASSWORD_FILE"] = str(password)
    result = subprocess.run(
        [str(RESTIC), "snapshots", "--json", "--host", "kodi"],
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=60,
    )
    if result.returncode:
        xbmcgui.Dialog().ok(HEADING, f"Snapshots konnten nicht gelesen werden:\n{result.stderr[-500:]}")
        return None
    snapshots = sorted(json.loads(result.stdout), key=lambda item: item.get("time", ""), reverse=True)
    if not snapshots:
        xbmcgui.Dialog().ok(HEADING, "Auf der Platte befinden sich keine Snapshots.")
        return None
    labels = [f"{item.get('time', '')[:19].replace('T', ' ')} · {item.get('short_id', item['id'][:8])}" for item in snapshots]
    choice = xbmcgui.Dialog().select("Backup-Zeitpunkt auswählen", labels)
    return snapshots[choice]["id"] if choice >= 0 else None


def choose_restore(disk: UsbDisk, partition):
    snapshot = choose_snapshot(partition)
    if not snapshot:
        return
    options = ["Kodi-Konfiguration", "Filme und Serien", "Musik", "Bilder", "ROMs", "Gesamtes /storage"]
    keys = ["kodi", "video", "music", "pictures", "roms", "all"]
    choice = xbmcgui.Dialog().select("Wiederherstellung auswählen", options)
    if choice < 0:
        return
    key = keys[choice]
    warning = f"Aus dem neuesten Snapshot wiederherstellen: {options[choice]}?"
    if key == "all":
        warning += "\n\nVorhandene Dateien können überschrieben werden; zusätzliche neuere Dateien bleiben erhalten."
    if not xbmcgui.Dialog().yesno(HEADING, warning, yeslabel="Wiederherstellen", nolabel="Abbrechen"):
        return
    if key == "all" and not xbmcgui.Dialog().yesno(
        "Letzte Bestätigung", "Gesamte Wiederherstellung wirklich starten?", yeslabel="STARTEN", nolabel="Abbrechen"
    ):
        return
    start_background(RESTORE_RUNNER, partition.uuid, key, snapshot)
    notify("Wiederherstellung wurde im Hintergrund gestartet.")


def show_disk_menu(disk: UsbDisk):
    registered = registered_uuid()
    registered_part = partition_by_uuid(disk, registered) if registered else None
    repository_part = has_repository(disk)

    labels = []
    actions = []
    if registered_part:
        labels.append("Backup jetzt starten")
        actions.append(("backup", registered_part))
    if repository_part:
        labels.append("Aus Backup wiederherstellen")
        actions.append(("restore", repository_part))
    labels.append("Medien importieren")
    actions.append(("import", mounted_partition(disk)))
    if not registered_part:
        labels.append("Als Backup-Platte einrichten (löscht Platte)")
        actions.append(("format", None))
    labels.append("Abbrechen")
    actions.append(("cancel", None))

    choice = xbmcgui.Dialog().select(f"Externe Festplatte erkannt\n{disk.title}", labels)
    if choice < 0:
        return
    action, partition = actions[choice]
    if action == "backup":
        start_background(BACKUP_RUNNER, partition.uuid)
        notify("Backup wurde im Hintergrund gestartet.")
    elif action == "restore":
        choose_restore(disk, partition)
    elif action == "import":
        choose_import(disk)
    elif action == "format":
        format_as_backup(disk)


class UsbMonitor(xbmc.Monitor):
    def run(self):
        xbmc.log("USB Backup & Import service started", xbmc.LOGINFO)
        known = set()
        first_scan = True
        while not self.abortRequested():
            try:
                disks = {disk.name: disk for disk in list_usb_disks()}
                new_names = set(disks) if first_scan else set(disks) - known
                first_scan = False
                for name in sorted(new_names):
                    if self.waitForAbort(3):
                        return
                    refreshed = next((item for item in list_usb_disks() if item.name == name), disks[name])
                    show_disk_menu(refreshed)
                known = set(disks)
            except Exception as exc:
                xbmc.log(f"USB manager service error: {exc}", xbmc.LOGERROR)
            if self.waitForAbort(2):
                xbmc.log("USB Backup & Import service stopped", xbmc.LOGINFO)
                return


if __name__ == "__main__":
    UsbMonitor().run()
