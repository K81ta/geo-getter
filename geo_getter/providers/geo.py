from __future__ import annotations

import posixpath
import re
import urllib.parse
from dataclasses import dataclass, field

from ..accession import find_supported_accessions
from ..errors import GeoGetterError
from ..http_client import fetch_text
from ..models import DatasetMetadata, SupplementaryFile


GEO_SOFT_ENDPOINT = "https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi"
RELATED_PREFIXES = (
    "SRP",
    "SRX",
    "SRR",
    "SRS",
    "ERP",
    "ERX",
    "ERR",
    "ERS",
    "DRP",
    "DRX",
    "DRR",
    "DRS",
    "PRJNA",
    "PRJEB",
    "PRJDB",
    "SAMN",
    "SAMEA",
    "SAMD",
)


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


class GeoProvider:
    def fetch_soft(self, accession: str) -> str:
        params = urllib.parse.urlencode(
            {"acc": accession, "targ": "self", "form": "text", "view": "full"}
        )
        return fetch_text(f"{GEO_SOFT_ENDPOINT}?{params}", timeout=90)

    def get_related(self, accession: str) -> GeoSoftParseResult:
        primary = parse_soft(self.fetch_soft(accession), accession)
        if not accession.startswith("GSE"):
            return primary
        try:
            samples = parse_soft(self.fetch_gsm_soft(accession), accession)
        except GeoGetterError as exc:
            primary.warnings.append(f"GEO sample metadata retrieval failed; sample-level details may be incomplete. Detail: {exc.code}")
            return primary
        return _merge_parse_results(primary, samples)

    def fetch_gsm_soft(self, accession: str) -> str:
        params = urllib.parse.urlencode(
            {"acc": accession, "targ": "gsm", "form": "text", "view": "brief"}
        )
        return fetch_text(f"{GEO_SOFT_ENDPOINT}?{params}", timeout=90)


def parse_soft(text: str, source_accession: str) -> GeoSoftParseResult:
    series_related: list[str] = []
    sample_related: list[str] = []
    supplementary: list[SupplementaryFile] = []
    sample_metadata_by_accession: dict[str, GeoSampleMetadata] = {}
    seen_series_related: set[str] = set()
    seen_sample_related: set[str] = set()
    seen_supplementary: set[str] = set()
    current_sample = ""
    current_sample_title = ""
    current_sample_relations: list[str] = []
    dataset_status = ""
    dataset_title = ""
    dataset_summary_parts: list[str] = []
    dataset_design_parts: list[str] = []
    dataset_experiment_types: list[str] = []
    dataset_organisms: list[str] = []
    first_sample_status = ""
    first_sample_title = ""
    first_sample_description_parts: list[str] = []

    def flush_sample() -> None:
        if not current_sample:
            return
        metadata = GeoSampleMetadata(
            geo_sample_accession=current_sample,
            geo_sample_title=current_sample_title,
        )
        for accession in current_sample_relations:
            sample_metadata_by_accession.setdefault(accession, metadata)

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("^SAMPLE"):
            flush_sample()
            current_sample = _split_soft_line(line.replace("^", "!", 1))[1]
            current_sample_title = ""
            current_sample_relations = []
            continue
        if not line.startswith("!"):
            continue
        key, value = _split_soft_line(line)
        if not key:
            continue

        if key == "Sample_title" and current_sample:
            current_sample_title = value
            if not first_sample_title:
                first_sample_title = value

        if key == "Sample_status" and current_sample and value and not first_sample_status:
            first_sample_status = value

        if key.startswith("Sample_organism_ch") and current_sample and value:
            _append_unique(dataset_organisms, value)

        if key == "Sample_description" and current_sample and value:
            first_sample_description_parts.append(value)

        if key == "Series_status" and value:
            dataset_status = value
        elif key == "Series_title" and value:
            dataset_title = value
        elif key == "Series_type" and value:
            _append_unique(dataset_experiment_types, value)
        elif key == "Series_summary" and value:
            dataset_summary_parts.append(value)
        elif key == "Series_overall_design" and value:
            dataset_design_parts.append(value)

        if key.endswith("_relation"):
            accessions = find_supported_accessions(value)
            for accession in accessions:
                if not accession.startswith(RELATED_PREFIXES):
                    continue
                if key.startswith("Series_") and accession not in seen_series_related:
                    series_related.append(accession)
                    seen_series_related.add(accession)
                elif key.startswith("Sample_") and accession not in seen_sample_related:
                    sample_related.append(accession)
                    seen_sample_related.add(accession)
            if key.startswith("Sample_") and current_sample:
                for accession in accessions:
                    if accession.startswith(RELATED_PREFIXES):
                        current_sample_relations.append(accession)

        if "_supplementary_file" in key and value and value.upper() != "NONE":
            normalized_url = value.strip()
            if normalized_url not in seen_supplementary:
                name = _name_from_url(normalized_url)
                origin_level = _origin_level_from_soft_key(key)
                supplementary.append(
                    SupplementaryFile(
                        source_accession=source_accession,
                        scope=_scope_from_soft_key(key),
                        name=name,
                        url=normalized_url,
                        origin_level=origin_level,
                        origin_accession=_origin_accession(origin_level, source_accession, current_sample),
                        extension=_extension_from_name(name),
                        estimated_type=_estimated_supplementary_type(name),
                    )
                )
                seen_supplementary.add(normalized_url)

    flush_sample()

    return GeoSoftParseResult(
        related_accessions=series_related or sample_related,
        supplementary_files=supplementary,
        sample_metadata_by_accession=sample_metadata_by_accession,
        dataset_metadata=DatasetMetadata(
            accession=source_accession,
            status=dataset_status or first_sample_status,
            title=dataset_title or first_sample_title,
            organism=_join_unique_values(dataset_organisms),
            experiment_type=_join_unique_values(dataset_experiment_types),
            summary=_join_soft_text(dataset_summary_parts or first_sample_description_parts),
            overall_design=_join_soft_text(dataset_design_parts),
        ),
    )


def _merge_parse_results(primary: GeoSoftParseResult, samples: GeoSoftParseResult) -> GeoSoftParseResult:
    return GeoSoftParseResult(
        related_accessions=primary.related_accessions or samples.related_accessions,
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


def _name_from_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    basename = posixpath.basename(parsed.path)
    return urllib.parse.unquote(basename or url)


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
