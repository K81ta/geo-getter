from __future__ import annotations

import hashlib
import http.client
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
import urllib.error
import urllib.request

from .errors import (
    ERROR_MESSAGES,
    MD5_MISMATCH,
    MD5_UNAVAILABLE,
    MD5_VERIFIED,
    NETWORK_FAILED,
    SIZE_MISMATCH,
)
from .http_client import USER_AGENT
from .models import DownloadPlan, PlannedFile
from .planner import append_download_log, ensure_capacity, write_fastq_outputs

ProgressCallback = Callable[[PlannedFile, int, int], None]
MessageCallback = Callable[[str], None]
ByteProgressCallback = Callable[[int, int], None]


class DownloadSizeMismatchError(Exception):
    pass


def verify_md5(path: str | Path, expected_md5: str) -> tuple[bool, str]:
    actual = calculate_md5(path)
    return actual.lower() == expected_md5.lower(), actual


def calculate_md5(path: str | Path) -> str:
    digest = hashlib.md5()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_plan(
    plan: DownloadPlan,
    progress_callback: ProgressCallback | None = None,
    message_callback: MessageCallback | None = None,
) -> list[tuple[PlannedFile, str, str]]:
    ensure_capacity(plan)
    write_fastq_outputs(plan)
    results: list[tuple[PlannedFile, str, str]] = []

    for planned in plan.files:
        try:
            _emit(message_callback, f"download_started: {planned.fastq.file_name}")
            existing_result = _reuse_or_quarantine_existing(planned, message_callback)
            if existing_result:
                status, message, actual_md5, downloaded = existing_result
                append_download_log(
                    plan.output_dir,
                    planned.fastq.run_accession,
                    planned.fastq.file_name,
                    status,
                    planned.fastq.expected_md5,
                    actual_md5,
                    planned.fastq.size_bytes,
                    downloaded,
                    message,
                )
                _emit(message_callback, f"{status}: {planned.fastq.file_name}")
                results.append((planned, status, message))
                continue

            part_result = _reuse_or_quarantine_complete_part(planned, message_callback)
            if part_result:
                status, message, actual_md5, downloaded = part_result
                append_download_log(
                    plan.output_dir,
                    planned.fastq.run_accession,
                    planned.fastq.file_name,
                    status,
                    planned.fastq.expected_md5,
                    actual_md5,
                    planned.fastq.size_bytes,
                    downloaded,
                    message,
                )
                _emit(message_callback, f"{status}: {planned.fastq.file_name}")
                results.append((planned, status, message))
                continue

            part_path, downloaded = download_one(
                planned,
                progress_callback=progress_callback,
                message_callback=message_callback,
            )
            if not planned.fastq.expected_md5:
                status = MD5_UNAVAILABLE
                message = ERROR_MESSAGES[status]
                actual_md5 = calculate_md5(part_path)
                _finalize_part(part_path, planned.local_path)
            else:
                ok, actual_md5 = verify_md5(part_path, planned.fastq.expected_md5)
                status = MD5_VERIFIED if ok else MD5_MISMATCH
                message = ERROR_MESSAGES[status]
                if ok:
                    _finalize_part(part_path, planned.local_path)
                else:
                    quarantined = _quarantine_file(part_path, "bad-md5")
                    message = f"{message} The mismatched file was moved aside instead of being saved under the final name: {quarantined}"
            append_download_log(
                plan.output_dir,
                planned.fastq.run_accession,
                planned.fastq.file_name,
                status,
                planned.fastq.expected_md5,
                actual_md5,
                planned.fastq.size_bytes,
                downloaded,
                message,
            )
            _emit(message_callback, f"{status}: {planned.fastq.file_name}")
            results.append((planned, status, message))
        except DownloadSizeMismatchError as exc:
            status = SIZE_MISMATCH
            part_path = _part_path(planned.local_path)
            downloaded = _existing_size(part_path)
            message = ERROR_MESSAGES[status]
            if part_path.exists():
                quarantined = _quarantine_file(part_path, "size-mismatch")
                message = f"{message} Quarantine path: {quarantined}"
            append_download_log(
                plan.output_dir,
                planned.fastq.run_accession,
                planned.fastq.file_name,
                status,
                planned.fastq.expected_md5,
                "",
                planned.fastq.size_bytes,
                downloaded,
                f"{message} Detail: {exc}",
            )
            _emit(message_callback, f"{status}: {planned.fastq.file_name}")
            results.append((planned, status, str(exc)))
        except (urllib.error.URLError, http.client.HTTPException, ValueError) as exc:
            status = NETWORK_FAILED
            message = ERROR_MESSAGES[status]
            append_download_log(
                plan.output_dir,
                planned.fastq.run_accession,
                planned.fastq.file_name,
                status,
                planned.fastq.expected_md5,
                "",
                planned.fastq.size_bytes,
                _existing_size(_part_path(planned.local_path)),
                f"{message} Detail: {exc}",
            )
            _emit(message_callback, f"{status}: {planned.fastq.file_name}")
            results.append((planned, status, str(exc)))
        except OSError as exc:
            status = NETWORK_FAILED
            append_download_log(
                plan.output_dir,
                planned.fastq.run_accession,
                planned.fastq.file_name,
                status,
                planned.fastq.expected_md5,
                "",
                planned.fastq.size_bytes,
                _existing_size(_part_path(planned.local_path)),
                str(exc),
            )
            _emit(message_callback, f"{status}: {planned.fastq.file_name}")
            results.append((planned, status, str(exc)))

    return results


