import threading
import time
import unittest

from geo_getter.accession import ENA_QUERY_PREFIXES
from geo_getter.errors import GeoGetterError, URL_UNAVAILABLE
from geo_getter.models import DatasetMetadata, FastqFile, SupplementaryFile
from geo_getter.providers.geo import GeoSampleMetadata, GeoSoftParseResult
from geo_getter.providers.resolver import resolve_metadata


def fake_geo_related(accession):
    return GeoSoftParseResult(
        related_accessions=["SRP000001", "SRP000001"],
        supplementary_files=[
            SupplementaryFile(
                source_accession=accession,
                scope="GEO Series supplementary/processed",
                name="processed.txt",
                url="https://example.invalid/processed.txt",
            )
        ],
        sample_metadata_by_accession={
            "SRP000001": GeoSampleMetadata(
                geo_sample_accession="GSM000001",
                geo_sample_title="sample title",
            )
        },
        dataset_metadata=DatasetMetadata(
            accession=accession,
            title="dataset title",
            summary="dataset summary",
        ),
    )


def fake_ena_fastq(accession, source_accession):
    return [
        FastqFile(
            source_accession=source_accession,
            query_accession=accession,
            run_accession="SRR000001",
            file_index=1,
            file_name="a.fastq.gz",
            url="https://example.invalid/a.fastq.gz",
            expected_md5="1" * 32,
            size_bytes=10,
        )
    ]


def empty_geo_related(accession):
    return GeoSoftParseResult(
        related_accessions=[],
        supplementary_files=[],
        sample_metadata_by_accession={},
        dataset_metadata=DatasetMetadata(accession=accession),
    )


def multi_geo_related(related_accessions):
    def lookup(accession):
        return GeoSoftParseResult(
            related_accessions=related_accessions,
            supplementary_files=[],
            sample_metadata_by_accession={
                related_accession: GeoSampleMetadata(
                    geo_sample_accession=f"GSM{related_accession.removeprefix('SRP')}",
                    geo_sample_title=f"sample {related_accession}",
                )
                for related_accession in related_accessions
            },
            dataset_metadata=DatasetMetadata(accession=accession),
        )

    return lookup


def failing_geo_related(accession):
    raise AssertionError(f"GEO lookup should not be called for direct ENA input: {accession}")


def recording_ena_fastq(calls):
    def lookup(accession, source_accession):
        calls.append((accession, source_accession))
        return [
            FastqFile(
                source_accession=source_accession,
                query_accession=accession,
                run_accession="SRR000001",
                file_index=1,
                file_name="a.fastq.gz",
                url="https://example.invalid/a.fastq.gz",
                expected_md5="1" * 32,
                size_bytes=10,
            )
        ]

    return lookup


def parallel_recording_ena_fastq():
    state = {"active": 0, "max_active": 0}
    lock = threading.Lock()

    def lookup(accession, source_accession):
        with lock:
            state["active"] += 1
            state["max_active"] = max(state["max_active"], state["active"])
        try:
            if accession.endswith("000001"):
                time.sleep(0.05)
            else:
                time.sleep(0.01)
            return [_fastq(accession, source_accession)]
        finally:
            with lock:
                state["active"] -= 1

    return lookup, state


def partially_failing_ena_fastq(accession, source_accession):
    if accession == "SRP000002":
        raise GeoGetterError(URL_UNAVAILABLE, accession)
    return [_fastq(accession, source_accession)]


def always_failing_ena_fastq(accession, source_accession):
    raise GeoGetterError(URL_UNAVAILABLE, accession)


def duplicate_fastq_ena(accession, source_accession):
    return [
        FastqFile(
            source_accession=source_accession,
            query_accession=accession,
            run_accession="SRR000001",
            file_index=1,
            file_name="shared.fastq.gz",
            url="https://example.invalid/shared.fastq.gz",
            expected_md5="1" * 32,
            size_bytes=10,
        )
    ]


def _fastq(accession, source_accession):
    suffix = accession.removeprefix("SRP")
    return FastqFile(
        source_accession=source_accession,
        query_accession=accession,
        run_accession=f"SRR{suffix}",
        file_index=1,
        file_name=f"{accession}.fastq.gz",
        url=f"https://example.invalid/{accession}.fastq.gz",
        expected_md5="1" * 32,
        size_bytes=10,
    )


