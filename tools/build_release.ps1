[CmdletBinding()]
param(
    [string]$PythonVersion = "3.14.5",
    [ValidateSet("x64")]
    [string]$Architecture = "x64",
    [switch]$SkipDownload,
    [switch]$BuildInstaller
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$DistRoot = Join-Path $RepoRoot "dist"
$BuildRoot = Join-Path $RepoRoot "build"
$PythonCacheRoot = Join-Path $BuildRoot "python"
$InstallerScript = Join-Path $RepoRoot "installer\GEOGetter.iss"
$ChecksumPath = Join-Path $DistRoot "SHA256SUMS.txt"

function Get-AppVersion {
    $init = Join-Path $RepoRoot "geo_getter\__init__.py"
    $match = Select-String -Path $init -Pattern '^__version__\s*=\s*"([^"]+)"' | Select-Object -First 1
    if (-not $match) {
        throw "Could not read source version from geo_getter.__version__."
    }
    return $match.Matches[0].Groups[1].Value
}

function Assert-Inside {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Base
    )
    $fullPath = [System.IO.Path]::GetFullPath($Path).TrimEnd([System.IO.Path]::DirectorySeparatorChar, [System.IO.Path]::AltDirectorySeparatorChar)
    $fullBase = [System.IO.Path]::GetFullPath($Base).TrimEnd([System.IO.Path]::DirectorySeparatorChar, [System.IO.Path]::AltDirectorySeparatorChar)
    $fullBaseWithSeparator = $fullBase + [System.IO.Path]::DirectorySeparatorChar
    if (
        -not $fullPath.Equals($fullBase, [System.StringComparison]::OrdinalIgnoreCase) -and
        -not $fullPath.StartsWith($fullBaseWithSeparator, [System.StringComparison]::OrdinalIgnoreCase)
    ) {
        throw "Refusing to operate outside $fullBase`: $fullPath"
    }
}

function Reset-Directory {
    param([Parameter(Mandatory = $true)][string]$Path)
    Assert-Inside -Path $Path -Base $RepoRoot
    if (Test-Path $Path) {
        Remove-Item -LiteralPath $Path -Recurse -Force
    }
    New-Item -ItemType Directory -Path $Path -Force | Out-Null
}

function Copy-FileToPayload {
    param(
        [Parameter(Mandatory = $true)][string]$RelativePath,
        [Parameter(Mandatory = $true)][string]$PayloadDir
    )
    $source = Join-Path $RepoRoot $RelativePath
    $dest = Join-Path $PayloadDir $RelativePath
    New-Item -ItemType Directory -Path (Split-Path -Parent $dest) -Force | Out-Null
    Copy-Item -LiteralPath $source -Destination $dest -Force
}

function Copy-DirectoryToPayload {
    param(
        [Parameter(Mandatory = $true)][string]$RelativePath,
        [Parameter(Mandatory = $true)][string]$PayloadDir
    )
    $source = Join-Path $RepoRoot $RelativePath
    $dest = Join-Path $PayloadDir $RelativePath
    if (Test-Path $dest) {
        Remove-Item -LiteralPath $dest -Recurse -Force
    }
    Copy-Item -LiteralPath $source -Destination $dest -Recurse -Force
    Get-ChildItem -Path $dest -Directory -Recurse -Force -Filter "__pycache__" | Remove-Item -Recurse -Force
    Get-ChildItem -Path $dest -File -Recurse -Force -Include "*.pyc", "*.pyo" | Remove-Item -Force
}

function Remove-PythonCaches {
    param([Parameter(Mandatory = $true)][string]$Path)
    Get-ChildItem -Path $Path -Directory -Recurse -Force -Filter "__pycache__" | Remove-Item -Recurse -Force
    Get-ChildItem -Path $Path -File -Recurse -Force -Include "*.pyc", "*.pyo" | Remove-Item -Force
}

