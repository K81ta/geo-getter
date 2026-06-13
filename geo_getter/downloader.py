from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import http.client
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Callable
import urllib.error
import urllib.request

from .errors import (
    ERROR_MESSAGES,
    GeoGetterError,
    LOCAL_IO_FAILED,
    MD5_MISMATCH,
    MD5_UNAVAILABLE,
    MD5_VERIFIED,
    NETWORK_FAILED,
    OUTPUT_PATH_INVALID,
    SIZE_MISMATCH,
)
from .hashing import verify_md5
from .http_client import USER_AGENT
from .models import DownloadPlan, PlannedFile
from .path_safety import download_part_path, existing_size, quarantine_candidate_path
from .planner import ResumeArtifactDigest, ResumeArtifacts, append_download_log, ensure_capacity, write_fastq_outputs

ProgressCallback = Callable[[PlannedFile, int, int], None]
MessageCallback = Callable[[str], None]
ByteProgressCallback = Callable[[int, int], None]
SleepCallback = Callable[[float], None]
NowCallback = Callable[[], datetime]
ResumeDigestLookup = dict[tuple[Path, str], ResumeArtifactDigest]
DEFAULT_RETRY_DELAYS = (1.0, 3.0, 9.0)
DEFAULT_DOWNLOAD_CHUNK_SIZE = 4 * 1024 * 1024
DEFAULT_PROGRESS_MIN_INTERVAL_SECONDS = 0.250
DEFAULT_PROGRESS_MIN_BYTES = 16 * 1024 * 1024
DEFAULT_DOWNLOAD_WORKERS = 2
MAX_DOWNLOAD_WORKERS = 4


class DownloadSizeMismatchError(Exception):
    pass


class DownloadNetworkError(OSError):
    def __init__(
        self,
        message: str,
        *,
        retryable: bool = True,
        retry_after: float | None = None,
        status_code: int | None = None,
    ):
        super().__init__(message)
        self.retryable = retryable
        self.retry_after = retry_after
        self.status_code = status_code


class DownloadLocalIoError(OSError):
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


class _ProgressReporter:
    def __init__(
        self,
        callback: ByteProgressCallback | None,
        *,
        initial_downloaded: int,
        min_interval_seconds: float,
        min_bytes: int,
        now_func: NowCallback,
    ):
        self.callback = callback
        self.min_interval_seconds = max(0.0, min_interval_seconds)
        self.min_bytes = max(1, min_bytes)
        self.now_func = now_func
        self.last_emitted_downloaded = initial_downloaded
        self.last_emitted_at = now_func() if callback else None
        self.last_event: tuple[int, int] | None = None

    def emit(self, downloaded: int, total: int, *, force: bool = False) -> None:
        if not self.callback:
            return
        if force and self.last_event == (downloaded, total):
            return
        now = self.now_func()
        byte_delta = downloaded - self.last_emitted_downloaded
        elapsed = (now - self.last_emitted_at).total_seconds() if self.last_emitted_at else 0.0
        if force or byte_delta >= self.min_bytes or elapsed >= self.min_interval_seconds:
            self.callback(downloaded, total)
            self.last_emitted_downloaded = downloaded
            self.last_emitted_at = now
            self.last_event = (downloaded, total)


def download_plan(
    plan: DownloadPlan,
    progress_callback: ProgressCallback | None = None,
    message_callback: MessageCallback | None = None,
    resume_artifacts: ResumeArtifacts | None = None,
    download_workers: int = DEFAULT_DOWNLOAD_WORKERS,
) -> list[tuple[PlannedFile, str, str]]:
    ensure_capacity(plan, required_bytes=resume_artifacts.required_bytes if resume_artifacts else None)
    write_fastq_outputs(plan, resume_artifacts=resume_artifacts)
    resume_digests = _resume_digest_lookup(resume_artifacts)
    results: list[tuple[PlannedFile, str, str]] = []
    worker_count = normalize_download_workers(download_workers)

    if worker_count == 1 or len(plan.files) <= 1:
        for planned in plan.files:
            outcome = _download_planned_file(planned, resume_digests, progress_callback, message_callback)
            _record_outcome(plan, planned, outcome, results, message_callback)
        return results

    worker_count = min(worker_count, len(plan.files))
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = [
            executor.submit(_download_planned_file, planned, resume_digests, progress_callback, message_callback)
            for planned in plan.files
        ]
        for planned, future in zip(plan.files, futures):
            outcome = future.result()
            _record_outcome(plan, planned, outcome, results, message_callback)

    return results


