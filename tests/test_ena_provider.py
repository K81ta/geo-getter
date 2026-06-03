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


if __name__ == "__main__":
    unittest.main()
