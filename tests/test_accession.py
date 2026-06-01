import unittest

from geo_getter.accession import extract_accession, find_supported_accessions


class AccessionTest(unittest.TestCase):
    def test_extract_gse(self):
        parsed = extract_accession("GSE123456")
        self.assertEqual(parsed.accession, "GSE123456")
        self.assertTrue(parsed.is_geo)

    def test_extract_gsm_from_url(self):
        parsed = extract_accession("https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSM758559")
        self.assertEqual(parsed.accession, "GSM758559")
        self.assertTrue(parsed.is_geo)

    def test_find_related_accessions(self):
        found = find_supported_accessions("SRA: https://www.ncbi.nlm.nih.gov/sra?term=SRX082565 PRJNA30709")
        self.assertEqual(found, ["SRX082565", "PRJNA30709"])


if __name__ == "__main__":
    unittest.main()
