; Windows installer (Inno Setup 6). Built by .github/workflows/release.yml:
;   iscc /DAppVersion=0.1.0a1 packaging\windows\mapmise.iss
; Installs for the current user only (no administrator rights needed).

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{8C2E1F4A-6B7D-4E59-9A3C-2D1F0B8E7A61}
AppName=Mapmise
AppVersion={#AppVersion}
AppPublisher=Mapmise contributors
AppPublisherURL=https://github.com/AiM0-create/mapmise
DefaultDirName={localappdata}\Programs\Mapmise
DefaultGroupName=Mapmise
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\..\build\installers
OutputBaseFilename=Mapmise-{#AppVersion}-windows-x64-setup
SetupIconFile=..\icon.ico
UninstallDisplayIcon={app}\Mapmise.exe
LicenseFile=..\..\LICENSE
Compression=lzma2/max
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
Source: "..\..\build\dist\Mapmise\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\Mapmise"; Filename: "{app}\Mapmise.exe"
Name: "{group}\Uninstall Mapmise"; Filename: "{uninstallexe}"
Name: "{userdesktop}\Mapmise"; Filename: "{app}\Mapmise.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Mapmise.exe"; Description: "Start Mapmise now"; Flags: nowait postinstall skipifsilent
