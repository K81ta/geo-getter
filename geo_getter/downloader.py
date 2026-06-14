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
from typing import Callable, cast
import urllib.error
import urllib.request

from .errors import (
    DOWNLOAD_COMPLETE,
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
from .models import DownloadPlan, PlannedFile, PlannedSupplementaryFile
from .path_safety import download_part_path, existing_size, unique_existing_path, unique_quarantine_path
from .planner import ResumeArtifacts, append_download_log, ensure_capacity, write_fastq_outputs

ProgressCallback = Callable[[PlannedFile, int, int], None]
MessageCallback = Callable[[str], None]
ByteProgressCallback = Callable[[int, int], None]
SleepCallback = Callable[[float], None]
NowCallback = Callable[[], datetime]
SUPPLEMENTARY_DOWNLOAD_COMPLETE_MESSAGE = (
    "Saved GEO supplementary/processed file. It was not verified because GEO SOFT does not provide a stable expected MD5 value."
)
DEFAULT_RETRY_DELAYS = (1.0, 3.0, 9.0)
DEFAULT_DOWNLOAD_CHUNK_SIZE = 4 * 1024 * 1024
DEFAULT_PROGRESS_MIN_INTERVAL_SECONDS = 0.250
DEFAULT_PROGRESS_MIN_BYTES = 16 * 1024 * 1024
DEFAULT_DOWNLOAD_WORKERS = 2
MAX_DOWNLOAD_WORKERS = 4
_CANDIDATE_MISSING = "missing"
_CANDIDATE_SHORT = "short"
_CANDIDATE_VERIFIED = "verified"
_CANDIDATE_BAD_MD5 = "bad_md5"
_CANDIDATE_SIZE_MISMATCH = "size_mismatch"
_CANDIDATE_UNVERIFIED_COMPLETE = "unverified_complete"
_CANDIDATE_UNVERIFIED_INCOMPLETE = "unverified_incomplete"


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


@dataclass(frozen=True)
class _PlannedDownload:
    kind: str
    file_name: str
    local_path: Path
    run_accession: str
    url: str = ""
    expected_md5: str = ""
    expected_bytes: int = 0
    source: object | None = None


@dataclass(frozen=True)
class _PreparedDownloadRequest:
    request: urllib.request.Request
    resume_from: int


@dataclass(frozen=True)
class _ResponseTransferPlan:
    resume_from: int
    total: int
    file_mode: str
    streamed_digest: object | None
    resumed: bool


@dataclass(frozen=True)
class _CandidateInspection:
    path: Path
    kind: str
    status: str
    size: int = 0
    actual_md5: str = ""


_PlannedProgressCallback = Callable[[_PlannedDownload, int, int], None]
_DownloadHandler = Callable[[_PlannedDownload, _PlannedProgressCallback | None, MessageCallback | None], DownloadOutcome]


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
    worker_count = normalize_download_workers(download_workers)
    items = [_fastq_download_record(planned) for planned in plan.files]

    def progress(item: _PlannedDownload, downloaded: int, total: int) -> None:
        if progress_callback:
            progress_callback(_fastq_planned_file(item), downloaded, total)

    results = _execute_planned_downloads(
        plan.output_dir,
        items,
        _download_fastq_record,
        progress_callback=progress,
        message_callback=message_callback,
        download_workers=worker_count,
    )
    return [(_fastq_planned_file(item), status, message) for item, status, message in results]


def download_supplementary_files(
    output_dir: Path,
    planned_supplementary: list[PlannedSupplementaryFile],
    progress_callback: _PlannedProgressCallback | None = None,
    message_callback: MessageCallback | None = None,
) -> list[tuple[_PlannedDownload, str, str]]:
    items = [_supplementary_download_record(planned) for planned in planned_supplementary]
    return _execute_planned_downloads(
        output_dir,
        items,
        _download_supplementary_record,
        progress_callback=progress_callback,
        message_callback=message_callback,
    )


def normalize_download_workers(value: int) -> int:
    try:
        worker_count = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"download_workers must be an integer from 1 to {MAX_DOWNLOAD_WORKERS}.") from exc
    if worker_count < 1 or worker_count > MAX_DOWNLOAD_WORKERS:
        raise ValueError(f"download_workers must be from 1 to {MAX_DOWNLOAD_WORKERS}: {worker_count}")
    return worker_count


