; Teams Lite Windows installer (Inno Setup 6).
; Build with build_windows.ps1 -Installer, which passes AppVersion and SourceDir.
; Per-user install: no admin rights. Meetings and models live in %LOCALAPPDATA%\TeamsLite,
; outside OneDrive and outside the program folder, so upgrades never touch them.

#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif
#ifndef SourceDir
  #define SourceDir GetEnv("LOCALAPPDATA") + "\TeamsLite-build\dist\TeamsLite"
#endif

; Pinned commit of the Hugging Face repo onnx_asr uses for nemo-parakeet-tdt-0.6b-v3.
#define ModelBaseUrl "https://huggingface.co/istupakov/parakeet-tdt-0.6b-v3-onnx/resolve/8f23f0c03c8761650bdb5b40aaf3e40d2c15f1ce/"

[Setup]
AppId={{6F1D2C3B-8E4A-4B7D-9C21-5A7E3F0B9D42}
AppName=Teams Lite
AppVersion={#AppVersion}
AppVerName=Teams Lite {#AppVersion}
DefaultDirName={localappdata}\Programs\TeamsLite
DisableDirPage=yes
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist\installer
OutputBaseFilename=TeamsLite-Setup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\TeamsLite.exe
UninstallDisplayName=Teams Lite
; The Parakeet model (~2.4 GB) is downloaded during installation.
ExtraDiskSpaceRequired=2600000000

[Languages]
Name: "es"; MessagesFile: "compiler:Languages\Spanish.isl"

[Tasks]
Name: "desktopicon"; Description: "Crear un acceso directo en el escritorio"; GroupDescription: "Accesos directos:"
Name: "tesseract"; Description: "Instalar Tesseract OCR para leer los asistentes (opcional, usa winget y pide permisos de administrador)"; GroupDescription: "Componentes opcionales:"; Flags: unchecked; Check: not TesseractInstalled

[InstallDelete]
; Drop libraries of the previous version before copying the new bundle.
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "{#SourceDir}\*"; Excludes: "config.json"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Teams Lite"; Filename: "{app}\TeamsLite.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\Teams Lite"; Filename: "{app}\TeamsLite.exe"; Tasks: desktopicon; WorkingDir: "{app}"

[Run]
Filename: "{localappdata}\Microsoft\WindowsApps\winget.exe"; Parameters: "install -e --id UB-Mannheim.TesseractOCR --silent --accept-package-agreements --accept-source-agreements"; StatusMsg: "Instalando Tesseract OCR..."; Tasks: tesseract; Flags: waituntilterminated skipifdoesntexist
Filename: "{app}\TeamsLite.exe"; Description: "Abrir Teams Lite"; WorkingDir: "{app}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: files; Name: "{app}\config.json"

[Code]
var
  DownloadPage: TDownloadWizardPage;
  ModelNames: TArrayOfString;
  ModelHashes: TArrayOfString;

function ModelDir: String;
begin
  Result := ExpandConstant('{localappdata}\TeamsLite\models\parakeet-v3');
end;

function TesseractPath: String;
begin
  Result := ExpandConstant('{commonpf64}\Tesseract-OCR\tesseract.exe');
end;

function TesseractInstalled: Boolean;
begin
  Result := FileExists(TesseractPath);
end;

procedure AddModelFile(const Name, Hash: String);
var
  Count: Integer;
begin
  Count := GetArrayLength(ModelNames);
  SetArrayLength(ModelNames, Count + 1);
  SetArrayLength(ModelHashes, Count + 1);
  ModelNames[Count] := Name;
  ModelHashes[Count] := Hash;
end;

function ModelFileIsCurrent(const Index: Integer): Boolean;
var
  Path: String;
begin
  Path := ModelDir + '\' + ModelNames[Index];
  Result := False;
  if FileExists(Path) then
    try
      Result := CompareText(GetSHA256OfFile(Path), ModelHashes[Index]) = 0;
    except
      Result := False;
    end;
end;

function OnDownloadProgress(const Url, FileName: String; const Progress, ProgressMax: Int64): Boolean;
begin
  Result := True;
end;

procedure InitializeWizard;
begin
  AddModelFile('config.json', '666903c76b9798caf2c210afd4f6cd60b08a8dbf9800ec8d7a3bc0d2148ac466');
  AddModelFile('vocab.txt', 'd58544679ea4bc6ac563d1f545eb7d474bd6cfa467f0a6e2c1dc1c7d37e3c35d');
  AddModelFile('decoder_joint-model.onnx', 'e978ddf6688527182c10fde2eb4b83068421648985ef23f7a86be732be8706c1');
  AddModelFile('encoder-model.onnx', '98a74b21b4cc0017c1e7030319a4a96f4a9506e50f0708f3a516d02a77c96bb1');
  AddModelFile('encoder-model.onnx.data', '9a22d372c51455c34f13405da2520baefb7125bd16981397561423ed32d24f36');
  DownloadPage := CreateDownloadPage(
    'Descargando el modelo de transcripción',
    'Parakeet (~2.4 GB) se descarga una sola vez; las reinstalaciones lo reutilizan.',
    @OnDownloadProgress);
end;

function InstallModel: Boolean;
var
  Index: Integer;
  Pending: TArrayOfString;
  Source, Target: String;
begin
  Result := True;
  DownloadPage.Clear;
  SetArrayLength(Pending, 0);
  for Index := 0 to GetArrayLength(ModelNames) - 1 do
    if not ModelFileIsCurrent(Index) then
    begin
      DownloadPage.Add('{#ModelBaseUrl}' + ModelNames[Index], ModelNames[Index], ModelHashes[Index]);
      SetArrayLength(Pending, GetArrayLength(Pending) + 1);
      Pending[GetArrayLength(Pending) - 1] := ModelNames[Index];
    end;
  if GetArrayLength(Pending) = 0 then
    Exit;
  DownloadPage.Show;
  try
    try
      DownloadPage.Download; // verifies each file against its SHA-256
      ForceDirectories(ModelDir);
      for Index := 0 to GetArrayLength(Pending) - 1 do
      begin
        Source := ExpandConstant('{tmp}\') + Pending[Index];
        Target := ModelDir + '\' + Pending[Index];
        DeleteFile(Target);
        if not RenameFile(Source, Target) then
          RaiseException('No se pudo guardar ' + Pending[Index] + ' en ' + ModelDir);
      end;
    except
      if not DownloadPage.AbortedByUser then
        SuppressibleMsgBox('No se pudo descargar el modelo de transcripción: ' + GetExceptionMessage + #13#10#13#10 +
          'Revisa la conexión a internet y vuelve a intentarlo.', mbCriticalError, MB_OK, IDOK);
      Result := False;
    end;
  finally
    DownloadPage.Hide;
  end;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if CurPageID = wpReady then
    Result := InstallModel;
end;

function JsonPath(const Path: String): String;
begin
  Result := Path;
  StringChangeEx(Result, '\', '\\', True);
  Result := '"' + Result + '"';
end;

procedure WriteDefaultConfig;
var
  ConfigPath, Base, Tesseract: String;
  Lines: TArrayOfString;
begin
  // An existing config.json holds the user's choices: upgrades keep it.
  ConfigPath := ExpandConstant('{app}\config.json');
  if FileExists(ConfigPath) then
    Exit;
  Base := ExpandConstant('{localappdata}\TeamsLite');
  if TesseractInstalled or WizardIsTaskSelected('tesseract') then
    Tesseract := JsonPath(TesseractPath)
  else
    Tesseract := 'null';
  SetArrayLength(Lines, 15);
  Lines[0] := '{';
  Lines[1] := '  "data_dir": ' + JsonPath(Base + '\data') + ',';
  Lines[2] := '  "sample_rate": 48000,';
  Lines[3] := '  "segment_seconds": 30,';
  Lines[4] := '  "auto_transcribe": true,';
  Lines[5] := '  "parakeet_model_dir": ' + JsonPath(ModelDir) + ',';
  Lines[6] := '  "ocr_enabled": false,';
  Lines[7] := '  "ocr_region": null,';
  Lines[8] := '  "tesseract_cmd": ' + Tesseract + ',';
  Lines[9] := '  "visual_speaker_enabled": false,';
  Lines[10] := '  "visual_sample_seconds": 2.0,';
  Lines[11] := '  "teams_window_title": "Microsoft Teams",';
  Lines[12] := '  "wevisone_model_dir": ' + JsonPath(Base + '\models\wevisone-vl-2b') + ',';
  Lines[13] := '  "minutes_cli": "claude"';
  Lines[14] := '}';
  if not SaveStringsToUTF8File(ConfigPath, Lines, False) then
    SuppressibleMsgBox('No se pudo escribir ' + ConfigPath, mbError, MB_OK, IDOK);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    WriteDefaultConfig;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  // The model can be downloaded again; recorded meetings in data\ are always kept.
  if CurUninstallStep = usPostUninstall then
    DelTree(ExpandConstant('{localappdata}\TeamsLite\models'), True, True, True);
end;
