from __future__ import annotations

from pathlib import Path


WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


def safe_file_name(value: object, default: str) -> str:
    raw = "" if value is None else str(value)
    safe = "".join("_" if _is_unsafe_char(char) else char for char in raw).strip(" .")
    if safe in {"", ".", ".."}:
        safe = default
    if _is_windows_reserved_name(safe):
        safe = f"_{safe}"
    return safe


def child_path(parent: Path, file_name: str) -> Path:
    parent_resolved = parent.resolve()
    candidate = (parent_resolved / file_name).resolve()
    try:
        candidate.relative_to(parent_resolved)
    except ValueError as exc:
        raise ValueError(f"Unsafe output path escapes the output directory: {file_name}") from exc
    return candidate


def unique_numbered_name(file_name: str, count: int) -> str:
    if count <= 0:
        return file_name
    stem, suffix = split_download_name(file_name)
    return f"{stem}.{count + 1}{suffix}"


def split_download_name(file_name: str) -> tuple[str, str]:
    if file_name.endswith(".fastq.gz"):
        return file_name[:-9], ".fastq.gz"
    path = Path(file_name)
    return path.stem, path.suffix


def _is_unsafe_char(char: str) -> bool:
    return char in '<>:"/\\|?*' or ord(char) < 32 or ord(char) == 127


def _is_windows_reserved_name(file_name: str) -> bool:
    stem = file_name.split(".", 1)[0].upper()
    return stem in WINDOWS_RESERVED_NAMES
