# Release

## 通常の作業手順

1. `main` が `origin/main` と同期済みで、作業ツリーが clean であることを確認する。
2. `codex/<topic>` などの feature branch を作成する。
3. 実装、ドキュメント更新、version 更新を feature branch 上で行う。
4. ローカルで次を確認する。

```powershell
python -m unittest discover -s tests -v
powershell -NoProfile -ExecutionPolicy Bypass -File .\GEOGetter.ps1 -SelfTest
```

5. feature branch を push し、通常 CI が成功することを確認する。
6. `main` に統合する。
7. `main` 上で release tag を作成して push する。

今回の `v0.1.1` 運用性強化作業では、Codex はローカル commit までで停止する。push、`main` 統合、tag 作成、Release 発行はユーザー確認後に行う。

## ローカルビルド

Windows x64 と Inno Setup 6 が必要です。

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\build_release.ps1 -BuildInstaller
```

成功すると `dist` に次の成果物が作られます。

```text
GEOGetter-Setup-v<version>.exe
GEOGetter-Setup-v<version>.exe.sha256
GEOGetter-v<version>-win-x64-portable.zip
GEOGetter-v<version>-win-x64-portable.zip.sha256
```

## GitHub Release

`pyproject.toml` と `geo_getter/__init__.py` の version を同じ番号にする。

`main` 上で version と同じ `v*` tag を作成して push します。

```powershell
git tag v0.1.1
git push origin v0.1.1
```

GitHub Actions の `Release` workflow が Windows runner でビルドし、GitHub Release に成果物を添付します。tag の version、`pyproject.toml` の version、`geo_getter.__version__` が一致しない場合、workflow は失敗します。

## Release 確認

Release workflow が成功したら、GitHub Release に次の4ファイルが添付されていることを確認する。

```text
GEOGetter-Setup-v<version>.exe
GEOGetter-Setup-v<version>.exe.sha256
GEOGetter-v<version>-win-x64-portable.zip
GEOGetter-v<version>-win-x64-portable.zip.sha256
```

`gh` を使える環境では次で確認できます。

```powershell
gh release view v0.1.1 --repo K81ta/geo-getter --json tagName,isDraft,isPrerelease,publishedAt,url,assets
```

## Release 本文

```text
v0.1.1 は公開後の不具合調査と変更検出を強化する更新です。

- 通常 CI を追加しました。
- 診断情報 zip を GUI から保存できるようにしました。
- release tag、pyproject.toml、geo_getter.__version__ の version 整合性確認を強化しました。

インストーラー版は GEOGetter-Setup-v0.1.1.exe、portable 版は GEOGetter-v0.1.1-win-x64-portable.zip をダウンロードしてください。
```
