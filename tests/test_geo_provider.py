import unittest

from geo_getter.providers.geo import _merge_parse_results, parse_soft


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
        self.assertIn("supplementary", parsed.supplementary_files[1].scope)
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


if __name__ == "__main__":
    unittest.main()
