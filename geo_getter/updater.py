from __future__ import annotations

import http.client
import re
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

from . import __version__
from .errors import (
    UPDATE_ASSET_MISSING,
    UPDATE_ASSET_URL_MISSING,
    UPDATE_DIGEST_INVALID,
    UPDATE_DIGEST_MISSING,
    UPDATE_DOWNLOAD_FAILED,
    UPDATE_NOT_AVAILABLE,
    UPDATE_SHA256_MISMATCH,
    UPDATE_VERSION_INVALID,
    GeoGetterError,
)
from .hashing import DEFAULT_CHUNK_SIZE, new_digest
from .http_client import USER_AGENT, fetch_json

LATEST_RELEASE_URL = "https://api.github.com/repos/K81ta/geo-getter/releases/latest"
GITHUB_API_HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}
SHA256_DIGEST_RE = re.compile(r"^sha256:([0-9a-fA-F]{64})$")
VERSION_RE = re.compile(r"^v?(\d+(?:\.\d+)*)$")


def compare_versions(left: str, right: str) -> int:
    left_parts = _version_parts(left)
    right_parts = _version_parts(right)
    max_len = max(len(left_parts), len(right_parts))
    left_parts.extend([0] * (max_len - len(left_parts)))
    right_parts.extend([0] * (max_len - len(right_parts)))
    if left_parts > right_parts:
        return 1
    if left_parts < right_parts:
        return -1
    return 0


def check_for_update(
    current_version: str = __version__,
    fetcher: Callable[..., Any] = fetch_json,
) -> dict[str, Any]:
    release = fetcher(LATEST_RELEASE_URL, timeout=60, headers=GITHUB_API_HEADERS)
    return build_update_check_payload(release, current_version=current_version)


def build_update_check_payload(release: dict[str, Any], current_version: str = __version__) -> dict[str, Any]:
    latest_version = _version_from_release(release)
    payload: dict[str, Any] = {
        "event": "done",
        "kind": "update_check",
        "current_version": current_version,
        "latest_version": latest_version,
        "update_available": compare_versions(latest_version, current_version) > 0,
        "release_url": str(release.get("html_url") or release.get("url") or ""),
        "asset": None,
    }
    if not payload["update_available"]:
        return payload

    asset = _find_installer_asset(release, latest_version)
    sha256 = extract_sha256_digest(asset)
    download_url = str(asset.get("browser_download_url") or "")
    if not download_url:
        raise GeoGetterError(UPDATE_ASSET_URL_MISSING, f"asset={asset.get('name', '')}")
    payload["asset"] = {
        "name": str(asset.get("name") or ""),
        "size": _int_or_zero(asset.get("size")),
        "digest": str(asset.get("digest") or ""),
        "sha256": sha256,
        "download_url": download_url,
    }
    return payload


def download_update_installer(
    version: str,
    output_dir: str | Path | None = None,
    current_version: str = __version__,
    fetcher: Callable[..., Any] = fetch_json,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> dict[str, Any]:
    check = check_for_update(current_version=current_version, fetcher=fetcher)
    if not check["update_available"] or check["latest_version"] != version:
        raise GeoGetterError(UPDATE_NOT_AVAILABLE, f"requested={version} latest={check['latest_version']}")

    asset = check["asset"] or {}
    installer_dir = _update_output_dir(version, output_dir)
    installer_dir.mkdir(parents=True, exist_ok=True)
    installer_path = installer_dir / str(asset["name"])
    part_path = installer_path.with_name(installer_path.name + ".part")
    expected_sha256 = str(asset["sha256"])
    expected_size = _int_or_zero(asset.get("size"))

    if part_path.exists():
        part_path.unlink()
    request = urllib.request.Request(str(asset["download_url"]), headers={"User-Agent": USER_AGENT})
    downloaded = 0
    digest = new_digest("sha256")
    try:
        with opener(request, timeout=120) as response:
            with part_path.open("wb") as handle:
                while True:
                    chunk = response.read(DEFAULT_CHUNK_SIZE)
                    if not chunk:
                        break
                    handle.write(chunk)
                    digest.update(chunk)
                    downloaded += len(chunk)
    except (urllib.error.URLError, http.client.HTTPException, OSError) as exc:
        _remove_if_exists(part_path)
        raise GeoGetterError(UPDATE_DOWNLOAD_FAILED, str(exc)) from exc

    if expected_size and downloaded != expected_size:
        _remove_if_exists(part_path)
        raise GeoGetterError(UPDATE_DOWNLOAD_FAILED, f"expected_size={expected_size} downloaded={downloaded}")

    actual_sha256 = digest.hexdigest()
    if actual_sha256.lower() != expected_sha256.lower():
        _remove_if_exists(part_path)
        raise GeoGetterError(UPDATE_SHA256_MISMATCH, f"expected={expected_sha256} actual={actual_sha256}")

    if installer_path.exists():
        installer_path.unlink()
    part_path.replace(installer_path)
    return {
        "event": "done",
        "kind": "update_installer",
        "version": version,
        "installer_path": str(installer_path),
        "sha256": actual_sha256,
        "bytes": downloaded,
    }


def extract_sha256_digest(asset: dict[str, Any]) -> str:
    digest = str(asset.get("digest") or "")
    if not digest:
        raise GeoGetterError(UPDATE_DIGEST_MISSING, f"asset={asset.get('name', '')}")
    match = SHA256_DIGEST_RE.match(digest)
    if not match:
        raise GeoGetterError(UPDATE_DIGEST_INVALID, f"asset={asset.get('name', '')} digest={digest}")
    return match.group(1).lower()


def installer_asset_name(version: str) -> str:
    return f"GEOGetter-Setup-v{version}.exe"


def _find_installer_asset(release: dict[str, Any], version: str) -> dict[str, Any]:
    expected_name = installer_asset_name(version)
    for asset in release.get("assets") or []:
        if str(asset.get("name") or "") == expected_name:
            return dict(asset)
    raise GeoGetterError(UPDATE_ASSET_MISSING, f"expected_asset={expected_name}")


def _version_from_release(release: dict[str, Any]) -> str:
    tag_name = str(release.get("tag_name") or "")
    match = VERSION_RE.match(tag_name)
    if not match:
        raise GeoGetterError(UPDATE_VERSION_INVALID, f"tag_name={tag_name}")
    return match.group(1)


def _version_parts(version: str) -> list[int]:
    match = VERSION_RE.match(str(version))
    if not match:
        raise GeoGetterError(UPDATE_VERSION_INVALID, f"version={version}")
    return [int(part) for part in match.group(1).split(".")]


def _update_output_dir(version: str, output_dir: str | Path | None) -> Path:
    if output_dir:
        return Path(output_dir)
    return Path(tempfile.gettempdir()) / "GEOGetter" / "updates" / version


def _int_or_zero(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _remove_if_exists(path: Path) -> None:
    try:
        if path.exists():
            path.unlink()
    except OSError:
        pass
