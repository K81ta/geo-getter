import inspect
import json
import re
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

    def test_pages_workflow_uses_jekyll_theme(self):
        workflow = ROOT / ".github" / "workflows" / "pages.yml"
        workflow_text = workflow.read_text(encoding="utf-8")
        config_text = (ROOT / "site" / "_config.yml").read_text(encoding="utf-8")

        self.assertIn("actions/jekyll-build-pages@v1", workflow_text)
        self.assertIn("source: ./site", workflow_text)
        self.assertIn("destination: ./_site", workflow_text)
        self.assertNotIn("actions/setup-python", workflow_text)
        self.assertNotIn("tools/build_site.py", workflow_text)
        self.assertIn("theme: jekyll-theme-minimal", config_text)
        self.assertFalse((ROOT / "tools" / "build_site.py").exists())
        self.assertFalse((ROOT / "site" / "_layouts" / "default.html").exists())
        self.assertFalse((ROOT / "site" / "assets" / "styles.css").exists())

    def test_mermaid_pages_load_renderer(self):
        include = ROOT / "site" / "_includes" / "mermaid.html"
        include_text = include.read_text(encoding="utf-8")
        self.assertIn("mermaid@11", include_text)
        self.assertIn("code.language-mermaid", include_text)
        self.assertIn("mermaid.run", include_text)

        for relative_path in ("site/architecture.md", "site/data-flow.md"):
            text = (ROOT / relative_path).read_text(encoding="utf-8")
            self.assertIn("```mermaid", text)
            self.assertIn("{% include mermaid.html %}", text)

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
        self.assertIn('"site"', text)
        self.assertNotIn('"docs"', text)

    def test_gui_text_resource_has_matching_languages(self):
        resource = ROOT / "resources" / "gui_text.json"
        self.assertTrue(resource.exists())
        payload = json.loads(resource.read_text(encoding="utf-8"))
        self.assertEqual(set(payload), {"ja", "en"})
        self.assertEqual(set(payload["ja"]), set(payload["en"]))
        self.assertTrue(payload["ja"]["helpUsageText"])
        self.assertTrue(payload["en"]["helpUsageText"])

    def test_release_publishes_sha256sums_manifest(self):
        release_workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        build_script = (ROOT / "tools" / "build_release.ps1").read_text(encoding="utf-8")
        self.assertNotIn("exe.sha256", release_workflow)
        self.assertNotIn("zip.sha256", release_workflow)
        self.assertIn("dist/SHA256SUMS.txt", release_workflow)
        self.assertIn("Write-Sha256Sums", build_script)
        self.assertIn("Created checksum manifest", build_script)

    def test_release_assert_inside_checks_path_boundary(self):
        build_script = (ROOT / "tools" / "build_release.ps1").read_text(encoding="utf-8")
        self.assertIn("$fullBaseWithSeparator", build_script)
        self.assertIn(".Equals($fullBase", build_script)
        self.assertNotIn("StartsWith($fullBase,", build_script)

    def test_release_version_validation_uses_source_version(self):
        release_workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        self.assertIn("geo_getter.__version__", release_workflow)
        self.assertIn("Tag version $tagVersion does not match geo_getter.__version__ $sourceVersion", release_workflow)
        self.assertNotIn("pyproject.toml version", release_workflow)
        self.assertNotIn("Could not read version from pyproject.toml", release_workflow)

    def test_release_workflow_passes_single_configured_python_runtime(self):
        release_workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        build_script = (ROOT / "tools" / "build_release.ps1").read_text(encoding="utf-8")

        runtime_matches = re.findall(r'^\s+RELEASE_PYTHON_VERSION:\s*"([^"]+)"', release_workflow, re.MULTILINE)
        self.assertEqual(1, len(runtime_matches))
        release_runtime = runtime_matches[0]
        self.assertEqual(1, release_workflow.count(release_runtime))
        self.assertIn("python-version: ${{ env.RELEASE_PYTHON_VERSION }}", release_workflow)
        self.assertIn('-PythonVersion "${{ env.RELEASE_PYTHON_VERSION }}"', release_workflow)
        self.assertIn(f'"{release_runtime}|x64"', build_script)

        default_match = re.search(r'\[string\]\$PythonVersion\s*=\s*"([^"]+)"', build_script)
        self.assertIsNotNone(default_match)
        self.assertIn(f'"{default_match.group(1)}|x64"', build_script)

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
