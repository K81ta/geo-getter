from __future__ import annotations

import http.client
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
import urllib.error
import urllib.request

from .errors import (
    ERROR_MESSAGES,
    GeoGetterError,
    MD5_MISMATCH,
    MD5_UNAVAILABLE,
    MD5_VERIFIED,
    NETWORK_FAILED,
    OUTPUT_PATH_INVALID,
    SIZE_MISMATCH,
)
from .hashing import calculate_md5, new_digest, verify_md5
from .http_client import USER_AGENT
from .models import DownloadPlan, PlannedFile
from .planner import ResumeArtifactDigest, ResumeArtifacts, append_download_log, ensure_capacity, write_fastq_outputs

ProgressCallback = Callable[[PlannedFile, int, int], None]
MessageCallback = Callable[[str], None]
ByteProgressCallback = Callable[[int, int], None]
ResumeDigestLookup = dict[tuple[Path, str], ResumeArtifactDigest]


class DownloadSizeMismatchError(Exception):
    pass


@dataclass(frozen=True)
class DownloadedPart:
    path: Path
    bytes_downloaded: int
    streamed_md5: str | None = None
    resumed: bool = False


@dataclass(frozen=True)
class DownloadOutcome:
    status: str
    message: str
    actual_md5: str = ""
    bytes_downloaded: int = 0
    result_message: str | None = None


def download_plan(
    plan: DownloadPlan,
    progress_callback: ProgressCallback | None = None,
    message_callback: MessageCallback | None = None,
    resume_artifacts: ResumeArtifacts | None = None,
) -> list[tuple[PlannedFile, str, str]]:
    ensure_capacity(plan, required_bytes=resume_artifacts.required_bytes if resume_artifacts else None)
    write_fastq_outputs(plan, resume_artifacts=resume_artifacts)
    resume_digests = _resume_digest_lookup(resume_artifacts)
    results: list[tuple[PlannedFile, str, str]] = []

    for planned in plan.files:
        try:
            _emit(message_callback, f"download_started: {planned.fastq.file_name}")
            existing_result = _reuse_or_quarantine_existing(planned, resume_digests, message_callback)
            if existing_result:
                _record_outcome(plan, planned, existing_result, results, message_callback)
                continue

            part_result = _reuse_or_quarantine_complete_part(planned, resume_digests, message_callback)
            if part_result:
                _record_outcome(plan, planned, part_result, results, message_callback)
                continue

            downloaded_part = download_one(
                planned,
                progress_callback=progress_callback,
                message_callback=message_callback,
            )
            outcome = _downloaded_part_outcome(planned, downloaded_part)
            _record_outcome(plan, planned, outcome, results, message_callback)
        except DownloadSizeMismatchError as exc:
            status = SIZE_MISMATCH
            part_path = _part_path(planned.local_path)
            downloaded = _existing_size(part_path)
            message = ERROR_MESSAGES[status]
            if part_path.exists():
                quarantined = _quarantine_file(part_path, "size-mismatch")
                message = f"{message} Quarantine path: {quarantined}"
            _record_outcome(
                plan,
                planned,
                DownloadOutcome(
                    status,
                    f"{message} Detail: {exc}",
                    bytes_downloaded=downloaded,
                    result_message=str(exc),
                ),
                results,
                message_callback,
            )
        except (urllib.error.URLError, http.client.HTTPException, ValueError) as exc:
            status = NETWORK_FAILED
            message = ERROR_MESSAGES[status]
            _record_outcome(
                plan,
                planned,
                DownloadOutcome(
                    status,
                    f"{message} Detail: {exc}",
                    bytes_downloaded=_existing_size(_part_path(planned.local_path)),
                    result_message=str(exc),
                ),
                results,
                message_callback,
            )
        except OSError as exc:
            status = NETWORK_FAILED
            _record_outcome(
                plan,
                planned,
                DownloadOutcome(
                    status,
                    str(exc),
                    bytes_downloaded=_existing_size(_part_path(planned.local_path)),
                    result_message=str(exc),
                ),
                results,
                message_callback,
            )

    return results


