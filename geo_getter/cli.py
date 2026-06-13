from __future__ import annotations

import argparse
import json
import shutil
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

from . import __version__
from .downloader import (
    DEFAULT_DOWNLOAD_WORKERS,
    DownloadLocalIoError,
    DownloadNetworkError,
    DownloadSizeMismatchError,
    download_plan,
    download_url_to_part,
    finalize_downloaded_part,
    normalize_download_workers,
)
from .errors import (
    DOWNLOAD_COMPLETE,
    LOCAL_IO_FAILED,
    MD5_UNAVAILABLE,
    MD5_VERIFIED,
    NETWORK_FAILED,
    RESUME_REQUIRED,
    RESUME_SUPPLEMENTARY_UNSUPPORTED,
    SIZE_MISMATCH,
    GeoGetterError,
)
from .models import DownloadPlan, FastqFile
from .path_safety import (
    download_part_path,
    existing_candidate_path,
    existing_size,
    name_collision_key,
    quarantine_candidate_path,
    reserved_download_names,
)
from .planner import (
    append_download_log,
    build_download_plan,
    download_log_path,
    fastq_manifest_path,
    initialize_log,
    plan_download_child_path,
    ResumeArtifacts,
    reserved_download_artifact_names,
    supplementary_manifest_path,
    validate_resume_artifacts,
    verify_fastq_manifest,
    write_supplementary_manifest,
)
from .providers.resolver import MetadataResolver
from .updater import check_for_update, download_update_installer


class GeoGetterArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ValueError(message)


@dataclass(frozen=True)
class CliDownloadPlan:
    output_dir: Path
    selected_fastq: list[FastqFile]
    selected_supplementary: list[dict]
    existing_output_nonempty: bool
    resume_active: bool
    fastq_plan: DownloadPlan | None
    resume_artifacts: ResumeArtifacts | None
    resume_required_bytes: int | None
    planned_supplementary: list[tuple[dict, Path]]


def main(argv: list[str] | None = None) -> int:
    argv_list = list(sys.argv[1:] if argv is None else argv)
    parser = GeoGetterArgumentParser(description="GEOGetter internal GUI bridge")
    subparsers = parser.add_subparsers(dest="command", required=True)

    resolve_json_parser = subparsers.add_parser("resolve-json", help="write resolved metadata as JSON")
    resolve_json_parser.add_argument("input_text", nargs="?")
    resolve_json_parser.add_argument("--input-file")
    resolve_json_parser.add_argument("--out-json")

    selected_download_parser = subparsers.add_parser("selected-download-json", help="download selected FASTQ and GEO supplementary files")
    selected_download_parser.add_argument("--input-json", required=True)
    selected_download_parser.add_argument("--fastq-indices", default="")
    selected_download_parser.add_argument("--supp-indices", default="")
    selected_download_parser.add_argument("--out", required=True)
    selected_download_parser.add_argument("--resume-existing", action="store_true")
    selected_download_parser.add_argument("--download-workers", type=int, default=DEFAULT_DOWNLOAD_WORKERS)

    if argv_list and argv_list[0] == "preflight-json":
        preflight_parser = subparsers.add_parser("preflight-json", help=argparse.SUPPRESS)
        preflight_parser.add_argument("--input-json", required=True)
        preflight_parser.add_argument("--fastq-indices", default="")
        preflight_parser.add_argument("--supp-indices", default="")
        preflight_parser.add_argument("--out", required=True)
        preflight_parser.add_argument("--resume-existing", action="store_true")
    if argv_list and argv_list[0] == "verify-manifest-json":
        verify_manifest_parser = subparsers.add_parser("verify-manifest-json", help=argparse.SUPPRESS)
        verify_manifest_parser.add_argument("--manifest", required=True)
    if argv_list and argv_list[0] == "check-update-json":
        subparsers.add_parser("check-update-json", help=argparse.SUPPRESS)
    if argv_list and argv_list[0] == "download-update-json":
        update_download_parser = subparsers.add_parser("download-update-json", help=argparse.SUPPRESS)
        update_download_parser.add_argument("--version", required=True)
        update_download_parser.add_argument("--out-dir")

    args = parser.parse_args(argv_list)
    if args.command == "resolve-json":
        return _resolve_json(args.input_text, args.input_file, args.out_json)
    if args.command == "selected-download-json":
        return _selected_download_json(
            Path(args.input_json),
            args.fastq_indices,
            args.supp_indices,
            Path(args.out),
            resume_existing=args.resume_existing,
            download_workers=args.download_workers,
        )
    if args.command == "preflight-json":
        return _preflight_json(
            Path(args.input_json),
            args.fastq_indices,
            args.supp_indices,
            Path(args.out),
            resume_existing=args.resume_existing,
        )
    if args.command == "verify-manifest-json":
        return _verify_manifest_json(Path(args.manifest))
    if args.command == "check-update-json":
        return _check_update_json()
    if args.command == "download-update-json":
        return _download_update_json(args.version, args.out_dir)
    return 2