def _execute_planned_downloads(
    output_dir: Path,
    items: list[_PlannedDownload],
    download_handler: _DownloadHandler,
    progress_callback: _PlannedProgressCallback | None = None,
    message_callback: MessageCallback | None = None,
    download_workers: int = 1,
) -> list[tuple[_PlannedDownload, str, str]]:
    results: list[tuple[_PlannedDownload, str, str]] = []
    if not items:
        return results

    worker_count = max(1, min(download_workers, len(items)))
    if worker_count == 1:
        for item in items:
            outcome = download_handler(item, progress_callback, message_callback)
            _record_download_outcome(output_dir, item, outcome, results, message_callback)
        return results

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = [
            executor.submit(download_handler, item, progress_callback, message_callback)
            for item in items
        ]
        for item, future in zip(items, futures):
            outcome = future.result()
            _record_download_outcome(output_dir, item, outcome, results, message_callback)

    return results


def _fastq_download_record(planned: PlannedFile) -> _PlannedDownload:
    return _PlannedDownload(
        kind="fastq",
        file_name=planned.fastq.file_name,
        local_path=planned.local_path,
        run_accession=planned.fastq.run_accession,
        url=planned.fastq.url,
        expected_md5=planned.fastq.expected_md5,
        expected_bytes=planned.fastq.size_bytes,
        source=planned,
    )


def _supplementary_download_record(planned: PlannedSupplementaryFile) -> _PlannedDownload:
    return _PlannedDownload(
        kind="supplementary",
        file_name=planned.local_path.name,
        local_path=planned.local_path,
        run_accession="GEO_SUPPLEMENTARY",
        url=planned.supplementary.url,
        source=planned,
    )


def _fastq_planned_file(item: _PlannedDownload) -> PlannedFile:
    return cast(PlannedFile, item.source)


def _download_fastq_record(
    item: _PlannedDownload,
    progress_callback: _PlannedProgressCallback | None,
    message_callback: MessageCallback | None,
) -> DownloadOutcome:
    planned = _fastq_planned_file(item)

    def progress(_planned: PlannedFile, downloaded: int, total: int) -> None:
        if progress_callback:
            progress_callback(item, downloaded, total)

    return _download_planned_file(planned, progress, message_callback)


def _download_supplementary_record(
    item: _PlannedDownload,
    progress_callback: _PlannedProgressCallback | None,
    message_callback: MessageCallback | None,
) -> DownloadOutcome:
    _emit(message_callback, f"supplementary_download_started: {item.file_name}")
    try:
        if item.local_path.exists():
            item.local_path.replace(unique_existing_path(item.local_path))
    except OSError as exc:
        return download_error_outcome(item.local_path, exc)

    def progress(current: int, total: int) -> None:
        if progress_callback:
            progress_callback(item, current, total)

    return download_url_without_md5(
        item.url,
        item.local_path,
        progress_callback=progress,
        message_callback=message_callback,
        success_message=SUPPLEMENTARY_DOWNLOAD_COMPLETE_MESSAGE,
    )


def _download_planned_file(
    planned: PlannedFile,
    progress_callback: ProgressCallback | None,
    message_callback: MessageCallback | None,
) -> DownloadOutcome:
    try:
        _emit(message_callback, f"download_started: {planned.fastq.file_name}")
        existing_result = _reuse_or_quarantine_existing(planned, message_callback)
        if existing_result:
            return existing_result

        part_result = _reuse_or_quarantine_complete_part(planned, message_callback)
        if part_result:
            return part_result

        downloaded_part = download_one(
            planned,
            progress_callback=progress_callback,
            message_callback=message_callback,
        )
        return _downloaded_part_outcome(planned, downloaded_part)
    except DownloadSizeMismatchError as exc:
        part_path = download_part_path(planned.local_path)
        downloaded = existing_size(part_path)
        try:
            if part_path.exists():
                quarantined = _quarantine_file(part_path, "size-mismatch")
                return download_error_outcome(
                    planned.local_path,
                    exc,
                    bytes_downloaded=downloaded,
                    message_note=f"Quarantine path: {quarantined}",
                )
        except DownloadLocalIoError as io_exc:
            return download_error_outcome(planned.local_path, io_exc, bytes_downloaded=downloaded)
        return download_error_outcome(planned.local_path, exc, bytes_downloaded=downloaded)
    except (DownloadNetworkError, DownloadLocalIoError, OSError) as exc:
        return download_error_outcome(planned.local_path, exc)