def normalize_download_workers(value: int) -> int:
    try:
        worker_count = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"download_workers must be an integer from 1 to {MAX_DOWNLOAD_WORKERS}.") from exc
    if worker_count < 1 or worker_count > MAX_DOWNLOAD_WORKERS:
        raise ValueError(f"download_workers must be from 1 to {MAX_DOWNLOAD_WORKERS}: {worker_count}")
    return worker_count


def _download_planned_file(
    planned: PlannedFile,
    resume_digests: ResumeDigestLookup,
    progress_callback: ProgressCallback | None,
    message_callback: MessageCallback | None,
) -> DownloadOutcome:
    try:
        _emit(message_callback, f"download_started: {planned.fastq.file_name}")
        existing_result = _reuse_or_quarantine_existing(planned, resume_digests, message_callback)
        if existing_result:
            return existing_result

        part_result = _reuse_or_quarantine_complete_part(planned, resume_digests, message_callback)
        if part_result:
            return part_result

        downloaded_part = download_one(
            planned,
            progress_callback=progress_callback,
            message_callback=message_callback,
        )
        return _downloaded_part_outcome(planned, downloaded_part)
    except DownloadSizeMismatchError as exc:
        status = SIZE_MISMATCH
        part_path = download_part_path(planned.local_path)
        downloaded = existing_size(part_path)
        message = ERROR_MESSAGES[status]
        try:
            if part_path.exists():
                quarantined = _quarantine_file(part_path, "size-mismatch")
                message = f"{message} Quarantine path: {quarantined}"
        except DownloadLocalIoError as io_exc:
            status = LOCAL_IO_FAILED
            message = f"{ERROR_MESSAGES[status]} Detail: {io_exc}"
        return DownloadOutcome(
            status,
            f"{message} Detail: {exc}",
            bytes_downloaded=downloaded,
            result_message=str(exc),
        )
    except DownloadNetworkError as exc:
        status = NETWORK_FAILED
        message = ERROR_MESSAGES[status]
        return DownloadOutcome(
            status,
            f"{message} Detail: {exc}",
            bytes_downloaded=existing_size(download_part_path(planned.local_path)),
            result_message=str(exc),
        )
    except DownloadLocalIoError as exc:
        status = LOCAL_IO_FAILED
        message = ERROR_MESSAGES[status]
        return DownloadOutcome(
            status,
            f"{message} Detail: {exc}",
            bytes_downloaded=existing_size(download_part_path(planned.local_path)),
            result_message=str(exc),
        )
    except OSError as exc:
        status = LOCAL_IO_FAILED
        message = ERROR_MESSAGES[status]
        return DownloadOutcome(
            status,
            f"{message} Detail: {exc}",
            bytes_downloaded=existing_size(download_part_path(planned.local_path)),
            result_message=str(exc),
        )


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
        _finalize_part(part_path, planned.local_path)
        return DownloadOutcome(MD5_UNAVAILABLE, ERROR_MESSAGES[MD5_UNAVAILABLE], "", downloaded)

    if downloaded_part.streamed_md5 and not downloaded_part.resumed:
        actual_md5 = downloaded_part.streamed_md5
        ok = actual_md5.lower() == planned.fastq.expected_md5.lower()
    else:
        try:
            ok, actual_md5 = verify_md5(part_path, planned.fastq.expected_md5)
        except OSError as exc:
            raise DownloadLocalIoError(f"Could not read partial FASTQ for MD5 verification: {part_path}") from exc
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
    chunk_size: int = DEFAULT_DOWNLOAD_CHUNK_SIZE,
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
        stream_md5=bool(planned.fastq.expected_md5),
    )


def download_url_to_part(
    url: str,
    local_path: Path,
    expected_size: int = 0,
    progress_callback: ByteProgressCallback | None = None,
    message_callback: MessageCallback | None = None,
    chunk_size: int = DEFAULT_DOWNLOAD_CHUNK_SIZE,
    max_attempts: int = 4,
    stream_md5: bool = False,
    retry_delays: tuple[float, ...] = DEFAULT_RETRY_DELAYS,
    sleep_func: SleepCallback | None = None,
    now_func: NowCallback | None = None,
    progress_min_interval_seconds: float = DEFAULT_PROGRESS_MIN_INTERVAL_SECONDS,
    progress_min_bytes: int = DEFAULT_PROGRESS_MIN_BYTES,
) -> DownloadedPart:
    if max_attempts < 1:
        raise ValueError("max_attempts must be positive.")
    part_path = download_part_path(local_path)
    last_error: BaseException | None = None
    sleep = sleep_func or time.sleep
    now = now_func or _utc_now
    for attempt in range(1, max_attempts + 1):
        try:
            return _download_url_to_part_once(
                url,
                part_path,
                expected_size=expected_size,
                progress_callback=progress_callback,
                chunk_size=chunk_size,
                stream_md5=stream_md5,
                now_func=now,
                progress_min_interval_seconds=progress_min_interval_seconds,
                progress_min_bytes=progress_min_bytes,
            )
        except DownloadSizeMismatchError:
            raise
        except DownloadLocalIoError:
            raise
        except DownloadNetworkError as exc:
            last_error = exc
            if not exc.retryable or attempt >= max_attempts:
                raise
            delay = _retry_delay_seconds(exc, attempt, retry_delays)
            _emit(
                message_callback,
                f"network_retry: waiting {delay:g}s before retry ({attempt + 1}/{max_attempts}) after {exc}",
            )
            sleep(delay)
    raise last_error or DownloadNetworkError("Download failed.")


