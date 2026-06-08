from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class DatasetMetadata:
    accession: str
    status: str = ""
    title: str = ""
    organism: str = ""
    experiment_type: str = ""
    summary: str = ""
    overall_design: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SupplementaryFile:
    source_accession: str
    scope: str
    name: str
    url: str
    origin_level: str = "unknown"
    origin_accession: str = ""
    extension: str = ""
    estimated_type: str = "other"
    size_status: str = "unknown"
    verification_status: str = "not_applicable"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FastqFile:
    source_accession: str
    query_accession: str
    run_accession: str
    file_index: int
    file_name: str
    url: str
    expected_md5: str
    size_bytes: int
    experiment_accession: str = ""
    sample_accession: str = ""
    secondary_sample_accession: str = ""
    study_accession: str = ""
    secondary_study_accession: str = ""
    scientific_name: str = ""
    instrument_platform: str = ""
    library_layout: str = ""
    library_strategy: str = ""
    geo_sample_accession: str = ""
    geo_sample_title: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ResolveResult:
    input_text: str
    primary_accession: str
    query_accessions: list[str]
    fastq_files: list[FastqFile]
    supplementary_files: list[SupplementaryFile]
    dataset_metadata: DatasetMetadata
    warnings: list[str]


@dataclass(frozen=True)
class PlannedFile:
    fastq: FastqFile
    local_path: Path

    def to_dict(self) -> dict[str, Any]:
        data = self.fastq.to_dict()
        data["local_path"] = str(self.local_path)
        return data


@dataclass(frozen=True)
class DownloadPlan:
    app_version: str
    created_at: str
    input_text: str
    primary_accession: str
    output_dir: Path
    total_bytes: int
    available_bytes: int
    files: list[PlannedFile]

    def to_dict(self) -> dict[str, Any]:
        return {
            "app_version": self.app_version,
            "created_at": self.created_at,
            "input_text": self.input_text,
            "primary_accession": self.primary_accession,
            "output_dir": str(self.output_dir),
            "total_bytes": self.total_bytes,
            "available_bytes": self.available_bytes,
            "files": [item.to_dict() for item in self.files],
        }