def _record_download_outcome(
    output_dir: Path,
    item: _PlannedDownload,
    outcome: DownloadOutcome,
    results: list[tuple[_PlannedDownload, str, str]],
    message_callback: MessageCallback | None,
) -> None:
    append_download_log(
        output_dir,
        item.run_accession,
        item.file_name,
        outcome.status,
        item.expected_md5,
        outcome.actual_md5,
        item.expected_bytes,
        outcome.bytes_downloaded,
        outcome.message,
    )
    _emit(message_callback, f"{outcome.status}: {item.file_name}")
    result_message = outcome.result_message if outcome.result_message is not None else outcome.message
    results.append((item, outcome.status, result_message))


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
        stream_md5=bool(planned.fastq.expected_md5),
    )


def download_url_to_part(
    url: str,
    local_path: Path,
    expected_size: int = 0,
    progress_callback: ByteProgressCallback | None = None,
    message_callback: MessageCallback | None = None,
    stream_md5: bool = False,
) -> DownloadedPart:
    return _download_url_to_part_with_retries(
        url,
        local_path,
        expected_size=expected_size,
        progress_callback=progress_callback,
        message_callback=message_callback,
        stream_md5=stream_md5,
    )


def download_url_without_md5(
    url: str,
    local_path: Path,
    *,
    progress_callback: ByteProgressCallback | None = None,
    message_callback: MessageCallback | None = None,
    success_message: str = "Downloaded file was saved without MD5 verification.",
) -> DownloadOutcome:
    try:
        downloaded_part = download_url_to_part(
            url,
            local_path,
            progress_callback=progress_callback,
            message_callback=message_callback,
        )
        _finalize_part(downloaded_part.path, local_path)
        return DownloadOutcome(
            DOWNLOAD_COMPLETE,
            success_message,
            bytes_downloaded=downloaded_part.bytes_downloaded,
        )
    except (DownloadSizeMismatchError, DownloadNetworkError, DownloadLocalIoError, OSError) as exc:
        return download_error_outcome(local_path, exc)


