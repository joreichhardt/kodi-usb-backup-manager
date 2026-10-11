import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "resources" / "lib"))

from storage import UsbDisk, decode_mount, human_size  # noqa: E402


class StorageTests(unittest.TestCase):
    def test_decode_mount_escapes(self):
        self.assertEqual(decode_mount("/var/media/My\\040Disk"), "/var/media/My Disk")

    def test_human_size(self):
        self.assertEqual(human_size(2 * 1024**4), "2.0 TiB")

    def test_disk_title_contains_identity(self):
        disk = UsbDisk("sdb", "/dev/sdb", "Backup Drive", "SER123", 1024**4)
        self.assertIn("Backup Drive", disk.title)
        self.assertIn("SER123", disk.title)
        self.assertIn("1.0 TiB", disk.title)


if __name__ == "__main__":
    unittest.main()