def finalize_downloaded_part(local_path: Path) -> None:
    _finalize_part(download_part_path(local_path), local_path)


def _download_url_to_part_once(
    url: str,
    part_path: Path,
    expected_size: int = 0,
    progress_callback: ByteProgressCallback | None = None,
    chunk_size: int = DEFAULT_DOWNLOAD_CHUNK_SIZE,
    stream_md5: bool = False,
    now_func: NowCallback | None = None,
    progress_min_interval_seconds: float = DEFAULT_PROGRESS_MIN_INTERVAL_SECONDS,
    progress_min_bytes: int = DEFAULT_PROGRESS_MIN_BYTES,
) -> DownloadedPart:
    if chunk_size <= 0:
        raise ValueError(f"chunk_size must be positive: {chunk_size}")
    try:
        part_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise DownloadLocalIoError(f"Could not create output folder: {part_path.parent}") from exc
    resume_from = existing_size(part_path)
    if expected_size > 0 and resume_from > expected_size:
        raise DownloadSizeMismatchError(
            f"Partial file size exceeds expected size: expected={expected_size} actual={resume_from}"
        )
    headers = {"User-Agent": USER_AGENT}
    if resume_from > 0:
        headers["Range"] = f"bytes={resume_from}-"
    try:
        request = urllib.request.Request(url, headers=headers)
    except ValueError as exc:
        raise DownloadNetworkError(str(exc), retryable=False) from exc
    downloaded = 0
    streamed_digest = None
    resumed = False
    now = now_func or _utc_now
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            status = _status_code(response)
            appending = resume_from > 0 and status == 206
            if appending:
                _validate_content_range(response, resume_from)
                resumed = True
            if not appending:
                resume_from = 0
                if stream_md5:
                    streamed_digest = hashlib.md5()
            total = _content_length(response)
            if total and appending:
                total += resume_from
            if not total:
                total = expected_size
            progress_reporter = _ProgressReporter(
                progress_callback,
                initial_downloaded=resume_from,
                min_interval_seconds=progress_min_interval_seconds,
                min_bytes=progress_min_bytes,
                now_func=now,
            )
            mode = "ab" if appending else "wb"
            try:
                handle = part_path.open(mode)
            except OSError as exc:
                raise DownloadLocalIoError(f"Could not open partial download file: {part_path}") from exc
            try:
                with handle:
                    while True:
                        try:
                            chunk = response.read(chunk_size)
                        except (urllib.error.URLError, http.client.HTTPException, OSError) as exc:
                            raise DownloadNetworkError(str(exc)) from exc
                        if not chunk:
                            break
                        try:
                            handle.write(chunk)
                        except OSError as exc:
                            raise DownloadLocalIoError(f"Could not write partial download file: {part_path}") from exc
                        if streamed_digest:
                            streamed_digest.update(chunk)
                        downloaded += len(chunk)
                        progress_reporter.emit(resume_from + downloaded, total)
                    progress_reporter.emit(resume_from + downloaded, total, force=True)
            except (DownloadNetworkError, DownloadLocalIoError):
                raise
            except OSError as exc:
                raise DownloadLocalIoError(f"Could not close partial download file: {part_path}") from exc
    except (DownloadSizeMismatchError, DownloadNetworkError, DownloadLocalIoError):
        raise
    except urllib.error.HTTPError as exc:
        raise _http_network_error(exc, now) from exc
    except urllib.error.URLError as exc:
        raise _url_network_error(exc) from exc
    except (http.client.HTTPException, OSError, ValueError) as exc:
        raise DownloadNetworkError(str(exc)) from exc
    final_size = existing_size(part_path)
    if expected_size > 0 and final_size < expected_size:
        raise DownloadSizeMismatchError(
            f"Downloaded size is smaller than expected: expected={expected_size} actual={final_size}"
        )
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
        raise DownloadNetworkError(
            f"Invalid Content-Range for resume: expected_start={expected_start} content_range={content_range!r}",
            retryable=False,
        )


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


