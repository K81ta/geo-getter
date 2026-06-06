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

    def test_pyproject_has_no_console_script(self):
        pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
        text = pyproject.read_text(encoding="utf-8")
        old_requires_python = 'requires-python = ">=3.' + '10"'
        project_scripts_header = "[project" + ".scripts]"
        old_console_script = 'geo-getter = "geo_getter.' + 'app:main"'
        self.assertIn('requires-python = ">=3.14"', text)
        self.assertNotIn(old_requires_python, text)
        self.assertNotIn(project_scripts_header, text)
        self.assertNotIn(old_console_script, text)

    def test_python_package_gui_launcher_is_removed(self):
        package_dir = Path(__file__).resolve().parents[1] / "geo_getter"
        self.assertFalse((package_dir / "app.py").exists())
        self.assertFalse((package_dir / "__main__.py").exists())

    def test_ci_uses_single_python_runtime(self):
        workflow = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml"
        text = workflow.read_text(encoding="utf-8")
        old_python_version = '"3.' + '10"'
        matrix_key = "matrix" + ":"
        codex_branch_pattern = '"codex/' + '**"'
        self.assertIn('python-version: "3.14"', text)
        self.assertNotIn(old_python_version, text)
        self.assertNotIn(matrix_key, text)
        self.assertNotIn(codex_branch_pattern, text)

    def test_installer_requires_release_macros(self):
        installer = Path(__file__).resolve().parents[1] / "installer" / "GEOGetter.iss"
        text = installer.read_text(encoding="utf-8")
        self.assertIn("#error AppVersion must be defined", text)
        self.assertIn("#error SourceDir must be defined", text)
        self.assertIn("#error OutputDir must be defined", text)
        self.assertNotRegex(text, r'#define\s+AppVersion\s+"')
        self.assertNotIn("GEOGetter-v0.1.3-win-x64-portable", text)

    def test_release_script_passes_installer_macros(self):
        build_script = Path(__file__).resolve().parents[1] / "tools" / "build_release.ps1"
        text = build_script.read_text(encoding="utf-8")
        self.assertIn('"/DAppVersion=$version"', text)
        self.assertIn('"/DSourceDir=$payloadDir"', text)
        self.assertIn('"/DOutputDir=$DistRoot"', text)

    def test_release_does_not_publish_checksum_assets(self):
        root = Path(__file__).resolve().parents[1]
        release_workflow = (root / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        build_script = (root / "tools" / "build_release.ps1").read_text(encoding="utf-8")
        self.assertNotIn("exe.sha256", release_workflow)
        self.assertNotIn("zip.sha256", release_workflow)
        self.assertNotIn("Write-Sha256File", build_script)
        self.assertNotIn("Created checksum", build_script)


if __name__ == "__main__":
    unittest.main()
