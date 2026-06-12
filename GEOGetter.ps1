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
    private readonly Action errorClosed;

    public GeoGetterProcessUiBridge(Control control, Action<string> output, Action<string> error, Action<int> exited)
        : this(control, output, error, exited, null, null)
    {
    }

    public GeoGetterProcessUiBridge(Control control, Action<string> output, Action<string> error, Action<int> exited, Action outputClosed)
        : this(control, output, error, exited, outputClosed, null)
    {
    }

    public GeoGetterProcessUiBridge(Control control, Action<string> output, Action<string> error, Action<int> exited, Action outputClosed, Action errorClosed)
    {
        this.control = control;
        this.output = output;
        this.error = error;
        this.exited = exited;
        this.outputClosed = outputClosed;
        this.errorClosed = errorClosed;
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
        process.ErrorDataReceived += (sender, args) =>
        {
            if (args.Data == null)
            {
                InvokeAction(errorClosed);
                return;
            }
            InvokeString(error, args.Data);
        };
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
$script:ResolveCanceled = $false
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
$script:UpdateProcess = $null
$script:UpdateBridge = $null
$script:UpdateCanceled = $false
$script:UpdateStdoutText = ""
$script:UpdateStderrText = ""
$script:ProcessOutputLimitChars = 1048576
$script:LastOperationError = $null
$script:LastResolveExitCode = $null
$script:LastResolveArguments = @()
$script:LastResolveStartError = ""
$script:LastDownloadArguments = @()
$script:LastVerificationArguments = @()
$script:LastUpdateArguments = @()
$script:LastDownloadStartError = ""
$script:LastVerificationStartError = ""
$script:LastUpdateStartError = ""
$script:LastDownloadDoneEvent = $null
$script:LastDownloadExitCode = $null
$script:LastVerificationDoneEvent = $null
$script:LastVerificationExitCode = $null
$script:LastUpdateDoneEvent = $null
$script:LastUpdateExitCode = $null
$script:LastUpdateCommand = ""
$script:ResolveExitObserved = $false
$script:ResolveStdoutClosed = $false
$script:ResolveStderrClosed = $false
$script:ResolveFinalized = $false
$script:DownloadExitObserved = $false
$script:DownloadStdoutClosed = $false
$script:DownloadStderrClosed = $false
$script:DownloadFinalized = $false
$script:VerifyExitObserved = $false
$script:VerifyStdoutClosed = $false
$script:VerifyStderrClosed = $false
$script:VerifyFinalized = $false
$script:UpdateExitObserved = $false
$script:UpdateStdoutClosed = $false
$script:UpdateStderrClosed = $false
$script:UpdateFinalized = $false
$script:LastPreflightStatus = ""
$script:LastPreflightError = ""
$script:LastPreflightOutputDir = ""
$script:LastPreflightRequiredBytes = $null
$script:LastPreflightFreeBytes = $null
$script:LastExistingOutputNonEmpty = $false
$script:LastResumeExistingRequested = $false
$script:LastResumeRequiredBytes = $null
$script:LastResumeErrorCode = ""
$script:ResumeExistingConfirmationForSelfTest = $null
$script:UpdateDownloadConfirmationForSelfTest = $null
$script:InstallerLauncherForSelfTest = $null
$script:ApplicationExitRequestedForSelfTest = $false
$script:LastInputText = ""
$script:LastResolvedInputText = ""
$script:SuppressFastqFilterEvents = $false
$script:Language = $UiLanguage
$script:GridCopyMenuItems = @()
$script:GuiTextResourcePath = Join-Path $AppRoot "resources\gui_text.json"

function Get-GuiTextResourcePath {
    return $script:GuiTextResourcePath
}

function Assert-GuiTextResource {
    param(
        [Parameter(Mandatory = $true)][hashtable]$Translations,
        [Parameter(Mandatory = $true)][string]$Path
    )
    foreach ($language in @("ja", "en")) {
        if (-not $Translations.ContainsKey($language)) {
            throw "GUI text resource missing language '$language': $Path"
        }
        if ($Translations[$language].Count -eq 0) {
            throw "GUI text resource language '$language' has no entries: $Path"
        }
    }

    $missingInEnglish = @($Translations["ja"].Keys | Where-Object { -not $Translations["en"].ContainsKey($_) } | Sort-Object)
    $missingInJapanese = @($Translations["en"].Keys | Where-Object { -not $Translations["ja"].ContainsKey($_) } | Sort-Object)
    if ($missingInEnglish.Count -gt 0 -or $missingInJapanese.Count -gt 0) {
        throw "GUI text resource key mismatch in $Path. Missing in en: $($missingInEnglish -join ', '); missing in ja: $($missingInJapanese -join ', ')"
    }

    foreach ($requiredKey in @("appTitle", "idle", "helpUsage", "helpUsageText", "helpInputText", "helpTablesText", "helpOutputFilesText", "helpIntegrityText", "helpCancelRetryText")) {
        foreach ($language in @("ja", "en")) {
            if (-not $Translations[$language].ContainsKey($requiredKey) -or [string]::IsNullOrWhiteSpace([string]$Translations[$language][$requiredKey])) {
                throw "GUI text resource missing required key '$requiredKey' for '$language': $Path"
            }
        }
    }
}

function Import-GuiTextResource {
    param([string]$Path = (Get-GuiTextResourcePath))
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "GUI text resource not found: $Path"
    }

    try {
        $resource = Get-Content -Raw -Encoding UTF8 -LiteralPath $Path | ConvertFrom-Json
    }
    catch {
        throw "GUI text resource could not be read: $Path. Detail: $($_.Exception.Message)"
    }
    if ($null -eq $resource) {
        throw "GUI text resource is empty: $Path"
    }

    $translations = @{}
    foreach ($language in @("ja", "en")) {
        $languageProperty = @($resource.PSObject.Properties | Where-Object { $_.Name -eq $language } | Select-Object -First 1)
        if ($languageProperty.Count -eq 0) {
            throw "GUI text resource missing language '$language': $Path"
        }
        $languageTable = @{}
        foreach ($entry in $languageProperty[0].Value.PSObject.Properties) {
            $languageTable[$entry.Name] = [string]$entry.Value
        }
        $translations[$language] = $languageTable
    }

    Assert-GuiTextResource -Translations $translations -Path $Path
    return $translations
}

$script:Translations = Import-GuiTextResource
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
    if ($script:Translations["ja"].ContainsKey($Key)) {
        return $script:Translations["ja"][$Key]
    }
    throw "GUI text resource key not found: $Key"
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
    if ($checkUpdatesMenuItem) { $checkUpdatesMenuItem.Text = T "checkUpdatesMenu" }
    Update-GridCopyMenuTexts
    if ($inputLabel) { $inputLabel.Text = T "inputLabel" }
    if ($fetchButton) { $fetchButton.Text = T "fetchButton" }
    if ($outputLabel) { $outputLabel.Text = T "outputLabel" }
    if ($browseButton) { $browseButton.Text = T "browseButton" }
    if ($datasetTitleLabel) { $datasetTitleLabel.Text = T "datasetTitleLabel" }
    Update-ResultTitles
    if ($downloadButton) { $downloadButton.Text = T "downloadButton" }
    if ($downloadWorkersLabel) { $downloadWorkersLabel.Text = T "downloadWorkersLabel" }
    if ($cancelButton) { $cancelButton.Text = T "cancelButton" }
    if ($fastqSelectAllButton) { $fastqSelectAllButton.Text = T "selectAllButton" }
    if ($fastqClearSelectionButton) { $fastqClearSelectionButton.Text = T "clearSelectionButton" }
    Update-FastqFilterTexts
    Update-ResultTitles
    if ($suppSelectAllButton) { $suppSelectAllButton.Text = T "selectAllButton" }
    if ($suppClearSelectionButton) { $suppClearSelectionButton.Text = T "clearSelectionButton" }
    $idleTexts = @($script:Translations["ja"]["idle"], $script:Translations["en"]["idle"])
    if ($statusLabel -and ([string]::IsNullOrWhiteSpace($statusLabel.Text) -or $idleTexts -contains $statusLabel.Text)) {
        $statusLabel.Text = T "idle"
    }
    if ($null -ne $capacityLabel) { Update-Capacity }
    Update-SelectionSummary
    Update-GridHeaders
    Refresh-SupplementaryDisplayRows
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
        $fastqGrid.Columns["strategy"].HeaderText = T "colStrategy"
        $fastqGrid.Columns["size"].HeaderText = T "colSize"
        $fastqGrid.Columns["md5"].HeaderText = T "colMd5"
        $fastqGrid.Columns["url"].HeaderText = T "colFastqUrl"
    }
    if ($suppGrid -and $suppGrid.Columns.Count -gt 0) {
        $suppGrid.Columns["supp_selected"].HeaderText = T "colSelect"
        $suppGrid.Columns["supp_origin"].HeaderText = T "colOrigin"
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
    if ($fastqTitle) {
        if (Test-FastqFilterActive) {
            $fastqTitle.Text = (T "fastqFilteredTitle") -f (Get-FastqVisibleRowCount), $fastqCount
        }
        else {
            $fastqTitle.Text = (T "fastqTitle") -f $fastqCount
        }
    }
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
    if ($SelfTest) { return }
    [System.Windows.Forms.MessageBox]::Show($Message, (T "appTitle"), "OK", "Error") | Out-Null
}

function New-OperationError {
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

function Set-OperationErrorFromProcessOutput {
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
        $script:LastOperationError = New-OperationError $Phase $Command ([string]$event.code) ([string]$event.detail) ([string]$event.message) $source $ExitCode
        return
    }

    $detail = (($Stdout + [Environment]::NewLine + $Stderr).Trim())
    $message = if ([string]::IsNullOrWhiteSpace($DefaultMessage)) { $detail } else { $DefaultMessage }
    $script:LastOperationError = New-OperationError $Phase $Command $DefaultCode $detail $message "process_output" $ExitCode
}

function Get-PreflightErrorCode {
    param([string]$Message)
    if ($Message -match "空き容量|free space") { return "insufficient_space" }
    if ($Message -match "保存先|output folder") { return "output_path_invalid" }
    if ($Message -match "長すぎます|too long") { return "path_too_long" }
    if ($Message -match "FASTQまたは|Select at least one") { return "selection_required" }
    if ($Message -match "検索結果|入力内容|search result|input has changed|Search files again") { return "resolved_state_invalid" }
    return "preflight_failed"
}

