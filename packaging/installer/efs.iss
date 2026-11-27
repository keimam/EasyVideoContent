; Inno Setup 6 Installer Script for EasyFaceless Studio (Stage 8C.2, D4, D9.6)
; Build via: scripts/build/build_installer.ps1

#ifndef MyAppName
  #define MyAppName "EasyFaceless Studio"
#endif
#ifndef MyAppVersion
  #define MyAppVersion "1.0.2"
#endif
#ifndef MyAppPublisher
  #define MyAppPublisher "Naseh Ngulik"
#endif
#ifndef MyAppExeName
  #define MyAppExeName "EasyFaceless Studio.exe"
#endif
#ifndef MyAppSetupBase
  #define MyAppSetupBase "EasyFacelessStudio-Setup-1.0.2"
#endif
#ifndef MyStagingDir
  #define MyStagingDir "..\..\build\staging\EasyFaceless Studio"
#endif
#ifndef MyOutputDir
  #define MyOutputDir "..\..\dist"
#endif
#ifndef MyIconFile
  #define MyIconFile "..\..\resource\branding\app.ico"
#endif
#ifndef MyLicenseFile
  #define MyLicenseFile "..\..\LICENSE"
#endif
#ifndef MyUserDataFolderName
  #define MyUserDataFolderName "EasyFaceless Studio"
#endif

