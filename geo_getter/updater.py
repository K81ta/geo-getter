from __future__ import annotations

import re
import tempfile
from pathlib import Path
from typing import Any, Callable

from . import __version__
from .downloader import (
    DownloadLocalIoError,
    DownloadNetworkError,
    DownloadSizeMismatchError,
    download_failure_outcome,
    download_url_to_part,
    finalize_downloaded_part,
)
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
from .hashing import calculate_sha256
from .http_client import fetch_json, fetch_text
from .path_safety import download_part_path

LATEST_RELEASE_URL = "https://api.github.com/repos/K81ta/geo-getter/releases/latest"
GITHUB_API_HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}
SHA256_DIGEST_RE = re.compile(r"^sha256:([0-9a-fA-F]{64})$")
SHA256SUMS_ASSET_NAME = "SHA256SUMS.txt"
SHA256SUMS_LINE_RE = re.compile(r"^([0-9a-fA-F]{64})\s+\*?(.+)$")
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
    text_fetcher: Callable[..., str] = fetch_text,
) -> dict[str, Any]:
    release = fetcher(LATEST_RELEASE_URL, timeout=60, headers=GITHUB_API_HEADERS)
    return build_update_check_payload(release, current_version=current_version, text_fetcher=text_fetcher)


def build_update_check_payload(
    release: dict[str, Any],
    current_version: str = __version__,
    text_fetcher: Callable[..., str] = fetch_text,
) -> dict[str, Any]:
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
    sha256, sha256_source = _resolve_installer_sha256(release, asset, text_fetcher)
    download_url = str(asset.get("browser_download_url") or "")
    if not download_url:
        raise GeoGetterError(UPDATE_ASSET_URL_MISSING, f"asset={asset.get('name', '')}")
    payload["asset"] = {
        "name": str(asset.get("name") or ""),
        "size": _int_or_zero(asset.get("size")),
        "digest": str(asset.get("digest") or ""),
        "sha256": sha256,
        "sha256_source": sha256_source,
        "download_url": download_url,
    }
    return payload


def download_update_installer(
    version: str,
    output_dir: str | Path | None = None,
    current_version: str = __version__,
    fetcher: Callable[..., Any] = fetch_json,
) -> dict[str, Any]:
    check = check_for_update(current_version=current_version, fetcher=fetcher)
    if not check["update_available"] or check["latest_version"] != version:
        raise GeoGetterError(UPDATE_NOT_AVAILABLE, f"requested={version} latest={check['latest_version']}")

    asset = check["asset"] or {}
    installer_dir = _update_output_dir(version, output_dir)
    installer_dir.mkdir(parents=True, exist_ok=True)
    installer_path = installer_dir / str(asset["name"])
    part_path = download_part_path(installer_path)
    expected_sha256 = str(asset["sha256"])
    expected_size = _int_or_zero(asset.get("size"))

    try:
        if part_path.exists():
            part_path.unlink()
    except OSError as exc:
        raise _update_download_error(installer_path, exc) from exc

    try:
        downloaded_part = download_url_to_part(
            str(asset["download_url"]),
            installer_path,
            expected_size=expected_size,
        )
    except (DownloadSizeMismatchError, DownloadNetworkError, DownloadLocalIoError) as exc:
        error = _update_download_error(installer_path, exc)
        _remove_if_exists(part_path)
        raise error from exc

    try:
        actual_sha256 = calculate_sha256(downloaded_part.path)
    except OSError as exc:
        error = _update_download_error(installer_path, exc)
        _remove_if_exists(part_path)
        raise error from exc

    if actual_sha256.lower() != expected_sha256.lower():
        _remove_if_exists(part_path)
        raise GeoGetterError(UPDATE_SHA256_MISMATCH, f"expected={expected_sha256} actual={actual_sha256}")

    try:
        if installer_path.exists():
            installer_path.unlink()
        finalize_downloaded_part(installer_path)
    except (DownloadLocalIoError, OSError) as exc:
        error = _update_download_error(installer_path, exc)
        _remove_if_exists(part_path)
        raise error from exc

    return {
        "event": "done",
        "kind": "update_installer",
        "version": version,
        "installer_path": str(installer_path),
        "sha256": actual_sha256,
        "bytes": downloaded_part.bytes_downloaded,
    }


