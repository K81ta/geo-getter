---
layout: default
title: GEO Getter
description: GEO Getter helps download raw FASTQ files linked to public GEO / ENA records, plus supplementary and processed files distributed through GEO.
permalink: /
---

# GEO Getter

Download raw FASTQ files linked to public GEO / ENA records, plus supplementary and processed files distributed through GEO.

[Download](https://github.com/K81ta/geo-getter/releases/latest) |
[GitHub](https://github.com/K81ta/geo-getter) |
[Japanese](ja/) |
[Architecture](architecture/) |
[Data flow](data-flow/)

## Data types

- Raw FASTQ files associated with public SRA / ENA / DRA accessions.
- Supplementary and processed files distributed through GEO records.

## Install

Download the latest GEO Getter release from [GitHub Releases](https://github.com/K81ta/geo-getter/releases/latest).

- For most users, download the `.exe` installer and start GEO Getter from the Start menu.
- For a no-install setup, download the `win-x64-portable.zip` archive, extract it, and run `start_geo_getter.vbs`.
- Requirements: Windows 10 or 11, 64-bit; internet access; and enough free disk space for the files you choose.
- The installer is unsigned, so Windows SmartScreen may display a warning.

## Quick start

1. Start GEO Getter.
2. Paste a supported accession or GEO page URL.
3. Select `Find files`.
4. Select the raw FASTQ, supplementary, or processed files to download.
5. Confirm the output folder. By default, GEO Getter creates an accession folder under `Downloads\GEOGetter`.
6. Select `Download selected files`.

## Supported input

| Input | Examples |
| --- | --- |
| GEO accession | `GSE52778`, `GSM...` |
| GEO page URL | `https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE52778` |
| SRA / ENA / DRA accession | `SRP...`, `SRX...`, `SRR...`, `SRS...`, `ERP...`, `ERX...`, `ERR...`, `ERS...`, `DRP...`, `DRX...`, `DRR...`, `DRS...` |
| BioProject accession | `PRJNA...`, `PRJEB...`, `PRJDB...` |
| BioSample accession | `SAMN...`, `SAMEA...`, `SAMD...` |

## Output folder

Downloaded files, TSV manifests, and logs are written to the accession folder shown in the app.

```text
Downloads/GEOGetter/
  GSE52778/
    GSE52778_fastq_manifest.tsv
    GSE52778_supplementary_manifest.tsv
    GSE52778_download_log.tsv
    SRR1039508_1.fastq.gz
```

- `*_fastq_manifest.tsv`: FASTQ source URL, ENA MD5 when available, expected size, and output path.
- `*_supplementary_manifest.tsv`: GEO supplementary or processed file URL and output path.
- `*_download_log.tsv`: download result for each selected file.
- `verification_report.tsv`: report produced by a later FASTQ verification run.

## Verification and resume

- FASTQ files are checked against ENA MD5 values when ENA provides them.
- GEO supplementary and processed files are downloaded from GEO URLs; they are not checked against ENA FASTQ MD5 values.
- To verify downloaded FASTQ files later, choose `Tools > Verify saved FASTQ` and select a `*_fastq_manifest.tsv` file. GEO Getter writes `verification_report.tsv` in the same folder.
- To resume an interrupted FASTQ download, choose the same save folder. Resume works only when the existing FASTQ manifest and download log in that folder match the current FASTQ selection. Incomplete files remain as `.part` files.
