#!/usr/bin/env python3
import fcntl
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from storage import CONFIG_DIR


LOCK_FILE = CONFIG_DIR / "operation.lock"
LOG_FILE = CONFIG_DIR / "import-history.log"

EXTENSIONS = {
    "Filme": {".avi", ".iso", ".m2ts", ".m4v", ".mkv", ".mov", ".mp4", ".mpeg", ".mpg", ".ts", ".wmv"},
    "Serien": {".avi", ".iso", ".m2ts", ".m4v", ".mkv", ".mov", ".mp4", ".mpeg", ".mpg", ".ts", ".wmv"},
    "Musik": {".aac", ".alac", ".flac", ".m4a", ".mp3", ".ogg", ".opus", ".wav", ".wma"},
    "Bilder": {".bmp", ".dng", ".gif", ".heic", ".heif", ".jpeg", ".jpg", ".mov", ".png", ".tif", ".tiff", ".webp"},
    "ROMs": {".7z", ".a26", ".a78", ".atr", ".bin", ".gb", ".gba", ".gbc", ".gen", ".md", ".nes", ".sfc", ".smc", ".xex", ".zip"},
}


def log(message):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    line = f"{datetime.now().isoformat(timespec='seconds')} {message}\n"
    with LOG_FILE.open("a", encoding="utf-8") as handle:
        handle.write(line)
    print(line, end="", flush=True)


def main(source, target, category):
    if category not in EXTENSIONS:
        raise ValueError("Unbekannte Importkategorie")
    source_path = Path(source).resolve()
    target_path = Path(target).resolve()
    if not source_path.is_dir() or not str(source_path).startswith("/var/media/"):
        raise ValueError("Quelle ist kein eingehängter USB-Ordner")
    if not str(target_path).startswith("/storage/"):
        raise ValueError("Importziel liegt nicht unter /storage")

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    copied = 0
    skipped = 0
    with LOCK_FILE.open("w", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log("Abbruch: Eine andere Operation läuft bereits")
            return 2
        for root, dirs, files in os.walk(source_path, followlinks=False):
            dirs[:] = [name for name in dirs if not name.startswith(".") and not (Path(root) / name).is_symlink()]
            relative = Path(root).relative_to(source_path)
            destination_dir = target_path / relative
            destination_dir.mkdir(parents=True, exist_ok=True)
            for name in files:
                source_file = Path(root) / name
                if name.startswith(".") or source_file.is_symlink() or source_file.suffix.lower() not in EXTENSIONS[category]:
                    skipped += 1
                    continue
                destination = destination_dir / name
                shutil.copy2(source_file, destination)
                copied += 1
        log(f"Import abgeschlossen: kategorie={category} kopiert={copied} übersprungen={skipped}")
        action = "UpdateLibrary(music)" if category == "Musik" else "UpdateLibrary(video)"
        if category in {"Filme", "Serien", "Musik"} and shutil.which("kodi-send"):
            subprocess.run(["kodi-send", f"--action={action}"], check=False)
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 4:
        raise SystemExit("usage: import_runner.py SOURCE TARGET CATEGORY")
    try:
        raise SystemExit(main(sys.argv[1], sys.argv[2], sys.argv[3]))
    except Exception as exc:
        log(f"FEHLER: {exc}")
        raise
