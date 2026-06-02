#define MyAppName "GEOGetter"
#ifndef AppVersion
#define AppVersion "0.1.1"
#endif
#ifndef SourceDir
#define SourceDir "..\dist\GEOGetter-v0.1.1-win-x64-portable"
#endif
#ifndef OutputDir
#define OutputDir "..\dist"
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