def run_cli(argv: list[str] | None = None) -> int:
    argv_list = list(sys.argv[1:] if argv is None else argv)
    command = _command_from_argv(argv_list)
    try:
        return main(argv_list)
    except Exception as exc:
        print(json.dumps(_error_payload(command, exc), ensure_ascii=False), file=sys.stderr)
        return 1


def _command_from_argv(argv: list[str]) -> str:
    if argv and not argv[0].startswith("-"):
        return argv[0]
    return ""


def _error_payload(command: str, exc: Exception) -> dict[str, str]:
    code, detail, message = _classify_error(exc)
    return {
        "event": "error",
        "command": command,
        "code": code,
        "detail": detail,
        "message": message,
    }


def _classify_error(exc: Exception) -> tuple[str, str, str]:
    if isinstance(exc, GeoGetterError):
        return exc.code, exc.detail, exc.user_message
    if isinstance(exc, json.JSONDecodeError):
        return "invalid_json", str(exc), f"Could not parse JSON input.\nDetail: {exc}"
    if isinstance(exc, IndexError):
        return "selection_invalid", str(exc), f"Selected file index is invalid.\nDetail: {exc}"
    if isinstance(exc, ValueError):
        return "invalid_input", str(exc), str(exc)
    if isinstance(exc, (FileNotFoundError, OSError)):
        return "file_error", str(exc), f"Could not read or write a required file.\nDetail: {exc}"
    return "internal_error", str(exc), f"Internal error.\nDetail: {exc}"


def _resolve_json(input_text: str | None, input_file: str | None, out_json: str | None) -> int:
    text = _read_input_text(input_text, input_file)
    result = MetadataResolver().resolve(text)
    payload = {
        "app_version": __version__,
        "input_text": result.input_text,
        "primary_accession": result.primary_accession,
        "query_accessions": result.query_accessions,
        "warnings": result.warnings,
        "dataset_metadata": result.dataset_metadata.to_dict(),
        "fastq_files": [item.to_dict() for item in result.fastq_files],
        "supplementary_files": [item.to_dict() for item in result.supplementary_files],
    }
    _write_or_print_json(payload, out_json)
    return 0


def _build_cli_download_plan(
    input_json: Path,
    fastq_indices: str,
    supp_indices: str,
    output_dir: Path,
    resume_existing: bool = False,
    require_resume_for_nonempty: bool = False,
) -> CliDownloadPlan:
    payload = _load_json(input_json)
    selected_fastq = _selected_fastq_from_payload(payload, fastq_indices) if fastq_indices.strip() else []
    selected_supp = _selected_supplementary_from_payload(payload, supp_indices) if supp_indices.strip() else []
    _ensure_any_selected(selected_fastq, selected_supp)

    run_output_dir = output_dir.expanduser().resolve()
    run_output_dir.mkdir(parents=True, exist_ok=True)
    existing_output_nonempty = _directory_has_entries(run_output_dir)
    if existing_output_nonempty and selected_supp:
        raise GeoGetterError(RESUME_SUPPLEMENTARY_UNSUPPORTED, f"output_dir={run_output_dir}")
    if require_resume_for_nonempty and existing_output_nonempty and not resume_existing:
        raise GeoGetterError(RESUME_REQUIRED, f"output_dir={run_output_dir}")

    reserved_output_names = reserved_download_artifact_names(run_output_dir)
    resume_active = existing_output_nonempty and resume_existing
    fastq_plan = None
    resume_artifacts = None
    resume_required_bytes: int | None = None
    if selected_fastq:
        fastq_plan = build_download_plan(payload["input_text"], payload["primary_accession"], selected_fastq, run_output_dir)
        if resume_active:
            resume_artifacts = validate_resume_artifacts(fastq_plan)
            resume_required_bytes = resume_artifacts.required_bytes
        reserved_output_names = [
            *reserved_output_names,
            *(name for planned in fastq_plan.files for name in reserved_download_names(planned.local_path.name)),
        ]

    planned_supplementary = _planned_supplementary_files(run_output_dir, selected_supp, reserved_output_names) if selected_supp else []
    return CliDownloadPlan(
        output_dir=run_output_dir,
        selected_fastq=selected_fastq,
        selected_supplementary=selected_supp,
        existing_output_nonempty=existing_output_nonempty,
        resume_active=resume_active,
        fastq_plan=fastq_plan,
        resume_artifacts=resume_artifacts,
        resume_required_bytes=resume_required_bytes,
        planned_supplementary=planned_supplementary,
    )


