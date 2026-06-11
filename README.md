# GEOGetter

[Japanese](README.md) | [English](README.en.md)

GEOGetter は、GEO・ENA の公開データから raw FASTQ と GEO supplementary / processed file を保存する Windows デスクトップアプリです。

GEO accession、GEO ページ URL、SRA / ENA accession、BioProject accession、BioSample accession を入力し、表示されたリストから保存するファイルを選びます。FASTQ は保存時に MD5 を照合します。

## インストールして起動する

### 動作環境

- Windows 10 / 11 64-bit
- インターネット接続
- 保存するファイルより大きい空き容量

### ダウンロード

[GitHub Releases](https://github.com/K81ta/geo-getter/releases/latest) から最新版をダウンロードします。

- インストーラー: `GEOGetter-Setup-v0.1.4.exe`
- portable zip: `GEOGetter-v0.1.4-win-x64-portable.zip`

通常はインストーラーを使います。インストール後は、スタートメニューの `GEOGetter` から起動できます。

インストールせずに使う場合は、portable zip を展開し、`start_geo_getter.vbs` を実行します。

Python は配布物に含まれています。

インストーラーは署名されていないため、Windows SmartScreen の警告が表示される場合があります。

## ファイルを保存する

### 入力

入力欄には accession または GEO ページ URL を貼り付けます。

対応する accession は、`GSE`、`GSM`、`SRP`、`SRX`、`SRR`、`SRS`、`ERP`、`ERX`、`ERR`、`ERS`、`DRP`、`DRX`、`DRR`、`DRS`、`PRJNA`、`PRJEB`、`PRJDB`、`SAMN`、`SAMEA`、`SAMD` で始まるものです。

### 操作

1. `GEOGetter` を起動します。
2. accession または GEO ページ URL を入力します。
3. `ファイルを検索` を押します。
4. 保存する raw FASTQ または GEO supplementary / processed file を選びます。
5. 保存先を確認します。
6. `選択ファイルをダウンロード` を押します。

初期保存先は `Downloads\GEOGetter` です。検索後、保存先欄には accession ごとの保存フォルダが表示されます。

### 保存されるもの

保存フォルダには、選択したファイルと記録用 TSV が保存されます。次は `GSE52778` を保存した場合の例です。

```text
Downloads/GEOGetter/
  GSE52778/
    GSE52778_fastq_manifest.tsv
    GSE52778_supplementary_manifest.tsv
    GSE52778_download_log.tsv
    SRR1039508_1.fastq.gz
```

- `GSE52778_fastq_manifest.tsv`: FASTQ の URL、ENA から取得した MD5、ファイルサイズ、保存先パス
- `GSE52778_supplementary_manifest.tsv`: GEO supplementary / processed file の URL、保存先パス
- `GSE52778_download_log.tsv`: ファイルごとの保存結果

### 中断後の再開

同じ保存フォルダを選ぶと、中断した FASTQ ダウンロードを再開できます。

前回の FASTQ manifest とダウンロード記録が今回の選択と一致しない場合、GEOGetter はそのフォルダで再開しません。

途中まで保存されたファイルは `.part` として残ります。

## ライセンス

MIT License です。本文は [LICENSE](LICENSE) を確認してください。
