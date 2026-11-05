# scripts/longform/baseline.ps1
# Script to measure baseline generation metrics for a ~60s short video using upstream MPT pipeline.
# Includes recursive process tree RAM tracking (parent, children, grandchildren).

[CmdletBinding()]
param(
    [string]$VoiceName = "en-US-ChristopherNeural-Male"
)

$ErrorActionPreference = "Stop"

# Ensure we are in repo root
$RepoRoot = (Resolve-Path "$PSScriptRoot\..\..").Path
Set-Location $RepoRoot

$PythonExe = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $PythonExe)) {
    Write-Error "Virtual environment Python not found at: $PythonExe. Run 'uv sync --frozen' first."
    exit 1
}

$ScriptText = "Throughout ancient history, human beings have constantly searched for meaning and purpose in a chaotic world. From the philosophers of ancient Athens walking through the olive groves, to the stoic thinkers of imperial Rome facing political turmoil, the fundamental psychological questions have remained surprisingly unchanged. We wonder why we experience fear, how we can cultivate resilience, and what it truly means to live a meaningful life. Modern psychology often discovers principles that ancient traditions recognized thousands of years ago. When we study the past, we are not simply memorizing dusty dates and distant battles. Instead, we are looking directly into a mirror of our own shared human nature. By understanding how historical figures struggled with their deepest anxieties, doubts, and ambitions, we unlock practical wisdom for our modern lives. The lessons of history remind us that we are never truly alone in our journey."

$Materials = "test/resources/1.png,test/resources/2.png,test/resources/3.png,test/resources/4.png,test/resources/5.png,test/resources/6.png,test/resources/7.png,test/resources/8.png,test/resources/9.png"

Write-Host "=================================================================" -ForegroundColor Cyan
Write-Host " Running Baseline Benchmark (Upstream Pipeline ~60s Short Video) " -ForegroundColor Cyan
Write-Host "=================================================================" -ForegroundColor Cyan
Write-Host "Repo Root: $RepoRoot"
Write-Host "Voice: $VoiceName"
Write-Host "Aspect: 16:9 | BGM: none | Subtitles: enabled"
Write-Host ""

$CliArgs = @(
    "cli.py",
    "--video-subject", "Ancient Wisdom and Modern Psychology",
    "--video-script", $ScriptText,
    "--voice-name", $VoiceName,
    "--video-source", "local",
    "--video-materials", $Materials,
    "--video-aspect", "16:9",
    "--bgm-type", "none",
    "--subtitle-enabled",
    "--stop-at", "video"
)

$StartTime = [System.Diagnostics.Stopwatch]::StartNew()

