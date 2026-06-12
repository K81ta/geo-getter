import tempfile
import unittest
from pathlib import Path

from geo_getter.path_safety import child_path, name_collision_key, reserve_unique_download_name, safe_file_name


class PathSafetyTest(unittest.TestCase):
    def test_name_collision_key_uses_casefold(self):
        pairs = [
            ("Same.fastq.gz", "same.fastq.gz"),
            ("\u03a3.txt", "\u03c3.txt"),
            ("\u00df.txt", "SS.txt"),
            ("\u03c2.txt", "\u03c3.txt"),
            ("\u0130.txt", "i\u0307.txt"),
            ("\u212a.txt", "K.txt"),
            ("\u1e9e.txt", "\u00df.txt"),
            ("\u212b.txt", "\u00c5.txt"),
        ]

        for left, right in pairs:
            with self.subTest(left=left, right=right):
                self.assertEqual(name_collision_key(left), name_collision_key(right))

    def test_casefold_collisions_are_numbered(self):
        used_keys: set[str] = set()

        first = reserve_unique_download_name("\u00df.fastq.gz", used_keys)
        second = reserve_unique_download_name("SS.fastq.gz", used_keys)

        self.assertEqual(first, "\u00df.fastq.gz")
        self.assertEqual(second, "SS.2.fastq.gz")

    def test_safe_file_name_preserves_windows_reserved_and_unsafe_protection(self):
        self.assertEqual(safe_file_name("CON.txt", "download.txt"), "_CON.txt")
        self.assertEqual(safe_file_name("../escape:bad?.fastq.gz", "download.fastq.gz"), "_escape_bad_.fastq.gz")
        self.assertEqual(safe_file_name(" . ", "download.fastq.gz"), "download.fastq.gz")

    def test_child_path_rejects_path_traversal(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "out"
            root.mkdir()

            with self.assertRaises(ValueError):
                child_path(root, "../escape.fastq.gz")

            self.assertEqual(child_path(root, "safe.fastq.gz"), root.resolve() / "safe.fastq.gz")


if __name__ == "__main__":
    unittest.main()
