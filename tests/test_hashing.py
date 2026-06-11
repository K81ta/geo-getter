import hashlib
import tempfile
import unittest
from pathlib import Path

from geo_getter.hashing import calculate_digest, calculate_sha256, new_digest, verify_digest


class HashingTest(unittest.TestCase):
    def test_calculate_digest_matches_hashlib_md5(self):
        data = b"hello\nworld\n"
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "fixture.bin"
            path.write_bytes(data)

            self.assertEqual(calculate_digest(path, "md5"), hashlib.md5(data).hexdigest())

    def test_calculate_digest_matches_hashlib_sha256(self):
        data = b"installer bytes\n"
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "fixture.bin"
            path.write_bytes(data)

            self.assertEqual(calculate_digest(path, "sha256"), hashlib.sha256(data).hexdigest())
            self.assertEqual(calculate_sha256(path), hashlib.sha256(data).hexdigest())

    def test_verify_digest_returns_match_and_actual_digest(self):
        data = b"verified\n"
        expected = hashlib.md5(data).hexdigest()
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "fixture.bin"
            path.write_bytes(data)

            self.assertEqual(verify_digest(path, expected, "md5"), (True, expected))
            self.assertEqual(verify_digest(path, "0" * 32, "md5"), (False, expected))

    def test_new_digest_supports_chunked_updates(self):
        chunks = [b"first\n", b"second\n", b"third\n"]
        cases = [
            ("MD5", hashlib.md5),
            ("SHA256", hashlib.sha256),
        ]

        for algorithm, factory in cases:
            digest = new_digest(algorithm)
            for chunk in chunks:
                digest.update(chunk)

            self.assertEqual(digest.hexdigest(), factory(b"".join(chunks)).hexdigest())

    def test_unsupported_algorithm_raises_value_error(self):
        with self.assertRaises(ValueError):
            new_digest("sha1")

    def test_calculate_digest_rejects_non_positive_chunk_size(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "fixture.bin"
            path.write_bytes(b"fixture")

            with self.assertRaises(ValueError):
                calculate_digest(path, "md5", chunk_size=0)


if __name__ == "__main__":
    unittest.main()
