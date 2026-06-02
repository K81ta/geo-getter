import re
import unittest
from pathlib import Path

from geo_getter import __version__


class VersionTest(unittest.TestCase):
    def test_package_version_matches_pyproject(self):
        pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
        match = re.search(r'(?m)^version\s*=\s*"([^"]+)"', pyproject.read_text(encoding="utf-8"))
        self.assertIsNotNone(match)
        self.assertEqual(__version__, match.group(1))


if __name__ == "__main__":
    unittest.main()
