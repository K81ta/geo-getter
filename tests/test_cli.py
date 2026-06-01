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


if __name__ == "__main__":
    unittest.main()
