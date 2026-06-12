import threading
import time
import unittest

from geo_getter.accession import ENA_QUERY_PREFIXES
from geo_getter.errors import GeoGetterError, URL_UNAVAILABLE
from geo_getter.models import DatasetMetadata, FastqFile, SupplementaryFile
from geo_getter.providers.geo import GeoSampleMetadata, GeoSoftParseResult
from geo_getter.providers.resolver import MetadataResolver


class FakeGeoProvider:
    def get_related(self, accession):
        return GeoSoftParseResult(
            related_accessions=["SRP000001", "SRP000001"],
            supplementary_files=[
                SupplementaryFile(
                    source_accession=accession,
                    scope="GEO Series supplementary/processed",
                    name="processed.txt",
                    url="https://example.invalid/processed.txt",
                )
            ],
            sample_metadata_by_accession={
                "SRP000001": GeoSampleMetadata(
                    geo_sample_accession="GSM000001",
                    geo_sample_title="sample title",
                )
            },
            dataset_metadata=DatasetMetadata(
                accession=accession,
                title="dataset title",
                summary="dataset summary",
            ),
        )


class FakeEnaProvider:
    def get_fastq_files(self, accession, source_accession):
        return [
            FastqFile(
                source_accession=source_accession,
                query_accession=accession,
                run_accession="SRR000001",
                file_index=1,
                file_name="a.fastq.gz",
                url="https://example.invalid/a.fastq.gz",
                expected_md5="1" * 32,
                size_bytes=10,
            )
        ]


class EmptyGeoProvider:
    def get_related(self, accession):
        return GeoSoftParseResult(
            related_accessions=[],
            supplementary_files=[],
            sample_metadata_by_accession={},
            dataset_metadata=DatasetMetadata(accession=accession),
        )


class MultiGeoProvider:
    def __init__(self, related_accessions):
        self.related_accessions = related_accessions

    def get_related(self, accession):
        return GeoSoftParseResult(
            related_accessions=self.related_accessions,
            supplementary_files=[],
            sample_metadata_by_accession={
                related_accession: GeoSampleMetadata(
                    geo_sample_accession=f"GSM{related_accession.removeprefix('SRP')}",
                    geo_sample_title=f"sample {related_accession}",
                )
                for related_accession in self.related_accessions
            },
            dataset_metadata=DatasetMetadata(accession=accession),
        )


class FailingGeoProvider:
    def get_related(self, accession):
        raise AssertionError(f"GEO provider should not be called for direct ENA input: {accession}")


class RecordingEnaProvider:
    def __init__(self):
        self.calls = []

    def get_fastq_files(self, accession, source_accession):
        self.calls.append((accession, source_accession))
        return [
            FastqFile(
                source_accession=source_accession,
                query_accession=accession,
                run_accession="SRR000001",
                file_index=1,
                file_name="a.fastq.gz",
                url="https://example.invalid/a.fastq.gz",
                expected_md5="1" * 32,
                size_bytes=10,
            )
        ]


class ParallelRecordingEnaProvider:
    def __init__(self):
        self.active = 0
        self.max_active = 0
        self.lock = threading.Lock()

    def get_fastq_files(self, accession, source_accession):
        with self.lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            if accession.endswith("000001"):
                time.sleep(0.05)
            else:
                time.sleep(0.01)
            return [_fastq(accession, source_accession)]
        finally:
            with self.lock:
                self.active -= 1


class PartiallyFailingEnaProvider:
    def get_fastq_files(self, accession, source_accession):
        if accession == "SRP000002":
            raise GeoGetterError(URL_UNAVAILABLE, accession)
        return [_fastq(accession, source_accession)]


class DuplicateFastqEnaProvider:
    def get_fastq_files(self, accession, source_accession):
        return [
            FastqFile(
                source_accession=source_accession,
                query_accession=accession,
                run_accession="SRR000001",
                file_index=1,
                file_name="shared.fastq.gz",
                url="https://example.invalid/shared.fastq.gz",
                expected_md5="1" * 32,
                size_bytes=10,
            )
        ]


def _fastq(accession, source_accession):
    suffix = accession.removeprefix("SRP")
    return FastqFile(
        source_accession=source_accession,
        query_accession=accession,
        run_accession=f"SRR{suffix}",
        file_index=1,
        file_name=f"{accession}.fastq.gz",
        url=f"https://example.invalid/{accession}.fastq.gz",
        expected_md5="1" * 32,
        size_bytes=10,
    )


