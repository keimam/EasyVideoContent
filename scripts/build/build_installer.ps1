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
    [switch]$SkipClean,
    [switch]$AllowDirty
)

$ErrorActionPreference = "Stop"

function Get-EfsStagingProcesses {
    param([string]$TargetDir)
    if ([string]::IsNullOrWhiteSpace($TargetDir)) { return @() }
    $norm = [System.IO.Path]::GetFullPath($TargetDir).TrimEnd('\', '/').ToLowerInvariant()
    $hits = @()
    try {
        $all = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue
        foreach ($p in $all) {
            $ep = if ($p.ExecutablePath) { $p.ExecutablePath.ToLowerInvariant() } else { "" }
            $cmd = if ($p.CommandLine) { $p.CommandLine.ToLowerInvariant() } else { "" }
            $inStaging = ($ep.StartsWith("$norm\") -or $ep.StartsWith("$norm/") -or $cmd.Contains($norm))
            if ($inStaging -and ($p.Name -like "python*" -or $p.Name -like "EasyFaceless*")) {
                $hits += [PSCustomObject]@{
                    ProcessId = [int]$p.ProcessId
                    Name = [string]$p.Name
                    ExecutablePath = [string]$p.ExecutablePath
                }
            }
        }
    } catch {}
    return $hits
}

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

    # 3. Verify git clean state (E12.3) and read commit + single-source metadata
    $GitDirty = (& git status --porcelain 2>$null)
    if ($GitDirty -and -not $AllowDirty) {
        throw "Working tree git tidak bersih (commit perubahan lebih dulu, atau gunakan -AllowDirty untuk uji sementara). Perubahan:`n$($GitDirty -join "`n")"
    }
    if ($GitDirty -and $AllowDirty) {
        Write-Warning "Build berjalan pada working tree tidak bersih karena flag -AllowDirty diaktifkan."
    }
    $CommitShort = (& git rev-parse --short HEAD 2>$null).Trim()
    $CommitFull = (& git rev-parse HEAD 2>$null).Trim()
    if (-not $CommitShort) { $CommitShort = "dev" }
    if (-not $CommitFull) { $CommitFull = $CommitShort }
    $BuildTime = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
    $BuildStamp = (Get-Date).ToString("yyyyMMdd-HHmmss")
    $BuildId = "$CommitShort-$BuildStamp"

    $MetaJson = & $VenvPython -c "import json; from app.services.branding.constants import APP_NAME, APP_VERSION, APP_OWNER, APP_SETUP_PREFIX, UPDATE_MANIFEST_URL; print(json.dumps({'name': APP_NAME, 'version': APP_VERSION, 'owner': APP_OWNER, 'prefix': APP_SETUP_PREFIX, 'update_url': UPDATE_MANIFEST_URL}))"
    $Meta = $MetaJson | ConvertFrom-Json
    $AppName = $Meta.name
    $AppVersion = $Meta.version
    $AppOwner = $Meta.owner
    $SetupBaseName = "$($Meta.prefix)-$AppVersion"
    if ([string]::IsNullOrWhiteSpace($Meta.update_url)) {
        Write-Warning "PERINGATAN (E11.4): UPDATE_MANIFEST_URL pada app\services\branding\constants.py masih kosong (placeholder). Cek pembaruan otomatis dinonaktifkan sampai URL rilis produksi diisi."
    }

    Write-Host "App       : $AppName v$AppVersion"
    Write-Host "Build     : $BuildId (commit $CommitShort)"
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

    # Generate multi-res .ico inside build\ so tracked repo stays clean (E12.3)
    $IconPath = Join-Path $BuildRoot "app.ico"
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

    # Write installed-mode marker & embedded build metadata cleanly inside staging (E12.1, E12.3)
    "EasyFaceless Studio v$AppVersion installed package marker (build $BuildId)" | Out-File (Join-Path $StagingDir ".efs_installed") -Encoding utf8 -Force
    $BuildMetaFile = Join-Path $StagingDir "app\services\branding\_build_meta.json"
    $BuildMetaObj = @{
        git_commit = $CommitShort
        git_commit_full = $CommitFull
        build_time = $BuildTime
        build_id = $BuildId
        dirty = [bool]$GitDirty
    } | ConvertTo-Json -Depth 3
    [System.IO.File]::WriteAllText($BuildMetaFile, $BuildMetaObj + "`n", [System.Text.UTF8Encoding]::new($false))

    # Ensure packaged Streamlit never watches bundled stdlib/code modules in installed mode (E10.1)
    # Write strictly UTF-8 WITHOUT BOM so Python tomllib parses .streamlit/config.toml cleanly.
    $StagedStCfg = Join-Path $StagingDir "webui\.streamlit\config.toml"
    if (Test-Path $StagedStCfg) {
        $stContent = Get-Content $StagedStCfg -Raw -Encoding UTF8
        if ($stContent -notmatch "fileWatcherType") {
            $stContent = $stContent -replace '(\[server\]\r?\n)', "`$1fileWatcherType = `"none`"`r`n"
        }
        [System.IO.File]::WriteAllText($StagedStCfg, $stContent, [System.Text.UTF8Encoding]::new($false))
    }

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

    $PreVerifyPids = @(Get-EfsStagingProcesses -TargetDir $StagingDir | ForEach-Object { $_.ProcessId })
    & $VenvPython (Join-Path $RepoRoot "scripts\build\verify_package.py") --staging $StagingDir --smoke
    $VerifyExit = $LASTEXITCODE
    $PostVerifyProcs = @(Get-EfsStagingProcesses -TargetDir $StagingDir | Where-Object { $PreVerifyPids -notcontains $_.ProcessId })
    if ($PostVerifyProcs.Count -gt 0) {
        $LeakSummary = ($PostVerifyProcs | ForEach-Object { "$($_.Name) (PID=$($_.ProcessId)): $($_.ExecutablePath)" }) -join "; "
        throw "[PROCESS LEAK DETECTED] Build/smoke meninggalkan proses berjalan dari build/staging: $LeakSummary"
    }
    Write-Host "[PROCESS CHECK PASSED] Zero leftover build/smoke processes under $StagingDir" -ForegroundColor Green
    if ($VerifyExit -ne 0) {
        throw "Package hygiene/smoke verification failed!"
    }

    # 10. Compile Inno Setup Installer (retry up to 3x for transient AV lock on EndUpdateResource)
    Write-Host "[6/6] Compiling Inno Setup installer -> dist\$SetupBaseName.exe..."
    $IssFile = Join-Path $RepoRoot "packaging\installer\efs.iss"
    $IsccExit = 1
    for ($attempt = 1; $attempt -le 3; $attempt++) {
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
        $IsccExit = $LASTEXITCODE
        if ($IsccExit -eq 0) { break }
        if ($attempt -lt 3) {
            Write-Warning "ISCC exited with $IsccExit (attempt $attempt/3); retrying after 3s..."
            Start-Sleep -Seconds 3
        }
    }
    if ($IsccExit -ne 0) {
        throw "Inno Setup compiler failed (exit $IsccExit)!"
    }

    $InstallerExe = Join-Path $DistDir "$SetupBaseName.exe"
    $StagingBytes = (Get-ChildItem $StagingDir -Recurse -File | Measure-Object -Property Length -Sum).Sum
    $InstallerBytes = (Get-Item $InstallerExe).Length
    $StagingMB = [Math]::Round($StagingBytes / 1MB, 2)
    $InstallerMB = [Math]::Round($InstallerBytes / 1MB, 2)

    # E7: Generate SHA256 checksum & release notes from CHANGELOG.md in dist\
    $ShaObj = Get-FileHash -Path $InstallerExe -Algorithm SHA256
    $ShaHash = $ShaObj.Hash.ToLowerInvariant()
    $ShaFile = "$InstallerExe.sha256"
    "$ShaHash  $SetupBaseName.exe" | Out-File -FilePath $ShaFile -Encoding ascii -Force

    # R1.5: Member-friendly release notes (Bahasa Indonesia first, then English, with SHA256 & build hash)
    $ReleaseNotesOut = Join-Path $DistDir "RELEASE-NOTES-$AppVersion.md"
    $ReleaseNotesTemplate = @'
# EasyFaceless Studio v__APP_VERSION__ — Catatan Rilis & Panduan Member

Halo member! Berikut rilis resmi **EasyFaceless Studio v__APP_VERSION__** untuk Windows 64-bit.

- **Berkas Installer:** `__SETUP_BASE__.exe`
- **Build Commit (`HEAD`):** `__COMMIT_SHORT__` (`__COMMIT_FULL__`)
- **Waktu Build:** `__BUILD_TIME__` (`Build ID: __BUILD_ID__`)
- **SHA-256:** `__SHA256__`

---

## Bahasa Indonesia

### Fitur Utama v__APP_VERSION__
- **Studio Video Panjang Dokumenter (10–20 menit):** Pengaturan proyek terisolasi, pembuatan/impor naskah, sintesis suara (**Edge TTS gratis** atau penyedia lain), visual otomatis, render MP4 1080p, hingga ekspor draft langsung ke **CapCut Desktop**.
- **Sumber Footage Gratis Tanpa API Key:** Dukungan bawaan **Wikimedia Commons**, **NASA Images**, dan **Openverse Photos** (filter lisensi komersial aman + catatan atribusi otomatis di `export/credits.md`), serta opsi Pixabay/Coverr dan AI Visual.
- **Wizard Pertama Kali & Diagnostik PC:** Menyiapkan bahasa, mengecek kesiapan RAM/CPU/disk/FFmpeg/CapCut, konfigurasi OpenRouter/API, dan membuat proyek contoh.
- **Cek Pembaruan & Pengalaman Desktop:** Peluncur tanpa jendela konsol, banner/badge pembaruan, penggantian server otomatis saat upgrade, serta data proyek tetap aman saat instal ulang.

### Kebutuhan Sistem (Terukur Benchmark)
- **OS:** Windows 10 / Windows 11 (64-bit)
- **RAM:** Minimum **8 GB** (disarankan **16 GB** bila menyunting bersama CapCut Desktop)
- **CPU:** Minimum **4 core / 4 thread** (disarankan **6–8+ thread**)
- **Ruang Disk Kosong:** Minimum **15 GB** (disarankan **20 GB+ SSD** untuk folder proyek & cache visual)
- **FFmpeg:** Sudah dibundel otomatis di dalam installer (tidak perlu instal terpisah)
- **CapCut Desktop:** Teruji pada versi **8.9.0**

### Langkah Unduh & Instalasi
1. Unduh `__SETUP_BASE__.exe` lewat tautan **Google Drive** pada deskripsi video ini. Jika Google Drive menampilkan layar *"tidak dapat memindai file untuk mencari virus"*, klik **Tetap download / Download anyway**.
2. *(Disarankan)* Verifikasi keutuhan installer di PowerShell:
   ```powershell
   Get-FileHash .\__SETUP_BASE__.exe -Algorithm SHA256
   ```
   Pastikan nilainya sama persis dengan SHA-256 di atas (`__SHA256__`).
3. Jalankan installer (tidak perlu Administrator). Jika muncul Windows SmartScreen (*Windows protected your PC*), klik **More info → Run anyway**.
4. Buka **EasyFaceless Studio** dari Start Menu atau Desktop dan ikuti **Wizard Pertama Kali**.

### Panduan & Bantuan
- Panduan lengkap berbahasa Indonesia tersedia di menu **Bantuan** di dalam aplikasi (`docs/user/id/`).

### Cara Melapor Masalah
- Sertakan screenshot error, versi & build di menu **Tentang** (`v__APP_VERSION__ · build __COMMIT_SHORT__`), serta berkas log di `%LOCALAPPDATA%\EasyFaceless Studio\logs\` (`launcher.log` dan `server.log`).

---

## English

### Key Highlights in v__APP_VERSION__
- **Longform Documentary Studio (10–20 min):** Structured project workflow with script generation/import, free **Edge TTS** or optional cloud voices, tiered stock visual matching, 1080p MP4 render, and direct **CapCut Desktop** timeline export.
- **Keyless Free Footage Sources:** Built-in **Wikimedia Commons**, **NASA Images**, and **Openverse Photos** with strict commercial-license filtering and automatic attribution export (`export/credits.md`), plus optional Pixabay/Coverr and AI visuals.
- **First-Run Setup Wizard & Desktop Polish:** Console-free launcher, automatic system check, update alerts, seamless server replacement on upgrade, and safe data preservation across upgrades.

### System Requirements (Benchmark-Verified)
- **OS:** Windows 10 / Windows 11 (64-bit)
- **RAM:** Minimum **8 GB** (**16 GB** recommended when editing alongside CapCut Desktop)
- **CPU:** Minimum **4 cores / 4 threads** (**6–8+ threads** recommended)
- **Free Disk Space:** Minimum **15 GB** (**20 GB+ SSD** recommended for project assets & cache)
- **FFmpeg:** Bundled automatically inside the installer
- **CapCut Desktop:** Verified on **v8.9.0**

### Installation Steps
1. Download `__SETUP_BASE__.exe` from the **Google Drive** link in this members-only post (click **Download anyway** if Google Drive warns about file size).
2. Verify SHA-256 in PowerShell (`Get-FileHash .\__SETUP_BASE__.exe -Algorithm SHA256`) against `__SHA256__`.
3. Run Setup (no admin needed; click **More info → Run anyway** on Windows SmartScreen) and complete the **First-Run Setup Wizard**.

### Documentation & Issue Reporting
- Open the **Help** menu inside the app (`docs/user/en/`) for quick start and troubleshooting guides.
- Report issues with screenshots, build badge (`v__APP_VERSION__ · build __COMMIT_SHORT__`), and log files from `%LOCALAPPDATA%\EasyFaceless Studio\logs\` (`launcher.log`, `server.log`).
'@
    $ReleaseNotesRendered = $ReleaseNotesTemplate.
        Replace("__APP_VERSION__", $AppVersion).
        Replace("__SETUP_BASE__", $SetupBaseName).
        Replace("__COMMIT_SHORT__", $CommitShort).
        Replace("__COMMIT_FULL__", $CommitFull).
        Replace("__BUILD_TIME__", $BuildTime).
        Replace("__BUILD_ID__", $BuildId).
        Replace("__SHA256__", $ShaHash)
    [System.IO.File]::WriteAllText($ReleaseNotesOut, $ReleaseNotesRendered + "`r`n", [System.Text.UTF8Encoding]::new($false))

    Write-Host ""
    Write-Host "=== BUILD SUCCEEDED ===" -ForegroundColor Green
    Write-Host "Installer   : $InstallerExe ($InstallerMB MB / $InstallerBytes bytes)"
    Write-Host "BuildCommit : $CommitShort ($CommitFull)"
    Write-Host "Build ID    : $BuildId ($BuildTime)"
    Write-Host "SHA256      : $ShaFile ($ShaHash)"
    if (Test-Path $ReleaseNotesOut) {
        Write-Host "ReleaseNote : $ReleaseNotesOut"
    }
    Write-Host "Staged Size : $StagingMB MB ($StagingBytes bytes)"
    Write-Host "Top-level items in package:"
    Get-ChildItem $StagingDir | Select-Object Name, Mode | Format-Table -AutoSize
}
finally {
    Pop-Location
}
