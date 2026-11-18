; Inno Setup 6 Installer Script for EasyFaceless Studio (Stage 8C.2, D4)
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
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
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
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "indonesian"; MessagesFile: "compiler:Default.isl,Indonesian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[CustomMessages]
english.AdditionalIcons=Additional shortcuts:
english.CreateDesktopIcon=Create a &Desktop shortcut
english.LaunchProgram=Launch %1
english.UninstallRemoveDataPrompt=Do you also want to remove your user data folder (%1)?%n%nThis contains config.toml, video projects, custom presets, cache, and logs.%nChoose "No" (recommended) to keep your projects and settings.

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
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[Code]
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