def _download_url_to_part_with_retries(
    url: str,
    local_path: Path,
    expected_size: int = 0,
    progress_callback: ByteProgressCallback | None = None,
    message_callback: MessageCallback | None = None,
    stream_md5: bool = False,
    chunk_size: int = DEFAULT_DOWNLOAD_CHUNK_SIZE,
    max_attempts: int = 4,
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
    now = now_func or _utc_now
    prepared = _prepare_download_request(url, part_path, expected_size)
    try:
        with _open_download_response(prepared.request, now) as response:
            transfer = _plan_response_transfer(response, prepared.resume_from, expected_size, stream_md5)
            progress_reporter = _ProgressReporter(
                progress_callback,
                initial_downloaded=transfer.resume_from,
                min_interval_seconds=progress_min_interval_seconds,
                min_bytes=progress_min_bytes,
                now_func=now,
            )
            _stream_response_to_part(
                response,
                part_path,
                file_mode=transfer.file_mode,
                chunk_size=chunk_size,
                progress_reporter=progress_reporter,
                progress_base=transfer.resume_from,
                total=transfer.total,
                streamed_digest=transfer.streamed_digest,
            )
    except (DownloadSizeMismatchError, DownloadNetworkError, DownloadLocalIoError):
        raise
    except (http.client.HTTPException, OSError, ValueError) as exc:
        raise DownloadNetworkError(str(exc)) from exc

    final_size = _validated_download_size(part_path, expected_size)
    streamed_md5 = transfer.streamed_digest.hexdigest() if transfer.streamed_digest else None
    return DownloadedPart(part_path, final_size, streamed_md5=streamed_md5, resumed=transfer.resumed)


def _prepare_download_request(url: str, part_path: Path, expected_size: int) -> _PreparedDownloadRequest:
    _ensure_part_parent(part_path)
    resume_from = _resume_start(part_path, expected_size)
    return _PreparedDownloadRequest(_build_download_request(url, resume_from), resume_from)


def _ensure_part_parent(part_path: Path) -> None:
    try:
        part_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise DownloadLocalIoError(f"Could not create output folder: {part_path.parent}") from exc


def _resume_start(part_path: Path, expected_size: int) -> int:
    resume_from = existing_size(part_path)
    if expected_size > 0 and resume_from > expected_size:
        raise DownloadSizeMismatchError(
            f"Partial file size exceeds expected size: expected={expected_size} actual={resume_from}"
        )
    return resume_from


def _build_download_request(url: str, resume_from: int) -> urllib.request.Request:
    headers = _download_headers(resume_from)
    try:
        return urllib.request.Request(url, headers=headers)
    except ValueError as exc:
        raise DownloadNetworkError(str(exc), retryable=False) from exc


def _download_headers(resume_from: int) -> dict[str, str]:
    headers = {"User-Agent": USER_AGENT}
    if resume_from > 0:
        headers["Range"] = f"bytes={resume_from}-"
    return headers


def _open_download_response(request: urllib.request.Request, now_func: NowCallback):
    try:
        return urllib.request.urlopen(request, timeout=120)
    except urllib.error.HTTPError as exc:
        raise _http_network_error(exc, now_func) from exc
    except urllib.error.URLError as exc:
        raise _url_network_error(exc) from exc
    except (http.client.HTTPException, OSError, ValueError) as exc:
        raise DownloadNetworkError(str(exc)) from exc


def _plan_response_transfer(
    response: object,
    resume_from: int,
    expected_size: int,
    stream_md5: bool,
) -> _ResponseTransferPlan:
    appending = resume_from > 0 and _status_code(response) == 206
    if appending:
        _validate_content_range(response, resume_from)
    else:
        resume_from = 0
    total = _response_total_size(response, resume_from, appending, expected_size)
    return _ResponseTransferPlan(
        resume_from=resume_from,
        total=total,
        file_mode="ab" if appending else "wb",
        streamed_digest=None if appending or not stream_md5 else hashlib.md5(),
        resumed=appending,
    )


def _response_total_size(response: object, resume_from: int, appending: bool, expected_size: int) -> int:
    total = _content_length(response)
    if total and appending:
        total += resume_from
    if not total:
        total = expected_size
    return total


def _stream_response_to_part(
    response: object,
    part_path: Path,
    *,
    file_mode: str,
    chunk_size: int,
    progress_reporter: _ProgressReporter,
    progress_base: int,
    total: int,
    streamed_digest: object | None,
) -> None:
    handle = _open_part_for_transfer(part_path, file_mode)
    downloaded = 0
    try:
        with handle:
            while True:
                chunk = _read_response_chunk(response, chunk_size)
                if not chunk:
                    break
                _write_part_chunk(part_path, handle, chunk)
                if streamed_digest:
                    streamed_digest.update(chunk)
                downloaded += len(chunk)
                progress_reporter.emit(progress_base + downloaded, total)
            progress_reporter.emit(progress_base + downloaded, total, force=True)
    except (DownloadNetworkError, DownloadLocalIoError):
        raise
    except OSError as exc:
        raise DownloadLocalIoError(f"Could not close partial download file: {part_path}") from exc


def _open_part_for_transfer(part_path: Path, file_mode: str):
    try:
        return part_path.open(file_mode)
    except OSError as exc:
        raise DownloadLocalIoError(f"Could not open partial download file: {part_path}") from exc


def _read_response_chunk(response: object, chunk_size: int) -> bytes:
    try:
        return response.read(chunk_size)
    except (urllib.error.URLError, http.client.HTTPException, OSError) as exc:
        raise DownloadNetworkError(str(exc)) from exc


def _write_part_chunk(part_path: Path, handle: object, chunk: bytes) -> None:
    try:
        handle.write(chunk)
    except OSError as exc:
        raise DownloadLocalIoError(f"Could not write partial download file: {part_path}") from exc


def _validated_download_size(part_path: Path, expected_size: int) -> int:
    final_size = existing_size(part_path)
    if expected_size > 0 and final_size < expected_size:
        raise DownloadSizeMismatchError(
            f"Downloaded size is smaller than expected: expected={expected_size} actual={final_size}"
        )
    if expected_size > 0 and final_size > expected_size:
        raise DownloadSizeMismatchError(
            f"Downloaded size exceeds expected size: expected={expected_size} actual={final_size}"
        )
    return final_size


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


def _reuse_or_quarantine_existing(
    planned: PlannedFile,
    message_callback: MessageCallback | None = None,
) -> DownloadOutcome | None:
    inspection = _inspect_download_candidate(planned.local_path, planned, "final")
    if inspection.status == _CANDIDATE_MISSING:
        return None
    if inspection.status in (_CANDIDATE_SHORT, _CANDIDATE_SIZE_MISMATCH):
        quarantined = _quarantine_file(inspection.path, "size-mismatch-existing")
        _emit(message_callback, f"existing_file_quarantined_size_mismatch: {quarantined}")
        return None
    if inspection.status == _CANDIDATE_VERIFIED:
        _remove_stale_part_after_final_reuse(planned.local_path)
        return DownloadOutcome(
            MD5_VERIFIED,
            "Existing file MD5 matched, so the file was reused without downloading again.",
            inspection.actual_md5,
            inspection.size,
        )
    if inspection.status == _CANDIDATE_BAD_MD5:
        quarantined = _quarantine_file(inspection.path, "bad-md5-existing")
        _emit(message_callback, f"existing_file_quarantined_bad_md5: {quarantined}")
        return None
    if inspection.status == _CANDIDATE_UNVERIFIED_COMPLETE:
        quarantined = _quarantine_file(inspection.path, "unverified-existing")
        _emit(message_callback, f"existing_file_quarantined_unverified: {quarantined}")
        return None
    quarantined = _quarantine_file(inspection.path, "unverified-existing")
    _emit(message_callback, f"existing_file_quarantined_unverified: {quarantined}")
    return None


def _reuse_or_quarantine_complete_part(
    planned: PlannedFile,
    message_callback: MessageCallback | None = None,
) -> DownloadOutcome | None:
    part_path = download_part_path(planned.local_path)
    inspection = _inspect_download_candidate(part_path, planned, "part")
    if inspection.status in (_CANDIDATE_MISSING, _CANDIDATE_SHORT, _CANDIDATE_UNVERIFIED_INCOMPLETE):
        return None
    if inspection.status == _CANDIDATE_SIZE_MISMATCH:
        raise DownloadSizeMismatchError(
            f"Partial file size exceeds expected size: expected={planned.fastq.size_bytes} actual={inspection.size}"
        )
    if inspection.status == _CANDIDATE_UNVERIFIED_COMPLETE:
        quarantined = _quarantine_file(part_path, "unverified-existing")
        _emit(message_callback, f"partial_file_quarantined_unverified: {quarantined}")
        return None
    if inspection.status == _CANDIDATE_VERIFIED:
        _finalize_part(part_path, planned.local_path)
        return DownloadOutcome(
            MD5_VERIFIED,
            "Previous partial file MD5 matched, so it was promoted to the final file name.",
            inspection.actual_md5,
            inspection.size,
        )
    if inspection.status == _CANDIDATE_BAD_MD5:
        quarantined = _quarantine_file(part_path, "bad-md5")
        _emit(message_callback, f"partial_file_quarantined_bad_md5: {quarantined}")
    return None


def _inspect_download_candidate(path: Path, planned: PlannedFile, kind: str) -> _CandidateInspection:
    if not path.exists():
        return _CandidateInspection(path, kind, _CANDIDATE_MISSING)
    if not path.is_file():
        if kind == "final":
            raise GeoGetterError(OUTPUT_PATH_INVALID, f"download_target_is_not_file path={path}")
        raise GeoGetterError(OUTPUT_PATH_INVALID, f"partial_download_target_is_not_file path={path}")

    candidate_size = existing_size(path)
    if planned.fastq.size_bytes > 0:
        if candidate_size < planned.fastq.size_bytes:
            return _CandidateInspection(path, kind, _CANDIDATE_SHORT, candidate_size)
        if candidate_size > planned.fastq.size_bytes:
            return _CandidateInspection(path, kind, _CANDIDATE_SIZE_MISMATCH, candidate_size)

    if planned.fastq.expected_md5:
        ok, actual_md5 = _verify_md5_candidate(path, planned.fastq.expected_md5, _candidate_md5_description(kind))
        status = _CANDIDATE_VERIFIED if ok else _CANDIDATE_BAD_MD5
        return _CandidateInspection(path, kind, status, candidate_size, actual_md5)

    if kind == "final" or (planned.fastq.size_bytes > 0 and candidate_size == planned.fastq.size_bytes):
        return _CandidateInspection(path, kind, _CANDIDATE_UNVERIFIED_COMPLETE, candidate_size)
    return _CandidateInspection(path, kind, _CANDIDATE_UNVERIFIED_INCOMPLETE, candidate_size)


def _candidate_md5_description(kind: str) -> str:
    return "existing FASTQ" if kind == "final" else "partial FASTQ"


def _remove_stale_part_after_final_reuse(local_path: Path) -> None:
    stale_part = download_part_path(local_path)
    if not stale_part.exists():
        return
    try:
        stale_part.unlink()
    except OSError as exc:
        raise DownloadLocalIoError(f"Could not remove stale partial download: {stale_part}") from exc


def _verify_md5_candidate(path: Path, expected_md5: str, description: str) -> tuple[bool, str]:
    try:
        return verify_md5(path, expected_md5)
    except OSError as exc:
        raise DownloadLocalIoError(f"Could not read {description} for MD5 verification: {path}") from exc


def classify_download_error(error: BaseException) -> str:
    if isinstance(error, DownloadSizeMismatchError):
        return SIZE_MISMATCH
    if isinstance(error, DownloadNetworkError):
        return NETWORK_FAILED
    return LOCAL_IO_FAILED


def download_failure_outcome(
    local_path: Path,
    error: BaseException,
    *,
    bytes_downloaded: int | None = None,
    message_note: str = "",
) -> DownloadOutcome:
    status = classify_download_error(error)
    detail = str(error)
    message_parts = [ERROR_MESSAGES[status]]
    if message_note:
        message_parts.append(message_note)
    message = " ".join(message_parts)
    if detail:
        message = f"{message} Detail: {detail}"
    return DownloadOutcome(
        status,
        message,
        bytes_downloaded=existing_size(download_part_path(local_path)) if bytes_downloaded is None else bytes_downloaded,
        result_message=detail,
    )


def download_error_outcome(
    local_path: Path,
    error: BaseException,
    *,
    bytes_downloaded: int | None = None,
    message_note: str = "",
) -> DownloadOutcome:
    return download_failure_outcome(
        local_path,
        error,
        bytes_downloaded=bytes_downloaded,
        message_note=message_note,
    )


def _finalize_part(part_path: Path, local_path: Path) -> None:
    try:
        local_path.parent.mkdir(parents=True, exist_ok=True)
        part_path.replace(local_path)
    except OSError as exc:
        raise DownloadLocalIoError(f"Could not move partial download into place: {part_path} -> {local_path}") from exc


def _quarantine_file(path: Path, reason: str) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    try:
        candidate = unique_quarantine_path(path, reason, timestamp)
        path.replace(candidate)
    except OSError as exc:
        raise DownloadLocalIoError(f"Could not move file aside: {path}") from exc
    return candidate
