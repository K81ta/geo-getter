import tempfile
import unittest
import hashlib
from pathlib import Path

from geo_getter.downloader import download_plan, verify_md5
from geo_getter.errors import GeoGetterError
from geo_getter.models import DownloadPlan, FastqFile
from geo_getter.planner import build_download_plan, download_log_path, ensure_capacity, fastq_manifest_path, write_fastq_outputs


class PlannerDownloaderTest(unittest.TestCase):
    def test_plan_and_manifest_are_written(self):
        with tempfile.TemporaryDirectory() as temp:
            fastq = FastqFile(
                source_accession="GSE000001",
                query_accession="SRP000001",
                run_accession="SRR000001",
                file_index=1,
                file_name="SRR000001.fastq.gz",
                url="https://example.invalid/SRR000001.fastq.gz",
                expected_md5="5d41402abc4b2a76b9719d911017c592",
                size_bytes=5,
            )
            plan = build_download_plan("GSE000001", "GSE000001", [fastq], temp)
            write_fastq_outputs(plan)
            self.assertFalse((Path(temp) / "download_plan.json").exists())
            self.assertFalse((Path(temp) / "manifest.tsv").exists())
            self.assertFalse((Path(temp) / "download_log.tsv").exists())
            manifest_path = fastq_manifest_path(temp)
            self.assertTrue(manifest_path.read_bytes().startswith(b"\xef\xbb\xbf"))
            manifest = manifest_path.read_text(encoding="utf-8-sig")
            self.assertIn("SRR000001.fastq.gz", manifest)
            log_path = download_log_path(temp)
            self.assertTrue(log_path.exists())
            self.assertTrue(log_path.read_bytes().startswith(b"\xef\xbb\xbf"))

    def test_md5_fixture_success(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "hello.txt"
            path.write_text("hello", encoding="utf-8")
            ok, actual = verify_md5(path, "5d41402abc4b2a76b9719d911017c592")
            self.assertTrue(ok)
            self.assertEqual(actual, "5d41402abc4b2a76b9719d911017c592")

    def test_download_fixture_and_md5_success(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "source.fastq.gz"
            data = b"@r1\nACGT\n+\n!!!!\n"
            source.write_bytes(data)
            output_dir = Path(temp) / "out"
            fastq = FastqFile(
                source_accession="FIXTURE",
                query_accession="FIXTURE",
                run_accession="FIXTURE_RUN",
                file_index=1,
                file_name="fixture.fastq.gz",
                url=source.as_uri(),
                expected_md5=hashlib.md5(data).hexdigest(),
                size_bytes=len(data),
            )
            plan = build_download_plan("FIXTURE", "FIXTURE", [fastq], output_dir)
            results = download_plan(plan)
            self.assertEqual(results[0][1], "md5_verified")
            self.assertTrue((output_dir / "fixture.fastq.gz").exists())
            log_text = download_log_path(output_dir).read_text(encoding="utf-8-sig")
            self.assertIn("md5_verified", log_text)
            self.assertFalse((output_dir / "fixture.fastq.gz.part").exists())

    def test_download_without_md5_is_logged_as_unverified(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "source.fastq.gz"
            data = b"@r1\nACGT\n+\n!!!!\n"
            source.write_bytes(data)
            output_dir = Path(temp) / "out"
            fastq = FastqFile(
                source_accession="FIXTURE",
                query_accession="FIXTURE",
                run_accession="FIXTURE_RUN",
                file_index=1,
                file_name="fixture.fastq.gz",
                url=source.as_uri(),
                expected_md5="",
                size_bytes=len(data),
            )
            plan = build_download_plan("FIXTURE", "FIXTURE", [fastq], output_dir)
            results = download_plan(plan)
            self.assertEqual(results[0][1], "md5_unavailable")
            self.assertTrue((output_dir / "fixture.fastq.gz").exists())
            log_text = download_log_path(output_dir).read_text(encoding="utf-8-sig")
            self.assertIn("md5_unavailable", log_text)
            self.assertIn("could not be verified", log_text)

    def test_download_fixture_and_md5_mismatch_is_logged(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "source.fastq.gz"
            data = b"@r1\nACGT\n+\n!!!!\n"
            source.write_bytes(data)
            output_dir = Path(temp) / "out"
            fastq = FastqFile(
                source_accession="FIXTURE",
                query_accession="FIXTURE",
                run_accession="FIXTURE_RUN",
                file_index=1,
                file_name="fixture.fastq.gz",
                url=source.as_uri(),
                expected_md5="0" * 32,
                size_bytes=len(data),
            )
            plan = build_download_plan("FIXTURE", "FIXTURE", [fastq], output_dir)
            results = download_plan(plan)
            self.assertEqual(results[0][1], "md5_mismatch")
            self.assertFalse((output_dir / "fixture.fastq.gz").exists())
            self.assertTrue(list(output_dir.glob("fixture.fastq.gz.part.bad-md5-*")))
            log_text = download_log_path(output_dir).read_text(encoding="utf-8-sig")
            self.assertIn("md5_mismatch", log_text)

    def test_oversized_download_is_quarantined(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "source.fastq.gz"
            data = b"@r1\nACGT\n+\n!!!!\n"
            source.write_bytes(data)
            output_dir = Path(temp) / "out"
            fastq = FastqFile(
                source_accession="FIXTURE",
                query_accession="FIXTURE",
                run_accession="FIXTURE_RUN",
                file_index=1,
                file_name="fixture.fastq.gz",
                url=source.as_uri(),
                expected_md5=hashlib.md5(data).hexdigest(),
                size_bytes=len(data) - 1,
            )
            plan = build_download_plan("FIXTURE", "FIXTURE", [fastq], output_dir)
            results = download_plan(plan)
            self.assertEqual(results[0][1], "size_mismatch")
            self.assertFalse((output_dir / "fixture.fastq.gz").exists())
            self.assertTrue(list(output_dir.glob("fixture.fastq.gz.part.size-mismatch-*")))
            log_text = download_log_path(output_dir).read_text(encoding="utf-8-sig")
            self.assertIn("size_mismatch", log_text)

    def test_existing_matching_file_is_reused(self):
        with tempfile.TemporaryDirectory() as temp:
            output_dir = Path(temp) / "out"
            output_dir.mkdir()
            data = b"already downloaded\n"
            existing = output_dir / "fixture.fastq.gz"
            existing.write_bytes(data)
            fastq = FastqFile(
                source_accession="FIXTURE",
                query_accession="FIXTURE",
                run_accession="FIXTURE_RUN",
                file_index=1,
                file_name="fixture.fastq.gz",
                url="file:///definitely/not/used.fastq.gz",
                expected_md5=hashlib.md5(data).hexdigest(),
                size_bytes=len(data),
            )
            plan = build_download_plan("FIXTURE", "FIXTURE", [fastq], output_dir)
            results = download_plan(plan)
            self.assertEqual(results[0][1], "md5_verified")
            log_text = download_log_path(output_dir).read_text(encoding="utf-8-sig")
            self.assertIn("reused without downloading again", log_text)
            self.assertEqual(existing.read_bytes(), data)

    def test_existing_mismatched_file_is_quarantined_before_redownload(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source.fastq.gz"
            data = b"correct data\n"
            source.write_bytes(data)
            output_dir = root / "out"
            output_dir.mkdir()
            existing = output_dir / "fixture.fastq.gz"
            existing.write_bytes(b"wrong data\n")
            fastq = FastqFile(
                source_accession="FIXTURE",
                query_accession="FIXTURE",
                run_accession="FIXTURE_RUN",
                file_index=1,
                file_name="fixture.fastq.gz",
                url=source.as_uri(),
                expected_md5=hashlib.md5(data).hexdigest(),
                size_bytes=len(data),
            )
            plan = build_download_plan("FIXTURE", "FIXTURE", [fastq], output_dir)
            results = download_plan(plan)
            self.assertEqual(results[0][1], "md5_verified")
            self.assertEqual(existing.read_bytes(), data)
            self.assertTrue(list(output_dir.glob("fixture.fastq.gz.bad-md5-existing-*")))

    def test_complete_part_file_is_verified_and_finalized(self):
        with tempfile.TemporaryDirectory() as temp:
            output_dir = Path(temp) / "out"
            output_dir.mkdir()
            data = b"complete part\n"
            part = output_dir / "fixture.fastq.gz.part"
            part.write_bytes(data)
            fastq = FastqFile(
                source_accession="FIXTURE",
                query_accession="FIXTURE",
                run_accession="FIXTURE_RUN",
                file_index=1,
                file_name="fixture.fastq.gz",
                url="file:///definitely/not/used.fastq.gz",
                expected_md5=hashlib.md5(data).hexdigest(),
                size_bytes=len(data),
            )
            plan = build_download_plan("FIXTURE", "FIXTURE", [fastq], output_dir)
            results = download_plan(plan)
            self.assertEqual(results[0][1], "md5_verified")
            self.assertFalse(part.exists())
            self.assertEqual((output_dir / "fixture.fastq.gz").read_bytes(), data)

    def test_oversized_part_file_is_quarantined(self):
        with tempfile.TemporaryDirectory() as temp:
            output_dir = Path(temp) / "out"
            output_dir.mkdir()
            data = b"oversized part\n"
            part = output_dir / "fixture.fastq.gz.part"
            part.write_bytes(data)
            fastq = FastqFile(
                source_accession="FIXTURE",
                query_accession="FIXTURE",
                run_accession="FIXTURE_RUN",
                file_index=1,
                file_name="fixture.fastq.gz",
                url="file:///definitely/not/used.fastq.gz",
                expected_md5=hashlib.md5(data).hexdigest(),
                size_bytes=len(data) - 1,
            )
            plan = build_download_plan("FIXTURE", "FIXTURE", [fastq], output_dir)
            results = download_plan(plan)
            self.assertEqual(results[0][1], "size_mismatch")
            self.assertFalse(part.exists())
            self.assertFalse((output_dir / "fixture.fastq.gz").exists())
            self.assertTrue(list(output_dir.glob("fixture.fastq.gz.part.size-mismatch-*")))

    def test_capacity_shortage_raises_english_error(self):
        plan = DownloadPlan(
            app_version="test",
            created_at="2026-01-01T00:00:00+00:00",
            input_text="FIXTURE",
            primary_accession="FIXTURE",
            output_dir=Path("."),
            total_bytes=10,
            available_bytes=9,
            files=[],
        )
        with self.assertRaises(GeoGetterError) as context:
            ensure_capacity(plan)
        self.assertEqual(context.exception.code, "insufficient_space")

    def test_duplicate_output_names_are_disambiguated(self):
        with tempfile.TemporaryDirectory() as temp:
            fastq1 = FastqFile(
                source_accession="GSE",
                query_accession="SRP",
                run_accession="SRR1",
                file_index=1,
                file_name="same.fastq.gz",
                url="https://example.invalid/a.fastq.gz",
                expected_md5="1" * 32,
                size_bytes=1,
            )
            fastq2 = FastqFile(
                source_accession="GSE",
                query_accession="SRP",
                run_accession="SRR2",
                file_index=1,
                file_name="same.fastq.gz",
                url="https://example.invalid/b.fastq.gz",
                expected_md5="2" * 32,
                size_bytes=1,
            )
            plan = build_download_plan("GSE", "GSE", [fastq1, fastq2], temp)
            self.assertEqual(plan.files[0].local_path.name, "same.fastq.gz")
            self.assertEqual(plan.files[1].local_path.name, "same.2.fastq.gz")


if __name__ == "__main__":
    unittest.main()
