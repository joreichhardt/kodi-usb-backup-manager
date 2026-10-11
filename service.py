#!/usr/bin/env python3
# ruff: noqa: I001
import json
import os
import subprocess
import time
from pathlib import Path

import xbmc  # type: ignore[import-not-found]
import xbmcgui  # type: ignore[import-not-found]

from resources.lib.storage import (
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
from resources.lib.ui_strings import text  # type: ignore[import-not-found]


ADDON_DIR = Path(__file__).resolve().parent
BACKUP_RUNNER = ADDON_DIR / "resources" / "lib" / "backup_runner.py"
RESTORE_RUNNER = ADDON_DIR / "resources" / "lib" / "restore_runner.py"
IMPORT_RUNNER = ADDON_DIR / "resources" / "lib" / "import_runner.py"


def ui_text(key, **values):
    try:
        language = xbmc.getLanguage(xbmc.ISO_639_1)
    except (AttributeError, TypeError):
        language = xbmc.getLanguage()
    return text(language, key, **values)


def notify(message, icon=xbmcgui.NOTIFICATION_INFO, milliseconds=5000):
    xbmcgui.Dialog().notification(ui_text("heading"), message, icon, milliseconds)


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
    if not dialog.yesno(
        ui_text("heading"),
        ui_text("format_warning", disk_title=disk.title, device=disk.device),
        yeslabel=ui_text("continue"),
        nolabel=ui_text("cancel"),
    ):
        return
    if not dialog.yesno(
        ui_text("last_confirmation"),
        ui_text("format_question", device=disk.device),
        yeslabel=ui_text("format"),
        nolabel=ui_text("cancel"),
    ):
        return
    if not validate_usb_disk(disk.device):
        dialog.ok(ui_text("heading"), ui_text("usb_check_failed"))
        return

    progress = xbmcgui.DialogProgress()
    progress.create(ui_text("heading"), ui_text("preparing_disk"))
    try:
        for part in disk.partitions:
            run(["udevil", "--umount", part.device], check=False, timeout=20)
            run(["umount", part.device], check=False, timeout=20)
        progress.update(20, ui_text("creating_partition_table"))
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
            raise RuntimeError(ui_text("new_partition_missing"))
        progress.update(45, ui_text("creating_filesystem"))
        run(["mkfs.ext4", "-F", "-L", "KODI_BACKUP", part_device], timeout=600)
        run(["udevadm", "settle", "--timeout=20"], check=False, timeout=25)
        run(["udevil", "--mount", part_device], check=False, timeout=30)
        progress.update(70, ui_text("registering_backup"))
        current, partition = wait_for_partition(disk.name)
        if not current or not partition:
            raise RuntimeError(ui_text("new_partition_not_mounted"))
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        UUID_FILE.write_text(partition.uuid + "\n", encoding="utf-8")
        os.chmod(UUID_FILE, 0o600)
        if not PASSWORD_FILE.exists():
            PASSWORD_FILE.write_text(os.urandom(32).hex() + "\n", encoding="utf-8")
            os.chmod(PASSWORD_FILE, 0o600)
        progress.update(100, ui_text("setup_complete"))
    except Exception as exc:  # noqa: BLE001 -- format failures must be reported in Kodi's UI
        xbmc.log(f"USB manager format failed: {exc}", xbmc.LOGERROR)
        dialog.ok(ui_text("heading"), ui_text("setup_failed", error=exc))
        return
    finally:
        progress.close()

    if dialog.yesno(ui_text("heading"), ui_text("start_first_backup")):
        start_background(BACKUP_RUNNER, partition.uuid)


def start_background(script, *args):
    log_dir = CONFIG_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"{Path(script).stem}.log"
    try:
        with log_file.open("a", encoding="utf-8") as stream:
            subprocess.Popen(
                ["/usr/bin/python3", str(script), *args],
                stdin=subprocess.DEVNULL,
                stdout=stream,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                close_fds=True,
            )
    except OSError as exc:
        xbmc.log(f"USB manager could not start job: {exc}", xbmc.LOGERROR)
        notify(ui_text("job_start_failed"))


def choose_import(disk: UsbDisk):
    part = mounted_partition(disk)
    if not part or not part.mountpoint:
        xbmcgui.Dialog().ok(ui_text("heading"), ui_text("filesystem_not_mounted"))
        return
    categories = ["movies", "tvshows", "music", "pictures", "roms"]
    targets = ["/storage/videos/Movies", "/storage/tvshows", "/storage/music", "/storage/pictures", "/storage/roms"]
    labels = [ui_text(f"category_{category}") for category in categories]
    choice = xbmcgui.Dialog().select(ui_text("select_import_target"), labels)
    if choice < 0:
        return
    source = xbmcgui.Dialog().browse(0, ui_text("select_usb_folder"), "files", "", False, False, part.mountpoint)
    if not source:
        return
    source_real = os.path.realpath(source)
    mount_real = os.path.realpath(part.mountpoint)
    if source_real != mount_real and not source_real.startswith(mount_real + os.sep):
        xbmcgui.Dialog().ok(ui_text("heading"), ui_text("source_not_on_disk"))
        return
    target = targets[choice]
    if xbmcgui.Dialog().yesno(ui_text("heading"), ui_text("import_question", source=source, target=target)):
        start_background(IMPORT_RUNNER, source_real, target, categories[choice])
        notify(ui_text("import_started"))


def choose_snapshot(partition):
    base = Path(partition.mountpoint) / REPO_NAME
    repo = base / "repository"
    recovery_key_file = PASSWORD_FILE if PASSWORD_FILE.exists() else base / ".restic-password"
    if not repo.is_dir() or not recovery_key_file.is_file() or not RESTIC.is_file():
        xbmcgui.Dialog().ok(ui_text("heading"), ui_text("repository_missing"))
        return None
    env = os.environ.copy()
    env["RESTIC_REPOSITORY"] = str(repo)
    env["RESTIC_PASSWORD_FILE"] = str(recovery_key_file)
    result = subprocess.run(
        [str(RESTIC), "snapshots", "--json", "--host", "kodi"],
        env=env,
        check=False,
        text=True,
        capture_output=True,
        timeout=60,
    )
    if result.returncode:
        xbmcgui.Dialog().ok(ui_text("heading"), ui_text("snapshots_failed", error=result.stderr[-500:]))
        return None
    try:
        snapshots = sorted(json.loads(result.stdout), key=lambda item: item.get("time", ""), reverse=True)
    except json.JSONDecodeError:
        xbmcgui.Dialog().ok(ui_text("heading"), ui_text("snapshots_invalid"))
        return None
    if not snapshots:
        xbmcgui.Dialog().ok(ui_text("heading"), ui_text("no_snapshots"))
        return None
    labels = [f"{item.get('time', '')[:19].replace('T', ' ')} · {item.get('short_id', item['id'][:8])}" for item in snapshots]
    choice = xbmcgui.Dialog().select(ui_text("select_snapshot"), labels)
    return snapshots[choice]["id"] if choice >= 0 else None


def choose_restore(disk: UsbDisk, partition):
    snapshot = choose_snapshot(partition)
    if not snapshot:
        return
    keys = ["kodi", "video", "music", "pictures", "roms", "all"]
    options = [ui_text(f"scope_{key}") for key in keys]
    choice = xbmcgui.Dialog().select(ui_text("select_restore"), options)
    if choice < 0:
        return
    key = keys[choice]
    warning = ui_text("restore_question", scope=options[choice])
    if key == "all":
        warning += ui_text("restore_all_warning")
    if not xbmcgui.Dialog().yesno(
        ui_text("heading"), warning, yeslabel=ui_text("restore"), nolabel=ui_text("cancel")
    ):
        return
    if key == "all" and not xbmcgui.Dialog().yesno(
        ui_text("last_confirmation"), ui_text("start_full_restore"), yeslabel=ui_text("start"), nolabel=ui_text("cancel")
    ):
        return
    start_background(RESTORE_RUNNER, partition.uuid, key, snapshot)
    notify(ui_text("restore_started"))


def show_disk_menu(disk: UsbDisk):
    registered = registered_uuid()
    registered_part = partition_by_uuid(disk, registered) if registered else None
    repository_part = has_repository(disk)

    labels = []
    actions = []
    if registered_part:
        labels.append(ui_text("start_backup"))
        actions.append(("backup", registered_part))
    if repository_part:
        labels.append(ui_text("restore_backup"))
        actions.append(("restore", repository_part))
    labels.append(ui_text("import_media"))
    actions.append(("import", mounted_partition(disk)))
    if not registered_part:
        labels.append(ui_text("setup_backup_disk"))
        actions.append(("format", None))
    labels.append(ui_text("cancel"))
    actions.append(("cancel", None))

    choice = xbmcgui.Dialog().select(ui_text("disk_detected", disk_title=disk.title), labels)
    if choice < 0:
        return
    action, partition = actions[choice]
    if action == "backup":
        start_background(BACKUP_RUNNER, partition.uuid)
        notify(ui_text("backup_started"))
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
            except Exception as exc:  # noqa: BLE001 -- keep the long-running service alive after a device error
                xbmc.log(f"USB manager service error: {exc}", xbmc.LOGERROR)
            if self.waitForAbort(2):
                xbmc.log("USB Backup & Import service stopped", xbmc.LOGINFO)
                return


if __name__ == "__main__":
    UsbMonitor().run()