function New-LicenseBundle {
    param([Parameter(Mandatory = $true)][string]$PayloadDir)

    $bundlePath = Join-Path $PayloadDir "LICENSE-BUNDLE.txt"
    $licensePath = Join-Path $PayloadDir "LICENSE"
    $noticePath = Join-Path $PayloadDir "THIRD_PARTY_NOTICES.txt"
    $pythonLicensePath = Join-Path $PayloadDir "licenses\PYTHON-LICENSE.txt"

    foreach ($requiredPath in @($licensePath, $noticePath, $pythonLicensePath)) {
        if (-not (Test-Path $requiredPath)) {
            throw "License bundle input not found: $requiredPath"
        }
    }

    $content = @(
        "GEOGetter bundled release license notices",
        "",
        "== GEOGetter MIT License ==",
        "",
        (Get-Content -Raw -Encoding UTF8 $licensePath).TrimEnd(),
        "",
        "== Third-party notices ==",
        "",
        (Get-Content -Raw -Encoding UTF8 $noticePath).TrimEnd(),
        "",
        "== CPython license ==",
        "",
        (Get-Content -Raw -Encoding UTF8 $pythonLicensePath).TrimEnd(),
        ""
    ) -join [Environment]::NewLine

    Set-Content -Encoding UTF8 -Path $bundlePath -Value $content
}

function Get-PythonEmbedArchiveName {
    param([string]$Version, [string]$Arch)
    if ($Arch -ne "x64") {
        throw "Only x64 is supported for this release script."
    }
    return "python-$Version-embed-amd64.zip"
}

function Get-PythonEmbedArchiveSha256 {
    param([string]$Version, [string]$Arch)
    $knownHashes = @{
        "3.14.5|x64" = "ba6bd811c4eedb19195cf275770ef127e893d63701e24152606e2cb76f6d876a"
    }
    $key = "$Version|$Arch"
    if (-not $knownHashes.ContainsKey($key)) {
        throw "No trusted SHA256 is configured for CPython embeddable package $Version ($Arch)."
    }
    return $knownHashes[$key]
}

function Assert-FileSha256 {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$ExpectedSha256
    )
    if (-not (Test-Path $Path)) {
        throw "Cannot verify checksum because file was not found: $Path"
    }
    $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash.ToLowerInvariant()
    $expected = $ExpectedSha256.ToLowerInvariant()
    if ($actual -ne $expected) {
        throw "SHA256 mismatch for $Path. Expected $expected, got $actual."
    }
}

function Write-Sha256Sums {
    param([Parameter(Mandatory = $true)][string[]]$Paths)
    $lines = @()
    foreach ($path in $Paths) {
        if (-not (Test-Path -LiteralPath $path)) {
            continue
        }
        $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash.ToLowerInvariant()
        $name = Split-Path -Leaf $path
        $lines += "$hash  $name"
    }
    if ($lines.Count -eq 0) {
        throw "No release artifacts were available for SHA256SUMS.txt."
    }
    Set-Content -Encoding ASCII -Path $ChecksumPath -Value ($lines -join [Environment]::NewLine)
    Write-Host "Created checksum manifest: $ChecksumPath"
}

function Ensure-PythonRuntime {
    param(
        [Parameter(Mandatory = $true)][string]$Version,
        [Parameter(Mandatory = $true)][string]$Arch,
        [Parameter(Mandatory = $true)][string]$PayloadDir
    )

    $archiveName = Get-PythonEmbedArchiveName -Version $Version -Arch $Arch
    $archivePath = Join-Path $PythonCacheRoot $archiveName
    $runtimeDir = Join-Path $PayloadDir "runtime\python"
    New-Item -ItemType Directory -Path $PythonCacheRoot -Force | Out-Null
    New-Item -ItemType Directory -Path $runtimeDir -Force | Out-Null

    if (-not (Test-Path $archivePath)) {
        if ($SkipDownload) {
            throw "Python embeddable archive not found: $archivePath"
        }
        $uri = "https://www.python.org/ftp/python/$Version/$archiveName"
        Write-Host "Downloading $uri"
        Invoke-WebRequest -Uri $uri -OutFile $archivePath
    }

    $expectedSha256 = Get-PythonEmbedArchiveSha256 -Version $Version -Arch $Arch
    Assert-FileSha256 -Path $archivePath -ExpectedSha256 $expectedSha256
    Expand-Archive -LiteralPath $archivePath -DestinationPath $runtimeDir -Force

    $majorMinor = ($Version -split "\.")[0..1] -join ""
    $pthPath = Join-Path $runtimeDir "python$majorMinor._pth"
    if (-not (Test-Path $pthPath)) {
        $pthPath = Get-ChildItem -Path $runtimeDir -Filter "python*._pth" | Select-Object -First 1 -ExpandProperty FullName
    }
    if (-not $pthPath) {
        throw "Could not find Python ._pth file in $runtimeDir."
    }

    $pthLines = Get-Content -Encoding UTF8 $pthPath
    if ($pthLines -notcontains "..\..") {
        $pthLines = @($pthLines + "..\..")
        Set-Content -Encoding ASCII -Path $pthPath -Value $pthLines
    }

    $runtimeLicense = Join-Path $runtimeDir "LICENSE.txt"
    if (Test-Path $runtimeLicense) {
        $licenseDest = Join-Path $PayloadDir "licenses\PYTHON-LICENSE.txt"
        New-Item -ItemType Directory -Path (Split-Path -Parent $licenseDest) -Force | Out-Null
        Copy-Item -LiteralPath $runtimeLicense -Destination $licenseDest -Force
    }
}

