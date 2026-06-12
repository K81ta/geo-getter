from __future__ import annotations

import posixpath
import urllib.parse
from typing import Any

from ..errors import GeoGetterError
from ..http_client import fetch_json
from ..models import FastqFile
from ..path_safety import safe_file_name


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
    "submitted_ftp",
    "submitted_md5",
    "submitted_bytes",
]


class EnaProvider:
    def get_fastq_files(self, accession: str, source_accession: str) -> list[FastqFile]:
        rows = self.fetch_file_report(accession)
        return parse_file_report(rows, source_accession=source_accession, query_accession=accession)

    def fetch_file_report(self, accession: str) -> list[dict[str, Any]]:
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
        urls = _split_url_values(row.get("fastq_ftp", ""))
        md5s = _split_positional_values(row.get("fastq_md5", ""))
        sizes = _split_positional_values(row.get("fastq_bytes", ""))
        for file_index, raw_url in urls:
            if not raw_url:
                continue
            download_url = _download_url(raw_url)
            if not download_url:
                continue
            fastq_files.append(
                FastqFile(
                    source_accession=source_accession,
                    query_accession=query_accession,
                    run_accession=_clean_metadata_value(row.get("run_accession", "")),
                    file_index=file_index + 1,
                    file_name=_file_name_from_url(raw_url),
                    url=download_url,
                    expected_md5=_value_at(md5s, file_index),
                    size_bytes=_int_at(sizes, file_index),
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


def _split_url_values(value: Any) -> list[tuple[int, str]]:
    return [(index, item) for index, item in enumerate(_split_positional_values(value)) if item]


def _split_positional_values(value: Any) -> list[str]:
    if value is None:
        return []
    return [item.strip() for item in str(value).split(";")]


def _value_at(values: list[str], index: int) -> str:
    if 0 <= index < len(values):
        return values[index]
    return ""


def _int_at(values: list[str], index: int) -> int:
    value = _value_at(values, index)
    try:
        return int(value)
    except ValueError:
        return 0


def _download_url(raw_url: str) -> str:
    if raw_url.startswith("http://") or raw_url.startswith("https://"):
        return raw_url
    if raw_url.startswith("ftp://ftp.sra.ebi.ac.uk/"):
        return "https://ftp.sra.ebi.ac.uk/" + raw_url.removeprefix("ftp://ftp.sra.ebi.ac.uk/")
    if raw_url.startswith("ftp.sra.ebi.ac.uk/"):
        return "https://" + raw_url
    if raw_url.startswith("fasp.sra.ebi.ac.uk/"):
        return ""
    return raw_url


def _file_name_from_url(url: str) -> str:
    parsed = urllib.parse.urlparse(_download_url(url))
    basename = posixpath.basename(parsed.path)
    return safe_file_name(urllib.parse.unquote(basename or "download.fastq.gz"), "download.fastq.gz")
