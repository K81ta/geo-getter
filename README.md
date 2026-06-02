# GEOGetter

GEOGetter は、GEO accession、GEO ページ URL、SRA / ENA / Project / BioSample 系 accession から、raw FASTQ と GEO supplementary / processed file を保存する Windows 用デスクトップアプリです。

## インストール

最新版は [GitHub Releases](https://github.com/K81ta/geo-getter/releases/latest) からダウンロードします。

`GEOGetter-Setup-v<version>.exe` をダウンロードして実行してください。インストール後は、スタートメニューの `GEOGetter` から起動できます。

このインストーラーは未署名です。Windows SmartScreen の警告が出る場合があります。

## 動作環境

- Windows x64

## 使い方

1. スタートメニューから `GEOGetter` を起動します。
2. accession または GEO URL を入力します。
3. `ファイルを検索` を押します。
4. 保存したいファイルを選びます。
5. 保存先を確認します。
6. `選択ファイルをダウンロード` を押します。

初期保存先は `Downloads\GEOGetter` です。

## 入力例

- `GSE30567`
- `GSM758559`
- `SRR1039508`
- `PRJNA30709`
- `https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE163620`

## 保存先

選択した保存先フォルダの下に、accession 名のフォルダを作成します。

```text
Downloads/GEOGetter/
  GSE52778/
    GSE52778_fastq_manifest.tsv
    GSE52778_supplementary_manifest.tsv
    GSE52778_download_log.tsv
    SRR1039508_1.fastq.gz
```

## 問題が起きた場合

検索やダウンロードが失敗した場合は、画面下部の `診断情報を保存` から診断 zip を保存してください。

診断 zip には、入力 accession、解決結果、warnings、GUI ログ、Python stdout / stderr、manifest、download log、保存先情報が含まれます。ローカルパスや accession が含まれるため、自動送信はしません。必要な場合だけ共有してください。

## ライセンス

GEOGetter は MIT License で公開しています。ライセンス本文は `LICENSE` を確認してください。
