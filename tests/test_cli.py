import csv
import hashlib
import json
import contextlib
import io
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from geo_getter.cli import (
    main,
    run_cli,
)
from geo_getter.downloader import DownloadNetworkError
from geo_getter.errors import GeoGetterError
from geo_getter.models import DatasetMetadata, FastqFile, ResolveResult, SupplementaryFile
from geo_getter.planner import (
    SUPPLEMENTARY_MANIFEST_COLUMNS,
    download_log_path,
    fastq_manifest_path,
    supplementary_manifest_path,
    verify_fastq_manifest,
)


def http_error(status: int, url: str = "https://example.invalid/supplementary.txt") -> urllib.error.HTTPError:
    return urllib.error.HTTPError(url, status, f"HTTP {status}", {}, None)


class CliTest(unittest.TestCase):
    def run_cli_with_streams(self, argv):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            exit_code = run_cli(argv)
        return exit_code, stdout.getvalue(), stderr.getvalue()

    def run_cli_success(self, argv):
        exit_code, stdout, stderr = self.run_cli_with_streams(argv)
        self.assertEqual(exit_code, 0)
        self.assertEqual(stderr, "")
        return stdout

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

    def run_selected_download_json(
        self,
        input_json,
        fastq_indices,
        supp_indices,
        output_dir,
        *,
        resume_existing=False,
        download_workers=None,
    ):
        argv = [
            "selected-download-json",
            "--input-json",
            str(input_json),
            "--fastq-indices",
            fastq_indices,
            "--supp-indices",
            supp_indices,
            "--out",
            str(output_dir),
        ]
        if resume_existing:
            argv.append("--resume-existing")
        if download_workers is not None:
            argv.extend(["--download-workers", str(download_workers)])
        exit_code, stdout, stderr = self.run_cli_with_streams(argv)
        self.assertEqual(stderr, "")
        return exit_code, stdout

    def test_all_bridge_commands_emit_one_structured_error_line(self):
        cases = (
            ("resolve-json", ["resolve-json", "--unexpected-option"]),
            ("selected-download-json", ["selected-download-json"]),
            ("preflight-json", ["preflight-json"]),
            ("verify-manifest-json", ["verify-manifest-json"]),
            ("limits-json", ["limits-json", "--unexpected-option"]),
            ("check-update-json", ["check-update-json", "--unexpected-option"]),
            ("download-update-json", ["download-update-json"]),
        )
        for command, argv in cases:
            with self.subTest(command=command):
                payload = self.assert_cli_error(argv, "invalid_input")
                self.assertEqual(payload["command"], command)

    def run_preflight_json(self, input_json, fastq_indices, supp_indices, output_dir, resume_existing=False):
        argv = [
            "preflight-json",
            "--input-json",
            str(input_json),
            "--fastq-indices",
            fastq_indices,
            "--supp-indices",
            supp_indices,
            "--out",
            str(output_dir),
        ]
        if resume_existing:
            argv.append("--resume-existing")
        return json.loads(self.run_cli_success(argv))


    def write_single_fastq_payload(self, root: Path):
        source = root / "source.fastq.gz"
        data = b"@r1\nACGT\n+\n!!!!\n"
        source.write_bytes(data)
        input_json = root / "payload.json"
        input_json.write_text(
            json.dumps(
                {
                    "input_text": "GSE000001",
                    "primary_accession": "GSE000001",
                    "fastq_files": [
                        {
                            "source_accession": "GSE000001",
                            "query_accession": "SRP000001",
                            "run_accession": "SRR000001",
                            "file_index": 1,
                            "file_name": "source.fastq.gz",
                            "url": source.as_uri(),
                            "expected_md5": hashlib.md5(data).hexdigest(),
                            "size_bytes": len(data),
                        }
                    ],
                    "supplementary_files": [],
                }
            ),
            encoding="utf-8",
        )
        return input_json

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
        self.assertNotIn("limits-json", output)
        self.assertNotIn("check-update-json", output)
        self.assertNotIn("download-update-json", output)
        self.assertNotIn("preflight-json", output)
        self.assertNotIn("==SUPPRESS==", output)
        self.assertNotIn("verify-fastq-manifest", output)
        self.assertNotIn("plan-json", output)
        self.assertNotIn("verify-fixture", output)
        self.assertNotIn("\n    resolve ", output)
        self.assertNotIn("\n    download-json", output)

    def test_hidden_bridge_commands_accept_explicit_help(self):
        for command in ("preflight-json", "verify-manifest-json", "limits-json", "check-update-json", "download-update-json"):
            with self.subTest(command=command):
                stdout = io.StringIO()
                with contextlib.redirect_stdout(stdout):
                    with self.assertRaises(SystemExit) as context:
                        main([command, "--help"])
                self.assertEqual(context.exception.code, 0)
                self.assertIn(command, stdout.getvalue())

    def test_hidden_bridge_missing_required_argument_keeps_command_in_error_payload(self):
        payload = self.assert_cli_error(["verify-manifest-json"], "invalid_input")
        self.assertEqual(payload["command"], "verify-manifest-json")
        self.assertIn("required", payload["message"])

    def test_limits_json_hidden_bridge_writes_download_worker_limits(self):
        exit_code, stdout, stderr = self.run_cli_with_streams(["limits-json"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(stderr, "")
        self.assertEqual(
            json.loads(stdout),
            {
                "event": "done",
                "kind": "limits",
                "download_workers": {
                    "min": 1,
                    "max": 4,
                    "default": 2,
                },
            },
        )

    def test_input_json_accepts_utf8_bom(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "payload.json"
            path.write_text(
                "\ufeff"
                + json.dumps(
                    {
                        "input_text": "GSE",
                        "primary_accession": "GSE",
                        "fastq_files": [],
                        "supplementary_files": [],
                    }
                ),
                encoding="utf-8",
            )
            payload = self.assert_cli_error(
                [
                    "preflight-json",
                    "--input-json",
                    str(path),
                    "--fastq-indices",
                    " , ",
                    "--out",
                    str(Path(temp) / "out"),
                ],
                "selection_required",
            )

        self.assertEqual(payload["command"], "preflight-json")
        self.assertIn("Select at least one", payload["message"])

    def test_resolve_json_empty_input_emits_structured_stderr_error(self):
        payload = self.assert_cli_error(["resolve-json", ""], "invalid_input")
        self.assertEqual(payload["command"], "resolve-json")
        self.assertIn("input_text or --input-file", payload["message"])

    def test_resolve_json_writes_bridge_payload_from_dataclasses(self):
        result = ResolveResult(
            input_text="GSE000001",
            primary_accession="GSE000001",
            query_accessions=["SRP000001"],
            fastq_files=[
                FastqFile(
                    source_accession="GSE000001",
                    query_accession="SRP000001",
                    run_accession="SRR000001",
                    file_index=1,
                    file_name="reads.fastq.gz",
                    url="https://example.invalid/reads.fastq.gz",
                    expected_md5="1" * 32,
                    size_bytes=10,
                )
            ],
            supplementary_files=[
                SupplementaryFile(
                    source_accession="GSE000001",
                    scope="GEO Series supplementary/processed",
                    name="matrix.tsv",
                    url="https://example.invalid/matrix.tsv",
                )
            ],
            dataset_metadata=DatasetMetadata(
                accession="GSE000001",
                title="dataset title",
            ),
            warnings=["fixture warning"],
        )
        with mock.patch("geo_getter.cli.resolve_metadata", return_value=result):
            exit_code, stdout, stderr = self.run_cli_with_streams(["resolve-json", "GSE000001"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(stderr, "")
        payload = json.loads(stdout)
        self.assertEqual(payload["dataset_metadata"]["title"], "dataset title")
        self.assertEqual(payload["supplementary_files"][0]["name"], "matrix.tsv")
        self.assertEqual(FastqFile(**payload["fastq_files"][0]), result.fastq_files[0])

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

    def test_download_payload_schema_errors_are_invalid_input(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            list_payload = root / "list-payload.json"
            list_payload.write_text(json.dumps([]), encoding="utf-8")
            payload = self.assert_cli_error(
                [
                    "preflight-json",
                    "--input-json",
                    str(list_payload),
                    "--fastq-indices",
                    "0",
                    "--out",
                    str(root / "out"),
                ],
                "invalid_input",
            )
            self.assertIn("download payload must be an object", payload["detail"])

            missing_fastq_field = root / "missing-fastq-field.json"
            missing_fastq_field.write_text(
                json.dumps(
                    {
                        "input_text": "GSE",
                        "primary_accession": "GSE",
                        "fastq_files": [
                            {
                                "source_accession": "GSE",
                                "query_accession": "SRP",
                                "run_accession": "SRR",
                                "file_index": 1,
                                "file_name": "fixture.fastq.gz",
                                "url": "file:///fixture.fastq.gz",
                                "expected_md5": "",
                            }
                        ],
                        "supplementary_files": [],
                    }
                ),
                encoding="utf-8",
            )
            payload = self.assert_cli_error(
                [
                    "selected-download-json",
                    "--input-json",
                    str(missing_fastq_field),
                    "--fastq-indices",
                    "0",
                    "--out",
                    str(root / "out"),
                ],
                "invalid_input",
            )
            self.assertIn("FASTQ item is missing required field(s): size_bytes", payload["detail"])

    def test_preflight_json_rejects_blank_output_dir(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            input_json = self.write_single_fastq_payload(root)

            payload = self.assert_cli_error(
                [
                    "preflight-json",
                    "--input-json",
                    str(input_json),
                    "--fastq-indices",
                    "0",
                    "--out",
                    "",
                ],
                "output_path_invalid",
            )

        self.assertEqual(payload["path_error_code"], "output_required")

    def test_preflight_json_rejects_output_path_that_is_file(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            input_json = self.write_single_fastq_payload(root)
            output_file = root / "output-file"
            output_file.write_text("not a directory", encoding="utf-8")

            payload = self.assert_cli_error(
                [
                    "preflight-json",
                    "--input-json",
                    str(input_json),
                    "--fastq-indices",
                    "0",
                    "--out",
                    str(output_file),
                ],
                "output_path_invalid",
            )

        self.assertEqual(payload["path_error_code"], "output_is_file")
        self.assertEqual(payload["output_dir"], str(output_file.resolve()))

    def test_preflight_json_reports_write_probe_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            input_json = self.write_single_fastq_payload(root)

            with mock.patch("geo_getter.cli.tempfile.NamedTemporaryFile", side_effect=PermissionError("denied")):
                payload = self.assert_cli_error(
                    [
                        "preflight-json",
                        "--input-json",
                        str(input_json),
                        "--fastq-indices",
                        "0",
                        "--out",
                        str(root / "out"),
                    ],
                    "output_path_invalid",
                )

        self.assertEqual(payload["path_error_code"], "cannot_write")
        self.assertIn("denied", payload["error"])

    def test_preflight_json_reports_write_probe_cleanup_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            input_json = self.write_single_fastq_payload(root)
            original_unlink = Path.unlink

            def fake_unlink(path, *args, **kwargs):
                if path.name.startswith(".geo_getter_preflight_"):
                    raise PermissionError("cleanup denied")
                return original_unlink(path, *args, **kwargs)

            with mock.patch.object(Path, "unlink", fake_unlink):
                payload = self.assert_cli_error(
                    [
                        "preflight-json",
                        "--input-json",
                        str(input_json),
                        "--fastq-indices",
                        "0",
                        "--out",
                        str(root / "out"),
                    ],
                    "output_path_invalid",
                )

        self.assertEqual(payload["path_error_code"], "cannot_write")
        self.assertIn("cleanup denied", payload["error"])

    def test_preflight_path_length_uses_structured_error(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            input_json = self.write_single_fastq_payload(root)
            payload = self.assert_cli_error(
                [
                    "preflight-json",
                    "--input-json",
                    str(input_json),
                    "--fastq-indices",
                    "0",
                    "--out",
                    str(root / ("x" * 260)),
                ],
                "path_too_long",
            )

        self.assertEqual(payload["command"], "preflight-json")
        self.assertIn("path", payload)

    def test_preflight_path_length_counts_utf16_code_units(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            input_json = self.write_single_fastq_payload(root)
            payload = self.assert_cli_error(
                [
                    "preflight-json",
                    "--input-json",
                    str(input_json),
                    "--fastq-indices",
                    "0",
                    "--out",
                    str(root / ("\U0001f600" * 120)),
                ],
                "path_too_long",
            )

        self.assertEqual(payload["command"], "preflight-json")
        self.assertIn("path", payload)

    def test_preflight_json_accepts_nested_output_when_existing_ancestor_is_writable(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            input_json = self.write_single_fastq_payload(root)
            out_dir = root / "missing" / "nested" / "out"

            preflight = self.run_preflight_json(input_json, "0", "", out_dir)

        self.assertEqual(preflight["event"], "done")
        self.assertEqual(preflight["output_dir"], str(out_dir.resolve()))
        self.assertFalse(out_dir.exists())

    def test_preflight_json_rejects_empty_overall_selection_with_structured_code(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            input_json = self.write_single_fastq_payload(root)

            payload = self.assert_cli_error(
                [
                    "preflight-json",
                    "--input-json",
                    str(input_json),
                    "--out",
                    str(root / "out"),
                ],
                "selection_required",
            )

        self.assertIn("Select at least one", payload["message"])

    def test_selected_download_supplementary_out_of_range_index_emits_target_name(self):
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
                    "--supp-indices",
                    "0",
                    "--out",
                    str(root / "out"),
                ],
                "selection_invalid",
            )

        self.assertIn("supplementary index is out of range", payload["detail"])

    def test_preflight_json_rejects_malformed_supplementary_payload_at_boundary(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            input_json = root / "payload.json"
            input_json.write_text(
                json.dumps(
                    {
                        "input_text": "GSE",
                        "primary_accession": "GSE",
                        "fastq_files": [],
                        "supplementary_files": [
                            {
                                "source_accession": "GSE",
                                "scope": "GEO Series supplementary/processed",
                                "name": "supplementary.txt",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            payload = self.assert_cli_error(
                [
                    "preflight-json",
                    "--input-json",
                    str(input_json),
                    "--supp-indices",
                    "0",
                    "--out",
                    str(root / "out"),
                ],
                "invalid_input",
            )

        self.assertIn("missing required field", payload["message"])
        self.assertIn("url", payload["detail"])

    def test_selected_download_missing_required_argument_emits_structured_stderr_error(self):
        payload = self.assert_cli_error(["selected-download-json"], "invalid_input")
        self.assertEqual(payload["command"], "selected-download-json")
        self.assertIn("required", payload["message"])

    def test_selected_download_rejects_invalid_download_workers(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            input_json = root / "payload.json"
            input_json.write_text(
                json.dumps(
                    {
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
                ),
                encoding="utf-8",
            )

            payload = self.assert_cli_error(
                [
                    "selected-download-json",
                    "--input-json",
                    str(input_json),
                    "--supp-indices",
                    "0",
                    "--out",
                    str(root / "out"),
                    "--download-workers",
                    "5",
                ],
                "invalid_input",
            )

        self.assertIn("download_workers", payload["message"])

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
        self.assertIn("FASTQ index is out of range", payload["detail"])

    def test_verify_manifest_invalid_manifest_emits_structured_stderr_error(self):
        with tempfile.TemporaryDirectory() as temp:
            manifest = Path(temp) / "sample_fastq_manifest.tsv"
            manifest.write_text("file_name\tlocal_path\nfixture.fastq.gz\tfixture.fastq.gz\n", encoding="utf-8-sig")

            payload = self.assert_cli_error(["verify-manifest-json", "--manifest", str(manifest)], "invalid_manifest")

        self.assertEqual(payload["command"], "verify-manifest-json")

    def test_verify_manifest_missing_file_emits_invalid_manifest_error(self):
        with tempfile.TemporaryDirectory() as temp:
            manifest = Path(temp) / "missing_fastq_manifest.tsv"

            payload = self.assert_cli_error(["verify-manifest-json", "--manifest", str(manifest)], "invalid_manifest")

        self.assertEqual(payload["command"], "verify-manifest-json")
        self.assertIn("read_failed", payload["detail"])

    def test_check_update_hidden_bridge_writes_json_event(self):
        event = {
            "event": "done",
            "kind": "update_check",
            "current_version": "0.1.3",
            "latest_version": "0.1.3",
            "update_available": False,
            "release_url": "https://example.invalid/release",
            "asset": None,
        }
        with mock.patch("geo_getter.cli.check_for_update", return_value=event):
            exit_code, stdout, stderr = self.run_cli_with_streams(["check-update-json"])
        self.assertEqual(exit_code, 0)
        self.assertEqual(stderr, "")
        self.assertEqual(json.loads(stdout), event)

    def test_download_update_hidden_bridge_writes_json_event(self):
        event = {
            "event": "done",
            "kind": "update_installer",
            "version": "0.1.4",
            "installer_path": "C:\\tmp\\GEOGetter-Setup-v0.1.4.exe",
            "sha256": "1" * 64,
            "bytes": 10,
        }
        with mock.patch("geo_getter.cli.download_update_installer", return_value=event) as download_update:
            exit_code, stdout, stderr = self.run_cli_with_streams(["download-update-json", "--version", "0.1.4", "--out-dir", "C:\\tmp"])
        self.assertEqual(exit_code, 0)
        self.assertEqual(stderr, "")
        self.assertEqual(json.loads(stdout), event)
        download_update.assert_called_once_with("0.1.4", output_dir="C:\\tmp")

    def test_check_update_error_emits_structured_stderr_error(self):
        with mock.patch("geo_getter.cli.check_for_update", side_effect=GeoGetterError("update_digest_missing", "fixture")):
            payload = self.assert_cli_error(["check-update-json"], "update_digest_missing")
        self.assertEqual(payload["command"], "check-update-json")

    def test_download_update_error_emits_structured_stderr_error(self):
        with mock.patch("geo_getter.cli.download_update_installer", side_effect=GeoGetterError("update_download_failed", "fixture")):
            payload = self.assert_cli_error(["download-update-json", "--version", "0.1.4"], "update_download_failed")
        self.assertEqual(payload["command"], "download-update-json")
        self.assertIn("fixture", payload["detail"])

    def test_selected_fastq_rejects_negative_index(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            input_json = root / "payload.json"
            input_json.write_text(
                json.dumps(
                    {
                        "input_text": "GSE",
                        "primary_accession": "GSE",
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
                        ],
                        "supplementary_files": [],
                    }
                ),
                encoding="utf-8",
            )
            payload = self.assert_cli_error(
                [
                    "selected-download-json",
                    "--input-json",
                    str(input_json),
                    "--fastq-indices",
                    "-1",
                    "--out",
                    str(root / "out"),
                ],
                "selection_invalid",
            )

        self.assertIn("FASTQ index is out of range: -1", payload["detail"])

    def test_selected_download_rejects_empty_overall_selection(self):
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
                    "--out",
                    str(root / "out"),
                ],
                "selection_required",
            )

        self.assertIn("Select at least one", payload["message"])

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
                        "origin_level": "series",
                        "origin_accession": "GSE000001",
                        "unknown_extra_field": "ignored at CLI boundary",
                    }
                ],
            }
            input_json = root / "payload.json"
            input_json.write_text(json.dumps(payload), encoding="utf-8")
            out_dir = root / "out"

            exit_code, stdout = self.run_selected_download_json(input_json, "", "0", out_dir)
            self.assertEqual(exit_code, 0)
            run_dir = out_dir
            resolved_run_dir = run_dir.resolve()
            done = json.loads(stdout.splitlines()[-1])
            self.assertEqual(done["output_dir"], str(resolved_run_dir))
            self.assertEqual(done["fastq_manifest"], "")
            self.assertEqual(done["supplementary_manifest"], str(supplementary_manifest_path(resolved_run_dir)))
            self.assertEqual(done["download_log"], str(download_log_path(resolved_run_dir)))
            self.assertEqual((run_dir / "supplementary.txt").read_bytes(), data)
            self.assertTrue(supplementary_manifest_path(run_dir).exists())
            self.assertFalse((run_dir / "supplementary_manifest.tsv").exists())
            manifest = supplementary_manifest_path(run_dir).read_text(encoding="utf-8-sig")
            self.assertEqual(manifest.splitlines()[0].split("\t"), list(SUPPLEMENTARY_MANIFEST_COLUMNS))
            self.assertNotIn("origin_level", manifest)
            self.assertNotIn("unknown_extra_field", manifest)
            with supplementary_manifest_path(run_dir).open("r", encoding="utf-8-sig", newline="") as handle:
                manifest_rows = list(csv.DictReader(handle, delimiter="\t"))
            self.assertEqual(
                manifest_rows,
                [
                    {
                        "source_accession": "GSE000001",
                        "scope": "GEO Series supplementary/processed",
                        "file_name": "supplementary.txt",
                        "url": source.as_uri(),
                        "local_path": str(resolved_run_dir / "supplementary.txt"),
                        "status": "planned",
                    }
                ],
            )
            log = download_log_path(run_dir).read_text(encoding="utf-8-sig")
            self.assertIn("download_complete", log)
            self.assertNotIn("not_applicable", log)

    def test_preflight_json_plans_fastq_and_supplementary_with_python_names(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source.fastq.gz"
            data = b"@r1\nACGT\n+\n!!!!\n"
            source.write_bytes(data)
            out_dir = root / "collision output"
            payload = {
                "input_text": "GSE000001",
                "primary_accession": "GSE000001",
                "fastq_files": [
                    {
                        "source_accession": "GSE000001",
                        "query_accession": "SRP000001",
                        "run_accession": "SRR000001",
                        "file_index": 1,
                        "file_name": "Same.fastq.gz",
                        "url": source.as_uri(),
                        "expected_md5": hashlib.md5(data).hexdigest(),
                        "size_bytes": len(data),
                    },
                    {
                        "source_accession": "GSE000001",
                        "query_accession": "SRP000001",
                        "run_accession": "SRR000002",
                        "file_index": 1,
                        "file_name": "same.2.fastq.gz",
                        "url": source.as_uri(),
                        "expected_md5": hashlib.md5(data).hexdigest(),
                        "size_bytes": len(data),
                    },
                    {
                        "source_accession": "GSE000001",
                        "query_accession": "SRP000001",
                        "run_accession": "SRR000003",
                        "file_index": 1,
                        "file_name": "same.fastq.gz",
                        "url": source.as_uri(),
                        "expected_md5": hashlib.md5(data).hexdigest(),
                        "size_bytes": len(data),
                    },
                    {
                        "source_accession": "GSE000001",
                        "query_accession": "SRP000001",
                        "run_accession": "SRR000004",
                        "file_index": 1,
                        "file_name": "collision output_fastq_manifest.tsv",
                        "url": source.as_uri(),
                        "expected_md5": hashlib.md5(data).hexdigest(),
                        "size_bytes": len(data),
                    },
                ],
                "supplementary_files": [
                    {
                        "source_accession": "GSE000001",
                        "scope": "GEO Series supplementary/processed",
                        "name": "same.fastq.gz",
                        "url": source.as_uri(),
                    },
                    {
                        "source_accession": "GSE000001",
                        "scope": "GEO Series supplementary/processed",
                        "name": "same.fastq.gz.part",
                        "url": source.as_uri(),
                    },
                    {
                        "source_accession": "GSE000001",
                        "scope": "GEO Series supplementary/processed",
                        "name": "collision output_download_log.tsv",
                        "url": source.as_uri(),
                    },
                ],
            }
            input_json = root / "payload.json"
            input_json.write_text(json.dumps(payload), encoding="utf-8")

            preflight = self.run_preflight_json(input_json, "0,1,2,3", "0,1,2", out_dir)

            self.assertEqual(preflight["event"], "done")
            self.assertEqual(preflight["kind"], "download_preflight")
            self.assertEqual(preflight["output_dir"], str(out_dir.resolve()))
            self.assertEqual(preflight["existing_output_nonempty"], False)
            self.assertFalse(out_dir.exists())
            self.assertEqual(preflight["fastq_required_bytes"], len(data) * 4)
            self.assertEqual(preflight["supplementary_required_bytes"], len(data) * 3)
            self.assertEqual(preflight["supplementary_size_unknown_count"], 0)
            self.assertEqual(preflight["capacity_unknown"], False)
            self.assertEqual(preflight["required_bytes"], len(data) * 7)
            self.assertGreater(preflight["free_bytes"], 0)
            self.assertEqual(preflight["capacity_checked"], True)
            self.assertEqual(preflight["capacity_ok"], True)
            self.assertIsNone(preflight["capacity_error_code"])
            self.assertEqual(preflight["fastq_manifest"], str(fastq_manifest_path(out_dir.resolve())))
            self.assertEqual(preflight["supplementary_manifest"], str(supplementary_manifest_path(out_dir.resolve())))
            self.assertEqual(preflight["download_log"], str(download_log_path(out_dir.resolve())))
            self.assertEqual(Path(preflight["fastq_files"][3]["local_path"]).name, "collision output_fastq_manifest.2.tsv")
            self.assertEqual(Path(preflight["supplementary_files"][0]["local_path"]).name, "same.4.fastq.gz")
            self.assertEqual(Path(preflight["supplementary_files"][1]["local_path"]).name, "same.fastq.gz.2.part")
            self.assertEqual(preflight["supplementary_files"][0]["size_bytes"], len(data))
            self.assertEqual(preflight["supplementary_files"][0]["size_status"], "known")

            planned_names = {Path(path).name for path in preflight["planned_paths"]}
            self.assertIn("Same.fastq.gz", planned_names)
            self.assertIn("same.2.fastq.gz", planned_names)
            self.assertIn("same.3.fastq.gz", planned_names)
            self.assertIn("same.4.fastq.gz", planned_names)
            self.assertIn("same.4.fastq.gz.part", planned_names)
            self.assertNotIn("Same.fastq.gz.part.unverified-existing-20000101T000000Z", planned_names)
            self.assertIn("same.fastq.gz.2.part", planned_names)
            self.assertIn("same.fastq.gz.2.part.part", planned_names)
            self.assertIn("collision output_fastq_manifest.tsv", planned_names)
            self.assertIn("collision output_fastq_manifest.2.tsv", planned_names)
            self.assertIn("collision output_supplementary_manifest.tsv", planned_names)
            self.assertIn("collision output_download_log.tsv", planned_names)
            self.assertIn("collision output_download_log.2.tsv", planned_names)
            self.assertFalse(any("20000101T000000Z" in name for name in planned_names))
            self.assertFalse(any(name.endswith(".existing") or ".existing." in name for name in planned_names))

            exit_code, _stdout = self.run_selected_download_json(input_json, "0,1,2,3", "0,1,2", out_dir)
            self.assertEqual(exit_code, 0)

            with fastq_manifest_path(out_dir).open("r", encoding="utf-8-sig", newline="") as handle:
                fastq_manifest_rows = list(csv.DictReader(handle, delimiter="\t"))
            with supplementary_manifest_path(out_dir).open("r", encoding="utf-8-sig", newline="") as handle:
                supp_manifest_rows = list(csv.DictReader(handle, delimiter="\t"))
            self.assertEqual(
                [row["local_path"] for row in fastq_manifest_rows],
                [Path(item["local_path"]).name for item in preflight["fastq_files"]],
            )
            self.assertEqual([row["local_path"] for row in supp_manifest_rows], [item["local_path"] for item in preflight["supplementary_files"]])
            for item in preflight["supplementary_files"]:
                self.assertEqual(Path(item["local_path"]).read_bytes(), data)

    def test_preflight_json_reports_existing_fastq_without_resume(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source.fastq.gz"
            data = b"@r1\nACGT\n+\n!!!!\n"
            source.write_bytes(data)
            out_dir = root / "out"
            out_dir.mkdir()
            (out_dir / "existing.txt").write_text("existing", encoding="utf-8")
            input_json = root / "payload.json"
            input_json.write_text(
                json.dumps(
                    {
                        "input_text": "GSE000001",
                        "primary_accession": "GSE000001",
                        "fastq_files": [
                            {
                                "source_accession": "GSE000001",
                                "query_accession": "SRP000001",
                                "run_accession": "SRR000001",
                                "file_index": 1,
                                "file_name": "source.fastq.gz",
                                "url": source.as_uri(),
                                "expected_md5": hashlib.md5(data).hexdigest(),
                                "size_bytes": len(data),
                            }
                        ],
                        "supplementary_files": [],
                    }
                ),
                encoding="utf-8",
            )

            with mock.patch("geo_getter.planner.shutil.disk_usage", return_value=mock.Mock(free=0)):
                preflight = self.run_preflight_json(input_json, "0", "", out_dir)

            self.assertEqual(preflight["existing_output_nonempty"], True)
            self.assertEqual(preflight["resume_existing"], False)
            self.assertIsNone(preflight["resume_required_bytes"])
            self.assertEqual(preflight["required_bytes"], len(data))
            self.assertEqual(preflight["free_bytes"], 0)
            self.assertEqual(preflight["capacity_checked"], False)
            self.assertEqual(preflight["capacity_ok"], True)
            self.assertIsNone(preflight["capacity_error_code"])

    def test_preflight_json_validates_resume_when_requested(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source.fastq.gz"
            data = b"@r1\nACGT\n+\n!!!!\n"
            source.write_bytes(data)
            input_json = root / "payload.json"
            input_json.write_text(
                json.dumps(
                    {
                        "input_text": "GSE000001",
                        "primary_accession": "GSE000001",
                        "fastq_files": [
                            {
                                "source_accession": "GSE000001",
                                "query_accession": "SRP000001",
                                "run_accession": "SRR000001",
                                "file_index": 1,
                                "file_name": "source.fastq.gz",
                                "url": source.as_uri(),
                                "expected_md5": hashlib.md5(data).hexdigest(),
                                "size_bytes": len(data),
                            }
                        ],
                        "supplementary_files": [],
                    }
                ),
                encoding="utf-8",
            )
            out_dir = root / "out"
            exit_code, _stdout = self.run_selected_download_json(input_json, "0", "", out_dir)
            self.assertEqual(exit_code, 0)

            preflight = self.run_preflight_json(input_json, "0", "", out_dir, resume_existing=True)

            self.assertEqual(preflight["existing_output_nonempty"], True)
            self.assertEqual(preflight["resume_existing"], True)
            self.assertEqual(preflight["resume_required_bytes"], 0)
            self.assertEqual(preflight["required_bytes"], 0)
            self.assertEqual(preflight["capacity_checked"], True)
            self.assertEqual(preflight["capacity_ok"], True)
            self.assertIsNone(preflight["capacity_error_code"])

    def test_preflight_json_reports_insufficient_space_result(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source.fastq.gz"
            data = b"@r1\nACGT\n+\n!!!!\n"
            source.write_bytes(data)
            input_json = root / "payload.json"
            input_json.write_text(
                json.dumps(
                    {
                        "input_text": "GSE000001",
                        "primary_accession": "GSE000001",
                        "fastq_files": [
                            {
                                "source_accession": "GSE000001",
                                "query_accession": "SRP000001",
                                "run_accession": "SRR000001",
                                "file_index": 1,
                                "file_name": "source.fastq.gz",
                                "url": source.as_uri(),
                                "expected_md5": hashlib.md5(data).hexdigest(),
                                "size_bytes": len(data),
                            }
                        ],
                        "supplementary_files": [],
                    }
                ),
                encoding="utf-8",
            )
            out_dir = root / "out"

            with mock.patch("geo_getter.planner.shutil.disk_usage", return_value=mock.Mock(free=len(data) - 1)):
                preflight = self.run_preflight_json(input_json, "0", "", out_dir)

            self.assertEqual(preflight["required_bytes"], len(data))
            self.assertEqual(preflight["free_bytes"], len(data) - 1)
            self.assertIsInstance(preflight["required_bytes"], int)
            self.assertIsInstance(preflight["free_bytes"], int)
            self.assertEqual(preflight["capacity_checked"], True)
            self.assertEqual(preflight["capacity_ok"], False)
            self.assertEqual(preflight["capacity_error_code"], "insufficient_space")

    def test_preflight_json_reports_supplementary_known_and_unknown_sizes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            out_dir = root / "supplementary out"
            input_json = root / "payload.json"
            input_json.write_text(
                json.dumps(
                    {
                        "input_text": "GSE000001",
                        "primary_accession": "GSE000001",
                        "fastq_files": [],
                        "supplementary_files": [
                            {
                                "source_accession": "GSE000001",
                                "scope": "GEO Series supplementary/processed",
                                "name": "known.txt",
                                "url": "https://example.invalid/known.txt",
                            },
                            {
                                "source_accession": "GSE000001",
                                "scope": "GEO Series supplementary/processed",
                                "name": "unknown.txt",
                                "url": "https://example.invalid/unknown.txt",
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )

            class HeadResponse:
                headers = {"Content-Length": "17"}

                def __enter__(self):
                    return self

                def __exit__(self, _exc_type, _exc, _traceback):
                    return False

            def fake_urlopen(request, timeout):
                self.assertEqual(timeout, 30)
                if request.full_url.endswith("/known.txt"):
                    return HeadResponse()
                raise urllib.error.URLError("no content length")

            with mock.patch("geo_getter.cli.urllib.request.urlopen", side_effect=fake_urlopen):
                preflight = self.run_preflight_json(input_json, "", "0,1", out_dir)

            self.assertFalse(out_dir.exists())
            self.assertEqual(preflight["required_bytes"], 17)
            self.assertEqual(preflight["fastq_required_bytes"], 0)
            self.assertEqual(preflight["supplementary_required_bytes"], 17)
            self.assertEqual(preflight["supplementary_size_unknown_count"], 1)
            self.assertEqual(preflight["capacity_unknown"], True)
            self.assertEqual(preflight["supplementary_files"][0]["size_status"], "known")
            self.assertEqual(preflight["supplementary_files"][0]["size_bytes"], 17)
            self.assertEqual(preflight["supplementary_files"][1]["size_status"], "unknown")

    def test_preflight_json_rejects_supplementary_in_nonempty_output(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "supplementary.txt"
            source.write_bytes(b"supplementary fixture\n")
            out_dir = root / "out"
            out_dir.mkdir()
            (out_dir / "existing.txt").write_text("existing", encoding="utf-8")
            input_json = root / "payload.json"
            input_json.write_text(
                json.dumps(
                    {
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
                ),
                encoding="utf-8",
            )

            payload = self.assert_cli_error(
                [
                    "preflight-json",
                    "--input-json",
                    str(input_json),
                    "--supp-indices",
                    "0",
                    "--out",
                    str(out_dir),
                    "--resume-existing",
                ],
                "resume_supplementary_unsupported",
            )
            self.assertEqual(payload["existing_output_nonempty"], True)
            self.assertEqual(payload["output_dir"], str(out_dir.resolve()))

    def test_preflight_json_resume_rejects_missing_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source.fastq.gz"
            data = b"@r1\nACGT\n+\n!!!!\n"
            source.write_bytes(data)
            out_dir = root / "out"
            out_dir.mkdir()
            (out_dir / "existing.txt").write_text("existing", encoding="utf-8")
            input_json = root / "payload.json"
            input_json.write_text(
                json.dumps(
                    {
                        "input_text": "GSE000001",
                        "primary_accession": "GSE000001",
                        "fastq_files": [
                            {
                                "source_accession": "GSE000001",
                                "query_accession": "SRP000001",
                                "run_accession": "SRR000001",
                                "file_index": 1,
                                "file_name": "source.fastq.gz",
                                "url": source.as_uri(),
                                "expected_md5": hashlib.md5(data).hexdigest(),
                                "size_bytes": len(data),
                            }
                        ],
                        "supplementary_files": [],
                    }
                ),
                encoding="utf-8",
            )

            payload = self.assert_cli_error(
                [
                    "preflight-json",
                    "--input-json",
                    str(input_json),
                    "--fastq-indices",
                    "0",
                    "--out",
                    str(out_dir),
                    "--resume-existing",
                ],
                "resume_artifact_mismatch",
            )
            self.assertIn("missing_fastq_manifest", payload["detail"])

    def test_selected_download_rejects_nonempty_output_without_resume(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source.fastq.gz"
            data = b"@r1\nACGT\n+\n!!!!\n"
            source.write_bytes(data)
            out_dir = root / "out"
            out_dir.mkdir()
            (out_dir / "existing.txt").write_text("existing", encoding="utf-8")
            input_json = root / "payload.json"
            input_json.write_text(
                json.dumps(
                    {
                        "input_text": "GSE000001",
                        "primary_accession": "GSE000001",
                        "fastq_files": [
                            {
                                "source_accession": "GSE000001",
                                "query_accession": "SRP000001",
                                "run_accession": "SRR000001",
                                "file_index": 1,
                                "file_name": "source.fastq.gz",
                                "url": source.as_uri(),
                                "expected_md5": hashlib.md5(data).hexdigest(),
                                "size_bytes": len(data),
                            }
                        ],
                        "supplementary_files": [],
                    }
                ),
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
                    str(out_dir),
                ],
                "resume_required",
            )

            self.assertIn(str(out_dir.resolve()), payload["detail"])
            self.assertEqual(payload["existing_output_nonempty"], True)
            self.assertEqual(payload["output_dir"], str(out_dir.resolve()))

    def test_selected_download_rejects_insufficient_space_with_structured_error(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source.fastq.gz"
            data = b"@r1\nACGT\n+\n!!!!\n"
            source.write_bytes(data)
            input_json = root / "payload.json"
            input_json.write_text(
                json.dumps(
                    {
                        "input_text": "GSE000001",
                        "primary_accession": "GSE000001",
                        "fastq_files": [
                            {
                                "source_accession": "GSE000001",
                                "query_accession": "SRP000001",
                                "run_accession": "SRR000001",
                                "file_index": 1,
                                "file_name": "source.fastq.gz",
                                "url": source.as_uri(),
                                "expected_md5": hashlib.md5(data).hexdigest(),
                                "size_bytes": len(data),
                            }
                        ],
                        "supplementary_files": [],
                    }
                ),
                encoding="utf-8",
            )

            with mock.patch("geo_getter.planner.shutil.disk_usage", return_value=mock.Mock(free=len(data) - 1)):
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
                    "insufficient_space",
                )

            self.assertEqual(payload["detail"], f"required_bytes={len(data)} available_bytes={len(data) - 1}")
            self.assertEqual(payload["required_bytes"], len(data))
            self.assertEqual(payload["available_bytes"], len(data) - 1)
            self.assertIn("The output folder does not have enough free space", payload["message"])
            self.assertNotIn(" B", payload["detail"])
            self.assertNotIn(" B", payload["message"])

    def test_selected_download_passes_download_workers_to_fastq_plan(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source.fastq.gz"
            data = b"@r1\nACGT\n+\n!!!!\n"
            source.write_bytes(data)
            input_json = root / "payload.json"
            input_json.write_text(
                json.dumps(
                    {
                        "input_text": "GSE000001",
                        "primary_accession": "GSE000001",
                        "fastq_files": [
                            {
                                "source_accession": "GSE000001",
                                "query_accession": "SRP000001",
                                "run_accession": "SRR000001",
                                "file_index": 1,
                                "file_name": "source.fastq.gz",
                                "url": source.as_uri(),
                                "expected_md5": hashlib.md5(data).hexdigest(),
                                "size_bytes": len(data),
                            }
                        ],
                        "supplementary_files": [],
                    }
                ),
                encoding="utf-8",
            )
            captured_workers = []

            def fake_download_plan(plan, **kwargs):
                captured_workers.append(kwargs["download_workers"])
                return [(plan.files[0], "md5_verified", "fixture")]

            with mock.patch("geo_getter.cli.download_plan", side_effect=fake_download_plan):
                exit_code, stdout = self.run_selected_download_json(
                    input_json,
                    "0",
                    "",
                    root / "out",
                    download_workers=4,
                )

            self.assertEqual(exit_code, 0)
            done = json.loads(stdout.splitlines()[-1])
            self.assertEqual(captured_workers, [4])
            self.assertEqual(done["download_workers"], 4)

    def test_selected_download_rejects_resume_when_manifest_is_missing(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source.fastq.gz"
            data = b"@r1\nACGT\n+\n!!!!\n"
            source.write_bytes(data)
            out_dir = root / "out"
            out_dir.mkdir()
            (out_dir / "existing.txt").write_text("existing", encoding="utf-8")
            input_json = root / "payload.json"
            input_json.write_text(
                json.dumps(
                    {
                        "input_text": "GSE000001",
                        "primary_accession": "GSE000001",
                        "fastq_files": [
                            {
                                "source_accession": "GSE000001",
                                "query_accession": "SRP000001",
                                "run_accession": "SRR000001",
                                "file_index": 1,
                                "file_name": "source.fastq.gz",
                                "url": source.as_uri(),
                                "expected_md5": hashlib.md5(data).hexdigest(),
                                "size_bytes": len(data),
                            }
                        ],
                        "supplementary_files": [],
                    }
                ),
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
                    str(out_dir),
                    "--resume-existing",
                ],
                "resume_artifact_mismatch",
            )

            self.assertIn("missing_fastq_manifest", payload["detail"])

    def test_selected_download_rejects_supplementary_in_nonempty_output(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "supplementary.txt"
            source.write_bytes(b"supplementary fixture\n")
            out_dir = root / "out"
            out_dir.mkdir()
            (out_dir / "existing.txt").write_text("existing", encoding="utf-8")
            input_json = root / "payload.json"
            input_json.write_text(
                json.dumps(
                    {
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
                ),
                encoding="utf-8",
            )

            self.assert_cli_error(
                [
                    "selected-download-json",
                    "--input-json",
                    str(input_json),
                    "--supp-indices",
                    "0",
                    "--out",
                    str(out_dir),
                    "--resume-existing",
                ],
                "resume_supplementary_unsupported",
            )

    def test_selected_download_resume_existing_fastq_folder(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source.fastq.gz"
            data = b"@r1\nACGT\n+\n!!!!\n"
            source.write_bytes(data)
            payload = {
                "input_text": "GSE000001",
                "primary_accession": "GSE000001",
                "fastq_files": [
                    {
                        "source_accession": "GSE000001",
                        "query_accession": "SRP000001",
                        "run_accession": "SRR000001",
                        "file_index": 1,
                        "file_name": "source.fastq.gz",
                        "url": source.as_uri(),
                        "expected_md5": hashlib.md5(data).hexdigest(),
                        "size_bytes": len(data),
                    }
                ],
                "supplementary_files": [],
            }
            input_json = root / "payload.json"
            input_json.write_text(json.dumps(payload), encoding="utf-8")
            out_dir = root / "out"

            exit_code, _stdout = self.run_selected_download_json(input_json, "0", "", out_dir)
            self.assertEqual(exit_code, 0)

            exit_code, stdout = self.run_selected_download_json(
                input_json,
                "0",
                "",
                out_dir,
                resume_existing=True,
            )

            self.assertEqual(exit_code, 0)
            done = json.loads(stdout.splitlines()[-1])
            self.assertEqual(done["statuses"], ["md5_verified"])
            self.assertEqual(done["output_dir"], str(out_dir.resolve()))
            self.assertEqual(done["resume_existing"], True)
            self.assertEqual(done["resume_required_bytes"], 0)

    def test_selected_download_resume_preserves_existing_fastq_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source.fastq.gz"
            data = b"@r1\nACGT\n+\n!!!!\n"
            source.write_bytes(data)
            payload = {
                "input_text": "GSE000001",
                "primary_accession": "GSE000001",
                "fastq_files": [
                    {
                        "source_accession": "GSE000001",
                        "query_accession": "SRP000001",
                        "run_accession": "SRR000001",
                        "file_index": 1,
                        "file_name": "source.fastq.gz",
                        "url": source.as_uri(),
                        "expected_md5": hashlib.md5(data).hexdigest(),
                        "size_bytes": len(data),
                    }
                ],
                "supplementary_files": [],
            }
            input_json = root / "payload.json"
            input_json.write_text(json.dumps(payload), encoding="utf-8")
            out_dir = root / "out"

            exit_code, _stdout = self.run_selected_download_json(input_json, "0", "", out_dir)
            self.assertEqual(exit_code, 0)
            manifest = fastq_manifest_path(out_dir)
            with manifest.open("r", encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle, delimiter="\t"))
                fieldnames = list(rows[0].keys())
            fieldnames.append("operator_note")
            rows[0]["status"] = "previous_status"
            rows[0]["operator_note"] = "keep me"
            with manifest.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t", lineterminator="\n")
                writer.writeheader()
                writer.writerows(rows)
            original_manifest = manifest.read_text(encoding="utf-8-sig")

            exit_code, _stdout = self.run_selected_download_json(
                input_json,
                "0",
                "",
                out_dir,
                resume_existing=True,
            )
            self.assertEqual(exit_code, 0)

            self.assertEqual(manifest.read_text(encoding="utf-8-sig"), original_manifest)

    def test_selected_download_resumes_fastq_only_from_mixed_history(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            fastq_source = root / "source.fastq.gz"
            fastq_data = b"@r1\nACGT\n+\n!!!!\n"
            fastq_source.write_bytes(fastq_data)
            supp_source = root / "supplementary.txt"
            supp_source.write_text("supplementary fixture\n", encoding="utf-8")
            payload = {
                "input_text": "GSE000001",
                "primary_accession": "GSE000001",
                "fastq_files": [
                    {
                        "source_accession": "GSE000001",
                        "query_accession": "SRP000001",
                        "run_accession": "SRR000001",
                        "file_index": 1,
                        "file_name": "source.fastq.gz",
                        "url": fastq_source.as_uri(),
                        "expected_md5": hashlib.md5(fastq_data).hexdigest(),
                        "size_bytes": len(fastq_data),
                    }
                ],
                "supplementary_files": [
                    {
                        "source_accession": "GSE000001",
                        "scope": "GEO Series supplementary/processed",
                        "name": "supplementary.txt",
                        "url": supp_source.as_uri(),
                    }
                ],
            }
            input_json = root / "payload.json"
            input_json.write_text(json.dumps(payload), encoding="utf-8")
            out_dir = root / "out"

            exit_code, _stdout = self.run_selected_download_json(input_json, "0", "0", out_dir)
            self.assertEqual(exit_code, 0)
            self.assertIn("GEO_SUPPLEMENTARY", download_log_path(out_dir).read_text(encoding="utf-8-sig"))

            exit_code, stdout = self.run_selected_download_json(
                input_json,
                "0",
                "",
                out_dir,
                resume_existing=True,
            )

            self.assertEqual(exit_code, 0)
            done = json.loads(stdout.splitlines()[-1])
            self.assertEqual(done["statuses"], ["md5_verified"])
            self.assertEqual(done["resume_existing"], True)

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

            exit_code, _stdout = self.run_selected_download_json(input_json, "", "0", out_dir)
            self.assertEqual(exit_code, 0)
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

            exit_code, _stdout = self.run_selected_download_json(input_json, "", "0,1,2", out_dir)
            self.assertEqual(exit_code, 0)
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

            exit_code, stdout = self.run_selected_download_json(input_json, "0", "0", out_dir)

            self.assertEqual(exit_code, 0)
            done = json.loads(stdout.splitlines()[-1])
            self.assertEqual(done["statuses"], ["md5_verified", "download_complete"])
            self.assertEqual((out_dir / "same.fastq.gz").read_bytes(), fastq_data)
            self.assertEqual((out_dir / "same.2.fastq.gz").read_bytes(), supp_data)
            self.assertFalse((out_dir / "same.fastq.gz.existing").exists())
            self.assertEqual(verify_fastq_manifest(fastq_manifest_path(out_dir))["status_counts"], {"md5_verified": 1})

    def test_selected_download_disambiguates_supplementary_name_from_fastq_part(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            fastq_source = root / "fastq_source.fastq.gz"
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
                        "name": "same.fastq.gz.part",
                        "url": supp_source.as_uri(),
                    }
                ],
            }
            input_json = root / "payload.json"
            input_json.write_text(json.dumps(payload), encoding="utf-8")
            out_dir = root / "out"

            exit_code, _stdout = self.run_selected_download_json(input_json, "0", "0", out_dir)
            self.assertEqual(exit_code, 0)

            self.assertEqual((out_dir / "same.fastq.gz").read_bytes(), fastq_data)
            self.assertEqual((out_dir / "same.fastq.gz.2.part").read_bytes(), supp_data)
            self.assertFalse((out_dir / "same.fastq.gz.part").exists())
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

            exit_code, _stdout = self.run_selected_download_json(input_json, "", "0", out_dir)
            self.assertEqual(exit_code, 0)

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

            exit_code, _stdout = self.run_selected_download_json(input_json, "0", "0", out_dir)
            self.assertEqual(exit_code, 0)

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
                raise DownloadNetworkError("fixture transfer failure")

            with mock.patch("geo_getter.downloader.download_url_to_part", side_effect=fail_with_part):
                exit_code, stdout = self.run_selected_download_json(input_json, "", "0", out_dir)

            self.assertEqual(exit_code, 1)
            done = json.loads(stdout.splitlines()[-1])
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
            exit_code, stdout = self.run_selected_download_json(input_json, "0", "", root / "out")

            output = stdout
            self.assertEqual(exit_code, 1)
            self.assertIn('"event": "done"', output)
            self.assertNotIn('"event": "error"', output)
            self.assertIn('"network_failed"', output)
            run_dir = root / "out"
            log_text = download_log_path(run_dir).read_text(encoding="utf-8")
            self.assertIn("network_failed", log_text)
            self.assertIn("unknown url type", log_text)

    def test_selected_download_does_not_retry_permanent_supplementary_http_error(self):
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
                        "name": "missing.txt",
                        "url": "https://example.invalid/missing.txt",
                    }
                ],
            }
            input_json = root / "input.json"
            input_json.write_text(json.dumps(payload), encoding="utf-8")
            out_dir = root / "out"
            calls = 0

            def fail_404(_request, timeout):
                nonlocal calls
                calls += 1
                raise http_error(404, "https://example.invalid/missing.txt")

            with (
                mock.patch("geo_getter.downloader.urllib.request.urlopen", side_effect=fail_404),
                mock.patch("geo_getter.downloader.time.sleep", side_effect=AssertionError("unexpected sleep")),
            ):
                exit_code, stdout = self.run_selected_download_json(input_json, "", "0", out_dir)

            output = stdout
            self.assertEqual(exit_code, 1)
            self.assertEqual(calls, 1)
            self.assertIn('"event": "done"', output)
            self.assertIn('"network_failed"', output)
            log_text = download_log_path(out_dir).read_text(encoding="utf-8")
            self.assertIn("network_failed", log_text)
            self.assertIn("HTTP Error 404", log_text)

    def test_selected_download_reports_local_io_failure_in_done_event(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source.fastq.gz"
            data = b"@r1\nACGT\n+\n!!!!\n"
            source.write_bytes(data)
            payload = {
                "input_text": "GSE000004",
                "primary_accession": "GSE000004",
                "fastq_files": [
                    {
                        "source_accession": "GSE000004",
                        "query_accession": "SRP000004",
                        "run_accession": "SRR000004",
                        "file_index": 1,
                        "file_name": "source.fastq.gz",
                        "url": source.as_uri(),
                        "expected_md5": hashlib.md5(data).hexdigest(),
                        "size_bytes": len(data),
                    }
                ],
                "supplementary_files": [],
            }
            input_json = root / "payload.json"
            input_json.write_text(json.dumps(payload), encoding="utf-8")
            original_replace = Path.replace

            def fail_part_replace(path, target):
                if path.name.endswith(".part"):
                    raise OSError("fixture replace failure")
                return original_replace(path, target)

            with mock.patch.object(Path, "replace", fail_part_replace):
                exit_code, stdout = self.run_selected_download_json(input_json, "0", "", root / "out")

            output = stdout
            self.assertEqual(exit_code, 1)
            done = json.loads(output.splitlines()[-1])
            self.assertEqual(done["statuses"], ["local_io_failed"])
            with download_log_path(root / "out").open("r", encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle, delimiter="\t"))
            self.assertEqual(rows[-1]["status"], "local_io_failed")
            self.assertIn("Could not move partial download into place", rows[-1]["message"])

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

    def test_internal_manifest_verification_bridge_returns_failure_for_unverified_md5(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            data = b"unverified\n"
            fastq_path = root / "unverified.fastq.gz"
            fastq_path.write_bytes(data)
            manifest = root / "sample_fastq_manifest.tsv"
            manifest.write_text(
                "\n".join(
                    [
                        "source_accession\tquery_accession\trun_accession\tfile_index\tfile_name\turl\texpected_md5\tsize_bytes\tlocal_path\tstatus",
                        f"GSE\tSRP\tRUN1\t1\tunverified.fastq.gz\thttps://example.invalid/unverified\t\t{len(data)}\t{fastq_path}\tplanned",
                    ]
                ),
                encoding="utf-8-sig",
            )

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                self.assertEqual(main(["verify-manifest-json", "--manifest", str(manifest)]), 1)
            event = json.loads(stdout.getvalue())
            self.assertEqual(event["status_counts"], {"md5_unavailable": 1})

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

    def test_selected_download_without_md5_emits_done_and_returns_zero(self):
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

            exit_code, stdout = self.run_selected_download_json(input_json, "0", "", out_dir)

            output = stdout
            self.assertEqual(exit_code, 0)
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
            with download_log_path(out_dir).open("r", encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle, delimiter="\t"))
            self.assertEqual(rows[-1]["status"], "md5_unavailable")
            self.assertEqual(rows[-1]["actual_md5"], "")


if __name__ == "__main__":
    unittest.main()
