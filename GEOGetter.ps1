param(
    [switch]$SmokeTest,
    [switch]$SelfTest,
    [string]$ResolveSmokeInput = "",
    [string]$ScreenshotPath = "",
    [ValidateSet("ja", "en")]
    [string]$UiLanguage = "ja"
)

$ErrorActionPreference = "Stop"
$AppRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$BundledPythonExe = Join-Path (Join-Path $AppRoot "runtime\python") "python.exe"
$PythonExe = if (Test-Path $BundledPythonExe) { $BundledPythonExe } else { "python" }
$script:Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = $script:Utf8NoBom
[Console]::InputEncoding = $script:Utf8NoBom
$OutputEncoding = $script:Utf8NoBom
$env:PYTHONPATH = if ($env:PYTHONPATH) { "$AppRoot;$env:PYTHONPATH" } else { $AppRoot }
$env:PYTHONIOENCODING = "utf-8"

function Get-DefaultOutputFolder {
    $profile = [Environment]::GetFolderPath([Environment+SpecialFolder]::UserProfile)
    if (-not [string]::IsNullOrWhiteSpace($profile)) {
        return (Join-Path (Join-Path $profile "Downloads") "GEOGetter")
    }
    return (Join-Path $AppRoot "downloads")
}

function Get-DefaultOutputFolderForAccession {
    param([string]$PrimaryAccession)
    $baseSource = if ([string]::IsNullOrWhiteSpace($PrimaryAccession)) { "geo_getter_download" } else { $PrimaryAccession.Trim() }
    $baseName = ConvertTo-GeoGetterSafeName $baseSource
    return [System.IO.Path]::GetFullPath((Join-Path (Get-DefaultOutputFolder) $baseName))
}

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
if (-not ([System.Management.Automation.PSTypeName]"GeoGetterProcessUiBridge").Type) {
    Add-Type -ReferencedAssemblies System.Windows.Forms -TypeDefinition @'
using System;
using System.Diagnostics;
using System.Windows.Forms;

public sealed class GeoGetterProcessUiBridge
{
    private readonly Control control;
    private readonly Action<string> output;
    private readonly Action<string> error;
    private readonly Action<int> exited;
    private readonly Action outputClosed;

    public GeoGetterProcessUiBridge(Control control, Action<string> output, Action<string> error, Action<int> exited)
        : this(control, output, error, exited, null)
    {
    }

    public GeoGetterProcessUiBridge(Control control, Action<string> output, Action<string> error, Action<int> exited, Action outputClosed)
    {
        this.control = control;
        this.output = output;
        this.error = error;
        this.exited = exited;
        this.outputClosed = outputClosed;
    }

    public void Attach(Process process)
    {
        process.OutputDataReceived += (sender, args) =>
        {
            if (args.Data == null)
            {
                InvokeAction(outputClosed);
                return;
            }
            InvokeString(output, args.Data);
        };
        process.ErrorDataReceived += (sender, args) => InvokeString(error, args.Data);
        process.Exited += (sender, args) =>
        {
            try
            {
                InvokeInt(exited, ((Process)sender).ExitCode);
            }
            catch
            {
                // The process may already be torn down while the form is closing.
            }
        };
    }

    private void InvokeString(Action<string> action, string value)
    {
        if (String.IsNullOrEmpty(value)) return;
        TryBeginInvoke(action, new object[] { value });
    }

    private void InvokeInt(Action<int> action, int value)
    {
        TryBeginInvoke(action, new object[] { value });
    }

    private void InvokeAction(Action action)
    {
        if (action == null) return;
        TryBeginInvoke(action, new object[] { });
    }

    private void TryBeginInvoke(Delegate action, object[] args)
    {
        try
        {
            if (control.IsDisposed || !control.IsHandleCreated) return;
            control.BeginInvoke(action, args);
        }
        catch (ObjectDisposedException)
        {
        }
        catch (InvalidOperationException)
        {
        }
    }
}
'@
}
[System.Windows.Forms.Application]::EnableVisualStyles()

$script:Resolved = $null
$script:ResolvedJsonPath = Join-Path ([System.IO.Path]::GetTempPath()) ("geo_getter_" + [System.Guid]::NewGuid().ToString("N") + ".json")
$script:FastqDefaultSorted = $false
$script:SuppDefaultSorted = $false
$script:ResolveProcess = $null
$script:ResolveBridge = $null
$script:ResolveInputPath = $null
$script:ResolveStdoutText = ""
$script:ResolveStderrText = ""
$script:DownloadProcess = $null
$script:DownloadBridge = $null
$script:DownloadCanceled = $false
$script:DownloadStdoutText = ""
$script:DownloadStderrText = ""
$script:VerifyProcess = $null
$script:VerifyBridge = $null
$script:VerifyCanceled = $false
$script:VerifyStdoutText = ""
$script:VerifyStderrText = ""
$script:DiagnosticProcessOutputLimitBytes = 1048576
$script:LastDiagnosticError = $null
$script:LastResolveExitCode = $null
$script:LastResolveArguments = @()
$script:LastDownloadArguments = @()
$script:LastVerificationArguments = @()
$script:LastDownloadStartError = ""
$script:LastDownloadDoneEvent = $null
$script:LastDownloadExitCode = $null
$script:LastVerificationDoneEvent = $null
$script:LastVerificationExitCode = $null
$script:DownloadExitObserved = $false
$script:DownloadStdoutClosed = $false
$script:DownloadFinalized = $false
$script:VerifyExitObserved = $false
$script:VerifyStdoutClosed = $false
$script:VerifyFinalized = $false
$script:LastPreflightStatus = ""
$script:LastPreflightError = ""
$script:LastPreflightOutputDir = ""
$script:LastPreflightRequiredBytes = $null
$script:LastPreflightFreeBytes = $null
$script:LastInputText = ""
$script:LastResolvedInputText = ""
$script:Language = $UiLanguage
$script:Translations = @{
    ja = @{
        appTitle = "GEOGetter"
        settingsMenu = "設定"
        languageMenu = "言語"
        japanese = "日本語"
        english = "English"
        toolsMenu = "ツール"
        verifyManifestMenu = "保存済みFASTQを確認"
        helpMenu = "ヘルプ"
        helpOpen = "ヘルプを開く"
        helpUsage = "基本の使い方"
        helpInput = "入力できるID"
        helpTables = "表の見方"
        helpOutputFiles = "保存されるファイル"
        helpIntegrity = "MD5・エラーの見方"
        helpCancelRetry = "キャンセルと再試行"
        helpClose = "閉じる"
        about = "このアプリについて"
        inputLabel = "GEO accession / URL"
        fetchButton = "ファイルを検索"
        outputLabel = "保存先"
        browseButton = "参照..."
        datasetTitleLabel = "GEO情報"
        capacityInitial = "必要容量: - / 空き容量: -"
        capacityText = "必要容量(FASTQ): {0} / 空き容量: {1}{2}"
        capacityUnknown = "必要容量(FASTQ): {0} / 空き容量: 取得不可"
        supplementarySuffix = " / 補足ファイル: {0} 件（サイズ不明）"
        selectionSummary = "選択内容: FASTQ {0} 件 / {1}、GEO supplementary {2} 件、保存先: {3}"
        fastqTitle = "raw FASTQ（ENA direct FASTQ）: {0}件"
        supplementaryTitle = "GEO supplementary / processed file（raw FASTQ以外）: {0}件"
        downloadButton = "選択ファイルをダウンロード"
        cancelButton = "キャンセル"
        diagnosticsButton = "診断情報を保存"
        selectAllButton = "すべて選択"
        clearSelectionButton = "選択解除"
        idle = "待機中"
        fetching = "metadata取得中"
        downloading = "ダウンロード中"
        verifyingManifest = "manifest確認中"
        complete = "完了"
        completePartial = "完了（一部失敗あり）"
        completeUnverified = "完了（MD5未検証あり）"
        error = "エラー"
        canceled = "キャンセルしました"
        overallDesign = "Overall design"
        helpUsageText = (@(
            "1. GEO accession、GEO URL、またはSRA/ENA/Project/BioSample系 accessionを入力します。"
            "2. [ファイルを検索] を押します。"
            "3. GEO情報欄で Accession / Organism / Status を確認します。"
            "4. raw FASTQ と GEO supplementary / processed file は別の表で確認します。"
            "5. 保存したい行だけを選択し、保存先と必要容量を確認します。"
            "6. [選択ファイルをダウンロード] を押します。"
        ) -join [Environment]::NewLine)
        helpInputText = (@(
            "対応している入力:"
            "- GSE、GSM"
            "- GEOページURL"
            "- SRP / SRX / SRR / SRS"
            "- ERP / ERX / ERR / ERS"
            "- DRP / DRX / DRR / DRS"
            "- PRJNA / PRJEB / PRJDB"
            "- SAMN / SAMEA / SAMD"
            ""
            "GSE/GSMを入力した場合は、GEOのSOFT情報から関連するSRA/ENA/BioProject/BioSample accessionを探します。SRA/ENA/Project/BioSample系 accessionを入力した場合は、直接ENAに問い合わせます。"
            ""
            "入力欄に複数のIDやURLが含まれている場合、最初に見つかった対応 accession を使います。"
        ) -join [Environment]::NewLine)
        helpTablesText = (@(
            "raw FASTQ（ENA direct FASTQ）:"
            "ENA Portal APIから取得したraw read候補です。FASTQ URL、ファイルサイズ、照合用MD5値が表示されます。照合用MD5値がある場合は、保存後に検証します。"
            ""
            "GEO supplementary / processed file:"
            "GEOページに登録されている補足ファイルやprocessed fileです。raw FASTQとは別の保存対象です。"
            ""
            "FASTQ表のGEO Sampleとサンプル名は、GEO由来のサンプル情報を確認するための列です。件数は各表の見出しに表示されます。"
        ) -join [Environment]::NewLine)
        helpOutputFilesText = (@(
            "検索後、保存先には実際に保存する accession フォルダが表示されます。"
            "別の場所に保存したい場合は、保存先で実際に使うフォルダを選びます。"
            ""
            "例:"
            "downloads\GSE52778\"
            "  GSE52778_fastq_manifest.tsv"
            "  GSE52778_supplementary_manifest.tsv"
            "  GSE52778_download_log.tsv"
            "  SRR1039508_1.fastq.gz"
            ""
            "download logには、FASTQとsupplementary fileの保存結果を記録します。FASTQを選んだ場合はfastq manifest、supplementary fileを選んだ場合はsupplementary manifestも作成します。"
        ) -join [Environment]::NewLine)
        helpIntegrityText = (@(
            "FASTQ:"
            "照合用MD5値がある場合、ダウンロード後に実際のMD5と照合します。一致した場合だけ正式なFASTQファイル名に変更します。"
            ""
            "主な状態:"
            "- md5_verified: MD5が一致しました。"
            "- md5_unavailable: ENAから照合用MD5値を取得できなかったため、保存はしますが検証はできません。"
            "- md5_mismatch: 正式なFASTQとしては保存せず、退避名に変更します。"
            "- network_failed: 通信に失敗しました。時間をおいて再実行してください。"
            ""
            "GEO supplementary / processed fileの保存結果はdownload logに残ります。"
        ) -join [Environment]::NewLine)
        helpCancelRetryText = (@(
            "キャンセル:"
            "実行中のダウンロードは [キャンセル] で停止できます。停止時点の途中ファイルが残ることがあります。"
            ""
            "再試行:"
            "通信失敗時は自動で再試行します。再試行しても失敗したファイルはdownload logに失敗として記録されます。"
            ""
            "既存ファイル:"
            "同名FASTQが既にありMD5が一致する場合は、再ダウンロードせず再利用します。MD5が一致しない場合は、既存ファイルを退避してから取り直します。"
            ""
            "保存先の空き容量が不足している場合は、保存先を変更するか、選択するFASTQを減らしてください。"
        ) -join [Environment]::NewLine)
        aboutText = "GEOページ起点でENA direct FASTQとGEO supplementary / processed fileを取得するデスクトップアプリです。FASTQはMD5検証ログを残します。"
        colSelect = "選択"
        colRun = "Run"
        colGeoSample = "GEO Sample"
        colGeoTitle = "サンプル名"
        colFileName = "ファイル名"
        colEnaSample = "ENA Sample"
        colLayout = "Layout"
        colSize = "サイズ"
        colMd5 = "MD5"
        colFastqUrl = "FASTQ URL"
        colScope = "区分"
        colGeoUrl = "GEO URL"
        metadataFailed = "metadata取得に失敗しました。"
        searchRequiredBeforeDownload = "現在の入力に対する検索結果がありません。もう一度ファイルを検索してください。"
        inputChangedAfterResolve = "入力内容が変更されています。現在の入力で再度ファイルを検索してください。"
        noFastqSelected = "FASTQが選択されていません。"
        noFilesSelected = "FASTQまたはGEO supplementary/processed fileを選択してください。"
        resolveAlreadyRunning = "metadata取得が既に実行中です。"
        verifyManifestAlreadyRunning = "manifest確認が既に実行中です。"
        fastqCountLog = "{0}: FASTQ {1}件"
        supplementaryCountLog = "GEO supplementary/processed: {0}件"
        fastqManifestLog = "FASTQリスト: {0}"
        supplementaryManifestLog = "GEO supplementaryリスト: {0}"
        downloadLogLog = "ログファイル: {0}"
        verifyManifestDialogTitle = "FASTQ manifestを選択"
        verifyManifestFilter = "FASTQ manifest (*_fastq_manifest.tsv)|*_fastq_manifest.tsv|TSVファイル (*.tsv)|*.tsv|すべてのファイル (*.*)|*.*"
        verifyManifestStartedLog = "FASTQ manifestを確認します: {0}"
        verifyManifestReportLog = "確認レポート: {0}"
        verifyManifestSummaryLog = "確認結果: {0}"
        verifyManifestCompleteMessage = "確認レポートを作成しました: {0}"
        verifyManifestPartialMessage = "確認レポートを作成しました（一部要確認）: {0}"
        verifyManifestNoReport = "確認レポートが作成されませんでした。"
        verifyCancelRequestLog = "キャンセル要求: manifest確認を停止します。"
        progressDisplayError = "進捗表示エラー: {0}"
        exitHandlerError = "終了処理エラー: {0}"
        processEnvError = "ProcessStartInfoの環境変数を設定できません。"
        cancelRequestLog = "キャンセル要求: 実行中のダウンロードを停止します。途中ファイルは .part として残ります。"
        cancelFailedLog = "キャンセル失敗: {0}"
        preflightFailedLog = "保存先preflight失敗: {0}"
        preflightOutputRequired = "保存先を選択してください。"
        preflightOutputIsFile = "保存先にファイルが指定されています。フォルダを選択してください: {0}"
        preflightCannotCreateOutput = "保存先フォルダを作成できません: {0}"
        preflightCannotWrite = "保存先に書き込めません: {0}"
        preflightInsufficientSpace = "空き容量が足りません。必要容量(FASTQ): {0} / 空き容量: {1}"
        preflightPathTooLong = "保存先パスが長すぎます: {0}"
        diagnosticsSavedLog = "診断情報を保存しました: {0}"
        diagnosticsFailedLog = "診断情報の保存に失敗しました: {0}"
        diagnosticsDialogTitle = "診断情報を保存"
        diagnosticsFilter = "ZIPファイル (*.zip)|*.zip"
    }
    en = @{
        appTitle = "GEOGetter"
        settingsMenu = "Settings"
        languageMenu = "Language"
        japanese = "Japanese"
        english = "English"
        toolsMenu = "Tools"
        verifyManifestMenu = "Verify saved FASTQ"
        helpMenu = "Help"
        helpOpen = "Open help"
        helpUsage = "Basic usage"
        helpInput = "Supported IDs"
        helpTables = "Reading the tables"
        helpOutputFiles = "Saved files"
        helpIntegrity = "MD5 and errors"
        helpCancelRetry = "Cancel and retry"
        helpClose = "Close"
        about = "About"
        inputLabel = "GEO accession / URL"
        fetchButton = "Find files"
        outputLabel = "Output folder"
        browseButton = "Browse"
        datasetTitleLabel = "GEO info"
        capacityInitial = "Required: - / Free: -"
        capacityText = "Required (FASTQ): {0} / Free: {1}{2}"
        capacityUnknown = "Required (FASTQ): {0} / Free: unavailable"
        supplementarySuffix = " / supplementary files: {0} selected (size unknown)"
        selectionSummary = "Selection: FASTQ {0} files / {1}, GEO supplementary {2} files, output: {3}"
        fastqTitle = "raw FASTQ (ENA direct FASTQ): {0} files"
        supplementaryTitle = "GEO supplementary / processed files (not raw FASTQ): {0} files"
        downloadButton = "Download selected files"
        cancelButton = "Cancel"
        diagnosticsButton = "Save diagnostics"
        selectAllButton = "Select all"
        clearSelectionButton = "Clear selection"
        idle = "Idle"
        fetching = "Fetching metadata"
        downloading = "Downloading"
        verifyingManifest = "Checking manifest"
        complete = "Complete"
        completePartial = "Complete with failures"
        completeUnverified = "Complete with unverified files"
        error = "Error"
        canceled = "Canceled"
        overallDesign = "Overall design"
        helpUsageText = (@(
            "1. Enter a GEO accession, GEO URL, or SRA/ENA/Project/BioSample accession."
            "2. Click [Find files]."
            "3. Check Accession / Organism / Status in the GEO info area."
            "4. Review raw FASTQ and GEO supplementary / processed files in separate tables."
            "5. Select only the rows you want, then confirm the output folder and required space."
            "6. Click [Download selected files]."
        ) -join [Environment]::NewLine)
        helpInputText = (@(
            "Supported input:"
            "- GSE, GSM"
            "- GEO page URL"
            "- SRP / SRX / SRR / SRS"
            "- ERP / ERX / ERR / ERS"
            "- DRP / DRX / DRR / DRS"
            "- PRJNA / PRJEB / PRJDB"
            "- SAMN / SAMEA / SAMD"
            ""
            "For GSE/GSM input, GEOGetter reads GEO SOFT metadata and searches for related SRA/ENA/BioProject/BioSample accessions. For SRA/ENA/Project/BioSample accessions, it queries ENA directly."
            ""
            "If the input contains multiple IDs or URLs, GEOGetter uses the first supported accession it finds."
        ) -join [Environment]::NewLine)
        helpTablesText = (@(
            "raw FASTQ (ENA direct FASTQ):"
            "Raw read candidates returned by the ENA Portal API. The table shows FASTQ URLs, file sizes, and expected MD5 values. When an expected MD5 is available, GEOGetter verifies the file after download."
            ""
            "GEO supplementary / processed files:"
            "Supplementary or processed files registered on the GEO page. These are saved separately from raw FASTQ files."
            ""
            "The GEO Sample and Sample title columns in the FASTQ table help confirm the source sample. Counts are shown in each table title."
        ) -join [Environment]::NewLine)
        helpOutputFilesText = (@(
            "After search, the output field shows the accession folder that GEOGetter will write to."
            "To save somewhere else, choose the actual folder you want to use."
            ""
            "Example:"
            "downloads\GSE52778\"
            "  GSE52778_fastq_manifest.tsv"
            "  GSE52778_supplementary_manifest.tsv"
            "  GSE52778_download_log.tsv"
            "  SRR1039508_1.fastq.gz"
            ""
            "The download log records save results for FASTQ and supplementary files. A FASTQ manifest is created when FASTQ files are selected, and a supplementary manifest is created when supplementary files are selected."
        ) -join [Environment]::NewLine)
        helpIntegrityText = (@(
            "FASTQ:"
            "When an expected MD5 is available, GEOGetter compares it with the actual MD5 after download. The file is moved to the final FASTQ name only when the MD5 matches."
            ""
            "Common statuses:"
            "- md5_verified: the expected and actual MD5 matched."
            "- md5_unavailable: ENA did not provide an expected MD5, so the file is saved but not verified."
            "- md5_mismatch: the file is not kept as the final FASTQ and is moved aside."
            "- network_failed: the transfer failed. Check the connection and try again later."
            ""
            "GEO supplementary / processed file save results are recorded in the download log."
        ) -join [Environment]::NewLine)
        helpCancelRetryText = (@(
            "Cancel:"
            "Use [Cancel] to stop a running download. A partial file may remain after cancellation."
            ""
            "Retry:"
            "Network failures are retried automatically. Files that still fail after retry are recorded as failures in the download log."
            ""
            "Existing files:"
            "If a FASTQ file with the same name already exists and its MD5 matches, GEOGetter reuses it instead of downloading again. If the MD5 does not match, the existing file is moved aside and downloaded again."
            ""
            "If the output folder does not have enough free space, change the output folder or select fewer FASTQ files."
        ) -join [Environment]::NewLine)
        aboutText = "Desktop app for downloading ENA direct FASTQ and GEO supplementary / processed files from a GEO page. It keeps MD5 verification logs for FASTQ files."
        colSelect = "Select"
        colRun = "Run"
        colGeoSample = "GEO Sample"
        colGeoTitle = "Sample title"
        colFileName = "File name"
        colEnaSample = "ENA Sample"
        colLayout = "Layout"
        colSize = "Size"
        colMd5 = "MD5"
        colFastqUrl = "FASTQ URL"
        colScope = "Type"
        colGeoUrl = "GEO URL"
        metadataFailed = "Metadata retrieval failed."
        searchRequiredBeforeDownload = "No search result is available for the current input. Search files again."
        inputChangedAfterResolve = "The input has changed. Search files again for the current input."
        noFastqSelected = "No FASTQ files are selected."
        noFilesSelected = "Select at least one FASTQ or GEO supplementary/processed file."
        resolveAlreadyRunning = "Metadata retrieval is already running."
        verifyManifestAlreadyRunning = "Manifest verification is already running."
        fastqCountLog = "{0}: FASTQ {1} files"
        supplementaryCountLog = "GEO supplementary/processed: {0} files"
        fastqManifestLog = "FASTQ list: {0}"
        supplementaryManifestLog = "GEO supplementary list: {0}"
        downloadLogLog = "Download log: {0}"
        verifyManifestDialogTitle = "Select FASTQ manifest"
        verifyManifestFilter = "FASTQ manifest (*_fastq_manifest.tsv)|*_fastq_manifest.tsv|TSV file (*.tsv)|*.tsv|All files (*.*)|*.*"
        verifyManifestStartedLog = "Checking FASTQ manifest: {0}"
        verifyManifestReportLog = "Verification report: {0}"
        verifyManifestSummaryLog = "Verification results: {0}"
        verifyManifestCompleteMessage = "Verification report created: {0}"
        verifyManifestPartialMessage = "Verification report created with issues: {0}"
        verifyManifestNoReport = "No verification report was created."
        verifyCancelRequestLog = "Cancel requested: stopping manifest verification."
        progressDisplayError = "Progress display error: {0}"
        exitHandlerError = "Exit handler error: {0}"
        processEnvError = "Could not set ProcessStartInfo environment variables."
        cancelRequestLog = "Cancel requested: stopping the running download. Partial files may remain as .part files."
        cancelFailedLog = "Cancel failed: {0}"
        preflightFailedLog = "Output preflight failed: {0}"
        preflightOutputRequired = "Select an output folder."
        preflightOutputIsFile = "The output path is a file. Select a folder: {0}"
        preflightCannotCreateOutput = "Could not create the output folder: {0}"
        preflightCannotWrite = "Could not write to the output folder: {0}"
        preflightInsufficientSpace = "Not enough free space. Required (FASTQ): {0} / Free: {1}"
        preflightPathTooLong = "The output path is too long: {0}"
        diagnosticsSavedLog = "Saved diagnostics: {0}"
        diagnosticsFailedLog = "Failed to save diagnostics: {0}"
        diagnosticsDialogTitle = "Save diagnostics"
        diagnosticsFilter = "ZIP file (*.zip)|*.zip"
    }
}

function Format-Bytes {
    param([Int64]$Bytes)
    $units = @("B", "KB", "MB", "GB", "TB")
    if ($Bytes -lt 0) {
        $Bytes = [Int64]0
    }
    $size = [double]$Bytes
    foreach ($unit in $units) {
        if ($size -lt 1024 -or $unit -eq "TB") {
            if ($unit -eq "B") { return ("{0} {1}" -f [Int64]$size, $unit) }
            return ("{0:N2} {1}" -f $size, $unit)
        }
        $size = $size / 1024
    }
}

function T {
    param([string]$Key)
    $table = $script:Translations[$script:Language]
    if ($table.ContainsKey($Key)) {
        return $table[$Key]
    }
    return $script:Translations["ja"][$Key]
}

function Get-HelpTopics {
    return @(
        [pscustomobject]@{ TitleKey = "helpUsage"; TextKey = "helpUsageText" }
        [pscustomobject]@{ TitleKey = "helpInput"; TextKey = "helpInputText" }
        [pscustomobject]@{ TitleKey = "helpTables"; TextKey = "helpTablesText" }
        [pscustomobject]@{ TitleKey = "helpOutputFiles"; TextKey = "helpOutputFilesText" }
        [pscustomobject]@{ TitleKey = "helpIntegrity"; TextKey = "helpIntegrityText" }
        [pscustomobject]@{ TitleKey = "helpCancelRetry"; TextKey = "helpCancelRetryText" }
    )
}

function Show-HelpWindow {
    $dialog = New-Object System.Windows.Forms.Form
    $dialog.Text = T "helpMenu"
    $dialog.Size = New-Object System.Drawing.Size(860, 560)
    $dialog.MinimumSize = New-Object System.Drawing.Size(680, 420)
    $dialog.StartPosition = "CenterParent"
    $dialog.ShowIcon = $false

    $layout = New-Object System.Windows.Forms.TableLayoutPanel
    $layout.Dock = "Fill"
    $layout.ColumnCount = 1
    $layout.RowCount = 2
    $layout.Padding = New-Object System.Windows.Forms.Padding(10)
    [void]$layout.ColumnStyles.Add((New-Object System.Windows.Forms.ColumnStyle([System.Windows.Forms.SizeType]::Percent, 100)))
    [void]$layout.RowStyles.Add((New-Object System.Windows.Forms.RowStyle([System.Windows.Forms.SizeType]::Percent, 100)))
    [void]$layout.RowStyles.Add((New-Object System.Windows.Forms.RowStyle([System.Windows.Forms.SizeType]::Absolute, 44)))
    $dialog.Controls.Add($layout)

    $contentLayout = New-Object System.Windows.Forms.TableLayoutPanel
    $contentLayout.Dock = "Fill"
    $contentLayout.ColumnCount = 2
    $contentLayout.RowCount = 1
    [void]$contentLayout.ColumnStyles.Add((New-Object System.Windows.Forms.ColumnStyle([System.Windows.Forms.SizeType]::Absolute, 210)))
    [void]$contentLayout.ColumnStyles.Add((New-Object System.Windows.Forms.ColumnStyle([System.Windows.Forms.SizeType]::Percent, 100)))
    [void]$contentLayout.RowStyles.Add((New-Object System.Windows.Forms.RowStyle([System.Windows.Forms.SizeType]::Percent, 100)))
    $layout.Controls.Add($contentLayout, 0, 0)

    $topicList = New-Object System.Windows.Forms.ListBox
    $topicList.Dock = "Fill"
    $topicList.IntegralHeight = $false
    $contentLayout.Controls.Add($topicList, 0, 0)

    $rightLayout = New-Object System.Windows.Forms.TableLayoutPanel
    $rightLayout.Dock = "Fill"
    $rightLayout.ColumnCount = 1
    $rightLayout.RowCount = 2
    $rightLayout.Margin = New-Object System.Windows.Forms.Padding(10, 0, 0, 0)
    [void]$rightLayout.ColumnStyles.Add((New-Object System.Windows.Forms.ColumnStyle([System.Windows.Forms.SizeType]::Percent, 100)))
    [void]$rightLayout.RowStyles.Add((New-Object System.Windows.Forms.RowStyle([System.Windows.Forms.SizeType]::Absolute, 34)))
    [void]$rightLayout.RowStyles.Add((New-Object System.Windows.Forms.RowStyle([System.Windows.Forms.SizeType]::Percent, 100)))
    $contentLayout.Controls.Add($rightLayout, 1, 0)

    $titleLabel = New-Object System.Windows.Forms.Label
    $titleLabel.Dock = "Fill"
    $titleLabel.TextAlign = "MiddleLeft"
    $titleLabel.Font = New-Object System.Drawing.Font($titleLabel.Font, [System.Drawing.FontStyle]::Bold)
    $rightLayout.Controls.Add($titleLabel, 0, 0)

    $bodyBox = New-Object System.Windows.Forms.TextBox
    $bodyBox.Dock = "Fill"
    $bodyBox.Multiline = $true
    $bodyBox.ReadOnly = $true
    $bodyBox.ScrollBars = "Vertical"
    $bodyBox.WordWrap = $true
    $bodyBox.BackColor = [System.Drawing.SystemColors]::Window
    $rightLayout.Controls.Add($bodyBox, 0, 1)

    $topics = Get-HelpTopics
    foreach ($topic in $topics) {
        [void]$topicList.Items.Add((T $topic.TitleKey))
    }
    $topicList.Add_SelectedIndexChanged({
        $index = $topicList.SelectedIndex
        if ($index -lt 0) { return }
        $topic = $topics[$index]
        $titleLabel.Text = T $topic.TitleKey
        $bodyBox.Text = T $topic.TextKey
        $bodyBox.SelectionStart = 0
        $bodyBox.ScrollToCaret()
    })
    if ($topicList.Items.Count -gt 0) {
        $topicList.SelectedIndex = 0
    }

    $buttonPanel = New-Object System.Windows.Forms.FlowLayoutPanel
    $buttonPanel.Dock = "Fill"
    $buttonPanel.FlowDirection = "RightToLeft"
    $buttonPanel.Padding = New-Object System.Windows.Forms.Padding(0, 8, 0, 0)
    $layout.Controls.Add($buttonPanel, 0, 1)

    $closeButton = New-Object System.Windows.Forms.Button
    $closeButton.Text = T "helpClose"
    $closeButton.Size = New-Object System.Drawing.Size(90, 28)
    $closeButton.DialogResult = [System.Windows.Forms.DialogResult]::OK
    [void]$buttonPanel.Controls.Add($closeButton)
    $dialog.AcceptButton = $closeButton
    $dialog.CancelButton = $closeButton

    [void]$dialog.ShowDialog($form)
}

function Set-Language {
    param([string]$Language)
    if (-not $script:Translations.ContainsKey($Language)) { return }
    $script:Language = $Language
    Update-StaticTexts
}

function Update-StaticTexts {
    if ($null -eq $form) { return }
    $form.Text = T "appTitle"
    if ($settingsMenuItem) { $settingsMenuItem.Text = T "settingsMenu" }
    if ($languageMenuItem) { $languageMenuItem.Text = T "languageMenu" }
    if ($japaneseMenuItem) { $japaneseMenuItem.Text = T "japanese" }
    if ($englishMenuItem) { $englishMenuItem.Text = T "english" }
    if ($toolsMenuItem) { $toolsMenuItem.Text = T "toolsMenu" }
    if ($verifyManifestMenuItem) { $verifyManifestMenuItem.Text = T "verifyManifestMenu" }
    if ($helpMenuItem) { $helpMenuItem.Text = T "helpMenu" }
    if ($helpOpenMenuItem) { $helpOpenMenuItem.Text = T "helpOpen" }
    if ($aboutMenuItem) { $aboutMenuItem.Text = T "about" }
    if ($inputLabel) { $inputLabel.Text = T "inputLabel" }
    if ($fetchButton) { $fetchButton.Text = T "fetchButton" }
    if ($outputLabel) { $outputLabel.Text = T "outputLabel" }
    if ($browseButton) { $browseButton.Text = T "browseButton" }
    if ($datasetTitleLabel) { $datasetTitleLabel.Text = T "datasetTitleLabel" }
    Update-ResultTitles
    if ($downloadButton) { $downloadButton.Text = T "downloadButton" }
    if ($cancelButton) { $cancelButton.Text = T "cancelButton" }
    if ($diagnosticsButton) { $diagnosticsButton.Text = T "diagnosticsButton" }
    if ($fastqSelectAllButton) { $fastqSelectAllButton.Text = T "selectAllButton" }
    if ($fastqClearSelectionButton) { $fastqClearSelectionButton.Text = T "clearSelectionButton" }
    if ($suppSelectAllButton) { $suppSelectAllButton.Text = T "selectAllButton" }
    if ($suppClearSelectionButton) { $suppClearSelectionButton.Text = T "clearSelectionButton" }
    $idleTexts = @($script:Translations["ja"]["idle"], $script:Translations["en"]["idle"])
    if ($statusLabel -and ([string]::IsNullOrWhiteSpace($statusLabel.Text) -or $idleTexts -contains $statusLabel.Text)) {
        $statusLabel.Text = T "idle"
    }
    if ($null -ne $capacityLabel) { Update-Capacity }
    Update-SelectionSummary
    Update-GridHeaders
    Update-DatasetInfo
}

function Update-GridHeaders {
    if ($fastqGrid -and $fastqGrid.Columns.Count -gt 0) {
        $fastqGrid.Columns["selected"].HeaderText = T "colSelect"
        $fastqGrid.Columns["run"].HeaderText = T "colRun"
        $fastqGrid.Columns["geo_sample"].HeaderText = T "colGeoSample"
        $fastqGrid.Columns["geo_title"].HeaderText = T "colGeoTitle"
        $fastqGrid.Columns["file_name"].HeaderText = T "colFileName"
        $fastqGrid.Columns["sample"].HeaderText = T "colEnaSample"
        $fastqGrid.Columns["layout"].HeaderText = T "colLayout"
        $fastqGrid.Columns["size"].HeaderText = T "colSize"
        $fastqGrid.Columns["md5"].HeaderText = T "colMd5"
        $fastqGrid.Columns["url"].HeaderText = T "colFastqUrl"
    }
    if ($suppGrid -and $suppGrid.Columns.Count -gt 0) {
        $suppGrid.Columns["supp_selected"].HeaderText = T "colSelect"
        $suppGrid.Columns["supp_scope"].HeaderText = T "colScope"
        $suppGrid.Columns["supp_name"].HeaderText = T "colFileName"
        $suppGrid.Columns["supp_url"].HeaderText = T "colGeoUrl"
    }
}

function Format-MetadataValue {
    param([object]$Value)
    $text = [string]$Value
    if ([string]::IsNullOrWhiteSpace($text)) { return "-" }
    return $text
}

function Update-ResultTitles {
    $fastqCount = 0
    $suppCount = 0
    if ($null -ne $script:Resolved) {
        $fastqCount = @($script:Resolved.fastq_files).Count
        $suppCount = @($script:Resolved.supplementary_files).Count
    }
    if ($fastqTitle) { $fastqTitle.Text = (T "fastqTitle") -f $fastqCount }
    if ($suppTitle) { $suppTitle.Text = (T "supplementaryTitle") -f $suppCount }
}

function Update-DatasetInfo {
    if ($null -eq $geoAccessionValueLabel -or $null -eq $geoOrganismValueLabel -or $null -eq $geoStatusValueLabel) { return }
    if ($null -eq $script:Resolved -or $null -eq $script:Resolved.dataset_metadata) {
        Set-DatasetInfoValues "-" "-" "-"
        return
    }
    $metadata = $script:Resolved.dataset_metadata
    $accession = if ($script:Resolved.primary_accession) { $script:Resolved.primary_accession } else { $metadata.accession }
    Set-DatasetInfoValues $accession $metadata.organism $metadata.status
}

function Set-DatasetInfoValues {
    param(
        [string]$Accession,
        [string]$Organism,
        [string]$Status
    )
    if ($geoAccessionValueLabel) { $geoAccessionValueLabel.Text = Format-MetadataValue $Accession }
    if ($geoOrganismValueLabel) { $geoOrganismValueLabel.Text = Format-MetadataValue $Organism }
    if ($geoStatusValueLabel) { $geoStatusValueLabel.Text = Format-MetadataValue $Status }
}

function Append-Log {
    param([string]$Message)
    $logBox.AppendText($Message + [Environment]::NewLine)
    $logBox.SelectionStart = $logBox.TextLength
    $logBox.ScrollToCaret()
}

function Show-AppError {
    param([string]$Message)
    Append-Log $Message
    [System.Windows.Forms.MessageBox]::Show($Message, (T "appTitle"), "OK", "Error") | Out-Null
}

function Get-AppVersionForDiagnostics {
    $initPath = Join-Path $AppRoot "geo_getter\__init__.py"
    if (-not (Test-Path -LiteralPath $initPath)) { return "" }
    $content = Get-Content -Raw -Encoding UTF8 -LiteralPath $initPath
    if ($content -match '(?m)^__version__\s*=\s*"([^"]+)"') {
        return $Matches[1]
    }
    return ""
}

function Get-OutputFreeSpaceOrNull {
    if (-not $outputBox) { return $null }
    return Get-FreeSpaceForPathOrNull ([string]$outputBox.Text)
}

function New-DiagnosticError {
    param(
        [string]$Phase,
        [string]$Command,
        [string]$Code,
        [string]$Detail,
        [string]$Message,
        [string]$Source,
        [object]$ExitCode
    )
    return [pscustomobject]@{
        phase = $Phase
        command = $Command
        code = $Code
        detail = [string]$Detail
        message = [string]$Message
        source = $Source
        exit_code = $ExitCode
    }
}

function Get-CliErrorEventFromText {
    param([string]$Text)
    if ([string]::IsNullOrWhiteSpace($Text)) { return $null }
    foreach ($line in ($Text -split "`r?`n")) {
        if ([string]::IsNullOrWhiteSpace($line)) { continue }
        try {
            $event = $line | ConvertFrom-Json
            if ($event.event -eq "error") { return $event }
        }
        catch { }
    }
    return $null
}

function Set-DiagnosticErrorFromProcessOutput {
    param(
        [string]$Phase,
        [string]$Command,
        [object]$ExitCode,
        [string]$Stdout,
        [string]$Stderr,
        [string]$DefaultCode,
        [string]$DefaultMessage
    )
    $event = Get-CliErrorEventFromText $Stderr
    $source = "cli_stderr_json"
    if ($null -eq $event) {
        $event = Get-CliErrorEventFromText $Stdout
        $source = "cli_stdout_json"
    }
    if ($null -ne $event) {
        $script:LastDiagnosticError = New-DiagnosticError $Phase $Command ([string]$event.code) ([string]$event.detail) ([string]$event.message) $source $ExitCode
        return
    }

    $detail = (($Stdout + [Environment]::NewLine + $Stderr).Trim())
    $message = if ([string]::IsNullOrWhiteSpace($DefaultMessage)) { $detail } else { $DefaultMessage }
    $script:LastDiagnosticError = New-DiagnosticError $Phase $Command $DefaultCode $detail $message "process_output" $ExitCode
}

function Get-PreflightDiagnosticCode {
    param([string]$Message)
    if ($Message -match "空き容量|free space") { return "insufficient_space" }
    if ($Message -match "保存先|output folder") { return "output_path_invalid" }
    if ($Message -match "長すぎます|too long") { return "path_too_long" }
    if ($Message -match "FASTQまたは|Select at least one") { return "selection_required" }
    if ($Message -match "検索結果|入力内容|search result|input has changed|Search files again") { return "resolved_state_invalid" }
    return "preflight_failed"
}

function Set-DownloadPreflightDiagnosticError {
    param(
        [string]$Message,
        [bool]$ClearOutputContext = $false
    )
    $script:LastPreflightStatus = "failed"
    $script:LastPreflightError = $Message
    if ($ClearOutputContext) {
        $script:LastPreflightOutputDir = ""
        $script:LastPreflightRequiredBytes = $null
        $script:LastPreflightFreeBytes = $null
    }
    $code = Get-PreflightDiagnosticCode $script:LastPreflightError
    $script:LastDiagnosticError = New-DiagnosticError "download_preflight" "selected-download-json" $code $script:LastPreflightError $script:LastPreflightError "gui_preflight" $null
}

function Get-ExistingDirectoryForPath {
    param([string]$Path)
    if ([string]::IsNullOrWhiteSpace($Path)) { return $null }
    try {
        $candidate = [System.IO.Path]::GetFullPath($Path)
        if ([System.IO.File]::Exists($candidate)) {
            $candidate = [System.IO.Path]::GetDirectoryName($candidate)
        }
        while (-not [string]::IsNullOrWhiteSpace($candidate) -and -not [System.IO.Directory]::Exists($candidate)) {
            $parent = [System.IO.Directory]::GetParent($candidate)
            if ($null -eq $parent) { return $null }
            $candidate = $parent.FullName
        }
        if ([System.IO.Directory]::Exists($candidate)) { return $candidate }
    }
    catch {
        return $null
    }
    return $null
}

function Get-FreeSpaceForPathOrNull {
    param([string]$Path)
    try {
        $existingDir = Get-ExistingDirectoryForPath $Path
        if ([string]::IsNullOrWhiteSpace($existingDir)) { return $null }
        $root = [System.IO.Path]::GetPathRoot($existingDir)
        if ([string]::IsNullOrWhiteSpace($root)) { return $null }
        $drive = New-Object System.IO.DriveInfo($root)
        return [Int64]$drive.AvailableFreeSpace
    }
    catch {
        return $null
    }
}

