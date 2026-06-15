---
layout: default
lang: ja
title: GEO Getter
description: GEO Getter は、公開 GEO / ENA データに紐づく raw FASTQ ファイルと、GEO レコードで配布されている supplementary / processed ファイルのダウンロード支援ツールです。
permalink: /ja/
---

# GEO Getter

公開 GEO / ENA データに紐づく raw FASTQ ファイルと、GEO レコードで配布されている supplementary / processed ファイルのダウンロード支援ツールです。

[ダウンロード](https://github.com/K81ta/geo-getter/releases/latest) |
[GitHub](https://github.com/K81ta/geo-getter) |
[English](../) |
[アーキテクチャ](../architecture/) |
[データの流れ](../data-flow/)

## 対象データ

- 公開 SRA / ENA / DRA アクセッションに紐づく raw FASTQ ファイル
- GEO レコードで配布されている supplementary / processed ファイル

## インストール

最新版は [GitHub Releases](https://github.com/K81ta/geo-getter/releases/latest) からダウンロードします。

- 通常は `.exe` インストーラーを使います。インストール後は、スタートメニューから GEO Getter を起動します。
- インストールせずに使う場合は `win-x64-portable.zip` を展開し、`start_geo_getter.vbs` を実行します。
- 必要な環境: Windows 10 / 11 64-bit、インターネット接続、取得するファイルに十分な空き容量。
- インストーラーは未署名のため、Windows SmartScreen の警告が表示される場合があります。

## 基本操作

1. GEO Getter を起動します。
2. 対応するアクセッションまたは GEO ページ URL を貼り付けます。
3. `ファイルを検索` を押します。
4. ダウンロード対象の raw FASTQ ファイルまたは GEO supplementary / processed ファイルを選びます。
5. 出力先を確認します。初期設定では、`Downloads\GEOGetter` の下にアクセッションごとのフォルダを作成します。
6. `選択ファイルをダウンロード` を押します。

## 対応入力

| 入力 | 例 |
| --- | --- |
| GEO アクセッション | `GSE52778`, `GSM...` |
| GEO ページ URL | `https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE52778` |
| SRA / ENA / DRA アクセッション | `SRP...`, `SRX...`, `SRR...`, `SRS...`, `ERP...`, `ERX...`, `ERR...`, `ERS...`, `DRP...`, `DRX...`, `DRR...`, `DRS...` |
| BioProject アクセッション | `PRJNA...`, `PRJEB...`, `PRJDB...` |
| BioSample アクセッション | `SAMN...`, `SAMEA...`, `SAMD...` |

## 出力

選択したファイルと TSV は、アプリに表示されるアクセッションフォルダに出力されます。

```text
Downloads/GEOGetter/
  GSE52778/
    GSE52778_fastq_manifest.tsv
    GSE52778_supplementary_manifest.tsv
    GSE52778_download_log.tsv
    SRR1039508_1.fastq.gz
```

- `*_fastq_manifest.tsv`: FASTQ の取得元 URL、ENA から取得した MD5、想定サイズ、保存先。
- `*_supplementary_manifest.tsv`: supplementary / processed ファイルの URL と保存先。
- `*_download_log.tsv`: 選択したファイルごとのダウンロード結果。
- `verification_report.tsv`: 保存済み FASTQ を後から検証した場合のレポート。

## チェックと再開

- FASTQ は、ENA が MD5 を返す場合に保存後の MD5 と照合します。
- GEO supplementary / processed ファイルは GEO の配布 URL から取得します。ENA FASTQ 用の MD5 では照合しません。
- 保存済み FASTQ を後から確認する場合は、`ツール > 保存済みFASTQを確認` で `*_fastq_manifest.tsv` を選びます。`verification_report.tsv` は同じフォルダに出力されます。
- 中断した FASTQ ダウンロードは、同じ保存先を選ぶと再開できます。ただし、そのフォルダ内の FASTQ マニフェストとダウンロードログが今回の FASTQ 選択と一致する場合に限ります。途中までのファイルは `.part` として残ります。