def _selected_download_json(
    input_json: Path,
    fastq_indices: str,
    supp_indices: str,
    output_dir: Path,
    resume_existing: bool = False,
    download_workers: int = DEFAULT_DOWNLOAD_WORKERS,
) -> int:
    cli_plan = _build_cli_download_plan(
        input_json,
        fastq_indices,
        supp_indices,
        output_dir,
        resume_existing=resume_existing,
        require_resume_for_nonempty=True,
    )
    statuses: list[str] = []
    worker_count = normalize_download_workers(download_workers)
    if cli_plan.fastq_plan:
        plan = cli_plan.fastq_plan
        progress_lock = threading.Lock()
        progress_by_path: dict[str, tuple[int, int]] = {}

        def progress(planned, downloaded: int, total: int) -> None:
            with progress_lock:
                progress_by_path[str(planned.local_path)] = (downloaded, total)
                aggregate_downloaded = sum(current for current, _total in progress_by_path.values())
                aggregate_total = plan.total_bytes or sum(item_total for _current, item_total in progress_by_path.values())
                _print_json_event(
                    {
                        "event": "progress",
                        "kind": "fastq",
                        "file_name": planned.fastq.file_name,
                        "downloaded": downloaded,
                        "total": total,
                        "aggregate_downloaded": aggregate_downloaded,
                        "aggregate_total": aggregate_total,
                        "download_workers": worker_count,
                    }
                )

        def message(text: str) -> None:
            with progress_lock:
                _print_json_event({"event": "message", "message": text})

        results = download_plan(
            plan,
            progress_callback=progress,
            message_callback=message,
            resume_artifacts=cli_plan.resume_artifacts,
            download_workers=worker_count,
        )
        statuses.extend(status for _planned, status, _message in results)
    else:
        initialize_log(cli_plan.output_dir)

    if cli_plan.planned_supplementary:
        write_supplementary_manifest(cli_plan.output_dir, cli_plan.planned_supplementary)
        statuses.extend(_download_supplementary_files(cli_plan.output_dir, cli_plan.planned_supplementary))

    print(
        json.dumps(
            {
                "event": "done",
                "statuses": statuses,
                "output_dir": str(cli_plan.output_dir),
                "fastq_manifest": str(fastq_manifest_path(cli_plan.output_dir)) if cli_plan.selected_fastq else "",
                "supplementary_manifest": str(supplementary_manifest_path(cli_plan.output_dir)) if cli_plan.selected_supplementary else "",
                "download_log": str(download_log_path(cli_plan.output_dir)),
                "resume_existing": cli_plan.resume_active,
                "resume_required_bytes": cli_plan.resume_required_bytes,
                "download_workers": worker_count,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    ok_statuses = {MD5_VERIFIED, MD5_UNAVAILABLE, DOWNLOAD_COMPLETE}
    return 0 if statuses and all(status in ok_statuses for status in statuses) else 1


def _preflight_json(
    input_json: Path,
    fastq_indices: str,
    supp_indices: str,
    output_dir: Path,
    resume_existing: bool = False,
) -> int:
    cli_plan = _build_cli_download_plan(
        input_json,
        fastq_indices,
        supp_indices,
        output_dir,
        resume_existing=resume_existing,
    )

    free_bytes = shutil.disk_usage(cli_plan.output_dir).free
    required_bytes = 0
    planned_paths: list[Path] = [cli_plan.output_dir]
    planned_fastq: list[dict[str, object]] = []
    planned_supp: list[dict[str, object]] = []

    if cli_plan.fastq_plan:
        plan = cli_plan.fastq_plan
        required_bytes = plan.total_bytes
        free_bytes = plan.available_bytes
        if cli_plan.resume_required_bytes is not None:
            required_bytes = cli_plan.resume_required_bytes
        planned_paths.append(fastq_manifest_path(cli_plan.output_dir))
        planned_fastq = [
            {
                "file_name": planned.fastq.file_name,
                "run_accession": planned.fastq.run_accession,
                "size_bytes": planned.fastq.size_bytes,
                "local_path": str(planned.local_path),
            }
            for planned in plan.files
        ]
        planned_paths.extend(path for planned in plan.files for path in _download_runtime_paths(planned.local_path, "fastq"))

    if cli_plan.planned_supplementary:
        planned_paths.append(supplementary_manifest_path(cli_plan.output_dir))
        planned_supp = [
            {
                "name": item.get("name", ""),
                "source_accession": item.get("source_accession", ""),
                "scope": item.get("scope", ""),
                "url": item.get("url", ""),
                "local_path": str(local_path),
            }
            for item, local_path in cli_plan.planned_supplementary
        ]
        planned_paths.extend(path for _item, local_path in cli_plan.planned_supplementary for path in _download_runtime_paths(local_path, "supplementary"))

    planned_paths.append(download_log_path(cli_plan.output_dir))
    print(
        json.dumps(
            {
                "event": "done",
                "kind": "download_preflight",
                "output_dir": str(cli_plan.output_dir),
                "existing_output_nonempty": cli_plan.existing_output_nonempty,
                "required_bytes": required_bytes,
                "free_bytes": free_bytes,
                "resume_existing": cli_plan.resume_active,
                "resume_required_bytes": cli_plan.resume_required_bytes,
                "fastq_manifest": str(fastq_manifest_path(cli_plan.output_dir)) if cli_plan.selected_fastq else "",
                "supplementary_manifest": str(supplementary_manifest_path(cli_plan.output_dir)) if cli_plan.selected_supplementary else "",
                "download_log": str(download_log_path(cli_plan.output_dir)),
                "planned_paths": [str(path) for path in planned_paths],
                "fastq_files": planned_fastq,
                "supplementary_files": planned_supp,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return 0


def _verify_manifest_json(manifest_path: Path) -> int:
    result = verify_fastq_manifest(manifest_path)
    payload = {
        "event": "done",
        "kind": "manifest_verification",
        "manifest": str(manifest_path),
        "report": str(result["report_path"]),
        "status_counts": result["status_counts"],
        "total": result["total"],
    }
    print(json.dumps(payload, ensure_ascii=False), flush=True)
    return 0 if result["total"] and result["status_counts"].get(MD5_VERIFIED, 0) == result["total"] else 1


def _check_update_json() -> int:
    print(json.dumps(check_for_update(), ensure_ascii=False), flush=True)
    return 0


def _download_update_json(version: str, out_dir: str | None) -> int:
    print(json.dumps(download_update_installer(version, output_dir=out_dir), ensure_ascii=False), flush=True)
    return 0


def _read_input_text(input_text: str | None, input_file: str | None) -> str:
    if input_file:
        return Path(input_file).read_text(encoding="utf-8").strip()
    if input_text:
        return input_text
    raise ValueError("input_text or --input-file is required.")


def _write_or_print_json(payload: object, out_json: str | None) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if out_json:
        Path(out_json).write_text(text, encoding="utf-8")
    else:
        print(text)


def _print_json_event(payload: object) -> None:
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _parse_indices(text: str) -> list[int]:
    indices: list[int] = []
    for part in text.split(","):
        stripped = part.strip()
        if stripped:
            indices.append(int(stripped))
    if not indices:
        raise ValueError("No FASTQ files are selected.")
    return indices


def _selected_fastq_from_payload(payload: dict, indices_text: str) -> list[FastqFile]:
    fastq_items = payload.get("fastq_files", [])
    selected: list[FastqFile] = []
    for index in _parse_indices(indices_text):
        if index < 0 or index >= len(fastq_items):
            raise IndexError(f"FASTQ index is out of range: {index}")
        selected.append(FastqFile(**fastq_items[index]))
    return selected


def _selected_supplementary_from_payload(payload: dict, indices_text: str) -> list[dict]:
    supp_items = payload.get("supplementary_files", [])
    selected: list[dict] = []
    for index in _parse_indices(indices_text):
        if index < 0 or index >= len(supp_items):
            raise IndexError(f"supplementary index is out of range: {index}")
        selected.append(dict(supp_items[index]))
    return selected


def _ensure_any_selected(selected_fastq: list[FastqFile], selected_supp: list[dict]) -> None:
    if not selected_fastq and not selected_supp:
        raise ValueError("Select at least one FASTQ or GEO supplementary/processed file.")


def _directory_has_entries(path: Path) -> bool:
    try:
        next(path.iterdir())
        return True
    except StopIteration:
        return False


def _download_supplementary_files(output_dir: Path, planned_supplementary: list[tuple[dict, Path]]) -> list[str]:
    statuses: list[str] = []
    for item, local_path in planned_supplementary:
        file_name = local_path.name
        url = item.get("url", "")
        downloaded = 0
        print(json.dumps({"event": "message", "message": f"supplementary_download_started: {file_name}"}, ensure_ascii=False), flush=True)
        try:
            if local_path.exists():
                local_path.replace(_unique_existing_path(local_path))

            def progress(current: int, total: int) -> None:
                nonlocal downloaded
                downloaded = current
                print(
                    json.dumps(
                        {
                            "event": "progress",
                            "kind": "supplementary",
                            "file_name": file_name,
                            "downloaded": current,
                            "total": total,
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )

            downloaded_part = download_url_to_part(
                url,
                local_path,
                progress_callback=progress,
                message_callback=lambda text: print(json.dumps({"event": "message", "message": text}, ensure_ascii=False), flush=True),
            )
            downloaded = downloaded_part.bytes_downloaded
            finalize_downloaded_part(local_path)
            status = DOWNLOAD_COMPLETE
            message = "Saved GEO supplementary/processed file. It was not verified because GEO SOFT does not provide a stable expected MD5 value."
        except DownloadSizeMismatchError as exc:
            status = SIZE_MISMATCH
            message = str(exc)
            downloaded = max(downloaded, existing_size(download_part_path(local_path)))
        except DownloadNetworkError as exc:
            status = NETWORK_FAILED
            message = str(exc)
            downloaded = max(downloaded, existing_size(download_part_path(local_path)))
        except (DownloadLocalIoError, OSError) as exc:
            status = LOCAL_IO_FAILED
            message = str(exc)
            downloaded = max(downloaded, existing_size(download_part_path(local_path)))
        append_download_log(
            output_dir,
            "GEO_SUPPLEMENTARY",
            file_name,
            status,
            "",
            "",
            0,
            downloaded,
            message,
        )
        print(json.dumps({"event": "message", "message": f"{status}: {file_name}"}, ensure_ascii=False), flush=True)
        statuses.append(status)
    return statuses


def _planned_supplementary_files(
    output_dir: Path,
    selected_supp: list[dict],
    reserved_names: list[str] | None = None,
) -> list[tuple[dict, Path]]:
    used_keys = {name_collision_key(name) for name in reserved_names or []}
    planned: list[tuple[dict, Path]] = []
    for item in selected_supp:
        local_path = plan_download_child_path(
            output_dir,
            item.get("name", "") or "geo_supplementary_file",
            "geo_supplementary_file",
            used_keys,
        )
        planned.append((item, local_path))
    return planned


def _download_runtime_paths(local_path: Path, kind: str) -> list[Path]:
    paths = [local_path, download_part_path(local_path)]
    if kind == "fastq":
        timestamp = "20000101T000000Z"
        part_path = download_part_path(local_path)
        paths.extend(
            [
                quarantine_candidate_path(local_path, "bad-md5-existing", timestamp),
                quarantine_candidate_path(local_path, "bad-md5-existing", timestamp, 2),
                quarantine_candidate_path(local_path, "size-mismatch-existing", timestamp),
                quarantine_candidate_path(local_path, "size-mismatch-existing", timestamp, 2),
                quarantine_candidate_path(local_path, "unverified-existing", timestamp),
                quarantine_candidate_path(local_path, "unverified-existing", timestamp, 2),
                quarantine_candidate_path(part_path, "bad-md5", timestamp),
                quarantine_candidate_path(part_path, "bad-md5", timestamp, 2),
                quarantine_candidate_path(part_path, "size-mismatch", timestamp),
                quarantine_candidate_path(part_path, "size-mismatch", timestamp, 2),
                quarantine_candidate_path(part_path, "unverified-existing", timestamp),
                quarantine_candidate_path(part_path, "unverified-existing", timestamp, 2),
            ]
        )
    else:
        paths.extend([existing_candidate_path(local_path), existing_candidate_path(local_path, 2)])
    return paths


def _unique_existing_path(path: Path) -> Path:
    candidate = existing_candidate_path(path)
    counter = 2
    while candidate.exists():
        candidate = existing_candidate_path(path, counter)
        counter += 1
    return candidate


if __name__ == "__main__":
    raise SystemExit(run_cli())
