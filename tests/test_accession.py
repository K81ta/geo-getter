import unittest

from geo_getter.accession import (
    SUPPORTED_ACCESSION_PREFIXES,
    extract_accession,
    find_supported_accessions,
)


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

    def test_long_prefixes_are_captured_directly(self):
        found = find_supported_accessions("PRJNA30709 SAMEA104726647 SAMN01103824 SAMD000001")

        self.assertEqual(found, ["PRJNA30709", "SAMEA104726647", "SAMN01103824", "SAMD000001"])
        self.assertEqual(extract_accession("prefix PRJEB123456").prefix, "PRJEB")

    def test_extract_supported_prefixes_case_insensitively(self):
        for prefix in SUPPORTED_ACCESSION_PREFIXES:
            with self.subTest(prefix=prefix):
                parsed = extract_accession(f"{prefix.lower()}123456")

                self.assertEqual(parsed.accession, f"{prefix}123456")
                self.assertEqual(parsed.prefix, prefix)


if __name__ == "__main__":
    unittest.main()