def download_one(
    planned: PlannedFile,
    progress_callback: ProgressCallback | None = None,
    message_callback: MessageCallback | None = None,
    chunk_size: int = 1024 * 1024,
) -> tuple[Path, int]:
    def progress(downloaded: int, total: int) -> None:
        if progress_callback:
            progress_callback(planned, downloaded, total)

    return download_url_to_part(
        planned.fastq.url,
        planned.local_path,
        expected_size=planned.fastq.size_bytes,
        progress_callback=progress,
        message_callback=message_callback,
        chunk_size=chunk_size,
    )


def download_url_to_part(
    url: str,
    local_path: Path,
    expected_size: int = 0,
    progress_callback: ByteProgressCallback | None = None,
    message_callback: MessageCallback | None = None,
    chunk_size: int = 1024 * 1024,
    max_retries: int = 3,
) -> tuple[Path, int]:
    part_path = _part_path(local_path)
    last_error: BaseException | None = None
    for attempt in range(1, max_retries + 1):
        try:
            return _download_url_to_part_once(
                url,
                part_path,
                expected_size=expected_size,
                progress_callback=progress_callback,
                chunk_size=chunk_size,
            )
        except DownloadSizeMismatchError:
            raise
        except (urllib.error.URLError, http.client.HTTPException, OSError) as exc:
            last_error = exc
            if attempt >= max_retries:
                raise
            _emit(message_callback, f"network_retry: retrying after transfer failure ({attempt + 1}/{max_retries})")
    raise last_error or OSError("Download failed.")


def finalize_downloaded_part(local_path: Path) -> None:
    _finalize_part(_part_path(local_path), local_path)


def _download_url_to_part_once(
    url: str,
    part_path: Path,
    expected_size: int = 0,
    progress_callback: ByteProgressCallback | None = None,
    chunk_size: int = 1024 * 1024,
) -> tuple[Path, int]:
    part_path.parent.mkdir(parents=True, exist_ok=True)
    resume_from = _existing_size(part_path)
    if expected_size > 0 and resume_from > expected_size:
        raise DownloadSizeMismatchError(
            f"Partial file size exceeds expected size: expected={expected_size} actual={resume_from}"
        )
    headers = {"User-Agent": USER_AGENT}
    if resume_from > 0:
        headers["Range"] = f"bytes={resume_from}-"
    request = urllib.request.Request(url, headers=headers)
    downloaded = 0
    with urllib.request.urlopen(request, timeout=120) as response:
        status = _status_code(response)
        appending = resume_from > 0 and status == 206
        if appending:
            _validate_content_range(response, resume_from)
        if not appending:
            resume_from = 0
        total = _content_length(response)
        if total and appending:
            total += resume_from
        if not total:
            total = expected_size
        mode = "ab" if appending else "wb"
        with part_path.open(mode) as handle:
            while True:
                chunk = response.read(chunk_size)
                if not chunk:
                    break
                handle.write(chunk)
                downloaded += len(chunk)
                if progress_callback:
                    progress_callback(resume_from + downloaded, total)
    final_size = _existing_size(part_path)
    if expected_size > 0 and final_size < expected_size:
        raise OSError(f"Downloaded size is smaller than expected: expected={expected_size} actual={final_size}")
    if expected_size > 0 and final_size > expected_size:
        raise DownloadSizeMismatchError(
            f"Downloaded size exceeds expected size: expected={expected_size} actual={final_size}"
        )
    return part_path, final_size