def _record_outcome(
    plan: DownloadPlan,
    planned: PlannedFile,
    outcome: DownloadOutcome,
    results: list[tuple[PlannedFile, str, str]],
    message_callback: MessageCallback | None,
) -> None:
    append_download_log(
        plan.output_dir,
        planned.fastq.run_accession,
        planned.fastq.file_name,
        outcome.status,
        planned.fastq.expected_md5,
        outcome.actual_md5,
        planned.fastq.size_bytes,
        outcome.bytes_downloaded,
        outcome.message,
    )
    _emit(message_callback, f"{outcome.status}: {planned.fastq.file_name}")
    result_message = outcome.result_message if outcome.result_message is not None else outcome.message
    results.append((planned, outcome.status, result_message))


def _downloaded_part_outcome(planned: PlannedFile, downloaded_part: DownloadedPart) -> DownloadOutcome:
    part_path = downloaded_part.path
    downloaded = downloaded_part.bytes_downloaded
    if not planned.fastq.expected_md5:
        actual_md5 = downloaded_part.streamed_md5 if downloaded_part.streamed_md5 else calculate_md5(part_path)
        _finalize_part(part_path, planned.local_path)
        return DownloadOutcome(MD5_UNAVAILABLE, ERROR_MESSAGES[MD5_UNAVAILABLE], actual_md5, downloaded)

    if downloaded_part.streamed_md5 and not downloaded_part.resumed:
        actual_md5 = downloaded_part.streamed_md5
        ok = actual_md5.lower() == planned.fastq.expected_md5.lower()
    else:
        ok, actual_md5 = verify_md5(part_path, planned.fastq.expected_md5)
    if ok:
        _finalize_part(part_path, planned.local_path)
        return DownloadOutcome(MD5_VERIFIED, ERROR_MESSAGES[MD5_VERIFIED], actual_md5, downloaded)

    quarantined = _quarantine_file(part_path, "bad-md5")
    message = (
        f"{ERROR_MESSAGES[MD5_MISMATCH]} "
        f"The mismatched file was moved aside instead of being saved under the final name: {quarantined}"
    )
    return DownloadOutcome(MD5_MISMATCH, message, actual_md5, downloaded)


def download_one(
    planned: PlannedFile,
    progress_callback: ProgressCallback | None = None,
    message_callback: MessageCallback | None = None,
    chunk_size: int = 1024 * 1024,
) -> DownloadedPart:
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
        stream_md5=True,
    )


def download_url_to_part(
    url: str,
    local_path: Path,
    expected_size: int = 0,
    progress_callback: ByteProgressCallback | None = None,
    message_callback: MessageCallback | None = None,
    chunk_size: int = 1024 * 1024,
    max_retries: int = 3,
    stream_md5: bool = False,
) -> DownloadedPart:
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
                stream_md5=stream_md5,
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
    stream_md5: bool = False,
) -> DownloadedPart:
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
    streamed_digest = None
    resumed = False
    with urllib.request.urlopen(request, timeout=120) as response:
        status = _status_code(response)
        appending = resume_from > 0 and status == 206
        if appending:
            _validate_content_range(response, resume_from)
            resumed = True
        if not appending:
            resume_from = 0
            if stream_md5:
                streamed_digest = new_digest("md5")
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
                if streamed_digest:
                    streamed_digest.update(chunk)
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
    streamed_md5 = streamed_digest.hexdigest() if streamed_digest else None
    return DownloadedPart(part_path, final_size, streamed_md5=streamed_md5, resumed=resumed)


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
        if not path.is_file():
            return 0
        return path.stat().st_size
    except OSError:
        return 0


def _emit(callback: MessageCallback | None, message: str) -> None:
    if callback:
        callback(message)


def _resume_digest_lookup(resume_artifacts: ResumeArtifacts | None) -> ResumeDigestLookup:
    if not resume_artifacts:
        return {}
    return {(artifact.path, artifact.kind): artifact for artifact in resume_artifacts.verified_artifacts}


