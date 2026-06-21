from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from . import __version__
from .downloader import (
    DEFAULT_DOWNLOAD_WORKERS,
    MAX_DOWNLOAD_WORKERS,
    MIN_DOWNLOAD_WORKERS,
    download_plan,
    download_supplementary_files,
    normalize_download_workers,
)
from .errors import (
    DOWNLOAD_COMPLETE,
    INSUFFICIENT_SPACE,
    MD5_UNAVAILABLE,
    MD5_VERIFIED,
    OUTPUT_PATH_INVALID,
    PATH_TOO_LONG,
    RESUME_REQUIRED,
    RESUME_SUPPLEMENTARY_UNSUPPORTED,
    SELECTION_REQUIRED,
    GeoGetterError,
)
from .http_client import USER_AGENT
from .models import DownloadPlan, FastqFile, PlannedSupplementaryFile, SupplementaryFile
from .path_safety import (
    download_part_path,
    download_runtime_paths,
    name_collision_key,
)
from .planner import (
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
from .providers.resolver import resolve_metadata
from .updater import check_for_update, download_update_installer


class GeoGetterArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ValueError(message)


@dataclass(frozen=True)
class CliDownloadPlan:
    output_dir: Path
    existing_output_nonempty: bool
    resume_active: bool
    fastq_plan: DownloadPlan | None
    resume_artifacts: ResumeArtifacts | None
    planned_supplementary: list[PlannedSupplementaryFile]

    @property
    def resume_required_bytes(self) -> int | None:
        if self.resume_artifacts is None:
            return None
        return self.resume_artifacts.required_bytes


_SUPPLEMENTARY_PAYLOAD_FIELDS = {field.name for field in fields(SupplementaryFile)}
_SUPPLEMENTARY_REQUIRED_FIELDS = ("source_accession", "scope", "name", "url")


def main(argv: list[str] | None = None) -> int:
    argv_list = list(sys.argv[1:] if argv is None else argv)
    command = _command_from_argv(argv_list)
    try:
        args = _parse_cli_args(argv_list)
    except Exception as exc:
        return _emit_bridge_error(command, exc)
    return _run_bridge_command(args.command, args.handler, args)


@dataclass(frozen=True)
class BridgeCommand:
    name: str
    configure: Callable[[argparse.ArgumentParser], None]
    handler: Callable[[argparse.Namespace], int]
    help: str = ""
    hidden: bool = False


def _handle_resolve_json(args: argparse.Namespace) -> int:
    return _resolve_json(args.input_text, args.input_file, args.out_json)


def _handle_selected_download_json(args: argparse.Namespace) -> int:
    return _selected_download_json(
        Path(args.input_json),
        args.fastq_indices,
        args.supp_indices,
        args.out,
        resume_existing=args.resume_existing,
        download_workers=args.download_workers,
    )


def _handle_preflight_json(args: argparse.Namespace) -> int:
    return _preflight_json(
        Path(args.input_json),
        args.fastq_indices,
        args.supp_indices,
        args.out,
        resume_existing=args.resume_existing,
    )


def _handle_verify_manifest_json(args: argparse.Namespace) -> int:
    return _verify_manifest_json(Path(args.manifest))


def _handle_limits_json(args: argparse.Namespace) -> int:
    return _limits_json()


def _handle_check_update_json(args: argparse.Namespace) -> int:
    return _check_update_json()


def _handle_download_update_json(args: argparse.Namespace) -> int:
    return _download_update_json(args.version, args.out_dir)


def _parse_cli_args(argv_list: list[str]) -> argparse.Namespace:
    if argv_list and not argv_list[0].startswith("-"):
        command = BRIDGE_COMMAND_BY_NAME.get(argv_list[0])
        if command and command.hidden:
            return _build_single_command_parser(command).parse_args(argv_list[1:])
    return _build_parser().parse_args(argv_list)


def _build_parser() -> GeoGetterArgumentParser:
    parser = GeoGetterArgumentParser(description="GEOGetter internal GUI bridge")
    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
        metavar="{resolve-json,selected-download-json}",
    )

    for command in BRIDGE_COMMANDS:
        if command.hidden:
            continue
        command_parser = subparsers.add_parser(command.name, help=command.help)
        _configure_bridge_command_parser(command_parser, command)

    return parser


