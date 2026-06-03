import hashlib
import json
import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from geo_getter.cli import _load_json, _selected_download_json, _selected_fastq_from_payload, main
from geo_getter.planner import download_log_path, supplementary_manifest_path


class CliTest(unittest.TestCase):
    def test_help_exposes_supported_commands(self):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            with self.assertRaises(SystemExit) as context:
                main(["--help"])
        self.assertEqual(context.exception.code, 0)
        output = stdout.getvalue()
        self.assertIn("resolve-json", output)
        self.assertIn("selected-download-json", output)
        self.assertIn("verify-fastq-manifest", output)
        self.assertNotIn("plan-json", output)
        self.assertNotIn("verify-fixture", output)
        self.assertNotIn("\n    resolve ", output)
        self.assertNotIn("\n    download-json", output)

    def test_load_json_accepts_utf8_bom(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "payload.json"
            path.write_text("\ufeff{\"value\": 1}", encoding="utf-8")
            self.assertEqual(_load_json(path), {"value": 1})

    def test_selected_fastq_rejects_negative_index(self):
        payload = {
            "fastq_files": [
                {
                    "source_accession": "GSE",
                    "query_accession": "SRP",
                    "run_accession": "SRR",
                    "file_index": 1,
                    "file_name": "a.fastq.gz",
                    "url": "https://example.invalid/a.fastq.gz",
                    "expected_md5": "1" * 32,
                    "size_bytes": 1,
                }
            ]
        }
        with self.assertRaises(IndexError):
            _selected_fastq_from_payload(payload, "-1")

    def test_selected_fastq_rejects_empty_selection(self):
        with self.assertRaises(ValueError):
            _selected_fastq_from_payload({"fastq_files": []}, "")

    def test_selected_download_supports_supplementary(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "supplementary.txt"
            data = b"supplementary fixture\n"
            source.write_bytes(data)
            payload = {
                "input_text": "GSE000001",
                "primary_accession": "GSE000001",
                "fastq_files": [],
                "supplementary_files": [
                    {
                        "source_accession": "GSE000001",
                        "scope": "GEO Series supplementary/processed",
                        "name": "supplementary.txt",
                        "url": source.as_uri(),
                    }
                ],
            }
            input_json = root / "payload.json"
            input_json.write_text(json.dumps(payload), encoding="utf-8")
            out_dir = root / "out"

            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(_selected_download_json(input_json, "", "0", out_dir), 0)
            run_dir = out_dir / "GSE000001"
            self.assertEqual((run_dir / "supplementary.txt").read_bytes(), data)
            self.assertTrue(supplementary_manifest_path(run_dir).exists())
            self.assertFalse((run_dir / "supplementary_manifest.tsv").exists())
            log = download_log_path(run_dir).read_text(encoding="utf-8-sig")
            self.assertIn("download_complete", log)

            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(_selected_download_json(input_json, "", "0", out_dir), 0)
            second_run_dir = out_dir / "GSE000001_2"
            self.assertEqual((second_run_dir / "supplementary.txt").read_bytes(), data)
            self.assertTrue(download_log_path(second_run_dir).exists())

    def test_verify_fastq_manifest_cli_writes_report_event(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            data = b"verified\n"
            fastq_path = root / "verified.fastq.gz"
            fastq_path.write_bytes(data)
            manifest = root / "sample_fastq_manifest.tsv"
            manifest.write_text(
                "\n".join(
                    [
                        "source_accession\tquery_accession\trun_accession\tfile_index\tfile_name\turl\texpected_md5\tsize_bytes\tlocal_path\tstatus",
                        f"GSE\tSRP\tRUN1\t1\tverified.fastq.gz\thttps://example.invalid/verified\t{hashlib.md5(data).hexdigest()}\t{len(data)}\t{fastq_path}\tplanned",
                    ]
                ),
                encoding="utf-8-sig",
            )

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                self.assertEqual(main(["verify-fastq-manifest", "--manifest", str(manifest)]), 0)
            event = json.loads(stdout.getvalue())
            self.assertEqual(event["event"], "verification_report")
            report = Path(event["report"])
            self.assertEqual(report, root / "verification_report.tsv")
            self.assertEqual(event["statuses"], ["md5_verified"])
            self.assertIn("md5_verified", report.read_text(encoding="utf-8-sig"))

    def test_verify_fastq_manifest_cli_returns_failure_when_report_has_mismatch(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            data = b"mismatch\n"
            fastq_path = root / "mismatch.fastq.gz"
            fastq_path.write_bytes(data)
            manifest = root / "sample_fastq_manifest.tsv"
            manifest.write_text(
                "\n".join(
                    [
                        "source_accession\tquery_accession\trun_accession\tfile_index\tfile_name\turl\texpected_md5\tsize_bytes\tlocal_path\tstatus",
                        f"GSE\tSRP\tRUN1\t1\tmismatch.fastq.gz\thttps://example.invalid/mismatch\t{'0' * 32}\t{len(data)}\t{fastq_path}\tplanned",
                    ]
                ),
                encoding="utf-8-sig",
            )

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                self.assertEqual(main(["verify-fastq-manifest", "--manifest", str(manifest)]), 1)
            event = json.loads(stdout.getvalue())
            self.assertEqual(event["statuses"], ["md5_mismatch"])


if __name__ == "__main__":
    unittest.main()
