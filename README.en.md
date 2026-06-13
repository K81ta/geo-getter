# GEOGetter

[Japanese](README.md) | [English](README.en.md)

GEOGetter is a Windows desktop app for saving raw FASTQ files and GEO supplementary / processed files from public GEO and ENA data.

Enter a GEO accession, GEO page URL, SRA / ENA accession, BioProject accession, or BioSample accession, then choose the files to save from the displayed list. FASTQ files are checked during download when ENA provides expected MD5 values.

## Install and Start

### Requirements

- Windows 10 / 11 64-bit
- Internet connection
- Free disk space larger than the files you want to save

### Download

Download the latest version from [GitHub Releases](https://github.com/K81ta/geo-getter/releases/latest).

- Installer: `GEOGetter-Setup-v*.exe` in the latest release assets
- Portable zip: `GEOGetter-v*-win-x64-portable.zip` in the latest release assets

The installer is recommended for normal use. After installation, start `GEOGetter` from the Start menu.

To use GEOGetter without installation, extract the portable zip and run `start_geo_getter.vbs`.

Python is included in the distributed package.

The installer is unsigned, so Windows SmartScreen may show a warning.

## Save Files

### Input

Paste an accession or GEO page URL into the input field.

Supported accessions start with `GSE`, `GSM`, `SRP`, `SRX`, `SRR`, `SRS`, `ERP`, `ERX`, `ERR`, `ERS`, `DRP`, `DRX`, `DRR`, `DRS`, `PRJNA`, `PRJEB`, `PRJDB`, `SAMN`, `SAMEA`, or `SAMD`.

### Steps

1. Start `GEOGetter`.
2. Enter an accession or GEO page URL.
3. Press `Find files`.
4. Select the raw FASTQ or GEO supplementary / processed files to save.
5. Confirm the save folder.
6. Press `Download selected files`.

The default save location is `Downloads\GEOGetter`. After search, the save folder field shows a folder for the accession.

### Saved Files

The save folder contains the selected files and TSV records. The following example shows a saved `GSE52778` folder.

```text
Downloads/GEOGetter/
  GSE52778/
    GSE52778_fastq_manifest.tsv
    GSE52778_supplementary_manifest.tsv
    GSE52778_download_log.tsv
    SRR1039508_1.fastq.gz
```

- `GSE52778_fastq_manifest.tsv`: FASTQ URL, MD5 value from ENA, file size, and saved path
- `GSE52778_supplementary_manifest.tsv`: GEO supplementary / processed file URL and saved path
- `GSE52778_download_log.tsv`: Save result for each file

### Resume After Interruption

Choose the same save folder to resume an interrupted FASTQ download.

If the previous FASTQ manifest and download log do not match the current selection, GEOGetter will not resume in that folder.

Partially saved files remain as `.part` files.

## License

GEOGetter is released under the MIT License. See [LICENSE](LICENSE) for the full text.
