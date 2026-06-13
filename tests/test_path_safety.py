import tempfile
import unittest
from pathlib import Path

from geo_getter.path_safety import (
    child_path,
    download_part_path,
    existing_candidate_path,
    existing_size,
    name_collision_key,
    quarantine_candidate_path,
    reserve_unique_download_name,
    safe_file_name,
)


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

    def test_download_name_reserves_final_and_part_names(self):
        used_keys = {name_collision_key("sample.fastq.gz.part")}

        planned = reserve_unique_download_name("sample.fastq.gz", used_keys)

        self.assertEqual(planned, "sample.2.fastq.gz")
        self.assertIn(name_collision_key("sample.2.fastq.gz"), used_keys)
        self.assertIn(name_collision_key("sample.2.fastq.gz.part"), used_keys)

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

    def test_existing_size_returns_zero_for_non_files_and_os_errors(self):
        class OSErrorPath:
            def is_file(self):
                raise OSError("boom")

        class StatOSErrorPath:
            def is_file(self):
                return True

            def stat(self):
                raise OSError("boom")

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            file_path = root / "file.bin"
            file_path.write_bytes(b"data")
            directory = root / "directory"
            directory.mkdir()

            self.assertEqual(existing_size(file_path), 4)
            self.assertEqual(existing_size(root / "missing.bin"), 0)
            self.assertEqual(existing_size(directory), 0)
            self.assertEqual(existing_size(OSErrorPath()), 0)
            self.assertEqual(existing_size(StatOSErrorPath()), 0)

    def test_download_runtime_candidate_paths_share_suffix_rules(self):
        target = Path("sample.fastq.gz")
        part = download_part_path(target)

        self.assertEqual(part.name, "sample.fastq.gz.part")
        self.assertEqual(existing_candidate_path(target).name, "sample.fastq.gz.existing")
        self.assertEqual(existing_candidate_path(target, 2).name, "sample.fastq.gz.existing.2")
        self.assertEqual(
            quarantine_candidate_path(target, "bad-md5-existing", "20000101T000000Z").name,
            "sample.fastq.gz.bad-md5-existing-20000101T000000Z",
        )
        self.assertEqual(
            quarantine_candidate_path(part, "bad-md5", "20000101T000000Z", 2).name,
            "sample.fastq.gz.part.bad-md5-20000101T000000Z.2",
        )


if __name__ == "__main__":
    unittest.main()
