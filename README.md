# GEOGetter

GEOGetter は、GEO accession、GEO ページ URL、SRA / ENA / Project / BioSample 系 accession から、raw FASTQ と GEO supplementary / processed file を保存する Windows 用デスクトップアプリです。

## ダウンロードとインストール

最新版は [GitHub Releases](https://github.com/K81ta/geo-getter/releases/latest) からダウンロードします。

通常は `GEOGetter-Setup-v<version>.exe` をダウンロードして実行してください。インストール後は、スタートメニューの `GEOGetter` から起動できます。

インストールせずに試す場合や、管理者権限なしで展開して使いたい場合は、`GEOGetter-v<version>-win-x64-portable.zip` を展開し、展開したフォルダ内の `start_geo_getter.vbs` を実行してください。

インストーラー版と portable zip には実行に必要な Python runtime が含まれています。

このインストーラーは未署名です。Windows SmartScreen の警告が出る場合があります。

## 動作環境

- Windows x64

## 対応入力

- GEO accession: `GSE...`, `GSM...`
- GEO ページ URL
- SRA / ENA accession: `SRP...`, `SRX...`, `SRR...`, `SRS...`, `ERP...`, `ERX...`, `ERR...`, `ERS...`, `DRP...`, `DRX...`, `DRR...`, `DRS...`
- Project / BioSample accession: `PRJNA...`, `PRJEB...`, `PRJDB...`, `SAMN...`, `SAMEA...`, `SAMD...`

入力に複数の accession や URL が含まれる場合は、最初に見つかった対応 accession を使います。

## 使い方

1. スタートメニューから `GEOGetter` を起動します。
2. accession または GEO URL を入力します。
3. `ファイルを検索` を押します。
4. raw FASTQ または GEO supplementary / processed file から保存したいファイルを選びます。
5. 保存先を確認します。
6. `選択ファイルをダウンロード` を押します。

初期保存先は `Downloads\GEOGetter` です。ファイル検索後は、保存先欄に `Downloads\GEOGetter\<accession>` が表示されます。

## 入力例

- `GSE30567`
- `GSM758559`
- `SRR1039508`
- `PRJNA30709`
- `https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE163620`

## 保存先

保存先欄に表示されたフォルダに、manifest、download log、選択したファイルを保存します。別の場所に保存したい場合は、保存先欄で実際に使うフォルダを選びます。

```text
Downloads/GEOGetter/
  GSE52778/
    GSE52778_fastq_manifest.tsv
    GSE52778_supplementary_manifest.tsv
    GSE52778_download_log.tsv
    SRR1039508_1.fastq.gz
```

raw FASTQ を保存した場合は `*_fastq_manifest.tsv`、GEO supplementary / processed file を保存した場合は `*_supplementary_manifest.tsv` が作成されます。`*_download_log.tsv` にはファイルごとの保存結果が記録されます。

中断後に同じ保存フォルダを選ぶと、前回の FASTQ manifest / download log と今回の選択が一致する場合だけ再開できます。完成済み FASTQ は、期待サイズがある場合はサイズも確認したうえで、MD5 が一致する場合だけ再利用します。途中の `.part` は同じ保存ファイル名に対応する場合だけ再開に使います。

## 保存済み FASTQ の確認

保存済み FASTQ をあとから確認したい場合は、`ツール > 保存済みFASTQを確認` から保存フォルダ内の `*_fastq_manifest.tsv` を選びます。同じフォルダに `verification_report.tsv` が作成されます。

確認結果は `md5_verified`、`md5_unavailable`、`missing`、`size_mismatch`、`md5_mismatch` などの status で記録されます。

## 詳細

- [データの流れ](docs/DATA_FLOW.md): GEO、SOFT、SRA / ENA accession、FASTQ、supplementary / processed file の関係
- [アーキテクチャ](docs/ARCHITECTURE.md): GUI、内部 CLI、Python core、manifest、status、診断情報の構造

## 問題が起きた場合

検索やダウンロードが失敗した場合は、画面下部の `診断情報を保存` から診断 zip を保存してください。

診断 zip には、入力 accession、解決結果、warnings、GUI ログ、Python stdout / stderr、error code / detail、manifest、download log、保存先情報が含まれます。保存済み FASTQ の確認を実行した場合は、確認ログや `verification_report.tsv` も含まれることがあります。

診断 zip は自動送信されません。ローカルパスや accession が含まれるため、必要な場合だけ共有してください。

## ライセンス

GEOGetter は MIT License で公開しています。ライセンス本文は `LICENSE` を確認してください。