function Write-DiagnosticTextFile {
    param(
        [string]$Directory,
        [string]$Name,
        [string]$Content
    )
    $path = Join-Path $Directory $Name
    [System.IO.File]::WriteAllText($path, [string]$Content, $script:Utf8NoBom)
}

function Add-DiagnosticProcessOutput {
    param(
        [ValidateSet("stdout", "stderr")]
        [string]$Stream,
        [string]$Line
    )
    $value = $Line + [Environment]::NewLine
    if ($Stream -eq "stdout") {
        $script:DownloadStdoutText = Limit-DiagnosticText ($script:DownloadStdoutText + $value)
        return
    }
    $script:DownloadStderrText = Limit-DiagnosticText ($script:DownloadStderrText + $value)
}

function Add-DiagnosticVerificationOutput {
    param(
        [ValidateSet("stdout", "stderr")]
        [string]$Stream,
        [string]$Line
    )
    $value = $Line + [Environment]::NewLine
    if ($Stream -eq "stdout") {
        $script:VerifyStdoutText = Limit-DiagnosticText ($script:VerifyStdoutText + $value)
        return
    }
    $script:VerifyStderrText = Limit-DiagnosticText ($script:VerifyStderrText + $value)
}

function Limit-DiagnosticText {
    param([string]$Text)
    if ([string]::IsNullOrEmpty($Text)) { return "" }
    $limit = [int]$script:DiagnosticProcessOutputLimitBytes
    if ($Text.Length -le $limit) { return $Text }
    $marker = "[GEOGetter diagnostics: earlier process output was truncated]" + [Environment]::NewLine
    $keep = [Math]::Max(0, $limit - $marker.Length)
    if ($Text.Length -le $keep) { return $Text }
    return $marker + $Text.Substring($Text.Length - $keep)
}

function Copy-DiagnosticFile {
    param(
        [string]$Source,
        [string]$DestinationDirectory
    )
    if ([string]::IsNullOrWhiteSpace($Source)) { return }
    if (-not (Test-Path -LiteralPath $Source)) { return }
    New-Item -ItemType Directory -Path $DestinationDirectory -Force | Out-Null
    Copy-Item -LiteralPath $Source -Destination (Join-Path $DestinationDirectory ([System.IO.Path]::GetFileName($Source))) -Force
}

function Copy-DiagnosticFilesByPattern {
    param(
        [string]$Directory,
        [string]$Pattern,
        [string]$DestinationDirectory
    )
    if ([string]::IsNullOrWhiteSpace($Directory)) { return }
    if (-not [System.IO.Directory]::Exists($Directory)) { return }
    $matches = @(Get-ChildItem -LiteralPath $Directory -Filter $Pattern -File -ErrorAction SilentlyContinue)
    if ($matches.Count -eq 0) { return }
    New-Item -ItemType Directory -Path $DestinationDirectory -Force | Out-Null
    foreach ($item in $matches) {
        Copy-Item -LiteralPath $item.FullName -Destination (Join-Path $DestinationDirectory $item.Name) -Force
    }
}

function Copy-DiagnosticPreDoneArtifacts {
    param([string]$ArtifactsDirectory)
    $dir = $script:LastPreflightOutputDir
    if ([string]::IsNullOrWhiteSpace($dir)) { return }
    Copy-DiagnosticFilesByPattern $dir "*_fastq_manifest.tsv" $ArtifactsDirectory
    Copy-DiagnosticFilesByPattern $dir "*_supplementary_manifest.tsv" $ArtifactsDirectory
    Copy-DiagnosticFilesByPattern $dir "*_download_log.tsv" $ArtifactsDirectory
}

function Copy-DiagnosticArtifacts {
    param([string]$ArtifactsDirectory)
    if ($null -ne $script:LastDownloadDoneEvent) {
        Copy-DiagnosticFile ([string]$script:LastDownloadDoneEvent.fastq_manifest) $ArtifactsDirectory
        Copy-DiagnosticFile ([string]$script:LastDownloadDoneEvent.supplementary_manifest) $ArtifactsDirectory
        Copy-DiagnosticFile ([string]$script:LastDownloadDoneEvent.download_log) $ArtifactsDirectory
    }
    else {
        Copy-DiagnosticPreDoneArtifacts $ArtifactsDirectory
    }
    if ($null -ne $script:LastVerificationDoneEvent) {
        Copy-DiagnosticFile ([string]$script:LastVerificationDoneEvent.report) $ArtifactsDirectory
    }
}

function Save-DiagnosticsZip {
    param([string]$OutputPath)
    if ([string]::IsNullOrWhiteSpace($OutputPath)) {
        throw "OutputPath is required."
    }
    $stagingRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("geo_getter_diagnostics_" + [System.Guid]::NewGuid().ToString("N"))
    try {
        New-Item -ItemType Directory -Path $stagingRoot -Force | Out-Null
        $diagnostics = [pscustomobject]@{
            app_version = Get-AppVersionForDiagnostics
            created_at_utc = [DateTime]::UtcNow.ToString("o")
            app_root = $AppRoot
            python_exe = $PythonExe
            bundled_python = (Test-Path -LiteralPath $BundledPythonExe)
            ui_language = $script:Language
            input_text = if ($inputBox) { [string]$inputBox.Text } else { $script:LastInputText }
            last_input_text = $script:LastInputText
            resolved_json_path = $script:ResolvedJsonPath
            resolved_present = ($null -ne $script:Resolved)
            primary_accession = if ($script:Resolved) { [string]$script:Resolved.primary_accession } else { "" }
            query_accessions = if ($script:Resolved) { @($script:Resolved.query_accessions) } else { @() }
            warnings = if ($script:Resolved) { @($script:Resolved.warnings) } else { @() }
            fastq_count = if ($script:Resolved) { @($script:Resolved.fastq_files).Count } else { 0 }
            supplementary_count = if ($script:Resolved) { @($script:Resolved.supplementary_files).Count } else { 0 }
            selected_fastq_indices = Get-SelectedFastqIndicesOrEmpty
            selected_supplementary_indices = Get-SelectedSuppIndicesOrEmpty
            output_dir = if ($outputBox) { [string]$outputBox.Text } else { "" }
            output_free_bytes = Get-OutputFreeSpaceOrNull
            last_error = $script:LastDiagnosticError
            last_resolve_exit_code = $script:LastResolveExitCode
            last_resolve_arguments = @($script:LastResolveArguments)
            last_preflight_status = $script:LastPreflightStatus
            last_preflight_error = $script:LastPreflightError
            preflight_output_dir = $script:LastPreflightOutputDir
            preflight_required_bytes = $script:LastPreflightRequiredBytes
            preflight_free_bytes = $script:LastPreflightFreeBytes
            last_download_arguments = @($script:LastDownloadArguments)
            last_download_start_error = $script:LastDownloadStartError
            last_download_exit_code = $script:LastDownloadExitCode
            last_download_done = $script:LastDownloadDoneEvent
            last_verification_arguments = @($script:LastVerificationArguments)
            last_verification_exit_code = $script:LastVerificationExitCode
            last_verification_done = $script:LastVerificationDoneEvent
        }
        Write-DiagnosticTextFile $stagingRoot "diagnostics.json" ($diagnostics | ConvertTo-Json -Depth 12)
        Write-DiagnosticTextFile $stagingRoot "resolve_stdout.txt" $script:ResolveStdoutText
        Write-DiagnosticTextFile $stagingRoot "resolve_stderr.txt" $script:ResolveStderrText
        Write-DiagnosticTextFile $stagingRoot "download_stdout.jsonl" $script:DownloadStdoutText
        Write-DiagnosticTextFile $stagingRoot "download_stderr.txt" $script:DownloadStderrText
        Write-DiagnosticTextFile $stagingRoot "verify_stdout.jsonl" $script:VerifyStdoutText
        Write-DiagnosticTextFile $stagingRoot "verify_stderr.txt" $script:VerifyStderrText
        $guiLogText = ""
        if ($logBox) { $guiLogText = $logBox.Text }
        Write-DiagnosticTextFile $stagingRoot "gui_log.txt" $guiLogText
        if (Test-Path -LiteralPath $script:ResolvedJsonPath) {
            Copy-Item -LiteralPath $script:ResolvedJsonPath -Destination (Join-Path $stagingRoot "resolved.json") -Force
        }
        elseif ($null -ne $script:Resolved) {
            Write-DiagnosticTextFile $stagingRoot "resolved.json" ($script:Resolved | ConvertTo-Json -Depth 12)
        }
        Copy-DiagnosticArtifacts (Join-Path $stagingRoot "artifacts")
        if (Test-Path -LiteralPath $OutputPath) {
            Remove-Item -LiteralPath $OutputPath -Force
        }
        Compress-Archive -Path (Join-Path $stagingRoot "*") -DestinationPath $OutputPath -Force
        return $OutputPath
    }
    finally {
        Remove-Item -LiteralPath $stagingRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}

function Show-DiagnosticsSaveDialog {
    $dialog = New-Object System.Windows.Forms.SaveFileDialog
    $dialog.Title = T "diagnosticsDialogTitle"
    $dialog.Filter = T "diagnosticsFilter"
    $dialog.FileName = ("GEOGetter-diagnostics-{0}.zip" -f (Get-Date -Format "yyyyMMdd-HHmmss"))
    $dialog.InitialDirectory = if ($outputBox -and (Test-Path -LiteralPath $outputBox.Text)) { $outputBox.Text } else { Get-DefaultOutputFolder }
    if ($dialog.ShowDialog($form) -ne [System.Windows.Forms.DialogResult]::OK) { return }
    try {
        $path = Save-DiagnosticsZip $dialog.FileName
        Append-Log ((T "diagnosticsSavedLog") -f $path)
        [System.Windows.Forms.MessageBox]::Show(((T "diagnosticsSavedLog") -f $path), (T "diagnosticsDialogTitle"), "OK", "Information") | Out-Null
    }
    catch {
        $message = (T "diagnosticsFailedLog") -f $_.Exception.Message
        Append-Log $message
        [System.Windows.Forms.MessageBox]::Show($message, (T "diagnosticsDialogTitle"), "OK", "Error") | Out-Null
    }
}

function Invoke-ResolveJson {
    param([string]$InputText)
    $inputPath = New-ResolveInputFile $InputText
    try {
        $result = Invoke-PythonCli -Arguments @("-m", "geo_getter.cli", "resolve-json", "--input-file", $inputPath, "--out-json", $script:ResolvedJsonPath)
        if ($result.ExitCode -ne 0) {
            throw (Join-ProcessOutput $result)
        }
        return (Get-Content -Raw -Encoding UTF8 $script:ResolvedJsonPath | ConvertFrom-Json)
    }
    finally {
        Remove-Item -LiteralPath $inputPath -ErrorAction SilentlyContinue
    }
}

function New-ResolveInputFile {
    param([string]$InputText)
    $inputPath = Join-Path ([System.IO.Path]::GetTempPath()) ("geo_getter_input_" + [System.Guid]::NewGuid().ToString("N") + ".txt")
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($inputPath, $InputText, $utf8NoBom)
    return $inputPath
}

function Normalize-InputText {
    param([string]$Value)
    if ($null -eq $Value) { return "" }
    return $Value.Trim()
}

function Clear-DownloadDiagnosticState {
    $script:DownloadStdoutText = ""
    $script:DownloadStderrText = ""
    $script:LastDownloadDoneEvent = $null
    $script:LastDownloadExitCode = $null
    $script:LastDownloadArguments = @()
    $script:LastDownloadStartError = ""
    $script:DownloadExitObserved = $false
    $script:DownloadStdoutClosed = $false
    $script:DownloadFinalized = $false
}

function Clear-VerificationDiagnosticState {
    $script:VerifyStdoutText = ""
    $script:VerifyStderrText = ""
    $script:LastVerificationDoneEvent = $null
    $script:LastVerificationExitCode = $null
    $script:LastVerificationArguments = @()
    $script:VerifyExitObserved = $false
    $script:VerifyStdoutClosed = $false
    $script:VerifyFinalized = $false
}

function Clear-ResolvedState {
    param(
        [switch]$DeleteResolvedJson,
        [switch]$PreserveResolveDiagnostics
    )
    $script:Resolved = $null
    $script:LastResolvedInputText = ""
    if (-not $PreserveResolveDiagnostics) {
        $script:ResolveStdoutText = ""
        $script:ResolveStderrText = ""
        $script:LastResolveExitCode = $null
        $script:LastResolveArguments = @()
        $script:LastDiagnosticError = $null
    }
    Clear-DownloadDiagnosticState
    Clear-VerificationDiagnosticState
    $script:LastPreflightStatus = ""
    $script:LastPreflightError = ""
    $script:LastPreflightOutputDir = ""
    $script:LastPreflightRequiredBytes = $null
    $script:LastPreflightFreeBytes = $null
    if ($fastqGrid) { $fastqGrid.Rows.Clear() }
    if ($suppGrid) { $suppGrid.Rows.Clear() }
    if ($DeleteResolvedJson -and (Test-Path -LiteralPath $script:ResolvedJsonPath)) {
        Remove-Item -LiteralPath $script:ResolvedJsonPath -Force -ErrorAction SilentlyContinue
    }
    Update-ResultTitles
    Update-DatasetInfo
    Update-Capacity
}

function Assert-ResolvedMatchesCurrentInput {
    if ($null -eq $script:Resolved) {
        throw (T "searchRequiredBeforeDownload")
    }
    if (-not (Test-Path -LiteralPath $script:ResolvedJsonPath)) {
        throw (T "searchRequiredBeforeDownload")
    }
    $currentInputValue = if ($inputBox) { [string]$inputBox.Text } else { $script:LastInputText }
    $currentInput = Normalize-InputText $currentInputValue
    $resolvedInput = Normalize-InputText $script:LastResolvedInputText
    if ([string]::IsNullOrWhiteSpace($currentInput) -or [string]::IsNullOrWhiteSpace($resolvedInput)) {
        throw (T "searchRequiredBeforeDownload")
    }
    if (-not [string]::Equals($resolvedInput, $currentInput, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw (T "inputChangedAfterResolve")
    }
}

function Apply-ResolvedResult {
    param([object]$Resolved)
    $script:Resolved = $Resolved
    $script:LastResolvedInputText = Normalize-InputText ([string]$script:Resolved.input_text)
    if ($outputBox) {
        $outputBox.Text = Get-DefaultOutputFolderForAccession ([string]$script:Resolved.primary_accession)
    }
    $items = @($script:Resolved.fastq_files)
    Add-FastqRowsFromResolved
    Add-SupplementaryRowsFromResolved
    Update-ResultTitles
    Update-DatasetInfo
    $statusLabel.Text = T "complete"
    Append-Log ((T "fastqCountLog") -f $script:Resolved.primary_accession, $items.Count)
    Append-Log ((T "supplementaryCountLog") -f @($script:Resolved.supplementary_files).Count)
    foreach ($warning in @($script:Resolved.warnings)) { Append-Log $warning }
    Update-Capacity
}

function Complete-ResolveProcess {
    param([int]$ExitCode)
    try {
        $script:ResolveProcess = $null
        $script:LastResolveExitCode = $ExitCode
        $inputPath = $script:ResolveInputPath
        $script:ResolveInputPath = $null
        if ($inputPath) {
            Remove-Item -LiteralPath $inputPath -ErrorAction SilentlyContinue
        }
        $progressBar.Style = "Continuous"
        $progressBar.Value = 0
        if ($ExitCode -eq 0) {
            Apply-ResolvedResult (Get-Content -Raw -Encoding UTF8 $script:ResolvedJsonPath | ConvertFrom-Json)
        }
        else {
            Clear-ResolvedState -DeleteResolvedJson -PreserveResolveDiagnostics
            Set-DiagnosticErrorFromProcessOutput "resolve" "resolve-json" $ExitCode $script:ResolveStdoutText $script:ResolveStderrText "resolve_failed" (T "metadataFailed")
            $message = (($script:ResolveStdoutText + $script:ResolveStderrText).Trim())
            if ([string]::IsNullOrWhiteSpace($message)) {
                $message = T "metadataFailed"
            }
            if ($null -ne $script:LastDiagnosticError -and -not [string]::IsNullOrWhiteSpace([string]$script:LastDiagnosticError.message)) {
                $message = [string]$script:LastDiagnosticError.message
            }
            $statusLabel.Text = T "error"
            Show-AppError $message
        }
    }
    finally {
        Set-Busy $false
    }
}

function Get-SelectedIndices {
    $indices = Get-SelectedFastqIndicesOrEmpty
    if (-not $indices) {
        throw (T "noFastqSelected")
    }
    return $indices
}

function Get-SelectedFastqIndicesOrEmpty {
    $indices = New-Object System.Collections.Generic.List[int]
    foreach ($row in $fastqGrid.Rows) {
        if ($row.IsNewRow) { continue }
        $checked = $false
        if ($null -ne $row.Cells["selected"].Value) {
            $checked = [bool]$row.Cells["selected"].Value
        }
        if ($checked) {
            $indices.Add([int]$row.Tag)
        }
    }
    return ($indices -join ",")
}

function Get-SelectedSuppIndicesOrEmpty {
    $indices = New-Object System.Collections.Generic.List[int]
    foreach ($row in $suppGrid.Rows) {
        if ($row.IsNewRow) { continue }
        $checked = $false
        if ($null -ne $row.Cells["supp_selected"].Value) {
            $checked = [bool]$row.Cells["supp_selected"].Value
        }
        if ($checked) {
            $indices.Add([int]$row.Tag)
        }
    }
    return ($indices -join ",")
}

function Get-SelectedSuppCount {
    $count = 0
    foreach ($row in $suppGrid.Rows) {
        if ($row.IsNewRow) { continue }
        if ([bool]$row.Cells["supp_selected"].Value) {
            $count += 1
        }
    }
    return $count
}

function Get-SelectedFastqCount {
    $count = 0
    foreach ($row in $fastqGrid.Rows) {
        if ($row.IsNewRow) { continue }
        if ([bool]$row.Cells["selected"].Value) {
            $count += 1
        }
    }
    return $count
}

function Set-GridSelection {
    param(
        [System.Windows.Forms.DataGridView]$Grid,
        [string]$ColumnName,
        [bool]$Selected
    )
    foreach ($row in $Grid.Rows) {
        if ($row.IsNewRow) { continue }
        $row.Cells[$ColumnName].Value = $Selected
    }
    Update-Capacity
}

function Assert-AnySelection {
    if (-not (Get-SelectedFastqIndicesOrEmpty) -and -not (Get-SelectedSuppIndicesOrEmpty)) {
        throw (T "noFilesSelected")
    }
}

function Get-SelectedTotalBytes {
    if ($null -eq $script:Resolved) { return 0 }
    $total = [Int64]0
    $items = @($script:Resolved.fastq_files)
    foreach ($row in $fastqGrid.Rows) {
        if ($row.IsNewRow) { continue }
        if ([bool]$row.Cells["selected"].Value) {
            $item = $items[[int]$row.Tag]
            $total += [Int64]$item.size_bytes
        }
    }
    return $total
}

function Update-Capacity {
    $total = Get-SelectedTotalBytes
    $freeBytes = if ($outputBox) { Get-FreeSpaceForPathOrNull ([string]$outputBox.Text) } else { $null }
    if ($null -ne $freeBytes) {
        $suppCount = Get-SelectedSuppCount
        $suffix = if ($suppCount -gt 0) { (T "supplementarySuffix") -f $suppCount } else { "" }
        $capacityLabel.Text = (T "capacityText") -f (Format-Bytes $total), (Format-Bytes ([Int64]$freeBytes)), $suffix
    }
    else {
        $capacityLabel.Text = (T "capacityUnknown") -f (Format-Bytes $total)
    }
    Update-SelectionSummary
}

function Update-SelectionSummary {
    if ($null -eq $selectionSummaryLabel) { return }
    $fastqCount = Get-SelectedFastqCount
    $suppCount = Get-SelectedSuppCount
    $output = if ($outputBox) { [string]$outputBox.Text } else { "" }
    $selectionSummaryLabel.Text = (T "selectionSummary") -f $fastqCount, (Format-Bytes (Get-SelectedTotalBytes)), $suppCount, $output
}

function ConvertTo-GeoGetterSafeName {
    param(
        [string]$Value,
        [string]$DefaultName = "geo_getter_download",
        [switch]$ArtifactPrefix
    )
    $safe = [regex]::Replace([string]$Value, '[<>:"/\\|?*\x00-\x1F\x7F]', '_')
    $safe = $safe.Trim(" .".ToCharArray())
    if ([string]::IsNullOrWhiteSpace($safe) -or $safe -eq "." -or $safe -eq "..") { return $DefaultName }
    $stem = ($safe -split '\.', 2)[0].ToUpperInvariant()
    if (@("CON", "PRN", "AUX", "NUL", "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9", "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9") -contains $stem) {
        $safe = "_" + $safe
    }
    return $safe
}

function Get-PreflightNameCollisionKey {
    param([string]$FileName)
    return ([string]$FileName).ToLowerInvariant()
}

function Split-PreflightFileName {
    param([string]$FileName)
    if ($FileName.EndsWith(".fastq.gz", [System.StringComparison]::Ordinal)) {
        return [pscustomobject]@{
            Stem = $FileName.Substring(0, $FileName.Length - 9)
            Suffix = ".fastq.gz"
        }
    }
    return [pscustomobject]@{
        Stem = [System.IO.Path]::GetFileNameWithoutExtension($FileName)
        Suffix = [System.IO.Path]::GetExtension($FileName)
    }
}

function Get-PreflightUniqueNumberedName {
    param(
        [string]$FileName,
        [int]$Count
    )
    if ($Count -le 0) { return $FileName }
    $parts = Split-PreflightFileName $FileName
    return "{0}.{1}{2}" -f $parts.Stem, ($Count + 1), $parts.Suffix
}

function Get-PreflightReservedUniqueName {
    param(
        [string]$FileName,
        [hashtable]$UsedKeys
    )
    $count = 0
    $candidate = $FileName
    while ($UsedKeys.ContainsKey((Get-PreflightNameCollisionKey $candidate))) {
        $count += 1
        $candidate = Get-PreflightUniqueNumberedName $FileName $count
    }
    $UsedKeys[(Get-PreflightNameCollisionKey $candidate)] = $true
    return $candidate
}

function Get-SelectedFastqItemsForPreflight {
    $selected = @()
    if ($null -eq $script:Resolved) { return $selected }
    $items = @($script:Resolved.fastq_files)
    foreach ($row in $fastqGrid.Rows) {
        if ($row.IsNewRow) { continue }
        if ([bool]$row.Cells["selected"].Value) {
            $selected += $items[[int]$row.Tag]
        }
    }
    return $selected
}

function Get-SelectedSupplementaryItemsForPreflight {
    $selected = @()
    if ($null -eq $script:Resolved) { return $selected }
    $items = @($script:Resolved.supplementary_files)
    foreach ($row in $suppGrid.Rows) {
        if ($row.IsNewRow) { continue }
        if ([bool]$row.Cells["supp_selected"].Value) {
            $selected += $items[[int]$row.Tag]
        }
    }
    return $selected
}

function Get-PreflightPlannedPaths {
    param([string]$RunOutputDir)
    $paths = @([System.IO.Path]::GetFullPath($RunOutputDir))
    $fastqItems = @(Get-SelectedFastqItemsForPreflight)
    $suppItems = @(Get-SelectedSupplementaryItemsForPreflight)
    $prefix = ConvertTo-GeoGetterSafeName ([System.IO.Path]::GetFileName($RunOutputDir)) -ArtifactPrefix

    if ($fastqItems.Count -gt 0) {
        $paths += (Join-Path $RunOutputDir ("{0}_fastq_manifest.tsv" -f $prefix))
    }
    if ($suppItems.Count -gt 0) {
        $paths += (Join-Path $RunOutputDir ("{0}_supplementary_manifest.tsv" -f $prefix))
    }
    if (($fastqItems.Count + $suppItems.Count) -gt 0) {
        $paths += (Join-Path $RunOutputDir ("{0}_download_log.tsv" -f $prefix))
    }

    $usedKeys = @{}
    foreach ($item in $fastqItems) {
        $fileName = ConvertTo-GeoGetterSafeName ([string]$item.file_name) -DefaultName "download.fastq.gz"
        $fileName = Get-PreflightReservedUniqueName $fileName $usedKeys
        $localPath = Join-Path $RunOutputDir $fileName
        $paths += $localPath
        $paths += ($localPath + ".part")
    }

    foreach ($item in $suppItems) {
        $fileName = ConvertTo-GeoGetterSafeName ([string]$item.name) -DefaultName "geo_supplementary_file"
        $fileName = Get-PreflightReservedUniqueName $fileName $usedKeys
        $localPath = Join-Path $RunOutputDir $fileName
        $paths += $localPath
        $paths += ($localPath + ".part")
    }
    return $paths
}

function Assert-PreflightPathLength {
    param([string[]]$Paths)
    foreach ($path in $Paths) {
        try {
            $fullPath = [System.IO.Path]::GetFullPath($path)
        }
        catch {
            throw ((T "preflightPathTooLong") -f $path)
        }
        if ($fullPath.Length -ge 260) {
            throw ((T "preflightPathTooLong") -f $fullPath)
        }
    }
}

function Test-DownloadPreflight {
    $script:LastPreflightStatus = "running"
    $script:LastPreflightError = ""
    $script:LastPreflightOutputDir = ""
    $script:LastPreflightRequiredBytes = $null
    $script:LastPreflightFreeBytes = $null
    try {
        if (-not $outputBox -or [string]::IsNullOrWhiteSpace($outputBox.Text)) {
            throw (T "preflightOutputRequired")
        }
        $runOutputDir = [System.IO.Path]::GetFullPath([string]$outputBox.Text)
        if ([System.IO.File]::Exists($runOutputDir)) {
            throw ((T "preflightOutputIsFile") -f $runOutputDir)
        }

        $script:LastPreflightOutputDir = $runOutputDir
        Assert-PreflightPathLength @(Get-PreflightPlannedPaths $runOutputDir)

        try {
            [System.IO.Directory]::CreateDirectory($runOutputDir) | Out-Null
        }
        catch {
            throw ((T "preflightCannotCreateOutput") -f ("{0} ({1})" -f $runOutputDir, $_.Exception.Message))
        }

        $probePath = Join-Path $runOutputDir (".geo_getter_preflight_" + [System.Guid]::NewGuid().ToString("N") + ".tmp")
        try {
            [System.IO.File]::WriteAllText($probePath, "ok", $script:Utf8NoBom)
            Remove-Item -LiteralPath $probePath -Force -ErrorAction Stop
        }
        catch {
            Remove-Item -LiteralPath $probePath -Force -ErrorAction SilentlyContinue
            throw ((T "preflightCannotWrite") -f ("{0} ({1})" -f $runOutputDir, $_.Exception.Message))
        }

        $freeBytes = Get-FreeSpaceForPathOrNull $runOutputDir
        $requiredBytes = [Int64](Get-SelectedTotalBytes)
        $script:LastPreflightRequiredBytes = $requiredBytes
        $script:LastPreflightFreeBytes = $freeBytes
        if ($null -ne $freeBytes -and $requiredBytes -gt [Int64]$freeBytes) {
            throw ((T "preflightInsufficientSpace") -f (Format-Bytes $requiredBytes), (Format-Bytes ([Int64]$freeBytes)))
        }

        $script:LastPreflightStatus = "ok"
        return [pscustomobject]@{
            OutputDir = $runOutputDir
            RequiredBytes = $requiredBytes
            FreeBytes = $freeBytes
        }
    }
    catch {
        Set-DownloadPreflightDiagnosticError $_.Exception.Message
        Append-Log ((T "preflightFailedLog") -f $script:LastPreflightError)
        throw
    }
}

function Add-FastqRowsFromResolved {
    $fastqGrid.Rows.Clear()
    if ($null -eq $script:Resolved) { return }
    $items = @($script:Resolved.fastq_files)
    for ($i = 0; $i -lt $items.Count; $i++) {
        $item = $items[$i]
        $geoSample = if ($item.geo_sample_accession) { $item.geo_sample_accession } else { "" }
        $geoTitle = if ($item.geo_sample_title) { $item.geo_sample_title } else { "" }
        $rowIndex = $fastqGrid.Rows.Add($false, $item.run_accession, $geoSample, $geoTitle, $item.file_name, $item.sample_accession, $item.library_layout, (Format-Bytes ([Int64]$item.size_bytes)), $item.expected_md5, $item.url, $i, ([Int64]$item.size_bytes), ([Int64]$item.file_index))
        $fastqGrid.Rows[$rowIndex].Tag = $i
    }
    if ($fastqGrid.Rows.Count -gt 0) {
        $fastqGrid.Sort($fastqGrid.Columns["run"], [System.ComponentModel.ListSortDirection]::Ascending)
        $fastqGrid.ClearSelection()
    }
}

function Add-SupplementaryRowsFromResolved {
    $suppGrid.Rows.Clear()
    if ($null -eq $script:Resolved) { return }
    $items = @($script:Resolved.supplementary_files)
    for ($i = 0; $i -lt $items.Count; $i++) {
        $item = $items[$i]
        $rowIndex = $suppGrid.Rows.Add($false, $item.scope, $item.name, $item.url, $i)
        $suppGrid.Rows[$rowIndex].Tag = $i
    }
    if ($suppGrid.Rows.Count -gt 0) {
        $suppGrid.Sort($suppGrid.Columns["supp_name"], [System.ComponentModel.ListSortDirection]::Ascending)
        $suppGrid.ClearSelection()
    }
}

function Set-Busy {
    param([bool]$Busy)
    $fetchButton.Enabled = -not $Busy
    $downloadButton.Enabled = -not $Busy
    $browseButton.Enabled = -not $Busy
    if ($diagnosticsButton) { $diagnosticsButton.Enabled = -not $Busy }
    if ($verifyManifestMenuItem) { $verifyManifestMenuItem.Enabled = -not $Busy }
    if ($fastqSelectAllButton) { $fastqSelectAllButton.Enabled = -not $Busy }
    if ($fastqClearSelectionButton) { $fastqClearSelectionButton.Enabled = -not $Busy }
    if ($suppSelectAllButton) { $suppSelectAllButton.Enabled = -not $Busy }
    if ($suppClearSelectionButton) { $suppClearSelectionButton.Enabled = -not $Busy }
    Update-CancelButton
}

function Update-CancelButton {
    if ($null -eq $cancelButton) { return }
    $cancelButton.Enabled = (
        ($null -ne $script:DownloadProcess -and -not $script:DownloadProcess.HasExited) -or
        ($null -ne $script:VerifyProcess -and -not $script:VerifyProcess.HasExited)
    )
}

function Handle-DownloadLine {
    param([string]$Line)
    if ([string]::IsNullOrWhiteSpace($Line)) { return }
    try {
        $event = $Line | ConvertFrom-Json
        if ($event.event -eq "progress") {
            $total = [Int64]$event.total
            $downloaded = [Int64]$event.downloaded
            $progressBar.Value = if ($total -gt 0) { [Math]::Min(100, [int](($downloaded / $total) * 100)) } else { 0 }
            $statusLabel.Text = T "downloading"
        }
        elseif ($event.event -eq "message") {
            Append-Log $event.message
        }
        elseif ($event.event -eq "done") {
            $script:LastDownloadDoneEvent = $event
            $progressBar.Value = 100
            if ($event.fastq_manifest) { Append-Log ((T "fastqManifestLog") -f $event.fastq_manifest) }
            if ($event.supplementary_manifest) { Append-Log ((T "supplementaryManifestLog") -f $event.supplementary_manifest) }
            Append-Log ((T "downloadLogLog") -f $event.download_log)
            Complete-DownloadIfReady
        }
        else {
            Append-Log $Line
        }
    }
    catch {
        Append-Log $Line
    }
}

function Format-VerificationStatusCounts {
    param([object]$Counts)
    if ($null -eq $Counts) { return "" }
    $parts = @()
    $seen = @{}
    foreach ($name in @("md5_verified", "md5_unavailable", "missing", "size_mismatch", "md5_mismatch")) {
        $property = @($Counts.PSObject.Properties | Where-Object { $_.Name -eq $name } | Select-Object -First 1)
        if ($property.Count -gt 0) {
            $parts += ("{0}={1}" -f $name, $property[0].Value)
            $seen[$name] = $true
        }
    }
    foreach ($property in $Counts.PSObject.Properties) {
        if (-not $seen.ContainsKey($property.Name)) {
            $parts += ("{0}={1}" -f $property.Name, $property.Value)
        }
    }
    return ($parts -join ", ")
}

function Handle-ManifestVerificationLine {
    param([string]$Line)
    if ([string]::IsNullOrWhiteSpace($Line)) { return }
    try {
        $event = $Line | ConvertFrom-Json
        if ($event.event -eq "done" -and $event.kind -eq "manifest_verification") {
            $script:LastVerificationDoneEvent = $event
            $progressBar.Style = "Continuous"
            $progressBar.Value = 100
            if ($event.report) { Append-Log ((T "verifyManifestReportLog") -f $event.report) }
            Append-Log ((T "verifyManifestSummaryLog") -f (Format-VerificationStatusCounts $event.status_counts))
            Complete-ManifestVerificationIfReady
        }
        else {
            Append-Log $Line
        }
    }
    catch {
        Append-Log $Line
    }
}

function Show-ManifestVerificationOpenDialog {
    $dialog = New-Object System.Windows.Forms.OpenFileDialog
    $dialog.Title = T "verifyManifestDialogTitle"
    $dialog.Filter = T "verifyManifestFilter"
    $dialog.CheckFileExists = $true
    $dialog.Multiselect = $false
    if ($outputBox -and -not [string]::IsNullOrWhiteSpace($outputBox.Text) -and [System.IO.Directory]::Exists($outputBox.Text)) {
        $dialog.InitialDirectory = $outputBox.Text
    }
    if ($dialog.ShowDialog($form) -ne "OK") { return }

    try {
        Set-Busy $true
        $progressBar.Style = "Marquee"
        $progressBar.MarqueeAnimationSpeed = 30
        $progressBar.Value = 0
        $statusLabel.Text = T "verifyingManifest"
        Start-ManifestVerificationProcess $dialog.FileName
    }
    catch {
        $progressBar.Style = "Continuous"
        $progressBar.Value = 0
        Set-Busy $false
        Show-AppError $_.Exception.Message
    }
}

function Get-DownloadFinalStatusKey {
    param(
        [object]$DoneEvent,
        [object]$ExitCode,
        [bool]$Canceled
    )
    if ($Canceled) { return "canceled" }
    if ($null -eq $DoneEvent) { return "error" }

    $statuses = @()
    if ($DoneEvent.PSObject.Properties.Name -contains "statuses") {
        $statuses = @($DoneEvent.statuses) | Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_) }
    }
    if ($statuses.Count -eq 0) { return "error" }

    $okStatuses = @("md5_verified", "download_complete")
    $hasUnverified = $false
    foreach ($status in $statuses) {
        $text = [string]$status
        if ($text -eq "md5_unavailable") {
            $hasUnverified = $true
            continue
        }
        if ($okStatuses -notcontains $text) {
            return "completePartial"
        }
    }
    if ($hasUnverified) { return "completeUnverified" }
    if ($ExitCode -eq 0) { return "complete" }
    return "completePartial"
}

