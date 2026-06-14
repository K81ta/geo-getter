from __future__ import annotations

from collections.abc import Iterator
import re
import urllib.parse
from dataclasses import dataclass, field

from ..accession import ENA_QUERY_PREFIXES, find_supported_accessions
from ..errors import GeoGetterError
from ..http_client import fetch_text
from ..models import DatasetMetadata, SupplementaryFile
from .download_urls import filename_from_url


GEO_SOFT_ENDPOINT = "https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi"


@dataclass(frozen=True)
class GeoSampleMetadata:
    geo_sample_accession: str
    geo_sample_title: str


@dataclass
class GeoSoftParseResult:
    related_accessions: list[str]
    supplementary_files: list[SupplementaryFile]
    sample_metadata_by_accession: dict[str, GeoSampleMetadata]
    dataset_metadata: DatasetMetadata
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class SoftRecord:
    record_type: str
    accession: str
    entries: tuple[tuple[str, str], ...]


@dataclass
class _SoftParseState:
    series_related: list[str] = field(default_factory=list)
    sample_related: list[str] = field(default_factory=list)
    supplementary: list[SupplementaryFile] = field(default_factory=list)
    sample_metadata_by_accession: dict[str, GeoSampleMetadata] = field(
        default_factory=dict
    )
    seen_series_related: set[str] = field(default_factory=set)
    seen_sample_related: set[str] = field(default_factory=set)
    seen_supplementary: set[str] = field(default_factory=set)
    dataset_status: str = ""
    dataset_title: str = ""
    dataset_summary_parts: list[str] = field(default_factory=list)
    dataset_design_parts: list[str] = field(default_factory=list)
    dataset_experiment_types: list[str] = field(default_factory=list)
    dataset_organisms: list[str] = field(default_factory=list)
    first_sample_status: str = ""
    first_sample_title: str = ""
    first_sample_description_parts: list[str] = field(default_factory=list)


def fetch_soft(accession: str) -> str:
    params = urllib.parse.urlencode(
        {"acc": accession, "targ": "self", "form": "text", "view": "full"}
    )
    return fetch_text(f"{GEO_SOFT_ENDPOINT}?{params}", timeout=90)


def get_related(accession: str) -> GeoSoftParseResult:
    primary = parse_soft(fetch_soft(accession), accession)
    if not accession.startswith("GSE"):
        return primary
    try:
        samples = parse_soft(fetch_gsm_soft(accession), accession)
    except GeoGetterError as exc:
        primary.warnings.append(
            "GEO sample metadata retrieval failed; sample-level details may be incomplete. "
            f"Detail: {exc.code}"
        )
        return primary
    return _merge_parse_results(primary, samples)


def fetch_gsm_soft(accession: str) -> str:
    params = urllib.parse.urlencode(
        {"acc": accession, "targ": "gsm", "form": "text", "view": "brief"}
    )
    return fetch_text(f"{GEO_SOFT_ENDPOINT}?{params}", timeout=90)


def parse_soft(text: str, source_accession: str) -> GeoSoftParseResult:
    state = _SoftParseState()
    for record in iter_soft_records(text):
        if record.record_type == "SERIES":
            _parse_series_record(record, source_accession, state)
        elif record.record_type == "SAMPLE":
            _parse_sample_record(record, source_accession, state)
        else:
            _parse_other_record(record, source_accession, state)
    return _build_parse_result(state, source_accession)


def iter_soft_records(text: str) -> Iterator[SoftRecord]:
    current_type = ""
    current_accession = ""
    current_entries: list[tuple[str, str]] = []

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("^"):
            if current_type:
                yield SoftRecord(current_type, current_accession, tuple(current_entries))
            current_type, current_accession = _split_soft_line(line.replace("^", "!", 1))
            current_entries = []
            continue
        if not current_type or not line.startswith("!"):
            continue
        key, value = _split_soft_line(line)
        if key:
            current_entries.append((key, value))

    if current_type:
        yield SoftRecord(current_type, current_accession, tuple(current_entries))


