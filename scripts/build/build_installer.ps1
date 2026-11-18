<#
.SYNOPSIS
One-command reproducible build script for EasyFaceless Studio Windows Installer (Stage 8C.2, D1-D7).

.DESCRIPTION
1. Reads APP_NAME, APP_VERSION, APP_OWNER, APP_SETUP_PREFIX from app/services/branding/constants.py
2. Warns clearly if LICENSE still contains [NAMA PEMILIK]
3. Stages clean codebase, relocatable Python 3.11 + uv.lock packages, FFmpeg + GPLv3 licenses, and icons
4. Compiles native WinExe launcher (EasyFaceless Studio.exe) with zero console popup
5. Verifies offline imports and runs scripts/build/verify_package.py
6. Builds Inno Setup installer -> dist\EasyFacelessStudio-Setup-<version>.exe
7. Prints staging size, installer size, and top-level bundle contents
#>

[CmdletBinding()]
param(
    [string]$FFmpegDir = "",
    [string]$InnoCompiler = "",
    [switch]$SkipClean
)

$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Push-Location $RepoRoot
try {
    Write-Host "=== EasyFaceless Studio Windows Package Build ===" -ForegroundColor Cyan

    # 1. Check LICENSE placeholder (D5)
    $LicensePath = Join-Path $RepoRoot "LICENSE"
    $LicenseContent = Get-Content $LicensePath -Raw -Encoding UTF8
    if ($LicenseContent -match "\[NAMA PEMILIK\]") {
        Write-Warning "LICENSE masih berisi placeholder '[NAMA PEMILIK]'! Harap isi nama pemilik sebelum rilis."
    }

    # 2. Check prerequisites
    $VenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
    if (-not (Test-Path $VenvPython)) {
        throw "Virtual environment Python tidak ditemukan di '$VenvPython'. Jalankan 'uv sync' terlebih dahulu."
    }

    if (-not $InnoCompiler) {
        $Candidates = @(
            (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe"),
            "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
            "C:\Program Files\Inno Setup 6\ISCC.exe"
        )
        foreach ($c in $Candidates) {
            if (Test-Path $c) { $InnoCompiler = $c; break }
        }
        if (-not $InnoCompiler) {
            $Cmd = Get-Command ISCC.exe -ErrorAction SilentlyContinue
            if ($Cmd) { $InnoCompiler = $Cmd.Source }
        }
    }
    if (-not $InnoCompiler -or -not (Test-Path $InnoCompiler)) {
        throw "Inno Setup 6 (ISCC.exe) tidak ditemukan. Pasang lewat: winget install JRSoftware.InnoSetup"
    }

    $CscExe = "C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
    if (-not (Test-Path $CscExe)) {
        $CscCmd = Get-Command csc.exe -ErrorAction SilentlyContinue
        if ($CscCmd) { $CscExe = $CscCmd.Source }
    }
    if (-not $CscExe -or -not (Test-Path $CscExe)) {
        throw ".NET Framework C# compiler (csc.exe) tidak ditemukan untuk membangun launcher WinExe."
    }

    if (-not $FFmpegDir) {
        $FfmpegCmd = Get-Command ffmpeg.exe -ErrorAction SilentlyContinue
        if ($FfmpegCmd) {
            $FFmpegDir = Split-Path -Parent $FfmpegCmd.Source
        } elseif (Test-Path "C:\ffmpeg\bin\ffmpeg.exe") {
            $FFmpegDir = "C:\ffmpeg\bin"
        }
    }
    if (-not $FFmpegDir -or -not (Test-Path (Join-Path $FFmpegDir "ffmpeg.exe")) -or -not (Test-Path (Join-Path $FFmpegDir "ffprobe.exe"))) {
        throw "FFmpeg/FFprobe binaries tidak ditemukan. Tentukan -FFmpegDir atau tambahkan ke PATH."
    }

    # 3. Read single-source metadata from constants.py
    $MetaJson = & $VenvPython -c "import json; from app.services.branding.constants import APP_NAME, APP_VERSION, APP_OWNER, APP_SETUP_PREFIX; print(json.dumps({'name': APP_NAME, 'version': APP_VERSION, 'owner': APP_OWNER, 'prefix': APP_SETUP_PREFIX}))"
    $Meta = $MetaJson | ConvertFrom-Json
    $AppName = $Meta.name
    $AppVersion = $Meta.version
    $AppOwner = $Meta.owner
    $SetupBaseName = "$($Meta.prefix)-$AppVersion"

    Write-Host "App       : $AppName v$AppVersion"
    Write-Host "Publisher : $AppOwner"
    Write-Host "InnoSetup : $InnoCompiler"
    Write-Host "FFmpeg    : $FFmpegDir"

    # 4. Prepare staging and output directories
    $BuildRoot = Join-Path $RepoRoot "build"
    $StagingDir = Join-Path $BuildRoot "staging\$AppName"
    $DistDir = Join-Path $RepoRoot "dist"
    if (-not $SkipClean -and (Test-Path $StagingDir)) {
        Remove-Item $StagingDir -Recurse -Force
    }
    New-Item -ItemType Directory -Path $StagingDir -Force | Out-Null
    New-Item -ItemType Directory -Path $DistDir -Force | Out-Null

    # Generate multi-res .ico
    $IconPath = Join-Path $RepoRoot "resource\branding\app.ico"
    & $VenvPython (Join-Path $RepoRoot "packaging\launcher\generate_icon.py") --dst $IconPath | Out-Null

    # 5. Copy clean app files into staging (strictly exclude storage, config.toml, .git, test, screens, backups)
    Write-Host "[1/6] Staging application code & assets..."
    foreach ($dirName in @("app", "webui", "resource", "licenses")) {
        $srcD = Join-Path $RepoRoot $dirName
        $dstD = Join-Path $StagingDir $dirName
        if (-not (Test-Path $srcD)) { throw "Missing source directory: $srcD" }
        & robocopy $srcD $dstD /E /XD "__pycache__" ".pytest_cache" "screens" /XF "*.pyc" "*.pyo" "*.log" "*.backup*" "*.bak" | Out-Null
        if ($LASTEXITCODE -ge 8) { throw "Robocopy failed for $dirName (exit $LASTEXITCODE)" }
    }

    # Copy read-only config/longform.toml
    $DstConfigDir = Join-Path $StagingDir "config"
    New-Item -ItemType Directory -Path $DstConfigDir -Force | Out-Null
    Copy-Item (Join-Path $RepoRoot "config\longform.toml") (Join-Path $DstConfigDir "longform.toml") -Force

    # Copy essential user docs & CapCut runtime template (excluding docs\branding\screens and dev archives)
    $DstUserDocs = Join-Path $StagingDir "docs\user"
    & robocopy (Join-Path $RepoRoot "docs\user") $DstUserDocs /E | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "Robocopy failed for docs\user (exit $LASTEXITCODE)" }

    $DstCapCutTpl = Join-Path $StagingDir "docs\longform\capcut_template_8.9.0"
    & robocopy (Join-Path $RepoRoot "docs\longform\capcut_template_8.9.0") $DstCapCutTpl /E /XF "*.bak" "*.tmp" "*.log" | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "Robocopy failed for capcut_template_8.9.0 (exit $LASTEXITCODE)" }

    # Copy top-level files
    foreach ($topFile in @("cli.py", "cli_longform.py", "config.example.toml", "LICENSE", "README.md", "THIRD_PARTY_NOTICES.md", "CHANGELOG.md")) {
        $fp = Join-Path $RepoRoot $topFile
        if (Test-Path $fp) {
            Copy-Item $fp (Join-Path $StagingDir $topFile) -Force
        }
    }

    # Write installed-mode marker cleanly
    "EasyFaceless Studio v$AppVersion installed package marker" | Out-File (Join-Path $StagingDir ".efs_installed") -Encoding utf8 -Force

    # 6. Bundle relocatable Python runtime + uv.lock dependencies
    Write-Host "[2/6] Bundling relocatable Python runtime & uv.lock dependencies..."
    $BasePrefix = (& $VenvPython -c "import sys; print(sys.base_prefix)").Trim()
    $RuntimePyDir = Join-Path $StagingDir "runtime\python"
    New-Item -ItemType Directory -Path $RuntimePyDir -Force | Out-Null

    # Copy root Python binaries & DLLs
    Get-ChildItem $BasePrefix -File | Where-Object { $_.Extension -in ".exe", ".dll", ".txt" } | ForEach-Object {
        Copy-Item $_.FullName (Join-Path $RuntimePyDir $_.Name) -Force
    }
    foreach ($pySub in @("DLLs", "Lib")) {
        $srcSub = Join-Path $BasePrefix $pySub
        $dstSub = Join-Path $RuntimePyDir $pySub
        $xd = @("__pycache__", "site-packages", "idlelib", "tkinter", "test", "turtledemo")
        if ($pySub -eq "Lib") {
            # Keep Lib\site-packages separate (from .venv), keep standard library & tkinter omitted cleanly unless needed
            & robocopy $srcSub $dstSub /E /XD "__pycache__" "site-packages" "test" "idlelib" "turtledemo" /XF "*.pyc" "*.pyo" | Out-Null
        } else {
            & robocopy $srcSub $dstSub /E /XD "__pycache__" | Out-Null
        }
        if ($LASTEXITCODE -ge 8) { throw "Robocopy failed for Python $pySub (exit $LASTEXITCODE)" }
    }

    # Copy .venv site-packages into bundled runtime
    $VenvSitePkg = Join-Path $RepoRoot ".venv\Lib\site-packages"
    $DstSitePkg = Join-Path $RuntimePyDir "Lib\site-packages"
    & robocopy $VenvSitePkg $DstSitePkg /E /XD "__pycache__" ".pytest_cache" /XF "*.pyc" "*.pyo" "*editable*.pth" "a1_coverage.pth" | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "Robocopy failed for site-packages (exit $LASTEXITCODE)" }

    # Remove any dev-only .pth / write relative path to staging root
    Get-ChildItem $DstSitePkg -Filter "*editable*.pth" -ErrorAction SilentlyContinue | Remove-Item -Force
    # Write relative path from runtime\python\Lib\site-packages -> install root (..\..\..)
    "..\..\.." | Out-File (Join-Path $DstSitePkg "efs_app_root.pth") -Encoding ascii -Force

    # 7. Bundle FFmpeg & probe binaries + ensure GPLv3 license files
    Write-Host "[3/6] Bundling FFmpeg binaries & GPLv3 compliance notices..."
    $DstFFmpegBin = Join-Path $StagingDir "runtime\ffmpeg\bin"
    New-Item -ItemType Directory -Path $DstFFmpegBin -Force | Out-Null
    Copy-Item (Join-Path $FFmpegDir "ffmpeg.exe") (Join-Path $DstFFmpegBin "ffmpeg.exe") -Force
    Copy-Item (Join-Path $FFmpegDir "ffprobe.exe") (Join-Path $DstFFmpegBin "ffprobe.exe") -Force
    Get-ChildItem $FFmpegDir -Filter "*.dll" -ErrorAction SilentlyContinue | ForEach-Object {
        Copy-Item $_.FullName (Join-Path $DstFFmpegBin $_.Name) -Force
    }

    # 8. Compile native WinExe Launcher with app icon
    Write-Host "[4/6] Compiling EasyFaceless Studio.exe launcher..."
    $LauncherExe = Join-Path $StagingDir "$AppName.exe"
    $LauncherCs = Join-Path $RepoRoot "packaging\launcher\LauncherStub.cs"
    & $CscExe /nologo /target:winexe /optimize+ "/win32icon:$IconPath" "/out:$LauncherExe" /r:System.Windows.Forms.dll $LauncherCs
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $LauncherExe)) {
        throw "Gagal mengompilasi EasyFaceless Studio.exe!"
    }

    # 9. Verify offline Python imports & run strict staging hygiene check
    Write-Host "[5/6] Verifying bundled offline runtime & package hygiene..."
    $BundledPy = Join-Path $RuntimePyDir "python.exe"
    $SavedPath = $env:PATH
    $SavedEfsData = $env:EFS_DATA_DIR
    $TmpVerifyData = Join-Path $BuildRoot "_verify_data"
    try {
        if (Test-Path $TmpVerifyData) { Remove-Item $TmpVerifyData -Recurse -Force }
        $env:PATH = "$DstFFmpegBin;$RuntimePyDir;$env:SystemRoot\System32"
        $env:EFS_DATA_DIR = $TmpVerifyData
        & $BundledPy -B -I -c "import sys; sys.path.insert(0, r'$StagingDir'); import streamlit, moviepy, edge_tts, PIL, openai, toml, app.services.longform.app_paths, app.services.longform.platform_utils; print('BUNDLED_IMPORTS_OK')"
        if ($LASTEXITCODE -ne 0) {
            throw "Offline import verification failed inside bundled Python runtime!"
        }
        $env:EFS_INSTALLED = "1"
        $env:PYTHONPATH = ""
        & $BundledPy -B -c "import app.services.longform.app_paths as ap; print('PTH_DATA_DIR:', ap.get_data_dir())"
        if ($LASTEXITCODE -ne 0) {
            throw "Relocatable .pth import verification failed!"
        }
    } finally {
        $env:PATH = $SavedPath
        $env:EFS_DATA_DIR = $SavedEfsData
        if (Test-Path $TmpVerifyData) { Remove-Item $TmpVerifyData -Recurse -Force }
    }

    & $VenvPython (Join-Path $RepoRoot "scripts\build\verify_package.py") --staging $StagingDir
    if ($LASTEXITCODE -ne 0) {
        throw "Package hygiene verification failed!"
    }

    # 10. Compile Inno Setup Installer
    Write-Host "[6/6] Compiling Inno Setup installer -> dist\$SetupBaseName.exe..."
    $IssFile = Join-Path $RepoRoot "packaging\installer\efs.iss"
    & $InnoCompiler `
        "/DMyAppName=$AppName" `
        "/DMyAppVersion=$AppVersion" `
        "/DMyAppPublisher=$AppOwner" `
        "/DMyAppExeName=$AppName.exe" `
        "/DMyAppSetupBase=$SetupBaseName" `
        "/DMyStagingDir=$StagingDir" `
        "/DMyOutputDir=$DistDir" `
        "/DMyIconFile=$IconPath" `
        "/DMyLicenseFile=$LicensePath" `
        $IssFile
    if ($LASTEXITCODE -ne 0) {
        throw "Inno Setup compiler failed (exit $LASTEXITCODE)!"
    }

    $InstallerExe = Join-Path $DistDir "$SetupBaseName.exe"
    $StagingBytes = (Get-ChildItem $StagingDir -Recurse -File | Measure-Object -Property Length -Sum).Sum
    $InstallerBytes = (Get-Item $InstallerExe).Length
    $StagingMB = [Math]::Round($StagingBytes / 1MB, 2)
    $InstallerMB = [Math]::Round($InstallerBytes / 1MB, 2)

    Write-Host ""
    Write-Host "=== BUILD SUCCEEDED ===" -ForegroundColor Green
    Write-Host "Installer   : $InstallerExe ($InstallerMB MB / $InstallerBytes bytes)"
    Write-Host "Staged Size : $StagingMB MB ($StagingBytes bytes)"
    Write-Host "Top-level items in package:"
    Get-ChildItem $StagingDir | Select-Object Name, Mode | Format-Table -AutoSize
}
finally {
    Pop-Location
}