function Complete-DownloadIfReady {
    if ($script:DownloadFinalized) { return }
    if (-not $script:DownloadExitObserved -or -not $script:DownloadStdoutClosed) { return }

    $script:DownloadFinalized = $true
    Set-Busy $false
    $script:DownloadProcess = $null
    Update-CancelButton
    $statusKey = Get-DownloadFinalStatusKey $script:LastDownloadDoneEvent $script:LastDownloadExitCode $script:DownloadCanceled
    $statusLabel.Text = T $statusKey
    if ($statusKey -eq "error") {
        $progressBar.Value = 0
        if ($null -eq $script:LastDiagnosticError) {
            Set-DiagnosticErrorFromProcessOutput "download" "selected-download-json" $script:LastDownloadExitCode $script:DownloadStdoutText $script:DownloadStderrText "download_failed_before_done" "Download process ended before the done event."
        }
    }
}

function Complete-ManifestVerificationIfReady {
    if ($script:VerifyFinalized) { return }
    if (-not $script:VerifyExitObserved -or -not $script:VerifyStdoutClosed) { return }

    $script:VerifyFinalized = $true
    $progressBar.Style = "Continuous"
    Set-Busy $false
    $script:VerifyProcess = $null
    Update-CancelButton
    if ($script:VerifyCanceled) {
        $statusLabel.Text = T "canceled"
        return
    }
    if ($null -eq $script:LastVerificationDoneEvent) {
        $progressBar.Value = 0
        $statusLabel.Text = T "error"
        if ($null -eq $script:LastDiagnosticError) {
            Set-DiagnosticErrorFromProcessOutput "verification" "verify-manifest-json" $script:LastVerificationExitCode $script:VerifyStdoutText $script:VerifyStderrText "verification_failed_before_report" (T "verifyManifestNoReport")
        }
        Append-Log (T "verifyManifestNoReport")
        return
    }
    $message = if ($script:LastVerificationExitCode -eq 0) {
        $statusLabel.Text = T "complete"
        (T "verifyManifestCompleteMessage") -f $script:LastVerificationDoneEvent.report
    }
    else {
        $statusLabel.Text = T "completePartial"
        (T "verifyManifestPartialMessage") -f $script:LastVerificationDoneEvent.report
    }
    if (-not $SelfTest) {
        $icon = if ($script:LastVerificationExitCode -eq 0) { "Information" } else { "Warning" }
        [System.Windows.Forms.MessageBox]::Show($message, (T "verifyManifestDialogTitle"), "OK", $icon) | Out-Null
    }
}

function Start-ResolveProcess {
    param([string]$InputText)
    if ($null -ne $script:ResolveProcess -and -not $script:ResolveProcess.HasExited) {
        throw (T "resolveAlreadyRunning")
    }
    $script:LastInputText = $InputText
    $script:ResolveInputPath = New-ResolveInputFile $InputText
    $script:ResolveStdoutText = ""
    $script:ResolveStderrText = ""
    $script:LastResolveExitCode = $null
    $script:LastDiagnosticError = $null
    $process = New-Object System.Diagnostics.Process
    try {
        $script:LastResolveArguments = Get-ResolvePythonArguments $script:ResolveInputPath
        $process.StartInfo = New-ResolveProcessStartInfo $script:ResolveInputPath
        $process.EnableRaisingEvents = $true
        $script:ResolveBridge = New-Object GeoGetterProcessUiBridge -ArgumentList @(
            $form,
            ([System.Action[string]]{
                param($line)
                $script:ResolveStdoutText += $line + [Environment]::NewLine
            }),
            ([System.Action[string]]{
                param($line)
                $script:ResolveStderrText += $line + [Environment]::NewLine
            }),
            ([System.Action[int]]{
                param($code)
                try {
                    Complete-ResolveProcess $code
                }
                catch {
                    try {
                        $progressBar.Style = "Continuous"
                        $progressBar.Value = 0
                        Set-Busy $false
                        Show-AppError $_.Exception.Message
                    }
                    catch { }
                }
            })
        )
        $script:ResolveBridge.Attach($process)
        $script:ResolveProcess = $process
        [void]$process.Start()
        $process.BeginOutputReadLine()
        $process.BeginErrorReadLine()
    }
    catch {
        if ($script:ResolveInputPath) {
            Remove-Item -LiteralPath $script:ResolveInputPath -ErrorAction SilentlyContinue
        }
        $script:ResolveInputPath = $null
        $script:ResolveProcess = $null
        throw
    }
}

function Start-ManifestVerificationProcess {
    param([string]$ManifestPath)
    if ($null -ne $script:VerifyProcess -and -not $script:VerifyProcess.HasExited) {
        throw (T "verifyManifestAlreadyRunning")
    }
    $script:VerifyCanceled = $false
    Clear-VerificationDiagnosticState
    $script:LastDiagnosticError = $null
    $script:LastVerificationArguments = Get-VerifyManifestPythonArguments $ManifestPath
    Append-Log ((T "verifyManifestStartedLog") -f $ManifestPath)

    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = New-VerifyManifestProcessStartInfo $ManifestPath
    $process.EnableRaisingEvents = $true
    $script:VerifyBridge = New-Object GeoGetterProcessUiBridge -ArgumentList @(
        $form,
        ([System.Action[string]]{
            param($line)
            Add-DiagnosticVerificationOutput "stdout" $line
            try {
                Handle-ManifestVerificationLine $line
            }
            catch {
                try { Append-Log ((T "progressDisplayError") -f $_.Exception.Message) } catch { }
            }
        }),
        ([System.Action[string]]{
            param($line)
            Add-DiagnosticVerificationOutput "stderr" $line
            try {
                Append-Log $line
            }
            catch { }
        }),
        ([System.Action[int]]{
            param($code)
            try {
                $script:LastVerificationExitCode = $code
                $script:VerifyExitObserved = $true
                Complete-ManifestVerificationIfReady
            }
            catch {
                try { Append-Log ((T "exitHandlerError") -f $_.Exception.Message) } catch { }
            }
        }),
        ([System.Action]{
            try {
                $script:VerifyStdoutClosed = $true
                Complete-ManifestVerificationIfReady
            }
            catch {
                try { Append-Log ((T "exitHandlerError") -f $_.Exception.Message) } catch { }
            }
        })
    )
    $script:VerifyBridge.Attach($process)
    $script:VerifyProcess = $process
    try {
        [void]$process.Start()
    }
    catch {
        $script:VerifyProcess = $null
        Update-CancelButton
        throw
    }
    Update-CancelButton
    $process.BeginOutputReadLine()
    $process.BeginErrorReadLine()
}

