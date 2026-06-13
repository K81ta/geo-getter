import inspect
import json
import tomllib
import unittest
from pathlib import Path

from geo_getter import __version__
from geo_getter import http_client, updater

ROOT = Path(__file__).resolve().parents[1]


class VersionTest(unittest.TestCase):
    def test_package_version_is_sourced_from_package_attribute(self):
        pyproject = ROOT / "pyproject.toml"
        config = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        self.assertNotIn("version", config["project"])
        self.assertEqual(["version"], config["project"]["dynamic"])
        self.assertEqual(
            {"attr": "geo_getter.__version__"},
            config["tool"]["setuptools"]["dynamic"]["version"],
        )

    def test_pyproject_has_no_console_script(self):
        pyproject = ROOT / "pyproject.toml"
        text = pyproject.read_text(encoding="utf-8")
        old_requires_python = 'requires-python = ">=3.' + '10"'
        project_scripts_header = "[project" + ".scripts]"
        old_console_script = 'geo-getter = "geo_getter.' + 'app:main"'
        self.assertIn('requires-python = ">=3.14"', text)
        self.assertNotIn(old_requires_python, text)
        self.assertNotIn(project_scripts_header, text)
        self.assertNotIn(old_console_script, text)

    def test_python_package_gui_launcher_is_removed(self):
        package_dir = ROOT / "geo_getter"
        self.assertFalse((package_dir / "app.py").exists())
        self.assertFalse((package_dir / "__main__.py").exists())

    def test_ci_uses_single_python_runtime(self):
        workflow = ROOT / ".github" / "workflows" / "ci.yml"
        text = workflow.read_text(encoding="utf-8")
        old_python_version = '"3.' + '10"'
        matrix_key = "matrix" + ":"
        codex_branch_pattern = '"codex/' + '**"'
        self.assertIn('python-version: "3.14"', text)
        self.assertNotIn(old_python_version, text)
        self.assertNotIn(matrix_key, text)
        self.assertNotIn(codex_branch_pattern, text)

    def test_installer_requires_release_macros(self):
        installer = ROOT / "installer" / "GEOGetter.iss"
        text = installer.read_text(encoding="utf-8")
        self.assertIn("#error AppVersion must be defined", text)
        self.assertIn("#error SourceDir must be defined", text)
        self.assertIn("#error OutputDir must be defined", text)
        self.assertNotRegex(text, r'#define\s+AppVersion\s+"')
        self.assertNotIn("GEOGetter-v0.1.3-win-x64-portable", text)

    def test_release_script_passes_installer_macros(self):
        build_script = ROOT / "tools" / "build_release.ps1"
        text = build_script.read_text(encoding="utf-8")
        self.assertIn("geo_getter\\__init__.py", text)
        self.assertIn("geo_getter.__version__", text)
        self.assertNotIn("Could not read project version from pyproject.toml", text)
        self.assertIn('"/DAppVersion=$version"', text)
        self.assertIn('"/DSourceDir=$payloadDir"', text)
        self.assertIn('"/DOutputDir=$DistRoot"', text)

    def test_release_payload_includes_gui_resources(self):
        build_script = ROOT / "tools" / "build_release.ps1"
        text = build_script.read_text(encoding="utf-8")
        self.assertIn('"resources"', text)

    def test_gui_text_resource_has_matching_languages(self):
        resource = ROOT / "resources" / "gui_text.json"
        self.assertTrue(resource.exists())
        payload = json.loads(resource.read_text(encoding="utf-8"))
        self.assertEqual(set(payload), {"ja", "en"})
        self.assertEqual(set(payload["ja"]), set(payload["en"]))
        self.assertTrue(payload["ja"]["helpUsageText"])
        self.assertTrue(payload["en"]["helpUsageText"])

    def test_release_does_not_publish_checksum_assets(self):
        release_workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        build_script = (ROOT / "tools" / "build_release.ps1").read_text(encoding="utf-8")
        self.assertNotIn("exe.sha256", release_workflow)
        self.assertNotIn("zip.sha256", release_workflow)
        self.assertNotIn("Write-Sha256File", build_script)
        self.assertNotIn("Created checksum", build_script)

    def test_release_version_validation_uses_source_version(self):
        release_workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        self.assertIn("geo_getter.__version__", release_workflow)
        self.assertIn("Tag version $tagVersion does not match geo_getter.__version__ $sourceVersion", release_workflow)
        self.assertNotIn("pyproject.toml version", release_workflow)
        self.assertNotIn("Could not read version from pyproject.toml", release_workflow)

    def test_runtime_version_surfaces_use_package_version(self):
        self.assertEqual(f"geo-getter/{__version__}", http_client.USER_AGENT)
        self.assertEqual(
            __version__,
            inspect.signature(updater.check_for_update).parameters["current_version"].default,
        )
        self.assertEqual(
            __version__,
            inspect.signature(updater.build_update_check_payload).parameters["current_version"].default,
        )
        self.assertEqual(
            __version__,
            inspect.signature(updater.download_update_installer).parameters["current_version"].default,
        )

    def test_readme_uses_latest_release_artifact_guidance(self):
        for readme_name in ("README.md", "README.en.md"):
            text = (ROOT / readme_name).read_text(encoding="utf-8")
            self.assertIn("https://github.com/K81ta/geo-getter/releases/latest", text)
            self.assertIn("GEOGetter-Setup-v*.exe", text)
            self.assertIn("GEOGetter-v*-win-x64-portable.zip", text)
            self.assertNotIn("GEOGetter-Setup-v0.1.4.exe", text)
            self.assertNotIn("GEOGetter-v0.1.4-win-x64-portable.zip", text)


if __name__ == "__main__":
    unittest.main()
