; Inno Setup 6 Installer Script for EasyFaceless Studio (Stage 8C.2, D4, D9.6)
; Build via: scripts/build/build_installer.ps1

#ifndef MyAppName
  #define MyAppName "EasyFaceless Studio"
#endif
#ifndef MyAppVersion
  #define MyAppVersion "1.0.0"
#endif
#ifndef MyAppPublisher
  #define MyAppPublisher "Naseh Ngulik"
#endif
#ifndef MyAppExeName
  #define MyAppExeName "EasyFaceless Studio.exe"
#endif
#ifndef MyAppSetupBase
  #define MyAppSetupBase "EasyFacelessStudio-Setup-1.0.0"
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
  if not DirExists(TargetDir) then
  begin
    Result := True;
    Exit;
  end;

  ScriptPath := ExpandConstant('{tmp}\efs_proc_mgr.ps1');
  LockPath := ExpandConstant('{localappdata}\{#MyUserDataFolderName}\.efs_launcher.lock.json');
  ProjectsPath := ExpandConstant('{localappdata}\{#MyUserDataFolderName}\storage\longform_projects');
  if DoShutdown then ShutdownFlag := '1' else ShutdownFlag := '0';

  SetArrayLength(ScriptLines, 53);
  ScriptLines[0]  := '$targetDir = ''' + EscapePsSingleQuote(TargetDir) + '''';
  ScriptLines[1]  := '$lockPath = ''' + EscapePsSingleQuote(LockPath) + '''';
  ScriptLines[2]  := '$projectsPath = ''' + EscapePsSingleQuote(ProjectsPath) + '''';
  ScriptLines[3]  := '$outFile = ''' + EscapePsSingleQuote(OutStatusFile) + '''';
  ScriptLines[4]  := '$doStop = ''' + ShutdownFlag + ''' -eq ''1''';
  ScriptLines[5]  := '$normTarget = $targetDir.TrimEnd(''\'', ''/'').ToLowerInvariant()';
  ScriptLines[6]  := 'function Get-EfsProcs {';
  ScriptLines[7]  := '  $list = @()';
  ScriptLines[8]  := '  try {';
  ScriptLines[9]  := '    $all = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue';
  ScriptLines[10] := '    foreach ($p in $all) {';
  ScriptLines[11] := '      if (-not $p.ExecutablePath) { continue }';
  ScriptLines[12] := '      $ep = $p.ExecutablePath.ToLowerInvariant()';
  ScriptLines[13] := '      if ($ep.StartsWith($normTarget + ''\'') -or $ep.StartsWith($normTarget + ''/'')) { $list += $p }';
  ScriptLines[14] := '    }';
  ScriptLines[15] := '  } catch {}';
  ScriptLines[16] := '  return ,$list';
  ScriptLines[17] := '}';
  ScriptLines[18] := 'function Get-RunningJobsCount {';
  ScriptLines[19] := '  $cnt = 0';
  ScriptLines[20] := '  if (Test-Path $projectsPath) {';
  ScriptLines[21] := '    Get-ChildItem -Path $projectsPath -Directory -ErrorAction SilentlyContinue | ForEach-Object {';
  ScriptLines[22] := '      $jf = Join-Path $_.FullName ''job_state.json''';
  ScriptLines[23] := '      if (Test-Path $jf) {';
  ScriptLines[24] := '        try {';
  ScriptLines[25] := '          $jd = Get-Content -Path $jf -Raw -Encoding UTF8 | ConvertFrom-Json';
  ScriptLines[26] := '          if ($jd.status -eq ''running'' -and $jd.pid) {';
  ScriptLines[27] := '            $alive = Get-Process -Id ([int]$jd.pid) -ErrorAction SilentlyContinue';
  ScriptLines[28] := '            if ($alive) { $cnt++ }';
  ScriptLines[29] := '          }';
  ScriptLines[30] := '        } catch {}';
  ScriptLines[31] := '      }';
  ScriptLines[32] := '    }';
  ScriptLines[33] := '  }';
  ScriptLines[34] := '  return $cnt';
  ScriptLines[35] := '}';
  ScriptLines[36] := '$procs = Get-EfsProcs';
  ScriptLines[37] := '$jobs = Get-RunningJobsCount';
  ScriptLines[38] := 'if ($doStop) {';
  ScriptLines[39] := '  $deadline = (Get-Date).AddSeconds(10)';
  ScriptLines[40] := '  while ($procs.Count -gt 0 -and (Get-Date) -lt $deadline) {';
  ScriptLines[41] := '    foreach ($p in $procs) {';
  ScriptLines[42] := '      try { Stop-Process -Id ([int]$p.ProcessId) -Force -ErrorAction SilentlyContinue } catch {}';
  ScriptLines[43] := '    }';
  ScriptLines[44] := '    Start-Sleep -Milliseconds 300';
  ScriptLines[45] := '    $procs = Get-EfsProcs';
  ScriptLines[46] := '  }';
  ScriptLines[47] := '  if (Test-Path $lockPath) { Remove-Item -Path $lockPath -Force -ErrorAction SilentlyContinue }';
  ScriptLines[48] := '  $jobs = Get-RunningJobsCount';
  ScriptLines[49] := '}';
  ScriptLines[50] := '$lines = @("PROCS=$($procs.Count)", "JOBS=$jobs")';
  ScriptLines[51] := 'Set-Content -Path $outFile -Value $lines -Encoding Ascii';
  ScriptLines[52] := 'exit 0';

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
  if not DirExists(TargetDir) then
    Exit;

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

  // Attempt graceful launcher_runtime --shutdown first, then wait/clean remaining locks outside TargetDir
  PyExe := AddBackslash(TargetDir) + 'runtime\python\python.exe';
  if FileExists(PyExe) then
    Exec(PyExe, '-m app.services.longform.launcher_runtime --shutdown --install-root "' + TargetDir + '"', ExpandConstant('{tmp}'), SW_HIDE, ewWaitUntilTerminated, DummyRes);

  RunProcessHelper(TargetDir, StatusFile, True, ProcsFound, JobsRunning);
  if ProcsFound > 0 then
  begin
    if not IsSilent then
      MsgBox(CustomMessage('AppCloseFailedMsg'), mbError, MB_OK);
    Result := False;
  end;
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
  if WizardSilent() then
    HandleRunningAppBeforeFileOp(WizardDirValue(), True);
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
