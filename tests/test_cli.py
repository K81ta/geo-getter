import csv
import hashlib
import json
import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from geo_getter.cli import _load_json, _selected_download_json, _selected_fastq_from_payload, main, run_cli
from geo_getter.planner import download_log_path, fastq_manifest_path, supplementary_manifest_path, verify_fastq_manifest


class CliTest(unittest.TestCase):
    def run_cli_with_streams(self, argv):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            exit_code = run_cli(argv)
        return exit_code, stdout.getvalue(), stderr.getvalue()

    def assert_cli_error(self, argv, expected_code):
        exit_code, stdout, stderr = self.run_cli_with_streams(argv)
        self.assertEqual(exit_code, 1)
        self.assertEqual(stdout, "")
        self.assertEqual(len(stderr.splitlines()), 1)
        payload = json.loads(stderr)
        self.assertEqual(payload["event"], "error")
        self.assertEqual(payload["code"], expected_code)
        self.assertIn("command", payload)
        self.assertIn("detail", payload)
        self.assertIn("message", payload)
        return payload

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

    def test_resolve_json_empty_input_emits_structured_stderr_error(self):
        payload = self.assert_cli_error(["resolve-json", ""], "invalid_input")
        self.assertEqual(payload["command"], "resolve-json")
        self.assertIn("input_text or --input-file", payload["message"])

    def test_selected_download_invalid_json_emits_structured_stderr_error(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            input_json = root / "invalid.json"
            input_json.write_text("{not-json", encoding="utf-8")

            payload = self.assert_cli_error(
                ["selected-download-json", "--input-json", str(input_json), "--out", str(root / "out")],
                "invalid_json",
            )

        self.assertEqual(payload["command"], "selected-download-json")

    def test_selected_download_missing_required_argument_emits_structured_stderr_error(self):
        payload = self.assert_cli_error(["selected-download-json"], "invalid_input")
        self.assertEqual(payload["command"], "selected-download-json")
        self.assertIn("required", payload["message"])

    def test_selected_download_out_of_range_index_emits_structured_stderr_error(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            input_json = root / "payload.json"
            input_json.write_text(
                json.dumps({"input_text": "GSE", "primary_accession": "GSE", "fastq_files": [], "supplementary_files": []}),
                encoding="utf-8",
            )

            payload = self.assert_cli_error(
                [
                    "selected-download-json",
                    "--input-json",
                    str(input_json),
                    "--fastq-indices",
                    "0",
                    "--out",
                    str(root / "out"),
                ],
                "selection_invalid",
            )

        self.assertEqual(payload["command"], "selected-download-json")

    def test_verify_manifest_invalid_manifest_emits_structured_stderr_error(self):
        with tempfile.TemporaryDirectory() as temp:
            manifest = Path(temp) / "sample_fastq_manifest.tsv"
            manifest.write_text("file_name\tlocal_path\nfixture.fastq.gz\tfixture.fastq.gz\n", encoding="utf-8-sig")

            payload = self.assert_cli_error(["verify-manifest-json", "--manifest", str(manifest)], "invalid_manifest")

        self.assertEqual(payload["command"], "verify-manifest-json")

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

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                self.assertEqual(_selected_download_json(input_json, "", "0", out_dir), 0)
            run_dir = out_dir
            resolved_run_dir = run_dir.resolve()
            done = json.loads(stdout.getvalue().splitlines()[-1])
            self.assertEqual(done["output_dir"], str(resolved_run_dir))
            self.assertEqual(done["fastq_manifest"], "")
            self.assertEqual(done["supplementary_manifest"], str(supplementary_manifest_path(resolved_run_dir)))
            self.assertEqual(done["download_log"], str(download_log_path(resolved_run_dir)))
            self.assertEqual((run_dir / "supplementary.txt").read_bytes(), data)
            self.assertTrue(supplementary_manifest_path(run_dir).exists())
            self.assertFalse((run_dir / "supplementary_manifest.tsv").exists())
            log = download_log_path(run_dir).read_text(encoding="utf-8-sig")
            self.assertIn("download_complete", log)

            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(_selected_download_json(input_json, "", "0", out_dir), 0)
            self.assertFalse((out_dir / "GSE000001").exists())
            self.assertFalse((out_dir / "GSE000001_2").exists())
            self.assertEqual((run_dir / "supplementary.txt").read_bytes(), data)
            self.assertTrue((run_dir / "supplementary.txt.existing").exists())
            self.assertTrue(download_log_path(run_dir).exists())

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
            saved = out_dir / "geo_supplementary_file"
            self.assertEqual(saved.read_bytes(), data)
            saved.resolve().relative_to(out_dir.resolve())

    def test_selected_download_disambiguates_case_only_supplementary_names(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source1 = root / "source1.txt"
            source2 = root / "source2.txt"
            source3 = root / "source3.txt"
            source1.write_bytes(b"first\n")
            source2.write_bytes(b"second\n")
            source3.write_bytes(b"third\n")
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
                        "name": "same.2.txt",
                        "url": source2.as_uri(),
                    },
                    {
                        "source_accession": "GSE000001",
                        "scope": "GEO Series supplementary/processed",
                        "name": "same.txt",
                        "url": source3.as_uri(),
                    },
                ],
            }
            input_json = root / "payload.json"
            input_json.write_text(json.dumps(payload), encoding="utf-8")
            out_dir = root / "out"

            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(_selected_download_json(input_json, "", "0,1,2", out_dir), 0)
            run_dir = out_dir
            self.assertEqual((run_dir / "Same.txt").read_bytes(), b"first\n")
            self.assertEqual((run_dir / "same.2.txt").read_bytes(), b"second\n")
            self.assertEqual((run_dir / "same.3.txt").read_bytes(), b"third\n")
            self.assertFalse((run_dir / "Same.txt.existing").exists())

    def test_selected_download_disambiguates_fastq_and_supplementary_names(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            fastq_source = root / "fastq_source.fastq.gz"
            supp_source = root / "supp_source.fastq.gz"
            fastq_data = b"@r1\nACGT\n+\n!!!!\n"
            supp_data = b"supplementary fixture\n"
            fastq_source.write_bytes(fastq_data)
            supp_source.write_bytes(supp_data)
            payload = {
                "input_text": "GSE000001",
                "primary_accession": "GSE000001",
                "fastq_files": [
                    {
                        "source_accession": "GSE000001",
                        "query_accession": "SRP000001",
                        "run_accession": "SRR000001",
                        "file_index": 1,
                        "file_name": "same.fastq.gz",
                        "url": fastq_source.as_uri(),
                        "expected_md5": hashlib.md5(fastq_data).hexdigest(),
                        "size_bytes": len(fastq_data),
                    }
                ],
                "supplementary_files": [
                    {
                        "source_accession": "GSE000001",
                        "scope": "GEO Series supplementary/processed",
                        "name": "same.fastq.gz",
                        "url": supp_source.as_uri(),
                    }
                ],
            }
            input_json = root / "payload.json"
            input_json.write_text(json.dumps(payload), encoding="utf-8")
            out_dir = root / "out"

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                self.assertEqual(_selected_download_json(input_json, "0", "0", out_dir), 0)

            done = json.loads(stdout.getvalue().splitlines()[-1])
            self.assertEqual(done["statuses"], ["md5_verified", "download_complete"])
            self.assertEqual((out_dir / "same.fastq.gz").read_bytes(), fastq_data)
            self.assertEqual((out_dir / "same.2.fastq.gz").read_bytes(), supp_data)
            self.assertFalse((out_dir / "same.fastq.gz.existing").exists())
            self.assertEqual(verify_fastq_manifest(fastq_manifest_path(out_dir))["status_counts"], {"md5_verified": 1})

    def test_selected_download_disambiguates_supplementary_name_from_artifacts(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source.tsv"
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
                        "name": "out_download_log.tsv",
                        "url": source.as_uri(),
                    }
                ],
            }
            input_json = root / "payload.json"
            input_json.write_text(json.dumps(payload), encoding="utf-8")
            out_dir = root / "out"

            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(_selected_download_json(input_json, "", "0", out_dir), 0)

            self.assertEqual((out_dir / "out_download_log.2.tsv").read_bytes(), data)
            log_text = download_log_path(out_dir).read_text(encoding="utf-8-sig")
            self.assertTrue(log_text.startswith("timestamp\t"))
            self.assertNotIn("supplementary fixture", log_text)

    def test_selected_download_disambiguates_fastq_name_from_artifacts_with_supplementary(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            fastq_source = root / "fastq_source.tsv"
            supp_source = root / "supp_source.txt"
            fastq_data = b"@r1\nACGT\n+\n!!!!\n"
            supp_data = b"supplementary fixture\n"
            fastq_source.write_bytes(fastq_data)
            supp_source.write_bytes(supp_data)
            payload = {
                "input_text": "GSE000001",
                "primary_accession": "GSE000001",
                "fastq_files": [
                    {
                        "source_accession": "GSE000001",
                        "query_accession": "SRP000001",
                        "run_accession": "SRR000001",
                        "file_index": 1,
                        "file_name": "out_supplementary_manifest.tsv",
                        "url": fastq_source.as_uri(),
                        "expected_md5": hashlib.md5(fastq_data).hexdigest(),
                        "size_bytes": len(fastq_data),
                    }
                ],
                "supplementary_files": [
                    {
                        "source_accession": "GSE000001",
                        "scope": "GEO Series supplementary/processed",
                        "name": "processed.txt",
                        "url": supp_source.as_uri(),
                    }
                ],
            }
            input_json = root / "payload.json"
            input_json.write_text(json.dumps(payload), encoding="utf-8")
            out_dir = root / "out"

            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(_selected_download_json(input_json, "0", "0", out_dir), 0)

            self.assertEqual((out_dir / "out_supplementary_manifest.2.tsv").read_bytes(), fastq_data)
            self.assertTrue(supplementary_manifest_path(out_dir).exists())
            self.assertEqual((out_dir / "processed.txt").read_bytes(), supp_data)
            self.assertEqual(verify_fastq_manifest(fastq_manifest_path(out_dir))["status_counts"], {"md5_verified": 1})

    def test_selected_download_logs_supplementary_part_size_on_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            payload = {
                "input_text": "GSE000001",
                "primary_accession": "GSE000001",
                "fastq_files": [],
                "supplementary_files": [
                    {
                        "source_accession": "GSE000001",
                        "scope": "GEO Series supplementary/processed",
                        "name": "supplementary.txt",
                        "url": "https://example.invalid/supplementary.txt",
                    }
                ],
            }
            input_json = root / "payload.json"
            input_json.write_text(json.dumps(payload), encoding="utf-8")
            out_dir = root / "out"

            def fail_with_part(_url, local_path, **_kwargs):
                local_path.with_name(local_path.name + ".part").write_bytes(b"abc")
                raise OSError("fixture transfer failure")

            stdout = io.StringIO()
            with mock.patch("geo_getter.cli.download_url_to_part", side_effect=fail_with_part):
                with contextlib.redirect_stdout(stdout):
                    self.assertEqual(_selected_download_json(input_json, "", "0", out_dir), 1)

            done = json.loads(stdout.getvalue().splitlines()[-1])
            self.assertEqual(done["statuses"], ["network_failed"])
            with download_log_path(out_dir).open("r", encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle, delimiter="\t"))
            self.assertEqual(rows[-1]["status"], "network_failed")
            self.assertEqual(rows[-1]["bytes_downloaded"], "3")

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
            self.assertNotIn('"event": "error"', output)
            self.assertIn('"network_failed"', output)
            run_dir = root / "out"
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
            self.assertNotIn('"event": "error"', output)
            self.assertIn('"md5_unavailable"', output)
            resolved_out_dir = out_dir.resolve()
            done = json.loads(output.splitlines()[-1])
            self.assertEqual(done["output_dir"], str(resolved_out_dir))
            self.assertEqual(done["fastq_manifest"], str(fastq_manifest_path(resolved_out_dir)))
            self.assertEqual(done["supplementary_manifest"], "")
            self.assertEqual(done["download_log"], str(download_log_path(resolved_out_dir)))
            self.assertTrue((out_dir / "source.fastq.gz").exists())


if __name__ == "__main__":
    unittest.main()
