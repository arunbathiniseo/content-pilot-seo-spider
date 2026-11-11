; Inno Setup 6 script for Content Pilot Spider. Built by packaging\build.py, which passes AppVersion, StageDir and OutDir.
; The app keeps settings.json and crawls\ next to itself, so it installs per user into a folder that stays writable.

#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif
#ifndef StageDir
  #define StageDir "..\dist\stage\Content Pilot Spider"
#endif
#ifndef OutDir
  #define OutDir "..\dist"
#endif

[Setup]
AppId={{6E0B3C52-7A3E-4C0B-9D4E-5C8F1A2B7D31}
AppName=Content Pilot Spider
AppVersion={#AppVersion}
AppVerName=Content Pilot Spider {#AppVersion}
AppPublisher=Arun Bathini, Content Pilot
AppPublisherURL=https://contentpilot.cloud
AppSupportURL=https://github.com/arunbathiniseo/content-pilot-seo-spider/issues
AppUpdatesURL=https://github.com/arunbathiniseo/content-pilot-seo-spider/releases
AppCopyright=Copyright (c) 2026 Arun Bathini, Content Pilot
VersionInfoVersion={#AppVersion}
DefaultDirName={localappdata}\Programs\Content Pilot Spider
DefaultGroupName=Content Pilot Spider
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
LicenseFile={#StageDir}\LICENSE
SetupIconFile={#StageDir}\ui\icon.ico
UninstallDisplayIcon={app}\ui\icon.ico
UninstallDisplayName=Content Pilot Spider
WizardStyle=modern
WizardSmallImageFile=wizard-small.bmp
WizardImageFile=wizard-large.bmp
OutputDir={#OutDir}
OutputBaseFilename=ContentPilotSpider-Setup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
CloseApplications=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
Source: "{#StageDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Content Pilot Spider"; Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\run.py"""; WorkingDir: "{app}"; IconFilename: "{app}\ui\icon.ico"; Comment: "Crawl a website and find its technical SEO issues"
Name: "{group}\Install browser for JavaScript rendering"; Filename: "{app}\Install browser for JavaScript rendering.bat"; WorkingDir: "{app}"; IconFilename: "{app}\ui\icon.ico"
Name: "{group}\Uninstall Content Pilot Spider"; Filename: "{uninstallexe}"
Name: "{userdesktop}\Content Pilot Spider"; Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\run.py"""; WorkingDir: "{app}"; IconFilename: "{app}\ui\icon.ico"; Tasks: desktopicon

[Run]
Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\run.py"""; WorkingDir: "{app}"; Description: "Open Content Pilot Spider"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}\python\Lib\site-packages\__pycache__"
Type: files; Name: "{app}\.port"
Type: files; Name: "{app}\spider.log"

[Messages]
FinishedLabel=Setup has finished installing Content Pilot Spider. Your crawls and settings are kept in the installation folder and are left in place if you uninstall.
