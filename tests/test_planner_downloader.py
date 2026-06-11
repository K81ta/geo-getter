import csv
import http.client
import tempfile
import unittest
import hashlib
import urllib.error
from pathlib import Path
from unittest import mock

from geo_getter.downloader import download_plan, download_url_to_part, verify_md5
from geo_getter.errors import GeoGetterError
from geo_getter.models import DownloadPlan, FastqFile
from geo_getter.planner import (
    append_download_log,
    build_download_plan,
    download_log_path,
    ensure_capacity,
    fastq_manifest_path,
    validate_resume_artifacts,
    verify_fastq_manifest,
    write_fastq_outputs,
)
from geo_getter.path_safety import name_collision_key


class FakeUrlopenResponse:
    def __init__(self, data: bytes = b"", status: int = 200, headers: dict[str, str] | None = None, error: Exception | None = None):
        self.data = data
        self.status = status
        self.headers = headers or {}
        self.error = error
        self.offset = 0

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _traceback):
        return False

    def getcode(self):
        return self.status

    def read(self, size: int = -1):
        if self.error:
            raise self.error
        if self.offset >= len(self.data):
            return b""
        if size is None or size < 0:
            size = len(self.data) - self.offset
        chunk = self.data[self.offset : self.offset + size]
        self.offset += len(chunk)
        return chunk


