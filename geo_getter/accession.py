from __future__ import annotations

import re
from dataclasses import dataclass


ACCESSION_PATTERN = re.compile(
    r"\b("
    r"GSE\d+|GSM\d+|SRP\d+|SRX\d+|SRR\d+|SRS\d+|"
    r"ERP\d+|ERX\d+|ERR\d+|ERS\d+|"
    r"DRP\d+|DRX\d+|DRR\d+|DRS\d+|"
    r"PRJNA\d+|PRJEB\d+|PRJDB\d+|"
    r"SAMN\d+|SAMEA\d+|SAMD\d+"
    r")\b",
    re.IGNORECASE,
)

GEO_PREFIXES = ("GSE", "GSM")
ENA_QUERY_PREFIXES = (
    "SRP",
    "SRX",
    "SRR",
    "SRS",
    "ERP",
    "ERX",
    "ERR",
    "ERS",
    "DRP",
    "DRX",
    "DRR",
    "DRS",
    "PRJNA",
    "PRJEB",
    "PRJDB",
    "SAMN",
    "SAMEA",
    "SAMD",
)


@dataclass(frozen=True)
class AccessionInput:
    raw_text: str
    accession: str
    prefix: str

    @property
    def is_geo(self) -> bool:
        return self.prefix in GEO_PREFIXES

    @property
    def is_ena_query(self) -> bool:
        return self.prefix in ENA_QUERY_PREFIXES


def extract_accession(text: str) -> AccessionInput:
    """Extract the first supported accession from free text or a GEO URL."""
    if not text or not text.strip():
        raise ValueError("Input is empty. Enter a GSE/GSM accession, GEO URL, or supported ENA/SRA/Project/BioSample accession.")
    match = ACCESSION_PATTERN.search(text)
    if not match:
        raise ValueError("Could not extract a supported accession from the input.")
    accession = match.group(1).upper()
    prefix = _prefix_for(accession)
    return AccessionInput(raw_text=text.strip(), accession=accession, prefix=prefix)


def find_supported_accessions(text: str) -> list[str]:
    seen: set[str] = set()
    values: list[str] = []
    for match in ACCESSION_PATTERN.finditer(text or ""):
        accession = match.group(1).upper()
        if accession not in seen:
            values.append(accession)
            seen.add(accession)
    return values


def _prefix_for(accession: str) -> str:
    for prefix in sorted(GEO_PREFIXES + ENA_QUERY_PREFIXES, key=len, reverse=True):
        if accession.startswith(prefix):
            return prefix
    return accession[:3]