[Setup]
AppId={{8C2E6F4D-9B1A-4E83-A412-EA54F0CE1E55}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} v{#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
; Strictly non-elevated per-user installation so child processes never run elevated (D9.6)
PrivilegesRequired=lowest
AppMutex=EasyFacelessStudioRunningMutex
CloseApplications=yes
CloseApplicationsFilter=*.exe,*.dll,*.pyd
RestartApplications=no
LicenseFile={#MyLicenseFile}
SetupIconFile={#MyIconFile}
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName} v{#MyAppVersion}
OutputDir={#MyOutputDir}
OutputBaseFilename={#MyAppSetupBase}
Compression=lzma2/fast
SolidCompression=yes
WizardStyle=modern
ShowLanguageDialog=auto
DisableDirPage=no
AllowNoIcons=yes

[Languages]
Name: "indonesian"; MessagesFile: "compiler:Default.isl,Indonesian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[CustomMessages]
english.AdditionalIcons=Additional shortcuts:
english.CreateDesktopIcon=Create a &Desktop shortcut
english.LaunchProgram=Launch %1
english.UninstallRemoveDataPrompt=Do you also want to remove your user data folder (%1)?%n%nThis contains config.toml, video projects, custom presets, cache, and logs.%nChoose "No" (recommended) to keep your projects and settings.
english.AppRunningClosePrompt=EasyFaceless Studio is currently running (%1 process(es)).%n%nClose EasyFaceless Studio cleanly before continuing?
english.AppJobsRunningWarning=WARNING: %1 background production job(s) are currently running in EasyFaceless Studio!%n%nContinuing will stop those running job(s) and close EasyFaceless Studio cleanly. Do you want to continue?
english.AppCloseFailedMsg=Could not automatically close all running EasyFaceless Studio processes. Please close EasyFaceless Studio using "Exit App" and try again.

indonesian.UninstallRemoveDataPrompt=Apakah Anda juga ingin menghapus folder data pengguna (%1)?%n%nFolder ini berisi config.toml, proyek video, preset kustom, cache, dan log.%nPilih "No / Tidak" (disarankan) agar proyek & pengaturan Anda tetap aman.
indonesian.AppRunningClosePrompt=EasyFaceless Studio sedang berjalan (%1 proses).%n%nTutup aplikasi dengan bersih sebelum melanjutkan?
indonesian.AppJobsRunningWarning=PERINGATAN: Masih ada %1 tugas produksi yang sedang berjalan di EasyFaceless Studio!%n%nMelanjutkan akan menghentikan tugas tersebut dan menutup EasyFaceless Studio dengan bersih. Lanjutkan sekarang?
indonesian.AppCloseFailedMsg=Tidak dapat menutup seluruh proses EasyFaceless Studio yang masih berjalan. Silakan tutup lewat tombol "Tutup aplikasi" di sidebar lalu coba lagi.

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#MyStagingDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[UninstallDelete]
Type: filesandordirs; Name: "{app}\app"
Type: filesandordirs; Name: "{app}\webui"
Type: filesandordirs; Name: "{app}\runtime"
Type: filesandordirs; Name: "{app}\config"
Type: filesandordirs; Name: "{app}\docs"
Type: filesandordirs; Name: "{app}\licenses"
Type: filesandordirs; Name: "{app}\resource"

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
; Always launch as original non-elevated user (D9.6)
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent runasoriginaluser

[Code]
function EscapePsSingleQuote(const S: string): string;
begin
  Result := S;
  StringChangeEx(Result, '''', '''''', True);
end;

function RunProcessHelper(const TargetDir, OutStatusFile: string; const DoShutdown: Boolean; var ProcsFound, JobsRunning: Integer): Boolean;
var
  ScriptPath, PsExe, Args, LockPath, ProjectsPath, ShutdownFlag: string;
  ScriptLines: TArrayOfString;
  ResultCode: Integer;
  StatusLines: TArrayOfString;
  I: Integer;
  Line, Key, Val: string;
begin
  Result := False;
  ProcsFound := 0;
  JobsRunning := 0;

  ScriptPath := ExpandConstant('{tmp}\efs_proc_mgr.ps1');
  LockPath := ExpandConstant('{localappdata}\{#MyUserDataFolderName}\.efs_launcher.lock.json');
  ProjectsPath := ExpandConstant('{localappdata}\{#MyUserDataFolderName}\storage\longform_projects');
  if DoShutdown then ShutdownFlag := '1' else ShutdownFlag := '0';

  SetArrayLength(ScriptLines, 66);
  ScriptLines[0]  := '$targetDir = ''' + EscapePsSingleQuote(TargetDir) + '''';
  ScriptLines[1]  := '$lockPath = ''' + EscapePsSingleQuote(LockPath) + '''';
  ScriptLines[2]  := '$projectsPath = ''' + EscapePsSingleQuote(ProjectsPath) + '''';
  ScriptLines[3]  := '$outFile = ''' + EscapePsSingleQuote(OutStatusFile) + '''';
  ScriptLines[4]  := '$doStop = ''' + ShutdownFlag + ''' -eq ''1''';
  ScriptLines[5]  := '$normTarget = if ($targetDir) { $targetDir.TrimEnd(''\'', ''/'').ToLowerInvariant() } else { '''' }';
  ScriptLines[6]  := 'try { $fullTarget = if ($targetDir) { [System.IO.Path]::GetFullPath($targetDir).TrimEnd(''\'', ''/'').ToLowerInvariant() } else { '''' } } catch { $fullTarget = $normTarget }';
  ScriptLines[7]  := 'function Get-EfsProcs {';
  ScriptLines[8]  := '  $list = @()';
  ScriptLines[9]  := '  try {';
  ScriptLines[10] := '    $all = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue';
  ScriptLines[11] := '    foreach ($p in $all) {';
  ScriptLines[12] := '      if ([int]$p.ProcessId -eq $PID) { continue }';
  ScriptLines[13] := '      $ep = if ($p.ExecutablePath) { $p.ExecutablePath.ToLowerInvariant() } else { '''' }';
  ScriptLines[14] := '      $cmd = if ($p.CommandLine) { $p.CommandLine.ToLowerInvariant() } else { '''' }';
  ScriptLines[15] := '      $nm = if ($p.Name) { $p.Name.ToLowerInvariant() } else { '''' }';
  ScriptLines[16] := '      $inDir = ($normTarget -and $ep -and ($ep.StartsWith($normTarget + ''\'') -or $ep.StartsWith($normTarget + ''/'') -or ($fullTarget -and ($ep.StartsWith($fullTarget + ''\'') -or $ep.StartsWith($fullTarget + ''/'')))))';
  ScriptLines[17] := '      $isEfsPy = ($nm -like ''python*'') -and ($cmd -like ''*app.services.longform.launcher_runtime*'' -or ($cmd -like ''*streamlit*'' -and ($cmd -like ''*webui*main.py*'' -or $cmd -like ''*easyfaceless*'')))';
  ScriptLines[18] := '      if ($inDir -or $isEfsPy) { $list += $p }';
  ScriptLines[19] := '    }';
  ScriptLines[20] := '  } catch {}';
  ScriptLines[21] := '  return ,$list';
  ScriptLines[22] := '}';
  ScriptLines[23] := 'function Get-RunningJobsCount {';
  ScriptLines[24] := '  $cnt = 0';
  ScriptLines[25] := '  if (Test-Path $projectsPath) {';
  ScriptLines[26] := '    Get-ChildItem -Path $projectsPath -Directory -ErrorAction SilentlyContinue | ForEach-Object {';
  ScriptLines[27] := '      $jf = Join-Path $_.FullName ''job_state.json''';
  ScriptLines[28] := '      if (Test-Path $jf) {';
  ScriptLines[29] := '        try {';
  ScriptLines[30] := '          $jd = Get-Content -Path $jf -Raw -Encoding UTF8 | ConvertFrom-Json';
  ScriptLines[31] := '          if ($jd.status -eq ''running'' -and $jd.pid) {';
  ScriptLines[32] := '            $alive = Get-Process -Id ([int]$jd.pid) -ErrorAction SilentlyContinue';
  ScriptLines[33] := '            if ($alive) { $cnt++ }';
  ScriptLines[34] := '          }';
  ScriptLines[35] := '        } catch {}';
  ScriptLines[36] := '      }';
  ScriptLines[37] := '    }';
  ScriptLines[38] := '  }';
  ScriptLines[39] := '  return $cnt';
  ScriptLines[40] := '}';
  ScriptLines[41] := '$procs = Get-EfsProcs';
  ScriptLines[42] := '$jobs = Get-RunningJobsCount';
  ScriptLines[43] := 'if ($doStop) {';
  ScriptLines[44] := '  $deadline = (Get-Date).AddSeconds(12)';
  ScriptLines[45] := '  while ($procs.Count -gt 0 -and (Get-Date) -lt $deadline) {';
  ScriptLines[46] := '    foreach ($p in $procs) {';
  ScriptLines[47] := '      $id = [int]$p.ProcessId';
  ScriptLines[48] := '      try { Stop-Process -Id $id -Force -ErrorAction SilentlyContinue } catch {}';
  ScriptLines[49] := '      try { & taskkill.exe /F /T /PID $id 2>$null | Out-Null } catch {}';
  ScriptLines[50] := '    }';
  ScriptLines[51] := '    Start-Sleep -Milliseconds 350';
  ScriptLines[52] := '    $procs = Get-EfsProcs';
  ScriptLines[53] := '  }';
  ScriptLines[54] := '  if (Test-Path $lockPath) { Remove-Item -Path $lockPath -Force -ErrorAction SilentlyContinue }';
  ScriptLines[55] := '  $envData = [Environment]::GetEnvironmentVariable(''EFS_DATA_DIR'')';
  ScriptLines[56] := '  if ($envData) {';
  ScriptLines[57] := '    $envLock = Join-Path $envData ''.efs_launcher.lock.json''';
  ScriptLines[58] := '    if (Test-Path $envLock) { Remove-Item -Path $envLock -Force -ErrorAction SilentlyContinue }';
  ScriptLines[59] := '  }';
  ScriptLines[60] := '  $jobs = Get-RunningJobsCount';
  ScriptLines[61] := '}';
  ScriptLines[62] := '$lines = @("PROCS=$($procs.Count)", "JOBS=$jobs")';
  ScriptLines[63] := 'Set-Content -Path $outFile -Value $lines -Encoding Ascii';
  ScriptLines[64] := 'exit 0';
  ScriptLines[65] := '# end EFS proc mgr';

  if not SaveStringsToUTF8File(ScriptPath, ScriptLines, False) then
    Exit;

  PsExe := ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe');
  if not FileExists(PsExe) then
    PsExe := 'powershell.exe';
  Args := '-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "' + ScriptPath + '"';
  if not Exec(PsExe, Args, ExpandConstant('{tmp}'), SW_HIDE, ewWaitUntilTerminated, ResultCode) then
    Exit;

  if FileExists(OutStatusFile) and LoadStringsFromFile(OutStatusFile, StatusLines) then
  begin
    for I := 0 to GetArrayLength(StatusLines) - 1 do
    begin
      Line := Trim(StatusLines[I]);
      if Pos('=', Line) > 0 then
      begin
        Key := Copy(Line, 1, Pos('=', Line) - 1);
        Val := Copy(Line, Pos('=', Line) + 1, Length(Line));
        if Key = 'PROCS' then ProcsFound := StrToIntDef(Val, 0);
        if Key = 'JOBS' then JobsRunning := StrToIntDef(Val, 0);
      end;
    end;
    Result := True;
  end;
end;

function HandleRunningAppBeforeFileOp(const TargetDir: string; const IsSilent: Boolean): Boolean;
var
  StatusFile, PyExe, Msg: string;
  ProcsFound, JobsRunning, DummyRes: Integer;
  ConfirmRes: Integer;
begin
  Result := True;

  StatusFile := ExpandConstant('{tmp}\efs_proc_status.txt');
  if not RunProcessHelper(TargetDir, StatusFile, False, ProcsFound, JobsRunning) then
    Exit;

  if (ProcsFound <= 0) and (JobsRunning <= 0) then
    Exit;

  if not IsSilent then
  begin
    if JobsRunning > 0 then
      Msg := FmtMessage(CustomMessage('AppJobsRunningWarning'), [IntToStr(JobsRunning)])
    else
      Msg := FmtMessage(CustomMessage('AppRunningClosePrompt'), [IntToStr(ProcsFound)]);

    ConfirmRes := MsgBox(Msg, mbConfirmation, MB_YESNO or MB_DEFBUTTON2);
    if ConfirmRes <> IDYES then
    begin
      Result := False;
      Exit;
    end;
  end;

  // Attempt graceful launcher_runtime --shutdown from TargetDir when present (D9.6, E12.2)
  if TargetDir <> '' then
  begin
    PyExe := AddBackslash(TargetDir) + 'runtime\python\python.exe';
    if FileExists(PyExe) then
      Exec(PyExe, '-m app.services.longform.launcher_runtime --shutdown --install-root "' + TargetDir + '"', TargetDir, SW_HIDE, ewWaitUntilTerminated, DummyRes);
  end;

  RunProcessHelper(TargetDir, StatusFile, True, ProcsFound, JobsRunning);
  if ProcsFound > 0 then
  begin
    if not IsSilent then
      MsgBox(CustomMessage('AppCloseFailedMsg'), mbError, MB_OK);
    Result := False;
  end;
end;

function GetPreviousInstallDir(): string;
var
  UninstKey, Loc: string;
begin
  Result := '';
  UninstKey := 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{8C2E6F4D-9B1A-4E83-A412-EA54F0CE1E55}_is1';
  if RegQueryStringValue(HKCU, UninstKey, 'InstallLocation', Loc) and (Loc <> '') then
    Result := Loc
  else
    Result := ExpandConstant('{localappdata}\Programs\{#MyAppName}');
end;

function InitializeSetup(): Boolean;
begin
  // Run BEFORE Inno AppMutex check so active server/mutex can be closed cleanly (E12.2)
  Result := HandleRunningAppBeforeFileOp(GetPreviousInstallDir(), WizardSilent());
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if CurPageID = wpReady then
    Result := HandleRunningAppBeforeFileOp(WizardDirValue(), WizardSilent());
end;

function PrepareToInstall(var NeedsRestart: Boolean): string;
begin
  Result := '';
  if not HandleRunningAppBeforeFileOp(WizardDirValue(), True) then
    Result := CustomMessage('AppCloseFailedMsg');
end;

function InitializeUninstall(): Boolean;
begin
  Result := HandleRunningAppBeforeFileOp(ExpandConstant('{app}'), UninstallSilent());
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  UserDataDir: string;
  PromptMsg: string;
  Res: Integer;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    UserDataDir := ExpandConstant('{localappdata}\{#MyUserDataFolderName}');
    if DirExists(UserDataDir) then
    begin
      if UninstallSilent() then
      begin
        // Preserve user data by default during silent/automated uninstall
        Exit;
      end;
      PromptMsg := FmtMessage(CustomMessage('UninstallRemoveDataPrompt'), [UserDataDir]);
      Res := MsgBox(PromptMsg, mbConfirmation, MB_YESNO or MB_DEFBUTTON2);
      if Res = IDYES then
      begin
        DelTree(UserDataDir, True, True, True);
      end;
    end;
  end;
end;
