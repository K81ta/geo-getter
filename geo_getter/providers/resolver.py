from __future__ import annotations

from dataclasses import replace

from ..accession import extract_accession
from ..errors import GeoGetterError
from ..models import DatasetMetadata, FastqFile, ResolveResult, SupplementaryFile
from .ena import EnaProvider
from .geo import GeoProvider


class MetadataResolver:
    def __init__(self, geo_provider: GeoProvider | None = None, ena_provider: EnaProvider | None = None):
        self.geo_provider = geo_provider or GeoProvider()
        self.ena_provider = ena_provider or EnaProvider()

    def resolve(self, input_text: str) -> ResolveResult:
        parsed = extract_accession(input_text)
        query_accessions: list[str] = []
        supplementary_files: list[SupplementaryFile] = []
        dataset_metadata = DatasetMetadata(accession=parsed.accession)
        sample_metadata_by_accession = {}
        warnings: list[str] = []

        if parsed.is_geo:
            geo_result = self.geo_provider.get_related(parsed.accession)
            query_accessions = _deduplicate_values(geo_result.related_accessions)
            supplementary_files = geo_result.supplementary_files
            dataset_metadata = geo_result.dataset_metadata
            sample_metadata_by_accession = geo_result.sample_metadata_by_accession
            if not query_accessions:
                warnings.append("No SRA, BioProject, or BioSample accessions were found in the GEO record.")
        elif parsed.is_ena_query:
            query_accessions = [parsed.accession]
        else:
            raise GeoGetterError("unsupported_accession", f"Unsupported accession: {parsed.accession}")

        fastq_files: list[FastqFile] = []
        for accession in query_accessions:
            for item in self.ena_provider.get_fastq_files(accession, parsed.accession):
                fastq_files.append(_with_geo_sample_metadata(item, sample_metadata_by_accession))

        fastq_files = _deduplicate_fastq(fastq_files)
        if query_accessions and not fastq_files:
            warnings.append("No ENA direct FASTQ files were found.")

        return ResolveResult(
            input_text=input_text.strip(),
            primary_accession=parsed.accession,
            query_accessions=query_accessions,
            fastq_files=fastq_files,
            supplementary_files=supplementary_files,
            dataset_metadata=dataset_metadata,
            warnings=warnings,
        )


def _deduplicate_fastq(files: list[FastqFile]) -> list[FastqFile]:
    seen: set[tuple[str, str]] = set()
    unique: list[FastqFile] = []
    for item in files:
        key = (item.run_accession, item.url)
        if key in seen:
            continue
        unique.append(item)
        seen.add(key)
    return unique


def _deduplicate_values(values: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        if value in seen:
            continue
        unique.append(value)
        seen.add(value)
    return unique


def _with_geo_sample_metadata(item: FastqFile, sample_metadata_by_accession: dict) -> FastqFile:
    metadata = (
        sample_metadata_by_accession.get(item.query_accession)
        or sample_metadata_by_accession.get(item.experiment_accession)
        or sample_metadata_by_accession.get(item.run_accession)
        or sample_metadata_by_accession.get(item.sample_accession)
        or sample_metadata_by_accession.get(item.secondary_sample_accession)
    )
    if not metadata:
        return item
    return replace(
        item,
        geo_sample_accession=metadata.geo_sample_accession,
        geo_sample_title=metadata.geo_sample_title,
    )
