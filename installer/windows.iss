; Inno Setup script for the Quill Master 2 Windows installer.
; Built by GitHub Actions (.github/workflows/build.yml). To build by hand:
;   iscc installer\windows.iss /DAppVersion=2.0.0

#ifndef AppVersion
  #define AppVersion "2.0.0"
#endif

[Setup]
AppId={{269F102B-1AA0-5855-8BC5-31A4BE89B7FC}
AppName=Quill Master 2
AppVersion={#AppVersion}
AppPublisher=kornerdspider
AppPublisherURL=https://github.com/kornerdspider/QUILLMASTER-2
DefaultDirName={autopf}\Quill Master 2
DefaultGroupName=Quill Master 2
DisableProgramGroupPage=yes
OutputDir=..\dist
OutputBaseFilename=QuillMaster2-Setup-Windows
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\Quill Master 2.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Tasks]
Name: "desktopicon"; Description: "Put a Quill Master 2 icon on the desktop"; GroupDescription: "Shortcuts:"

[Files]
Source: "..\dist\Quill Master 2\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Quill Master 2"; Filename: "{app}\Quill Master 2.exe"
Name: "{group}\Uninstall Quill Master 2"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Quill Master 2"; Filename: "{app}\Quill Master 2.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Quill Master 2.exe"; Description: "Start Quill Master 2 now"; Flags: nowait postinstall skipifsilent
Filename: "https://ollama.com/download"; Description: "Open the Ollama download page (the free AI engine, if you don't have it yet)"; Flags: shellexec postinstall skipifsilent unchecked