function Start-DownloadProcess {
    $script:DownloadCanceled = $false
    Clear-DownloadDiagnosticState
    Clear-VerificationDiagnosticState
    $script:LastDiagnosticError = $null
    try {
        Assert-ResolvedMatchesCurrentInput
        Assert-AnySelection
    }
    catch {
        Set-DownloadPreflightDiagnosticError $_.Exception.Message $true
        throw
    }
    Test-DownloadPreflight | Out-Null

    $fastqIndices = Get-SelectedFastqIndicesOrEmpty
    $suppIndices = Get-SelectedSuppIndicesOrEmpty
    $script:LastDownloadArguments = Get-DownloadPythonArguments $fastqIndices $suppIndices
    $psi = New-DownloadProcessStartInfo $fastqIndices $suppIndices
    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $psi
    $process.EnableRaisingEvents = $true
    Set-Busy $true
    $progressBar.Style = "Continuous"
    $progressBar.Value = 0
    $statusLabel.Text = T "downloading"
    $script:DownloadBridge = New-Object GeoGetterProcessUiBridge -ArgumentList @(
        $form,
        ([System.Action[string]]{
            param($line)
            Add-DiagnosticProcessOutput "stdout" $line
            try {
                Handle-DownloadLine $line
            }
            catch {
                try { Append-Log ((T "progressDisplayError") -f $_.Exception.Message) } catch { }
            }
        }),
        ([System.Action[string]]{
            param($line)
            Add-DiagnosticProcessOutput "stderr" $line
            try {
                Append-Log $line
            }
            catch { }
        }),
        ([System.Action[int]]{
            param($code)
            try {
                $script:LastDownloadExitCode = $code
                $script:DownloadExitObserved = $true
                Complete-DownloadIfReady
            }
            catch {
                try { Append-Log ((T "exitHandlerError") -f $_.Exception.Message) } catch { }
            }
        }),
        ([System.Action]{
            try {
                $script:DownloadStdoutClosed = $true
                Complete-DownloadIfReady
            }
            catch {
                try { Append-Log ((T "exitHandlerError") -f $_.Exception.Message) } catch { }
            }
        })
    )
    $script:DownloadBridge.Attach($process)
    try {
        [void]$process.Start()
    }
    catch {
        $script:DownloadProcess = $null
        $script:LastDownloadStartError = $_.Exception.Message
        $script:LastDiagnosticError = New-DiagnosticError "download_process_start" "selected-download-json" "process_start_failed" $script:LastDownloadStartError $script:LastDownloadStartError "process_start" $null
        Update-CancelButton
        throw
    }
    $script:DownloadProcess = $process
    Update-CancelButton
    $process.BeginOutputReadLine()
    $process.BeginErrorReadLine()
}

function New-ResolveProcessStartInfo {
    param([string]$InputPath)
    return New-PythonProcessStartInfo -Arguments (Get-ResolvePythonArguments $InputPath)
}

function New-DownloadProcessStartInfo {
    param(
        [string]$FastqIndices,
        [string]$SuppIndices
    )
    return New-PythonProcessStartInfo -Arguments (Get-DownloadPythonArguments $FastqIndices $SuppIndices)
}

function New-VerifyManifestProcessStartInfo {
    param([string]$ManifestPath)
    return New-PythonProcessStartInfo -Arguments (Get-VerifyManifestPythonArguments $ManifestPath)
}

function Get-ResolvePythonArguments {
    param([string]$InputPath)
    return @("-m", "geo_getter.cli", "resolve-json", "--input-file", $InputPath, "--out-json", $script:ResolvedJsonPath)
}

function Get-DownloadPythonArguments {
    param(
        [string]$FastqIndices,
        [string]$SuppIndices
    )
    return @("-m", "geo_getter.cli", "selected-download-json", "--input-json", $script:ResolvedJsonPath, "--fastq-indices", $FastqIndices, "--supp-indices", $SuppIndices, "--out", $outputBox.Text)
}

function Get-VerifyManifestPythonArguments {
    param([string]$ManifestPath)
    return @("-m", "geo_getter.cli", "verify-manifest-json", "--manifest", $ManifestPath)
}

function New-PythonProcessStartInfo {
    param([string[]]$Arguments)
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $PythonExe
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.StandardOutputEncoding = [System.Text.Encoding]::UTF8
    $psi.StandardErrorEncoding = [System.Text.Encoding]::UTF8
    Set-ProcessEnvironment $psi "PYTHONPATH" $env:PYTHONPATH
    Set-ProcessEnvironment $psi "PYTHONIOENCODING" "utf-8"
    $psi.Arguments = ($Arguments | ForEach-Object { ConvertTo-ProcessArgument $_ }) -join " "
    return $psi
}

function Invoke-PythonCli {
    param([string[]]$Arguments)
    $psi = New-PythonProcessStartInfo -Arguments $Arguments
    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $psi
    [void]$process.Start()
    $stdout = $process.StandardOutput.ReadToEnd()
    $stderr = $process.StandardError.ReadToEnd()
    $process.WaitForExit()
    return [pscustomobject]@{
        ExitCode = $process.ExitCode
        Stdout = $stdout
        Stderr = $stderr
    }
}

function Join-ProcessOutput {
    param([object]$Result)
    $parts = @()
    if (-not [string]::IsNullOrWhiteSpace($Result.Stdout)) { $parts += $Result.Stdout.TrimEnd() }
    if (-not [string]::IsNullOrWhiteSpace($Result.Stderr)) { $parts += $Result.Stderr.TrimEnd() }
    return ($parts -join [Environment]::NewLine)
}

function Set-ProcessEnvironment {
    param(
        [System.Diagnostics.ProcessStartInfo]$ProcessStartInfo,
        [string]$Name,
        [string]$Value
    )
    if ($null -ne $ProcessStartInfo.Environment) {
        $ProcessStartInfo.Environment[$Name] = $Value
        return
    }
    if ($null -ne $ProcessStartInfo.EnvironmentVariables) {
        $ProcessStartInfo.EnvironmentVariables[$Name] = $Value
        return
    }
    throw (T "processEnvError")
}

function Invoke-SelectedDownloadJsonForSelfTest {
    param(
        [string]$FastqIndices,
        [string]$SuppIndices
    )
    $psi = New-DownloadProcessStartInfo $FastqIndices $SuppIndices
    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $psi
    [void]$process.Start()
    $stdout = $process.StandardOutput.ReadToEnd()
    $stderr = $process.StandardError.ReadToEnd()
    $process.WaitForExit()
    return [pscustomobject]@{
        ExitCode = $process.ExitCode
        Stdout = $stdout
        Stderr = $stderr
    }
}

function Invoke-VerifyManifestJsonForSelfTest {
    param([string]$ManifestPath)
    $psi = New-VerifyManifestProcessStartInfo $ManifestPath
    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $psi
    [void]$process.Start()
    $stdout = $process.StandardOutput.ReadToEnd()
    $stderr = $process.StandardError.ReadToEnd()
    $process.WaitForExit()
    return [pscustomobject]@{
        ExitCode = $process.ExitCode
        Stdout = $stdout
        Stderr = $stderr
    }
}

function ConvertTo-ProcessArgument {
    param([string]$Value)
    if ($Value -eq "") {
        return '""'
    }
    if ($Value -notmatch '[\s"]') {
        return $Value
    }
    return '"' + ($Value -replace '"', '\"') + '"'
}

function Assert-Equal {
    param(
        [object]$Actual,
        [object]$Expected,
        [string]$Name
    )
    if ($Actual -ne $Expected) {
        throw "$Name failed. expected=[$Expected] actual=[$Actual]"
    }
}

function Assert-Contains {
    param(
        [string]$Actual,
        [string]$ExpectedPart,
        [string]$Name
    )
    if (-not $Actual.Contains($ExpectedPart)) {
        throw "$Name failed. expected part=[$ExpectedPart] actual=[$Actual]"
    }
}

function Compare-AccessionText {
    param(
        [string]$Left,
        [string]$Right
    )
    $leftMatch = [regex]::Match($Left, '^([A-Za-z]+)(\d+)$')
    $rightMatch = [regex]::Match($Right, '^([A-Za-z]+)(\d+)$')
    if ($leftMatch.Success -and $rightMatch.Success -and $leftMatch.Groups[1].Value -eq $rightMatch.Groups[1].Value) {
        $prefixCompare = [string]::Compare($leftMatch.Groups[1].Value, $rightMatch.Groups[1].Value, $true)
        if ($prefixCompare -ne 0) { return $prefixCompare }
        $leftNumber = [Int64]$leftMatch.Groups[2].Value
        $rightNumber = [Int64]$rightMatch.Groups[2].Value
        return $leftNumber.CompareTo($rightNumber)
    }
    return [string]::Compare($Left, $Right, $true)
}

function Compare-GridString {
    param(
        [object]$Left,
        [object]$Right
    )
    return [string]::Compare(([string]$Left), ([string]$Right), $true)
}

function Save-FormScreenshot {
    param([string]$Path)
    $fullPath = [System.IO.Path]::GetFullPath($Path)
    [System.IO.Directory]::CreateDirectory([System.IO.Path]::GetDirectoryName($fullPath)) | Out-Null
    $form.Show()
    $form.Refresh()
    [System.Windows.Forms.Application]::DoEvents()
    Start-Sleep -Milliseconds 300
    $bitmap = New-Object System.Drawing.Bitmap($form.Width, $form.Height)
    try {
        $form.DrawToBitmap($bitmap, (New-Object System.Drawing.Rectangle(0, 0, $form.Width, $form.Height)))
        $bitmap.Save($fullPath, [System.Drawing.Imaging.ImageFormat]::Png)
    }
    finally {
        $bitmap.Dispose()
    }
    Write-Output "screenshot: $fullPath"
}