def extract_sha256_digest(asset: dict[str, Any]) -> str:
    digest = str(asset.get("digest") or "")
    if not digest:
        raise GeoGetterError(UPDATE_DIGEST_MISSING, f"asset={asset.get('name', '')}")
    match = SHA256_DIGEST_RE.match(digest)
    if not match:
        raise GeoGetterError(UPDATE_DIGEST_INVALID, f"asset={asset.get('name', '')} digest={digest}")
    return match.group(1).lower()


def _resolve_installer_sha256(
    release: dict[str, Any],
    installer_asset: dict[str, Any],
    text_fetcher: Callable[..., str],
) -> tuple[str, str]:
    try:
        return extract_sha256_digest(installer_asset), "asset_digest"
    except GeoGetterError as digest_error:
        checksum_asset = _find_checksum_asset(release)
        if checksum_asset is None:
            raise digest_error
        try:
            sha256 = _fetch_installer_sha256_from_sums(
                checksum_asset,
                str(installer_asset.get("name") or ""),
                text_fetcher,
            )
            return sha256, SHA256SUMS_ASSET_NAME
        except GeoGetterError:
            raise
        except Exception as exc:
            raise GeoGetterError(UPDATE_DIGEST_INVALID, f"asset={SHA256SUMS_ASSET_NAME} error={exc}") from exc


def installer_asset_name(version: str) -> str:
    return f"GEOGetter-Setup-v{version}.exe"


def _find_installer_asset(release: dict[str, Any], version: str) -> dict[str, Any]:
    expected_name = installer_asset_name(version)
    asset = _find_asset_by_name(release, expected_name)
    if asset is not None:
        return asset
    raise GeoGetterError(UPDATE_ASSET_MISSING, f"expected_asset={expected_name}")


def _find_checksum_asset(release: dict[str, Any]) -> dict[str, Any] | None:
    return _find_asset_by_name(release, SHA256SUMS_ASSET_NAME)


def _find_asset_by_name(release: dict[str, Any], expected_name: str) -> dict[str, Any] | None:
    for asset in release.get("assets") or []:
        if str(asset.get("name") or "") == expected_name:
            return dict(asset)
    return None


def _fetch_installer_sha256_from_sums(
    checksum_asset: dict[str, Any],
    installer_name: str,
    text_fetcher: Callable[..., str],
) -> str:
    download_url = str(checksum_asset.get("browser_download_url") or "")
    if not download_url:
        raise GeoGetterError(UPDATE_DIGEST_MISSING, f"asset={SHA256SUMS_ASSET_NAME}")
    text = text_fetcher(download_url, timeout=60)
    for line in text.splitlines():
        match = SHA256SUMS_LINE_RE.match(line.strip())
        if not match:
            continue
        digest, file_name = match.groups()
        if file_name.strip() == installer_name:
            return digest.lower()
    raise GeoGetterError(UPDATE_DIGEST_MISSING, f"asset={SHA256SUMS_ASSET_NAME} installer={installer_name}")


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


def _update_download_error(local_path: Path, error: BaseException) -> GeoGetterError:
    outcome = download_failure_outcome(local_path, error)
    return GeoGetterError(
        UPDATE_DOWNLOAD_FAILED,
        outcome.result_message,
        extra={
            "download_status": outcome.status,
            "download_message": outcome.message,
            "download_result_message": outcome.result_message,
            "bytes_downloaded": outcome.bytes_downloaded,
        },
    )


def _remove_if_exists(path: Path) -> None:
    try:
        if path.exists():
            path.unlink()
    except OSError:
        pass