def _http_network_error(error: urllib.error.HTTPError, now_func: NowCallback) -> DownloadNetworkError:
    status = int(getattr(error, "code", 0) or 0)
    retryable = status == 429 or status >= 500
    retry_after = _parse_retry_after(_header_value(getattr(error, "headers", None), "Retry-After"), now_func)
    message = str(error)
    try:
        error.close()
    except Exception:
        pass
    return DownloadNetworkError(message, retryable=retryable, retry_after=retry_after, status_code=status)


def _url_network_error(error: urllib.error.URLError) -> DownloadNetworkError:
    message = str(error)
    lower_message = message.lower()
    reason = getattr(error, "reason", None)
    retryable = not isinstance(reason, FileNotFoundError)
    retryable = retryable and "unknown url type" not in lower_message and "no host given" not in lower_message
    return DownloadNetworkError(message, retryable=retryable)


def _header_value(headers: object, name: str) -> str | None:
    if headers is None:
        return None
    try:
        value = headers.get(name)
    except AttributeError:
        return None
    return str(value) if value is not None else None


def _parse_retry_after(value: str | None, now_func: NowCallback) -> float | None:
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    try:
        return max(0.0, float(text))
    except ValueError:
        pass
    try:
        retry_at = parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError, OverflowError):
        return None
    if retry_at.tzinfo is None:
        retry_at = retry_at.replace(tzinfo=timezone.utc)
    now = now_func()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return max(0.0, (retry_at - now).total_seconds())


def _retry_delay_seconds(error: DownloadNetworkError, attempt: int, retry_delays: tuple[float, ...]) -> float:
    if error.retry_after is not None:
        return max(0.0, error.retry_after)
    if not retry_delays:
        return 0.0
    index = min(max(attempt - 1, 0), len(retry_delays) - 1)
    return max(0.0, float(retry_delays[index]))


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


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
    local_size = existing_size(planned.local_path)
    if planned.fastq.expected_md5:
        if planned.fastq.size_bytes > 0 and local_size != planned.fastq.size_bytes:
            quarantined = _quarantine_file(planned.local_path, "size-mismatch-existing")
            _emit(message_callback, f"existing_file_quarantined_size_mismatch: {quarantined}")
            return None
        actual_md5 = _cached_resume_md5(
            resume_digests,
            planned.local_path,
            "final",
            planned.fastq.expected_md5,
            local_size,
        )
        ok = actual_md5 is not None
        if actual_md5 is None:
            try:
                ok, actual_md5 = verify_md5(planned.local_path, planned.fastq.expected_md5)
            except OSError as exc:
                raise DownloadLocalIoError(f"Could not read existing FASTQ for MD5 verification: {planned.local_path}") from exc
        if ok:
            stale_part = download_part_path(planned.local_path)
            if stale_part.exists():
                try:
                    stale_part.unlink()
                except OSError as exc:
                    raise DownloadLocalIoError(f"Could not remove stale partial download: {stale_part}") from exc
            return DownloadOutcome(
                MD5_VERIFIED,
                "Existing file MD5 matched, so the file was reused without downloading again.",
                actual_md5,
                local_size,
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
    part_path = download_part_path(planned.local_path)
    if not part_path.exists():
        return None
    if not part_path.is_file():
        raise GeoGetterError(OUTPUT_PATH_INVALID, f"partial_download_target_is_not_file path={part_path}")
    part_size = existing_size(part_path)
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
        try:
            ok, actual_md5 = verify_md5(part_path, planned.fastq.expected_md5)
        except OSError as exc:
            raise DownloadLocalIoError(f"Could not read partial FASTQ for MD5 verification: {part_path}") from exc
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
    try:
        local_path.parent.mkdir(parents=True, exist_ok=True)
        part_path.replace(local_path)
    except OSError as exc:
        raise DownloadLocalIoError(f"Could not move partial download into place: {part_path} -> {local_path}") from exc


def _quarantine_file(path: Path, reason: str) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    candidate = quarantine_candidate_path(path, reason, timestamp)
    counter = 2
    try:
        while candidate.exists():
            candidate = quarantine_candidate_path(path, reason, timestamp, counter)
            counter += 1
        path.replace(candidate)
    except OSError as exc:
        raise DownloadLocalIoError(f"Could not move file aside: {path}") from exc
    return candidate