def _build_single_command_parser(command: BridgeCommand) -> GeoGetterArgumentParser:
    parser = GeoGetterArgumentParser(prog=f"GEOGetter {command.name}", description="GEOGetter internal GUI bridge")
    _configure_bridge_command_parser(parser, command)
    return parser


def _configure_bridge_command_parser(parser: argparse.ArgumentParser, command: BridgeCommand) -> None:
    command.configure(parser)
    parser.set_defaults(command=command.name, handler=command.handler)


def _run_bridge_command(
    command: str,
    handler: Callable[[argparse.Namespace], int],
    args: argparse.Namespace,
) -> int:
    try:
        return handler(args)
    except Exception as exc:
        return _emit_bridge_error(command, exc)


def _add_resolve_json_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("input_text", nargs="?")
    parser.add_argument("--input-file")
    parser.add_argument("--out-json")


def _add_download_selection_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--input-json", required=True)
    parser.add_argument("--fastq-indices", default="")
    parser.add_argument("--supp-indices", default="")
    parser.add_argument("--out", required=True)
    parser.add_argument("--resume-existing", action="store_true")


def _add_selected_download_arguments(parser: argparse.ArgumentParser) -> None:
    _add_download_selection_arguments(parser)
    parser.add_argument("--download-workers", type=int, default=DEFAULT_DOWNLOAD_WORKERS)


def _add_preflight_arguments(parser: argparse.ArgumentParser) -> None:
    _add_download_selection_arguments(parser)


def _add_verify_manifest_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--manifest", required=True)


def _add_update_download_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--version", required=True)
    parser.add_argument("--out-dir")


BRIDGE_COMMANDS = (
    BridgeCommand("resolve-json", _add_resolve_json_arguments, _handle_resolve_json, "write resolved metadata as JSON"),
    BridgeCommand("selected-download-json", _add_selected_download_arguments, _handle_selected_download_json, "download selected FASTQ and GEO supplementary files"),
    BridgeCommand("preflight-json", _add_preflight_arguments, _handle_preflight_json, hidden=True),
    BridgeCommand("verify-manifest-json", _add_verify_manifest_arguments, _handle_verify_manifest_json, hidden=True),
    BridgeCommand("limits-json", lambda parser: None, _handle_limits_json, hidden=True),
    BridgeCommand("check-update-json", lambda parser: None, _handle_check_update_json, hidden=True),
    BridgeCommand("download-update-json", _add_update_download_arguments, _handle_download_update_json, hidden=True),
)
BRIDGE_COMMAND_BY_NAME = {command.name: command for command in BRIDGE_COMMANDS}


def run_cli(argv: list[str] | None = None) -> int:
    return main(argv)


def _emit_bridge_error(command: str, exc: Exception) -> int:
    print(json.dumps(_error_payload(command, exc), ensure_ascii=False), file=sys.stderr)
    return 1


def _command_from_argv(argv: list[str]) -> str:
    if argv and not argv[0].startswith("-"):
        return argv[0]
    return ""


def _error_payload(command: str, exc: Exception) -> dict[str, object]:
    code, detail, message = _classify_error(exc)
    payload: dict[str, object] = {
        "event": "error",
        "command": command,
        "code": code,
        "detail": detail,
        "message": message,
    }
    if isinstance(exc, GeoGetterError):
        payload.update(exc.extra)
    return payload


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
    result = resolve_metadata(text)
    payload = {
        "app_version": __version__,
        "input_text": result.input_text,
        "primary_accession": result.primary_accession,
        "query_accessions": result.query_accessions,
        "warnings": result.warnings,
        "dataset_metadata": asdict(result.dataset_metadata),
        "fastq_files": [asdict(item) for item in result.fastq_files],
        "supplementary_files": [asdict(item) for item in result.supplementary_files],
    }
    _write_or_print_json(payload, out_json)
    return 0


