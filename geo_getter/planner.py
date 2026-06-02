from __future__ import annotations

import csv
import hashlib
import shutil
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .errors import GeoGetterError
from .models import DownloadPlan, FastqFile, PlannedFile


FASTQ_MANIFEST_SUFFIX = "fastq_manifest.tsv"
SUPPLEMENTARY_MANIFEST_SUFFIX = "supplementary_manifest.tsv"
DOWNLOAD_LOG_SUFFIX = "download_log.tsv"
VERIFICATION_REPORT_NAME = "verification_report.tsv"


def build_download_plan(
    input_text: str,
    primary_accession: str,
    selected_files: list[FastqFile],
    output_dir: str | Path,
) -> DownloadPlan:
    if not selected_files:
        raise ValueError("No FASTQ files are selected.")
    out_dir = Path(output_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    total_bytes = sum(item.size_bytes for item in selected_files)
    available_bytes = shutil.disk_usage(out_dir).free
    planned = _planned_files(selected_files, out_dir)
    return DownloadPlan(
        app_version=__version__,
        created_at=datetime.now(timezone.utc).isoformat(),
        input_text=input_text,
        primary_accession=primary_accession,
        output_dir=out_dir,
        total_bytes=total_bytes,
        available_bytes=available_bytes,
        files=planned,
    )


def ensure_capacity(plan: DownloadPlan) -> None:
    if plan.total_bytes > plan.available_bytes:
        raise GeoGetterError(
            "insufficient_space",
            f"required={format_bytes(plan.total_bytes)} available={format_bytes(plan.available_bytes)}",
        )


def write_fastq_outputs(plan: DownloadPlan) -> None:
    plan.output_dir.mkdir(parents=True, exist_ok=True)
    write_fastq_manifest(plan)
    initialize_log(plan.output_dir)


def write_fastq_manifest(plan: DownloadPlan) -> None:
    path = fastq_manifest_path(plan.output_dir)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(
            [
                "source_accession",
                "query_accession",
                "run_accession",
                "file_index",
                "file_name",
                "url",
                "expected_md5",
                "size_bytes",
                "local_path",
                "status",
            ]
        )
        for planned in plan.files:
            item = planned.fastq
            writer.writerow(
                [
                    item.source_accession,
                    item.query_accession,
                    item.run_accession,
                    item.file_index,
                    item.file_name,
                    item.url,
                    item.expected_md5,
                    item.size_bytes,
                    str(planned.local_path),
                    "planned",
                ]
            )


def initialize_log(output_dir: str | Path) -> None:
    path = download_log_path(output_dir)
    if path.exists():
        return
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(
            [
                "timestamp",
                "run_accession",
                "file_name",
                "status",
                "expected_md5",
                "actual_md5",
                "bytes_expected",
                "bytes_downloaded",
                "message",
            ]
        )


def append_download_log(
    output_dir: str | Path,
    run_accession: str,
    file_name: str,
    status: str,
    expected_md5: str,
    actual_md5: str,
    bytes_expected: int,
    bytes_downloaded: int,
    message: str,
) -> None:
    initialize_log(output_dir)
    with download_log_path(output_dir).open("a", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(
            [
                datetime.now(timezone.utc).isoformat(),
                run_accession,
                file_name,
                status,
                expected_md5,
                actual_md5,
                bytes_expected,
                bytes_downloaded,
                message,
            ]
        )


def format_bytes(value: int) -> str:
    if value < 0:
        value = 0
    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(value)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.2f} {unit}"
        size /= 1024
    return f"{value} B"


def fastq_manifest_path(output_dir: str | Path) -> Path:
    return _artifact_path(output_dir, FASTQ_MANIFEST_SUFFIX)


def supplementary_manifest_path(output_dir: str | Path) -> Path:
    return _artifact_path(output_dir, SUPPLEMENTARY_MANIFEST_SUFFIX)


def download_log_path(output_dir: str | Path) -> Path:
    return _artifact_path(output_dir, DOWNLOAD_LOG_SUFFIX)


def verify_fastq_manifest(manifest_path: str | Path, report_path: str | Path | None = None) -> Path:
    manifest = Path(manifest_path)
    output = Path(report_path) if report_path else manifest.parent / VERIFICATION_REPORT_NAME
    with manifest.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        rows = list(reader)

    with output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(
            [
                "source_accession",
                "query_accession",
                "run_accession",
                "file_index",
                "file_name",
                "local_path",
                "exists",
                "expected_size_bytes",
                "actual_size_bytes",
                "expected_md5",
                "actual_md5",
                "status",
            ]
        )
        for row in rows:
            local_path = _resolve_manifest_local_path(manifest, row.get("local_path", ""))
            exists = local_path.exists()
            expected_size = _parse_int(row.get("size_bytes", "0"))
            expected_md5 = (row.get("expected_md5", "") or "").strip()
            actual_size = _existing_size(local_path)
            actual_md5 = _calculate_md5(local_path) if exists else ""
            status = _verification_status(exists, expected_size, actual_size, expected_md5, actual_md5)
            writer.writerow(
                [
                    row.get("source_accession", ""),
                    row.get("query_accession", ""),
                    row.get("run_accession", ""),
                    row.get("file_index", ""),
                    row.get("file_name", ""),
                    str(local_path),
                    "yes" if exists else "no",
                    expected_size,
                    actual_size,
                    expected_md5,
                    actual_md5,
                    status,
                ]
            )
    return output


def _artifact_path(output_dir: str | Path, suffix: str) -> Path:
    out_dir = Path(output_dir)
    prefix = _artifact_prefix(out_dir.name)
    return out_dir / f"{prefix}_{suffix}"


def _artifact_prefix(value: str) -> str:
    safe = "".join("_" if char in '<>:"/\\|?*' else char for char in value).strip(" .")
    return safe or "geo_getter_download"


def _planned_files(files: list[FastqFile], output_dir: Path) -> list[PlannedFile]:
    counts: dict[str, int] = {}
    planned: list[PlannedFile] = []
    for item in files:
        file_name = item.file_name
        count = counts.get(file_name, 0)
        counts[file_name] = count + 1
        if count:
            stem, suffix = _split_name(file_name)
            file_name = f"{stem}.{count + 1}{suffix}"
        planned.append(PlannedFile(fastq=item, local_path=output_dir / file_name))
    return planned


def _split_name(file_name: str) -> tuple[str, str]:
    if file_name.endswith(".fastq.gz"):
        return file_name[:-9], ".fastq.gz"
    path = Path(file_name)
    return path.stem, path.suffix


def _resolve_manifest_local_path(manifest_path: Path, local_path: str) -> Path:
    candidate = Path(local_path)
    if candidate.is_absolute():
        return candidate
    return (manifest_path.parent / candidate).resolve()


def _parse_int(value: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _existing_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _calculate_md5(path: Path) -> str:
    digest = hashlib.md5()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verification_status(
    exists: bool,
    expected_size: int,
    actual_size: int,
    expected_md5: str,
    actual_md5: str,
) -> str:
    if not exists:
        return "missing"
    if expected_size > 0 and actual_size != expected_size:
        return "size_mismatch"
    if not expected_md5:
        return "md5_unavailable"
    if actual_md5.lower() == expected_md5.lower():
        return "md5_verified"
    return "md5_mismatch"
