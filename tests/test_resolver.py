import unittest

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


if __name__ == "__main__":
    unittest.main()