class FailAfterFirstChunkResponse(FakeUrlopenResponse):
    def __init__(self, data: bytes, error: Exception):
        super().__init__(data=data, status=200, headers={"Content-Length": str(len(data))})
        self.error_after_first_chunk = error
        self.first_chunk_returned = False

    def read(self, size: int = -1):
        if self.first_chunk_returned:
            raise self.error_after_first_chunk
        self.first_chunk_returned = True
        return super().read(size)


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

    def test_new_download_uses_streaming_md5_without_rereading_part_file(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "source.fastq.gz"
            data = b"@r1\nACGT\n+\n!!!!\n"
            source.write_bytes(data)
            output_dir = Path(temp) / "out"
            expected_md5 = hashlib.md5(data).hexdigest()
            fastq = FastqFile(
                source_accession="FIXTURE",
                query_accession="FIXTURE",
                run_accession="FIXTURE_RUN",
                file_index=1,
                file_name="fixture.fastq.gz",
                url=source.as_uri(),
                expected_md5=expected_md5,
                size_bytes=len(data),
            )
            plan = build_download_plan("FIXTURE", "FIXTURE", [fastq], output_dir)

            with (
                mock.patch("geo_getter.downloader.calculate_md5", side_effect=AssertionError("unexpected md5 reread")),
                mock.patch("geo_getter.downloader.verify_md5", side_effect=AssertionError("unexpected md5 reread")),
            ):
                results = download_plan(plan)

            self.assertEqual(results[0][1], "md5_verified")
            self.assertEqual((output_dir / "fixture.fastq.gz").read_bytes(), data)
            self.assertFalse((output_dir / "fixture.fastq.gz.part").exists())
            with download_log_path(output_dir).open("r", encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle, delimiter="\t"))
            self.assertEqual(rows[-1]["actual_md5"], expected_md5)

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

    def test_new_download_without_expected_md5_uses_streaming_md5_for_log(self):
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

            with (
                mock.patch("geo_getter.downloader.calculate_md5", side_effect=AssertionError("unexpected md5 reread")),
                mock.patch("geo_getter.downloader.verify_md5", side_effect=AssertionError("unexpected md5 reread")),
            ):
                results = download_plan(plan)

            self.assertEqual(results[0][1], "md5_unavailable")
            self.assertEqual((output_dir / "fixture.fastq.gz").read_bytes(), data)
            with download_log_path(output_dir).open("r", encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle, delimiter="\t"))
            self.assertEqual(rows[-1]["actual_md5"], hashlib.md5(data).hexdigest())

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

    def test_new_download_md5_mismatch_uses_streaming_md5_before_quarantine(self):
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

            with (
                mock.patch("geo_getter.downloader.calculate_md5", side_effect=AssertionError("unexpected md5 reread")),
                mock.patch("geo_getter.downloader.verify_md5", side_effect=AssertionError("unexpected md5 reread")),
            ):
                results = download_plan(plan)

            self.assertEqual(results[0][1], "md5_mismatch")
            self.assertFalse((output_dir / "fixture.fastq.gz").exists())
            self.assertTrue(list(output_dir.glob("fixture.fastq.gz.part.bad-md5-*")))
            with download_log_path(output_dir).open("r", encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle, delimiter="\t"))
            self.assertEqual(rows[-1]["actual_md5"], hashlib.md5(data).hexdigest())

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

    def test_existing_file_with_unknown_size_reuses_matching_md5(self):
        with tempfile.TemporaryDirectory() as temp:
            output_dir = Path(temp) / "out"
            output_dir.mkdir()
            data = b"already downloaded with unknown size\n"
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
                size_bytes=0,
            )
            plan = build_download_plan("FIXTURE", "FIXTURE", [fastq], output_dir)

            results = download_plan(plan)

            self.assertEqual(results[0][1], "md5_verified")
            self.assertEqual(existing.read_bytes(), data)
            self.assertFalse(list(output_dir.glob("fixture.fastq.gz.*-existing-*")))

    def test_existing_file_requires_matching_size_before_reuse(self):
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
                size_bytes=len(data) + 1,
            )
            plan = build_download_plan("FIXTURE", "FIXTURE", [fastq], output_dir)

            results = download_plan(plan)

            self.assertEqual(results[0][1], "network_failed")
            self.assertFalse(existing.exists())
            self.assertTrue(list(output_dir.glob("fixture.fastq.gz.size-mismatch-existing-*")))

    def test_existing_directory_at_fastq_path_is_rejected_without_moving_it(self):
        with tempfile.TemporaryDirectory() as temp:
            output_dir = Path(temp) / "out"
            output_dir.mkdir()
            target = output_dir / "fixture.fastq.gz"
            target.mkdir()
            fastq = FastqFile(
                source_accession="FIXTURE",
                query_accession="FIXTURE",
                run_accession="FIXTURE_RUN",
                file_index=1,
                file_name="fixture.fastq.gz",
                url="file:///definitely/not/used.fastq.gz",
                expected_md5="1" * 32,
                size_bytes=10,
            )
            plan = build_download_plan("FIXTURE", "FIXTURE", [fastq], output_dir)

            with self.assertRaises(GeoGetterError) as context:
                download_plan(plan)

            self.assertEqual(context.exception.code, "output_path_invalid")
            self.assertTrue(target.is_dir())

    def test_existing_directory_at_part_path_is_rejected_without_moving_it(self):
        with tempfile.TemporaryDirectory() as temp:
            output_dir = Path(temp) / "out"
            output_dir.mkdir()
            part_target = output_dir / "fixture.fastq.gz.part"
            part_target.mkdir()
            fastq = FastqFile(
                source_accession="FIXTURE",
                query_accession="FIXTURE",
                run_accession="FIXTURE_RUN",
                file_index=1,
                file_name="fixture.fastq.gz",
                url="file:///definitely/not/used.fastq.gz",
                expected_md5="1" * 32,
                size_bytes=10,
            )
            plan = build_download_plan("FIXTURE", "FIXTURE", [fastq], output_dir)

            with self.assertRaises(GeoGetterError) as context:
                download_plan(plan)

            self.assertEqual(context.exception.code, "output_path_invalid")
            self.assertTrue(part_target.is_dir())

    def test_existing_mismatched_file_is_quarantined_before_redownload(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source.fastq.gz"
            data = b"correct data\n"
            source.write_bytes(data)
            output_dir = root / "out"
            output_dir.mkdir()
            existing = output_dir / "fixture.fastq.gz"
            existing.write_bytes(b"wrong content")
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

    def test_complete_part_file_without_md5_is_not_reused(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            output_dir = root / "out"
            output_dir.mkdir()
            data = b"complete unverified part\n"
            part = output_dir / "fixture.fastq.gz.part"
            part.write_bytes(data)
            source = root / "source.fastq.gz"
            source.write_bytes(data)
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
            self.assertFalse(part.exists())
            self.assertEqual((output_dir / "fixture.fastq.gz").read_bytes(), data)
            self.assertTrue(list(output_dir.glob("fixture.fastq.gz.part.unverified-existing-*")))

    def test_resume_artifacts_match_existing_manifest_and_log(self):
        with tempfile.TemporaryDirectory() as temp:
            output_dir = Path(temp) / "out"
            fastq = FastqFile(
                source_accession="FIXTURE",
                query_accession="FIXTURE",
                run_accession="FIXTURE_RUN",
                file_index=1,
                file_name="fixture.fastq.gz",
                url="https://example.invalid/fixture.fastq.gz",
                expected_md5="1" * 32,
                size_bytes=10,
            )
            plan = build_download_plan("FIXTURE", "FIXTURE", [fastq], output_dir)
            write_fastq_outputs(plan)
            append_download_log(output_dir, "FIXTURE_RUN", "fixture.fastq.gz", "network_failed", "1" * 32, "", 10, 3, "fixture")

            resume = validate_resume_artifacts(plan)

            self.assertEqual(resume.manifest_path, fastq_manifest_path(plan.output_dir))
            self.assertEqual(resume.download_log_path, download_log_path(plan.output_dir))
            self.assertEqual(resume.required_bytes, 10)
            self.assertEqual(resume.matched_fastq_count, 1)

    def test_resume_artifacts_reject_manifest_mismatch(self):
        with tempfile.TemporaryDirectory() as temp:
            output_dir = Path(temp) / "out"
            fastq = FastqFile(
                source_accession="FIXTURE",
                query_accession="FIXTURE",
                run_accession="FIXTURE_RUN",
                file_index=1,
                file_name="fixture.fastq.gz",
                url="https://example.invalid/fixture.fastq.gz",
                expected_md5="1" * 32,
                size_bytes=10,
            )
            plan = build_download_plan("FIXTURE", "FIXTURE", [fastq], output_dir)
            write_fastq_outputs(plan)
            manifest = fastq_manifest_path(output_dir)
            original = manifest.read_text(encoding="utf-8-sig")
            manifest.write_text(original.replace("https://example.invalid/fixture.fastq.gz", "https://example.invalid/other.fastq.gz"), encoding="utf-8-sig")
            changed = manifest.read_text(encoding="utf-8-sig")

            with self.assertRaises(GeoGetterError) as context:
                validate_resume_artifacts(plan)

            self.assertEqual(context.exception.code, "resume_artifact_mismatch")
            self.assertIn("fastq_manifest_selection_mismatch", context.exception.detail)
            self.assertEqual(manifest.read_text(encoding="utf-8-sig"), changed)

    def test_resume_artifacts_reject_download_log_outside_selection(self):
        with tempfile.TemporaryDirectory() as temp:
            output_dir = Path(temp) / "out"
            fastq = FastqFile(
                source_accession="FIXTURE",
                query_accession="FIXTURE",
                run_accession="FIXTURE_RUN",
                file_index=1,
                file_name="fixture.fastq.gz",
                url="https://example.invalid/fixture.fastq.gz",
                expected_md5="1" * 32,
                size_bytes=10,
            )
            plan = build_download_plan("FIXTURE", "FIXTURE", [fastq], output_dir)
            write_fastq_outputs(plan)
            append_download_log(output_dir, "OTHER_RUN", "other.fastq.gz", "network_failed", "2" * 32, "", 10, 0, "fixture")

            with self.assertRaises(GeoGetterError) as context:
                validate_resume_artifacts(plan)

            self.assertEqual(context.exception.code, "resume_artifact_mismatch")
            self.assertIn("download_log_selection_mismatch", context.exception.detail)

    def test_resume_artifacts_ignore_supplementary_log_rows(self):
        with tempfile.TemporaryDirectory() as temp:
            output_dir = Path(temp) / "out"
            fastq = FastqFile(
                source_accession="FIXTURE",
                query_accession="FIXTURE",
                run_accession="FIXTURE_RUN",
                file_index=1,
                file_name="fixture.fastq.gz",
                url="https://example.invalid/fixture.fastq.gz",
                expected_md5="1" * 32,
                size_bytes=10,
            )
            plan = build_download_plan("FIXTURE", "FIXTURE", [fastq], output_dir)
            write_fastq_outputs(plan)
            append_download_log(output_dir, "FIXTURE_RUN", "fixture.fastq.gz", "network_failed", "1" * 32, "", 10, 3, "fixture")
            append_download_log(output_dir, "GEO_SUPPLEMENTARY", "supplementary.txt", "download_complete", "", "", 0, 12, "fixture")

            resume = validate_resume_artifacts(plan)

            self.assertEqual(resume.matched_fastq_count, 1)

    def test_resume_required_bytes_uses_verified_existing_and_partial_files(self):
        with tempfile.TemporaryDirectory() as temp:
            output_dir = Path(temp) / "out"
            complete_data = b"complete\n"
            partial_data = b"abc"
            fastq1 = FastqFile(
                source_accession="FIXTURE",
                query_accession="FIXTURE",
                run_accession="RUN1",
                file_index=1,
                file_name="complete.fastq.gz",
                url="https://example.invalid/complete.fastq.gz",
                expected_md5=hashlib.md5(complete_data).hexdigest(),
                size_bytes=len(complete_data),
            )
            fastq2 = FastqFile(
                source_accession="FIXTURE",
                query_accession="FIXTURE",
                run_accession="RUN2",
                file_index=1,
                file_name="partial.fastq.gz",
                url="https://example.invalid/partial.fastq.gz",
                expected_md5="2" * 32,
                size_bytes=6,
            )
            plan = build_download_plan("FIXTURE", "FIXTURE", [fastq1, fastq2], output_dir)
            write_fastq_outputs(plan)
            (output_dir / "complete.fastq.gz").write_bytes(complete_data)
            (output_dir / "partial.fastq.gz.part").write_bytes(partial_data)

            resume = validate_resume_artifacts(plan)

            self.assertEqual(resume.required_bytes, 3)

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

    def test_resume_requires_matching_content_range(self):
        with tempfile.TemporaryDirectory() as temp:
            local_path = Path(temp) / "fixture.fastq.gz"
            part = Path(temp) / "fixture.fastq.gz.part"
            part.write_bytes(b"abc")
            response = FakeUrlopenResponse(
                data=b"def",
                status=206,
                headers={"Content-Length": "3", "Content-Range": "bytes 3-5/6"},
            )

            with mock.patch("geo_getter.downloader.urllib.request.urlopen", return_value=response):
                downloaded_part = download_url_to_part(
                    "https://example.invalid/fixture.fastq.gz",
                    local_path,
                    expected_size=6,
                    chunk_size=2,
                )

            self.assertEqual(downloaded_part.path, part)
            self.assertEqual(downloaded_part.bytes_downloaded, 6)
            self.assertTrue(downloaded_part.resumed)
            self.assertIsNone(downloaded_part.streamed_md5)
            self.assertEqual(part.read_bytes(), b"abcdef")

    def test_resumed_download_uses_full_md5_verification_after_append(self):
        with tempfile.TemporaryDirectory() as temp:
            output_dir = Path(temp) / "out"
            output_dir.mkdir()
            source_data = b"abcdef"
            part = output_dir / "fixture.fastq.gz.part"
            part.write_bytes(source_data[:3])
            response = FakeUrlopenResponse(
                data=source_data[3:],
                status=206,
                headers={"Content-Length": "3", "Content-Range": "bytes 3-5/6"},
            )
            range_headers = []
            fastq = FastqFile(
                source_accession="FIXTURE",
                query_accession="FIXTURE",
                run_accession="FIXTURE_RUN",
                file_index=1,
                file_name="fixture.fastq.gz",
                url="https://example.invalid/fixture.fastq.gz",
                expected_md5=hashlib.md5(source_data).hexdigest(),
                size_bytes=len(source_data),
            )
            plan = build_download_plan("FIXTURE", "FIXTURE", [fastq], output_dir)
            original_verify_md5 = verify_md5
            verify_calls = []

            def urlopen_resume(request, timeout):
                range_headers.append(request.get_header("Range"))
                return response

            def verify_complete_part(path, expected_md5):
                verify_calls.append((path.name, expected_md5, path.read_bytes()))
                return original_verify_md5(path, expected_md5)

            with (
                mock.patch("geo_getter.downloader.urllib.request.urlopen", side_effect=urlopen_resume),
                mock.patch("geo_getter.downloader.verify_md5", side_effect=verify_complete_part),
            ):
                results = download_plan(plan)

            self.assertEqual(results[0][1], "md5_verified")
            self.assertEqual(range_headers, ["bytes=3-"])
            self.assertEqual(len(verify_calls), 1)
            self.assertEqual(verify_calls[0][0], part.name)
            self.assertEqual(verify_calls[0][1], hashlib.md5(source_data).hexdigest())
            self.assertEqual(verify_calls[0][2], source_data)
            self.assertFalse(part.exists())
            self.assertEqual((output_dir / "fixture.fastq.gz").read_bytes(), source_data)

    def test_resume_rejects_missing_or_mismatched_content_range(self):
        for content_range in ("", "bytes 0-2/6"):
            with self.subTest(content_range=content_range):
                with tempfile.TemporaryDirectory() as temp:
                    local_path = Path(temp) / "fixture.fastq.gz"
                    part = Path(temp) / "fixture.fastq.gz.part"
                    part.write_bytes(b"abc")
                    headers = {"Content-Length": "3"}
                    if content_range:
                        headers["Content-Range"] = content_range
                    response = FakeUrlopenResponse(data=b"def", status=206, headers=headers)

                    with mock.patch("geo_getter.downloader.urllib.request.urlopen", return_value=response):
                        with self.assertRaises(OSError):
                            download_url_to_part(
                                "https://example.invalid/fixture.fastq.gz",
                                local_path,
                                expected_size=6,
                                max_retries=1,
                            )

                    self.assertEqual(part.read_bytes(), b"abc")

    def test_transient_transfer_errors_retry_and_succeed(self):
        for error in (urllib.error.URLError("temporary failure"), OSError("temporary failure")):
            with self.subTest(error=type(error).__name__):
                with tempfile.TemporaryDirectory() as temp:
                    local_path = Path(temp) / "fixture.fastq.gz"
                    messages = []
                    responses = [
                        error,
                        FakeUrlopenResponse(data=b"abcdef", status=200, headers={"Content-Length": "6"}),
                    ]

                    def urlopen_retry(_request, timeout):
                        item = responses.pop(0)
                        if isinstance(item, BaseException):
                            raise item
                        return item

                    with mock.patch("geo_getter.downloader.urllib.request.urlopen", side_effect=urlopen_retry):
                        downloaded_part = download_url_to_part(
                            "https://example.invalid/fixture.fastq.gz",
                            local_path,
                            expected_size=6,
                            message_callback=messages.append,
                            chunk_size=2,
                            max_retries=2,
                        )

                    self.assertEqual(downloaded_part.path, local_path.with_name("fixture.fastq.gz.part"))
                    self.assertEqual(downloaded_part.bytes_downloaded, 6)
                    self.assertFalse(downloaded_part.resumed)
                    self.assertIsNone(downloaded_part.streamed_md5)
                    self.assertEqual(downloaded_part.path.read_bytes(), b"abcdef")
                    self.assertEqual(len(responses), 0)
                    self.assertTrue(any("network_retry" in message for message in messages))

    def test_transient_transfer_error_after_partial_write_resumes_and_succeeds(self):
        with tempfile.TemporaryDirectory() as temp:
            local_path = Path(temp) / "fixture.fastq.gz"
            messages = []
            range_headers = []
            responses = [
                FailAfterFirstChunkResponse(b"abc", OSError("temporary failure after partial write")),
                FakeUrlopenResponse(
                    data=b"def",
                    status=206,
                    headers={"Content-Length": "3", "Content-Range": "bytes 3-5/6"},
                ),
            ]

            def urlopen_retry(request, timeout):
                range_headers.append(request.get_header("Range"))
                return responses.pop(0)

            with mock.patch("geo_getter.downloader.urllib.request.urlopen", side_effect=urlopen_retry):
                downloaded_part = download_url_to_part(
                    "https://example.invalid/fixture.fastq.gz",
                    local_path,
                    expected_size=6,
                    message_callback=messages.append,
                    chunk_size=3,
                    max_retries=2,
                )

            self.assertEqual(downloaded_part.path, local_path.with_name("fixture.fastq.gz.part"))
            self.assertEqual(downloaded_part.bytes_downloaded, 6)
            self.assertTrue(downloaded_part.resumed)
            self.assertIsNone(downloaded_part.streamed_md5)
            self.assertEqual(downloaded_part.path.read_bytes(), b"abcdef")
            self.assertEqual(range_headers, [None, "bytes=3-"])
            self.assertEqual(len(responses), 0)
            self.assertTrue(any("network_retry" in message for message in messages))

    def test_incomplete_read_is_logged_as_network_failed(self):
        with tempfile.TemporaryDirectory() as temp:
            output_dir = Path(temp) / "out"
            fastq = FastqFile(
                source_accession="FIXTURE",
                query_accession="FIXTURE",
                run_accession="FIXTURE_RUN",
                file_index=1,
                file_name="fixture.fastq.gz",
                url="https://example.invalid/fixture.fastq.gz",
                expected_md5="1" * 32,
                size_bytes=10,
            )
            plan = build_download_plan("FIXTURE", "FIXTURE", [fastq], output_dir)

            def fail_incomplete_read(_request, timeout):
                return FakeUrlopenResponse(error=http.client.IncompleteRead(b"partial", 10))

            with mock.patch("geo_getter.downloader.urllib.request.urlopen", side_effect=fail_incomplete_read):
                results = download_plan(plan)

            self.assertEqual(results[0][1], "network_failed")
            log_text = download_log_path(output_dir).read_text(encoding="utf-8-sig")
            self.assertIn("network_failed", log_text)
            self.assertIn("IncompleteRead", log_text)

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
            fastq3 = FastqFile(
                source_accession="GSE",
                query_accession="SRP",
                run_accession="SRR3",
                file_index=1,
                file_name="same.fastq.gz",
                url="https://example.invalid/c.fastq.gz",
                expected_md5="3" * 32,
                size_bytes=1,
            )
            plan = build_download_plan("GSE", "GSE", [fastq1, fastq2, fastq3], temp)
            self.assertEqual(plan.files[0].local_path.name, "same.fastq.gz")
            self.assertEqual(plan.files[1].local_path.name, "same.2.fastq.gz")
            self.assertEqual(plan.files[2].local_path.name, "same.3.fastq.gz")

    def test_pre_numbered_output_names_are_reserved_before_numbering(self):
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
                file_name="same.2.fastq.gz",
                url="https://example.invalid/b.fastq.gz",
                expected_md5="2" * 32,
                size_bytes=1,
            )
            fastq3 = FastqFile(
                source_accession="GSE",
                query_accession="SRP",
                run_accession="SRR3",
                file_index=1,
                file_name="same.fastq.gz",
                url="https://example.invalid/c.fastq.gz",
                expected_md5="3" * 32,
                size_bytes=1,
            )
            plan = build_download_plan("GSE", "GSE", [fastq1, fastq2, fastq3], temp)
            self.assertEqual([file.local_path.name for file in plan.files], ["same.fastq.gz", "same.2.fastq.gz", "same.3.fastq.gz"])

    def test_case_only_duplicate_output_names_are_disambiguated(self):
        with tempfile.TemporaryDirectory() as temp:
            fastq1 = FastqFile(
                source_accession="GSE",
                query_accession="SRP",
                run_accession="SRR1",
                file_index=1,
                file_name="Same.fastq.gz",
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
            self.assertEqual(plan.files[0].local_path.name, "Same.fastq.gz")
            self.assertEqual(plan.files[1].local_path.name, "same.2.fastq.gz")

    def test_fastq_output_names_avoid_download_artifacts(self):
        with tempfile.TemporaryDirectory() as temp:
            output_dir = Path(temp) / "out"
            files = [
                FastqFile(
                    source_accession="GSE",
                    query_accession="SRP",
                    run_accession=f"SRR{index}",
                    file_index=1,
                    file_name=file_name,
                    url=f"https://example.invalid/{index}",
                    expected_md5=str(index) * 32,
                    size_bytes=1,
                )
                for index, file_name in enumerate(
                    [
                        "out_fastq_manifest.tsv",
                        "out_supplementary_manifest.tsv",
                        "out_download_log.tsv",
                    ],
                    start=1,
                )
            ]

            plan = build_download_plan("GSE", "GSE", files, output_dir)

            self.assertEqual([planned.local_path.name for planned in plan.files], [
                "out_fastq_manifest.2.tsv",
                "out_supplementary_manifest.2.tsv",
                "out_download_log.2.tsv",
            ])

    def test_fastq_output_names_reserve_part_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            output_dir = Path(temp) / "out"
            files = [
                FastqFile(
                    source_accession="GSE",
                    query_accession="SRP",
                    run_accession="SRR1",
                    file_index=1,
                    file_name="same.fastq.gz",
                    url="https://example.invalid/1",
                    expected_md5="1" * 32,
                    size_bytes=1,
                ),
                FastqFile(
                    source_accession="GSE",
                    query_accession="SRP",
                    run_accession="SRR2",
                    file_index=1,
                    file_name="same.fastq.gz.part",
                    url="https://example.invalid/2",
                    expected_md5="2" * 32,
                    size_bytes=1,
                ),
            ]

            plan = build_download_plan("GSE", "GSE", files, output_dir)
            runtime_names = [
                name
                for planned in plan.files
                for name in (planned.local_path.name, f"{planned.local_path.name}.part")
            ]

            self.assertEqual([planned.local_path.name for planned in plan.files], [
                "same.fastq.gz",
                "same.fastq.gz.2.part",
            ])
            self.assertEqual(len(runtime_names), len({name_collision_key(name) for name in runtime_names}))

    def test_name_collision_key_matches_gui_boundary(self):
        self.assertEqual(name_collision_key("Same.fastq.gz"), name_collision_key("same.fastq.gz"))
        self.assertEqual(name_collision_key("\u03a3.txt"), name_collision_key("\u03c3.txt"))
        self.assertNotEqual(name_collision_key("\u00df.txt"), name_collision_key("SS.txt"))
        self.assertNotEqual(name_collision_key("\u03c2.txt"), name_collision_key("\u03c3.txt"))
        self.assertNotEqual(name_collision_key("\u0130.txt"), name_collision_key("i\u0307.txt"))
        self.assertNotEqual(name_collision_key("\u212a.txt"), name_collision_key("K.txt"))
        self.assertNotEqual(name_collision_key("\u1e9e.txt"), name_collision_key("\u00df.txt"))
        self.assertNotEqual(name_collision_key("\u212b.txt"), name_collision_key("\u00c5.txt"))

    def test_unsafe_fastq_file_name_stays_inside_output_dir(self):
        with tempfile.TemporaryDirectory() as temp:
            output_dir = Path(temp) / "out"
            fastq = FastqFile(
                source_accession="GSE",
                query_accession="SRP",
                run_accession="SRR1",
                file_index=1,
                file_name="../escape.fastq.gz",
                url="https://example.invalid/escape.fastq.gz",
                expected_md5="",
                size_bytes=0,
            )
            plan = build_download_plan("GSE", "GSE", [fastq], output_dir)
            self.assertEqual(plan.files[0].local_path.name, "_escape.fastq.gz")
            plan.files[0].local_path.resolve().relative_to(output_dir.resolve())

    def test_verify_fastq_manifest_writes_report_with_expected_statuses(self):
        with tempfile.TemporaryDirectory() as temp:
            output_dir = Path(temp) / "out"
            output_dir.mkdir()
            verified_data = b"verified\n"
            verified_path = output_dir / "verified.fastq.gz"
            verified_path.write_bytes(verified_data)
            no_md5_data = b"no-md5\n"
            no_md5_path = output_dir / "no-md5.fastq.gz"
            no_md5_path.write_bytes(no_md5_data)
            size_mismatch_data = b"size-mismatch\n"
            size_mismatch_path = output_dir / "size.fastq.gz"
            size_mismatch_path.write_bytes(size_mismatch_data)
            md5_mismatch_data = b"md5-mismatch\n"
            md5_mismatch_path = output_dir / "md5.fastq.gz"
            md5_mismatch_path.write_bytes(md5_mismatch_data)

            manifest = output_dir / "sample_fastq_manifest.tsv"
            manifest.write_text(
                "\n".join(
                    [
                        "source_accession\tquery_accession\trun_accession\tfile_index\tfile_name\turl\texpected_md5\tsize_bytes\tlocal_path\tstatus",
                        f"GSE\tSRP\tRUN1\t1\tverified.fastq.gz\thttps://example.invalid/verified\t{hashlib.md5(verified_data).hexdigest()}\t{len(verified_data)}\t{verified_path}\tplanned",
                        f"GSE\tSRP\tRUN2\t1\tno-md5.fastq.gz\thttps://example.invalid/no-md5\t\t{len(no_md5_data)}\t{no_md5_path}\tplanned",
                        f"GSE\tSRP\tRUN3\t1\tmissing.fastq.gz\thttps://example.invalid/missing\t{'1' * 32}\t10\t{output_dir / 'missing.fastq.gz'}\tplanned",
                        f"GSE\tSRP\tRUN4\t1\tsize.fastq.gz\thttps://example.invalid/size\t{hashlib.md5(size_mismatch_data).hexdigest()}\t{len(size_mismatch_data) + 1}\t{size_mismatch_path}\tplanned",
                        f"GSE\tSRP\tRUN5\t1\tmd5.fastq.gz\thttps://example.invalid/md5\t{'0' * 32}\t{len(md5_mismatch_data)}\t{md5_mismatch_path}\tplanned",
                    ]
                ),
                encoding="utf-8-sig",
            )

            result = verify_fastq_manifest(manifest)
            self.assertEqual(result["report_path"], output_dir / "verification_report.tsv")
            self.assertEqual(
                result["status_counts"],
                {
                    "md5_verified": 1,
                    "md5_unavailable": 1,
                    "missing": 1,
                    "size_mismatch": 1,
                    "md5_mismatch": 1,
                },
            )
            report_text = result["report_path"].read_text(encoding="utf-8-sig")
            self.assertIn("md5_verified", report_text)
            self.assertIn("md5_unavailable", report_text)
            self.assertIn("missing", report_text)
            self.assertIn("size_mismatch", report_text)
            self.assertIn("md5_mismatch", report_text)

    def test_verify_fastq_manifest_skips_md5_when_size_mismatched(self):
        with tempfile.TemporaryDirectory() as temp:
            output_dir = Path(temp) / "out"
            output_dir.mkdir()
            data = b"size-mismatch\n"
            fastq_path = output_dir / "size.fastq.gz"
            fastq_path.write_bytes(data)
            manifest = output_dir / "sample_fastq_manifest.tsv"
            manifest.write_text(
                "\n".join(
                    [
                        "source_accession\tquery_accession\trun_accession\tfile_index\tfile_name\turl\texpected_md5\tsize_bytes\tlocal_path\tstatus",
                        f"GSE\tSRP\tRUN1\t1\tsize.fastq.gz\thttps://example.invalid/size\t{hashlib.md5(data).hexdigest()}\t{len(data) + 1}\t{fastq_path}\tplanned",
                    ]
                ),
                encoding="utf-8-sig",
            )

            with mock.patch("geo_getter.planner._calculate_md5") as calculate_md5:
                result = verify_fastq_manifest(manifest)
            calculate_md5.assert_not_called()
            self.assertEqual(result["status_counts"], {"size_mismatch": 1})

    def test_verify_fastq_manifest_falls_back_to_manifest_folder_when_absolute_path_is_stale(self):
        with tempfile.TemporaryDirectory() as temp:
            output_dir = Path(temp) / "out"
            output_dir.mkdir()
            data = b"verified after folder move\n"
            fastq_path = output_dir / "verified.fastq.gz"
            fastq_path.write_bytes(data)
            stale_path = Path(temp) / "old_location" / "verified.fastq.gz"
            manifest = output_dir / "sample_fastq_manifest.tsv"
            manifest.write_text(
                "\n".join(
                    [
                        "source_accession\tquery_accession\trun_accession\tfile_index\tfile_name\turl\texpected_md5\tsize_bytes\tlocal_path\tstatus",
                        f"GSE\tSRP\tRUN1\t1\tverified.fastq.gz\thttps://example.invalid/verified\t{hashlib.md5(data).hexdigest()}\t{len(data)}\t{stale_path}\tplanned",
                    ]
                ),
                encoding="utf-8-sig",
            )

            result = verify_fastq_manifest(manifest)
            with result["report_path"].open("r", encoding="utf-8-sig", newline="") as handle:
                row = next(csv.DictReader(handle, delimiter="\t"))
            self.assertEqual(row["local_path"], str(fastq_path.resolve()))
            self.assertEqual(row["exists"], "yes")
            self.assertEqual(row["status"], "md5_verified")

    def test_verify_fastq_manifest_prefers_manifest_folder_when_absolute_path_still_exists(self):
        with tempfile.TemporaryDirectory() as temp:
            original_dir = Path(temp) / "original"
            copied_dir = Path(temp) / "copied"
            original_dir.mkdir()
            copied_dir.mkdir()
            original_data = b"original\n"
            copied_data = b"modified\n"
            original_path = original_dir / "verified.fastq.gz"
            copied_path = copied_dir / "verified.fastq.gz"
            original_path.write_bytes(original_data)
            copied_path.write_bytes(copied_data)
            manifest = copied_dir / "sample_fastq_manifest.tsv"
            manifest.write_text(
                "\n".join(
                    [
                        "source_accession\tquery_accession\trun_accession\tfile_index\tfile_name\turl\texpected_md5\tsize_bytes\tlocal_path\tstatus",
                        f"GSE\tSRP\tRUN1\t1\tverified.fastq.gz\thttps://example.invalid/verified\t{hashlib.md5(original_data).hexdigest()}\t{len(original_data)}\t{original_path}\tplanned",
                    ]
                ),
                encoding="utf-8-sig",
            )

            result = verify_fastq_manifest(manifest)
            with result["report_path"].open("r", encoding="utf-8-sig", newline="") as handle:
                row = next(csv.DictReader(handle, delimiter="\t"))
            self.assertEqual(result["status_counts"], {"md5_mismatch": 1})
            self.assertEqual(row["local_path"], str(copied_path.resolve()))
            self.assertEqual(row["actual_md5"], hashlib.md5(copied_data).hexdigest())
            self.assertEqual(row["status"], "md5_mismatch")

    def test_verify_fastq_manifest_falls_back_to_local_path_name_for_moved_duplicates(self):
        with tempfile.TemporaryDirectory() as temp:
            output_dir = Path(temp) / "out"
            output_dir.mkdir()
            first_data = b"first duplicate\n"
            second_data = b"second duplicate\n"
            first_path = output_dir / "same.fastq.gz"
            second_path = output_dir / "same.2.fastq.gz"
            first_path.write_bytes(first_data)
            second_path.write_bytes(second_data)
            stale_dir = Path(temp) / "old_location"
            manifest = output_dir / "sample_fastq_manifest.tsv"
            manifest.write_text(
                "\n".join(
                    [
                        "source_accession\tquery_accession\trun_accession\tfile_index\tfile_name\turl\texpected_md5\tsize_bytes\tlocal_path\tstatus",
                        f"GSE\tSRP\tRUN1\t1\tsame.fastq.gz\thttps://example.invalid/same\t{hashlib.md5(first_data).hexdigest()}\t{len(first_data)}\t{stale_dir / 'same.fastq.gz'}\tplanned",
                        f"GSE\tSRP\tRUN2\t1\tsame.fastq.gz\thttps://example.invalid/same2\t{hashlib.md5(second_data).hexdigest()}\t{len(second_data)}\t{stale_dir / 'same.2.fastq.gz'}\tplanned",
                    ]
                ),
                encoding="utf-8-sig",
            )

            result = verify_fastq_manifest(manifest)
            with result["report_path"].open("r", encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle, delimiter="\t"))
            self.assertEqual(result["status_counts"], {"md5_verified": 2})
            self.assertEqual(rows[0]["local_path"], str(first_path.resolve()))
            self.assertEqual(rows[1]["local_path"], str(second_path.resolve()))

    def test_verify_fastq_manifest_rejects_invalid_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            manifest = Path(temp) / "sample_fastq_manifest.tsv"
            manifest.write_text("file_name\tlocal_path\nfixture.fastq.gz\tfixture.fastq.gz\n", encoding="utf-8-sig")

            with self.assertRaises(GeoGetterError) as context:
                verify_fastq_manifest(manifest)
            self.assertEqual(context.exception.code, "invalid_manifest")

    def test_verify_fastq_manifest_rejects_report_path_overwriting_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            output_dir = Path(temp)
            data = b"verified\n"
            fastq_path = output_dir / "verified.fastq.gz"
            fastq_path.write_bytes(data)
            manifest = output_dir / "sample_fastq_manifest.tsv"
            original_manifest = "\n".join(
                [
                    "source_accession\tquery_accession\trun_accession\tfile_index\tfile_name\turl\texpected_md5\tsize_bytes\tlocal_path\tstatus",
                    f"GSE\tSRP\tRUN1\t1\tverified.fastq.gz\thttps://example.invalid/verified\t{hashlib.md5(data).hexdigest()}\t{len(data)}\t{fastq_path}\tplanned",
                ]
            )
            manifest.write_text(original_manifest, encoding="utf-8-sig")

            with self.assertRaises(GeoGetterError) as context:
                verify_fastq_manifest(manifest, report_path=manifest)
            self.assertEqual(context.exception.code, "invalid_manifest")
            self.assertEqual(manifest.read_text(encoding="utf-8-sig"), original_manifest)

    def test_verify_fastq_manifest_rejects_invalid_size_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            output_dir = Path(temp)
            data = b"verified\n"
            fastq_path = output_dir / "verified.fastq.gz"
            fastq_path.write_bytes(data)
            manifest = output_dir / "sample_fastq_manifest.tsv"
            manifest.write_text(
                "\n".join(
                    [
                        "source_accession\tquery_accession\trun_accession\tfile_index\tfile_name\turl\texpected_md5\tsize_bytes\tlocal_path\tstatus",
                        f"GSE\tSRP\tRUN1\t1\tverified.fastq.gz\thttps://example.invalid/verified\t{hashlib.md5(data).hexdigest()}\tabc\t{fastq_path}\tplanned",
                    ]
                ),
                encoding="utf-8-sig",
            )

            with self.assertRaises(GeoGetterError) as context:
                verify_fastq_manifest(manifest)
            self.assertEqual(context.exception.code, "invalid_manifest")


if __name__ == "__main__":
    unittest.main()
