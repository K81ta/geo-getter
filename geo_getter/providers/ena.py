from __future__ import annotations

import urllib.parse
from typing import Any

from ..errors import GeoGetterError
from ..http_client import fetch_json
from ..models import FastqFile
from .download_urls import filename_from_url, normalize_download_url


ENA_FILE_REPORT_ENDPOINT = "https://www.ebi.ac.uk/ena/portal/api/filereport"

ENA_FIELDS = [
    "run_accession",
    "experiment_accession",
    "sample_accession",
    "secondary_sample_accession",
    "study_accession",
    "secondary_study_accession",
    "scientific_name",
    "instrument_platform",
    "library_layout",
    "library_strategy",
    "fastq_ftp",
    "fastq_md5",
    "fastq_bytes",
]


def get_fastq_files(accession: str, source_accession: str) -> list[FastqFile]:
    rows = fetch_file_report(accession)
    return parse_file_report(rows, source_accession=source_accession, query_accession=accession)


def fetch_file_report(accession: str) -> list[dict[str, Any]]:
    params = urllib.parse.urlencode(
        {
            "accession": accession,
            "result": "read_run",
            "fields": ",".join(ENA_FIELDS),
            "format": "json",
            "download": "false",
            "limit": "0",
        }
    )
    data = fetch_json(f"{ENA_FILE_REPORT_ENDPOINT}?{params}", timeout=90)
    if not isinstance(data, list):
        raise GeoGetterError("url_unavailable", f"Unexpected ENA API response type: {type(data).__name__}")
    return data


def parse_file_report(
    rows: list[dict[str, Any]], source_accession: str, query_accession: str
) -> list[FastqFile]:
    fastq_files: list[FastqFile] = []
    for row in rows:
        md5s = _split_positional_values(row.get("fastq_md5", ""))
        sizes = _split_positional_values(row.get("fastq_bytes", ""))
        for file_index, raw_url in enumerate(_split_positional_values(row.get("fastq_ftp", ""))):
            if not raw_url:
                continue
            download_url = normalize_download_url(raw_url)
            if not download_url:
                continue
            expected_md5 = md5s[file_index] if file_index < len(md5s) else ""
            size_value = sizes[file_index] if file_index < len(sizes) else ""
            fastq_files.append(
                FastqFile(
                    source_accession=source_accession,
                    query_accession=query_accession,
                    run_accession=_clean_metadata_value(row.get("run_accession", "")),
                    file_index=file_index + 1,
                    file_name=filename_from_url(
                        download_url,
                        default="download.fastq.gz",
                        sanitize=True,
                    ),
                    url=download_url,
                    expected_md5=expected_md5,
                    size_bytes=_int_or_zero(size_value),
                    experiment_accession=_clean_metadata_value(row.get("experiment_accession", "")),
                    sample_accession=_clean_metadata_value(row.get("sample_accession", "")),
                    secondary_sample_accession=_clean_metadata_value(row.get("secondary_sample_accession", "")),
                    study_accession=_clean_metadata_value(row.get("study_accession", "")),
                    secondary_study_accession=_clean_metadata_value(row.get("secondary_study_accession", "")),
                    scientific_name=_clean_metadata_value(row.get("scientific_name", "")),
                    instrument_platform=_clean_metadata_value(row.get("instrument_platform", "")),
                    library_layout=_clean_metadata_value(row.get("library_layout", "")),
                    library_strategy=_clean_metadata_value(row.get("library_strategy", "")),
                )
            )
    return fastq_files


def _clean_metadata_value(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _split_positional_values(value: Any) -> list[str]:
    if value is None:
        return []
    return [item.strip() for item in str(value).split(";")]


def _int_or_zero(value: str) -> int:
    try:
        return int(value)
    except ValueError:
        return 0