function New-MainForm {
    $formLocal = New-Object System.Windows.Forms.Form
    $formLocal.Text = "GEOGetter"
    $formLocal.Size = New-Object System.Drawing.Size(1180, 760)
    $formLocal.MinimumSize = New-Object System.Drawing.Size(900, 640)
    $formLocal.StartPosition = "CenterScreen"
    $anchorTopLeftRight = [System.Windows.Forms.AnchorStyles]::Top -bor [System.Windows.Forms.AnchorStyles]::Left -bor [System.Windows.Forms.AnchorStyles]::Right
    $anchorTopRight = [System.Windows.Forms.AnchorStyles]::Top -bor [System.Windows.Forms.AnchorStyles]::Right

    $rootLayout = New-Object System.Windows.Forms.TableLayoutPanel
    $rootLayout.Dock = "Fill"
    $rootLayout.ColumnCount = 1
    $rootLayout.RowCount = 4
    [void]$rootLayout.ColumnStyles.Add((New-Object System.Windows.Forms.ColumnStyle([System.Windows.Forms.SizeType]::Percent, 100)))
    [void]$rootLayout.RowStyles.Add((New-Object System.Windows.Forms.RowStyle([System.Windows.Forms.SizeType]::Absolute, 26)))
    [void]$rootLayout.RowStyles.Add((New-Object System.Windows.Forms.RowStyle([System.Windows.Forms.SizeType]::Absolute, 138)))
    [void]$rootLayout.RowStyles.Add((New-Object System.Windows.Forms.RowStyle([System.Windows.Forms.SizeType]::Percent, 100)))
    [void]$rootLayout.RowStyles.Add((New-Object System.Windows.Forms.RowStyle([System.Windows.Forms.SizeType]::Absolute, 160)))
    $formLocal.Controls.Add($rootLayout)

    $script:menuStrip = New-Object System.Windows.Forms.MenuStrip
    $menuStrip.Dock = "Fill"
    $script:settingsMenuItem = New-Object System.Windows.Forms.ToolStripMenuItem
    $script:languageMenuItem = New-Object System.Windows.Forms.ToolStripMenuItem
    $script:japaneseMenuItem = New-Object System.Windows.Forms.ToolStripMenuItem
    $script:englishMenuItem = New-Object System.Windows.Forms.ToolStripMenuItem
    $script:toolsMenuItem = New-Object System.Windows.Forms.ToolStripMenuItem
    $script:verifyManifestMenuItem = New-Object System.Windows.Forms.ToolStripMenuItem
    $script:helpMenuItem = New-Object System.Windows.Forms.ToolStripMenuItem
    $script:helpOpenMenuItem = New-Object System.Windows.Forms.ToolStripMenuItem
    $script:aboutMenuItem = New-Object System.Windows.Forms.ToolStripMenuItem
    [void]$languageMenuItem.DropDownItems.Add($japaneseMenuItem)
    [void]$languageMenuItem.DropDownItems.Add($englishMenuItem)
    [void]$settingsMenuItem.DropDownItems.Add($languageMenuItem)
    [void]$toolsMenuItem.DropDownItems.Add($verifyManifestMenuItem)
    [void]$helpMenuItem.DropDownItems.Add($helpOpenMenuItem)
    [void]$helpMenuItem.DropDownItems.Add((New-Object System.Windows.Forms.ToolStripSeparator))
    [void]$helpMenuItem.DropDownItems.Add($aboutMenuItem)
    [void]$menuStrip.Items.Add($settingsMenuItem)
    [void]$menuStrip.Items.Add($toolsMenuItem)
    [void]$menuStrip.Items.Add($helpMenuItem)
    $rootLayout.Controls.Add($menuStrip, 0, 0)

    $topPanel = New-Object System.Windows.Forms.Panel
    $topPanel.Dock = "Fill"
    $topPanel.Height = 138
    $rootLayout.Controls.Add($topPanel, 0, 1)

    $script:inputLabel = New-Object System.Windows.Forms.Label
    $inputLabel.Text = "GEO accession / URL"
    $inputLabel.Location = New-Object System.Drawing.Point(10, 13)
    $inputLabel.Size = New-Object System.Drawing.Size(130, 22)
    $topPanel.Controls.Add($inputLabel)

    $script:inputBox = New-Object System.Windows.Forms.TextBox
    $inputBox.Location = New-Object System.Drawing.Point(145, 10)
    $inputBox.Size = New-Object System.Drawing.Size(810, 24)
    $inputBox.Anchor = $anchorTopLeftRight
    $inputBox.Text = "GSE30567"
    $topPanel.Controls.Add($inputBox)

    $script:fetchButton = New-Object System.Windows.Forms.Button
    $fetchButton.Text = "Find files"
    $fetchButton.Location = New-Object System.Drawing.Point(965, 8)
    $fetchButton.Size = New-Object System.Drawing.Size(170, 28)
    $fetchButton.Anchor = $anchorTopRight
    $topPanel.Controls.Add($fetchButton)

    $script:outputLabel = New-Object System.Windows.Forms.Label
    $outputLabel.Text = "Output folder"
    $outputLabel.Location = New-Object System.Drawing.Point(10, 50)
    $outputLabel.Size = New-Object System.Drawing.Size(130, 22)
    $topPanel.Controls.Add($outputLabel)

    $script:outputBox = New-Object System.Windows.Forms.TextBox
    $outputBox.Location = New-Object System.Drawing.Point(145, 47)
    $outputBox.Size = New-Object System.Drawing.Size(630, 24)
    $outputBox.Anchor = $anchorTopLeftRight
    $outputBox.ReadOnly = $true
    $outputBox.BackColor = [System.Drawing.SystemColors]::Window
    $outputBox.Text = Get-DefaultOutputFolder
    $topPanel.Controls.Add($outputBox)

    $script:browseButton = New-Object System.Windows.Forms.Button
    $browseButton.Text = "Browse"
    $browseButton.Location = New-Object System.Drawing.Point(785, 45)
    $browseButton.Size = New-Object System.Drawing.Size(70, 28)
    $browseButton.Anchor = $anchorTopRight
    $topPanel.Controls.Add($browseButton)

    $script:capacityLabel = New-Object System.Windows.Forms.Label
    $capacityLabel.Text = "Required: - / Free: -"
    $capacityLabel.Location = New-Object System.Drawing.Point(870, 50)
    $capacityLabel.Size = New-Object System.Drawing.Size(280, 22)
    $capacityLabel.Anchor = $anchorTopRight
    $topPanel.Controls.Add($capacityLabel)

    $script:datasetTitleLabel = New-Object System.Windows.Forms.Label
    $datasetTitleLabel.Text = "GEO info"
    $datasetTitleLabel.Location = New-Object System.Drawing.Point(10, 86)
    $datasetTitleLabel.Size = New-Object System.Drawing.Size(130, 22)
    $topPanel.Controls.Add($datasetTitleLabel)

    $script:geoInfoPanel = New-Object System.Windows.Forms.TableLayoutPanel
    $geoInfoPanel.Location = New-Object System.Drawing.Point(145, 78)
    $geoInfoPanel.Size = New-Object System.Drawing.Size(990, 52)
    $geoInfoPanel.Anchor = $anchorTopLeftRight
    $geoInfoPanel.ColumnCount = 3
    $geoInfoPanel.RowCount = 2
    $geoInfoPanel.CellBorderStyle = "Single"
    $geoInfoPanel.BackColor = [System.Drawing.SystemColors]::Window
    [void]$geoInfoPanel.ColumnStyles.Add((New-Object System.Windows.Forms.ColumnStyle([System.Windows.Forms.SizeType]::Percent, 18)))
    [void]$geoInfoPanel.ColumnStyles.Add((New-Object System.Windows.Forms.ColumnStyle([System.Windows.Forms.SizeType]::Percent, 52)))
    [void]$geoInfoPanel.ColumnStyles.Add((New-Object System.Windows.Forms.ColumnStyle([System.Windows.Forms.SizeType]::Percent, 30)))
    [void]$geoInfoPanel.RowStyles.Add((New-Object System.Windows.Forms.RowStyle([System.Windows.Forms.SizeType]::Absolute, 18)))
    [void]$geoInfoPanel.RowStyles.Add((New-Object System.Windows.Forms.RowStyle([System.Windows.Forms.SizeType]::Percent, 100)))
    $topPanel.Controls.Add($geoInfoPanel)

    $geoInfoColumns = @(
        @("accession", "Accession"),
        @("organism", "Organism"),
        @("status", "Status")
    )
    for ($i = 0; $i -lt $geoInfoColumns.Count; $i++) {
        $header = New-Object System.Windows.Forms.Label
        $header.Text = $geoInfoColumns[$i][1]
        $header.Dock = "Fill"
        $header.TextAlign = "MiddleLeft"
        $header.Margin = New-Object System.Windows.Forms.Padding(5, 0, 5, 0)
        $header.ForeColor = [System.Drawing.SystemColors]::GrayText
        $geoInfoPanel.Controls.Add($header, $i, 0)

        $valueLabel = New-Object System.Windows.Forms.Label
        $valueLabel.Text = "-"
        $valueLabel.Dock = "Fill"
        $valueLabel.TextAlign = "MiddleLeft"
        $valueLabel.Margin = New-Object System.Windows.Forms.Padding(5, 0, 5, 0)
        $valueLabel.AutoEllipsis = $true
        $geoInfoPanel.Controls.Add($valueLabel, $i, 1)

        if ($geoInfoColumns[$i][0] -eq "accession") { $script:geoAccessionValueLabel = $valueLabel }
        if ($geoInfoColumns[$i][0] -eq "organism") { $script:geoOrganismValueLabel = $valueLabel }
        if ($geoInfoColumns[$i][0] -eq "status") { $script:geoStatusValueLabel = $valueLabel }
    }

    $split = New-Object System.Windows.Forms.SplitContainer
    $split.Dock = "Fill"
    $split.Orientation = "Horizontal"
    $split.SplitterDistance = 380
    $rootLayout.Controls.Add($split, 0, 2)

    $fastqPanel = New-Object System.Windows.Forms.TableLayoutPanel
    $fastqPanel.Dock = "Fill"
    $fastqPanel.Padding = New-Object System.Windows.Forms.Padding(0)
    $fastqPanel.ColumnCount = 1
    $fastqPanel.RowCount = 2
    [void]$fastqPanel.ColumnStyles.Add((New-Object System.Windows.Forms.ColumnStyle([System.Windows.Forms.SizeType]::Percent, 100)))
    [void]$fastqPanel.RowStyles.Add((New-Object System.Windows.Forms.RowStyle([System.Windows.Forms.SizeType]::Absolute, 42)))
    [void]$fastqPanel.RowStyles.Add((New-Object System.Windows.Forms.RowStyle([System.Windows.Forms.SizeType]::Percent, 100)))
    $split.Panel1.Controls.Add($fastqPanel)

    $fastqHeaderPanel = New-Object System.Windows.Forms.Panel
    $fastqHeaderPanel.Dock = "Fill"
    $fastqPanel.Controls.Add($fastqHeaderPanel, 0, 0)

    $script:fastqTitle = New-Object System.Windows.Forms.Label
    $fastqTitle.Text = "raw FASTQ (ENA direct FASTQ): 0 files"
    $fastqTitle.Location = New-Object System.Drawing.Point(0, 0)
    $fastqTitle.Size = New-Object System.Drawing.Size(880, 42)
    $fastqTitle.Anchor = $anchorTopLeftRight
    $fastqTitle.TextAlign = "MiddleLeft"
    $fastqHeaderPanel.Controls.Add($fastqTitle)

    $script:fastqSelectAllButton = New-Object System.Windows.Forms.Button
    $fastqSelectAllButton.Text = "Select all"
    $fastqSelectAllButton.Location = New-Object System.Drawing.Point(895, 6)
    $fastqSelectAllButton.Size = New-Object System.Drawing.Size(110, 30)
    $fastqSelectAllButton.Anchor = $anchorTopRight
    $fastqHeaderPanel.Controls.Add($fastqSelectAllButton)

    $script:fastqClearSelectionButton = New-Object System.Windows.Forms.Button
    $fastqClearSelectionButton.Text = "Clear selection"
    $fastqClearSelectionButton.Location = New-Object System.Drawing.Point(1015, 6)
    $fastqClearSelectionButton.Size = New-Object System.Drawing.Size(120, 30)
    $fastqClearSelectionButton.Anchor = $anchorTopRight
    $fastqHeaderPanel.Controls.Add($fastqClearSelectionButton)

    $script:fastqGrid = New-Object System.Windows.Forms.DataGridView
    $fastqGrid.Dock = "Fill"
    $fastqGrid.AllowUserToAddRows = $false
    $fastqGrid.RowHeadersVisible = $false
    $fastqGrid.AutoSizeColumnsMode = "None"
    $fastqGrid.ColumnHeadersVisible = $true
    $fastqGrid.ColumnHeadersHeightSizeMode = "DisableResizing"
    $fastqGrid.ColumnHeadersHeight = 28
    $fastqGrid.SelectionMode = "FullRowSelect"
    $fastqGrid.MultiSelect = $true
    $fastqGrid.BackgroundColor = [System.Drawing.SystemColors]::Window
    $fastqGrid.BorderStyle = "FixedSingle"
    $fastqGrid.EnableHeadersVisualStyles = $false
    $fastqGrid.ColumnHeadersDefaultCellStyle.BackColor = [System.Drawing.SystemColors]::ControlLight
    $fastqGrid.ColumnHeadersDefaultCellStyle.ForeColor = [System.Drawing.SystemColors]::ControlText
    $fastqPanel.Controls.Add($fastqGrid, 0, 1)

    $selectCol = New-Object System.Windows.Forms.DataGridViewCheckBoxColumn
    $selectCol.Name = "selected"
    $selectCol.HeaderText = "Select"
    $selectCol.Width = 55
    $selectCol.SortMode = "NotSortable"
    [void]$fastqGrid.Columns.Add($selectCol)
    foreach ($col in @(
        @("run", "Run", 110),
        @("geo_sample", "GEO Sample", 110),
        @("geo_title", "Sample title", 180),
        @("file_name", "File name", 160),
        @("sample", "ENA Sample", 140),
        @("layout", "Layout", 80),
        @("size", "Size", 100),
        @("md5", "MD5", 230),
        @("url", "FASTQ URL", 430)
    )) {
        $textCol = New-Object System.Windows.Forms.DataGridViewTextBoxColumn
        $textCol.Name = $col[0]
        $textCol.HeaderText = $col[1]
        $textCol.Width = $col[2]
        $textCol.ReadOnly = $true
        $textCol.SortMode = "Automatic"
        [void]$fastqGrid.Columns.Add($textCol)
    }
    foreach ($col in @(
        @("source_index", "source_index"),
        @("size_bytes_raw", "size_bytes_raw"),
        @("file_index_raw", "file_index_raw")
    )) {
        $hiddenCol = New-Object System.Windows.Forms.DataGridViewTextBoxColumn
        $hiddenCol.Name = $col[0]
        $hiddenCol.HeaderText = $col[1]
        $hiddenCol.Visible = $false
        [void]$fastqGrid.Columns.Add($hiddenCol)
    }

    $suppPanel = New-Object System.Windows.Forms.TableLayoutPanel
    $suppPanel.Dock = "Fill"
    $suppPanel.Padding = New-Object System.Windows.Forms.Padding(0)
    $suppPanel.ColumnCount = 1
    $suppPanel.RowCount = 2
    [void]$suppPanel.ColumnStyles.Add((New-Object System.Windows.Forms.ColumnStyle([System.Windows.Forms.SizeType]::Percent, 100)))
    [void]$suppPanel.RowStyles.Add((New-Object System.Windows.Forms.RowStyle([System.Windows.Forms.SizeType]::Absolute, 42)))
    [void]$suppPanel.RowStyles.Add((New-Object System.Windows.Forms.RowStyle([System.Windows.Forms.SizeType]::Percent, 100)))
    $split.Panel2.Controls.Add($suppPanel)

    $suppHeaderPanel = New-Object System.Windows.Forms.Panel
    $suppHeaderPanel.Dock = "Fill"
    $suppPanel.Controls.Add($suppHeaderPanel, 0, 0)

    $script:suppTitle = New-Object System.Windows.Forms.Label
    $suppTitle.Text = "GEO supplementary / processed files (not raw FASTQ): 0 files"
    $suppTitle.Location = New-Object System.Drawing.Point(0, 0)
    $suppTitle.Size = New-Object System.Drawing.Size(880, 42)
    $suppTitle.Anchor = $anchorTopLeftRight
    $suppTitle.TextAlign = "MiddleLeft"
    $suppHeaderPanel.Controls.Add($suppTitle)

    $script:suppSelectAllButton = New-Object System.Windows.Forms.Button
    $suppSelectAllButton.Text = "Select all"
    $suppSelectAllButton.Location = New-Object System.Drawing.Point(895, 6)
    $suppSelectAllButton.Size = New-Object System.Drawing.Size(110, 30)
    $suppSelectAllButton.Anchor = $anchorTopRight
    $suppHeaderPanel.Controls.Add($suppSelectAllButton)

    $script:suppClearSelectionButton = New-Object System.Windows.Forms.Button
    $suppClearSelectionButton.Text = "Clear selection"
    $suppClearSelectionButton.Location = New-Object System.Drawing.Point(1015, 6)
    $suppClearSelectionButton.Size = New-Object System.Drawing.Size(120, 30)
    $suppClearSelectionButton.Anchor = $anchorTopRight
    $suppHeaderPanel.Controls.Add($suppClearSelectionButton)

    $script:suppGrid = New-Object System.Windows.Forms.DataGridView
    $suppGrid.Dock = "Fill"
    $suppGrid.AllowUserToAddRows = $false
    $suppGrid.RowHeadersVisible = $false
    $suppGrid.ReadOnly = $false
    $suppGrid.ColumnHeadersVisible = $true
    $suppGrid.ColumnHeadersHeightSizeMode = "DisableResizing"
    $suppGrid.ColumnHeadersHeight = 28
    $suppGrid.SelectionMode = "FullRowSelect"
    $suppGrid.BackgroundColor = [System.Drawing.SystemColors]::Window
    $suppGrid.BorderStyle = "FixedSingle"
    $suppGrid.EnableHeadersVisualStyles = $false
    $suppGrid.ColumnHeadersDefaultCellStyle.BackColor = [System.Drawing.SystemColors]::ControlLight
    $suppGrid.ColumnHeadersDefaultCellStyle.ForeColor = [System.Drawing.SystemColors]::ControlText
    $suppPanel.Controls.Add($suppGrid, 0, 1)
    $suppSelectCol = New-Object System.Windows.Forms.DataGridViewCheckBoxColumn
    $suppSelectCol.Name = "supp_selected"
    $suppSelectCol.HeaderText = "Select"
    $suppSelectCol.Width = 55
    $suppSelectCol.SortMode = "NotSortable"
    [void]$suppGrid.Columns.Add($suppSelectCol)
    foreach ($col in @(
        @("supp_scope", "Type", 230),
        @("supp_name", "File name", 310),
        @("supp_url", "GEO URL", 560)
    )) {
        $textCol = New-Object System.Windows.Forms.DataGridViewTextBoxColumn
        $textCol.Name = $col[0]
        $textCol.HeaderText = $col[1]
        $textCol.Width = $col[2]
        $textCol.ReadOnly = $true
        $textCol.SortMode = "Automatic"
        [void]$suppGrid.Columns.Add($textCol)
    }
    $suppIndexCol = New-Object System.Windows.Forms.DataGridViewTextBoxColumn
    $suppIndexCol.Name = "supp_source_index"
    $suppIndexCol.HeaderText = "supp_source_index"
    $suppIndexCol.Visible = $false
    [void]$suppGrid.Columns.Add($suppIndexCol)

    $bottom = New-Object System.Windows.Forms.Panel
    $bottom.Dock = "Fill"
    $bottom.Height = 160
    $rootLayout.Controls.Add($bottom, 0, 3)

    $script:downloadButton = New-Object System.Windows.Forms.Button
    $downloadButton.Text = "Download selected files"
    $downloadButton.Location = New-Object System.Drawing.Point(10, 8)
    $downloadButton.Size = New-Object System.Drawing.Size(190, 30)
    $bottom.Controls.Add($downloadButton)

    $script:cancelButton = New-Object System.Windows.Forms.Button
    $cancelButton.Text = "Cancel"
    $cancelButton.Location = New-Object System.Drawing.Point(210, 8)
    $cancelButton.Size = New-Object System.Drawing.Size(95, 30)
    $cancelButton.Enabled = $false
    $bottom.Controls.Add($cancelButton)

    $script:diagnosticsButton = New-Object System.Windows.Forms.Button
    $diagnosticsButton.Text = "Save diagnostics"
    $diagnosticsButton.Location = New-Object System.Drawing.Point(315, 8)
    $diagnosticsButton.Size = New-Object System.Drawing.Size(145, 30)
    $bottom.Controls.Add($diagnosticsButton)

    $script:statusLabel = New-Object System.Windows.Forms.Label
    $statusLabel.Text = "Idle"
    $statusLabel.Location = New-Object System.Drawing.Point(470, 14)
    $statusLabel.Size = New-Object System.Drawing.Size(315, 22)
    $statusLabel.Anchor = $anchorTopLeftRight
    $bottom.Controls.Add($statusLabel)

    $script:progressBar = New-Object System.Windows.Forms.ProgressBar
    $progressBar.Location = New-Object System.Drawing.Point(800, 12)
    $progressBar.Size = New-Object System.Drawing.Size(330, 22)
    $progressBar.Anchor = $anchorTopRight
    $bottom.Controls.Add($progressBar)

    $script:selectionSummaryLabel = New-Object System.Windows.Forms.Label
    $selectionSummaryLabel.Location = New-Object System.Drawing.Point(10, 43)
    $selectionSummaryLabel.Size = New-Object System.Drawing.Size(1120, 20)
    $selectionSummaryLabel.Anchor = $anchorTopLeftRight
    $selectionSummaryLabel.AutoEllipsis = $true
    $bottom.Controls.Add($selectionSummaryLabel)

    $script:logBox = New-Object System.Windows.Forms.TextBox
    $logBox.Location = New-Object System.Drawing.Point(10, 66)
    $logBox.Size = New-Object System.Drawing.Size(1120, 84)
    $logBox.Anchor = [System.Windows.Forms.AnchorStyles]::Top -bor [System.Windows.Forms.AnchorStyles]::Bottom -bor [System.Windows.Forms.AnchorStyles]::Left -bor [System.Windows.Forms.AnchorStyles]::Right
    $logBox.Multiline = $true
    $logBox.ScrollBars = "Vertical"
    $logBox.ReadOnly = $true
    $bottom.Controls.Add($logBox)

    $browseButton.Add_Click({
        $dialog = New-Object System.Windows.Forms.FolderBrowserDialog
        $initialDir = Get-ExistingDirectoryForPath $outputBox.Text
        if (-not [string]::IsNullOrWhiteSpace($initialDir)) {
            $dialog.SelectedPath = $initialDir
        }
        if ($dialog.ShowDialog() -eq "OK") {
            $outputBox.Text = $dialog.SelectedPath
            Update-Capacity
        }
    })
    $outputBox.Add_TextChanged({ Update-SelectionSummary })

    $japaneseMenuItem.Add_Click({ Set-Language "ja" })
    $englishMenuItem.Add_Click({ Set-Language "en" })
    $verifyManifestMenuItem.Add_Click({ Show-ManifestVerificationOpenDialog })
    $helpOpenMenuItem.Add_Click({ Show-HelpWindow })
    $aboutMenuItem.Add_Click({
        [System.Windows.Forms.MessageBox]::Show((T "aboutText"), (T "about"), "OK", "Information") | Out-Null
    })

    $fastqGrid.add_CurrentCellDirtyStateChanged({
        if ($fastqGrid.IsCurrentCellDirty) {
            $fastqGrid.CommitEdit([System.Windows.Forms.DataGridViewDataErrorContexts]::Commit)
        }
    })
    $fastqGrid.add_CellValueChanged({ Update-Capacity })
    $fastqSelectAllButton.Add_Click({ Set-GridSelection $fastqGrid "selected" $true })
    $fastqClearSelectionButton.Add_Click({ Set-GridSelection $fastqGrid "selected" $false })
    $fastqGrid.add_SortCompare({
        param($sender, $eventArgs)
        if ($eventArgs.Column.Name -eq "size") {
            $left = [Int64]$sender.Rows[$eventArgs.RowIndex1].Cells["size_bytes_raw"].Value
            $right = [Int64]$sender.Rows[$eventArgs.RowIndex2].Cells["size_bytes_raw"].Value
            $eventArgs.SortResult = $left.CompareTo($right)
        }
        elseif ($eventArgs.Column.Name -eq "run") {
            $eventArgs.SortResult = Compare-AccessionText ([string]$eventArgs.CellValue1) ([string]$eventArgs.CellValue2)
            if ($eventArgs.SortResult -eq 0) {
                $leftFile = [Int64]$sender.Rows[$eventArgs.RowIndex1].Cells["file_index_raw"].Value
                $rightFile = [Int64]$sender.Rows[$eventArgs.RowIndex2].Cells["file_index_raw"].Value
                $eventArgs.SortResult = $leftFile.CompareTo($rightFile)
            }
        }
        else {
            $eventArgs.SortResult = Compare-GridString $eventArgs.CellValue1 $eventArgs.CellValue2
        }
        if ($eventArgs.SortResult -eq 0) {
            $leftIndex = [Int64]$sender.Rows[$eventArgs.RowIndex1].Cells["source_index"].Value
            $rightIndex = [Int64]$sender.Rows[$eventArgs.RowIndex2].Cells["source_index"].Value
            $eventArgs.SortResult = $leftIndex.CompareTo($rightIndex)
        }
        $eventArgs.Handled = $true
    })

    $suppGrid.add_CurrentCellDirtyStateChanged({
        if ($suppGrid.IsCurrentCellDirty) {
            $suppGrid.CommitEdit([System.Windows.Forms.DataGridViewDataErrorContexts]::Commit)
        }
    })
    $suppGrid.add_CellValueChanged({ Update-Capacity })
    $suppSelectAllButton.Add_Click({ Set-GridSelection $suppGrid "supp_selected" $true })
    $suppClearSelectionButton.Add_Click({ Set-GridSelection $suppGrid "supp_selected" $false })
    $suppGrid.add_SortCompare({
        param($sender, $eventArgs)
        $eventArgs.SortResult = Compare-GridString $eventArgs.CellValue1 $eventArgs.CellValue2
        if ($eventArgs.SortResult -eq 0) {
            $leftIndex = [Int64]$sender.Rows[$eventArgs.RowIndex1].Cells["supp_source_index"].Value
            $rightIndex = [Int64]$sender.Rows[$eventArgs.RowIndex2].Cells["supp_source_index"].Value
            $eventArgs.SortResult = $leftIndex.CompareTo($rightIndex)
        }
        $eventArgs.Handled = $true
    })

    $fetchButton.Add_Click({
        try {
            Clear-ResolvedState -DeleteResolvedJson
            Set-Busy $true
            $statusLabel.Text = T "fetching"
            $progressBar.Style = "Marquee"
            $progressBar.MarqueeAnimationSpeed = 30
            $progressBar.Value = 0
            Start-ResolveProcess $inputBox.Text
        }
        catch {
            $progressBar.Style = "Continuous"
            $progressBar.Value = 0
            Show-AppError $_.Exception.Message
            Set-Busy $false
        }
    })

    $downloadButton.Add_Click({
        try {
            Start-DownloadProcess
        }
        catch {
            $progressBar.Style = "Continuous"
            $progressBar.Value = 0
            $statusLabel.Text = T "error"
            Set-Busy $false
            $progressBar.Style = "Continuous"
            $progressBar.Value = 0
            $statusLabel.Text = T "error"
            Show-AppError $_.Exception.Message
        }
    })

    $diagnosticsButton.Add_Click({ Show-DiagnosticsSaveDialog })

    $cancelButton.Add_Click({
        $canceledAny = $false
        if ($null -ne $script:DownloadProcess -and -not $script:DownloadProcess.HasExited) {
            $canceledAny = $true
            $script:DownloadCanceled = $true
            Append-Log (T "cancelRequestLog")
            try {
                $script:DownloadProcess.Kill()
            }
            catch {
                Append-Log ((T "cancelFailedLog") -f $_.Exception.Message)
            }
        }
        if ($null -ne $script:VerifyProcess -and -not $script:VerifyProcess.HasExited) {
            $canceledAny = $true
            $script:VerifyCanceled = $true
            Append-Log (T "verifyCancelRequestLog")
            try {
                $script:VerifyProcess.Kill()
            }
            catch {
                Append-Log ((T "cancelFailedLog") -f $_.Exception.Message)
            }
        }
        if ($canceledAny) { Update-CancelButton }
    })

    $formLocal.add_FormClosing({
        if ($null -ne $script:ResolveProcess -and -not $script:ResolveProcess.HasExited) {
            try { $script:ResolveProcess.Kill() } catch { }
        }
        if ($null -ne $script:DownloadProcess -and -not $script:DownloadProcess.HasExited) {
            $script:DownloadCanceled = $true
            try { $script:DownloadProcess.Kill() } catch { }
        }
        if ($null -ne $script:VerifyProcess -and -not $script:VerifyProcess.HasExited) {
            $script:VerifyCanceled = $true
            try { $script:VerifyProcess.Kill() } catch { }
        }
    })

    $script:form = $formLocal
    Update-StaticTexts
    return $formLocal
}

$script:form = New-MainForm

