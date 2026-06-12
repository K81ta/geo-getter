from __future__ import annotations

import hashlib
from pathlib import Path


def _calculate_file_digest(path: str | Path, algorithm: str) -> str:
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, algorithm).hexdigest()


def calculate_md5(path: str | Path) -> str:
    return _calculate_file_digest(path, "md5")


def calculate_sha256(path: str | Path) -> str:
    return _calculate_file_digest(path, "sha256")


def verify_md5(path: str | Path, expected_md5: str) -> tuple[bool, str]:
    actual = calculate_md5(path)
    return actual.lower() == expected_md5.lower(), actual
