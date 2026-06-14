import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from geo_getter.downloader import (
    DownloadedPart,
    DownloadLocalIoError,
    DownloadNetworkError,
    DownloadSizeMismatchError,
)
from geo_getter.errors import GeoGetterError
from geo_getter.updater import (
    GITHUB_API_HEADERS,
    LATEST_RELEASE_URL,
    build_update_check_payload,
    check_for_update,
    compare_versions,
    download_update_installer,
    extract_sha256_digest,
    installer_asset_name,
)


class UpdaterTest(unittest.TestCase):
    def release_payload(self, version="0.1.4", data=b"installer", digest=None, asset_name=None):
        if digest is None:
            digest = "sha256:" + hashlib.sha256(data).hexdigest()
        name = asset_name or installer_asset_name(version)
        return {
            "tag_name": f"v{version}",
            "html_url": f"https://github.com/K81ta/geo-getter/releases/tag/v{version}",
            "assets": [
                {
                    "name": name,
                    "size": len(data),
                    "digest": digest,
                    "browser_download_url": f"https://example.invalid/{name}",
                }
            ],
        }

    def assert_geo_error(self, code, callback, *args, **kwargs):
        with self.assertRaises(GeoGetterError) as context:
            callback(*args, **kwargs)
        self.assertEqual(context.exception.code, code)

    def write_downloaded_part(self, local_path, data):
        part_path = local_path.with_name(local_path.name + ".part")
        part_path.write_bytes(data)
        return DownloadedPart(part_path, len(data))

    def test_version_comparison_is_numeric(self):
        self.assertGreater(compare_versions("0.1.10", "0.1.9"), 0)
        self.assertEqual(compare_versions("v0.1.3", "0.1.3"), 0)
        self.assertLess(compare_versions("0.1.3", "0.1.4"), 0)

    def test_no_update_returns_normal_payload_without_asset(self):
        payload = build_update_check_payload({"tag_name": "v0.1.3", "html_url": "https://example.invalid", "assets": []}, "0.1.3")

        self.assertFalse(payload["update_available"])
        self.assertEqual(payload["latest_version"], "0.1.3")
        self.assertIsNone(payload["asset"])

    def test_check_for_update_passes_github_api_headers(self):
        calls = []

        def fetcher(url, **kwargs):
            calls.append((url, kwargs))
            return {"tag_name": "v0.1.3", "assets": []}

        payload = check_for_update(current_version="0.1.3", fetcher=fetcher)

        self.assertFalse(payload["update_available"])
        self.assertEqual(calls, [(LATEST_RELEASE_URL, {"timeout": 60, "headers": GITHUB_API_HEADERS})])

    def test_newer_release_returns_installer_asset_and_digest(self):
        data = b"fixture"
        release = self.release_payload(data=data)

        payload = build_update_check_payload(release, "0.1.3")

        self.assertTrue(payload["update_available"])
        self.assertEqual(payload["latest_version"], "0.1.4")
        self.assertEqual(payload["asset"]["name"], "GEOGetter-Setup-v0.1.4.exe")
        self.assertEqual(payload["asset"]["sha256"], hashlib.sha256(data).hexdigest())

    def test_newer_release_requires_installer_asset(self):
        release = self.release_payload(asset_name="GEOGetter-v0.1.4-win-x64-portable.zip")

        self.assert_geo_error("update_asset_missing", build_update_check_payload, release, "0.1.3")

    def test_newer_release_requires_installer_download_url(self):
        release = self.release_payload()
        release["assets"][0]["browser_download_url"] = ""

        self.assert_geo_error("update_asset_url_missing", build_update_check_payload, release, "0.1.3")

    def test_digest_is_required(self):
        release = self.release_payload(digest="")

        self.assert_geo_error("update_digest_missing", build_update_check_payload, release, "0.1.3")

    def test_digest_must_be_sha256_hex(self):
        self.assert_geo_error("update_digest_invalid", extract_sha256_digest, {"name": "fixture.exe", "digest": "md5:" + "0" * 32})
        self.assert_geo_error("update_digest_invalid", extract_sha256_digest, {"name": "fixture.exe", "digest": "sha256:" + "0" * 63})

    def test_download_update_installer_verifies_sha256_before_returning_path(self):
        data = b"verified installer"
        release = self.release_payload(data=data)
        fetcher = lambda *_args, **_kwargs: release
        calls = []

        def download_installer(url, local_path, **kwargs):
            calls.append((url, local_path, kwargs))
            self.assertFalse(local_path.with_name(local_path.name + ".part").exists())
            return self.write_downloaded_part(local_path, data)

        with tempfile.TemporaryDirectory() as temp:
            installer_path = Path(temp) / installer_asset_name("0.1.4")
            installer_path.write_bytes(b"old installer")
            stale_part = installer_path.with_name(installer_path.name + ".part")
            stale_part.write_bytes(b"stale part")

            with mock.patch("geo_getter.updater.download_url_to_part", side_effect=download_installer):
                payload = download_update_installer("0.1.4", output_dir=temp, current_version="0.1.3", fetcher=fetcher)
            installer_path = Path(payload["installer_path"])

            self.assertEqual(payload["kind"], "update_installer")
            self.assertEqual(payload["sha256"], hashlib.sha256(data).hexdigest())
            self.assertEqual(payload["bytes"], len(data))
            self.assertTrue(installer_path.exists())
            self.assertEqual(installer_path.read_bytes(), data)
            self.assertFalse(stale_part.exists())
            self.assertEqual(
                calls,
                [
                    (
                        release["assets"][0]["browser_download_url"],
                        installer_path,
                        {"expected_size": len(data)},
                    )
                ],
            )

    def test_download_failure_does_not_return_installer_path(self):
        release = self.release_payload()
        fetcher = lambda *_args, **_kwargs: release

        def fail_download(_url, local_path, **_kwargs):
            self.write_downloaded_part(local_path, b"partial")
            raise DownloadNetworkError("temporary failure")

        with tempfile.TemporaryDirectory() as temp:
            with mock.patch("geo_getter.updater.download_url_to_part", side_effect=fail_download):
                self.assert_geo_error(
                    "update_download_failed",
                    download_update_installer,
                    "0.1.4",
                    output_dir=temp,
                    current_version="0.1.3",
                    fetcher=fetcher,
                )
            self.assertFalse(list(Path(temp).glob("*.exe")))
            self.assertFalse(list(Path(temp).glob("*.part")))

    def test_download_size_mismatch_does_not_return_installer_path(self):
        data = b"short installer"
        release = self.release_payload(data=data)
        release["assets"][0]["size"] = len(data) + 1
        fetcher = lambda *_args, **_kwargs: release

        def fail_size(_url, local_path, **_kwargs):
            self.write_downloaded_part(local_path, data)
            raise DownloadSizeMismatchError(f"expected={len(data) + 1} actual={len(data)}")

        with tempfile.TemporaryDirectory() as temp:
            with mock.patch("geo_getter.updater.download_url_to_part", side_effect=fail_size):
                self.assert_geo_error(
                    "update_download_failed",
                    download_update_installer,
                    "0.1.4",
                    output_dir=temp,
                    current_version="0.1.3",
                    fetcher=fetcher,
                )
            self.assertFalse(list(Path(temp).glob("*.exe")))
            self.assertFalse(list(Path(temp).glob("*.part")))

    def test_download_failure_carries_shared_classification_extra(self):
        release = self.release_payload()
        fetcher = lambda *_args, **_kwargs: release

        def fail_download(_url, local_path, **_kwargs):
            self.write_downloaded_part(local_path, b"partial")
            raise DownloadNetworkError("temporary failure")

        with tempfile.TemporaryDirectory() as temp:
            with (
                mock.patch("geo_getter.updater.download_url_to_part", side_effect=fail_download),
                self.assertRaises(GeoGetterError) as context,
            ):
                download_update_installer("0.1.4", output_dir=temp, current_version="0.1.3", fetcher=fetcher)

            error = context.exception
            self.assertEqual(error.code, "update_download_failed")
            self.assertEqual(error.extra["download_status"], "network_failed")
            self.assertIn("Network transfer failed", error.extra["download_message"])
            self.assertIn("temporary failure", error.extra["download_message"])
            self.assertEqual(error.extra["download_result_message"], "temporary failure")
            self.assertEqual(error.extra["bytes_downloaded"], len(b"partial"))
            self.assertFalse(list(Path(temp).glob("*.exe")))
            self.assertFalse(list(Path(temp).glob("*.part")))

    def test_local_io_failure_cleans_part_and_reports_download_failure(self):
        release = self.release_payload()
        fetcher = lambda *_args, **_kwargs: release

        def fail_local_io(_url, local_path, **_kwargs):
            self.write_downloaded_part(local_path, b"partial")
            raise DownloadLocalIoError("could not write partial download")

        with tempfile.TemporaryDirectory() as temp:
            with mock.patch("geo_getter.updater.download_url_to_part", side_effect=fail_local_io):
                self.assert_geo_error(
                    "update_download_failed",
                    download_update_installer,
                    "0.1.4",
                    output_dir=temp,
                    current_version="0.1.3",
                    fetcher=fetcher,
                )
            self.assertFalse(list(Path(temp).glob("*.exe")))
            self.assertFalse(list(Path(temp).glob("*.part")))

    def test_sha256_mismatch_does_not_return_installer_path(self):
        data = b"downloaded installer"
        release = self.release_payload(data=data, digest="sha256:" + "0" * 64)
        fetcher = lambda *_args, **_kwargs: release

        def download_installer(_url, local_path, **_kwargs):
            return self.write_downloaded_part(local_path, data)

        with tempfile.TemporaryDirectory() as temp:
            with mock.patch("geo_getter.updater.download_url_to_part", side_effect=download_installer):
                self.assert_geo_error(
                    "update_sha256_mismatch",
                    download_update_installer,
                    "0.1.4",
                    output_dir=temp,
                    current_version="0.1.3",
                    fetcher=fetcher,
                )
            self.assertFalse(list(Path(temp).glob("*.exe")))
            self.assertFalse(list(Path(temp).glob("*.part")))

    def test_finalize_failure_reports_download_failure(self):
        data = b"downloaded installer"
        release = self.release_payload(data=data)
        fetcher = lambda *_args, **_kwargs: release

        def download_installer(_url, local_path, **_kwargs):
            return self.write_downloaded_part(local_path, data)

        with tempfile.TemporaryDirectory() as temp:
            with (
                mock.patch("geo_getter.updater.download_url_to_part", side_effect=download_installer),
                mock.patch("geo_getter.updater.finalize_downloaded_part", side_effect=DownloadLocalIoError("replace failed")),
            ):
                self.assert_geo_error(
                    "update_download_failed",
                    download_update_installer,
                    "0.1.4",
                    output_dir=temp,
                    current_version="0.1.3",
                    fetcher=fetcher,
                )
            self.assertFalse(list(Path(temp).glob("*.exe")))
            self.assertFalse(list(Path(temp).glob("*.part")))


if __name__ == "__main__":
    unittest.main()