if ($SelfTest) {
    $selfTestRoot = $null
    $selfTestSucceeded = $false
    try {
    Assert-Equal $form.Text "GEOGetter" "window title unchanged"
    Set-Language "en"
    Assert-Equal $settingsMenuItem.Text "Settings" "English settings menu"
    Assert-Equal $toolsMenuItem.Text "Tools" "English tools menu"
    Assert-Equal $verifyManifestMenuItem.Text "Verify saved FASTQ" "English verify manifest menu"
    Assert-Equal $helpOpenMenuItem.Text "Open help" "English open help menu"
    Assert-Equal $helpMenuItem.DropDownItems.Count 3 "Help menu uses single help entry plus separator and about"
    Assert-Equal $fetchButton.Text "Find files" "English find files button"
    Assert-Equal $browseButton.Text "Browse" "English browse button"
    Assert-Equal $diagnosticsButton.Text "Save diagnostics" "English diagnostics button"
    Assert-Equal $fastqGrid.Columns["geo_title"].HeaderText "Sample title" "English FASTQ header"
    Set-Language "ja"
    Assert-Equal $helpOpenMenuItem.Text "ヘルプを開く" "Japanese open help menu"
    Assert-Equal $toolsMenuItem.Text "ツール" "Japanese tools menu"
    Assert-Equal $verifyManifestMenuItem.Text "保存済みFASTQを確認" "Japanese verify manifest menu"
    Assert-Equal ((Get-Variable -Name inputHelpMenuItem -Scope Script -ErrorAction SilentlyContinue) -eq $null) $true "individual input help menu removed"
    Assert-Equal $fetchButton.Text "ファイルを検索" "Japanese find files button"
    Assert-Equal $browseButton.Text "参照..." "Japanese browse button"
    Assert-Equal $diagnosticsButton.Text "診断情報を保存" "Japanese diagnostics button"
    Assert-Equal $suppTitle.Text "GEO supplementary / processed file（raw FASTQ以外）: 0件" "Japanese supplementary title"
    Assert-Equal $outputBox.ReadOnly $true "output folder is browse-only"
    Assert-Equal $outputBox.Text (Get-DefaultOutputFolder) "default output folder"
    Assert-Equal $fastqGrid.Columns["run"].ReadOnly $true "FASTQ run column readonly"
    Assert-Equal $fastqGrid.Columns["url"].ReadOnly $true "FASTQ URL column readonly"
    Assert-Equal $fastqGrid.Columns["selected"].ReadOnly $false "FASTQ select column editable"
    Assert-Equal ((Get-Variable -Name planButton -Scope Script -ErrorAction SilentlyContinue) -eq $null) $true "save-list button removed from main UI"
    Assert-Equal (Format-Bytes ([Int64]2377036173)) "2.21 GB" "Format-Bytes over Int32"
    Assert-Equal (Format-Bytes ([Int64]5000000000)) "4.66 GB" "Format-Bytes 5GB"
    Assert-Equal (Format-Bytes ([Int64]-1)) "0 B" "Format-Bytes negative"
    Assert-Equal (ConvertTo-GeoGetterSafeName "CON") "_CON" "preflight safe name handles reserved Windows name"
    Assert-Equal (ConvertTo-GeoGetterSafeName "..") "geo_getter_download" "preflight safe name handles dot-only name"
    Assert-Equal (Get-PreflightNameCollisionKey "Same.fastq.gz") "same.fastq.gz" "preflight name collision key is case-insensitive"
    Assert-Equal (Get-PreflightUniqueNumberedName "same.fastq.gz" 1) "same.2.fastq.gz" "preflight duplicate FASTQ numbering"
    $preflightUsedKeys = @{}
    Assert-Equal (Get-PreflightReservedUniqueName "Same.fastq.gz" $preflightUsedKeys) "Same.fastq.gz" "preflight reserves first name"
    Assert-Equal (Get-PreflightReservedUniqueName "same.2.fastq.gz" $preflightUsedKeys) "same.2.fastq.gz" "preflight reserves pre-numbered name"
    Assert-Equal (Get-PreflightReservedUniqueName "same.fastq.gz" $preflightUsedKeys) "same.3.fastq.gz" "preflight skips occupied numbered candidate"
    Assert-Equal (ConvertTo-ProcessArgument "") '""' "empty process argument"
    $originalDiagnosticLimit = $script:DiagnosticProcessOutputLimitBytes
    $script:DiagnosticProcessOutputLimitBytes = 80
    $script:DownloadStdoutText = ""
    Add-DiagnosticProcessOutput "stdout" ("a" * 100)
    Assert-Contains $script:DownloadStdoutText "earlier process output was truncated" "diagnostic stdout cap marker"
    Assert-Equal ($script:DownloadStdoutText.Length -le 80) $true "diagnostic stdout cap"
    $script:DiagnosticProcessOutputLimitBytes = $originalDiagnosticLimit
    $encodingResult = Invoke-PythonCli -Arguments @("-m", "geo_getter.cli", "resolve-json", "")
    Assert-Equal $encodingResult.ExitCode 1 "empty input error exit code"
    Assert-Contains $encodingResult.Stderr "input_text or --input-file" "CLI stderr stays English"
    $resolveErrorEvent = $encodingResult.Stderr.Trim() | ConvertFrom-Json
    Assert-Equal $resolveErrorEvent.event "error" "CLI emits structured error event"
    Assert-Equal $resolveErrorEvent.command "resolve-json" "CLI error records command"
    Assert-Equal $resolveErrorEvent.code "invalid_input" "CLI error records code"

    $selfTestRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("geo getter selftest " + [System.Guid]::NewGuid().ToString("N"))
    [System.IO.Directory]::CreateDirectory($selfTestRoot) | Out-Null
    $script:ResolveStdoutText = $encodingResult.Stdout
    $script:ResolveStderrText = $encodingResult.Stderr
    $script:LastResolveExitCode = $encodingResult.ExitCode
    $script:LastResolveArguments = @("-m", "geo_getter.cli", "resolve-json", "")
    Set-DiagnosticErrorFromProcessOutput "resolve" "resolve-json" $encodingResult.ExitCode $encodingResult.Stdout $encodingResult.Stderr "resolve_failed" (T "metadataFailed")
    Assert-Equal $script:LastDiagnosticError.code "invalid_input" "GUI parses CLI error code"
    $resolveFailureZip = Join-Path $selfTestRoot "resolve-failure-diagnostics.zip"
    Save-DiagnosticsZip $resolveFailureZip | Out-Null
    $resolveFailureExtract = Join-Path $selfTestRoot "resolve failure extract"
    Expand-Archive -LiteralPath $resolveFailureZip -DestinationPath $resolveFailureExtract -Force
    $resolveFailureDiagnostics = Get-Content -Raw -Encoding UTF8 (Join-Path $resolveFailureExtract "diagnostics.json") | ConvertFrom-Json
    Assert-Equal $resolveFailureDiagnostics.last_error.phase "resolve" "diagnostics records resolve phase"
    Assert-Equal $resolveFailureDiagnostics.last_error.code "invalid_input" "diagnostics records resolve error code"
    Assert-Equal (Test-Path -LiteralPath (Join-Path $resolveFailureExtract "resolved.json")) $false "resolve failure diagnostics excludes stale resolved json"
    Clear-ResolvedState -DeleteResolvedJson
    $noCreateOutput = Join-Path $selfTestRoot "capacity should not be created"
    $outputBox.Text = $noCreateOutput
    Update-Capacity
    Assert-Equal (Test-Path -LiteralPath $noCreateOutput) $false "capacity update does not create output folder"
    $null = Get-OutputFreeSpaceOrNull
    Assert-Equal (Test-Path -LiteralPath $noCreateOutput) $false "diagnostic free space lookup does not create output folder"

    $sourcePath = Join-Path $selfTestRoot "source fastq.gz"
    [System.IO.File]::WriteAllBytes($sourcePath, [System.Text.Encoding]::ASCII.GetBytes("@r1`nACGT`n+`n!!!!`n"))
    $expectedMd5 = (Get-FileHash -Algorithm MD5 -LiteralPath $sourcePath).Hash.ToLowerInvariant()
    $sourceUri = (New-Object System.Uri((Resolve-Path -LiteralPath $sourcePath).Path)).AbsoluteUri
    $outputBox.Text = Join-Path $selfTestRoot "out folder"

    $resolvedFixture = [pscustomobject]@{
        input_text = "SELFTEST"
        primary_accession = "SELFTEST"
        query_accessions = @("SELFTEST")
        warnings = @()
        dataset_metadata = [pscustomobject]@{
            accession = "SELFTEST"
            status = "Public on Jan 01 2026"
            title = "Self test dataset"
            organism = "Homo sapiens; Mus musculus"
            experiment_type = "Expression profiling by high throughput sequencing"
        }
        supplementary_files = @(
            [pscustomobject]@{
                source_accession = "SELFTEST"
                scope = "GEO Series supplementary/processed"
                name = "processed.txt"
                url = $sourceUri
            }
        )
        fastq_files = @(
            [pscustomobject]@{
                source_accession = "SELFTEST"
                query_accession = "SELFTEST"
                run_accession = "SRR2"
                file_index = 1
                file_name = "large1.fastq.gz"
                url = "https://example.invalid/large1.fastq.gz"
                expected_md5 = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
                size_bytes = 2377036173
                sample_accession = "SAM1"
                library_layout = "SINGLE"
            },
            [pscustomobject]@{
                source_accession = "SELFTEST"
                query_accession = "SELFTEST"
                run_accession = "SRR10"
                file_index = 1
                file_name = "large2.fastq.gz"
                url = "https://example.invalid/large2.fastq.gz"
                expected_md5 = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
                size_bytes = 2392496788
                sample_accession = "SAM2"
                library_layout = "SINGLE"
            },
            [pscustomobject]@{
                source_accession = "SELFTEST"
                query_accession = "SELFTEST"
                run_accession = "SRR1"
                file_index = 1
                file_name = "fixture.fastq.gz"
                url = $sourceUri
                expected_md5 = $expectedMd5
                size_bytes = 16
                sample_accession = "SAM_SMALL"
                library_layout = "SINGLE"
            }
        )
    }
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($script:ResolvedJsonPath, ($resolvedFixture | ConvertTo-Json -Depth 10), $utf8NoBom)

    $collisionFixture = [pscustomobject]@{
        input_text = "COLLISION"
        primary_accession = "COLLISION"
        query_accessions = @("COLLISION")
        warnings = @()
        dataset_metadata = [pscustomobject]@{
            accession = "COLLISION"
            status = "Public on Jan 01 2026"
            title = "Collision self test dataset"
            organism = "Homo sapiens"
            experiment_type = "Expression profiling by high throughput sequencing"
        }
        supplementary_files = @(
            [pscustomobject]@{
                source_accession = "COLLISION"
                scope = "GEO Series supplementary/processed"
                name = "same.fastq.gz"
                url = $sourceUri
            }
        )
        fastq_files = @(
            [pscustomobject]@{
                source_accession = "COLLISION"
                query_accession = "COLLISION"
                run_accession = "SRR1"
                file_index = 1
                file_name = "Same.fastq.gz"
                url = $sourceUri
                expected_md5 = $expectedMd5
                size_bytes = 16
                sample_accession = "SAM1"
                library_layout = "SINGLE"
            },
            [pscustomobject]@{
                source_accession = "COLLISION"
                query_accession = "COLLISION"
                run_accession = "SRR2"
                file_index = 1
                file_name = "same.2.fastq.gz"
                url = $sourceUri
                expected_md5 = $expectedMd5
                size_bytes = 16
                sample_accession = "SAM2"
                library_layout = "SINGLE"
            },
            [pscustomobject]@{
                source_accession = "COLLISION"
                query_accession = "COLLISION"
                run_accession = "SRR3"
                file_index = 1
                file_name = "same.fastq.gz"
                url = $sourceUri
                expected_md5 = $expectedMd5
                size_bytes = 16
                sample_accession = "SAM3"
                library_layout = "SINGLE"
            }
        )
    }
    Apply-ResolvedResult $collisionFixture
    Set-GridSelection $fastqGrid "selected" $true
    Set-GridSelection $suppGrid "supp_selected" $true
    $collisionNames = @(Get-PreflightPlannedPaths (Join-Path $selfTestRoot "collision output") | ForEach-Object { [System.IO.Path]::GetFileName([string]$_) })
    Assert-Equal ($collisionNames -contains "Same.fastq.gz") $true "preflight includes first FASTQ collision name"
    Assert-Equal ($collisionNames -contains "same.2.fastq.gz") $true "preflight includes pre-numbered FASTQ name"
    Assert-Equal ($collisionNames -contains "same.3.fastq.gz") $true "preflight reserves next FASTQ collision name"
    Assert-Equal ($collisionNames -contains "same.4.fastq.gz") $true "preflight reserves supplementary after FASTQ names"
    Assert-Equal ($collisionNames -contains "same.4.fastq.gz.part") $true "preflight includes supplementary part collision path"

    Apply-ResolvedResult $resolvedFixture
    Assert-Equal $outputBox.Text (Get-DefaultOutputFolderForAccession "SELFTEST") "search success sets accession output folder"
    Assert-Equal $fastqGrid.Rows[0].Cells["run"].Value "SRR1" "fastq display defaults to SRR order"
    Assert-Equal $fastqGrid.Rows[0].Cells["size"].Value "16 B" "fastq display keeps SRR1 row data"
    $fastqGrid.Sort($fastqGrid.Columns["run"], [System.ComponentModel.ListSortDirection]::Descending)
    Assert-Equal $fastqGrid.Rows[0].Cells["run"].Value "SRR10" "fastq run descending sort"
    $fastqGrid.Sort($fastqGrid.Columns["size"], [System.ComponentModel.ListSortDirection]::Ascending)
    Assert-Equal $fastqGrid.Rows[0].Cells["size"].Value "16 B" "fastq size ascending sort"
    $fastqGrid.Sort($fastqGrid.Columns["size"], [System.ComponentModel.ListSortDirection]::Descending)
    Assert-Equal $fastqGrid.Rows[0].Cells["run"].Value "SRR10" "fastq size descending sort"
    $fastqGrid.Sort($fastqGrid.Columns["run"], [System.ComponentModel.ListSortDirection]::Ascending)
    Assert-Equal (Get-SelectedFastqIndicesOrEmpty) "" "no default FASTQ selection"
    Assert-Equal (Get-SelectedTotalBytes) ([Int64]0) "no default capacity"
    Assert-Equal $suppGrid.Rows.Count 1 "supplementary row count"
    Assert-Contains $fastqTitle.Text "3件" "FASTQ title includes count"
    Assert-Contains $suppTitle.Text "1件" "supplementary title includes count"
    Assert-Contains $selectionSummaryLabel.Text "FASTQ 0 件" "selection summary default fastq count"
    Update-DatasetInfo
    Assert-Equal $geoAccessionValueLabel.Text "SELFTEST" "GEO info includes accession"
    Assert-Equal $geoStatusValueLabel.Text "Public on Jan 01 2026" "GEO info includes status"
    Assert-Equal $geoOrganismValueLabel.Text "Homo sapiens; Mus musculus" "GEO info includes organism"
    $geoInfoText = (($geoInfoPanel.Controls | ForEach-Object { $_.Text }) -join " ")
    Assert-Equal ($geoInfoText.Contains("Title")) $false "GEO info excludes title"
    Assert-Equal ($geoInfoText.Contains("Experiment type")) $false "GEO info excludes experiment type"
    Assert-Equal ($geoInfoText.Contains("FASTQ 3件")) $false "GEO info excludes file counts"

    Set-GridSelection $fastqGrid "selected" $true
    Assert-Equal (Get-SelectedIndices) "2,0,1" "selected indices all rows sorted"
    Assert-Equal (Get-SelectedTotalBytes) ([Int64]4769532977) "selected total bytes"
    Update-Capacity
    Assert-Contains $capacityLabel.Text "必要容量(FASTQ): 4.44 GB /" "capacity label"
    Assert-Contains $selectionSummaryLabel.Text "FASTQ 3 件 / 4.44 GB" "selection summary selected fastq"
    Set-GridSelection $fastqGrid "selected" $false
    Assert-Equal (Get-SelectedFastqIndicesOrEmpty) "" "bulk clear fastq selection"
    Set-GridSelection $suppGrid "supp_selected" $true
    Assert-Equal (Get-SelectedSuppIndicesOrEmpty) "0" "bulk supplementary selection"
    Assert-Contains $selectionSummaryLabel.Text "GEO supplementary 1 件" "selection summary selected supplementary"
    Set-GridSelection $suppGrid "supp_selected" $false
    Assert-Equal (Get-SelectedSuppIndicesOrEmpty) "" "bulk clear supplementary selection"
    $inputBox.Text = "SELFTEST"
    Assert-ResolvedMatchesCurrentInput
    $inputBox.Text = "DIFFERENT_INPUT"
    $inputChangedMessage = ""
    try {
        Assert-ResolvedMatchesCurrentInput
    }
    catch {
        $inputChangedMessage = $_.Exception.Message
    }
    Assert-Equal $inputChangedMessage (T "inputChangedAfterResolve") "download blocked when input changed"
    $inputBox.Text = "SELFTEST"
    Set-GridSelection $fastqGrid "selected" $true
    Set-GridSelection $suppGrid "supp_selected" $true
    Clear-ResolvedState -DeleteResolvedJson
    Assert-Equal $script:Resolved $null "clear resolved removes cached result"
    Assert-Equal $fastqGrid.Rows.Count 0 "clear resolved removes fastq rows"
    Assert-Equal $suppGrid.Rows.Count 0 "clear resolved removes supplementary rows"
    Assert-Contains $fastqTitle.Text "0件" "clear resolved resets fastq title count"
    Assert-Contains $suppTitle.Text "0件" "clear resolved resets supplementary title count"
    Assert-Equal (Get-SelectedFastqIndicesOrEmpty) "" "clear resolved removes fastq selections"
    Assert-Equal (Get-SelectedSuppIndicesOrEmpty) "" "clear resolved removes supplementary selections"
    Assert-Equal (Test-Path -LiteralPath $script:ResolvedJsonPath) $false "clear resolved removes resolved json"
    $noResultMessage = ""
    try {
        Assert-ResolvedMatchesCurrentInput
    }
    catch {
        $noResultMessage = $_.Exception.Message
    }
    Assert-Equal $noResultMessage (T "searchRequiredBeforeDownload") "download blocked when no resolved state"
    $script:Resolved = $resolvedFixture
    [System.IO.File]::WriteAllText($script:ResolvedJsonPath, ($script:Resolved | ConvertTo-Json -Depth 10), $utf8NoBom)
    $script:LastResolvedInputText = Normalize-InputText ([string]$script:Resolved.input_text)
    Add-FastqRowsFromResolved
    Add-SupplementaryRowsFromResolved
    Update-ResultTitles
    Update-DatasetInfo
    Update-Capacity
    Handle-DownloadLine '{"event":"progress","file_name":"large1.fastq.gz","downloaded":1188518086,"total":2377036173}'
    Assert-Equal $statusLabel.Text (T "downloading") "progress label remains process state"
    $script:DownloadExitObserved = $false
    $script:DownloadStdoutClosed = $false
    $script:DownloadFinalized = $false
    $statusLabel.Text = T "downloading"
    Handle-DownloadLine '{"event":"done","statuses":["md5_unavailable"],"output_dir":"C:\\tmp\\SELFTEST","fastq_manifest":"","supplementary_manifest":"","download_log":"C:\\tmp\\SELFTEST\\SELFTEST_download_log.tsv"}'
    Assert-Equal $statusLabel.Text (T "downloading") "done event does not finalize status before exit and stdout close"
    Assert-Equal (Get-DownloadFinalStatusKey ([pscustomobject]@{ statuses = @("md5_verified", "download_complete") }) 0 $false) "complete" "final state all ok"
    Assert-Equal (Get-DownloadFinalStatusKey ([pscustomobject]@{ statuses = @("md5_unavailable") }) 1 $false) "completeUnverified" "final state md5 unavailable"
    Assert-Equal (Get-DownloadFinalStatusKey ([pscustomobject]@{ statuses = @("network_failed") }) 1 $false) "completePartial" "final state network failed"
    Assert-Equal (Get-DownloadFinalStatusKey ([pscustomobject]@{ statuses = @("md5_mismatch") }) 1 $false) "completePartial" "final state md5 mismatch"
    Assert-Equal (Get-DownloadFinalStatusKey ([pscustomobject]@{ statuses = @("size_mismatch") }) 1 $false) "completePartial" "final state size mismatch"
    Assert-Equal (Get-DownloadFinalStatusKey $null 0 $false) "error" "final state missing done event with zero exit"
    Assert-Equal (Get-DownloadFinalStatusKey $null 1 $false) "error" "final state missing done event with nonzero exit"
    Assert-Equal (Get-DownloadFinalStatusKey ([pscustomobject]@{ statuses = @("md5_verified") }) 1 $true) "canceled" "final state canceled wins"

    $script:LastDownloadDoneEvent = $null
    $script:LastDownloadExitCode = 1
    $script:DownloadCanceled = $false
    $script:DownloadExitObserved = $true
    $script:DownloadStdoutClosed = $false
    $script:DownloadFinalized = $false
    $statusLabel.Text = T "downloading"
    Complete-DownloadIfReady
    Assert-Equal $statusLabel.Text (T "downloading") "download finalizer waits for stdout close after exit"
    $script:LastDownloadDoneEvent = [pscustomobject]@{ statuses = @("md5_unavailable") }
    Complete-DownloadIfReady
    Assert-Equal $statusLabel.Text (T "downloading") "download finalizer still waits for stdout close after done"
    $script:DownloadStdoutClosed = $true
    Complete-DownloadIfReady
    Assert-Equal $statusLabel.Text (T "completeUnverified") "download finalizer handles exit before done processing"

    $script:LastVerificationDoneEvent = $null
    $script:LastVerificationExitCode = 0
    $script:VerifyCanceled = $false
    $script:VerifyExitObserved = $true
    $script:VerifyStdoutClosed = $false
    $script:VerifyFinalized = $false
    $statusLabel.Text = T "verifyingManifest"
    Complete-ManifestVerificationIfReady
    Assert-Equal $statusLabel.Text (T "verifyingManifest") "verification finalizer waits for stdout close after exit"
    $script:LastVerificationDoneEvent = [pscustomobject]@{ report = "C:\tmp\verification_report.tsv" }
    Complete-ManifestVerificationIfReady
    Assert-Equal $statusLabel.Text (T "verifyingManifest") "verification finalizer still waits for stdout close after done"
    $script:VerifyStdoutClosed = $true
    Complete-ManifestVerificationIfReady
    Assert-Equal $statusLabel.Text (T "complete") "verification finalizer handles exit before done processing"

    foreach ($row in $fastqGrid.Rows) {
        if (-not $row.IsNewRow) { $row.Cells["selected"].Value = $false }
    }
    $fastqGrid.Rows[0].Cells["selected"].Value = $true
    $suppGrid.Rows[0].Cells["supp_selected"].Value = $true

    $fileOutputPath = Join-Path $selfTestRoot "output path is file"
    [System.IO.File]::WriteAllText($fileOutputPath, "not a directory", $utf8NoBom)
    $outputBox.Text = $fileOutputPath
    $fileOutputPreflightMessage = ""
    try {
        Test-DownloadPreflight | Out-Null
    }
    catch {
        $fileOutputPreflightMessage = $_.Exception.Message
    }
    Assert-Contains $fileOutputPreflightMessage "ファイル" "preflight rejects output path that is a file"
    Assert-Equal $script:LastPreflightStatus "failed" "preflight records file output failure"
    $startPreflightMessage = ""
    $script:DownloadProcess = $null
    try {
        Start-DownloadProcess
    }
    catch {
        $startPreflightMessage = $_.Exception.Message
    }
    Assert-Contains $startPreflightMessage "ファイル" "download start stops before subprocess on preflight failure"
    Assert-Equal $script:DownloadProcess $null "download process is not created when preflight fails"
    Assert-Equal $script:LastDiagnosticError.phase "download_preflight" "preflight records diagnostic phase"
    Assert-Equal $script:LastDiagnosticError.code "output_path_invalid" "preflight records diagnostic code"
    $preflightFailureZip = Join-Path $selfTestRoot "preflight-failure-diagnostics.zip"
    Save-DiagnosticsZip $preflightFailureZip | Out-Null
    $preflightFailureExtract = Join-Path $selfTestRoot "preflight failure extract"
    Expand-Archive -LiteralPath $preflightFailureZip -DestinationPath $preflightFailureExtract -Force
    $preflightFailureDiagnostics = Get-Content -Raw -Encoding UTF8 (Join-Path $preflightFailureExtract "diagnostics.json") | ConvertFrom-Json
    Assert-Equal $preflightFailureDiagnostics.last_error.phase "download_preflight" "diagnostics records preflight phase"
    Assert-Equal $preflightFailureDiagnostics.last_error.code "output_path_invalid" "diagnostics records preflight code"

    $longPreflightMessage = ""
    try {
        Assert-PreflightPathLength @(Join-Path $selfTestRoot ("x" * 270))
    }
    catch {
        $longPreflightMessage = $_.Exception.Message
    }
    Assert-Contains $longPreflightMessage "長すぎます" "preflight checks long paths"

    $outputBox.Text = Join-Path $selfTestRoot "supp only output"
    $fastqGrid.Rows[0].Cells["selected"].Value = $false
    $suppGrid.Rows[0].Cells["supp_selected"].Value = $true
    $suppOnlyPreflight = Test-DownloadPreflight
    Assert-Equal $script:LastPreflightStatus "ok" "preflight accepts supplementary-only selection"
    Assert-Equal $suppOnlyPreflight.RequiredBytes ([Int64]0) "supplementary-only preflight excludes unknown size from capacity"

    $outputBox.Text = Join-Path $selfTestRoot "huge fastq output"
    Set-GridSelection $fastqGrid "selected" $true
    Set-GridSelection $suppGrid "supp_selected" $false
    $originalSmallSize = [Int64]$script:Resolved.fastq_files[[int]$fastqGrid.Rows[0].Tag].size_bytes
    $hugeFreeBytes = Get-FreeSpaceForPathOrNull $outputBox.Text
    if ($null -eq $hugeFreeBytes) { throw "self-test could not read temporary drive free space" }
    $script:Resolved.fastq_files[[int]$fastqGrid.Rows[0].Tag].size_bytes = [Int64]$hugeFreeBytes + 1
    $hugePreflightMessage = ""
    try {
        Test-DownloadPreflight | Out-Null
    }
    catch {
        $hugePreflightMessage = $_.Exception.Message
    }
    finally {
        $script:Resolved.fastq_files[[int]$fastqGrid.Rows[0].Tag].size_bytes = $originalSmallSize
    }
    Assert-Contains $hugePreflightMessage "空き容量" "preflight rejects insufficient FASTQ capacity"
    Assert-Equal $script:LastDiagnosticError.code "insufficient_space" "preflight records insufficient space code"

    $outputBox.Text = Join-Path $selfTestRoot "SELFTEST"
    Set-GridSelection $fastqGrid "selected" $false
    $fastqGrid.Rows[0].Cells["selected"].Value = $true
    $suppGrid.Rows[0].Cells["supp_selected"].Value = $true
    $downloadPreflight = Test-DownloadPreflight
    $selfTestRunOutput = [System.IO.Path]::GetFullPath([string]$outputBox.Text)
    Assert-Equal $script:LastPreflightOutputDir $selfTestRunOutput "preflight output dir matches output field"
    Assert-Equal $downloadPreflight.OutputDir $selfTestRunOutput "preflight result output dir matches output field"

    $downloadResult = Invoke-SelectedDownloadJsonForSelfTest (Get-SelectedFastqIndicesOrEmpty) (Get-SelectedSuppIndicesOrEmpty)
    $script:DownloadStdoutText = Limit-DiagnosticText $downloadResult.Stdout
    $script:DownloadStderrText = Limit-DiagnosticText $downloadResult.Stderr
    $script:LastDownloadExitCode = $downloadResult.ExitCode
    $doneLine = @($downloadResult.Stdout -split "`r?`n" | Where-Object { $_ -match '"event"\s*:\s*"done"' } | Select-Object -Last 1)
    if ($doneLine.Count -gt 0) {
        $script:LastDownloadDoneEvent = $doneLine[0] | ConvertFrom-Json
    }
    Assert-Equal $downloadResult.ExitCode 0 "selected-download-json exit code"
    Assert-Contains $downloadResult.Stdout '"event": "done"' "selected-download-json done event"
    Assert-Contains $downloadResult.Stdout '"md5_verified"' "selected-download-json md5 success"
    Assert-Contains $downloadResult.Stdout '"download_complete"' "selected-download-json supplementary success"
    Assert-Equal ([System.IO.Path]::GetFullPath([string]$script:LastDownloadDoneEvent.output_dir)) $selfTestRunOutput "download done output dir matches output field"
    Assert-Equal (Test-Path -LiteralPath (Join-Path $selfTestRunOutput "SELFTEST")) $false "download does not create nested accession folder"
    Assert-Equal (Test-Path -LiteralPath (Join-Path $selfTestRunOutput "SELFTEST_fastq_manifest.tsv")) $true "fastq manifest exists"
    Assert-Equal (Test-Path -LiteralPath (Join-Path $selfTestRunOutput "SELFTEST_supplementary_manifest.tsv")) $true "supplementary manifest exists"
    Assert-Equal (Test-Path -LiteralPath (Join-Path $selfTestRunOutput "SELFTEST_download_log.tsv")) $true "download log exists"
    Assert-Equal (Test-Path -LiteralPath (Join-Path $selfTestRunOutput "manifest.tsv")) $false "old manifest removed"
    Assert-Equal (Test-Path -LiteralPath (Join-Path $selfTestRunOutput "download_log.tsv")) $false "old download log removed"
    Assert-Contains (Get-Content -Raw -Encoding UTF8 (Join-Path $selfTestRunOutput "SELFTEST_download_log.tsv")) "md5_verified" "download log md5 success"
    Assert-Contains (Get-Content -Raw -Encoding UTF8 (Join-Path $selfTestRunOutput "SELFTEST_download_log.tsv")) "download_complete" "download log supplementary success"
    $verifyResult = Invoke-VerifyManifestJsonForSelfTest (Join-Path $selfTestRunOutput "SELFTEST_fastq_manifest.tsv")
    $script:VerifyStdoutText = Limit-DiagnosticText $verifyResult.Stdout
    $script:VerifyStderrText = Limit-DiagnosticText $verifyResult.Stderr
    $script:LastVerificationExitCode = $verifyResult.ExitCode
    $verifyDoneLine = @($verifyResult.Stdout -split "`r?`n" | Where-Object { $_ -match '"kind"\s*:\s*"manifest_verification"' } | Select-Object -Last 1)
    if ($verifyDoneLine.Count -gt 0) {
        $script:LastVerificationDoneEvent = $verifyDoneLine[0] | ConvertFrom-Json
    }
    Assert-Equal $verifyResult.ExitCode 0 "verify-manifest-json exit code"
    Assert-Contains $verifyResult.Stdout '"kind": "manifest_verification"' "verify-manifest-json done event"
    Assert-Equal (Test-Path -LiteralPath (Join-Path $selfTestRunOutput "verification_report.tsv")) $true "verification report exists"
    Assert-Contains (Get-Content -Raw -Encoding UTF8 (Join-Path $selfTestRunOutput "verification_report.tsv")) "md5_verified" "verification report md5 success"

    $downloadDoneEventForDiagnostics = $script:LastDownloadDoneEvent
    $script:LastDownloadDoneEvent = $null
    $script:LastDownloadExitCode = 1
    $script:DownloadCanceled = $false
    $script:DownloadExitObserved = $true
    $script:DownloadStdoutClosed = $true
    $script:DownloadFinalized = $false
    $script:DownloadStdoutText = ""
    $script:DownloadStderrText = '{"event":"error","command":"selected-download-json","code":"invalid_json","detail":"fixture","message":"fixture"}'
    $script:LastPreflightOutputDir = $selfTestRunOutput
    $script:LastDiagnosticError = $null
    Complete-DownloadIfReady
    Assert-Equal $script:LastDiagnosticError.phase "download" "download without done records phase"
    Assert-Equal $script:LastDiagnosticError.code "invalid_json" "download without done parses CLI error"
    $noDoneZip = Join-Path $selfTestRoot "download-no-done-diagnostics.zip"
    Save-DiagnosticsZip $noDoneZip | Out-Null
    $noDoneExtract = Join-Path $selfTestRoot "download no done extract"
    Expand-Archive -LiteralPath $noDoneZip -DestinationPath $noDoneExtract -Force
    $noDoneDiagnostics = Get-Content -Raw -Encoding UTF8 (Join-Path $noDoneExtract "diagnostics.json") | ConvertFrom-Json
    Assert-Equal $noDoneDiagnostics.last_error.phase "download" "diagnostics records no-done phase"
    Assert-Equal $noDoneDiagnostics.last_error.code "invalid_json" "diagnostics records no-done code"
    Assert-Equal ((Get-ChildItem -Path $noDoneExtract -Recurse -Filter "*_download_log.tsv").Count -gt 0) $true "no-done diagnostics includes available download log"
    $script:LastDownloadDoneEvent = $downloadDoneEventForDiagnostics
    $script:LastDownloadExitCode = $downloadResult.ExitCode
    $script:DownloadFinalized = $true
    $script:LastDiagnosticError = $null

    $progressBar.Value = 0
    $statusLabel.Text = T "verifyingManifest"
    [void]$form.Handle
    Set-Busy $true
    Start-ManifestVerificationProcess (Join-Path $selfTestRunOutput "SELFTEST_fastq_manifest.tsv")
    $deadline = [DateTime]::UtcNow.AddSeconds(10)
    while ($null -ne $script:VerifyProcess -and [DateTime]::UtcNow -lt $deadline) {
        [System.Windows.Forms.Application]::DoEvents()
        Start-Sleep -Milliseconds 50
    }
    for ($i = 0; $i -lt 20; $i++) {
        [System.Windows.Forms.Application]::DoEvents()
        Start-Sleep -Milliseconds 50
    }
    Assert-Equal $script:VerifyProcess $null "async manifest verification process finished"
    Assert-Equal $statusLabel.Text (T "complete") "async manifest verification status"
    $diagnosticsZip = Join-Path $selfTestRoot "diagnostics.zip"
    Save-DiagnosticsZip $diagnosticsZip | Out-Null
    Assert-Equal (Test-Path -LiteralPath $diagnosticsZip) $true "diagnostics zip exists"
    $diagnosticsExtract = Join-Path $selfTestRoot "diagnostics extract"
    Expand-Archive -LiteralPath $diagnosticsZip -DestinationPath $diagnosticsExtract -Force
    Assert-Equal (Test-Path -LiteralPath (Join-Path $diagnosticsExtract "diagnostics.json")) $true "diagnostics metadata exists"
    Assert-Equal (Test-Path -LiteralPath (Join-Path $diagnosticsExtract "gui_log.txt")) $true "diagnostics GUI log exists"
    Assert-Equal (Test-Path -LiteralPath (Join-Path $diagnosticsExtract "resolved.json")) $true "diagnostics resolved JSON exists"
    Assert-Equal ((Get-ChildItem -Path $diagnosticsExtract -Recurse -Filter "*_download_log.tsv").Count -gt 0) $true "diagnostics includes download log"
    Assert-Equal ((Get-ChildItem -Path $diagnosticsExtract -Recurse -Filter "verification_report.tsv").Count -gt 0) $true "diagnostics includes verification report"
    $successDiagnostics = Get-Content -Raw -Encoding UTF8 (Join-Path $diagnosticsExtract "diagnostics.json") | ConvertFrom-Json
    Assert-Equal $successDiagnostics.last_error $null "successful diagnostics does not keep stale error"

    $inputBox.Text = "DIFFERENT_INPUT"
    $staleDownloadStartMessage = ""
    try {
        Start-DownloadProcess
    }
    catch {
        $staleDownloadStartMessage = $_.Exception.Message
    }
    Assert-Equal $staleDownloadStartMessage (T "inputChangedAfterResolve") "download start validation reports changed input"
    Assert-Equal $script:DownloadProcess $null "download start validation does not create process"
    Assert-Equal $script:LastDiagnosticError.phase "download_preflight" "download start validation records phase"
    Assert-Equal $script:LastDiagnosticError.code "resolved_state_invalid" "download start validation records code"
    $staleDownloadZip = Join-Path $selfTestRoot "download-validation-stale-diagnostics.zip"
    Save-DiagnosticsZip $staleDownloadZip | Out-Null
    $staleDownloadExtract = Join-Path $selfTestRoot "download validation stale extract"
    Expand-Archive -LiteralPath $staleDownloadZip -DestinationPath $staleDownloadExtract -Force
    Assert-Equal ((Get-ChildItem -Path $staleDownloadExtract -Recurse -Filter "*_download_log.tsv").Count) 0 "download validation diagnostics omits stale download log"
    Assert-Equal ((Get-ChildItem -Path $staleDownloadExtract -Recurse -Filter "*_fastq_manifest.tsv").Count) 0 "download validation diagnostics omits stale fastq manifest"
    Assert-Equal ((Get-ChildItem -Path $staleDownloadExtract -Recurse -Filter "verification_report.tsv").Count) 0 "download validation diagnostics omits stale verification report"
    $inputBox.Text = "SELFTEST"

    $originalPythonExe = $PythonExe
    $PythonExe = Join-Path $selfTestRoot "missing-python.exe"
    $threwVerifyStart = $false
    try {
        Start-ManifestVerificationProcess (Join-Path $selfTestRunOutput "SELFTEST_fastq_manifest.tsv")
    }
    catch {
        $threwVerifyStart = $true
    }
    finally {
        $PythonExe = $originalPythonExe
    }
    Assert-Equal $threwVerifyStart $true "manifest verification start failure throws"
    Assert-Equal $script:VerifyProcess $null "manifest verification start failure clears process"

    $outputBox.Text = Join-Path $selfTestRoot "download start failure output"
    $fastqGrid.Rows[0].Cells["selected"].Value = $true
    $suppGrid.Rows[0].Cells["supp_selected"].Value = $false
    $PythonExe = Join-Path $selfTestRoot "missing-python.exe"
    $threwDownloadStart = $false
    try {
        Start-DownloadProcess
    }
    catch {
        $threwDownloadStart = $true
    }
    finally {
        $PythonExe = $originalPythonExe
    }
    Assert-Equal $threwDownloadStart $true "download start failure throws"
    Assert-Equal $script:DownloadProcess $null "download start failure clears process"
    Assert-Equal $script:LastDiagnosticError.phase "download_process_start" "download start failure records phase"
    Assert-Equal $script:LastDiagnosticError.code "process_start_failed" "download start failure records code"
    $downloadStartFailureZip = Join-Path $selfTestRoot "download-start-failure-diagnostics.zip"
    Save-DiagnosticsZip $downloadStartFailureZip | Out-Null
    $downloadStartFailureExtract = Join-Path $selfTestRoot "download start failure extract"
    Expand-Archive -LiteralPath $downloadStartFailureZip -DestinationPath $downloadStartFailureExtract -Force
    $downloadStartFailureDiagnostics = Get-Content -Raw -Encoding UTF8 (Join-Path $downloadStartFailureExtract "diagnostics.json") | ConvertFrom-Json
    Assert-Equal $downloadStartFailureDiagnostics.last_error.phase "download_process_start" "diagnostics records download start phase"
    Assert-Equal $downloadStartFailureDiagnostics.last_error.code "process_start_failed" "diagnostics records download start code"

    $outputBox.Text = Join-Path $selfTestRoot "async out folder"
    $suppGrid.Rows[0].Cells["supp_selected"].Value = $false
    $progressBar.Value = 0
    $statusLabel.Text = T "downloading"
    [void]$form.Handle
    Set-Busy $true
    Start-DownloadProcess
    $deadline = [DateTime]::UtcNow.AddSeconds(10)
    while ($null -ne $script:DownloadProcess -and [DateTime]::UtcNow -lt $deadline) {
        [System.Windows.Forms.Application]::DoEvents()
        Start-Sleep -Milliseconds 50
    }
    for ($i = 0; $i -lt 20; $i++) {
        [System.Windows.Forms.Application]::DoEvents()
        Start-Sleep -Milliseconds 50
    }
    Assert-Equal $script:DownloadProcess $null "async download process finished"
    Assert-Equal $statusLabel.Text (T "complete") "async download status"
    $asyncOutputDir = [System.IO.Path]::GetFullPath([string]$outputBox.Text)
    $asyncPrefix = ConvertTo-GeoGetterSafeName ([System.IO.Path]::GetFileName($asyncOutputDir)) -ArtifactPrefix
    Assert-Contains (Get-Content -Raw -Encoding UTF8 (Join-Path $asyncOutputDir ("{0}_download_log.tsv" -f $asyncPrefix))) "md5_verified" "async download log md5 success"

    Write-Output "PowerShell WinForms self test OK"
    $selfTestSucceeded = $true
    }
    finally {
        if ($form -and -not $form.IsDisposed) { $form.Dispose() }
        if ($script:ResolvedJsonPath -and (Test-Path -LiteralPath $script:ResolvedJsonPath)) {
            Remove-Item -LiteralPath $script:ResolvedJsonPath -Force -ErrorAction SilentlyContinue
        }
        if ($selfTestRoot -and (Test-Path -LiteralPath $selfTestRoot)) {
            Remove-Item -LiteralPath $selfTestRoot -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
    if ($selfTestSucceeded) { exit 0 }
}

if ($SmokeTest) {
    if ($ScreenshotPath) { Save-FormScreenshot $ScreenshotPath }
    Write-Output "PowerShell WinForms smoke test OK"
    $form.Dispose()
    exit 0
}

if ($ResolveSmokeInput) {
    $inputBox.Text = $ResolveSmokeInput
    Apply-ResolvedResult (Invoke-ResolveJson $ResolveSmokeInput)
    $fastqCount = @($script:Resolved.fastq_files).Count
    if ($fastqCount -lt 1) {
        throw "FASTQ count is zero: $ResolveSmokeInput"
    }
    if ($ScreenshotPath) { Save-FormScreenshot $ScreenshotPath }
    Write-Output ("PowerShell resolve smoke OK: input={0} fastq={1} supplementary={2} selected_fastq={3} first_run={4} first_size={5}" -f $ResolveSmokeInput, $fastqCount, @($script:Resolved.supplementary_files).Count, (Get-SelectedFastqIndicesOrEmpty), $fastqGrid.Rows[0].Cells["run"].Value, $fastqGrid.Rows[0].Cells["size"].Value)
    $form.Dispose()
    exit 0
}

if ($ScreenshotPath) {
    Save-FormScreenshot $ScreenshotPath
    $form.Dispose()
    exit 0
}

[System.Windows.Forms.Application]::Run($form)
