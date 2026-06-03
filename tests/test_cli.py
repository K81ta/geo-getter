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
    def test_help_only_exposes_gui_bridge_commands(self):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            with self.assertRaises(SystemExit) as context:
                main(["--help"])
        self.assertEqual(context.exception.code, 0)
        output = stdout.getvalue()
        self.assertIn("resolve-json", output)
        self.assertIn("selected-download-json", output)
        self.assertNotIn("verify-manifest-json", output)
        self.assertNotIn("verify-fastq-manifest", output)
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

    def test_selected_download_sanitizes_supplementary_name(self):
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
                        "name": "..",
                        "url": source.as_uri(),
                    }
                ],
            }
            input_json = root / "payload.json"
            input_json.write_text(json.dumps(payload), encoding="utf-8")
            out_dir = root / "out"

            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(_selected_download_json(input_json, "", "0", out_dir), 0)
            saved = out_dir / "GSE000001" / "geo_supplementary_file"
            self.assertEqual(saved.read_bytes(), data)
            saved.resolve().relative_to((out_dir / "GSE000001").resolve())

    def test_selected_download_disambiguates_case_only_supplementary_names(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source1 = root / "source1.txt"
            source2 = root / "source2.txt"
            source1.write_bytes(b"first\n")
            source2.write_bytes(b"second\n")
            payload = {
                "input_text": "GSE000001",
                "primary_accession": "GSE000001",
                "fastq_files": [],
                "supplementary_files": [
                    {
                        "source_accession": "GSE000001",
                        "scope": "GEO Series supplementary/processed",
                        "name": "Same.txt",
                        "url": source1.as_uri(),
                    },
                    {
                        "source_accession": "GSE000001",
                        "scope": "GEO Series supplementary/processed",
                        "name": "same.txt",
                        "url": source2.as_uri(),
                    },
                ],
            }
            input_json = root / "payload.json"
            input_json.write_text(json.dumps(payload), encoding="utf-8")
            out_dir = root / "out"

            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(_selected_download_json(input_json, "", "0,1", out_dir), 0)
            run_dir = out_dir / "GSE000001"
            self.assertEqual((run_dir / "Same.txt").read_bytes(), b"first\n")
            self.assertEqual((run_dir / "same.2.txt").read_bytes(), b"second\n")
            self.assertFalse((run_dir / "Same.txt.existing").exists())

    def test_selected_download_reports_unsupported_fastq_url_without_losing_done_event(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            payload = {
                "input_text": "GSE000003",
                "primary_accession": "GSE000003",
                "fastq_files": [
                    {
                        "source_accession": "GSE000003",
                        "query_accession": "SRP000003",
                        "run_accession": "SRR000003",
                        "file_index": 1,
                        "file_name": "source.fastq.gz",
                        "url": "fasp.sra.ebi.ac.uk/vol1/source.fastq.gz",
                        "expected_md5": "1" * 32,
                        "size_bytes": 1,
                    }
                ],
                "supplementary_files": [],
            }
            input_json = root / "payload.json"
            input_json.write_text(json.dumps(payload), encoding="utf-8")
            stdout = io.StringIO()

            with contextlib.redirect_stdout(stdout):
                exit_code = _selected_download_json(input_json, "0", "", root / "out")

            output = stdout.getvalue()
            self.assertEqual(exit_code, 1)
            self.assertIn('"event": "done"', output)
            self.assertIn('"network_failed"', output)
            run_dir = root / "out" / "GSE000003"
            log_text = download_log_path(run_dir).read_text(encoding="utf-8")
            self.assertIn("network_failed", log_text)
            self.assertIn("unknown url type", log_text)

    def test_internal_manifest_verification_bridge_writes_json_event(self):
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
                self.assertEqual(main(["verify-manifest-json", "--manifest", str(manifest)]), 0)
            event = json.loads(stdout.getvalue())
            self.assertEqual(event["event"], "done")
            self.assertEqual(event["kind"], "manifest_verification")
            self.assertEqual(event["status_counts"], {"md5_verified": 1})
            self.assertEqual(Path(event["report"]), root / "verification_report.tsv")

    def test_internal_manifest_verification_bridge_returns_failure_for_mismatch(self):
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
                self.assertEqual(main(["verify-manifest-json", "--manifest", str(manifest)]), 1)
            event = json.loads(stdout.getvalue())
            self.assertEqual(event["status_counts"], {"md5_mismatch": 1})

    def test_selected_download_without_md5_emits_done_and_returns_nonzero(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source.fastq.gz"
            data = b"@r1\nACGT\n+\n!!!!\n"
            source.write_bytes(data)
            payload = {
                "input_text": "GSE000002",
                "primary_accession": "GSE000002",
                "fastq_files": [
                    {
                        "source_accession": "GSE000002",
                        "query_accession": "SRP000002",
                        "run_accession": "SRR000002",
                        "file_index": 1,
                        "file_name": "source.fastq.gz",
                        "url": source.as_uri(),
                        "expected_md5": "",
                        "size_bytes": len(data),
                    }
                ],
                "supplementary_files": [],
            }
            input_json = root / "payload.json"
            input_json.write_text(json.dumps(payload), encoding="utf-8")
            out_dir = root / "out"

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                exit_code = _selected_download_json(input_json, "0", "", out_dir)

            output = stdout.getvalue()
            self.assertEqual(exit_code, 1)
            self.assertIn('"event": "done"', output)
            self.assertIn('"md5_unavailable"', output)
            self.assertTrue((out_dir / "GSE000002" / "source.fastq.gz").exists())


if __name__ == "__main__":
    unittest.main()