def _build_cli_download_plan(
    input_json: Path,
    fastq_indices: str,
    supp_indices: str,
    output_dir: str | Path,
    resume_existing: bool = False,
    require_resume_for_nonempty: bool = False,
    create_output_dir: bool = True,
) -> CliDownloadPlan:
    payload = _load_json(input_json)
    selected_fastq = _selected_fastq_from_payload(payload, fastq_indices) if fastq_indices.strip() else []
    selected_supp = _selected_supplementary_from_payload(payload, supp_indices) if supp_indices.strip() else []
    _ensure_any_selected(selected_fastq, selected_supp)

    run_output_dir = _prepare_download_output_dir(output_dir, create_output_dir=create_output_dir)
    existing_output_nonempty = _directory_has_entries(run_output_dir)
    if existing_output_nonempty and selected_supp:
        raise GeoGetterError(
            RESUME_SUPPLEMENTARY_UNSUPPORTED,
            f"output_dir={run_output_dir}",
            extra={"existing_output_nonempty": True, "output_dir": str(run_output_dir)},
        )
    if require_resume_for_nonempty and existing_output_nonempty and not resume_existing:
        raise GeoGetterError(
            RESUME_REQUIRED,
            f"output_dir={run_output_dir}",
            extra={"existing_output_nonempty": True, "output_dir": str(run_output_dir)},
        )

    reserved_output_names = reserved_download_artifact_names(run_output_dir)
    resume_active = existing_output_nonempty and resume_existing
    fastq_plan = None
    resume_artifacts = None
    if selected_fastq:
        fastq_plan = build_download_plan(
            payload["input_text"],
            payload["primary_accession"],
            selected_fastq,
            run_output_dir,
            create_output_dir=create_output_dir,
        )
        if resume_active:
            resume_artifacts = validate_resume_artifacts(fastq_plan)
        reserved_output_names = [
            *reserved_output_names,
            *(
                name
                for planned in fastq_plan.files
                for name in (planned.local_path.name, download_part_path(planned.local_path).name)
            ),
        ]

    planned_supplementary = _planned_supplementary_files(run_output_dir, selected_supp, reserved_output_names) if selected_supp else []
    cli_plan = CliDownloadPlan(
        output_dir=run_output_dir,
        existing_output_nonempty=existing_output_nonempty,
        resume_active=resume_active,
        fastq_plan=fastq_plan,
        resume_artifacts=resume_artifacts,
        planned_supplementary=planned_supplementary,
    )
    _ensure_preflight_path_lengths(_preflight_planned_paths(cli_plan))
    return cli_plan


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
                _print_download_progress(
                    "fastq",
                    planned.fastq.file_name,
                    downloaded,
                    total,
                    aggregate_downloaded=aggregate_downloaded,
                    aggregate_total=aggregate_total,
                    download_workers=worker_count,
                )

        def message(text: str) -> None:
            with progress_lock:
                _print_download_message(text)

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
        supp_results = download_supplementary_files(
            cli_plan.output_dir,
            cli_plan.planned_supplementary,
            progress_callback=lambda item, current, total: _print_download_progress(
                item.kind,
                item.file_name,
                current,
                total,
            ),
            message_callback=_print_download_message,
        )
        statuses.extend(status for _item, status, _message in supp_results)

    print(
        json.dumps(
            {
                "event": "done",
                "statuses": statuses,
                "output_dir": str(cli_plan.output_dir),
                "fastq_manifest": str(fastq_manifest_path(cli_plan.output_dir)) if cli_plan.fastq_plan else "",
                "supplementary_manifest": str(supplementary_manifest_path(cli_plan.output_dir)) if cli_plan.planned_supplementary else "",
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
    output_dir: str | Path,
    resume_existing: bool = False,
) -> int:
    cli_plan = _build_cli_download_plan(
        input_json,
        fastq_indices,
        supp_indices,
        output_dir,
        resume_existing=resume_existing,
        create_output_dir=False,
    )

    free_bytes = _free_bytes_for_output(cli_plan.output_dir)
    fastq_required_bytes = 0
    supplementary_required_bytes = 0
    supplementary_size_unknown_count = 0
    capacity_checked = not cli_plan.existing_output_nonempty or resume_existing
    capacity_ok = True
    capacity_error_code: str | None = None
    planned_paths = _preflight_planned_paths(cli_plan)
    planned_fastq: list[dict[str, object]] = []
    planned_supp: list[dict[str, object]] = []

    if cli_plan.fastq_plan:
        plan = cli_plan.fastq_plan
        fastq_required_bytes = plan.total_bytes
        free_bytes = plan.available_bytes
        if cli_plan.resume_required_bytes is not None:
            fastq_required_bytes = cli_plan.resume_required_bytes
        planned_fastq = [
            {
                "file_name": planned.fastq.file_name,
                "run_accession": planned.fastq.run_accession,
                "size_bytes": planned.fastq.size_bytes,
                "local_path": str(planned.local_path),
            }
            for planned in plan.files
        ]

    if cli_plan.planned_supplementary:
        for planned in cli_plan.planned_supplementary:
            size_bytes, size_status = _estimate_supplementary_size(planned.supplementary.url)
            if size_status == "known":
                supplementary_required_bytes += size_bytes
            else:
                supplementary_size_unknown_count += 1
            planned_supp.append(
                {
                "name": planned.supplementary.name,
                "source_accession": planned.supplementary.source_accession,
                "scope": planned.supplementary.scope,
                "url": planned.supplementary.url,
                "local_path": str(planned.local_path),
                    "size_bytes": size_bytes,
                    "size_status": size_status,
                }
            )

    required_bytes = fastq_required_bytes + supplementary_required_bytes
    if capacity_checked and required_bytes > free_bytes:
        capacity_ok = False
        capacity_error_code = INSUFFICIENT_SPACE

    print(
        json.dumps(
            {
                "event": "done",
                "kind": "download_preflight",
                "output_dir": str(cli_plan.output_dir),
                "existing_output_nonempty": cli_plan.existing_output_nonempty,
                "required_bytes": required_bytes,
                "fastq_required_bytes": fastq_required_bytes,
                "supplementary_required_bytes": supplementary_required_bytes,
                "supplementary_size_unknown_count": supplementary_size_unknown_count,
                "capacity_unknown": supplementary_size_unknown_count > 0,
                "free_bytes": free_bytes,
                "capacity_checked": capacity_checked,
                "capacity_ok": capacity_ok,
                "capacity_error_code": capacity_error_code,
                "resume_existing": cli_plan.resume_active,
                "resume_required_bytes": cli_plan.resume_required_bytes,
                "fastq_manifest": str(fastq_manifest_path(cli_plan.output_dir)) if cli_plan.fastq_plan else "",
                "supplementary_manifest": str(supplementary_manifest_path(cli_plan.output_dir)) if cli_plan.planned_supplementary else "",
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


def _limits_json() -> int:
    payload = {
        "event": "done",
        "kind": "limits",
        "download_workers": {
            "min": MIN_DOWNLOAD_WORKERS,
            "max": MAX_DOWNLOAD_WORKERS,
            "default": DEFAULT_DOWNLOAD_WORKERS,
        },
    }
    print(json.dumps(payload, ensure_ascii=False), flush=True)
    return 0


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


def _print_download_message(text: str) -> None:
    _print_json_event({"event": "message", "message": text})


def _print_download_progress(
    kind: str,
    file_name: str,
    downloaded: int,
    total: int,
    **extra: object,
) -> None:
    payload = {
        "event": "progress",
        "kind": kind,
        "file_name": file_name,
        "downloaded": downloaded,
        "total": total,
    }
    payload.update(extra)
    _print_json_event(payload)


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _parse_indices(text: str) -> list[int]:
    indices: list[int] = []
    for part in text.split(","):
        stripped = part.strip()
        if stripped:
            indices.append(int(stripped))
    return indices


def _selected_fastq_from_payload(payload: dict, indices_text: str) -> list[FastqFile]:
    return _selected_items_from_payload(payload, "fastq_files", indices_text, "FASTQ", lambda item: FastqFile(**item))


def _selected_supplementary_from_payload(payload: dict, indices_text: str) -> list[SupplementaryFile]:
    return _selected_items_from_payload(
        payload,
        "supplementary_files",
        indices_text,
        "supplementary",
        _supplementary_file_from_payload,
    )


def _supplementary_file_from_payload(item: object) -> SupplementaryFile:
    if not isinstance(item, dict):
        raise ValueError("supplementary item must be an object")
    missing_fields = [name for name in _SUPPLEMENTARY_REQUIRED_FIELDS if name not in item]
    if missing_fields:
        raise ValueError(f"supplementary item is missing required field(s): {', '.join(missing_fields)}")

    # Filter at the bridge boundary so unused JSON fields do not leak into the plan model.
    payload = {name: item[name] for name in _SUPPLEMENTARY_PAYLOAD_FIELDS if name in item}
    return SupplementaryFile(**payload)


def _selected_items_from_payload(payload: dict, key: str, indices_text: str, label: str, build_item) -> list:
    items = payload.get(key, [])
    selected = []
    for index in _parse_indices(indices_text):
        if index < 0 or index >= len(items):
            raise IndexError(f"{label} index is out of range: {index}")
        selected.append(build_item(items[index]))
    return selected


def _ensure_any_selected(selected_fastq: list[FastqFile], selected_supp: list[SupplementaryFile]) -> None:
    if not selected_fastq and not selected_supp:
        raise GeoGetterError(SELECTION_REQUIRED)


def _prepare_download_output_dir(output_dir: str | Path, *, create_output_dir: bool = True) -> Path:
    raw_output_dir = str(output_dir)
    if not raw_output_dir.strip():
        raise GeoGetterError(
            OUTPUT_PATH_INVALID,
            "reason=output_required",
            extra={"path_error_code": "output_required"},
        )
    try:
        run_output_dir = Path(output_dir).expanduser().resolve()
    except OSError as exc:
        raise GeoGetterError(
            OUTPUT_PATH_INVALID,
            f"reason=resolve_failed path={raw_output_dir} error={exc}",
            extra={"path_error_code": "resolve_failed", "path": raw_output_dir, "error": str(exc)},
        ) from exc
    _ensure_preflight_path_lengths([run_output_dir])
    try:
        if run_output_dir.exists() and not run_output_dir.is_dir():
            raise GeoGetterError(
                OUTPUT_PATH_INVALID,
                f"reason=output_is_file path={run_output_dir}",
                extra={"path_error_code": "output_is_file", "output_dir": str(run_output_dir)},
            )
        if create_output_dir:
            run_output_dir.mkdir(parents=True, exist_ok=True)
    except GeoGetterError:
        raise
    except OSError as exc:
        raise GeoGetterError(
            OUTPUT_PATH_INVALID,
            f"reason=cannot_create_output path={run_output_dir} error={exc}",
            extra={"path_error_code": "cannot_create_output", "output_dir": str(run_output_dir), "error": str(exc)},
        ) from exc
    writable_dir = _preflight_writable_dir(run_output_dir)
    _assert_output_dir_writable(writable_dir)
    return run_output_dir


def _preflight_writable_dir(output_dir: Path) -> Path:
    current = output_dir if output_dir.exists() else output_dir.parent
    while True:
        if current.exists():
            if current.is_dir():
                return current
            raise GeoGetterError(
                OUTPUT_PATH_INVALID,
                f"reason=cannot_create_output path={output_dir} blocked_by={current}",
                extra={"path_error_code": "cannot_create_output", "output_dir": str(output_dir), "path": str(current)},
            )
        parent = current.parent
        if parent == current:
            break
        current = parent
    raise GeoGetterError(
        OUTPUT_PATH_INVALID,
        f"reason=parent_missing path={output_dir}",
        extra={"path_error_code": "parent_missing", "output_dir": str(output_dir)},
    )


def _assert_output_dir_writable(output_dir: Path) -> None:
    probe_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "wb",
            prefix=".geo_getter_preflight_",
            suffix=".tmp",
            dir=output_dir,
            delete=False,
        ) as handle:
            probe_path = Path(handle.name)
            handle.write(b"ok")
    except OSError as exc:
        if probe_path is not None:
            try:
                probe_path.unlink(missing_ok=True)
            except OSError:
                pass
        raise GeoGetterError(
            OUTPUT_PATH_INVALID,
            f"reason=cannot_write path={output_dir} error={exc}",
            extra={"path_error_code": "cannot_write", "output_dir": str(output_dir), "error": str(exc)},
        ) from exc
    if probe_path is not None:
        try:
            probe_path.unlink(missing_ok=True)
        except OSError as exc:
            raise GeoGetterError(
                OUTPUT_PATH_INVALID,
                f"reason=cannot_write path={output_dir} error={exc}",
                extra={"path_error_code": "cannot_write", "output_dir": str(output_dir), "error": str(exc)},
            ) from exc


def _ensure_preflight_path_lengths(paths: list[Path]) -> None:
    for path in paths:
        text = str(path)
        if _windows_utf16_code_units(text) >= 260:
            raise GeoGetterError(
                PATH_TOO_LONG,
                f"path={text}",
                extra={"path": text},
            )
        for part in _path_name_parts(path):
            if _windows_utf16_code_units(part) > 255:
                raise GeoGetterError(
                    PATH_TOO_LONG,
                    f"path={text} component={part}",
                    extra={"path": text},
                )


def _windows_utf16_code_units(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def _path_name_parts(path: Path) -> list[str]:
    anchor = path.anchor
    return [part for part in path.parts if part and part != anchor]


def _free_bytes_for_output(output_dir: Path) -> int:
    return shutil.disk_usage(_preflight_writable_dir(output_dir)).free


def _estimate_supplementary_size(url: str) -> tuple[int, str]:
    parsed = urllib.parse.urlparse(str(url))
    if parsed.scheme == "file":
        try:
            path = Path(urllib.request.url2pathname(parsed.path))
            if path.is_file():
                return path.stat().st_size, "known"
        except OSError:
            return 0, "unknown"
        return 0, "unknown"
    if parsed.scheme in {"http", "https"}:
        try:
            request = urllib.request.Request(str(url), method="HEAD", headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=30) as response:
                size = int(response.headers.get("Content-Length") or 0)
        except (OSError, ValueError, urllib.error.URLError):
            return 0, "unknown"
        if size > 0:
            return size, "known"
    return 0, "unknown"


def _preflight_planned_paths(cli_plan: CliDownloadPlan) -> list[Path]:
    planned_paths: list[Path] = [cli_plan.output_dir]
    if cli_plan.fastq_plan:
        planned_paths.append(fastq_manifest_path(cli_plan.output_dir))
        planned_paths.extend(
            path
            for planned in cli_plan.fastq_plan.files
            for path in download_runtime_paths(planned.local_path)
        )
    if cli_plan.planned_supplementary:
        planned_paths.append(supplementary_manifest_path(cli_plan.output_dir))
        planned_paths.extend(
            path
            for planned in cli_plan.planned_supplementary
            for path in download_runtime_paths(planned.local_path)
        )
    planned_paths.append(download_log_path(cli_plan.output_dir))
    return planned_paths


def _directory_has_entries(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        next(path.iterdir())
        return True
    except StopIteration:
        return False
    except OSError as exc:
        raise GeoGetterError(
            OUTPUT_PATH_INVALID,
            f"reason=cannot_read_output path={path} error={exc}",
            extra={"path_error_code": "cannot_read_output", "output_dir": str(path), "error": str(exc)},
        ) from exc


def _planned_supplementary_files(
    output_dir: Path,
    selected_supp: list[SupplementaryFile],
    reserved_names: list[str] | None = None,
) -> list[PlannedSupplementaryFile]:
    used_keys = {name_collision_key(name) for name in reserved_names or []}
    planned: list[PlannedSupplementaryFile] = []
    for item in selected_supp:
        local_path = plan_download_child_path(
            output_dir,
            item.name or "geo_supplementary_file",
            "geo_supplementary_file",
            used_keys,
        )
        planned.append(PlannedSupplementaryFile(supplementary=item, local_path=local_path))
    return planned


if __name__ == "__main__":
    raise SystemExit(run_cli())
