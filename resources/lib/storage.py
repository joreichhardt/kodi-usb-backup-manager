import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional


CONFIG_DIR = Path("/storage/.config/kodi-usb-backup")
UUID_FILE = CONFIG_DIR / "uuid"
PASSWORD_FILE = CONFIG_DIR / "password"
RESTIC = Path("/storage/.kodi/addons/service.kodi.usbmanager/resources/bin/restic")
REPO_NAME = "Kodi-Server-Backup"


def run(args, check=True, timeout=30):
    return subprocess.run(
        args,
        check=check,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
    )


def udev_properties(name: str) -> Dict[str, str]:
    result = run(
        ["udevadm", "info", "--query=property", f"--path=/sys/class/block/{name}"],
        check=False,
    )
    props = {}
    for line in result.stdout.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            props[key] = value
    return props


def human_size(byte_count: int) -> str:
    value = float(byte_count)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TiB"


def decode_mount(value: str) -> str:
    return re.sub(
        r"\\([0-7]{3})", lambda match: chr(int(match.group(1), 8)), value
    )


def mount_for_device(device: str) -> Optional[str]:
    try:
        with open("/proc/mounts", encoding="utf-8") as mounts:
            for line in mounts:
                fields = line.split()
                if len(fields) >= 2 and decode_mount(fields[0]) == device:
                    return decode_mount(fields[1])
    except OSError:
        pass
    return None


@dataclass
class Partition:
    name: str
    device: str
    uuid: str = ""
    label: str = ""
    fstype: str = ""
    mountpoint: Optional[str] = None


@dataclass
class UsbDisk:
    name: str
    device: str
    model: str
    serial: str
    size_bytes: int
    partitions: List[Partition] = field(default_factory=list)

    @property
    def title(self) -> str:
        identity = self.model or self.name
        return f"{identity} · {human_size(self.size_bytes)} · {self.serial or 'ohne Seriennummer'}"


def list_usb_disks() -> List[UsbDisk]:
    disks = []
    sys_block = Path("/sys/class/block")
    if not sys_block.exists():
        return disks

    all_names = [entry.name for entry in sys_block.iterdir()]
    for name in sorted(all_names):
        if (sys_block / name / "partition").exists():
            continue
        props = udev_properties(name)
        if props.get("DEVTYPE") != "disk" or props.get("ID_BUS") != "usb":
            continue
        try:
            sectors = int((sys_block / name / "size").read_text().strip())
        except (OSError, ValueError):
            sectors = 0
        disk = UsbDisk(
            name=name,
            device=f"/dev/{name}",
            model=props.get("ID_MODEL", "").replace("_", " ").strip(),
            serial=props.get("ID_SERIAL_SHORT", "").strip(),
            size_bytes=sectors * 512,
        )
        for child_name in all_names:
            child_path = sys_block / child_name
            if not (child_path / "partition").exists():
                continue
            try:
                parent = child_path.resolve().parent.name
            except OSError:
                continue
            if parent != name:
                continue
            child_props = udev_properties(child_name)
            device = f"/dev/{child_name}"
            disk.partitions.append(
                Partition(
                    name=child_name,
                    device=device,
                    uuid=child_props.get("ID_FS_UUID", ""),
                    label=child_props.get("ID_FS_LABEL", ""),
                    fstype=child_props.get("ID_FS_TYPE", ""),
                    mountpoint=mount_for_device(device),
                )
            )
        disks.append(disk)
    return disks


def registered_uuid() -> str:
    try:
        return UUID_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def partition_by_uuid(disk: UsbDisk, uuid: str) -> Optional[Partition]:
    return next((part for part in disk.partitions if part.uuid == uuid), None)


def repository_path(partition: Partition) -> Optional[Path]:
    if not partition.mountpoint:
        return None
    return Path(partition.mountpoint) / REPO_NAME / "repository"


def has_repository(disk: UsbDisk) -> Optional[Partition]:
    for part in disk.partitions:
        repo = repository_path(part)
        if repo and (repo / "config").is_file():
            return part
    return None
