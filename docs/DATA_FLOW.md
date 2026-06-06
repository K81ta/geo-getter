# データの流れ

GEOGetter は、入力された accession または GEO URL からファイル候補を作り、選択された raw FASTQ と GEO supplementary / processed file を保存する。

入力テキストに複数の accession や URL が含まれる場合は、最初に見つかった対応 accession を primary accession として扱う。

## 使うデータソース

| 名前 | 役割 |
| --- | --- |
| GEO | 研究、サンプル、supplementary / processed file の情報を持つ |
| GEO SOFT | GEO record をテキストで取得する形式 |
| SRA / ENA / Project / BioSample accession | raw sequencing data を ENA で探すための accession |
| ENA Portal API | raw FASTQ の URL、期待 MD5、ファイルサイズを返す |

## 入力からファイル候補まで

```mermaid
flowchart TD
    Input["GEO URL / GSE / GSM / SRR / SRX / SRP / Project / BioSample"] --> Accession["primary accession"]
    Accession --> GeoCheck{"GSE / GSM"}
    GeoCheck -->|yes| Soft["GEO SOFT"]
    Soft --> Metadata["dataset metadata"]
    Soft --> Relation["Series_relation / Sample_relation"]
    Relation --> Query["SRA / ENA / Project / BioSample accession"]
    Query --> Ena["ENA Portal API filereport"]
    GeoCheck -->|no| Ena
    Ena --> Fastq["raw FASTQ candidates\nfastq_ftp / fastq_md5 / fastq_bytes"]
    Soft --> SuppUrl["Series_supplementary_file / Sample_supplementary_file"]
    SuppUrl --> Supp["supplementary / processed file candidates"]
```

入力が `GSE`、`GSM`、GEO URL の場合は、まず GEO SOFT を取得する。GEO SOFT から dataset metadata、関連 accession、supplementary / processed file の URL を読む。

GEO SOFT の `Series_relation` と `Sample_relation` に `SRP`、`SRX`、`SRR`、`SRS`、`PRJNA`、`SAMN` などがあれば、それを ENA Portal API `filereport` に問い合わせる。ENA から `fastq_ftp`、`fastq_md5`、`fastq_bytes` が返ると raw FASTQ 候補になる。

入力が `SRR`、`SRX`、`SRP`、`SRS`、`ERR`、`ERX`、`ERP`、`ERS`、`DRR`、`DRX`、`DRP`、`DRS`、`PRJNA`、`PRJEB`、`PRJDB`、`SAMN`、`SAMEA`、`SAMD` などの場合は、GEO SOFT を取得せず、入力 accession を ENA Portal API `filereport` に問い合わせる。

GEO から関連 accession を見つけた場合も、SRA / ENA / Project / BioSample accession を直接入力した場合も、raw FASTQ 候補を作る最終経路は ENA Portal API `filereport` で同じである。公開状態、データ種別、登録内容によっては FASTQ が見つからないことがある。

## supplementary / processed file

GEO supplementary / processed file は GEO SOFT の `Series_supplementary_file` と `Sample_supplementary_file` から見つける。

supplementary / processed file は GEO 側の URL から保存する。raw FASTQ のように ENA `fastq_md5` を使った MD5 照合は行わない。

## 保存時に作成される情報

検索後、GUI の保存先欄には実際に保存する accession フォルダが表示される。選択したファイルは、保存先欄に表示されたフォルダ直下に保存する。

```text
Downloads/GEOGetter/
  GSE52778/
    GSE52778_fastq_manifest.tsv
    GSE52778_supplementary_manifest.tsv
    GSE52778_download_log.tsv
    SRR1039508_1.fastq.gz
```

raw FASTQ を選んだ場合は `*_fastq_manifest.tsv` が作成される。manifest には、元の GEO accession、ENA に問い合わせた accession、run accession、FASTQ URL、期待 MD5、期待サイズ、保存予定パスが入る。

GEO supplementary / processed file を選んだ場合は `*_supplementary_manifest.tsv` が作成される。manifest には、元の GEO accession、GEO SOFT 上の区分、URL、保存予定パスが入る。

`*_download_log.tsv` には、ファイルごとの保存結果が記録される。

保存済み FASTQ をあとから確認した場合は、manifest と同じフォルダに `verification_report.tsv` が作成される。

## 完全性確認の違い

| 種別 | URL の取得元 | MD5 照合 | 主な成功 status |
| --- | --- | --- | --- |
| raw FASTQ | ENA `fastq_ftp` | ENA `fastq_md5` がある場合に行う | `md5_verified` |
| 期待 MD5 なし raw FASTQ | ENA `fastq_ftp` | 期待 MD5 がないため行えない | `md5_unavailable` |
| supplementary / processed file | GEO SOFT の supplementary URL | 行わない | `download_complete` |

`md5_unavailable` は、ファイルは保存されたが MD5 で完全性を確認できない状態を表す。`download_complete` は、GEO supplementary / processed file を保存した状態を表す。

保存済み FASTQ の確認では、manifest に記録されたサイズと MD5 を使って確認する。期待 MD5 がない FASTQ は、ファイルが存在していても `md5_unavailable` になる。ファイルがない場合は `missing`、サイズが合わない場合は `size_mismatch`、MD5 が合わない場合は `md5_mismatch` になる。

## 途中ファイルと既存ファイル

ダウンロード中のファイルは `.part` として保存される。同じ保存パスに `.part` が残っている場合は、HTTP Range で再開を試みる。

完成済みの同名 FASTQ があり、期待 MD5 と一致する場合は再利用する。MD5 が合わない場合や、期待 MD5 がない既存 FASTQ は、正式ファイル名のまま使わず別名に退避して取り直す。

GEO supplementary / processed file は MD5 照合を行わない。同名ファイルが既にある場合は、既存ファイルを `.existing` などの名前に退避してから保存する。

## FASTQ が見つからない場合

GEO record に raw sequencing data への relation がない場合、または ENA Portal API から direct FASTQ が返らない場合、raw FASTQ 表は空になる。

この場合でも、GEO supplementary / processed file が SOFT text に載っていれば、supplementary 表には表示される。
