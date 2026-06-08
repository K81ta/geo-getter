from __future__ import annotations

import ctypes
import os
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
    return _lower_invariant(str(file_name))


def _lower_invariant(value: str) -> str:
    if os.name == "nt":
        return _windows_lower_invariant(value)
    return "".join(_fallback_lower_invariant_char(char) for char in value)


def _windows_lower_invariant(value: str) -> str:
    needed = _LC_MAP_STRING_EX(
        _LOCALE_NAME_INVARIANT,
        _LCMAP_LOWERCASE,
        value,
        -1,
        None,
        0,
        None,
        None,
        0,
    )
    if needed <= 0:
        raise OSError(ctypes.get_last_error(), "LCMapStringEx failed")
    buffer = ctypes.create_unicode_buffer(needed)
    result = _LC_MAP_STRING_EX(
        _LOCALE_NAME_INVARIANT,
        _LCMAP_LOWERCASE,
        value,
        -1,
        buffer,
        needed,
        None,
        None,
        0,
    )
    if result <= 0:
        raise OSError(ctypes.get_last_error(), "LCMapStringEx failed")
    return buffer.value


def _fallback_lower_invariant_char(char: str) -> str:
    if char in _FALLBACK_PRESERVE_CHARS:
        return char
    return char.lower()


_LCMAP_LOWERCASE = 0x00000100
_LOCALE_NAME_INVARIANT = ""
_FALLBACK_PRESERVE_CHARS = {
    "\u0130",  # Latin capital I with dot above
    "\u1e9e",  # Latin capital sharp S
    "\u212a",  # Kelvin sign
    "\u212b",  # Angstrom sign
}
if os.name == "nt":
    _LC_MAP_STRING_EX = ctypes.WinDLL("kernel32", use_last_error=True).LCMapStringEx
    _LC_MAP_STRING_EX.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_uint,
        ctypes.c_wchar_p,
        ctypes.c_int,
        ctypes.c_wchar_p,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_long,
    ]
    _LC_MAP_STRING_EX.restype = ctypes.c_int


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