function Set-DownloadPreflightError {
    param(
        [string]$Message,
        [bool]$ClearOutputContext = $false,
        [string]$Code = ""
    )
    $script:LastPreflightStatus = "failed"
    $script:LastPreflightError = $Message
    if ($ClearOutputContext) {
        $script:LastPreflightOutputDir = ""
        $script:LastPreflightRequiredBytes = $null
        $script:LastPreflightFreeBytes = $null
    }
    $code = if ([string]::IsNullOrWhiteSpace($Code)) { Get-PreflightErrorCode $script:LastPreflightError } else { $Code }
    if ($code -like "resume_*") {
        $script:LastResumeErrorCode = $code
    }
    $script:LastOperationError = New-OperationError "download_preflight" "selected-download-json" $code $script:LastPreflightError $script:LastPreflightError "gui_preflight" $null
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

function Append-DownloadProcessOutput {
    param(
        [ValidateSet("stdout", "stderr")]
        [string]$Stream,
        [string]$Line
    )
    $value = $Line + [Environment]::NewLine
    if ($Stream -eq "stdout") {
        $script:DownloadStdoutText = Limit-ProcessOutputText ($script:DownloadStdoutText + $value)
        return
    }
    $script:DownloadStderrText = Limit-ProcessOutputText ($script:DownloadStderrText + $value)
}

function Append-ResolveProcessOutput {
    param(
        [ValidateSet("stdout", "stderr")]
        [string]$Stream,
        [string]$Line
    )
    $value = $Line + [Environment]::NewLine
    if ($Stream -eq "stdout") {
        $script:ResolveStdoutText = Limit-ProcessOutputText ($script:ResolveStdoutText + $value)
        return
    }
    $script:ResolveStderrText = Limit-ProcessOutputText ($script:ResolveStderrText + $value)
}

function Append-VerificationProcessOutput {
    param(
        [ValidateSet("stdout", "stderr")]
        [string]$Stream,
        [string]$Line
    )
    $value = $Line + [Environment]::NewLine
    if ($Stream -eq "stdout") {
        $script:VerifyStdoutText = Limit-ProcessOutputText ($script:VerifyStdoutText + $value)
        return
    }
    $script:VerifyStderrText = Limit-ProcessOutputText ($script:VerifyStderrText + $value)
}

function Append-UpdateProcessOutput {
    param(
        [ValidateSet("stdout", "stderr")]
        [string]$Stream,
        [string]$Line
    )
    $value = $Line + [Environment]::NewLine
    if ($Stream -eq "stdout") {
        $script:UpdateStdoutText = Limit-ProcessOutputText ($script:UpdateStdoutText + $value)
        return
    }
    $script:UpdateStderrText = Limit-ProcessOutputText ($script:UpdateStderrText + $value)
}

function Limit-ProcessOutputText {
    param([string]$Text)
    if ([string]::IsNullOrEmpty($Text)) { return "" }
    $limit = [int]$script:ProcessOutputLimitChars
    if ($Text.Length -le $limit) { return $Text }
    $marker = "[GEOGetter process output: earlier process output was truncated]" + [Environment]::NewLine
    $keep = [Math]::Max(0, $limit - $marker.Length)
    if ($Text.Length -le $keep) { return $Text }
    return $marker + $Text.Substring($Text.Length - $keep)
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

function Remove-ResolveInputFile {
    $inputPath = $script:ResolveInputPath
    $script:ResolveInputPath = $null
    if ($inputPath) {
        Remove-Item -LiteralPath $inputPath -ErrorAction SilentlyContinue
    }
}

function Normalize-InputText {
    param([string]$Value)
    if ($null -eq $Value) { return "" }
    return $Value.Trim()
}

function Test-ProcessRunning {
    param([System.Diagnostics.Process]$Process)
    if ($null -eq $Process) { return $false }
    try {
        return -not $Process.HasExited
    }
    catch {
        return $false
    }
}

function Dispose-ProcessQuietly {
    param([System.Diagnostics.Process]$Process)
    if ($null -eq $Process) { return }
    try { $Process.Dispose() } catch { }
}

function Start-GeoGetterPythonProcess {
    param(
        [string]$OperationName,
        [scriptblock]$CreateStartInfo,
        [System.Action[string]]$OutputHandler,
        [System.Action[string]]$ErrorHandler,
        [System.Action[int]]$ExitHandler,
        [System.Action]$OutputClosedHandler,
        [System.Action]$ErrorClosedHandler,
        [scriptblock]$SetProcess,
        [scriptblock]$SetBridge,
        [scriptblock]$OnStartFailure
    )
    if ([string]::IsNullOrWhiteSpace($OperationName)) {
        throw "OperationName is required."
    }

    $process = New-Object System.Diagnostics.Process
    try {
        $process.StartInfo = & $CreateStartInfo
        $process.EnableRaisingEvents = $true
        $bridge = New-Object GeoGetterProcessUiBridge -ArgumentList @(
            $form,
            $OutputHandler,
            $ErrorHandler,
            $ExitHandler,
            $OutputClosedHandler,
            $ErrorClosedHandler
        )
        $bridge.Attach($process)
        & $SetBridge $bridge
        & $SetProcess $process
        [void]$process.Start()
        Update-CancelButton
        $process.BeginOutputReadLine()
        $process.BeginErrorReadLine()
    }
    catch {
        $startError = $_
        if (Test-ProcessRunning $process) {
            try { $process.Kill() } catch { }
        }
        & $SetProcess $null
        & $SetBridge $null
        if ($null -ne $OnStartFailure) {
            & $OnStartFailure $startError.Exception.Message
        }
        Dispose-ProcessQuietly $process
        Update-CancelButton
        throw
    }
}

function Clear-ResolveRunState {
    $script:ResolveCanceled = $false
    $script:ResolveStdoutText = ""
    $script:ResolveStderrText = ""
    $script:LastResolveExitCode = $null
    $script:LastResolveArguments = @()
    $script:LastResolveStartError = ""
    $script:ResolveExitObserved = $false
    $script:ResolveStdoutClosed = $false
    $script:ResolveStderrClosed = $false
    $script:ResolveFinalized = $false
}

function Clear-DownloadRunState {
    $script:DownloadCanceled = $false
    $script:DownloadStdoutText = ""
    $script:DownloadStderrText = ""
    $script:LastDownloadDoneEvent = $null
    $script:LastDownloadExitCode = $null
    $script:LastDownloadArguments = @()
    $script:LastDownloadStartError = ""
    $script:DownloadExitObserved = $false
    $script:DownloadStdoutClosed = $false
    $script:DownloadStderrClosed = $false
    $script:DownloadFinalized = $false
    $script:LastResumeExistingRequested = $false
    $script:LastResumeRequiredBytes = $null
    $script:LastResumeErrorCode = ""
}

function Clear-VerificationRunState {
    $script:VerifyCanceled = $false
    $script:VerifyStdoutText = ""
    $script:VerifyStderrText = ""
    $script:LastVerificationDoneEvent = $null
    $script:LastVerificationExitCode = $null
    $script:LastVerificationArguments = @()
    $script:LastVerificationStartError = ""
    $script:VerifyExitObserved = $false
    $script:VerifyStdoutClosed = $false
    $script:VerifyStderrClosed = $false
    $script:VerifyFinalized = $false
}

function Clear-UpdateRunState {
    $script:UpdateCanceled = $false
    $script:UpdateStdoutText = ""
    $script:UpdateStderrText = ""
    $script:LastUpdateDoneEvent = $null
    $script:LastUpdateExitCode = $null
    $script:LastUpdateArguments = @()
    $script:LastUpdateStartError = ""
    $script:LastUpdateCommand = ""
    $script:UpdateExitObserved = $false
    $script:UpdateStdoutClosed = $false
    $script:UpdateStderrClosed = $false
    $script:UpdateFinalized = $false
}

function Clear-ResolvedState {
    param(
        [switch]$DeleteResolvedJson,
        [switch]$PreserveResolveRunState
    )
    $script:Resolved = $null
    $script:LastResolvedInputText = ""
    if (-not $PreserveResolveRunState) {
        Clear-ResolveRunState
        $script:LastOperationError = $null
    }
    Clear-DownloadRunState
    Clear-VerificationRunState
    $script:LastPreflightStatus = ""
    $script:LastPreflightError = ""
    $script:LastPreflightOutputDir = ""
    $script:LastPreflightRequiredBytes = $null
    $script:LastPreflightFreeBytes = $null
    $script:LastExistingOutputNonEmpty = $false
    $script:LastResumeExistingRequested = $false
    $script:LastResumeRequiredBytes = $null
    $script:LastResumeErrorCode = ""
    if ($fastqGrid) { $fastqGrid.Rows.Clear() }
    if ($suppGrid) { $suppGrid.Rows.Clear() }
    Reset-FastqFilterControls -SkipApply
    Refresh-FastqFilterValueOptions
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
    Reset-FastqFilterControls -SkipApply
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

function Complete-ResolveIfReady {
    if ($script:ResolveFinalized) { return }
    if (-not $script:ResolveExitObserved -or -not $script:ResolveStdoutClosed -or -not $script:ResolveStderrClosed) { return }

    $script:ResolveFinalized = $true
    $process = $script:ResolveProcess
    try {
        $script:ResolveProcess = $null
        Update-CancelButton
        Remove-ResolveInputFile
        $progressBar.Style = "Continuous"
        $progressBar.Value = 0
        if ($script:ResolveCanceled) {
            Clear-ResolvedState -DeleteResolvedJson -PreserveResolveRunState
            $statusLabel.Text = T "canceled"
            return
        }
        if ($script:LastResolveExitCode -eq 0) {
            try {
                if (-not (Test-Path -LiteralPath $script:ResolvedJsonPath)) {
                    throw "resolve-json completed without writing resolved JSON."
                }
                Apply-ResolvedResult (Get-Content -Raw -Encoding UTF8 $script:ResolvedJsonPath | ConvertFrom-Json)
            }
            catch {
                $detail = $_.Exception.Message
                Clear-ResolvedState -DeleteResolvedJson -PreserveResolveRunState
                $script:LastOperationError = New-OperationError "resolve" "resolve-json" "resolve_output_invalid" $detail (T "metadataFailed") "gui_resolve_output" $script:LastResolveExitCode
                $statusLabel.Text = T "error"
                Show-AppError (T "metadataFailed")
            }
        }
        else {
            Clear-ResolvedState -DeleteResolvedJson -PreserveResolveRunState
            Set-OperationErrorFromProcessOutput "resolve" "resolve-json" $script:LastResolveExitCode $script:ResolveStdoutText $script:ResolveStderrText "resolve_failed" (T "metadataFailed")
            $message = (($script:ResolveStdoutText + $script:ResolveStderrText).Trim())
            if ([string]::IsNullOrWhiteSpace($message)) {
                $message = T "metadataFailed"
            }
            if ($null -ne $script:LastOperationError -and -not [string]::IsNullOrWhiteSpace([string]$script:LastOperationError.message)) {
                $message = [string]$script:LastOperationError.message
            }
            $statusLabel.Text = T "error"
            Show-AppError $message
        }
    }
    finally {
        Dispose-ProcessQuietly $process
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
        if (Test-FastqRowSelectedVisible $row) {
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
        if (Test-FastqRowSelectedVisible $row) {
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
    $visibleOnly = ($Grid -eq $fastqGrid)
    foreach ($row in $Grid.Rows) {
        if ($row.IsNewRow) { continue }
        if ($visibleOnly -and -not $row.Visible) { continue }
        $row.Cells[$ColumnName].Value = $Selected
    }
    Update-Capacity
}

function Get-GridCurrentCellText {
    param([System.Windows.Forms.DataGridView]$Grid)
    if ($null -eq $Grid -or $null -eq $Grid.CurrentCell) { return "" }
    if ($Grid.CurrentCell.RowIndex -lt 0 -or $Grid.CurrentCell.ColumnIndex -lt 0) { return "" }
    $value = $Grid.CurrentCell.Value
    if ($null -eq $value) { return "" }
    return [string]$value
}

function Copy-CurrentGridCell {
    param([System.Windows.Forms.DataGridView]$Grid)
    $text = Get-GridCurrentCellText $Grid
    if ([string]::IsNullOrEmpty($text)) { return }
    [System.Windows.Forms.Clipboard]::SetText($text)
}

function Update-GridCopyMenuTexts {
    foreach ($item in @($script:GridCopyMenuItems)) {
        if ($null -ne $item) { $item.Text = T "copyCellMenu" }
    }
}

function Add-GridCellCopyHandlers {
    param([System.Windows.Forms.DataGridView]$Grid)
    if ($null -eq $Grid) { return }

    $copyMenu = New-Object System.Windows.Forms.ContextMenuStrip
    $copyItem = New-Object System.Windows.Forms.ToolStripMenuItem
    $copyItem.Text = T "copyCellMenu"
    [void]$copyMenu.Items.Add($copyItem)
    $script:GridCopyMenuItems += $copyItem

    $gridForCopy = $Grid
    $copyItem.Add_Click({ Copy-CurrentGridCell $gridForCopy }.GetNewClosure())
    $Grid.ContextMenuStrip = $copyMenu
    $Grid.Add_CellMouseDown({
        param($sender, $eventArgs)
        if ($eventArgs.Button -ne [System.Windows.Forms.MouseButtons]::Right) { return }
        if ($eventArgs.RowIndex -lt 0 -or $eventArgs.ColumnIndex -lt 0) { return }
        $sender.CurrentCell = $sender.Rows[$eventArgs.RowIndex].Cells[$eventArgs.ColumnIndex]
    })
    $Grid.Add_KeyDown({
        param($sender, $eventArgs)
        if (-not $eventArgs.Control -or $eventArgs.KeyCode -ne [System.Windows.Forms.Keys]::C) { return }
        Copy-CurrentGridCell $sender
        $eventArgs.Handled = $true
        $eventArgs.SuppressKeyPress = $true
    })
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
        if (Test-FastqRowSelectedVisible $row) {
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
        $capacityLabel.Text = (T "capacityText") -f (Format-Bytes $total), (Format-Bytes ([Int64]$freeBytes))
    }
    else {
        $capacityLabel.Text = (T "capacityUnknown") -f (Format-Bytes $total)
    }
    Update-SelectionSummary
}

function Get-ObjectPropertyString {
    param(
        $Object,
        [string]$Name
    )
    if ($null -eq $Object) { return "" }
    $property = $Object.PSObject.Properties[$Name]
    if ($null -eq $property -or $null -eq $property.Value) { return "" }
    return [string]$property.Value
}

function Get-FastqComboSelectedValue {
    param([System.Windows.Forms.ComboBox]$Combo)
    if ($null -eq $Combo -or $Combo.SelectedIndex -le 0) { return "" }
    return [string]$Combo.SelectedItem
}

function Set-ComboBoxItems {
    param(
        [System.Windows.Forms.ComboBox]$Combo,
        [string[]]$Items,
        [int]$SelectedIndex = 0
    )
    if ($null -eq $Combo) { return }
    $Combo.BeginUpdate()
    try {
        $Combo.Items.Clear()
        foreach ($item in $Items) {
            if ($null -eq $item) { continue }
            [void]$Combo.Items.Add($item)
        }
    }
    finally {
        $Combo.EndUpdate()
    }
    if ($Combo.Items.Count -gt 0) {
        $Combo.SelectedIndex = [Math]::Min([Math]::Max($SelectedIndex, 0), $Combo.Items.Count - 1)
    }
}

function Set-ComboBoxItemsPreservingValue {
    param(
        [System.Windows.Forms.ComboBox]$Combo,
        [string[]]$Values,
        [string]$SelectedValue
    )
    $items = @((T "fastqFilterAll")) + @($Values)
    $selectedIndex = 0
    if (-not [string]::IsNullOrWhiteSpace($SelectedValue)) {
        for ($i = 1; $i -lt $items.Count; $i++) {
            if ([string]::Equals($items[$i], $SelectedValue, [System.StringComparison]::OrdinalIgnoreCase)) {
                $selectedIndex = $i
                break
            }
        }
    }
    Set-ComboBoxItems $Combo $items $selectedIndex
}

function Get-FastqFilterDistinctValues {
    param([string]$PropertyName)
    if ($null -eq $script:Resolved) { return @() }
    $seen = [System.Collections.Hashtable]::new([System.StringComparer]::OrdinalIgnoreCase)
    $values = @()
    foreach ($item in @($script:Resolved.fastq_files)) {
        $value = (Get-ObjectPropertyString $item $PropertyName).Trim()
        if ([string]::IsNullOrWhiteSpace($value)) { continue }
        if (-not $seen.ContainsKey($value)) {
            $seen[$value] = $true
            $values += $value
        }
    }
    return @($values | Sort-Object)
}

function Update-FastqFilterTexts {
    if ($fastqFilterLabel) { $fastqFilterLabel.Text = T "fastqFilterLabel" }
    if ($fastqFilterKeywordLabel) { $fastqFilterKeywordLabel.Text = T "fastqFilterKeywordLabel" }
    if ($fastqFilterLayoutLabel) { $fastqFilterLayoutLabel.Text = T "fastqFilterLayoutLabel" }
    if ($fastqFilterStrategyLabel) { $fastqFilterStrategyLabel.Text = T "fastqFilterStrategyLabel" }
    if ($fastqClearFilterButton) { $fastqClearFilterButton.Text = T "clearFastqFilterButton" }

    $wasSuppressed = $script:SuppressFastqFilterEvents
    $script:SuppressFastqFilterEvents = $true
    try {
        Refresh-FastqFilterValueOptions
    }
    finally {
        $script:SuppressFastqFilterEvents = $wasSuppressed
    }
}

function Refresh-FastqFilterValueOptions {
    $layoutSelection = Get-FastqComboSelectedValue $fastqLayoutFilterCombo
    $strategySelection = Get-FastqComboSelectedValue $fastqStrategyFilterCombo
    Set-ComboBoxItemsPreservingValue $fastqLayoutFilterCombo (Get-FastqFilterDistinctValues "library_layout") $layoutSelection
    Set-ComboBoxItemsPreservingValue $fastqStrategyFilterCombo (Get-FastqFilterDistinctValues "library_strategy") $strategySelection
}

function Reset-FastqFilterControls {
    param([switch]$SkipApply)
    $wasSuppressed = $script:SuppressFastqFilterEvents
    $script:SuppressFastqFilterEvents = $true
    try {
        if ($fastqFilterBox) { $fastqFilterBox.Text = "" }
        if ($fastqLayoutFilterCombo -and $fastqLayoutFilterCombo.Items.Count -gt 0) { $fastqLayoutFilterCombo.SelectedIndex = 0 }
        if ($fastqStrategyFilterCombo -and $fastqStrategyFilterCombo.Items.Count -gt 0) { $fastqStrategyFilterCombo.SelectedIndex = 0 }
    }
    finally {
        $script:SuppressFastqFilterEvents = $wasSuppressed
    }
    if (-not $SkipApply) {
        Apply-FastqFilter
    }
}

function Test-FastqFilterActive {
    if ($fastqFilterBox -and -not [string]::IsNullOrWhiteSpace([string]$fastqFilterBox.Text)) { return $true }
    if ($fastqLayoutFilterCombo -and $fastqLayoutFilterCombo.SelectedIndex -gt 0) { return $true }
    if ($fastqStrategyFilterCombo -and $fastqStrategyFilterCombo.SelectedIndex -gt 0) { return $true }
    return $false
}

function Get-FastqVisibleRowCount {
    if ($null -eq $fastqGrid) { return 0 }
    $count = 0
    foreach ($row in $fastqGrid.Rows) {
        if ($row.IsNewRow) { continue }
        if ($row.Visible) { $count += 1 }
    }
    return $count
}

function Test-FastqRowSelectedVisible {
    param([System.Windows.Forms.DataGridViewRow]$Row)
    if ($null -eq $Row -or $Row.IsNewRow -or -not $Row.Visible) { return $false }
    if ($null -eq $Row.Cells["selected"].Value) { return $false }
    return [bool]$Row.Cells["selected"].Value
}

function Test-FastqCellContains {
    param(
        [System.Windows.Forms.DataGridViewRow]$Row,
        [string]$ColumnName,
        [string]$Needle
    )
    if ([string]::IsNullOrWhiteSpace($Needle)) { return $true }
    $value = [string]$Row.Cells[$ColumnName].Value
    return $value.IndexOf($Needle, [System.StringComparison]::OrdinalIgnoreCase) -ge 0
}

function Test-FastqRowMatchesKeyword {
    param(
        [System.Windows.Forms.DataGridViewRow]$Row,
        [string]$Keyword
    )
    if ([string]::IsNullOrWhiteSpace($Keyword)) { return $true }
    foreach ($columnName in @("run", "geo_sample", "geo_title", "file_name", "sample", "layout", "strategy", "md5", "url")) {
        if (Test-FastqCellContains $Row $columnName $Keyword) { return $true }
    }
    return $false
}

function Test-FastqRowMatchesSelectedText {
    param(
        [System.Windows.Forms.DataGridViewRow]$Row,
        [string]$ColumnName,
        [string]$SelectedValue
    )
    if ([string]::IsNullOrWhiteSpace($SelectedValue)) { return $true }
    return [string]::Equals([string]$Row.Cells[$ColumnName].Value, $SelectedValue, [System.StringComparison]::OrdinalIgnoreCase)
}

function Test-FastqRowMatchesFilter {
    param([System.Windows.Forms.DataGridViewRow]$Row)
    if ($null -eq $Row -or $Row.IsNewRow) { return $false }
    $keyword = if ($fastqFilterBox) { [string]$fastqFilterBox.Text } else { "" }
    if (-not (Test-FastqRowMatchesKeyword $Row $keyword)) { return $false }
    if (-not (Test-FastqRowMatchesSelectedText $Row "layout" (Get-FastqComboSelectedValue $fastqLayoutFilterCombo))) { return $false }
    if (-not (Test-FastqRowMatchesSelectedText $Row "strategy" (Get-FastqComboSelectedValue $fastqStrategyFilterCombo))) { return $false }
    return $true
}

function Apply-FastqFilter {
    if ($script:SuppressFastqFilterEvents -or $null -eq $fastqGrid) { return }
    try {
        $fastqGrid.CurrentCell = $null
    }
    catch {
    }
    foreach ($row in $fastqGrid.Rows) {
        if ($row.IsNewRow) { continue }
        $visible = Test-FastqRowMatchesFilter $row
        if (-not $visible -and [bool]$row.Cells["selected"].Value) {
            $row.Cells["selected"].Value = $false
        }
        if ($row.Visible -ne $visible) {
            $row.Visible = $visible
        }
    }
    Update-ResultTitles
    Update-Capacity
}

function Get-SupplementarySelectionSummary {
    param([int]$Count)
    if ($Count -gt 0) {
        return (T "supplementarySelectedSummary") -f $Count
    }
    return (T "supplementaryNoneSummary") -f $Count
}

function Get-SupplementaryOriginDisplay {
    param($Item)
    $level = (Get-ObjectPropertyString $Item "origin_level").ToLowerInvariant()
    $accession = Get-ObjectPropertyString $Item "origin_accession"
    if ([string]::IsNullOrWhiteSpace($accession)) {
        $accession = Get-ObjectPropertyString $Item "source_accession"
    }
    if ([string]::IsNullOrWhiteSpace($accession)) {
        $accession = "-"
    }
    if ($level -eq "series") { return (T "suppOriginSeries") -f $accession }
    if ($level -eq "sample") { return (T "suppOriginSample") -f $accession }
    return (T "suppOriginUnknown") -f $accession
}

function Update-SupplementaryRowDisplay {
    param(
        [System.Windows.Forms.DataGridViewRow]$Row,
        $Item
    )
    $Row.Cells["supp_origin"].Value = Get-SupplementaryOriginDisplay $Item
    $Row.Cells["supp_name"].Value = $Item.name
    $Row.Cells["supp_url"].Value = $Item.url
}

function Refresh-SupplementaryDisplayRows {
    if ($null -eq $suppGrid -or $null -eq $script:Resolved) { return }
    $items = @($script:Resolved.supplementary_files)
    foreach ($row in $suppGrid.Rows) {
        if ($row.IsNewRow -or $null -eq $row.Tag) { continue }
        $index = [int]$row.Tag
        if ($index -lt 0 -or $index -ge $items.Count) { continue }
        Update-SupplementaryRowDisplay $row $items[$index]
    }
}

function Update-SelectionSummary {
    if ($null -eq $selectionSummaryLabel) { return }
    $fastqCount = Get-SelectedFastqCount
    $suppCount = Get-SelectedSuppCount
    $suppSummary = Get-SupplementarySelectionSummary $suppCount
    $output = if ($outputBox) { [string]$outputBox.Text } else { "" }
    $selectionSummaryLabel.Text = (T "selectionSummary") -f $fastqCount, (Format-Bytes (Get-SelectedTotalBytes)), $suppSummary, $output
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

function Test-DirectoryHasEntries {
    param([string]$Path)
    if (-not [System.IO.Directory]::Exists($Path)) { return $false }
    $entries = [System.IO.Directory]::EnumerateFileSystemEntries($Path)
    $enumerator = $entries.GetEnumerator()
    try {
        return $enumerator.MoveNext()
    }
    finally {
        if ($enumerator -is [System.IDisposable]) { $enumerator.Dispose() }
    }
}

function Confirm-ResumeExistingOutput {
    param([string]$OutputDir)
    if ($SelfTest -and $null -ne $script:ResumeExistingConfirmationForSelfTest) {
        return [bool]$script:ResumeExistingConfirmationForSelfTest
    }
    $result = [System.Windows.Forms.MessageBox]::Show(
        ((T "resumeExistingPrompt") -f $OutputDir),
        (T "resumeExistingTitle"),
        [System.Windows.Forms.MessageBoxButtons]::YesNo,
        [System.Windows.Forms.MessageBoxIcon]::Warning
    )
    return $result -eq [System.Windows.Forms.DialogResult]::Yes
}

function Get-PreflightErrorMessage {
    param([object]$OperationError)
    if ($null -eq $OperationError) {
        return "preflight-json failed."
    }
    switch ([string]$OperationError.code) {
        "resume_supplementary_unsupported" { return T "resumeSupplementaryUnsupported" }
        default {
            if (-not [string]::IsNullOrWhiteSpace([string]$OperationError.message)) {
                return [string]$OperationError.message
            }
        }
    }
    return [string]$OperationError.detail
}

function Invoke-DownloadPreflightJsonForGui {
    param(
        [string]$FastqIndices,
        [string]$SuppIndices,
        [string]$OutputDir,
        [bool]$ResumeExisting = $false
    )
    $arguments = Get-PreflightPythonArguments $FastqIndices $SuppIndices $OutputDir $ResumeExisting
    try {
        $result = Invoke-PythonCli -Arguments $arguments
    }
    catch {
        $message = $_.Exception.Message
        $script:LastOperationError = New-OperationError "download_preflight" "preflight-json" "process_start_failed" $message $message "process_start" $null
        throw $message
    }
    if ($result.ExitCode -ne 0) {
        Set-OperationErrorFromProcessOutput "download_preflight" "preflight-json" $result.ExitCode $result.Stdout $result.Stderr "preflight_failed" ""
        if ($null -ne $script:LastOperationError -and [string]$script:LastOperationError.code -like "resume_*") {
            $script:LastResumeErrorCode = [string]$script:LastOperationError.code
        }
        $message = Get-PreflightErrorMessage $script:LastOperationError
        if ([string]::IsNullOrWhiteSpace($message)) {
            $message = Join-ProcessOutput $result
        }
        throw $message
    }
    try {
        return ($result.Stdout | ConvertFrom-Json)
    }
    catch {
        $detail = $_.Exception.Message
        $script:LastOperationError = New-OperationError "download_preflight" "preflight-json" "preflight_output_invalid" $detail $detail "gui_preflight_output" $result.ExitCode
        throw $detail
    }
}

function Test-DownloadPreflight {
    param([bool]$ResumeExisting = $false)
    $script:LastPreflightStatus = "running"
    $script:LastPreflightError = ""
    $script:LastPreflightOutputDir = ""
    $script:LastPreflightRequiredBytes = $null
    $script:LastPreflightFreeBytes = $null
    $script:LastExistingOutputNonEmpty = $false
    $script:LastResumeExistingRequested = $false
    $script:LastResumeRequiredBytes = $null
    $script:LastResumeErrorCode = ""
    $script:LastOperationError = $null
    try {
        if (-not $outputBox -or [string]::IsNullOrWhiteSpace($outputBox.Text)) {
            throw (T "preflightOutputRequired")
        }
        $runOutputDir = [System.IO.Path]::GetFullPath([string]$outputBox.Text)
        if ([System.IO.File]::Exists($runOutputDir)) {
            throw ((T "preflightOutputIsFile") -f $runOutputDir)
        }

        $script:LastPreflightOutputDir = $runOutputDir
        Assert-PreflightPathLength @($runOutputDir)

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

        $script:LastExistingOutputNonEmpty = Test-DirectoryHasEntries $runOutputDir
        $preflight = Invoke-DownloadPreflightJsonForGui (Get-SelectedFastqIndicesOrEmpty) (Get-SelectedSuppIndicesOrEmpty) $runOutputDir $ResumeExisting
        $plannedPaths = @($preflight.planned_paths | ForEach-Object { [string]$_ })
        Assert-PreflightPathLength $plannedPaths

        $existingOutputNonEmpty = [bool]$preflight.existing_output_nonempty
        $script:LastExistingOutputNonEmpty = $existingOutputNonEmpty
        $requiredBytes = [Int64]$preflight.required_bytes
        $freeBytes = $null
        $freeBytesProperty = @($preflight.PSObject.Properties | Where-Object { $_.Name -eq "free_bytes" } | Select-Object -First 1)
        if ($freeBytesProperty.Count -gt 0 -and $null -ne $freeBytesProperty[0].Value) {
            $freeBytes = [Int64]$freeBytesProperty[0].Value
        }
        $resumeBytesProperty = @($preflight.PSObject.Properties | Where-Object { $_.Name -eq "resume_required_bytes" } | Select-Object -First 1)
        if ($resumeBytesProperty.Count -gt 0 -and $null -ne $resumeBytesProperty[0].Value) {
            $script:LastResumeRequiredBytes = [Int64]$resumeBytesProperty[0].Value
        }
        $script:LastPreflightRequiredBytes = $requiredBytes
        $script:LastPreflightFreeBytes = $freeBytes
        if ((-not $existingOutputNonEmpty -or $ResumeExisting) -and $null -ne $freeBytes -and $requiredBytes -gt [Int64]$freeBytes) {
            throw ((T "preflightInsufficientSpace") -f (Format-Bytes $requiredBytes), (Format-Bytes ([Int64]$freeBytes)))
        }

        $script:LastPreflightStatus = "ok"
        return [pscustomobject]@{
            OutputDir = $runOutputDir
            RequiredBytes = $requiredBytes
            FreeBytes = $freeBytes
            ExistingOutputNonEmpty = $existingOutputNonEmpty
            PlannedPaths = $plannedPaths
            Preflight = $preflight
        }
    }
    catch {
        if ($script:LastPreflightStatus -ne "failed" -or $script:LastPreflightError -ne $_.Exception.Message) {
            if ($null -ne $script:LastOperationError -and $script:LastOperationError.phase -eq "download_preflight") {
                $script:LastPreflightStatus = "failed"
                $script:LastPreflightError = $_.Exception.Message
            }
            else {
                Set-DownloadPreflightError $_.Exception.Message
            }
        }
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
        $strategy = Get-ObjectPropertyString $item "library_strategy"
        $rowIndex = $fastqGrid.Rows.Add($false, $item.run_accession, $geoSample, $geoTitle, $item.file_name, $item.sample_accession, $item.library_layout, $strategy, (Format-Bytes ([Int64]$item.size_bytes)), $item.expected_md5, $item.url, $i, ([Int64]$item.size_bytes), ([Int64]$item.file_index))
        $fastqGrid.Rows[$rowIndex].Tag = $i
    }
    if ($fastqGrid.Rows.Count -gt 0) {
        $fastqGrid.Sort($fastqGrid.Columns["run"], [System.ComponentModel.ListSortDirection]::Ascending)
        $fastqGrid.ClearSelection()
    }
    Refresh-FastqFilterValueOptions
    Apply-FastqFilter
}

function Add-SupplementaryRowsFromResolved {
    $suppGrid.Rows.Clear()
    if ($null -eq $script:Resolved) { return }
    $items = @($script:Resolved.supplementary_files)
    for ($i = 0; $i -lt $items.Count; $i++) {
        $item = $items[$i]
        $rowIndex = $suppGrid.Rows.Add(
            $false,
            (Get-SupplementaryOriginDisplay $item),
            $item.name,
            $item.url,
            $i
        )
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
    if ($verifyManifestMenuItem) { $verifyManifestMenuItem.Enabled = -not $Busy }
    if ($checkUpdatesMenuItem) { $checkUpdatesMenuItem.Enabled = -not $Busy }
    if ($fastqSelectAllButton) { $fastqSelectAllButton.Enabled = -not $Busy }
    if ($fastqClearSelectionButton) { $fastqClearSelectionButton.Enabled = -not $Busy }
    if ($fastqFilterBox) { $fastqFilterBox.Enabled = -not $Busy }
    if ($fastqLayoutFilterCombo) { $fastqLayoutFilterCombo.Enabled = -not $Busy }
    if ($fastqStrategyFilterCombo) { $fastqStrategyFilterCombo.Enabled = -not $Busy }
    if ($fastqClearFilterButton) { $fastqClearFilterButton.Enabled = -not $Busy }
    if ($downloadWorkersUpDown) { $downloadWorkersUpDown.Enabled = -not $Busy }
    if ($suppSelectAllButton) { $suppSelectAllButton.Enabled = -not $Busy }
    if ($suppClearSelectionButton) { $suppClearSelectionButton.Enabled = -not $Busy }
    Update-CancelButton
}

function Update-CancelButton {
    if ($null -eq $cancelButton) { return }
    $cancelButton.Enabled = (
        (Test-ProcessRunning $script:ResolveProcess) -or
        (Test-ProcessRunning $script:DownloadProcess) -or
        (Test-ProcessRunning $script:VerifyProcess) -or
        (Test-ProcessRunning $script:UpdateProcess)
    )
}

function Stop-RunningGuiProcesses {
    $canceledAny = $false
    if (Test-ProcessRunning $script:ResolveProcess) {
        $canceledAny = $true
        $script:ResolveCanceled = $true
        Append-Log (T "resolveCancelRequestLog")
        try {
            $script:ResolveProcess.Kill()
        }
        catch {
            Append-Log ((T "cancelFailedLog") -f $_.Exception.Message)
        }
    }
    if (Test-ProcessRunning $script:DownloadProcess) {
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
    if (Test-ProcessRunning $script:VerifyProcess) {
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
    if (Test-ProcessRunning $script:UpdateProcess) {
        $canceledAny = $true
        $script:UpdateCanceled = $true
        Append-Log (T "updateCancelRequestLog")
        try {
            $script:UpdateProcess.Kill()
        }
        catch {
            Append-Log ((T "cancelFailedLog") -f $_.Exception.Message)
        }
    }
    if ($canceledAny) { Update-CancelButton }
    return $canceledAny
}

function Stop-RunningGuiProcessesForShutdown {
    if (Test-ProcessRunning $script:ResolveProcess) {
        $script:ResolveCanceled = $true
        try { $script:ResolveProcess.Kill() } catch { }
    }
    Remove-ResolveInputFile
    if (Test-ProcessRunning $script:DownloadProcess) {
        $script:DownloadCanceled = $true
        try { $script:DownloadProcess.Kill() } catch { }
    }
    if (Test-ProcessRunning $script:VerifyProcess) {
        $script:VerifyCanceled = $true
        try { $script:VerifyProcess.Kill() } catch { }
    }
    if (Test-ProcessRunning $script:UpdateProcess) {
        $script:UpdateCanceled = $true
        try { $script:UpdateProcess.Kill() } catch { }
    }
    Update-CancelButton
}

function Handle-DownloadLine {
    param([string]$Line)
    if ([string]::IsNullOrWhiteSpace($Line)) { return }
    try {
        $event = $Line | ConvertFrom-Json
        if ($event.event -eq "progress") {
            $total = [Int64]$event.total
            $downloaded = [Int64]$event.downloaded
            $aggregateTotalProperty = @($event.PSObject.Properties | Where-Object { $_.Name -eq "aggregate_total" } | Select-Object -First 1)
            $aggregateDownloadedProperty = @($event.PSObject.Properties | Where-Object { $_.Name -eq "aggregate_downloaded" } | Select-Object -First 1)
            if ($aggregateTotalProperty.Count -gt 0 -and $null -ne $aggregateTotalProperty[0].Value -and $aggregateDownloadedProperty.Count -gt 0 -and $null -ne $aggregateDownloadedProperty[0].Value) {
                $total = [Int64]$aggregateTotalProperty[0].Value
                $downloaded = [Int64]$aggregateDownloadedProperty[0].Value
            }
            $progressBar.Value = if ($total -gt 0) { [Math]::Min(100, [int](($downloaded / $total) * 100)) } else { 0 }
            $statusLabel.Text = T "downloading"
        }
        elseif ($event.event -eq "message") {
            $messageText = [string]$event.message
            Append-Log $messageText
            if ($messageText -like "network_retry:*") {
                $statusLabel.Text = T "downloadRetryWaiting"
            }
        }
        elseif ($event.event -eq "done") {
            $script:LastDownloadDoneEvent = $event
            $resumeBytesProperty = @($event.PSObject.Properties | Where-Object { $_.Name -eq "resume_required_bytes" } | Select-Object -First 1)
            if ($resumeBytesProperty.Count -gt 0 -and $null -ne $resumeBytesProperty[0].Value) {
                $script:LastResumeRequiredBytes = [Int64]$resumeBytesProperty[0].Value
            }
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

function Handle-DownloadErrorLine {
    param([string]$Line)
    if ([string]::IsNullOrWhiteSpace($Line)) { return }
    try {
        $event = $Line | ConvertFrom-Json
        if ($event.event -eq "error") {
            $script:LastOperationError = New-OperationError "download" ([string]$event.command) ([string]$event.code) ([string]$event.detail) ([string]$event.message) "cli_stderr_json" $script:LastDownloadExitCode
            if ([string]$event.code -like "resume_*") {
                $script:LastResumeErrorCode = [string]$event.code
            }
            Append-Log ([string]$event.message)
            return
        }
    }
    catch { }
    Append-Log $Line
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
    if (-not $script:DownloadExitObserved -or -not $script:DownloadStdoutClosed -or -not $script:DownloadStderrClosed) { return }

    $script:DownloadFinalized = $true
    $process = $script:DownloadProcess
    Set-Busy $false
    $script:DownloadProcess = $null
    Update-CancelButton
    Dispose-ProcessQuietly $process
    $statusKey = Get-DownloadFinalStatusKey $script:LastDownloadDoneEvent $script:LastDownloadExitCode $script:DownloadCanceled
    $statusLabel.Text = T $statusKey
    if ($statusKey -eq "error") {
        $progressBar.Value = 0
        if ($null -eq $script:LastOperationError) {
            Set-OperationErrorFromProcessOutput "download" "selected-download-json" $script:LastDownloadExitCode $script:DownloadStdoutText $script:DownloadStderrText "download_failed_before_done" "Download process ended before the done event."
        }
    }
}

function Complete-ManifestVerificationIfReady {
    if ($script:VerifyFinalized) { return }
    if (-not $script:VerifyExitObserved -or -not $script:VerifyStdoutClosed -or -not $script:VerifyStderrClosed) { return }

    $script:VerifyFinalized = $true
    $process = $script:VerifyProcess
    $progressBar.Style = "Continuous"
    Set-Busy $false
    $script:VerifyProcess = $null
    Update-CancelButton
    Dispose-ProcessQuietly $process
    if ($script:VerifyCanceled) {
        $statusLabel.Text = T "canceled"
        return
    }
    if ($null -eq $script:LastVerificationDoneEvent) {
        $progressBar.Value = 0
        $statusLabel.Text = T "error"
        if ($null -eq $script:LastOperationError) {
            Set-OperationErrorFromProcessOutput "verification" "verify-manifest-json" $script:LastVerificationExitCode $script:VerifyStdoutText $script:VerifyStderrText "verification_failed_before_report" (T "verifyManifestNoReport")
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

function Handle-UpdateLine {
    param([string]$Line)
    if ([string]::IsNullOrWhiteSpace($Line)) { return }
    try {
        $event = $Line | ConvertFrom-Json
        if ($event.event -eq "done" -and ($event.kind -eq "update_check" -or $event.kind -eq "update_installer")) {
            $script:LastUpdateDoneEvent = $event
            if ($event.kind -eq "update_installer") {
                $progressBar.Style = "Continuous"
                $progressBar.Value = 100
            }
            Complete-UpdateIfReady
        }
        elseif ($event.event -eq "message") {
            Append-Log ([string]$event.message)
        }
        else {
            Append-Log $Line
        }
    }
    catch {
        Append-Log $Line
    }
}

function Handle-UpdateErrorLine {
    param([string]$Line)
    if ([string]::IsNullOrWhiteSpace($Line)) { return }
    try {
        $event = $Line | ConvertFrom-Json
        if ($event.event -eq "error") {
            $script:LastOperationError = New-OperationError "update" ([string]$event.command) ([string]$event.code) ([string]$event.detail) ([string]$event.message) "cli_stderr_json" $script:LastUpdateExitCode
            Append-Log (Get-UpdateFailureReason)
            return
        }
    }
    catch { }
    Append-Log $Line
}

function Get-UpdateFailureReason {
    if ($null -eq $script:LastOperationError) {
        return T "updateNoResult"
    }
    switch ([string]$script:LastOperationError.code) {
        "update_asset_missing" { return T "updateAssetMissingMessage" }
        "update_asset_url_missing" { return T "updateAssetMissingMessage" }
        "update_digest_missing" { return T "updateDigestMissingMessage" }
        "update_digest_invalid" { return T "updateDigestInvalidMessage" }
        "update_download_failed" { return T "updateDownloadFailedMessage" }
        "update_sha256_mismatch" { return T "updateSha256MismatchMessage" }
        "update_not_available" { return T "updateNotAvailableMessage" }
        "network_failed" { return T "updateNetworkFailedMessage" }
        "file_error" { return T "updateFileErrorMessage" }
        "update_version_invalid" { return T "updateVersionInvalidMessage" }
        "url_unavailable" { return T "updateReleaseResponseInvalidMessage" }
        default {
            if (-not [string]::IsNullOrWhiteSpace([string]$script:LastOperationError.message)) {
                return [string]$script:LastOperationError.message
            }
        }
    }
    return T "updateNoResult"
}

function Confirm-UpdateDownload {
    param([object]$UpdateEvent)
    if ($SelfTest -and $null -ne $script:UpdateDownloadConfirmationForSelfTest) {
        return [bool]$script:UpdateDownloadConfirmationForSelfTest
    }
    $sizeBytes = 0
    if ($null -ne $UpdateEvent.asset -and $null -ne $UpdateEvent.asset.size) {
        try { $sizeBytes = [Int64]$UpdateEvent.asset.size } catch { $sizeBytes = 0 }
    }
    $sizeText = if ($sizeBytes -gt 0) { Format-Bytes $sizeBytes } else { "-" }
    $message = (T "updateAvailablePrompt") -f ([string]$UpdateEvent.latest_version), ([string]$UpdateEvent.current_version), $sizeText
    return ([System.Windows.Forms.MessageBox]::Show($message, (T "checkUpdatesMenu"), "YesNo", "Information") -eq [System.Windows.Forms.DialogResult]::Yes)
}

function Complete-UpdateIfReady {
    if ($script:UpdateFinalized) { return }
    if (-not $script:UpdateExitObserved -or -not $script:UpdateStdoutClosed -or -not $script:UpdateStderrClosed) { return }

    $script:UpdateFinalized = $true
    $process = $script:UpdateProcess
    $script:UpdateProcess = $null
    $progressBar.Style = "Continuous"
    if ($null -ne $script:LastUpdateDoneEvent -and $script:LastUpdateDoneEvent.kind -eq "update_installer") {
        $progressBar.Value = 100
    }
    else {
        $progressBar.Value = 0
    }
    Set-Busy $false
    Update-CancelButton
    Dispose-ProcessQuietly $process

    if ($script:UpdateCanceled) {
        $statusLabel.Text = T "canceled"
        return
    }
    if ($script:LastUpdateExitCode -ne 0 -or $null -eq $script:LastUpdateDoneEvent) {
        $statusLabel.Text = T "error"
        if ($null -eq $script:LastOperationError -or $script:LastOperationError.phase -ne "update") {
            Set-OperationErrorFromProcessOutput "update" $script:LastUpdateCommand $script:LastUpdateExitCode $script:UpdateStdoutText $script:UpdateStderrText "update_failed" (T "updateNoResult")
        }
        Show-AppError ((T "updateFailedMessage") -f (Get-UpdateFailureReason))
        return
    }

    $event = $script:LastUpdateDoneEvent
    if ($event.kind -eq "update_check") {
        if (-not [bool]$event.update_available) {
            $statusLabel.Text = T "complete"
            $message = (T "updateLatestMessage") -f ([string]$event.current_version)
            Append-Log $message
            if (-not $SelfTest) {
                [System.Windows.Forms.MessageBox]::Show($message, (T "checkUpdatesMenu"), "OK", "Information") | Out-Null
            }
            return
        }
        Append-Log ((T "updateAvailableLog") -f ([string]$event.latest_version))
        if (Confirm-UpdateDownload $event) {
            try {
                Start-UpdateDownloadProcess ([string]$event.latest_version)
            }
            catch {
                $statusLabel.Text = T "error"
                Set-Busy $false
                Show-AppError $_.Exception.Message
            }
            return
        }
        $statusLabel.Text = T "canceled"
        Append-Log (T "updateDeclinedLog")
        return
    }

    if ($event.kind -eq "update_installer") {
        $statusLabel.Text = T "complete"
        Append-Log ((T "updateDownloadedLog") -f ([string]$event.installer_path))
        Start-VerifiedUpdateInstallerAndExit ([string]$event.installer_path)
        return
    }

    $statusLabel.Text = T "error"
    Show-AppError ((T "updateFailedMessage") -f (T "updateNoResult"))
}

function Start-UpdateCheckProcess {
    Append-Log (T "updateCheckStartedLog")
    Start-UpdateProcess (Get-UpdateCheckPythonArguments) "checkingUpdates"
}

function Start-UpdateDownloadProcess {
    param([string]$Version)
    Append-Log ((T "updateDownloadStartedLog") -f $Version)
    Start-UpdateProcess (Get-UpdateDownloadPythonArguments $Version) "downloadingUpdate"
}

function Start-UpdateProcess {
    param(
        [string[]]$Arguments,
        [string]$StatusKey
    )
    if (Test-ProcessRunning $script:UpdateProcess) {
        throw (T "updateAlreadyRunning")
    }
    Clear-UpdateRunState
    $script:LastOperationError = $null
    $script:LastUpdateArguments = @($Arguments)
    if ($Arguments.Count -ge 3) {
        $script:LastUpdateCommand = [string]$Arguments[2]
    }
    Set-Busy $true
    $progressBar.Style = "Marquee"
    $progressBar.MarqueeAnimationSpeed = 30
    $progressBar.Value = 0
    $statusLabel.Text = T $StatusKey
    Start-GeoGetterPythonProcess `
        -OperationName "update" `
        -CreateStartInfo { New-UpdateProcessStartInfo $Arguments } `
        -OutputHandler ([System.Action[string]]{
            param($line)
            Append-UpdateProcessOutput "stdout" $line
            try {
                Handle-UpdateLine $line
            }
            catch {
                try { Append-Log ((T "progressDisplayError") -f $_.Exception.Message) } catch { }
            }
        }) `
        -ErrorHandler ([System.Action[string]]{
            param($line)
            Append-UpdateProcessOutput "stderr" $line
            try {
                Handle-UpdateErrorLine $line
            }
            catch { }
        }) `
        -ExitHandler ([System.Action[int]]{
            param($code)
            try {
                $script:LastUpdateExitCode = $code
                $script:UpdateExitObserved = $true
                Complete-UpdateIfReady
            }
            catch {
                try { Append-Log ((T "exitHandlerError") -f $_.Exception.Message) } catch { }
            }
        }) `
        -OutputClosedHandler ([System.Action]{
            try {
                $script:UpdateStdoutClosed = $true
                Complete-UpdateIfReady
            }
            catch {
                try { Append-Log ((T "exitHandlerError") -f $_.Exception.Message) } catch { }
            }
        }) `
        -ErrorClosedHandler ([System.Action]{
            try {
                $script:UpdateStderrClosed = $true
                Complete-UpdateIfReady
            }
            catch {
                try { Append-Log ((T "exitHandlerError") -f $_.Exception.Message) } catch { }
            }
        }) `
        -SetProcess { param($value) $script:UpdateProcess = $value } `
        -SetBridge { param($value) $script:UpdateBridge = $value } `
        -OnStartFailure {
            param($message)
            $script:LastUpdateStartError = $message
            $script:LastOperationError = New-OperationError "update_process_start" $script:LastUpdateCommand "process_start_failed" $script:LastUpdateStartError $script:LastUpdateStartError "process_start" $null
            $progressBar.Style = "Continuous"
            $progressBar.Value = 0
            Set-Busy $false
        }
}

function Start-VerifiedUpdateInstallerAndExit {
    param([string]$InstallerPath)
    try {
        Start-UpdateInstallerProcess $InstallerPath
        Append-Log (T "updateInstallerStartedLog")
        Exit-ApplicationAfterUpdate
    }
    catch {
        $statusLabel.Text = T "error"
        $detail = $_.Exception.Message
        $message = (T "updateInstallerLaunchFailed") -f $detail
        $script:LastOperationError = New-OperationError "update_installer_launch" "Start-Process" "installer_launch_failed" $detail $message "gui_update_installer" $null
        Append-Log $message
        Show-AppError $message
    }
}

function Start-UpdateInstallerProcess {
    param([string]$InstallerPath)
    if ([string]::IsNullOrWhiteSpace($InstallerPath)) {
        throw (T "updateNoResult")
    }
    if ($SelfTest -and $null -ne $script:InstallerLauncherForSelfTest) {
        & $script:InstallerLauncherForSelfTest $InstallerPath | Out-Null
        return
    }
    Start-Process -FilePath $InstallerPath -ErrorAction Stop | Out-Null
}

function Exit-ApplicationAfterUpdate {
    if ($SelfTest) {
        $script:ApplicationExitRequestedForSelfTest = $true
        return
    }
    if ($form) {
        $form.Close()
    }
}

function Start-ResolveProcess {
    param([string]$InputText)
    if (Test-ProcessRunning $script:ResolveProcess) {
        throw (T "resolveAlreadyRunning")
    }
    $script:LastInputText = $InputText
    Clear-ResolveRunState
    $script:LastOperationError = $null
    $script:ResolveInputPath = New-ResolveInputFile $InputText
    $script:LastResolveArguments = Get-ResolvePythonArguments $script:ResolveInputPath
    Start-GeoGetterPythonProcess `
        -OperationName "resolve" `
        -CreateStartInfo { New-ResolveProcessStartInfo $script:ResolveInputPath } `
        -OutputHandler ([System.Action[string]]{
            param($line)
            Append-ResolveProcessOutput "stdout" $line
        }) `
        -ErrorHandler ([System.Action[string]]{
            param($line)
            Append-ResolveProcessOutput "stderr" $line
        }) `
        -ExitHandler ([System.Action[int]]{
            param($code)
            try {
                $script:LastResolveExitCode = $code
                $script:ResolveExitObserved = $true
                Complete-ResolveIfReady
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
        }) `
        -OutputClosedHandler ([System.Action]{
            try {
                $script:ResolveStdoutClosed = $true
                Complete-ResolveIfReady
            }
            catch {
                try { Append-Log ((T "exitHandlerError") -f $_.Exception.Message) } catch { }
            }
        }) `
        -ErrorClosedHandler ([System.Action]{
            try {
                $script:ResolveStderrClosed = $true
                Complete-ResolveIfReady
            }
            catch {
                try { Append-Log ((T "exitHandlerError") -f $_.Exception.Message) } catch { }
            }
        }) `
        -SetProcess { param($value) $script:ResolveProcess = $value } `
        -SetBridge { param($value) $script:ResolveBridge = $value } `
        -OnStartFailure {
            param($message)
            Remove-ResolveInputFile
            $script:LastResolveStartError = $message
            $script:LastOperationError = New-OperationError "resolve_process_start" "resolve-json" "process_start_failed" $script:LastResolveStartError $script:LastResolveStartError "process_start" $null
        }
}

function Start-ManifestVerificationProcess {
    param([string]$ManifestPath)
    if (Test-ProcessRunning $script:VerifyProcess) {
        throw (T "verifyManifestAlreadyRunning")
    }
    $script:VerifyCanceled = $false
    Clear-VerificationRunState
    $script:LastOperationError = $null
    $script:LastVerificationArguments = Get-VerifyManifestPythonArguments $ManifestPath
    Append-Log ((T "verifyManifestStartedLog") -f $ManifestPath)

    Start-GeoGetterPythonProcess `
        -OperationName "verification" `
        -CreateStartInfo { New-VerifyManifestProcessStartInfo $ManifestPath } `
        -OutputHandler ([System.Action[string]]{
            param($line)
            Append-VerificationProcessOutput "stdout" $line
            try {
                Handle-ManifestVerificationLine $line
            }
            catch {
                try { Append-Log ((T "progressDisplayError") -f $_.Exception.Message) } catch { }
            }
        }) `
        -ErrorHandler ([System.Action[string]]{
            param($line)
            Append-VerificationProcessOutput "stderr" $line
            try {
                Append-Log $line
            }
            catch { }
        }) `
        -ExitHandler ([System.Action[int]]{
            param($code)
            try {
                $script:LastVerificationExitCode = $code
                $script:VerifyExitObserved = $true
                Complete-ManifestVerificationIfReady
            }
            catch {
                try { Append-Log ((T "exitHandlerError") -f $_.Exception.Message) } catch { }
            }
        }) `
        -OutputClosedHandler ([System.Action]{
            try {
                $script:VerifyStdoutClosed = $true
                Complete-ManifestVerificationIfReady
            }
            catch {
                try { Append-Log ((T "exitHandlerError") -f $_.Exception.Message) } catch { }
            }
        }) `
        -ErrorClosedHandler ([System.Action]{
            try {
                $script:VerifyStderrClosed = $true
                Complete-ManifestVerificationIfReady
            }
            catch {
                try { Append-Log ((T "exitHandlerError") -f $_.Exception.Message) } catch { }
            }
        }) `
        -SetProcess { param($value) $script:VerifyProcess = $value } `
        -SetBridge { param($value) $script:VerifyBridge = $value } `
        -OnStartFailure {
            param($message)
            $script:LastVerificationStartError = $message
            $script:LastOperationError = New-OperationError "verification_process_start" "verify-manifest-json" "process_start_failed" $script:LastVerificationStartError $script:LastVerificationStartError "process_start" $null
        }
}

function Start-DownloadProcess {
    $script:DownloadCanceled = $false
    Clear-DownloadRunState
    Clear-VerificationRunState
    $script:LastOperationError = $null
    try {
        Assert-ResolvedMatchesCurrentInput
        Assert-AnySelection
    }
    catch {
        Set-DownloadPreflightError $_.Exception.Message $true
        throw
    }
    $preflight = Test-DownloadPreflight
    $resumeExisting = $false
    if ($preflight.ExistingOutputNonEmpty) {
        if (-not (Confirm-ResumeExistingOutput $preflight.OutputDir)) {
            $script:LastResumeExistingRequested = $false
            $statusLabel.Text = T "canceled"
            Append-Log (T "resumeDeclinedLog")
            return
        }
        $resumeExisting = $true
        $preflight = Test-DownloadPreflight -ResumeExisting $true
        $script:LastResumeExistingRequested = $true
    }

    $fastqIndices = Get-SelectedFastqIndicesOrEmpty
    $suppIndices = Get-SelectedSuppIndicesOrEmpty
    $script:LastDownloadArguments = Get-DownloadPythonArguments $fastqIndices $suppIndices $resumeExisting
    Set-Busy $true
    $progressBar.Style = "Continuous"
    $progressBar.Value = 0
    $statusLabel.Text = T "downloading"
    Start-GeoGetterPythonProcess `
        -OperationName "download" `
        -CreateStartInfo { New-DownloadProcessStartInfo $fastqIndices $suppIndices $resumeExisting } `
        -OutputHandler ([System.Action[string]]{
            param($line)
            Append-DownloadProcessOutput "stdout" $line
            try {
                Handle-DownloadLine $line
            }
            catch {
                try { Append-Log ((T "progressDisplayError") -f $_.Exception.Message) } catch { }
            }
        }) `
        -ErrorHandler ([System.Action[string]]{
            param($line)
            Append-DownloadProcessOutput "stderr" $line
            try {
                Handle-DownloadErrorLine $line
            }
            catch { }
        }) `
        -ExitHandler ([System.Action[int]]{
            param($code)
            try {
                $script:LastDownloadExitCode = $code
                $script:DownloadExitObserved = $true
                Complete-DownloadIfReady
            }
            catch {
                try { Append-Log ((T "exitHandlerError") -f $_.Exception.Message) } catch { }
            }
        }) `
        -OutputClosedHandler ([System.Action]{
            try {
                $script:DownloadStdoutClosed = $true
                Complete-DownloadIfReady
            }
            catch {
                try { Append-Log ((T "exitHandlerError") -f $_.Exception.Message) } catch { }
            }
        }) `
        -ErrorClosedHandler ([System.Action]{
            try {
                $script:DownloadStderrClosed = $true
                Complete-DownloadIfReady
            }
            catch {
                try { Append-Log ((T "exitHandlerError") -f $_.Exception.Message) } catch { }
            }
        }) `
        -SetProcess { param($value) $script:DownloadProcess = $value } `
        -SetBridge { param($value) $script:DownloadBridge = $value } `
        -OnStartFailure {
            param($message)
            $script:LastDownloadStartError = $message
            $script:LastOperationError = New-OperationError "download_process_start" "selected-download-json" "process_start_failed" $script:LastDownloadStartError $script:LastDownloadStartError "process_start" $null
        }
}

function New-ResolveProcessStartInfo {
    param([string]$InputPath)
    return New-PythonProcessStartInfo -Arguments (Get-ResolvePythonArguments $InputPath)
}

function New-DownloadProcessStartInfo {
    param(
        [string]$FastqIndices,
        [string]$SuppIndices,
        [bool]$ResumeExisting = $false
    )
    return New-PythonProcessStartInfo -Arguments (Get-DownloadPythonArguments $FastqIndices $SuppIndices $ResumeExisting)
}

function New-VerifyManifestProcessStartInfo {
    param([string]$ManifestPath)
    return New-PythonProcessStartInfo -Arguments (Get-VerifyManifestPythonArguments $ManifestPath)
}

function New-UpdateProcessStartInfo {
    param([string[]]$Arguments)
    return New-PythonProcessStartInfo -Arguments $Arguments
}

function Get-ResolvePythonArguments {
    param([string]$InputPath)
    return @("-m", "geo_getter.cli", "resolve-json", "--input-file", $InputPath, "--out-json", $script:ResolvedJsonPath)
}

function Get-DownloadPythonArguments {
    param(
        [string]$FastqIndices,
        [string]$SuppIndices,
        [bool]$ResumeExisting = $false
    )
    $args = @("-m", "geo_getter.cli", "selected-download-json", "--input-json", $script:ResolvedJsonPath, "--fastq-indices", $FastqIndices, "--supp-indices", $SuppIndices, "--out", $outputBox.Text, "--download-workers", ([string](Get-DownloadWorkerCount)))
    if ($ResumeExisting) {
        $args += "--resume-existing"
    }
    return $args
}

function Get-DownloadWorkerCount {
    if ($downloadWorkersUpDown) {
        return [int]$downloadWorkersUpDown.Value
    }
    return 2
}

function Get-PreflightPythonArguments {
    param(
        [string]$FastqIndices,
        [string]$SuppIndices,
        [string]$OutputDir,
        [bool]$ResumeExisting = $false
    )
    $args = @("-m", "geo_getter.cli", "preflight-json", "--input-json", $script:ResolvedJsonPath, "--fastq-indices", $FastqIndices, "--supp-indices", $SuppIndices, "--out", $OutputDir)
    if ($ResumeExisting) {
        $args += "--resume-existing"
    }
    return $args
}

function Get-VerifyManifestPythonArguments {
    param([string]$ManifestPath)
    return @("-m", "geo_getter.cli", "verify-manifest-json", "--manifest", $ManifestPath)
}

function Get-UpdateCheckPythonArguments {
    return @("-m", "geo_getter.cli", "check-update-json")
}

function Get-UpdateDownloadPythonArguments {
    param([string]$Version)
    return @("-m", "geo_getter.cli", "download-update-json", "--version", $Version)
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
        [string]$SuppIndices,
        [bool]$ResumeExisting = $false
    )
    $psi = New-DownloadProcessStartInfo $FastqIndices $SuppIndices $ResumeExisting
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
    if ($null -eq $Value) { $Value = "" }
    if ($Value -eq "") {
        return '""'
    }
    if ($Value -notmatch '[\s"]') {
        return $Value
    }
    $builder = New-Object System.Text.StringBuilder
    [void]$builder.Append('"')
    $backslashes = 0
    $backslashChar = [char]92
    $quoteChar = [char]34
    foreach ($char in $Value.ToCharArray()) {
        if ($char -eq $backslashChar) {
            $backslashes += 1
            continue
        }
        if ($char -eq $quoteChar) {
            if ($backslashes -gt 0) {
                [void]$builder.Append(('\' * (($backslashes * 2) + 1)))
            }
            else {
                [void]$builder.Append('\')
            }
            [void]$builder.Append('"')
            $backslashes = 0
            continue
        }
        if ($backslashes -gt 0) {
            [void]$builder.Append(('\' * $backslashes))
            $backslashes = 0
        }
        [void]$builder.Append($char)
    }
    if ($backslashes -gt 0) {
        [void]$builder.Append(('\' * ($backslashes * 2)))
    }
    [void]$builder.Append('"')
    return $builder.ToString()
}

function Assert-Equal {
    param(
        [object]$Actual,
        [object]$Expected,
        [string]$Name
    )
    if ($Actual -ne $Expected) {
        $expectedText = try { [string]$Expected } catch { ($Expected | Out-String).Trim() }
        $actualText = try { [string]$Actual } catch { ($Actual | Out-String).Trim() }
        throw "$Name failed. expected=[$expectedText] actual=[$actualText]"
    }
}

function Assert-SequenceEqual {
    param(
        [object[]]$Actual,
        [object[]]$Expected,
        [string]$Name
    )
    Assert-Equal $Actual.Count $Expected.Count "$Name count"
    for ($index = 0; $index -lt $Expected.Count; $index++) {
        Assert-Equal ([string]$Actual[$index]) ([string]$Expected[$index]) "$Name item $index"
    }
}

function Assert-PythonArgumentRoundTrip {
    param(
        [string[]]$Values,
        [string]$Name
    )
    $result = Invoke-PythonCli -Arguments (@("-c", "import json, sys; print(json.dumps(sys.argv[1:], ensure_ascii=False))") + $Values)
    Assert-Equal $result.ExitCode 0 "$Name exit code"
    $parsed = ConvertFrom-Json -InputObject $result.Stdout.Trim()
    $actual = foreach ($item in $parsed) { [string]$item }
    $actual = @($actual)
    Assert-SequenceEqual $actual $Values $Name
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

function Set-ResultSplitDistance {
    if ($null -eq $mainSplit -or $mainSplit.Height -le 0) { return }
    $splitterWidth = [Math]::Max(1, [int]$mainSplit.SplitterWidth)
    $minFastqPanelHeight = 180
    $minSupplementaryPanelHeight = 170
    $targetFastqPanelHeight = 220
    $maxDistance = [int]$mainSplit.Height - $splitterWidth - [int]$mainSplit.Panel2MinSize
    if ($maxDistance -lt [int]$mainSplit.Panel1MinSize) { return }
    $distance = [Math]::Min($targetFastqPanelHeight, ([int]$mainSplit.Height - $splitterWidth - $minSupplementaryPanelHeight))
    $distance = [Math]::Max($minFastqPanelHeight, $distance)
    $distance = [Math]::Min($distance, $maxDistance)
    if ($distance -ge [int]$mainSplit.Panel1MinSize -and $mainSplit.SplitterDistance -ne $distance) {
        $mainSplit.SplitterDistance = $distance
    }
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
    [void]$rootLayout.RowStyles.Add((New-Object System.Windows.Forms.RowStyle([System.Windows.Forms.SizeType]::Absolute, 130)))
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
    $script:checkUpdatesMenuItem = New-Object System.Windows.Forms.ToolStripMenuItem
    [void]$languageMenuItem.DropDownItems.Add($japaneseMenuItem)
    [void]$languageMenuItem.DropDownItems.Add($englishMenuItem)
    [void]$settingsMenuItem.DropDownItems.Add($languageMenuItem)
    [void]$toolsMenuItem.DropDownItems.Add($verifyManifestMenuItem)
    [void]$helpMenuItem.DropDownItems.Add($helpOpenMenuItem)
    [void]$helpMenuItem.DropDownItems.Add($checkUpdatesMenuItem)
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
    $split.SplitterDistance = 180
    $script:mainSplit = $split
    $rootLayout.Controls.Add($split, 0, 2)

    $fastqPanel = New-Object System.Windows.Forms.TableLayoutPanel
    $fastqPanel.Dock = "Fill"
    $fastqPanel.Padding = New-Object System.Windows.Forms.Padding(0)
    $fastqPanel.ColumnCount = 1
    $fastqPanel.RowCount = 2
    [void]$fastqPanel.ColumnStyles.Add((New-Object System.Windows.Forms.ColumnStyle([System.Windows.Forms.SizeType]::Percent, 100)))
    [void]$fastqPanel.RowStyles.Add((New-Object System.Windows.Forms.RowStyle([System.Windows.Forms.SizeType]::Absolute, 88)))
    [void]$fastqPanel.RowStyles.Add((New-Object System.Windows.Forms.RowStyle([System.Windows.Forms.SizeType]::Percent, 100)))
    $split.Panel1.Controls.Add($fastqPanel)

    $fastqHeaderPanel = New-Object System.Windows.Forms.TableLayoutPanel
    $fastqHeaderPanel.Dock = "Fill"
    $fastqHeaderPanel.ColumnCount = 1
    $fastqHeaderPanel.RowCount = 2
    [void]$fastqHeaderPanel.ColumnStyles.Add((New-Object System.Windows.Forms.ColumnStyle([System.Windows.Forms.SizeType]::Percent, 100)))
    [void]$fastqHeaderPanel.RowStyles.Add((New-Object System.Windows.Forms.RowStyle([System.Windows.Forms.SizeType]::Absolute, 42)))
    [void]$fastqHeaderPanel.RowStyles.Add((New-Object System.Windows.Forms.RowStyle([System.Windows.Forms.SizeType]::Absolute, 46)))
    $fastqPanel.Controls.Add($fastqHeaderPanel, 0, 0)

    $fastqTitlePanel = New-Object System.Windows.Forms.TableLayoutPanel
    $fastqTitlePanel.Dock = "Fill"
    $fastqTitlePanel.ColumnCount = 3
    $fastqTitlePanel.RowCount = 1
    [void]$fastqTitlePanel.ColumnStyles.Add((New-Object System.Windows.Forms.ColumnStyle([System.Windows.Forms.SizeType]::Percent, 100)))
    [void]$fastqTitlePanel.ColumnStyles.Add((New-Object System.Windows.Forms.ColumnStyle([System.Windows.Forms.SizeType]::Absolute, 128)))
    [void]$fastqTitlePanel.ColumnStyles.Add((New-Object System.Windows.Forms.ColumnStyle([System.Windows.Forms.SizeType]::Absolute, 132)))
    [void]$fastqTitlePanel.RowStyles.Add((New-Object System.Windows.Forms.RowStyle([System.Windows.Forms.SizeType]::Percent, 100)))
    $fastqHeaderPanel.Controls.Add($fastqTitlePanel, 0, 0)

    $script:fastqTitle = New-Object System.Windows.Forms.Label
    $fastqTitle.Text = "raw FASTQ (ENA direct FASTQ): 0 files"
    $fastqTitle.Dock = "Fill"
    $fastqTitle.TextAlign = "MiddleLeft"
    $fastqTitle.AutoEllipsis = $true
    $fastqTitle.Margin = New-Object System.Windows.Forms.Padding(0, 0, 8, 0)
    $fastqTitlePanel.Controls.Add($fastqTitle, 0, 0)

    $script:fastqSelectAllButton = New-Object System.Windows.Forms.Button
    $fastqSelectAllButton.Text = "Select all"
    $fastqSelectAllButton.Dock = "Fill"
    $fastqSelectAllButton.Margin = New-Object System.Windows.Forms.Padding(4, 5, 4, 5)
    $fastqTitlePanel.Controls.Add($fastqSelectAllButton, 1, 0)

    $script:fastqClearSelectionButton = New-Object System.Windows.Forms.Button
    $fastqClearSelectionButton.Text = "Clear selection"
    $fastqClearSelectionButton.Dock = "Fill"
    $fastqClearSelectionButton.Margin = New-Object System.Windows.Forms.Padding(4, 5, 0, 5)
    $fastqTitlePanel.Controls.Add($fastqClearSelectionButton, 2, 0)

    $fastqFilterPanel = New-Object System.Windows.Forms.FlowLayoutPanel
    $fastqFilterPanel.Dock = "Fill"
    $fastqFilterPanel.FlowDirection = "LeftToRight"
    $fastqFilterPanel.WrapContents = $true
    $fastqFilterPanel.Padding = New-Object System.Windows.Forms.Padding(0, 4, 0, 0)
    $fastqFilterPanel.AutoScroll = $false
    $fastqHeaderPanel.Controls.Add($fastqFilterPanel, 0, 1)

    $script:fastqFilterLabel = New-Object System.Windows.Forms.Label
    $fastqFilterLabel.Text = "FASTQ filter"
    $fastqFilterLabel.Size = New-Object System.Drawing.Size(85, 26)
    $fastqFilterLabel.TextAlign = "MiddleLeft"
    $fastqFilterPanel.Controls.Add($fastqFilterLabel)

    $script:fastqFilterKeywordLabel = New-Object System.Windows.Forms.Label
    $fastqFilterKeywordLabel.Text = "Search"
    $fastqFilterKeywordLabel.Size = New-Object System.Drawing.Size(50, 26)
    $fastqFilterKeywordLabel.TextAlign = "MiddleLeft"
    $fastqFilterPanel.Controls.Add($fastqFilterKeywordLabel)

    $script:fastqFilterBox = New-Object System.Windows.Forms.TextBox
    $fastqFilterBox.Width = 170
    $fastqFilterPanel.Controls.Add($fastqFilterBox)

    $script:fastqFilterLayoutLabel = New-Object System.Windows.Forms.Label
    $fastqFilterLayoutLabel.Text = "Layout"
    $fastqFilterLayoutLabel.Size = New-Object System.Drawing.Size(50, 26)
    $fastqFilterLayoutLabel.TextAlign = "MiddleLeft"
    $fastqFilterPanel.Controls.Add($fastqFilterLayoutLabel)

    $script:fastqLayoutFilterCombo = New-Object System.Windows.Forms.ComboBox
    $fastqLayoutFilterCombo.DropDownStyle = "DropDownList"
    $fastqLayoutFilterCombo.Width = 95
    $fastqFilterPanel.Controls.Add($fastqLayoutFilterCombo)

    $script:fastqFilterStrategyLabel = New-Object System.Windows.Forms.Label
    $fastqFilterStrategyLabel.Text = "Strategy"
    $fastqFilterStrategyLabel.Size = New-Object System.Drawing.Size(60, 26)
    $fastqFilterStrategyLabel.TextAlign = "MiddleLeft"
    $fastqFilterPanel.Controls.Add($fastqFilterStrategyLabel)

    $script:fastqStrategyFilterCombo = New-Object System.Windows.Forms.ComboBox
    $fastqStrategyFilterCombo.DropDownStyle = "DropDownList"
    $fastqStrategyFilterCombo.Width = 120
    $fastqFilterPanel.Controls.Add($fastqStrategyFilterCombo)

    $script:fastqClearFilterButton = New-Object System.Windows.Forms.Button
    $fastqClearFilterButton.Text = "Clear filter"
    $fastqClearFilterButton.Size = New-Object System.Drawing.Size(110, 28)
    $fastqFilterPanel.Controls.Add($fastqClearFilterButton)

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
        @("strategy", "Strategy", 100),
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
    Add-GridCellCopyHandlers $fastqGrid

    $suppPanel = New-Object System.Windows.Forms.TableLayoutPanel
    $suppPanel.Dock = "Fill"
    $suppPanel.Padding = New-Object System.Windows.Forms.Padding(0)
    $suppPanel.ColumnCount = 1
    $suppPanel.RowCount = 2
    [void]$suppPanel.ColumnStyles.Add((New-Object System.Windows.Forms.ColumnStyle([System.Windows.Forms.SizeType]::Percent, 100)))
    [void]$suppPanel.RowStyles.Add((New-Object System.Windows.Forms.RowStyle([System.Windows.Forms.SizeType]::Absolute, 42)))
    [void]$suppPanel.RowStyles.Add((New-Object System.Windows.Forms.RowStyle([System.Windows.Forms.SizeType]::Percent, 100)))
    $split.Panel2.Controls.Add($suppPanel)

    $suppHeaderPanel = New-Object System.Windows.Forms.TableLayoutPanel
    $suppHeaderPanel.Dock = "Fill"
    $suppHeaderPanel.ColumnCount = 3
    $suppHeaderPanel.RowCount = 1
    [void]$suppHeaderPanel.ColumnStyles.Add((New-Object System.Windows.Forms.ColumnStyle([System.Windows.Forms.SizeType]::Percent, 100)))
    [void]$suppHeaderPanel.ColumnStyles.Add((New-Object System.Windows.Forms.ColumnStyle([System.Windows.Forms.SizeType]::Absolute, 128)))
    [void]$suppHeaderPanel.ColumnStyles.Add((New-Object System.Windows.Forms.ColumnStyle([System.Windows.Forms.SizeType]::Absolute, 132)))
    [void]$suppHeaderPanel.RowStyles.Add((New-Object System.Windows.Forms.RowStyle([System.Windows.Forms.SizeType]::Percent, 100)))
    $suppPanel.Controls.Add($suppHeaderPanel, 0, 0)

    $script:suppTitle = New-Object System.Windows.Forms.Label
    $suppTitle.Text = "GEO supplementary / processed files (not raw FASTQ): 0 files"
    $suppTitle.Dock = "Fill"
    $suppTitle.TextAlign = "MiddleLeft"
    $suppTitle.AutoEllipsis = $true
    $suppTitle.Margin = New-Object System.Windows.Forms.Padding(0, 0, 8, 0)
    $suppHeaderPanel.Controls.Add($suppTitle, 0, 0)

    $script:suppSelectAllButton = New-Object System.Windows.Forms.Button
    $suppSelectAllButton.Text = "Select all"
    $suppSelectAllButton.Dock = "Fill"
    $suppSelectAllButton.Margin = New-Object System.Windows.Forms.Padding(4, 6, 4, 6)
    $suppHeaderPanel.Controls.Add($suppSelectAllButton, 1, 0)

    $script:suppClearSelectionButton = New-Object System.Windows.Forms.Button
    $suppClearSelectionButton.Text = "Clear selection"
    $suppClearSelectionButton.Dock = "Fill"
    $suppClearSelectionButton.Margin = New-Object System.Windows.Forms.Padding(4, 6, 0, 6)
    $suppHeaderPanel.Controls.Add($suppClearSelectionButton, 2, 0)

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
        @("supp_origin", "Origin", 150),
        @("supp_name", "File name", 360),
        @("supp_url", "GEO URL", 520)
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
    Add-GridCellCopyHandlers $suppGrid

    $bottom = New-Object System.Windows.Forms.Panel
    $bottom.Dock = "Fill"
    $bottom.Height = 130
    $script:bottomPanel = $bottom
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

    $script:downloadWorkersLabel = New-Object System.Windows.Forms.Label
    $downloadWorkersLabel.Text = "FASTQ workers"
    $downloadWorkersLabel.Location = New-Object System.Drawing.Point(315, 14)
    $downloadWorkersLabel.Size = New-Object System.Drawing.Size(95, 22)
    $downloadWorkersLabel.TextAlign = "MiddleLeft"
    $bottom.Controls.Add($downloadWorkersLabel)

    $script:downloadWorkersUpDown = New-Object System.Windows.Forms.NumericUpDown
    $downloadWorkersUpDown.Location = New-Object System.Drawing.Point(410, 10)
    $downloadWorkersUpDown.Size = New-Object System.Drawing.Size(45, 24)
    $downloadWorkersUpDown.Minimum = 1
    $downloadWorkersUpDown.Maximum = 4
    $downloadWorkersUpDown.Value = 2
    $bottom.Controls.Add($downloadWorkersUpDown)

    $script:statusLabel = New-Object System.Windows.Forms.Label
    $statusLabel.Text = "Idle"
    $statusLabel.Location = New-Object System.Drawing.Point(465, 14)
    $statusLabel.Size = New-Object System.Drawing.Size(320, 22)
    $statusLabel.Anchor = $anchorTopLeftRight
    $bottom.Controls.Add($statusLabel)

    $script:progressBar = New-Object System.Windows.Forms.ProgressBar
    $progressBar.Location = New-Object System.Drawing.Point(800, 12)
    $progressBar.Size = New-Object System.Drawing.Size(330, 22)
    $progressBar.Anchor = $anchorTopRight
    $bottom.Controls.Add($progressBar)

    $script:selectionSummaryLabel = New-Object System.Windows.Forms.Label
    $selectionSummaryLabel.Location = New-Object System.Drawing.Point(10, 43)
    $selectionSummaryLabel.Size = New-Object System.Drawing.Size(1120, 34)
    $selectionSummaryLabel.Anchor = $anchorTopLeftRight
    $selectionSummaryLabel.AutoEllipsis = $true
    $bottom.Controls.Add($selectionSummaryLabel)

    $script:logBox = New-Object System.Windows.Forms.TextBox
    $logBox.Location = New-Object System.Drawing.Point(10, 80)
    $logBox.Size = New-Object System.Drawing.Size(1120, 40)
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
    $fastqFilterBox.Add_TextChanged({ Apply-FastqFilter })
    $fastqLayoutFilterCombo.Add_SelectedIndexChanged({ Apply-FastqFilter })
    $fastqStrategyFilterCombo.Add_SelectedIndexChanged({ Apply-FastqFilter })
    $fastqClearFilterButton.Add_Click({ Reset-FastqFilterControls })

    $japaneseMenuItem.Add_Click({ Set-Language "ja" })
    $englishMenuItem.Add_Click({ Set-Language "en" })
    $verifyManifestMenuItem.Add_Click({ Show-ManifestVerificationOpenDialog })
    $helpOpenMenuItem.Add_Click({ Show-HelpWindow })
    $checkUpdatesMenuItem.Add_Click({
        try {
            Start-UpdateCheckProcess
        }
        catch {
            Set-Busy $false
            Show-AppError $_.Exception.Message
        }
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

    $cancelButton.Add_Click({
        Stop-RunningGuiProcesses | Out-Null
    })

    $formLocal.add_FormClosing({
        Stop-RunningGuiProcessesForShutdown
    })
    $formLocal.add_Shown({ Set-ResultSplitDistance })
    $formLocal.add_SizeChanged({ Set-ResultSplitDistance })

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
    Assert-Equal (Test-Path -LiteralPath (Get-GuiTextResourcePath) -PathType Leaf) $true "GUI text resource file exists"
    $resourceReload = Import-GuiTextResource
    Assert-Equal ($resourceReload["ja"].Keys.Count -gt 0) $true "Japanese GUI text resource has entries"
    Assert-Equal $resourceReload["ja"].Keys.Count $resourceReload["en"].Keys.Count "GUI text resource language key count"
    foreach ($topic in Get-HelpTopics) {
        foreach ($language in @("ja", "en")) {
            Assert-Equal $script:Translations[$language].ContainsKey($topic.TitleKey) $true "$language help title key $($topic.TitleKey)"
            Assert-Equal $script:Translations[$language].ContainsKey($topic.TextKey) $true "$language help body key $($topic.TextKey)"
            Assert-Equal ([string]::IsNullOrWhiteSpace([string]$script:Translations[$language][$topic.TextKey])) $false "$language help body text $($topic.TextKey)"
        }
    }
    $missingResourceError = ""
    try {
        Import-GuiTextResource -Path (Join-Path ([System.IO.Path]::GetTempPath()) ("geo_getter_missing_resource_" + [System.Guid]::NewGuid().ToString("N") + ".json")) | Out-Null
    }
    catch {
        $missingResourceError = $_.Exception.Message
    }
    Assert-Contains $missingResourceError "GUI text resource not found" "missing GUI text resource error"
    Set-Language "en"
    Assert-Equal $settingsMenuItem.Text "Settings" "English settings menu"
    Assert-Equal $toolsMenuItem.Text "Tools" "English tools menu"
    Assert-Equal $verifyManifestMenuItem.Text "Verify saved FASTQ" "English verify manifest menu"
    Assert-Equal $helpOpenMenuItem.Text "Open help" "English open help menu"
    Assert-Equal $checkUpdatesMenuItem.Text "Check for updates" "English check updates menu"
    Assert-Equal $helpMenuItem.DropDownItems.Count 2 "Help menu uses help and update"
    Assert-Equal $helpMenuItem.DropDownItems[0] $helpOpenMenuItem "Help menu opens help first"
    Assert-Equal $helpMenuItem.DropDownItems[1] $checkUpdatesMenuItem "Help menu checks updates second"
    Assert-Equal $fetchButton.Text "Find files" "English find files button"
    Assert-Equal $browseButton.Text "Browse" "English browse button"
    Assert-Equal $downloadWorkersLabel.Text "FASTQ workers" "English download workers label"
    Assert-Equal ((Get-Variable -Name ("diag" + "nosticsButton") -Scope Script -ErrorAction SilentlyContinue) -eq $null) $true "save button removed from main UI"
    Assert-Equal $fastqGrid.Columns["geo_title"].HeaderText "Sample title" "English FASTQ header"
    Assert-Equal $fastqGrid.Columns["strategy"].HeaderText "Strategy" "English FASTQ strategy header"
    Assert-Equal $fastqFilterLabel.Text "FASTQ filter" "English FASTQ filter label"
    Assert-Equal $fastqFilterKeywordLabel.Text "Search" "English FASTQ search label"
    Assert-Equal $fastqClearFilterButton.Text "Clear filter" "English clear FASTQ filter button"
    Assert-Equal $fastqGrid.ContextMenuStrip.Items[0].Text "Copy" "English FASTQ copy menu"
    Assert-Equal $suppGrid.Columns["supp_name"].HeaderText "File name" "English supplementary file name header"
    Set-Language "ja"
    Assert-Equal $helpOpenMenuItem.Text "ヘルプを開く" "Japanese open help menu"
    Assert-Equal $checkUpdatesMenuItem.Text "更新を確認" "Japanese check updates menu"
    Assert-Equal $toolsMenuItem.Text "ツール" "Japanese tools menu"
    Assert-Equal $verifyManifestMenuItem.Text "保存済みFASTQを確認" "Japanese verify manifest menu"
    Assert-Equal $downloadWorkersLabel.Text "同時FASTQ" "Japanese download workers label"
    Assert-Equal ((Get-Variable -Name inputHelpMenuItem -Scope Script -ErrorAction SilentlyContinue) -eq $null) $true "individual input help menu removed"
    Assert-Equal $fetchButton.Text "ファイルを検索" "Japanese find files button"
    Assert-Equal $browseButton.Text "参照..." "Japanese browse button"
    Assert-Equal $fastqFilterLabel.Text "Filter" "Japanese FASTQ filter label"
    Assert-Equal $fastqFilterKeywordLabel.Text "検索" "Japanese FASTQ search label"
    Assert-Equal $fastqClearFilterButton.Text "フィルタ解除" "Japanese clear FASTQ filter button"
    Assert-Equal $fastqGrid.ContextMenuStrip.Items[0].Text "コピー" "Japanese FASTQ copy menu"
    Assert-Equal $suppTitle.Text "GEO supplementary / processed file（raw FASTQ以外）: 0件" "Japanese supplementary title"
    Assert-Equal $suppGrid.Columns.Count 5 "supplementary grid keeps selection, origin, file name, URL, and source index"
    Assert-Equal $suppGrid.Columns["supp_origin"].HeaderText "由来" "Japanese supplementary origin header"
    Assert-Equal $suppGrid.Columns["supp_name"].HeaderText "ファイル名" "Japanese supplementary file name header"
    Assert-Equal $suppGrid.Columns["supp_url"].HeaderText "GEO URL" "Japanese supplementary URL header"
    Set-Busy $true
    Assert-Equal $checkUpdatesMenuItem.Enabled $false "busy disables check updates menu"
    Assert-Equal $downloadWorkersUpDown.Enabled $false "busy disables download worker setting"
    Set-Busy $false
    Assert-Equal $checkUpdatesMenuItem.Enabled $true "idle enables check updates menu"
    Assert-Equal $downloadWorkersUpDown.Enabled $true "idle enables download worker setting"
    Assert-Equal $outputBox.ReadOnly $true "output folder is browse-only"
    Assert-Equal $outputBox.Text (Get-DefaultOutputFolder) "default output folder"
    Assert-Equal ([int]$downloadWorkersUpDown.Minimum) 1 "download worker minimum"
    Assert-Equal ([int]$downloadWorkersUpDown.Maximum) 4 "download worker maximum"
    Assert-Equal ([int]$downloadWorkersUpDown.Value) 2 "download worker default"
    Assert-Equal $fastqGrid.Columns["run"].ReadOnly $true "FASTQ run column readonly"
    Assert-Equal $fastqGrid.Columns["url"].ReadOnly $true "FASTQ URL column readonly"
    Assert-Equal $fastqGrid.Columns["selected"].ReadOnly $false "FASTQ select column editable"
    Assert-Equal ($bottomPanel.Height -le 130) $true "bottom panel leaves more room for result tables"
    Assert-Equal ($suppGrid.Columns["supp_name"].Width -ge 300) $true "supplementary file name column remains prominent"
    Assert-Equal ((Get-Variable -Name planButton -Scope Script -ErrorAction SilentlyContinue) -eq $null) $true "save-list button removed from main UI"
    Assert-Equal (Format-Bytes ([Int64]2377036173)) "2.21 GB" "Format-Bytes over Int32"
    Assert-Equal (Format-Bytes ([Int64]5000000000)) "4.66 GB" "Format-Bytes 5GB"
    Assert-Equal (Format-Bytes ([Int64]-1)) "0 B" "Format-Bytes negative"
    Assert-Equal (ConvertTo-GeoGetterSafeName "CON") "_CON" "preflight safe name handles reserved Windows name"
    Assert-Equal (ConvertTo-GeoGetterSafeName "..") "geo_getter_download" "preflight safe name handles dot-only name"
    Assert-Equal (ConvertTo-ProcessArgument "") '""' "empty process argument"
    Assert-PythonArgumentRoundTrip @("", 'C:\tmp\geo getter\a.txt', 'C:\tmp\日本語 path\manifest.tsv', 'C:\tmp\space path\', 'quote"name', 'C:\tmp\backslash\"quote') "process argument round trip"
    $updateCheckArgs = Get-UpdateCheckPythonArguments
    Assert-Equal ($updateCheckArgs -join "|") "-m|geo_getter.cli|check-update-json" "update check bridge arguments"
    $updateDownloadArgs = Get-UpdateDownloadPythonArguments "0.1.4"
    Assert-Equal ($updateDownloadArgs -join "|") "-m|geo_getter.cli|download-update-json|--version|0.1.4" "update download bridge arguments"
    $downloadWorkersUpDown.Value = 4
    $workerDownloadArgs = Get-DownloadPythonArguments "0" "" $false
    Assert-Equal ($workerDownloadArgs -join "|") "-m|geo_getter.cli|selected-download-json|--input-json|$script:ResolvedJsonPath|--fastq-indices|0|--supp-indices||--out|$($outputBox.Text)|--download-workers|4" "download worker bridge arguments"
    $downloadWorkersUpDown.Value = 2
    $selfTestResolvedJsonPath = $script:ResolvedJsonPath
    $script:ResolvedJsonPath = "C:\tmp\geo getter\resolved.json"
    $preflightArgs = Get-PreflightPythonArguments "0,1" "2" "C:\tmp\geo getter\out" $true
    Assert-Equal ($preflightArgs -join "|") "-m|geo_getter.cli|preflight-json|--input-json|C:\tmp\geo getter\resolved.json|--fastq-indices|0,1|--supp-indices|2|--out|C:\tmp\geo getter\out|--resume-existing" "preflight bridge arguments"
    $script:ResolvedJsonPath = $selfTestResolvedJsonPath
    Clear-UpdateRunState
    $script:LastUpdateDoneEvent = [pscustomobject]@{
        event = "done"
        kind = "update_check"
        current_version = "0.1.3"
        latest_version = "0.1.3"
        update_available = $false
        release_url = "https://example.invalid/release"
        asset = $null
    }
    $script:LastUpdateExitCode = 0
    $script:UpdateExitObserved = $true
    $script:UpdateStdoutClosed = $true
    $script:UpdateStderrClosed = $true
    Set-Busy $true
    Complete-UpdateIfReady
    Assert-Equal $statusLabel.Text (T "complete") "update check latest status"

    Clear-UpdateRunState
    $script:UpdateDownloadConfirmationForSelfTest = $false
    $script:LastUpdateDoneEvent = [pscustomobject]@{
        event = "done"
        kind = "update_check"
        current_version = "0.1.3"
        latest_version = "0.1.4"
        update_available = $true
        release_url = "https://example.invalid/release"
        asset = [pscustomobject]@{ name = "GEOGetter-Setup-v0.1.4.exe"; size = 12; sha256 = ("1" * 64) }
    }
    $script:LastUpdateExitCode = 0
    $script:UpdateExitObserved = $true
    $script:UpdateStdoutClosed = $true
    $script:UpdateStderrClosed = $true
    Set-Busy $true
    Complete-UpdateIfReady
    Assert-Equal $statusLabel.Text (T "canceled") "declined update leaves GUI open"
    $script:UpdateDownloadConfirmationForSelfTest = $null

    Clear-UpdateRunState
    $script:LastUpdateCommand = "check-update-json"
    $script:UpdateStderrText = '{"event":"error","command":"check-update-json","code":"update_digest_missing","detail":"fixture","message":"fixture"}'
    $script:LastUpdateExitCode = 1
    $script:UpdateExitObserved = $true
    $script:UpdateStdoutClosed = $true
    $script:UpdateStderrClosed = $true
    Set-Busy $true
    Complete-UpdateIfReady
    Assert-Equal $script:LastOperationError.code "update_digest_missing" "update finalizer parses stderr error"
    Assert-Equal $statusLabel.Text (T "error") "update error marks status"

    $script:LastOperationError = New-OperationError "update" "check-update-json" "network_failed" "fixture" "fixture" "cli_stderr_json" 1
    Assert-Equal (Get-UpdateFailureReason) (T "updateNetworkFailedMessage") "update network failure uses localized message"
    $script:LastOperationError = New-OperationError "update" "check-update-json" "update_version_invalid" "fixture" "fixture" "cli_stderr_json" 1
    Assert-Equal (Get-UpdateFailureReason) (T "updateVersionInvalidMessage") "update version failure uses localized message"

    Clear-UpdateRunState
    $script:InstallerLaunchPathForSelfTest = ""
    $script:ApplicationExitRequestedForSelfTest = $false
    $script:InstallerLauncherForSelfTest = { param([string]$Path) $script:InstallerLaunchPathForSelfTest = $Path }
    $script:LastUpdateDoneEvent = [pscustomobject]@{
        event = "done"
        kind = "update_installer"
        version = "0.1.4"
        installer_path = "C:\tmp\GEOGetter-Setup-v0.1.4.exe"
        sha256 = ("1" * 64)
        bytes = 12
    }
    $script:LastUpdateExitCode = 0
    $script:UpdateExitObserved = $true
    $script:UpdateStdoutClosed = $true
    $script:UpdateStderrClosed = $true
    Set-Busy $true
    Complete-UpdateIfReady
    Assert-Equal $script:InstallerLaunchPathForSelfTest "C:\tmp\GEOGetter-Setup-v0.1.4.exe" "verified installer is launched"
    Assert-Equal $script:ApplicationExitRequestedForSelfTest $true "installer launch requests GUI exit"
    $script:InstallerLauncherForSelfTest = $null

    Clear-UpdateRunState
    $script:ApplicationExitRequestedForSelfTest = $false
    $script:InstallerLauncherForSelfTest = { param([string]$Path) throw "launch failed" }
    $script:LastUpdateDoneEvent = [pscustomobject]@{
        event = "done"
        kind = "update_installer"
        version = "0.1.4"
        installer_path = "C:\tmp\GEOGetter-Setup-v0.1.4.exe"
        sha256 = ("1" * 64)
        bytes = 12
    }
    $script:LastUpdateExitCode = 0
    $script:UpdateExitObserved = $true
    $script:UpdateStdoutClosed = $true
    $script:UpdateStderrClosed = $true
    Set-Busy $true
    Complete-UpdateIfReady
    Assert-Equal $script:LastOperationError.phase "update_installer_launch" "installer launch failure records operation error phase"
    Assert-Equal $script:LastOperationError.code "installer_launch_failed" "installer launch failure records operation error code"
    Assert-Equal $script:ApplicationExitRequestedForSelfTest $false "installer launch failure leaves GUI open"
    $script:InstallerLauncherForSelfTest = $null
    $originalProcessOutputLimit = $script:ProcessOutputLimitChars
    $script:ProcessOutputLimitChars = 80
    $script:DownloadStdoutText = ""
    Append-DownloadProcessOutput "stdout" ("a" * 100)
    Assert-Contains $script:DownloadStdoutText "earlier process output was truncated" "process output cap marker"
    Assert-Equal ($script:DownloadStdoutText.Length -le 80) $true "process output cap"
    $script:ProcessOutputLimitChars = $originalProcessOutputLimit
    [void]$form.Handle
    $script:HelperProcess = $null
    $script:HelperBridge = $null
    $script:HelperStdoutLines = @()
    $script:HelperStderrLines = @()
    $script:HelperExitCode = $null
    $script:HelperExitObserved = $false
    $script:HelperStdoutClosed = $false
    $script:HelperStderrClosed = $false
    Start-GeoGetterPythonProcess `
        -OperationName "selftest" `
        -CreateStartInfo { New-PythonProcessStartInfo -Arguments @("-c", "import sys; print('helper stdout'); print('helper stderr', file=sys.stderr)") } `
        -OutputHandler ([System.Action[string]]{ param($line) $script:HelperStdoutLines += $line }) `
        -ErrorHandler ([System.Action[string]]{ param($line) $script:HelperStderrLines += $line }) `
        -ExitHandler ([System.Action[int]]{
            param($code)
            $script:HelperExitCode = $code
            $script:HelperExitObserved = $true
        }) `
        -OutputClosedHandler ([System.Action]{ $script:HelperStdoutClosed = $true }) `
        -ErrorClosedHandler ([System.Action]{ $script:HelperStderrClosed = $true }) `
        -SetProcess { param($value) $script:HelperProcess = $value } `
        -SetBridge { param($value) $script:HelperBridge = $value } `
        -OnStartFailure { param($message) throw $message }
    $helperDeadline = [DateTime]::UtcNow.AddSeconds(10)
    while ((-not $script:HelperExitObserved -or -not $script:HelperStdoutClosed -or -not $script:HelperStderrClosed) -and [DateTime]::UtcNow -lt $helperDeadline) {
        [System.Windows.Forms.Application]::DoEvents()
        Start-Sleep -Milliseconds 50
    }
    Assert-Equal $script:HelperExitObserved $true "shared Python process helper observes exit"
    Assert-Equal $script:HelperExitCode 0 "shared Python process helper exit code"
    Assert-Equal $script:HelperStdoutClosed $true "shared Python process helper stdout close"
    Assert-Equal $script:HelperStderrClosed $true "shared Python process helper stderr close"
    Assert-Contains ($script:HelperStdoutLines -join "`n") "helper stdout" "shared Python process helper stdout"
    Assert-Contains ($script:HelperStderrLines -join "`n") "helper stderr" "shared Python process helper stderr"
    $helperProcess = $script:HelperProcess
    $script:HelperProcess = $null
    $script:HelperBridge = $null
    Dispose-ProcessQuietly $helperProcess
    Update-CancelButton
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
    Set-OperationErrorFromProcessOutput "resolve" "resolve-json" $encodingResult.ExitCode $encodingResult.Stdout $encodingResult.Stderr "resolve_failed" (T "metadataFailed")
    Assert-Equal $script:LastOperationError.code "invalid_input" "GUI parses CLI error code"
    Clear-ResolvedState -DeleteResolvedJson
    $noCreateOutput = Join-Path $selfTestRoot "capacity should not be created"
    $outputBox.Text = $noCreateOutput
    Update-Capacity
    Assert-Equal (Test-Path -LiteralPath $noCreateOutput) $false "capacity update does not create output folder"

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
                origin_level = "series"
                origin_accession = "SELFTEST"
                extension = ".txt"
                estimated_type = "table_text"
                size_status = "unknown"
                verification_status = "not_applicable"
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
                library_strategy = "RNA-Seq"
                geo_sample_accession = "GSM2"
                geo_sample_title = "large RNA sample"
            },
            [pscustomobject]@{
                source_accession = "SELFTEST"
                query_accession = "SELFTEST"
                run_accession = "SRR10"
                file_index = 1
                file_name = "large2.fastq.gz"
                url = "https://example.invalid/large2.fastq.gz"
                expected_md5 = ""
                size_bytes = 2392496788
                sample_accession = "SAM2"
                library_layout = "PAIRED"
                library_strategy = "ChIP-Seq"
                geo_sample_accession = "GSM10"
                geo_sample_title = "large ChIP sample"
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
                library_strategy = "RNA-Seq"
                geo_sample_accession = "GSM1"
                geo_sample_title = "small RNA sample"
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
            },
            [pscustomobject]@{
                source_accession = "COLLISION"
                scope = "GEO Series supplementary/processed"
                name = "collision output_download_log.tsv"
                url = $sourceUri
            },
            [pscustomobject]@{
                source_accession = "COLLISION"
                scope = "GEO Series supplementary/processed"
                name = "same.fastq.gz.part"
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
            },
            [pscustomobject]@{
                source_accession = "COLLISION"
                query_accession = "COLLISION"
                run_accession = "SRR4"
                file_index = 1
                file_name = "collision output_fastq_manifest.tsv"
                url = $sourceUri
                expected_md5 = $expectedMd5
                size_bytes = 16
                sample_accession = "SAM4"
                library_layout = "SINGLE"
            }
        )
    }
    Apply-ResolvedResult $collisionFixture
    Set-GridSelection $fastqGrid "selected" $true
    Set-GridSelection $suppGrid "supp_selected" $true
    [System.IO.File]::WriteAllText($script:ResolvedJsonPath, ($collisionFixture | ConvertTo-Json -Depth 10), $utf8NoBom)
    $outputBox.Text = Join-Path $selfTestRoot "collision output"
    $collisionPreflight = Test-DownloadPreflight
    $collisionNames = @($collisionPreflight.PlannedPaths | ForEach-Object { [System.IO.Path]::GetFileName([string]$_) })
    Assert-Equal ($collisionNames -contains "Same.fastq.gz") $true "preflight includes first FASTQ collision name"
    Assert-Equal ($collisionNames -contains "same.2.fastq.gz") $true "preflight includes pre-numbered FASTQ name"
    Assert-Equal ($collisionNames -contains "same.3.fastq.gz") $true "preflight reserves next FASTQ collision name"
    Assert-Equal ($collisionNames -contains "same.4.fastq.gz") $true "preflight reserves supplementary after FASTQ names"
    Assert-Equal ($collisionNames -contains "same.4.fastq.gz.part") $true "preflight includes supplementary part collision path"
    Assert-Equal ($collisionNames -contains "same.fastq.gz.2.part") $true "preflight reserves supplementary away from FASTQ part path"
    Assert-Equal ($collisionNames -contains "same.fastq.gz.2.part.part") $true "preflight includes supplementary doubled part path"
    Assert-Equal ($collisionNames -contains "collision output_fastq_manifest.2.tsv") $true "preflight reserves FASTQ away from artifact name"
    Assert-Equal ($collisionNames -contains "collision output_download_log.2.tsv") $true "preflight reserves supplementary away from artifact name"

    Apply-ResolvedResult $resolvedFixture
    [System.IO.File]::WriteAllText($script:ResolvedJsonPath, ($resolvedFixture | ConvertTo-Json -Depth 10), $utf8NoBom)
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
    Assert-Equal ([string]$fastqLayoutFilterCombo.Items[1]) "PAIRED" "FASTQ layout filter includes PAIRED"
    Assert-Equal ([string]$fastqLayoutFilterCombo.Items[2]) "SINGLE" "FASTQ layout filter includes SINGLE"
    Assert-Equal ([string]$fastqStrategyFilterCombo.Items[1]) "ChIP-Seq" "FASTQ strategy filter includes ChIP-Seq"
    Assert-Equal ([string]$fastqStrategyFilterCombo.Items[2]) "RNA-Seq" "FASTQ strategy filter includes RNA-Seq"
    $fastqFilterBox.Text = "SAM_SMALL"
    Apply-FastqFilter
    Assert-Equal (Get-FastqVisibleRowCount) 1 "FASTQ keyword filter visible count"
    Assert-Contains $fastqTitle.Text "1/3" "FASTQ filtered title count"
    Set-GridSelection $fastqGrid "selected" $true
    Assert-Equal (Get-SelectedFastqIndicesOrEmpty) "2" "filtered keyword selection keeps resolved index"
    Reset-FastqFilterControls
    Assert-Equal (Get-FastqVisibleRowCount) 3 "FASTQ clear filter restores row count"
    Assert-Equal (Get-SelectedFastqIndicesOrEmpty) "2" "hidden FASTQ rows remain unchecked after filtered bulk selection"
    Assert-Contains $fastqTitle.Text "3件" "FASTQ title restored after filter clear"
    Set-GridSelection $fastqGrid "selected" $false
    $fastqLayoutFilterCombo.SelectedItem = "PAIRED"
    Apply-FastqFilter
    Set-GridSelection $fastqGrid "selected" $true
    Assert-Equal (Get-SelectedFastqIndicesOrEmpty) "1" "FASTQ layout filter selection keeps resolved index"
    Reset-FastqFilterControls
    Set-GridSelection $fastqGrid "selected" $false
    $fastqStrategyFilterCombo.SelectedItem = "RNA-Seq"
    Apply-FastqFilter
    Set-GridSelection $fastqGrid "selected" $true
    Assert-Equal (Get-SelectedFastqIndicesOrEmpty) "2,0" "FASTQ strategy filter preserves sorted resolved indices"
    Reset-FastqFilterControls
    Set-GridSelection $fastqGrid "selected" $false
    Assert-Equal $suppGrid.Rows.Count 1 "supplementary row count"
    Assert-Equal $suppGrid.Rows[0].Cells["supp_origin"].Value "Series: SELFTEST" "supplementary origin display"
    Assert-Equal $suppGrid.Rows[0].Cells["supp_name"].Value "processed.txt" "supplementary file name display"
    Assert-Equal $suppGrid.Rows[0].Cells["supp_url"].Value $sourceUri "supplementary URL display"
    $suppGrid.Rows[0].Cells["supp_selected"].Value = $true
    Set-Language "en"
    Assert-Equal $suppGrid.Rows[0].Cells["supp_name"].Value "processed.txt" "supplementary file name survives English language switch"
    Assert-Equal ([bool]$suppGrid.Rows[0].Cells["supp_selected"].Value) $true "supplementary selection survives English language switch"
    Set-Language "ja"
    Assert-Equal $suppGrid.Rows[0].Cells["supp_name"].Value "processed.txt" "supplementary file name survives Japanese language switch"
    Assert-Equal ([bool]$suppGrid.Rows[0].Cells["supp_selected"].Value) $true "supplementary selection survives Japanese language switch"
    $suppGrid.Rows[0].Cells["supp_selected"].Value = $false
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
    Assert-Contains $capacityLabel.Text "必要容量(FASTQ): 0 B /" "capacity label stays FASTQ-only for supplementary selection"
    Assert-Contains $selectionSummaryLabel.Text "GEO supplementary/processed 1 件" "selection summary selected supplementary"
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
    Assert-Equal $fastqLayoutFilterCombo.Items.Count 1 "clear resolved removes stale FASTQ layout filter values"
    Assert-Equal $fastqStrategyFilterCombo.Items.Count 1 "clear resolved removes stale FASTQ strategy filter values"
    Assert-Equal ([string]$fastqLayoutFilterCombo.Items[0]) "すべて" "clear resolved keeps FASTQ layout all option"
    Assert-Equal ([string]$fastqStrategyFilterCombo.Items[0]) "すべて" "clear resolved keeps FASTQ strategy all option"
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

    Clear-ResolvedState -DeleteResolvedJson
    [System.IO.File]::WriteAllText($script:ResolvedJsonPath, ($resolvedFixture | ConvertTo-Json -Depth 10), $utf8NoBom)
    $resolveSuccessInput = New-ResolveInputFile "SELFTEST"
    $script:ResolveInputPath = $resolveSuccessInput
    $script:LastResolveExitCode = 0
    $script:ResolveCanceled = $false
    $script:ResolveExitObserved = $true
    $script:ResolveStdoutClosed = $false
    $script:ResolveStderrClosed = $false
    $script:ResolveFinalized = $false
    $statusLabel.Text = T "fetching"
    Set-Busy $true
    Complete-ResolveIfReady
    Assert-Equal $statusLabel.Text (T "fetching") "resolve finalizer waits for stdout close after exit"
    $script:ResolveStdoutClosed = $true
    Complete-ResolveIfReady
    Assert-Equal $statusLabel.Text (T "fetching") "resolve finalizer waits for stderr close after stdout close"
    $script:ResolveStderrClosed = $true
    Complete-ResolveIfReady
    Assert-Equal $statusLabel.Text (T "complete") "resolve finalizer applies result after stream close"
    Assert-Equal (Test-Path -LiteralPath $resolveSuccessInput) $false "resolve finalizer removes temp input"

    Clear-ResolvedState -DeleteResolvedJson
    $script:ResolveStderrText = '{"event":"error","command":"resolve-json","code":"invalid_input","detail":"late stderr","message":"late stderr"}'
    $script:LastResolveExitCode = 1
    $script:ResolveCanceled = $false
    $script:ResolveExitObserved = $true
    $script:ResolveStdoutClosed = $true
    $script:ResolveStderrClosed = $false
    $script:ResolveFinalized = $false
    $script:LastOperationError = $null
    $statusLabel.Text = T "fetching"
    Set-Busy $true
    Complete-ResolveIfReady
    Assert-Equal $script:LastOperationError $null "resolve finalizer waits for stderr close before setting operation error"
    $script:ResolveStderrClosed = $true
    Complete-ResolveIfReady
    Assert-Equal $script:LastOperationError.code "invalid_input" "resolve finalizer parses stderr error after close"
    Assert-Equal $statusLabel.Text (T "error") "resolve finalizer marks failed resolve"

    $resolveCancelInput = New-ResolveInputFile "SELFTEST"
    $script:ResolveInputPath = $resolveCancelInput
    $script:LastResolveExitCode = 1
    $script:ResolveCanceled = $true
    $script:ResolveExitObserved = $true
    $script:ResolveStdoutClosed = $true
    $script:ResolveStderrClosed = $true
    $script:ResolveFinalized = $false
    $statusLabel.Text = T "fetching"
    Set-Busy $true
    Complete-ResolveIfReady
    Assert-Equal $statusLabel.Text (T "canceled") "resolve finalizer reports cancellation"
    Assert-Equal (Test-Path -LiteralPath $resolveCancelInput) $false "resolve cancel removes temp input"
    Assert-Equal $fetchButton.Enabled $true "resolve cancel clears busy state"

    Clear-ResolvedState -DeleteResolvedJson
    [void]$form.Handle
    Set-Busy $true
    $statusLabel.Text = T "fetching"
    $resolveAsyncInputPath = $null
    try {
        Start-ResolveProcess ""
        $resolveAsyncInputPath = $script:ResolveInputPath
        $deadline = [DateTime]::UtcNow.AddSeconds(10)
        while ($null -ne $script:ResolveProcess -and [DateTime]::UtcNow -lt $deadline) {
            [System.Windows.Forms.Application]::DoEvents()
            Start-Sleep -Milliseconds 50
        }
        for ($i = 0; $i -lt 20; $i++) {
            [System.Windows.Forms.Application]::DoEvents()
            Start-Sleep -Milliseconds 50
        }
    }
    finally {
        if (Test-ProcessRunning $script:ResolveProcess) {
            try { $script:ResolveProcess.Kill() } catch { }
            try { [void]$script:ResolveProcess.WaitForExit(5000) } catch { }
        }
    }
    Assert-Equal $script:ResolveProcess $null "async resolve process finished after invalid input"
    Assert-Equal $script:ResolveFinalized $true "async resolve finalizer ran"
    Assert-Equal $script:ResolveStdoutClosed $true "async resolve stdout closed"
    Assert-Equal $script:ResolveStderrClosed $true "async resolve stderr closed"
    Assert-Equal $statusLabel.Text (T "error") "async resolve invalid input status"
    Assert-Equal $script:LastOperationError.phase "resolve" "async resolve error records phase"
    Assert-Equal $script:LastOperationError.code "invalid_input" "async resolve error records code"
    Assert-Equal $script:ResolveInputPath $null "async resolve clears temp input path"
    Assert-Equal (Test-Path -LiteralPath $resolveAsyncInputPath) $false "async resolve removes temp input"
    Assert-Equal $fetchButton.Enabled $true "async resolve failure clears busy state"

    $cancelProbe = New-Object System.Diagnostics.Process
    $cancelProbe.StartInfo = New-PythonProcessStartInfo -Arguments @("-c", "import time; time.sleep(30)")
    try {
        [void]$cancelProbe.Start()
        $script:ResolveProcess = $cancelProbe
        $script:ResolveCanceled = $false
        Update-CancelButton
        Assert-Equal $cancelButton.Enabled $true "cancel button enabled for resolve process"
        Stop-RunningGuiProcesses | Out-Null
        [void]$cancelProbe.WaitForExit(5000)
        Assert-Equal $cancelProbe.HasExited $true "cancel button kills resolve process"
        Assert-Equal $script:ResolveCanceled $true "cancel button marks resolve canceled"
    }
    finally {
        if (Test-ProcessRunning $cancelProbe) {
            try { $cancelProbe.Kill() } catch { }
            try { [void]$cancelProbe.WaitForExit(5000) } catch { }
        }
        Dispose-ProcessQuietly $cancelProbe
        $script:ResolveProcess = $null
        Update-CancelButton
    }

    $updateCancelProbe = New-Object System.Diagnostics.Process
    $updateCancelProbe.StartInfo = New-PythonProcessStartInfo -Arguments @("-c", "import time; time.sleep(30)")
    try {
        [void]$updateCancelProbe.Start()
        $script:UpdateProcess = $updateCancelProbe
        $script:UpdateCanceled = $false
        Update-CancelButton
        Assert-Equal $cancelButton.Enabled $true "cancel button enabled for update process"
        Stop-RunningGuiProcesses | Out-Null
        [void]$updateCancelProbe.WaitForExit(5000)
        Assert-Equal $updateCancelProbe.HasExited $true "cancel button kills update process"
        Assert-Equal $script:UpdateCanceled $true "cancel button marks update canceled"
    }
    finally {
        if (Test-ProcessRunning $updateCancelProbe) {
            try { $updateCancelProbe.Kill() } catch { }
            try { [void]$updateCancelProbe.WaitForExit(5000) } catch { }
        }
        Dispose-ProcessQuietly $updateCancelProbe
        $script:UpdateProcess = $null
        Update-CancelButton
    }

    $shutdownProbe = New-Object System.Diagnostics.Process
    $shutdownProbe.StartInfo = New-PythonProcessStartInfo -Arguments @("-c", "import time; time.sleep(30)")
    $shutdownInput = New-ResolveInputFile "SELFTEST"
    try {
        [void]$shutdownProbe.Start()
        $script:ResolveProcess = $shutdownProbe
        $script:ResolveInputPath = $shutdownInput
        $script:ResolveCanceled = $false
        Stop-RunningGuiProcessesForShutdown
        [void]$shutdownProbe.WaitForExit(5000)
        Assert-Equal $shutdownProbe.HasExited $true "shutdown kills resolve process"
        Assert-Equal $script:ResolveCanceled $true "shutdown marks resolve canceled"
        Assert-Equal $script:ResolveInputPath $null "shutdown clears resolve temp input path"
        Assert-Equal (Test-Path -LiteralPath $shutdownInput) $false "shutdown removes resolve temp input"
    }
    finally {
        if (Test-ProcessRunning $shutdownProbe) {
            try { $shutdownProbe.Kill() } catch { }
            try { [void]$shutdownProbe.WaitForExit(5000) } catch { }
        }
        Dispose-ProcessQuietly $shutdownProbe
        $script:ResolveProcess = $null
        Remove-ResolveInputFile
        Update-CancelButton
    }

    Clear-ResolvedState -DeleteResolvedJson
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
    Handle-DownloadLine '{"event":"progress","file_name":"large2.fastq.gz","downloaded":1,"total":10,"aggregate_downloaded":5,"aggregate_total":20}'
    Assert-Equal $progressBar.Value 25 "progress bar uses aggregate FASTQ progress when present"
    Handle-DownloadLine '{"event":"message","message":"download_started: large1.fastq.gz"}'
    Assert-Equal $statusLabel.Text (T "downloading") "normal download message does not change status"
    Handle-DownloadLine '{"event":"message","message":"network_retry: waiting 5s before retry (2/4) after temporary failure"}'
    Assert-Equal $statusLabel.Text (T "downloadRetryWaiting") "retry message updates status"
    Handle-DownloadLine '{"event":"progress","file_name":"large1.fastq.gz","downloaded":1188518086,"total":2377036173}'
    Assert-Equal $statusLabel.Text (T "downloading") "progress restores status after retry wait"
    $script:DownloadExitObserved = $false
    $script:DownloadStdoutClosed = $false
    $script:DownloadFinalized = $false
    $statusLabel.Text = T "downloading"
    Handle-DownloadLine '{"event":"done","statuses":["md5_unavailable"],"output_dir":"C:\\tmp\\SELFTEST","fastq_manifest":"","supplementary_manifest":"","download_log":"C:\\tmp\\SELFTEST\\SELFTEST_download_log.tsv"}'
    Assert-Equal $statusLabel.Text (T "downloading") "done event does not finalize status before exit and stdout close"
    Assert-Equal (Get-DownloadFinalStatusKey ([pscustomobject]@{ statuses = @("md5_verified", "download_complete") }) 0 $false) "complete" "final state all ok"
    Assert-Equal (Get-DownloadFinalStatusKey ([pscustomobject]@{ statuses = @("md5_unavailable") }) 0 $false) "completeUnverified" "final state md5 unavailable"
    Assert-Equal (Get-DownloadFinalStatusKey ([pscustomobject]@{ statuses = @("network_failed") }) 1 $false) "completePartial" "final state network failed"
    Assert-Equal (Get-DownloadFinalStatusKey ([pscustomobject]@{ statuses = @("local_io_failed") }) 1 $false) "completePartial" "final state local I/O failed"
    Assert-Equal (Get-DownloadFinalStatusKey ([pscustomobject]@{ statuses = @("md5_mismatch") }) 1 $false) "completePartial" "final state md5 mismatch"
    Assert-Equal (Get-DownloadFinalStatusKey ([pscustomobject]@{ statuses = @("size_mismatch") }) 1 $false) "completePartial" "final state size mismatch"
    Assert-Equal (Get-DownloadFinalStatusKey $null 0 $false) "error" "final state missing done event with zero exit"
    Assert-Equal (Get-DownloadFinalStatusKey $null 1 $false) "error" "final state missing done event with nonzero exit"
    Assert-Equal (Get-DownloadFinalStatusKey ([pscustomobject]@{ statuses = @("md5_verified") }) 1 $true) "canceled" "final state canceled wins"
    $script:DownloadCanceled = $true
    Clear-DownloadRunState
    Assert-Equal $script:DownloadCanceled $false "download run state clear resets canceled flag"
    $script:VerifyCanceled = $true
    Clear-VerificationRunState
    Assert-Equal $script:VerifyCanceled $false "verification run state clear resets canceled flag"

    $script:LastDownloadDoneEvent = $null
    $script:LastDownloadExitCode = 1
    $script:DownloadCanceled = $false
    $script:DownloadExitObserved = $true
    $script:DownloadStdoutClosed = $false
    $script:DownloadStderrClosed = $false
    $script:DownloadFinalized = $false
    $statusLabel.Text = T "downloading"
    Complete-DownloadIfReady
    Assert-Equal $statusLabel.Text (T "downloading") "download finalizer waits for stdout close after exit"
    $script:LastDownloadDoneEvent = [pscustomobject]@{ statuses = @("md5_unavailable") }
    Complete-DownloadIfReady
    Assert-Equal $statusLabel.Text (T "downloading") "download finalizer still waits for stdout close after done"
    $script:DownloadStdoutClosed = $true
    Complete-DownloadIfReady
    Assert-Equal $statusLabel.Text (T "downloading") "download finalizer waits for stderr close after stdout close"
    $script:DownloadStderrClosed = $true
    Complete-DownloadIfReady
    Assert-Equal $statusLabel.Text (T "completeUnverified") "download finalizer handles exit before done processing"

    $script:LastVerificationDoneEvent = $null
    $script:LastVerificationExitCode = 0
    $script:VerifyCanceled = $false
    $script:VerifyExitObserved = $true
    $script:VerifyStdoutClosed = $false
    $script:VerifyStderrClosed = $false
    $script:VerifyFinalized = $false
    $statusLabel.Text = T "verifyingManifest"
    Complete-ManifestVerificationIfReady
    Assert-Equal $statusLabel.Text (T "verifyingManifest") "verification finalizer waits for stdout close after exit"
    $script:LastVerificationDoneEvent = [pscustomobject]@{ report = "C:\tmp\verification_report.tsv" }
    Complete-ManifestVerificationIfReady
    Assert-Equal $statusLabel.Text (T "verifyingManifest") "verification finalizer still waits for stdout close after done"
    $script:VerifyStdoutClosed = $true
    Complete-ManifestVerificationIfReady
    Assert-Equal $statusLabel.Text (T "verifyingManifest") "verification finalizer waits for stderr close after stdout close"
    $script:VerifyStderrClosed = $true
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
    Assert-Equal $script:LastOperationError.phase "download_preflight" "preflight records operation error phase"
    Assert-Equal $script:LastOperationError.code "output_path_invalid" "preflight records operation error code"

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

    $nonEmptySuppOutput = Join-Path $selfTestRoot "nonempty supp output"
    [System.IO.Directory]::CreateDirectory($nonEmptySuppOutput) | Out-Null
    [System.IO.File]::WriteAllText((Join-Path $nonEmptySuppOutput "existing.txt"), "existing", $utf8NoBom)
    $outputBox.Text = $nonEmptySuppOutput
    $nonEmptySuppMessage = ""
    try {
        Test-DownloadPreflight | Out-Null
    }
    catch {
        $nonEmptySuppMessage = $_.Exception.Message
    }
    Assert-Contains $nonEmptySuppMessage "supplementary" "preflight rejects supplementary in nonempty output"
    Assert-Equal $script:LastOperationError.code "resume_supplementary_unsupported" "preflight records supplementary resume code"
    Assert-Equal $script:LastResumeErrorCode "resume_supplementary_unsupported" "preflight records resume-specific code"
    Assert-Equal $script:LastExistingOutputNonEmpty $true "preflight records nonempty output"

    $outputBox.Text = Join-Path $selfTestRoot "huge fastq output"
    Set-GridSelection $fastqGrid "selected" $true
    Set-GridSelection $suppGrid "supp_selected" $false
    $originalSmallSize = [Int64]$script:Resolved.fastq_files[[int]$fastqGrid.Rows[0].Tag].size_bytes
    $hugeFreeBytes = Get-FreeSpaceForPathOrNull $outputBox.Text
    if ($null -eq $hugeFreeBytes) { throw "self-test could not read temporary drive free space" }
    $script:Resolved.fastq_files[[int]$fastqGrid.Rows[0].Tag].size_bytes = [Int64]$hugeFreeBytes + 1
    [System.IO.File]::WriteAllText($script:ResolvedJsonPath, ($script:Resolved | ConvertTo-Json -Depth 10), $utf8NoBom)
    $hugePreflightMessage = ""
    try {
        Test-DownloadPreflight | Out-Null
    }
    catch {
        $hugePreflightMessage = $_.Exception.Message
    }
    finally {
        $script:Resolved.fastq_files[[int]$fastqGrid.Rows[0].Tag].size_bytes = $originalSmallSize
        [System.IO.File]::WriteAllText($script:ResolvedJsonPath, ($script:Resolved | ConvertTo-Json -Depth 10), $utf8NoBom)
    }
    Assert-Contains $hugePreflightMessage "空き容量" "preflight rejects insufficient FASTQ capacity"
    Assert-Equal $script:LastOperationError.code "insufficient_space" "preflight records insufficient space code"

    $outputBox.Text = Join-Path $selfTestRoot "SELFTEST"
    Set-GridSelection $fastqGrid "selected" $false
    $fastqGrid.Rows[0].Cells["selected"].Value = $true
    $suppGrid.Rows[0].Cells["supp_selected"].Value = $true
    $downloadPreflight = Test-DownloadPreflight
    $selfTestRunOutput = [System.IO.Path]::GetFullPath([string]$outputBox.Text)
    Assert-Equal $script:LastPreflightOutputDir $selfTestRunOutput "preflight output dir matches output field"
    Assert-Equal $downloadPreflight.OutputDir $selfTestRunOutput "preflight result output dir matches output field"

    $downloadResult = Invoke-SelectedDownloadJsonForSelfTest (Get-SelectedFastqIndicesOrEmpty) (Get-SelectedSuppIndicesOrEmpty)
    $script:DownloadStdoutText = Limit-ProcessOutputText $downloadResult.Stdout
    $script:DownloadStderrText = Limit-ProcessOutputText $downloadResult.Stderr
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
    $script:VerifyStdoutText = Limit-ProcessOutputText $verifyResult.Stdout
    $script:VerifyStderrText = Limit-ProcessOutputText $verifyResult.Stderr
    $script:LastVerificationExitCode = $verifyResult.ExitCode
    $verifyDoneLine = @($verifyResult.Stdout -split "`r?`n" | Where-Object { $_ -match '"kind"\s*:\s*"manifest_verification"' } | Select-Object -Last 1)
    if ($verifyDoneLine.Count -gt 0) {
        $script:LastVerificationDoneEvent = $verifyDoneLine[0] | ConvertFrom-Json
    }
    Assert-Equal $verifyResult.ExitCode 0 "verify-manifest-json exit code"
    Assert-Contains $verifyResult.Stdout '"kind": "manifest_verification"' "verify-manifest-json done event"
    Assert-Equal (Test-Path -LiteralPath (Join-Path $selfTestRunOutput "verification_report.tsv")) $true "verification report exists"
    Assert-Contains (Get-Content -Raw -Encoding UTF8 (Join-Path $selfTestRunOutput "verification_report.tsv")) "md5_verified" "verification report md5 success"

    $resumeOutput = Join-Path $selfTestRoot "resume fastq output"
    $outputBox.Text = $resumeOutput
    Set-GridSelection $fastqGrid "selected" $false
    $fastqGrid.Rows[0].Cells["selected"].Value = $true
    Set-GridSelection $suppGrid "supp_selected" $false
    $resumeFirst = Invoke-SelectedDownloadJsonForSelfTest (Get-SelectedFastqIndicesOrEmpty) (Get-SelectedSuppIndicesOrEmpty)
    Assert-Equal $resumeFirst.ExitCode 0 "resume fixture first download exit code"
    $resumePreflight = Test-DownloadPreflight
    Assert-Equal $resumePreflight.ExistingOutputNonEmpty $true "preflight detects nonempty FASTQ resume output"
    $resumeArgs = Get-DownloadPythonArguments (Get-SelectedFastqIndicesOrEmpty) (Get-SelectedSuppIndicesOrEmpty) $true
    Assert-Equal ($resumeArgs -contains "--resume-existing") $true "resume argument is passed to selected-download-json"
    $resumeSecond = Invoke-SelectedDownloadJsonForSelfTest (Get-SelectedFastqIndicesOrEmpty) (Get-SelectedSuppIndicesOrEmpty) $true
    Assert-Equal $resumeSecond.ExitCode 0 "resume fixture second download exit code"
    Assert-Contains $resumeSecond.Stdout '"resume_existing": true' "resume done event records resume mode"
    Assert-Contains $resumeSecond.Stdout '"resume_required_bytes": 0' "resume done event records remaining bytes"
    $script:ResumeExistingConfirmationForSelfTest = $false
    $script:DownloadProcess = $null
    Start-DownloadProcess
    Assert-Equal $script:DownloadProcess $null "resume cancellation does not start subprocess"
    Assert-Equal $script:LastResumeExistingRequested $false "resume cancellation records no resume request"
    Assert-Equal $statusLabel.Text (T "canceled") "resume cancellation updates status"
    $script:ResumeExistingConfirmationForSelfTest = $true
    $progressBar.Value = 0
    $statusLabel.Text = T "downloading"
    Start-DownloadProcess
    Assert-Equal ($script:LastDownloadArguments -contains "--resume-existing") $true "download start passes resume argument after confirmation"
    Assert-Equal $script:LastResumeExistingRequested $true "download start records confirmed resume request"
    $resumeStartDeadline = [DateTime]::UtcNow.AddSeconds(10)
    while ($null -ne $script:DownloadProcess -and [DateTime]::UtcNow -lt $resumeStartDeadline) {
        [System.Windows.Forms.Application]::DoEvents()
        Start-Sleep -Milliseconds 50
    }
    for ($i = 0; $i -lt 20; $i++) {
        [System.Windows.Forms.Application]::DoEvents()
        Start-Sleep -Milliseconds 50
    }
    Assert-Equal $script:DownloadProcess $null "confirmed resume subprocess finished"
    Assert-Equal $statusLabel.Text (T "complete") "confirmed resume completes"
    Assert-Equal ([bool]$script:LastDownloadDoneEvent.resume_existing) $true "confirmed resume done event records resume mode"
    $script:ResumeExistingConfirmationForSelfTest = $null

    $downloadDoneEventForErrorCheck = $script:LastDownloadDoneEvent
    $script:LastDownloadDoneEvent = $null
    $script:LastDownloadExitCode = 1
    $script:DownloadCanceled = $false
    $script:DownloadExitObserved = $true
    $script:DownloadStdoutClosed = $true
    $script:DownloadStderrClosed = $true
    $script:DownloadFinalized = $false
    $script:DownloadStdoutText = ""
    $script:DownloadStderrText = '{"event":"error","command":"selected-download-json","code":"invalid_json","detail":"fixture","message":"fixture"}'
    $script:LastPreflightOutputDir = $selfTestRunOutput
    $script:LastOperationError = $null
    Complete-DownloadIfReady
    Assert-Equal $script:LastOperationError.phase "download" "download without done records phase"
    Assert-Equal $script:LastOperationError.code "invalid_json" "download without done parses CLI error"
    $script:LastDownloadDoneEvent = $downloadDoneEventForErrorCheck
    $script:LastDownloadExitCode = $downloadResult.ExitCode
    $script:DownloadFinalized = $true
    $script:LastOperationError = $null

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
    Assert-Equal $script:LastOperationError.phase "download_preflight" "download start validation records phase"
    Assert-Equal $script:LastOperationError.code "resolved_state_invalid" "download start validation records code"
    $inputBox.Text = "SELFTEST"

    $originalPythonExe = $PythonExe
    $PythonExe = Join-Path $selfTestRoot "missing-python.exe"
    $threwResolveStart = $false
    $resolveStartInputPath = $null
    $resolveFailureMarker = "SELFTEST_RESOLVE_START_FAILURE_" + [System.Guid]::NewGuid().ToString("N")
    $resolveFailureStartedAt = [DateTime]::UtcNow.AddSeconds(-1)
    try {
        Start-ResolveProcess $resolveFailureMarker
        $resolveStartInputPath = $script:ResolveInputPath
    }
    catch {
        $threwResolveStart = $true
        $resolveStartInputPath = $script:ResolveInputPath
    }
    finally {
        $PythonExe = $originalPythonExe
    }
    Assert-Equal $threwResolveStart $true "resolve start failure throws"
    Assert-Equal $script:ResolveProcess $null "resolve start failure clears process"
    Assert-Equal $script:ResolveInputPath $null "resolve start failure clears temp input path"
    Assert-Equal $resolveStartInputPath $null "resolve start failure removes temp input path before returning"
    $leakedResolveInputs = @(
        Get-ChildItem -Path ([System.IO.Path]::GetTempPath()) -Filter "geo_getter_input_*.txt" -File -ErrorAction SilentlyContinue |
        Where-Object { $_.LastWriteTimeUtc -ge $resolveFailureStartedAt } |
        Where-Object {
            try { (Get-Content -Raw -Encoding UTF8 -LiteralPath $_.FullName) -eq $resolveFailureMarker }
            catch { $false }
        }
    )
    Assert-Equal $leakedResolveInputs.Count 0 "resolve start failure removes temp input file"
    Assert-Equal $script:LastOperationError.phase "resolve_process_start" "resolve start failure records phase"
    Assert-Equal $script:LastOperationError.code "process_start_failed" "resolve start failure records code"

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
    Assert-Equal $script:LastOperationError.phase "download_preflight" "download start failure is blocked by preflight phase"
    Assert-Equal $script:LastOperationError.code "process_start_failed" "download start failure records code"

    $PythonExe = Join-Path $selfTestRoot "missing-python.exe"
    $threwUpdateStart = $false
    try {
        Start-UpdateCheckProcess
    }
    catch {
        $threwUpdateStart = $true
    }
    finally {
        $PythonExe = $originalPythonExe
    }
    Assert-Equal $threwUpdateStart $true "update start failure throws"
    Assert-Equal $script:UpdateProcess $null "update start failure clears process"
    Assert-Equal $script:LastOperationError.phase "update_process_start" "update start failure records phase"
    Assert-Equal $script:LastOperationError.code "process_start_failed" "update start failure records code"

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