$psi = New-Object System.Diagnostics.ProcessStartInfo
$psi.FileName = $PythonExe
$psi.Arguments = [string]::Join(" ", ($CliArgs | ForEach-Object {
    if ($_ -match "[\s`",]") {
        "`"" + ($_ -replace '"', '\"') + "`""
    } else {
        $_
    }
}))
$psi.WorkingDirectory = $RepoRoot
$psi.UseShellExecute = $false
$psi.RedirectStandardOutput = $true
$psi.RedirectStandardError = $true

$proc = New-Object System.Diagnostics.Process
$proc.StartInfo = $psi

$stdOutBuilder = New-Object System.Text.StringBuilder
$stdErrBuilder = New-Object System.Text.StringBuilder

$stdOutEvent = Register-ObjectEvent -InputObject $proc -EventName OutputDataReceived -Action {
    if ($EventArgs.Data) {
        [void]$stdOutBuilder.AppendLine($EventArgs.Data)
    }
}
$stdErrEvent = Register-ObjectEvent -InputObject $proc -EventName ErrorDataReceived -Action {
    if ($EventArgs.Data) {
        [void]$stdErrBuilder.AppendLine($EventArgs.Data)
        Write-Host $EventArgs.Data -ForegroundColor DarkGray
    }
}

[void]$proc.Start()
$proc.BeginOutputReadLine()
$proc.BeginErrorReadLine()

$PeakRamBytes = 0
$ParentPid = $proc.Id
$RecordedProcessNames = New-Object System.Collections.Generic.HashSet[string]
[void]$RecordedProcessNames.Add("python.exe")

function Get-ProcessTreeStats {
    param([int]$RootPid)
    
    $allProcesses = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue
    if (-not $allProcesses) { 
        return @{ TotalBytes = 0; ProcessNames = @() } 
    }
    
    $pidsToScan = New-Object System.Collections.Generic.Queue[int]
    $pidsToScan.Enqueue($RootPid)
    $collectedPids = New-Object System.Collections.Generic.HashSet[int]
    [void]$collectedPids.Add($RootPid)
    
    $totalWorkingSet = 0
    $processNames = New-Object System.Collections.Generic.HashSet[string]
    
    $childrenMap = @{}
    foreach ($p in $allProcesses) {
        $ppid = [int]$p.ParentProcessId
        if (-not $childrenMap.ContainsKey($ppid)) {
            $childrenMap[$ppid] = New-Object System.Collections.Generic.List[object]
        }
        $childrenMap[$ppid].Add($p)
    }
    
    while ($pidsToScan.Count -gt 0) {
        $curPid = $pidsToScan.Dequeue()
        $matching = $allProcesses | Where-Object { $_.ProcessId -eq $curPid }
        if ($matching) {
            $totalWorkingSet += [int64]$matching.WorkingSetSize
            [void]$processNames.Add($matching.Name)
        }
        if ($childrenMap.ContainsKey($curPid)) {
            foreach ($child in $childrenMap[$curPid]) {
                $cid = [int]$child.ProcessId
                if (-not $collectedPids.Contains($cid)) {
                    [void]$collectedPids.Add($cid)
                    $pidsToScan.Enqueue($cid)
                }
            }
        }
    }
    
    return @{
        TotalBytes = $totalWorkingSet
        ProcessNames = [string[]]$processNames
    }
}

while (-not $proc.HasExited) {
    try {
        $treeStats = Get-ProcessTreeStats -RootPid $ParentPid
        if ($treeStats.TotalBytes -gt $PeakRamBytes) {
            $PeakRamBytes = $treeStats.TotalBytes
        }
        foreach ($name in $treeStats.ProcessNames) {
            [void]$RecordedProcessNames.Add($name)
        }
    } catch {
        # ignore transient query errors
    }
    Start-Sleep -Milliseconds 200
}

$proc.WaitForExit()
$StartTime.Stop()
$TotalElapsedSeconds = $StartTime.Elapsed.TotalSeconds

# Clean up event handlers
Unregister-Event -SourceIdentifier $stdOutEvent.Name -ErrorAction SilentlyContinue
Unregister-Event -SourceIdentifier $stdErrEvent.Name -ErrorAction SilentlyContinue

$StdoutText = $stdOutBuilder.ToString().Trim()
$StderrText = $stdErrBuilder.ToString()

if ($proc.ExitCode -ne 0) {
    Write-Error "Baseline generation failed with exit code $($proc.ExitCode)"
    exit $proc.ExitCode
}

# Parse JSON result from stdout
$FinalVideoPath = $null
try {
    $jsonObj = $StdoutText | ConvertFrom-Json
    if ($jsonObj.result -and $jsonObj.result.videos) {
        $FinalVideoPath = $jsonObj.result.videos[0]
    }
} catch {
    Write-Warning "Could not parse JSON output directly: $_"
}

if (-not $FinalVideoPath -or -not (Test-Path $FinalVideoPath)) {
    # Fallback to finding latest final-1.mp4 in storage/tasks/
    $candidate = Get-ChildItem -Path "$RepoRoot\storage\tasks\*\final-1.mp4" | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($candidate) {
        $FinalVideoPath = $candidate.FullName
    }
}

# Parse stages timing from stderr logs
$audioStart = [regex]::Match($StderrText, '(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*generate_audio -')
$subStart = [regex]::Match($StderrText, '(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*generating subtitle')
$matStart = [regex]::Match($StderrText, '(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*preprocess local materials')
$combStart = [regex]::Match($StderrText, '(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*combining video:')
$renderStart = [regex]::Match($StderrText, '(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*generating video: 1920 x 1080')
$taskFinished = [regex]::Match($StderrText, '(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*_run_pipeline - task .* finished')

Write-Host ""
Write-Host "=================================================================" -ForegroundColor Green
Write-Host "                    BASELINE BENCHMARK RESULTS                   " -ForegroundColor Green
Write-Host "=================================================================" -ForegroundColor Green
Write-Host ("Total Wall-Clock Time : {0:N2} s ({1:N2} min)" -f $TotalElapsedSeconds, ($TotalElapsedSeconds / 60))
Write-Host ("Peak RAM (Process Tree): {0:N2} MB" -f ($PeakRamBytes / 1MB))
Write-Host ("Process Tree Recorded : {0}" -f ([string]::Join(", ", $RecordedProcessNames)))

if ($renderStart.Success -and $taskFinished.Success) {
    $t1 = [datetime]::ParseExact($renderStart.Groups[1].Value, "yyyy-MM-dd HH:mm:ss", $null)
    $t2 = [datetime]::ParseExact($taskFinished.Groups[1].Value, "yyyy-MM-dd HH:mm:ss", $null)
    $renderSecs = ($t2 - $t1).TotalSeconds
    Write-Host ("MoviePy Render Time   : {0:N2} s ({1:N2} min)" -f $renderSecs, ($renderSecs / 60))
}

if ($combStart.Success -and $renderStart.Success) {
    $t1 = [datetime]::ParseExact($combStart.Groups[1].Value, "yyyy-MM-dd HH:mm:ss", $null)
    $t2 = [datetime]::ParseExact($renderStart.Groups[1].Value, "yyyy-MM-dd HH:mm:ss", $null)
    $combSecs = ($t2 - $t1).TotalSeconds
    Write-Host ("Combine Videos Time   : {0:N2} s" -f $combSecs)
}

if ($matStart.Success -and $combStart.Success) {
    $t1 = [datetime]::ParseExact($matStart.Groups[1].Value, "yyyy-MM-dd HH:mm:ss", $null)
    $t2 = [datetime]::ParseExact($combStart.Groups[1].Value, "yyyy-MM-dd HH:mm:ss", $null)
    $matSecs = ($t2 - $t1).TotalSeconds
    Write-Host ("Materials Prep Time   : {0:N2} s" -f $matSecs)
}

if ($FinalVideoPath -and (Test-Path $FinalVideoPath)) {
    Write-Host ""
    Write-Host "Output Video File     : $FinalVideoPath" -ForegroundColor Yellow
    $fileItem = Get-Item $FinalVideoPath
    Write-Host ("File Size             : {0:N2} MB ({1} bytes)" -f ($fileItem.Length / 1MB), $fileItem.Length)
    
    # ffprobe probe
    Write-Host ""
    Write-Host "ffprobe Video Details:" -ForegroundColor Cyan
    & ffprobe -v error -show_entries format=duration,size,bit_rate -show_entries stream=codec_name,width,height,r_frame_rate -of default=noprint_wrappers=1 "$FinalVideoPath"
} else {
    Write-Warning "Final video path could not be located."
}

Write-Host "=================================================================" -ForegroundColor Green
