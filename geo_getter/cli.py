from __future__ import annotations

import argparse
import csv
import json
import sys
import urllib.error
from pathlib import Path

from . import __version__
from .downloader import download_plan, download_url_to_part, finalize_downloaded_part
from .errors import DOWNLOAD_COMPLETE, MD5_VERIFIED, NETWORK_FAILED
from .models import FastqFile
from .path_safety import child_path, safe_file_name, unique_numbered_name
from .planner import (
    append_download_log,
    build_download_plan,
    download_log_path,
    fastq_manifest_path,
    initialize_log,
    supplementary_manifest_path,
    verify_fastq_manifest,
)
from .providers.resolver import MetadataResolver


def main(argv: list[str] | None = None) -> int:
    argv_list = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(description="GEOGetter internal GUI bridge")
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

    if argv_list and argv_list[0] == "verify-manifest-json":
        verify_manifest_parser = subparsers.add_parser("verify-manifest-json", help=argparse.SUPPRESS)
        verify_manifest_parser.add_argument("--manifest", required=True)

    args = parser.parse_args(argv_list)
    if args.command == "resolve-json":
        return _resolve_json(args.input_text, args.input_file, args.out_json)
    if args.command == "selected-download-json":
        return _selected_download_json(Path(args.input_json), args.fastq_indices, args.supp_indices, Path(args.out))
    if args.command == "verify-manifest-json":
        return _verify_manifest_json(Path(args.manifest))
    return 2


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


def _selected_download_json(input_json: Path, fastq_indices: str, supp_indices: str, output_dir: Path) -> int:
    payload = _load_json(input_json)
    selected_fastq = _selected_fastq_from_payload(payload, fastq_indices) if fastq_indices.strip() else []
    selected_supp = _selected_supplementary_from_payload(payload, supp_indices) if supp_indices.strip() else []
    _ensure_any_selected(selected_fastq, selected_supp)

    statuses: list[str] = []
    output_dir.mkdir(parents=True, exist_ok=True)
    run_output_dir = _new_accession_output_dir(output_dir, str(payload.get("primary_accession", "")))
    run_output_dir.mkdir(parents=True, exist_ok=True)
    if selected_fastq:
        plan = build_download_plan(payload["input_text"], payload["primary_accession"], selected_fastq, run_output_dir)

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

        results = download_plan(plan, progress_callback=progress, message_callback=message)
        statuses.extend(status for _planned, status, _message in results)
    else:
        initialize_log(run_output_dir)

    if selected_supp:
        _write_supplementary_manifest(run_output_dir, selected_supp)
        statuses.extend(_download_supplementary_files(run_output_dir, selected_supp))

    print(
        json.dumps(
            {
                "event": "done",
                "statuses": statuses,
                "output_dir": str(run_output_dir),
                "fastq_manifest": str(fastq_manifest_path(run_output_dir)) if selected_fastq else "",
                "supplementary_manifest": str(supplementary_manifest_path(run_output_dir)) if selected_supp else "",
                "download_log": str(download_log_path(run_output_dir)),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    ok_statuses = {MD5_VERIFIED, DOWNLOAD_COMPLETE}
    return 0 if statuses and all(status in ok_statuses for status in statuses) else 1


def _new_accession_output_dir(output_root: Path, primary_accession: str) -> Path:
    base_name = safe_file_name(primary_accession.strip() or "geo_getter_download", "geo_getter_download")
    output_root = output_root.resolve()
    candidate = child_path(output_root, base_name)
    if not candidate.exists() or _is_empty_directory(candidate):
        return candidate
    counter = 2
    while True:
        candidate = child_path(output_root, f"{base_name}_{counter}")
        if not candidate.exists() or _is_empty_directory(candidate):
            return candidate
        counter += 1


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


def _is_empty_directory(path: Path) -> bool:
    if not path.is_dir():
        return False
    try:
        next(path.iterdir())
    except StopIteration:
        return True
    return False


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


def _write_supplementary_manifest(output_dir: Path, selected_supp: list[dict]) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = supplementary_manifest_path(output_dir)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(["source_accession", "scope", "file_name", "url", "local_path", "status"])
        for item, local_path in _planned_supplementary_files(output_dir, selected_supp):
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


def _download_supplementary_files(output_dir: Path, selected_supp: list[dict]) -> list[str]:
    statuses: list[str] = []
    for item, local_path in _planned_supplementary_files(output_dir, selected_supp):
        file_name = local_path.name
        url = item.get("url", "")
        downloaded = 0
        print(json.dumps({"event": "message", "message": f"supplementary_download_started: {file_name}"}, ensure_ascii=False), flush=True)
        try:
            if local_path.exists():
                local_path.replace(_unique_existing_path(local_path))

            def progress(current: int, total: int) -> None:
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

            _part_path, downloaded = download_url_to_part(
                url,
                local_path,
                progress_callback=progress,
                message_callback=lambda text: print(json.dumps({"event": "message", "message": text}, ensure_ascii=False), flush=True),
            )
            finalize_downloaded_part(local_path)
            status = DOWNLOAD_COMPLETE
            message = "Saved GEO supplementary/processed file. It was not verified because GEO SOFT does not provide a stable expected MD5 value."
        except (urllib.error.URLError, OSError, ValueError) as exc:
            status = NETWORK_FAILED
            message = str(exc)
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


def _planned_supplementary_files(output_dir: Path, selected_supp: list[dict]) -> list[tuple[dict, Path]]:
    counts: dict[str, int] = {}
    planned: list[tuple[dict, Path]] = []
    for item in selected_supp:
        file_name = safe_file_name(item.get("name", "") or "geo_supplementary_file", "geo_supplementary_file")
        count = counts.get(file_name, 0)
        counts[file_name] = count + 1
        file_name = unique_numbered_name(file_name, count)
        planned.append((item, child_path(output_dir, file_name)))
    return planned


def _unique_existing_path(path: Path) -> Path:
    candidate = path.with_name(path.name + ".existing")
    counter = 2
    while candidate.exists():
        candidate = path.with_name(f"{path.name}.existing.{counter}")
        counter += 1
    return candidate


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
