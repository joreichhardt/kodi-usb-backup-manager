#!/usr/bin/env python3
import fcntl
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

from storage import CONFIG_DIR, PASSWORD_FILE, REPO_NAME, RESTIC, mount_for_device, run

LOCK_FILE = CONFIG_DIR / "operation.lock"
LOG_FILE = CONFIG_DIR / "restore-history.log"

INCLUDES = {
    "video": ["/storage/videos", "/storage/tvshows"],
    "music": ["/storage/music"],
    "pictures": ["/storage/pictures"],
    "roms": ["/storage/roms", "/storage/emulators", "/storage/.kodi/saves"],
    "all": ["/storage"],
}


def log(message):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    line = f"{datetime.now().isoformat(timespec='seconds')} {message}\n"
    with LOG_FILE.open("a", encoding="utf-8") as handle:
        handle.write(line)
    print(line, end="", flush=True)


def find_mount(uuid, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        device = run(["blkid", "-U", uuid], check=False).stdout.strip()
        if device:
            mountpoint = mount_for_device(device)
            if mountpoint:
                return Path(mountpoint)
        time.sleep(1)
    raise RuntimeError("Backup-Platte ist nicht eingehängt")


def password_file(base):
    return PASSWORD_FILE if PASSWORD_FILE.exists() else base / ".restic-password"


def restic(repo, password, *args, timeout=None):
    env = os.environ.copy()
    env["RESTIC_REPOSITORY"] = str(repo)
    env["RESTIC_PASSWORD_FILE"] = str(password)
    subprocess.run([str(RESTIC), *args], env=env, check=True, timeout=timeout)


def photorec_running():
    for cmdline in Path("/proc").glob("[0-9]*/cmdline"):
        try:
            if b"photorec" in cmdline.read_bytes():
                return True
        except OSError:
            continue
    return False


def restore_kodi(repo, password, snapshot):
    with tempfile.TemporaryDirectory(prefix="kodi-restore-", dir="/storage") as target:
        restic(
            repo,
            password,
            "restore",
            snapshot,
            "--target",
            target,
            "--include",
            "/storage/.backup-snapshots/kodi-profile.tar",
        )
        archive = Path(target) / "storage" / ".backup-snapshots" / "kodi-profile.tar"
        if not archive.is_file():
            raise RuntimeError("Kodi-Profilarchiv fehlt im gewählten Snapshot")
        subprocess.run(["systemctl", "stop", "kodi"], check=True, timeout=60)
        try:
            subprocess.run(["tar", "-xf", str(archive), "-C", "/storage"], check=True, timeout=900)
        finally:
            subprocess.run(["systemctl", "start", "kodi"], check=False, timeout=60)


def main(uuid, kind, snapshot):
    if kind not in {*INCLUDES, "kodi"}:
        raise ValueError("Unbekannter Wiederherstellungstyp")
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with LOCK_FILE.open("w", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log("Abbruch: Eine andere Operation läuft bereits")
            return 2
        mountpoint = find_mount(uuid)
        base = mountpoint / REPO_NAME
        repo = base / "repository"
        password = password_file(base)
        if not (repo / "config").is_file() or not password.is_file():
            raise RuntimeError("Repository oder Wiederherstellungsschlüssel fehlt")
        if kind in {"all", "pictures"} and photorec_running():
            raise RuntimeError("PhotoRec läuft noch; Bilder/Gesamtbestand jetzt nicht überschreiben")
        log(f"Wiederherstellung gestartet: snapshot={snapshot} bereich={kind}")
        if kind == "kodi":
            restore_kodi(repo, password, snapshot)
        else:
            args = ["restore", snapshot, "--target", "/"]
            for path in INCLUDES[kind]:
                args.extend(["--include", path])
            restic(repo, password, *args)
        subprocess.run(["sync"], check=False)
        log("Wiederherstellung erfolgreich abgeschlossen")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 4:
        raise SystemExit("usage: restore_runner.py UUID TYPE SNAPSHOT")
    try:
        raise SystemExit(main(sys.argv[1], sys.argv[2], sys.argv[3]))
    except Exception as exc:
        log(f"FEHLER: {exc}")
        raise