def _parse_series_record(record: SoftRecord, source_accession: str, state: _SoftParseState) -> None:
    for key, value in record.entries:
        if key == "Series_status" and value:
            state.dataset_status = value
        elif key == "Series_title" and value:
            state.dataset_title = value
        elif key == "Series_type" and value:
            _append_unique(state.dataset_experiment_types, value)
        elif key == "Series_summary" and value:
            state.dataset_summary_parts.append(value)
        elif key == "Series_overall_design" and value:
            state.dataset_design_parts.append(value)

        if key.endswith("_relation"):
            _append_related_accessions(key, value, state)

        _append_supplementary_file(key, value, source_accession, "", state)


def _parse_sample_record(record: SoftRecord, source_accession: str, state: _SoftParseState) -> None:
    sample_title = ""
    sample_relations: list[str] = []

    for key, value in record.entries:
        if key == "Sample_title":
            sample_title = value
            if value and not state.first_sample_title:
                state.first_sample_title = value

        if key == "Sample_status" and value and not state.first_sample_status:
            state.first_sample_status = value

        if key.startswith("Sample_organism_ch") and value:
            _append_unique(state.dataset_organisms, value)

        if key == "Sample_description" and value:
            state.first_sample_description_parts.append(value)

        if key.endswith("_relation"):
            sample_relations.extend(_append_related_accessions(key, value, state))

        _append_supplementary_file(key, value, source_accession, record.accession, state)

    metadata = GeoSampleMetadata(
        geo_sample_accession=record.accession,
        geo_sample_title=sample_title,
    )
    for accession in sample_relations:
        state.sample_metadata_by_accession.setdefault(accession, metadata)


def _parse_other_record(record: SoftRecord, source_accession: str, state: _SoftParseState) -> None:
    for key, value in record.entries:
        _append_supplementary_file(key, value, source_accession, "", state)


def _append_related_accessions(key: str, value: str, state: _SoftParseState) -> list[str]:
    matched_sample_accessions: list[str] = []
    for accession in find_supported_accessions(value):
        if not accession.startswith(ENA_QUERY_PREFIXES):
            continue
        if key.startswith("Series_") and accession not in state.seen_series_related:
            state.series_related.append(accession)
            state.seen_series_related.add(accession)
        elif key.startswith("Sample_") and accession not in state.seen_sample_related:
            state.sample_related.append(accession)
            state.seen_sample_related.add(accession)
        if key.startswith("Sample_"):
            matched_sample_accessions.append(accession)
    return matched_sample_accessions


def _append_supplementary_file(
    key: str,
    value: str,
    source_accession: str,
    sample_accession: str,
    state: _SoftParseState,
) -> None:
    if "_supplementary_file" not in key or not value or value.upper() == "NONE":
        return
    normalized_url = value.strip()
    if normalized_url in state.seen_supplementary:
        return
    name = filename_from_url(normalized_url, default=normalized_url)
    origin_level = _origin_level_from_soft_key(key)
    state.supplementary.append(
        SupplementaryFile(
            source_accession=source_accession,
            scope=_scope_from_soft_key(key),
            name=name,
            url=normalized_url,
            origin_level=origin_level,
            origin_accession=_origin_accession(origin_level, source_accession, sample_accession),
            extension=_extension_from_name(name),
            estimated_type=_estimated_supplementary_type(name),
        )
    )
    state.seen_supplementary.add(normalized_url)


def _build_parse_result(state: _SoftParseState, source_accession: str) -> GeoSoftParseResult:
    return GeoSoftParseResult(
        related_accessions=_merge_related_accessions(state.series_related, state.sample_related),
        supplementary_files=state.supplementary,
        sample_metadata_by_accession=state.sample_metadata_by_accession,
        dataset_metadata=DatasetMetadata(
            accession=source_accession,
            status=state.dataset_status or state.first_sample_status,
            title=state.dataset_title or state.first_sample_title,
            organism=_join_unique_values(state.dataset_organisms),
            experiment_type=_join_unique_values(state.dataset_experiment_types),
            summary=_join_soft_text(
                state.dataset_summary_parts or state.first_sample_description_parts
            ),
            overall_design=_join_soft_text(state.dataset_design_parts),
        ),
    )


