from __future__ import annotations

from collections.abc import Callable
from pathlib import Path


WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}
SidecarCandidateFactory = Callable[[Path, int], Path]


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


def existing_size(path: Path) -> int:
    try:
        if not path.is_file():
            return 0
        return path.stat().st_size
    except OSError:
        return 0


def download_part_path(local_path: Path) -> Path:
    return local_path.with_name(local_path.name + ".part")


def existing_candidate_path(path: Path, counter: int = 1) -> Path:
    return _numbered_sidecar_path(path, ".existing", counter)


def quarantine_candidate_path(path: Path, reason: str, timestamp: str, counter: int = 1) -> Path:
    return _numbered_sidecar_path(path, f".{reason}-{timestamp}", counter)


def unique_existing_path(path: Path) -> Path:
    return first_available_sidecar_path(path, existing_candidate_path)


def unique_quarantine_path(path: Path, reason: str, timestamp: str) -> Path:
    return first_available_sidecar_path(
        path,
        lambda candidate_path, counter: quarantine_candidate_path(candidate_path, reason, timestamp, counter),
    )


def download_runtime_paths(local_path: Path) -> list[Path]:
    return [local_path, download_part_path(local_path)]


def first_available_sidecar_path(path: Path, candidate_factory: SidecarCandidateFactory) -> Path:
    candidate = candidate_factory(path, 1)
    counter = 2
    while candidate.exists():
        candidate = candidate_factory(path, counter)
        counter += 1
    return candidate


def reserve_unique_download_name(file_name: str, used_keys: set[str]) -> str:
    count = 0
    candidate = file_name
    while name_collision_key(candidate) in used_keys or name_collision_key(f"{candidate}.part") in used_keys:
        count += 1
        candidate = unique_numbered_name(file_name, count)
    used_keys.add(name_collision_key(candidate))
    used_keys.add(name_collision_key(f"{candidate}.part"))
    return candidate


def name_collision_key(file_name: str) -> str:
    return str(file_name).casefold()


def split_download_name(file_name: str) -> tuple[str, str]:
    if file_name.endswith(".fastq.gz"):
        return file_name[:-9], ".fastq.gz"
    path = Path(file_name)
    return path.stem, path.suffix


def _numbered_sidecar_path(path: Path, suffix: str, counter: int) -> Path:
    name = f"{path.name}{suffix}"
    if counter > 1:
        name = f"{name}.{counter}"
    return path.with_name(name)


def _is_unsafe_char(char: str) -> bool:
    return char in '<>:"/\\|?*' or ord(char) < 32 or ord(char) == 127


def _is_windows_reserved_name(file_name: str) -> bool:
    stem = file_name.split(".", 1)[0].upper()
    return stem in WINDOWS_RESERVED_NAMES
