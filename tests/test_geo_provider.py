import unittest

from geo_getter.errors import GeoGetterError
from geo_getter.providers.geo import GeoProvider, _merge_parse_results, parse_soft


SOFT = """
^SERIES = GSE30567
!Series_status = Public on Jan 01 2026
!Series_title = Test series title
!Series_type = Expression profiling by high throughput sequencing
!Series_type = Expression profiling by high throughput sequencing
!Series_summary = first summary line
!Series_summary = second summary line
!Series_overall_design = design line
!Series_supplementary_file = ftp://ftp.ncbi.nlm.nih.gov/geo/series/GSE30nnn/GSE30567/suppl/GSE30567_RAW.tar
!Series_relation = SRA: https://www.ncbi.nlm.nih.gov/sra?term=SRP007461
^SAMPLE = GSM758559
!Sample_status = Public on Jan 02 2026
!Sample_title = adipose sample 1
!Sample_organism_ch1 = Homo sapiens
!Sample_organism_ch2 = Mus musculus
!Sample_relation = SRA: https://www.ncbi.nlm.nih.gov/sra?term=SRX082565
!Sample_relation = BioSample: SAMN01103824
!Sample_supplementary_file_1 = ftp://ftp.ncbi.nlm.nih.gov/geo/samples/GSM758nnn/GSM758559/suppl/example.bigWig
^SAMPLE = GSM758560
!Sample_title = adipose sample 2
!Sample_organism_ch1 = Homo sapiens
"""


class GeoProviderTest(unittest.TestCase):
    def test_parse_related_and_supplementary(self):
        parsed = parse_soft(SOFT, "GSE30567")
        self.assertEqual(parsed.related_accessions, ["SRP007461"])
        self.assertEqual(len(parsed.supplementary_files), 2)
        self.assertEqual(parsed.supplementary_files[0].name, "GSE30567_RAW.tar")
        self.assertEqual(parsed.supplementary_files[0].origin_level, "series")
        self.assertEqual(parsed.supplementary_files[0].origin_accession, "GSE30567")
        self.assertEqual(parsed.supplementary_files[0].extension, ".tar")
        self.assertEqual(parsed.supplementary_files[0].estimated_type, "geo_raw_archive")
        self.assertEqual(parsed.supplementary_files[0].size_status, "unknown")
        self.assertEqual(parsed.supplementary_files[0].verification_status, "not_applicable")
        self.assertIn("supplementary", parsed.supplementary_files[1].scope)
        self.assertEqual(parsed.supplementary_files[1].origin_level, "sample")
        self.assertEqual(parsed.supplementary_files[1].origin_accession, "GSM758559")
        self.assertEqual(parsed.supplementary_files[1].extension, ".bigwig")
        self.assertEqual(parsed.supplementary_files[1].estimated_type, "genome_track")
        metadata = parsed.sample_metadata_by_accession["SRX082565"]
        self.assertEqual(metadata.geo_sample_accession, "GSM758559")
        self.assertEqual(metadata.geo_sample_title, "adipose sample 1")
        self.assertEqual(parsed.sample_metadata_by_accession["SAMN01103824"], metadata)
        self.assertEqual(parsed.dataset_metadata.status, "Public on Jan 01 2026")
        self.assertEqual(parsed.dataset_metadata.title, "Test series title")
        self.assertEqual(parsed.dataset_metadata.organism, "Homo sapiens; Mus musculus")
        self.assertEqual(parsed.dataset_metadata.experiment_type, "Expression profiling by high throughput sequencing")
        self.assertEqual(parsed.dataset_metadata.summary, "first summary line second summary line")
        self.assertEqual(parsed.dataset_metadata.overall_design, "design line")

    def test_merge_uses_gsm_organism_for_gse_metadata(self):
        primary = parse_soft(
            """
^SERIES = GSE000001
!Series_status = Public on Jan 01 2026
!Series_title = Series title
!Series_type = Genome binding/occupancy profiling by high throughput sequencing
""",
            "GSE000001",
        )
        samples = parse_soft(
            """
^SAMPLE = GSM000001
!Sample_status = Public on Jan 02 2026
!Sample_title = sample title
!Sample_organism_ch1 = Bos taurus
!Sample_organism_ch2 = Bos taurus
""",
            "GSE000001",
        )
        merged = _merge_parse_results(primary, samples)
        self.assertEqual(merged.dataset_metadata.status, "Public on Jan 01 2026")
        self.assertEqual(merged.dataset_metadata.organism, "Bos taurus")
        self.assertEqual(
            merged.dataset_metadata.experiment_type,
            "Genome binding/occupancy profiling by high throughput sequencing",
        )

    def test_sample_status_is_fallback_when_series_status_is_absent(self):
        parsed = parse_soft(
            """
^SAMPLE = GSM000001
!Sample_status = Public on Jan 02 2026
!Sample_title = sample title
""",
            "GSM000001",
        )
        self.assertEqual(parsed.dataset_metadata.status, "Public on Jan 02 2026")

    def test_gse_sample_soft_failure_is_reported_as_warning(self):
        class FailingSampleGeoProvider(GeoProvider):
            def fetch_soft(self, accession):
                return """
^SERIES = GSE000001
!Series_relation = SRA: https://www.ncbi.nlm.nih.gov/sra?term=SRP000001
"""

            def fetch_gsm_soft(self, accession):
                raise GeoGetterError("network_failed", "fixture")

        parsed = FailingSampleGeoProvider().get_related("GSE000001")
        self.assertEqual(parsed.related_accessions, ["SRP000001"])
        self.assertIn("sample metadata retrieval failed", parsed.warnings[0])

    def test_supplementary_display_metadata_handles_common_names(self):
        parsed = parse_soft(
            """
^SERIES = GSE000001
!Series_supplementary_file = ftp://example.invalid/GSE000001_count%20matrix.tsv.gz?download=1
!Series_supplementary_file_1 = ftp://example.invalid/reads.fastq.gz
!Series_supplementary_file_2 = ftp://example.invalid/no_extension
""",
            "GSE000001",
        )

        self.assertEqual(parsed.supplementary_files[0].name, "GSE000001_count matrix.tsv.gz")
        self.assertEqual(parsed.supplementary_files[0].extension, ".tsv.gz")
        self.assertEqual(parsed.supplementary_files[0].estimated_type, "count_matrix")
        self.assertEqual(parsed.supplementary_files[1].extension, ".fastq.gz")
        self.assertEqual(parsed.supplementary_files[1].estimated_type, "fastq_like_supplementary")
        self.assertEqual(parsed.supplementary_files[2].extension, "")
        self.assertEqual(parsed.supplementary_files[2].estimated_type, "other")


if __name__ == "__main__":
    unittest.main()
