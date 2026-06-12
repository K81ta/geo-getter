import json
import unittest
from pathlib import Path

from geo_getter.providers.ena import parse_file_report


class EnaProviderTest(unittest.TestCase):
    def test_parse_semicolon_fastq_fields(self):
        rows = json.loads(Path("tests/fixtures/ena_report.json").read_text(encoding="utf-8"))
        files = parse_file_report(rows, source_accession="GSE000001", query_accession="SRP000001")
        self.assertEqual(len(files), 2)
        self.assertEqual(files[0].run_accession, "SRR000001")
        self.assertEqual(files[0].file_name, "SRR000001_1.fastq.gz")
        self.assertEqual(files[0].url, "https://ftp.sra.ebi.ac.uk/vol1/fastq/SRR000/SRR000001/SRR000001_1.fastq.gz")
        self.assertEqual(files[1].expected_md5, "22222222222222222222222222222222")
        self.assertEqual(files[1].size_bytes, 34)

    def test_parse_missing_md5_and_size_keeps_url(self):
        rows = [
            {
                "run_accession": "SRR000002",
                "fastq_ftp": "ftp.sra.ebi.ac.uk/vol1/fastq/SRR000/SRR000002/SRR000002.fastq.gz",
                "fastq_md5": "",
                "fastq_bytes": "",
            }
        ]
        files = parse_file_report(rows, source_accession="GSE000002", query_accession="SRP000002")
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].expected_md5, "")
        self.assertEqual(files[0].size_bytes, 0)
        self.assertTrue(files[0].url.startswith("https://ftp.sra.ebi.ac.uk/"))

    def test_parse_preserves_md5_and_size_positions_when_first_value_is_empty(self):
        rows = [
            {
                "run_accession": "SRR000003",
                "fastq_ftp": (
                    "ftp.sra.ebi.ac.uk/vol1/fastq/SRR000/SRR000003/SRR000003_1.fastq.gz;"
                    "ftp.sra.ebi.ac.uk/vol1/fastq/SRR000/SRR000003/SRR000003_2.fastq.gz"
                ),
                "fastq_md5": ";22222222222222222222222222222222",
                "fastq_bytes": ";34",
            }
        ]
        files = parse_file_report(rows, source_accession="GSE000003", query_accession="SRP000003")
        self.assertEqual(files[0].expected_md5, "")
        self.assertEqual(files[0].size_bytes, 0)
        self.assertEqual(files[1].expected_md5, "22222222222222222222222222222222")
        self.assertEqual(files[1].size_bytes, 34)

    def test_parse_sanitizes_decoded_fastq_file_name(self):
        rows = [
            {
                "run_accession": "SRR000004",
                "fastq_ftp": "ftp.sra.ebi.ac.uk/vol1/%2e%2e%2fescape.fastq.gz",
                "fastq_md5": "",
                "fastq_bytes": "",
            }
        ]
        files = parse_file_report(rows, source_accession="GSE000004", query_accession="SRP000004")
        self.assertEqual(files[0].file_name, "_escape.fastq.gz")

    def test_submitted_ftp_without_fastq_ftp_is_not_fastq_candidate(self):
        for submitted_ftp in (
            "ftp.sra.ebi.ac.uk/vol1/run/SRR000/SRR000005/submitted.bam",
            "ftp.sra.ebi.ac.uk/vol1/run/SRR000/SRR000005/submitted.fastq.gz",
        ):
            with self.subTest(submitted_ftp=submitted_ftp):
                rows = [
                    {
                        "run_accession": "SRR000005",
                        "fastq_ftp": "",
                        "fastq_md5": "",
                        "fastq_bytes": "",
                        "submitted_ftp": submitted_ftp,
                        "submitted_md5": "1" * 32,
                        "submitted_bytes": "123",
                    }
                ]
                files = parse_file_report(rows, source_accession="GSE000005", query_accession="SRP000005")
                self.assertEqual(files, [])

    def test_none_metadata_values_are_normalized_to_empty_strings(self):
        rows = [
            {
                "run_accession": "SRR000006",
                "experiment_accession": None,
                "sample_accession": None,
                "secondary_sample_accession": None,
                "study_accession": None,
                "secondary_study_accession": None,
                "scientific_name": None,
                "instrument_platform": None,
                "library_layout": None,
                "library_strategy": None,
                "fastq_ftp": "ftp.sra.ebi.ac.uk/vol1/fastq/SRR000/SRR000006/SRR000006.fastq.gz",
                "fastq_md5": None,
                "fastq_bytes": None,
            }
        ]

        files = parse_file_report(rows, source_accession="GSE000006", query_accession="SRP000006")

        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].experiment_accession, "")
        self.assertEqual(files[0].sample_accession, "")
        self.assertEqual(files[0].secondary_sample_accession, "")
        self.assertEqual(files[0].study_accession, "")
        self.assertEqual(files[0].secondary_study_accession, "")
        self.assertEqual(files[0].scientific_name, "")
        self.assertEqual(files[0].instrument_platform, "")
        self.assertEqual(files[0].library_layout, "")
        self.assertEqual(files[0].library_strategy, "")
        self.assertEqual(files[0].expected_md5, "")
        self.assertEqual(files[0].size_bytes, 0)

    def test_fasp_fastq_urls_are_not_download_candidates(self):
        rows = [
            {
                "run_accession": "SRR000007",
                "fastq_ftp": "fasp.sra.ebi.ac.uk/vol1/fastq/SRR000/SRR000007/SRR000007.fastq.gz",
                "fastq_md5": "1" * 32,
                "fastq_bytes": "123",
            }
        ]

        files = parse_file_report(rows, source_accession="GSE000007", query_accession="SRP000007")

        self.assertEqual(files, [])


if __name__ == "__main__":
    unittest.main()