def _cached_resume_md5(
    lookup: ResumeDigestLookup,
    path: Path,
    kind: str,
    expected_md5: str,
    size_bytes: int,
) -> str | None:
    artifact = lookup.get((path, kind))
    if not artifact:
        return None
    if artifact.expected_md5.lower() != expected_md5.lower():
        return None
    if artifact.size_bytes != size_bytes:
        return None
    try:
        if path.stat().st_mtime_ns != artifact.mtime_ns:
            return None
    except OSError:
        return None
    return artifact.actual_md5


def _reuse_or_quarantine_existing(
    planned: PlannedFile,
    resume_digests: ResumeDigestLookup,
    message_callback: MessageCallback | None = None,
) -> DownloadOutcome | None:
    if not planned.local_path.exists():
        return None
    if not planned.local_path.is_file():
        raise GeoGetterError(OUTPUT_PATH_INVALID, f"download_target_is_not_file path={planned.local_path}")
    existing_size = _existing_size(planned.local_path)
    if planned.fastq.expected_md5:
        if planned.fastq.size_bytes > 0 and existing_size != planned.fastq.size_bytes:
            quarantined = _quarantine_file(planned.local_path, "size-mismatch-existing")
            _emit(message_callback, f"existing_file_quarantined_size_mismatch: {quarantined}")
            return None
        actual_md5 = _cached_resume_md5(
            resume_digests,
            planned.local_path,
            "final",
            planned.fastq.expected_md5,
            existing_size,
        )
        ok = actual_md5 is not None
        if actual_md5 is None:
            ok, actual_md5 = verify_md5(planned.local_path, planned.fastq.expected_md5)
        if ok:
            stale_part = _part_path(planned.local_path)
            if stale_part.exists():
                stale_part.unlink()
            return DownloadOutcome(
                MD5_VERIFIED,
                "Existing file MD5 matched, so the file was reused without downloading again.",
                actual_md5,
                existing_size,
            )
        quarantined = _quarantine_file(planned.local_path, "bad-md5-existing")
        _emit(message_callback, f"existing_file_quarantined_bad_md5: {quarantined}")
        return None

    quarantined = _quarantine_file(planned.local_path, "unverified-existing")
    _emit(message_callback, f"existing_file_quarantined_unverified: {quarantined}")
    return None


def _reuse_or_quarantine_complete_part(
    planned: PlannedFile,
    resume_digests: ResumeDigestLookup,
    message_callback: MessageCallback | None = None,
) -> DownloadOutcome | None:
    part_path = _part_path(planned.local_path)
    if not part_path.exists():
        return None
    if not part_path.is_file():
        raise GeoGetterError(OUTPUT_PATH_INVALID, f"partial_download_target_is_not_file path={part_path}")
    part_size = _existing_size(part_path)
    if planned.fastq.size_bytes > 0 and part_size < planned.fastq.size_bytes:
        return None
    if planned.fastq.size_bytes > 0 and part_size > planned.fastq.size_bytes:
        raise DownloadSizeMismatchError(
            f"Partial file size exceeds expected size: expected={planned.fastq.size_bytes} actual={part_size}"
        )
    if not planned.fastq.expected_md5:
        if planned.fastq.size_bytes > 0 and part_size == planned.fastq.size_bytes:
            quarantined = _quarantine_file(part_path, "unverified-existing")
            _emit(message_callback, f"partial_file_quarantined_unverified: {quarantined}")
        return None
    actual_md5 = _cached_resume_md5(
        resume_digests,
        part_path,
        "complete_part",
        planned.fastq.expected_md5,
        part_size,
    )
    ok = actual_md5 is not None
    if actual_md5 is None:
        ok, actual_md5 = verify_md5(part_path, planned.fastq.expected_md5)
    if ok:
        _finalize_part(part_path, planned.local_path)
        return DownloadOutcome(
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
