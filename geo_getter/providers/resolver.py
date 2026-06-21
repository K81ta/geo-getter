from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

from ..accession import extract_accession
from ..errors import GeoGetterError
from ..models import DatasetMetadata, FastqFile, ResolveResult, SupplementaryFile
from .ena import get_fastq_files
from .geo import GeoSampleMetadata, GeoSoftParseResult, get_related


MAX_ENA_FILE_REPORT_WORKERS = 4
_PRIMARY_ENA_QUERY_PREFIXES = ("SRP", "ERP", "DRP", "PRJNA", "PRJEB", "PRJDB")
GeoRelatedLookup = Callable[[str], GeoSoftParseResult]
EnaFastqLookup = Callable[[str, str], list[FastqFile]]


def resolve_metadata(
    input_text: str,
    *,
    geo_related_lookup: GeoRelatedLookup = get_related,
    ena_fastq_lookup: EnaFastqLookup = get_fastq_files,
) -> ResolveResult:
    parsed = extract_accession(input_text)
    query_accessions: list[str] = []
    supplementary_files: list[SupplementaryFile] = []
    dataset_metadata = DatasetMetadata(accession=parsed.accession)
    sample_metadata_by_accession = {}
    warnings: list[str] = []

    if parsed.is_geo:
        geo_result = geo_related_lookup(parsed.accession)
        query_accessions = _deduplicate_values(geo_result.related_accessions)
        supplementary_files = geo_result.supplementary_files
        dataset_metadata = geo_result.dataset_metadata
        sample_metadata_by_accession = geo_result.sample_metadata_by_accession
        warnings.extend(geo_result.warnings)
        if not query_accessions:
            warnings.append("No SRA, BioProject, or BioSample accessions were found in the GEO record.")
    elif parsed.is_ena_query:
        query_accessions = [parsed.accession]
    else:
        raise GeoGetterError("unsupported_accession", f"Unsupported accession: {parsed.accession}")

    fastq_files: list[FastqFile] = []
    accession_file_groups, ena_warnings = _get_fastq_files_for_accessions(
        ena_fastq_lookup, query_accessions, parsed.accession
    )
    warnings.extend(ena_warnings)
    for accession_files in accession_file_groups:
        for item in accession_files:
            fastq_files.append(_with_geo_sample_metadata(item, sample_metadata_by_accession))

    fastq_files = _deduplicate_fastq(fastq_files)
    if query_accessions and not fastq_files:
        warnings.append("No ENA direct FASTQ files were found.")
    if any(item.size_bytes <= 0 for item in fastq_files):
        warnings.append("One or more ENA FASTQ file sizes were unavailable or invalid; capacity checks may be incomplete.")

    return ResolveResult(
        input_text=input_text.strip(),
        primary_accession=parsed.accession,
        query_accessions=query_accessions,
        fastq_files=fastq_files,
        supplementary_files=supplementary_files,
        dataset_metadata=dataset_metadata,
        warnings=warnings,
    )


def _get_fastq_files_for_accessions(
    ena_fastq_lookup: EnaFastqLookup,
    query_accessions: list[str],
    source_accession: str,
) -> tuple[list[list[FastqFile]], list[str]]:
    if not query_accessions:
        return [], []
    if len(query_accessions) == 1:
        accession = query_accessions[0]
        return [ena_fastq_lookup(accession, source_accession)], []

    results: list[list[FastqFile]] = []
    warnings: list[str] = []
    first_error: GeoGetterError | None = None
    covered_accessions: set[str] = set()
    for batch in _prioritized_query_batches(query_accessions):
        pending = [accession for accession in batch if accession not in covered_accessions]
        if not pending:
            continue
        batch_results, batch_warnings, batch_error = _lookup_fastq_file_groups(
            ena_fastq_lookup,
            pending,
            source_accession,
            allow_partial=True,
        )
        results.extend(batch_results)
        warnings.extend(batch_warnings)
        if first_error is None and batch_error is not None:
            first_error = batch_error
        for files in batch_results:
            covered_accessions.update(_covered_accessions(files))
    if not results and first_error is not None:
        raise first_error
    return results, warnings


def _prioritized_query_batches(query_accessions: list[str]) -> list[list[str]]:
    primary = [accession for accession in query_accessions if accession.startswith(_PRIMARY_ENA_QUERY_PREFIXES)]
    secondary = [accession for accession in query_accessions if not accession.startswith(_PRIMARY_ENA_QUERY_PREFIXES)]
    return [batch for batch in (primary, secondary) if batch]


def _lookup_fastq_file_groups(
    ena_fastq_lookup: EnaFastqLookup,
    query_accessions: list[str],
    source_accession: str,
    *,
    allow_partial: bool,
) -> tuple[list[list[FastqFile]], list[str], GeoGetterError | None]:
    if len(query_accessions) == 1:
        accession = query_accessions[0]
        try:
            return [ena_fastq_lookup(accession, source_accession)], [], None
        except GeoGetterError as exc:
            if not allow_partial:
                raise
            return [], [_ena_lookup_warning(accession, exc)], exc

    worker_count = min(MAX_ENA_FILE_REPORT_WORKERS, len(query_accessions))
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = [
            executor.submit(ena_fastq_lookup, accession, source_accession)
            for accession in query_accessions
        ]
        results: list[list[FastqFile]] = []
        warnings: list[str] = []
        first_error: GeoGetterError | None = None
        for accession, future in zip(query_accessions, futures):
            try:
                results.append(future.result())
            except GeoGetterError as exc:
                if first_error is None:
                    first_error = exc
                warnings.append(_ena_lookup_warning(accession, exc))
        return results, warnings, first_error


def _ena_lookup_warning(accession: str, exc: GeoGetterError) -> str:
    return (
        f"ENA FASTQ lookup failed for {accession}; "
        f"continuing with other accessions. Detail: {exc.code}"
    )


def _covered_accessions(files: list[FastqFile]) -> set[str]:
    covered: set[str] = set()
    for item in files:
        for accession in (
            item.query_accession,
            item.run_accession,
            item.experiment_accession,
            item.sample_accession,
            item.secondary_sample_accession,
            item.study_accession,
            item.secondary_study_accession,
        ):
            if accession:
                covered.add(accession)
    return covered


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
    return list(dict.fromkeys(values))


def _with_geo_sample_metadata(
    item: FastqFile,
    sample_metadata_by_accession: dict[str, GeoSampleMetadata | None],
) -> FastqFile:
    metadata = _geo_sample_metadata_for_fastq(item, sample_metadata_by_accession)
    if not metadata:
        return item
    return replace(
        item,
        geo_sample_accession=metadata.geo_sample_accession,
        geo_sample_title=metadata.geo_sample_title,
    )


def _geo_sample_metadata_for_fastq(
    item: FastqFile,
    sample_metadata_by_accession: dict[str, GeoSampleMetadata | None],
) -> GeoSampleMetadata | None:
    for accession in (
        item.run_accession,
        item.experiment_accession,
        item.sample_accession,
        item.secondary_sample_accession,
        item.study_accession,
        item.secondary_study_accession,
        item.query_accession,
    ):
        if not accession:
            continue
        metadata = sample_metadata_by_accession.get(accession)
        if metadata:
            return metadata
    return None