def _content_length(response: object) -> int:
    headers = getattr(response, "headers", {})
    try:
        return int(headers.get("Content-Length") or 0)
    except (TypeError, ValueError):
        return 0


def _validate_content_range(response: object, expected_start: int) -> None:
    headers = getattr(response, "headers", {})
    content_range = headers.get("Content-Range") if headers is not None else None
    start = _content_range_start(content_range)
    if start != expected_start:
        raise OSError(f"Invalid Content-Range for resume: expected_start={expected_start} content_range={content_range!r}")


def _content_range_start(value: object) -> int | None:
    if not value:
        return None
    match = re.match(r"^bytes\s+(\d+)-\d+/(?:\d+|\*)$", str(value).strip(), re.IGNORECASE)
    if not match:
        return None
    return int(match.group(1))


def _status_code(response: object) -> int:
    try:
        return int(response.getcode() or 0)
    except (AttributeError, TypeError, ValueError):
        return 0


def _existing_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _emit(callback: MessageCallback | None, message: str) -> None:
    if callback:
        callback(message)


def _reuse_or_quarantine_existing(
    planned: PlannedFile,
    message_callback: MessageCallback | None = None,
) -> tuple[str, str, str, int] | None:
    if not planned.local_path.exists():
        return None
    if planned.fastq.expected_md5:
        ok, actual_md5 = verify_md5(planned.local_path, planned.fastq.expected_md5)
        if ok:
            stale_part = _part_path(planned.local_path)
            if stale_part.exists():
                stale_part.unlink()
            return (
                MD5_VERIFIED,
                "Existing file MD5 matched, so the file was reused without downloading again.",
                actual_md5,
                _existing_size(planned.local_path),
            )
        quarantined = _quarantine_file(planned.local_path, "bad-md5-existing")
        _emit(message_callback, f"existing_file_quarantined_bad_md5: {quarantined}")
        return None

    quarantined = _quarantine_file(planned.local_path, "unverified-existing")
    _emit(message_callback, f"existing_file_quarantined_unverified: {quarantined}")
    return None


def _reuse_or_quarantine_complete_part(
    planned: PlannedFile,
    message_callback: MessageCallback | None = None,
) -> tuple[str, str, str, int] | None:
    part_path = _part_path(planned.local_path)
    if not part_path.exists():
        return None
    part_size = _existing_size(part_path)
    if planned.fastq.size_bytes > 0 and part_size < planned.fastq.size_bytes:
        return None
    if planned.fastq.size_bytes > 0 and part_size > planned.fastq.size_bytes:
        raise DownloadSizeMismatchError(
            f"Partial file size exceeds expected size: expected={planned.fastq.size_bytes} actual={part_size}"
        )
    if not planned.fastq.expected_md5:
        if planned.fastq.size_bytes > 0 and part_size == planned.fastq.size_bytes:
            actual_md5 = calculate_md5(part_path)
            _finalize_part(part_path, planned.local_path)
            return (
                MD5_UNAVAILABLE,
                "Previous partial file size matched, so it was promoted to the final file name without MD5 verification.",
                actual_md5,
                part_size,
            )
        return None
    ok, actual_md5 = verify_md5(part_path, planned.fastq.expected_md5)
    if ok:
        _finalize_part(part_path, planned.local_path)
        return (
            MD5_VERIFIED,
            "Previous partial file MD5 matched, so it was promoted to the final file name.",
            actual_md5,
            part_size,
        )
    if planned.fastq.size_bytes == 0 or part_size >= planned.fastq.size_bytes:
        quarantined = _quarantine_file(part_path, "bad-md5")
        _emit(message_callback, f"partial_file_quarantined_bad_md5: {quarantined}")
    return None


def _finalize_part(part_path: Path, local_path: Path) -> None:
    local_path.parent.mkdir(parents=True, exist_ok=True)
    part_path.replace(local_path)


def _part_path(local_path: Path) -> Path:
    return local_path.with_name(local_path.name + ".part")


def _quarantine_file(path: Path, reason: str) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    candidate = path.with_name(f"{path.name}.{reason}-{timestamp}")
    counter = 2
    while candidate.exists():
        candidate = path.with_name(f"{path.name}.{reason}-{timestamp}.{counter}")
        counter += 1
    path.replace(candidate)
    return candidate
