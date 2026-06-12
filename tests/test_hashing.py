import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from geo_getter.hashing import calculate_md5, calculate_sha256, verify_md5


class FakeDigest:
    def __init__(self, value: str):
        self.value = value

    def hexdigest(self) -> str:
        return self.value


class HashingTest(unittest.TestCase):
    def test_calculate_md5_matches_hashlib(self):
        data = b"hello\nworld\n"
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "fixture.bin"
            path.write_bytes(data)

            self.assertEqual(calculate_md5(path), hashlib.md5(data).hexdigest())

    def test_calculate_sha256_matches_hashlib(self):
        data = b"installer bytes\n"
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "fixture.bin"
            path.write_bytes(data)

            self.assertEqual(calculate_sha256(path), hashlib.sha256(data).hexdigest())

    def test_verify_md5_returns_match_and_actual_digest(self):
        data = b"verified\n"
        expected = hashlib.md5(data).hexdigest()
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "fixture.bin"
            path.write_bytes(data)

            self.assertEqual(verify_md5(path, expected), (True, expected))
            self.assertEqual(verify_md5(path, "0" * 32), (False, expected))

    def test_calculate_md5_uses_hashlib_file_digest(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "fixture.bin"
            path.write_bytes(b"fixture")

            with mock.patch("geo_getter.hashing.hashlib.file_digest", return_value=FakeDigest("abc123")) as file_digest:
                self.assertEqual(calculate_md5(path), "abc123")

            file_digest.assert_called_once()
            self.assertEqual(file_digest.call_args.args[1], "md5")

    def test_calculate_sha256_uses_hashlib_file_digest(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "fixture.bin"
            path.write_bytes(b"fixture")

            with mock.patch("geo_getter.hashing.hashlib.file_digest", return_value=FakeDigest("def456")) as file_digest:
                self.assertEqual(calculate_sha256(path), "def456")

            file_digest.assert_called_once()
            self.assertEqual(file_digest.call_args.args[1], "sha256")


if __name__ == "__main__":
    unittest.main()
