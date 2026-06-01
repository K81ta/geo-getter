# Release

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

`pyproject.toml` の `version` と同じ番号で `v*` タグを作成して push します。

```powershell
git tag v0.1.0
git push origin v0.1.0
```

GitHub Actions の `Release` workflow が Windows runner でビルドし、GitHub Release に成果物を添付します。タグの version と `pyproject.toml` の version が一致しない場合、workflow は失敗します。

## Release本文

```text
Windows x64 用の初回リリースです。

GEOGetter-Setup-v0.1.0.exe をダウンロードして実行してください。
インストール後は、スタートメニューから GEOGetter を起動できます。

このインストーラーは未署名のため、Windows SmartScreen の警告が出る場合があります。
```
