from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Protocol

DEFAULT_CHUNK_SIZE = 1024 * 1024


class Digest(Protocol):
    def update(self, data: bytes, /) -> None:
        ...

    def hexdigest(self) -> str:
        ...


_DIGEST_FACTORIES = {
    "md5": hashlib.md5,
    "sha256": hashlib.sha256,
}


def new_digest(algorithm: str) -> Digest:
    return _DIGEST_FACTORIES[_normalize_algorithm(algorithm)]()


def calculate_digest(path: str | Path, algorithm: str, chunk_size: int = DEFAULT_CHUNK_SIZE) -> str:
    if chunk_size <= 0:
        raise ValueError(f"chunk_size must be positive: {chunk_size}")
    digest = new_digest(algorithm)
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_digest(
    path: str | Path,
    expected: str,
    algorithm: str,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> tuple[bool, str]:
    actual = calculate_digest(path, algorithm, chunk_size=chunk_size)
    return actual.lower() == expected.lower(), actual


def calculate_md5(path: str | Path, chunk_size: int = DEFAULT_CHUNK_SIZE) -> str:
    return calculate_digest(path, "md5", chunk_size=chunk_size)


def calculate_sha256(path: str | Path, chunk_size: int = DEFAULT_CHUNK_SIZE) -> str:
    return calculate_digest(path, "sha256", chunk_size=chunk_size)


def verify_md5(path: str | Path, expected_md5: str, chunk_size: int = DEFAULT_CHUNK_SIZE) -> tuple[bool, str]:
    return verify_digest(path, expected_md5, "md5", chunk_size=chunk_size)


def _normalize_algorithm(algorithm: str) -> str:
    normalized = str(algorithm).lower()
    if normalized not in _DIGEST_FACTORIES:
        raise ValueError(f"Unsupported digest algorithm: {algorithm}")
    return normalized
