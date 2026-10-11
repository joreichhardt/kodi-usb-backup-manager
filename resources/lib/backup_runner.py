#!/usr/bin/env python3
import fcntl
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from storage import CONFIG_DIR, PASSWORD_FILE, REPO_NAME, RESTIC, mount_for_device, run


LOCK_FILE = CONFIG_DIR / "operation.lock"
LOG_FILE = CONFIG_DIR / "backup-history.log"
STAGING_DIR = Path("/storage/.backup-snapshots")


def log(message):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    line = f"{datetime.now().isoformat(timespec='seconds')} {message}\n"
    with LOG_FILE.open("a", encoding="utf-8") as handle:
        handle.write(line)
    print(line, end="", flush=True)


def find_mount(uuid, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = run(["blkid", "-U", uuid], check=False)
        device = result.stdout.strip()
        if device:
            mountpoint = mount_for_device(device)
            if mountpoint and os.access(mountpoint, os.W_OK):
                return Path(mountpoint)
        time.sleep(1)
    raise RuntimeError("Backup-Platte ist nicht schreibbar eingehängt")


def snapshot_kodi_profile():
    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    temporary = STAGING_DIR / "kodi-profile.tar.tmp"
    final = STAGING_DIR / "kodi-profile.tar"
    subprocess.run(["systemctl", "stop", "kodi"], check=True, timeout=60)
    try:
        temporary.unlink(missing_ok=True)
        subprocess.run(
            ["tar", "-cf", str(temporary), "--exclude=.kodi/temp", "-C", "/storage", ".kodi"],
            check=True,
            timeout=900,
        )
        os.replace(temporary, final)
    finally:
        subprocess.run(["systemctl", "start", "kodi"], check=False, timeout=60)


def restic(repo, *args, timeout=None):
    env = os.environ.copy()
    env["RESTIC_REPOSITORY"] = str(repo)
    env["RESTIC_PASSWORD_FILE"] = str(PASSWORD_FILE)
    return subprocess.run([str(RESTIC), *args], env=env, check=True, timeout=timeout)


def write_recovery_kit(base):
    recovery_password = base / ".restic-password"
    shutil.copyfile(PASSWORD_FILE, recovery_password)
    try:
        os.chmod(recovery_password, 0o600)
    except OSError:
        pass
    instructions = base / "WIEDERHERSTELLUNG.txt"
    instructions.write_text(
        "Kodi-Server-Backup\n"
        "==================\n\n"
        "Das verschlüsselte Restic-Repository liegt im Unterordner repository.\n"
        "Das Kennwort steht in .restic-password auf dieser Platte.\n"
        "Der Kodi USB Backup & Import Service kann Snapshots über das TV-Menü\n"
        "wiederherstellen. Bewahre diese Platte sicher auf.\n",
        encoding="utf-8",
    )


def main(uuid):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with LOCK_FILE.open("w", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log("Abbruch: Eine Backup-, Restore- oder Import-Operation läuft bereits")
            return 2

        mountpoint = find_mount(uuid)
        base = mountpoint / REPO_NAME
        repo = base / "repository"
        base.mkdir(parents=True, exist_ok=True)
        if not PASSWORD_FILE.exists():
            PASSWORD_FILE.write_text(os.urandom(32).hex() + "\n", encoding="utf-8")
            os.chmod(PASSWORD_FILE, 0o600)
        write_recovery_kit(base)
        if not (repo / "config").exists():
            repo.mkdir(parents=True, exist_ok=True)
            restic(repo, "init", timeout=300)
            log(f"Repository initialisiert: {repo}")

        log("Kodi-Profil wird konsistent zwischengespeichert")
        snapshot_kodi_profile()
        log("Vollständiges inkrementelles Backup gestartet")
        restic(
            repo,
            "backup",
            "/storage",
            "/flash",
            "--host",
            "kodi",
            "--tag",
            "usb-auto",
            "--exclude",
            "/storage/.kodi/temp",
            "--exclude",
            "/storage/.cache/kodi-usb-backup",
            "--exclude",
            "/storage/lost+found",
        )
        restic(
            repo,
            "forget",
            "--host",
            "kodi",
            "--keep-last",
            "3",
            "--keep-daily",
            "7",
            "--keep-weekly",
            "8",
            "--keep-monthly",
            "12",
            timeout=1800,
        )
        subprocess.run(["sync"], check=False)
        log("Backup erfolgreich abgeschlossen")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: backup_runner.py UUID")
    try:
        raise SystemExit(main(sys.argv[1]))
    except Exception as exc:
        log(f"FEHLER: {exc}")
        raise