function Get-IsccPath {
    $candidates = @()
    $command = Get-Command "ISCC.exe" -ErrorAction SilentlyContinue
    if ($command) {
        $candidates += $command.Source
    }
    if (${env:ProgramFiles(x86)}) {
        $candidates += (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe")
    }
    if ($env:ProgramFiles) {
        $candidates += (Join-Path $env:ProgramFiles "Inno Setup 6\ISCC.exe")
    }
    return $candidates | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
}

$version = Get-AppVersion
$packageName = "GEOGetter-v$version-win-$Architecture-portable"
$payloadDir = Join-Path $DistRoot $packageName
$zipPath = Join-Path $DistRoot "$packageName.zip"
$installerPath = Join-Path $DistRoot "GEOGetter-Setup-v$version.exe"

New-Item -ItemType Directory -Path $DistRoot -Force | Out-Null
Reset-Directory -Path $payloadDir

foreach ($file in @(
    "GEOGetter.ps1",
    "start_geo_getter.vbs",
    "start_geo_getter.bat",
    "README.md",
    "LICENSE",
    "THIRD_PARTY_NOTICES.txt",
    "pyproject.toml"
)) {
    Copy-FileToPayload -RelativePath $file -PayloadDir $payloadDir
}

foreach ($dir in @("geo_getter", "docs", "licenses", "resources")) {
    Copy-DirectoryToPayload -RelativePath $dir -PayloadDir $payloadDir
}

Ensure-PythonRuntime -Version $PythonVersion -Arch $Architecture -PayloadDir $payloadDir
New-LicenseBundle -PayloadDir $payloadDir

$payloadPython = Join-Path $payloadDir "runtime\python\python.exe"
& $payloadPython -m geo_getter.cli --help | Out-Host
if ($LASTEXITCODE -ne 0) {
    throw "Bundled Python CLI smoke test failed."
}

$payloadScript = Join-Path $payloadDir "GEOGetter.ps1"
powershell -NoProfile -ExecutionPolicy Bypass -File $payloadScript -SelfTest
if ($LASTEXITCODE -ne 0) {
    throw "Portable payload self-test failed."
}
Remove-PythonCaches -Path $payloadDir

if (Test-Path $zipPath) {
    Remove-Item -LiteralPath $zipPath -Force
}
Compress-Archive -Path (Join-Path $payloadDir "*") -DestinationPath $zipPath -Force

Write-Host "Created portable zip: $zipPath"

if ($BuildInstaller) {
    $iscc = Get-IsccPath
    if (-not $iscc) {
        throw "ISCC.exe was not found. Install Inno Setup 6 or add ISCC.exe to PATH, then rerun with -BuildInstaller."
    }
    if (-not (Test-Path $InstallerScript)) {
        throw "Installer script not found: $InstallerScript"
    }
    if (Test-Path $installerPath) {
        Remove-Item -LiteralPath $installerPath -Force
    }
    & $iscc "/DAppVersion=$version" "/DSourceDir=$payloadDir" "/DOutputDir=$DistRoot" $InstallerScript
    if ($LASTEXITCODE -ne 0) {
        throw "Inno Setup build failed."
    }
    if (-not (Test-Path $installerPath)) {
        throw "Expected installer was not created: $installerPath"
    }
    Write-Host "Created installer: $installerPath"
}

Write-Sha256Sums -Paths @($installerPath, $zipPath)