class ResolverTest(unittest.TestCase):
    def test_resolver_deduplicates_fastq_and_keeps_supplementary(self):
        result = MetadataResolver(FakeGeoProvider(), FakeEnaProvider()).resolve("GSE000001")
        self.assertEqual(result.primary_accession, "GSE000001")
        self.assertEqual(result.query_accessions, ["SRP000001"])
        self.assertEqual(len(result.fastq_files), 1)
        self.assertEqual(len(result.supplementary_files), 1)
        self.assertEqual(result.fastq_files[0].geo_sample_accession, "GSM000001")
        self.assertEqual(result.fastq_files[0].geo_sample_title, "sample title")
        self.assertEqual(result.dataset_metadata.title, "dataset title")

    def test_geo_without_related_accession_warns_fastq_absent(self):
        result = MetadataResolver(EmptyGeoProvider(), FakeEnaProvider()).resolve("GSE000002")
        self.assertEqual(result.fastq_files, [])
        self.assertIn("No SRA", result.warnings[0])

    def test_size_unknown_fastq_adds_warning(self):
        class SizeUnknownEnaProvider:
            def get_fastq_files(self, accession, source_accession):
                return [
                    FastqFile(
                        source_accession=source_accession,
                        query_accession=accession,
                        run_accession="SRR000001",
                        file_index=1,
                        file_name="a.fastq.gz",
                        url="https://example.invalid/a.fastq.gz",
                        expected_md5="",
                        size_bytes=0,
                    )
                ]

        result = MetadataResolver(FakeGeoProvider(), SizeUnknownEnaProvider()).resolve("GSE000003")
        self.assertTrue(any("file sizes were unavailable" in warning for warning in result.warnings))

    def test_direct_ena_accessions_skip_geo_and_query_ena_directly(self):
        for prefix in ENA_QUERY_PREFIXES:
            accession = f"{prefix}000001"
            with self.subTest(accession=accession):
                ena_provider = RecordingEnaProvider()
                result = MetadataResolver(FailingGeoProvider(), ena_provider).resolve(accession)

                self.assertEqual(result.primary_accession, accession)
                self.assertEqual(result.query_accessions, [accession])
                self.assertEqual(ena_provider.calls, [(accession, accession)])
                self.assertEqual(len(result.fastq_files), 1)
                self.assertEqual(result.fastq_files[0].source_accession, accession)
                self.assertEqual(result.fastq_files[0].query_accession, accession)
                self.assertEqual(result.supplementary_files, [])

    def test_geo_related_accessions_are_queried_in_small_parallel_batches(self):
        accessions = [f"SRP{index:06d}" for index in range(1, 13)]
        ena_provider = ParallelRecordingEnaProvider()

        result = MetadataResolver(MultiGeoProvider(accessions), ena_provider).resolve("GSE000004")

        self.assertGreater(ena_provider.max_active, 1)
        self.assertLessEqual(ena_provider.max_active, 4)
        self.assertEqual(result.query_accessions, accessions)
        self.assertEqual(
            [item.query_accession for item in result.fastq_files],
            accessions,
        )
        self.assertEqual(result.fastq_files[0].geo_sample_accession, "GSM000001")

    def test_geo_related_accession_failure_propagates_without_partial_result(self):
        with self.assertRaises(GeoGetterError) as raised:
            MetadataResolver(
                MultiGeoProvider(["SRP000001", "SRP000002", "SRP000003"]),
                PartiallyFailingEnaProvider(),
            ).resolve("GSE000005")

        self.assertEqual(raised.exception.code, URL_UNAVAILABLE)
        self.assertEqual(raised.exception.detail, "SRP000002")

    def test_parallel_fastq_dedup_keeps_first_query_order(self):
        result = MetadataResolver(
            MultiGeoProvider(["SRP000001", "SRP000002"]),
            DuplicateFastqEnaProvider(),
        ).resolve("GSE000006")

        self.assertEqual(len(result.fastq_files), 1)
        self.assertEqual(result.fastq_files[0].query_accession, "SRP000001")


if __name__ == "__main__":
    unittest.main()