class ResolverTest(unittest.TestCase):
    def test_resolver_deduplicates_fastq_and_keeps_supplementary(self):
        result = resolve_metadata(
            "GSE000001",
            geo_related_lookup=fake_geo_related,
            ena_fastq_lookup=fake_ena_fastq,
        )
        self.assertEqual(result.primary_accession, "GSE000001")
        self.assertEqual(result.query_accessions, ["SRP000001"])
        self.assertEqual(len(result.fastq_files), 1)
        self.assertEqual(len(result.supplementary_files), 1)
        self.assertEqual(result.fastq_files[0].geo_sample_accession, "GSM000001")
        self.assertEqual(result.fastq_files[0].geo_sample_title, "sample title")
        self.assertEqual(result.dataset_metadata.title, "dataset title")

    def test_geo_without_related_accession_warns_fastq_absent(self):
        result = resolve_metadata(
            "GSE000002",
            geo_related_lookup=empty_geo_related,
            ena_fastq_lookup=fake_ena_fastq,
        )
        self.assertEqual(result.fastq_files, [])
        self.assertIn("No SRA", result.warnings[0])

    def test_size_unknown_fastq_adds_warning(self):
        def size_unknown_ena_fastq(accession, source_accession):
            return [
                FastqFile(
                    source_accession=source_accession,
                    query_accession=accession,
                    run_accession="SRR000001",
                    file_index=1,
                    file_name="a.fastq.gz",
                    url="https://example.invalid/a.fastq.gz",
                    expected_md5="",
                    size_bytes=0,
                )
            ]

        result = resolve_metadata(
            "GSE000003",
            geo_related_lookup=fake_geo_related,
            ena_fastq_lookup=size_unknown_ena_fastq,
        )
        self.assertTrue(any("file sizes were unavailable" in warning for warning in result.warnings))

    def test_direct_ena_accessions_skip_geo_and_query_ena_directly(self):
        for prefix in ENA_QUERY_PREFIXES:
            accession = f"{prefix}000001"
            with self.subTest(accession=accession):
                calls = []
                result = resolve_metadata(
                    accession,
                    geo_related_lookup=failing_geo_related,
                    ena_fastq_lookup=recording_ena_fastq(calls),
                )

                self.assertEqual(result.primary_accession, accession)
                self.assertEqual(result.query_accessions, [accession])
                self.assertEqual(calls, [(accession, accession)])
                self.assertEqual(len(result.fastq_files), 1)
                self.assertEqual(result.fastq_files[0].source_accession, accession)
                self.assertEqual(result.fastq_files[0].query_accession, accession)
                self.assertEqual(result.supplementary_files, [])

    def test_geo_related_accessions_are_queried_in_small_parallel_batches(self):
        accessions = [f"SRP{index:06d}" for index in range(1, 13)]
        ena_fastq_lookup, state = parallel_recording_ena_fastq()

        result = resolve_metadata(
            "GSE000004",
            geo_related_lookup=multi_geo_related(accessions),
            ena_fastq_lookup=ena_fastq_lookup,
        )

        self.assertGreater(state["max_active"], 1)
        self.assertLessEqual(state["max_active"], 4)
        self.assertEqual(result.query_accessions, accessions)
        self.assertEqual(
            [item.query_accession for item in result.fastq_files],
            accessions,
        )
        self.assertEqual(result.fastq_files[0].geo_sample_accession, "GSM000001")

    def test_geo_related_accession_partial_failure_returns_successes_with_warning(self):
        result = resolve_metadata(
            "GSE000005",
            geo_related_lookup=multi_geo_related(["SRP000001", "SRP000002", "SRP000003"]),
            ena_fastq_lookup=partially_failing_ena_fastq,
        )

        self.assertEqual(result.query_accessions, ["SRP000001", "SRP000002", "SRP000003"])
        self.assertEqual(
            [item.query_accession for item in result.fastq_files],
            ["SRP000001", "SRP000003"],
        )
        self.assertTrue(
            any("SRP000002" in warning and URL_UNAVAILABLE in warning for warning in result.warnings)
        )

    def test_direct_ena_accession_failure_still_raises(self):
        with self.assertRaises(GeoGetterError) as raised:
            resolve_metadata(
                "SRP000002",
                geo_related_lookup=failing_geo_related,
                ena_fastq_lookup=partially_failing_ena_fastq,
            )

        self.assertEqual(raised.exception.code, URL_UNAVAILABLE)
        self.assertEqual(raised.exception.detail, "SRP000002")

    def test_single_geo_related_accession_failure_still_raises(self):
        with self.assertRaises(GeoGetterError) as raised:
            resolve_metadata(
                "GSE000005",
                geo_related_lookup=multi_geo_related(["SRP000002"]),
                ena_fastq_lookup=partially_failing_ena_fastq,
            )

        self.assertEqual(raised.exception.code, URL_UNAVAILABLE)
        self.assertEqual(raised.exception.detail, "SRP000002")

    def test_all_geo_related_accession_failures_still_raise(self):
        with self.assertRaises(GeoGetterError) as raised:
            resolve_metadata(
                "GSE000005",
                geo_related_lookup=multi_geo_related(["SRP000001", "SRP000002"]),
                ena_fastq_lookup=always_failing_ena_fastq,
            )

        self.assertEqual(raised.exception.code, URL_UNAVAILABLE)
        self.assertEqual(raised.exception.detail, "SRP000001")

    def test_parallel_fastq_dedup_keeps_first_query_order(self):
        result = resolve_metadata(
            "GSE000006",
            geo_related_lookup=multi_geo_related(["SRP000001", "SRP000002"]),
            ena_fastq_lookup=duplicate_fastq_ena,
        )

        self.assertEqual(len(result.fastq_files), 1)
        self.assertEqual(result.fastq_files[0].query_accession, "SRP000001")


if __name__ == "__main__":
    unittest.main()