def _merge_parse_results(primary: GeoSoftParseResult, samples: GeoSoftParseResult) -> GeoSoftParseResult:
    return GeoSoftParseResult(
        related_accessions=_merge_related_accessions(
            primary.related_accessions, samples.related_accessions
        ),
        supplementary_files=_deduplicate_supplementary(
            [*primary.supplementary_files, *samples.supplementary_files]
        ),
        sample_metadata_by_accession={
            **samples.sample_metadata_by_accession,
            **primary.sample_metadata_by_accession,
        },
        dataset_metadata=_merge_dataset_metadata(primary.dataset_metadata, samples.dataset_metadata),
        warnings=[*primary.warnings, *samples.warnings],
    )


def _merge_related_accessions(*accession_lists: list[str]) -> list[str]:
    return list(
        dict.fromkeys(accession for accessions in accession_lists for accession in accessions)
    )


def _merge_dataset_metadata(primary: DatasetMetadata, samples: DatasetMetadata) -> DatasetMetadata:
    return DatasetMetadata(
        accession=primary.accession,
        status=primary.status or samples.status,
        title=primary.title or samples.title,
        organism=primary.organism or samples.organism,
        experiment_type=primary.experiment_type or samples.experiment_type,
        summary=primary.summary or samples.summary,
        overall_design=primary.overall_design or samples.overall_design,
    )


def _deduplicate_supplementary(files: list[SupplementaryFile]) -> list[SupplementaryFile]:
    seen: set[str] = set()
    unique: list[SupplementaryFile] = []
    for item in files:
        if item.url in seen:
            continue
        unique.append(item)
        seen.add(item.url)
    return unique


def _split_soft_line(line: str) -> tuple[str, str]:
    match = re.match(r"^!(?P<key>[^=]+?)\s*=\s*(?P<value>.*)$", line)
    if not match:
        return "", ""
    return match.group("key").strip(), match.group("value").strip()


def _scope_from_soft_key(key: str) -> str:
    if key.startswith("Series"):
        return "GEO Series supplementary/processed"
    if key.startswith("Sample"):
        return "GEO Sample supplementary/processed"
    return "GEO supplementary/processed"


def _origin_level_from_soft_key(key: str) -> str:
    if key.startswith("Series"):
        return "series"
    if key.startswith("Sample"):
        return "sample"
    return "unknown"


def _origin_accession(origin_level: str, source_accession: str, current_sample: str) -> str:
    if origin_level == "sample" and current_sample:
        return current_sample
    if origin_level == "series":
        return source_accession
    return source_accession


COMPOUND_EXTENSIONS = (
    ".fastq.gz",
    ".fq.gz",
    ".tar.gz",
    ".tsv.gz",
    ".csv.gz",
    ".txt.gz",
    ".bed.gz",
    ".bedgraph.gz",
)


def _extension_from_name(name: str) -> str:
    lowered = name.lower()
    for extension in COMPOUND_EXTENSIONS:
        if lowered.endswith(extension):
            return extension
    match = re.search(r"(?<!^)(\.[A-Za-z0-9][A-Za-z0-9_-]*)$", name)
    if not match:
        return ""
    return match.group(1).lower()


def _estimated_supplementary_type(name: str) -> str:
    lowered = name.lower()
    extension = _extension_from_name(name)
    if re.search(r"(^|[_.-])raw([_.-]|$)", lowered) and extension in {".tar", ".tar.gz", ".tgz", ".gz"}:
        return "geo_raw_archive"
    if extension in {".fastq", ".fastq.gz", ".fq", ".fq.gz"}:
        return "fastq_like_supplementary"
    if "count" in lowered or "matrix" in lowered:
        return "count_matrix"
    if extension in {".bigwig", ".bw", ".bedgraph", ".bedgraph.gz", ".bed", ".bed.gz", ".wig"}:
        return "genome_track"
    if extension in {".txt", ".txt.gz", ".csv", ".csv.gz", ".tsv", ".tsv.gz", ".xls", ".xlsx"}:
        return "table_text"
    if extension in {".tar", ".tar.gz", ".tgz", ".zip", ".gz", ".bz2", ".xz", ".7z"}:
        return "archive"
    return "other"


def _join_soft_text(parts: list[str]) -> str:
    return " ".join(part.strip() for part in parts if part.strip())


def _append_unique(values: list[str], value: str) -> None:
    stripped = value.strip()
    if stripped and stripped not in values:
        values.append(stripped)


def _join_unique_values(values: list[str]) -> str:
    return "; ".join(values)
