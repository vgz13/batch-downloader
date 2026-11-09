; Installer Windows untuk Batch Downloader (Inno Setup 6).
; Dibangun otomatis oleh GitHub Actions; hasil: Output\BatchDownloader-Setup.exe
; Build manual di Windows:  iscc installer.iss   (setelah dist\BatchDownloader.exe ada)

#define AppName "Batch Downloader"
#ifndef AppVersion
  #define AppVersion "1.1"
#endif
#define AppExe "BatchDownloader.exe"
#define AppURL "https://github.com/vgz13/batch-downloader"

[Setup]
AppId={{6F1C2B7A-4E2D-4C55-9B1E-BD0A11C0D0E1}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=vgz13
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
AppUpdatesURL={#AppURL}/releases
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
; tidak butuh admin: dipasang untuk pengguna saat ini (bisa pilih "semua pengguna" di dialog)
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=Output
OutputBaseFilename=BatchDownloader-Setup
SetupIconFile=assets\icon.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes

[Languages]
Name: "indonesian"; MessagesFile: "compiler:Languages\Indonesian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[CustomMessages]
indonesian.FfmpegTask=Pasang ffmpeg (untuk video kualitas tertinggi && MP3, lewat winget)
english.FfmpegTask=Install ffmpeg (for best video quality && MP3, via winget)
indonesian.Extras=Tambahan:
english.Extras=Extras:

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "ffmpeg"; Description: "{cm:FfmpegTask}"; GroupDescription: "{cm:Extras}"; Flags: unchecked

[Files]
Source: "dist\{#AppExe}"; DestDir: "{app}"; Flags: ignoreversion
Source: "README.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{cmd}"; Parameters: "/c winget install --id Gyan.FFmpeg -e --accept-source-agreements --accept-package-agreements"; StatusMsg: "Memasang ffmpeg..."; Tasks: ffmpeg; Flags: runhidden waituntilterminated skipifdoesntexist
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
