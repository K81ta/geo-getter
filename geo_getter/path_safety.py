from __future__ import annotations

from pathlib import Path
from typing import Iterable


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


DOWNLOAD_RESERVED_SUFFIXES = (".part",)


def reserve_unique_name(file_name: str, used_keys: set[str], reserved_suffixes: Iterable[str] = ()) -> str:
    count = 0
    candidate = file_name
    while any(name_collision_key(name) in used_keys for name in reserved_runtime_names(candidate, reserved_suffixes)):
        count += 1
        candidate = unique_numbered_name(file_name, count)
    for name in reserved_runtime_names(candidate, reserved_suffixes):
        used_keys.add(name_collision_key(name))
    return candidate


def reserve_unique_download_name(file_name: str, used_keys: set[str]) -> str:
    return reserve_unique_name(file_name, used_keys, DOWNLOAD_RESERVED_SUFFIXES)


def reserved_download_names(file_name: str) -> tuple[str, ...]:
    return reserved_runtime_names(file_name, DOWNLOAD_RESERVED_SUFFIXES)


def reserved_runtime_names(file_name: str, reserved_suffixes: Iterable[str]) -> tuple[str, ...]:
    return (file_name, *(f"{file_name}{suffix}" for suffix in reserved_suffixes))


def name_collision_key(file_name: str) -> str:
    return str(file_name).casefold()


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
