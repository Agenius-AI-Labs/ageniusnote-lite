; AgeniusNote Lite installer (Inno Setup)
;
; Compile:
;     iscc packaging/installer.iss
;
; Expects PyInstaller output at: dist/AgeniusNoteLite/
; Reads version from: packaging/VERSION (override via /DAppVersion=x.y.z)

#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif

#ifndef SrcDir
  #define SrcDir "..\dist-build\AgeniusNoteLite"
#endif

#ifndef OutDir
  #define OutDir "..\dist-build\installer"
#endif

#define AppName       "AgeniusNote Lite"
#define AppPublisher  "Agenius AI Labs"
#define AppURL        "https://ageniusailabs.com"
#define AppExeName    "AgeniusNoteLite.exe"

[Setup]
AppId={{B9F1A2E0-7B5C-4F4F-9E2D-AGENIUSNOTELITE}}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
AppUpdatesURL={#AppURL}
DefaultDirName={autopf}\AgeniusNote Lite
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir={#OutDir}
OutputBaseFilename=AgeniusNoteLite-Setup-{#AppVersion}
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\{#AppExeName}
Compression=lzma
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog commandline

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "autostart"; Description: "Launch {#AppName} when Windows starts"; GroupDescription: "Startup:"; Flags: unchecked

[Files]
Source: "{#SrcDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon
Name: "{userstartup}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: autostart

[Run]
Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(AppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent
