import unittest

from resources.lib.ui_strings import STRINGS, is_german, text  # pyright: ignore[reportMissingImports]


class UiStringsTests(unittest.TestCase):
    def test_german_locale_uses_german_strings(self):
        self.assertTrue(is_german("de_DE"))
        self.assertEqual(text("de", "start_backup"), "Backup jetzt starten")

    def test_non_german_locales_use_english_strings(self):
        self.assertFalse(is_german("en_GB"))
        self.assertFalse(is_german("fr_FR"))
        self.assertEqual(text("en_GB", "start_backup"), "Start backup now")
        self.assertEqual(text("fr_FR", "start_backup"), "Start backup now")

    def test_translation_catalogs_have_the_same_keys(self):
        self.assertEqual(set(STRINGS["de"]), set(STRINGS["en"]))

    def test_interpolated_strings_are_localized(self):
        self.assertEqual(
            text("de", "format_question", device="/dev/sdb"),
            "/dev/sdb jetzt vollständig löschen und als KODI_BACKUP formatieren?",
        )
        self.assertEqual(
            text("en", "format_question", device="/dev/sdb"),
            "Permanently erase /dev/sdb and format it as KODI_BACKUP now?",
        )


if __name__ == "__main__":
    unittest.main()
