import unittest

from geo_getter.providers.download_urls import filename_from_url, normalize_download_url


class ProviderDownloadUrlsTest(unittest.TestCase):
    def test_normalize_download_url_keeps_http_urls(self):
        self.assertEqual(
            normalize_download_url("https://example.invalid/file.fastq.gz"),
            "https://example.invalid/file.fastq.gz",
        )

    def test_normalize_download_url_converts_ena_ftp_urls_to_https(self):
        self.assertEqual(
            normalize_download_url("ftp://ftp.sra.ebi.ac.uk/vol1/fastq/SRR000/SRR000001.fastq.gz"),
            "https://ftp.sra.ebi.ac.uk/vol1/fastq/SRR000/SRR000001.fastq.gz",
        )
        self.assertEqual(
            normalize_download_url("ftp.sra.ebi.ac.uk/vol1/fastq/SRR000/SRR000001.fastq.gz"),
            "https://ftp.sra.ebi.ac.uk/vol1/fastq/SRR000/SRR000001.fastq.gz",
        )

    def test_normalize_download_url_skips_fasp_urls(self):
        self.assertEqual(
            normalize_download_url("fasp.sra.ebi.ac.uk/vol1/fastq/SRR000/SRR000001.fastq.gz"),
            "",
        )

    def test_filename_from_url_decodes_basename(self):
        self.assertEqual(
            filename_from_url(
                "ftp://example.invalid/GSE000001_count%20matrix.tsv.gz?download=1",
                default="fallback.txt",
            ),
            "GSE000001_count matrix.tsv.gz",
        )

    def test_filename_from_url_uses_fallback_when_path_has_no_basename(self):
        self.assertEqual(
            filename_from_url("ftp://example.invalid/", default="fallback.txt"),
            "fallback.txt",
        )

    def test_filename_from_url_can_sanitize_decoded_name(self):
        self.assertEqual(
            filename_from_url(
                "https://ftp.sra.ebi.ac.uk/vol1/%2e%2e%2fescape.fastq.gz",
                default="download.fastq.gz",
                sanitize=True,
            ),
            "_escape.fastq.gz",
        )


if __name__ == "__main__":
    unittest.main()
