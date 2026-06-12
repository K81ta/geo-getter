from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
from pathlib import Path

from . import __version__
from .downloader import (
    DownloadLocalIoError,
    DownloadNetworkError,
    DownloadSizeMismatchError,
    download_plan,
    download_url_to_part,
    finalize_downloaded_part,
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
from .models import FastqFile
from .path_safety import child_path, name_collision_key, reserve_unique_download_name, reserved_download_names, safe_file_name
from .planner import (
    append_download_log,
    build_download_plan,
    download_log_path,
    fastq_manifest_path,
    initialize_log,
    reserved_download_artifact_names,
    supplementary_manifest_path,
    validate_resume_artifacts,
    verify_fastq_manifest,
)
from .providers.resolver import MetadataResolver
from .updater import check_for_update, download_update_installer


class GeoGetterArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ValueError(message)


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


def _selected_download_json(
    input_json: Path,
    fastq_indices: str,
    supp_indices: str,
    output_dir: Path,
    resume_existing: bool = False,
) -> int:
    payload = _load_json(input_json)
    selected_fastq = _selected_fastq_from_payload(payload, fastq_indices) if fastq_indices.strip() else []
    selected_supp = _selected_supplementary_from_payload(payload, supp_indices) if supp_indices.strip() else []
    _ensure_any_selected(selected_fastq, selected_supp)

    statuses: list[str] = []
    run_output_dir = output_dir.expanduser().resolve()
    run_output_dir.mkdir(parents=True, exist_ok=True)
    existing_output_nonempty = _directory_has_entries(run_output_dir)
    if existing_output_nonempty and selected_supp:
        raise GeoGetterError(RESUME_SUPPLEMENTARY_UNSUPPORTED, f"output_dir={run_output_dir}")
    if existing_output_nonempty and not resume_existing:
        raise GeoGetterError(RESUME_REQUIRED, f"output_dir={run_output_dir}")

    reserved_output_names = reserved_download_artifact_names(run_output_dir)
    resume_required_bytes: int | None = None
    resume_active = existing_output_nonempty and resume_existing
    resume_artifacts = None
    if selected_fastq:
        plan = build_download_plan(payload["input_text"], payload["primary_accession"], selected_fastq, run_output_dir)
        if resume_active:
            resume_artifacts = validate_resume_artifacts(plan)
            resume_required_bytes = resume_artifacts.required_bytes
        reserved_output_names = [
            *reserved_output_names,
            *(name for planned in plan.files for name in reserved_download_names(planned.local_path.name)),
        ]

        def progress(planned, downloaded: int, total: int) -> None:
            print(
                json.dumps(
                    {
                        "event": "progress",
                        "kind": "fastq",
                        "file_name": planned.fastq.file_name,
                        "downloaded": downloaded,
                        "total": total,
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )

        def message(text: str) -> None:
            print(json.dumps({"event": "message", "message": text}, ensure_ascii=False), flush=True)

        results = download_plan(
            plan,
            progress_callback=progress,
            message_callback=message,
            resume_artifacts=resume_artifacts,
        )
        statuses.extend(status for _planned, status, _message in results)
    else:
        initialize_log(run_output_dir)

    if selected_supp:
        _write_supplementary_manifest(run_output_dir, selected_supp, reserved_output_names)
        statuses.extend(_download_supplementary_files(run_output_dir, selected_supp, reserved_output_names))

    print(
        json.dumps(
            {
                "event": "done",
                "statuses": statuses,
                "output_dir": str(run_output_dir),
                "fastq_manifest": str(fastq_manifest_path(run_output_dir)) if selected_fastq else "",
                "supplementary_manifest": str(supplementary_manifest_path(run_output_dir)) if selected_supp else "",
                "download_log": str(download_log_path(run_output_dir)),
                "resume_existing": resume_active,
                "resume_required_bytes": resume_required_bytes,
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
    payload = _load_json(input_json)
    selected_fastq = _selected_fastq_from_payload(payload, fastq_indices) if fastq_indices.strip() else []
    selected_supp = _selected_supplementary_from_payload(payload, supp_indices) if supp_indices.strip() else []
    _ensure_any_selected(selected_fastq, selected_supp)

    run_output_dir = output_dir.expanduser().resolve()
    run_output_dir.mkdir(parents=True, exist_ok=True)
    existing_output_nonempty = _directory_has_entries(run_output_dir)
    if existing_output_nonempty and selected_supp:
        raise GeoGetterError(RESUME_SUPPLEMENTARY_UNSUPPORTED, f"output_dir={run_output_dir}")

    free_bytes = shutil.disk_usage(run_output_dir).free
    required_bytes = 0
    resume_required_bytes: int | None = None
    reserved_output_names = reserved_download_artifact_names(run_output_dir)
    planned_paths: list[Path] = [run_output_dir]
    planned_fastq: list[dict[str, object]] = []
    planned_supp: list[dict[str, object]] = []

    if selected_fastq:
        plan = build_download_plan(payload["input_text"], payload["primary_accession"], selected_fastq, run_output_dir)
        required_bytes = plan.total_bytes
        free_bytes = plan.available_bytes
        if existing_output_nonempty and resume_existing:
            resume_artifacts = validate_resume_artifacts(plan)
            required_bytes = resume_artifacts.required_bytes
            resume_required_bytes = resume_artifacts.required_bytes
        reserved_output_names = [
            *reserved_output_names,
            *(name for planned in plan.files for name in reserved_download_names(planned.local_path.name)),
        ]
        planned_paths.append(fastq_manifest_path(run_output_dir))
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

    if selected_supp:
        planned_supplementary = _planned_supplementary_files(run_output_dir, selected_supp, reserved_output_names)
        planned_paths.append(supplementary_manifest_path(run_output_dir))
        planned_supp = [
            {
                "name": item.get("name", ""),
                "source_accession": item.get("source_accession", ""),
                "scope": item.get("scope", ""),
                "url": item.get("url", ""),
                "local_path": str(local_path),
            }
            for item, local_path in planned_supplementary
        ]
        planned_paths.extend(path for _item, local_path in planned_supplementary for path in _download_runtime_paths(local_path, "supplementary"))

    planned_paths.append(download_log_path(run_output_dir))
    print(
        json.dumps(
            {
                "event": "done",
                "kind": "download_preflight",
                "output_dir": str(run_output_dir),
                "existing_output_nonempty": existing_output_nonempty,
                "required_bytes": required_bytes,
                "free_bytes": free_bytes,
                "resume_existing": bool(existing_output_nonempty and resume_existing),
                "resume_required_bytes": resume_required_bytes,
                "fastq_manifest": str(fastq_manifest_path(run_output_dir)) if selected_fastq else "",
                "supplementary_manifest": str(supplementary_manifest_path(run_output_dir)) if selected_supp else "",
                "download_log": str(download_log_path(run_output_dir)),
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


def _write_supplementary_manifest(output_dir: Path, selected_supp: list[dict], reserved_names: list[str] | None = None) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = supplementary_manifest_path(output_dir)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(["source_accession", "scope", "file_name", "url", "local_path", "status"])
        for item, local_path in _planned_supplementary_files(output_dir, selected_supp, reserved_names):
            writer.writerow(
                [
                    item.get("source_accession", ""),
                    item.get("scope", ""),
                    item.get("name", ""),
                    item.get("url", ""),
                    str(local_path),
                    "planned",
                ]
            )
    initialize_log(output_dir)
    return path


def _download_supplementary_files(output_dir: Path, selected_supp: list[dict], reserved_names: list[str] | None = None) -> list[str]:
    statuses: list[str] = []
    for item, local_path in _planned_supplementary_files(output_dir, selected_supp, reserved_names):
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
            downloaded = max(downloaded, _existing_size(local_path.with_name(local_path.name + ".part")))
        except DownloadNetworkError as exc:
            status = NETWORK_FAILED
            message = str(exc)
            downloaded = max(downloaded, _existing_size(local_path.with_name(local_path.name + ".part")))
        except (DownloadLocalIoError, OSError) as exc:
            status = LOCAL_IO_FAILED
            message = str(exc)
            downloaded = max(downloaded, _existing_size(local_path.with_name(local_path.name + ".part")))
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
        file_name = safe_file_name(item.get("name", "") or "geo_supplementary_file", "geo_supplementary_file")
        file_name = reserve_unique_download_name(file_name, used_keys)
        planned.append((item, child_path(output_dir, file_name)))
    return planned


def _download_runtime_paths(local_path: Path, kind: str) -> list[Path]:
    names = [local_path.name, f"{local_path.name}.part"]
    if kind == "fastq":
        timestamp = "20000101T000000Z"
        part_name = f"{local_path.name}.part"
        names.extend(
            [
                f"{local_path.name}.bad-md5-existing-{timestamp}",
                f"{local_path.name}.bad-md5-existing-{timestamp}.2",
                f"{local_path.name}.size-mismatch-existing-{timestamp}",
                f"{local_path.name}.size-mismatch-existing-{timestamp}.2",
                f"{local_path.name}.unverified-existing-{timestamp}",
                f"{local_path.name}.unverified-existing-{timestamp}.2",
                f"{part_name}.bad-md5-{timestamp}",
                f"{part_name}.bad-md5-{timestamp}.2",
                f"{part_name}.size-mismatch-{timestamp}",
                f"{part_name}.size-mismatch-{timestamp}.2",
            ]
        )
    else:
        names.extend([f"{local_path.name}.existing", f"{local_path.name}.existing.2"])
    return [local_path.with_name(name) for name in names]


def _existing_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _unique_existing_path(path: Path) -> Path:
    candidate = path.with_name(path.name + ".existing")
    counter = 2
    while candidate.exists():
        candidate = path.with_name(f"{path.name}.existing.{counter}")
        counter += 1
    return candidate


if __name__ == "__main__":
    raise SystemExit(run_cli())
