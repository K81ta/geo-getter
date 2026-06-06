#define MyAppName "GEOGetter"
#ifndef AppVersion
#error AppVersion must be defined by tools\build_release.ps1
#endif
#ifndef SourceDir
#error SourceDir must be defined by tools\build_release.ps1
#endif
#ifndef OutputDir
#error OutputDir must be defined by tools\build_release.ps1
#endif

[Setup]
AppId={{2D9779EF-9499-45E6-8E87-BC7D9DF3B77F}
AppName={#MyAppName}
AppVersion={#AppVersion}
AppPublisher=K81ta
DefaultDirName={localappdata}\Programs\GEOGetter
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
LicenseFile={#SourceDir}\LICENSE-BUNDLE.txt
OutputDir={#OutputDir}
OutputBaseFilename=GEOGetter-Setup-v{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\GEOGetter"; Filename: "{win}\System32\wscript.exe"; Parameters: """{app}\start_geo_getter.vbs"""; WorkingDir: "{app}"
Name: "{autodesktop}\GEOGetter"; Filename: "{win}\System32\wscript.exe"; Parameters: """{app}\start_geo_getter.vbs"""; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{win}\System32\wscript.exe"; Parameters: """{app}\start_geo_getter.vbs"""; Description: "Launch GEOGetter"; Flags: nowait postinstall skipifsilent
