; Teams Lite Windows installer (Inno Setup 6.5+).
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
; Official UB-Mannheim Windows build of Tesseract, verified by SHA-256.
#define TesseractUrl "https://github.com/UB-Mannheim/tesseract/releases/download/v5.4.0.20240606/tesseract-ocr-w64-setup-5.4.0.20240606.exe"
#define TesseractHash "c885fff6998e0608ba4bb8ab51436e1c6775c2bafc2559a19b423e18678b60c9"
#define TesseractSize "50175248"

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
; Room for the component list under the download progress.
WizardSizePercent=120
UninstallDisplayIcon={app}\TeamsLite.exe
UninstallDisplayName=Teams Lite
; The Parakeet model (~2.4 GB) and Tesseract OCR are downloaded during installation.
ExtraDiskSpaceRequired=2700000000

[Languages]
Name: "es"; MessagesFile: "compiler:Languages\Spanish.isl"

[Tasks]
Name: "desktopicon"; Description: "Crear un acceso directo en el escritorio"; GroupDescription: "Accesos directos:"
Name: "tesseract"; Description: "Instalar Tesseract OCR 5.4 para leer los nombres de los asistentes (descarga 48 MB; Windows pedirá permiso de administrador)"; GroupDescription: "Componentes:"; Flags: dontinheritcheck; Check: not TesseractInstalled

[InstallDelete]
; Drop libraries of the previous version before copying the new bundle.
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "{#SourceDir}\*"; Excludes: "config.json"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Teams Lite"; Filename: "{app}\TeamsLite.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\Teams Lite"; Filename: "{app}\TeamsLite.exe"; Tasks: desktopicon; WorkingDir: "{app}"

[Run]
Filename: "{app}\TeamsLite.exe"; Description: "Abrir Teams Lite"; WorkingDir: "{app}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: files; Name: "{app}\config.json"

[Code]
var
  DownloadPage: TDownloadWizardPage;
  ModelNames, ModelHashes, ModelSizes: TArrayOfString;
  ModelPending: array of Boolean;
  // Component list shown under the download progress, like a database engine setup.
  ModelStatus, TesseractStatus: TNewStaticText;
  TotalBar: TNewProgressBar;
  // Byte accounting for the status lines and the total bar.
  TotalBytes, DoneBytes, ComponentBase, ComponentTotal, CurrentFileSize: Int64;
  CurrentFile: String;
  CurrentStatus: TNewStaticText;
  ComponentStartTick: DWORD;

// Milliseconds since boot, for the remaining-time estimate (Pascal Script has no clock of its own).
function GetTickCount: DWORD;
  external 'GetTickCount@kernel32.dll stdcall';

function ModelDir: String;
begin
  Result := ExpandConstant('{localappdata}\TeamsLite\models\parakeet-v3');
end;

// Only hides the optional task: the app itself finds Tesseract at run time (ocr.find_tesseract).
function TesseractInstalled: Boolean;
begin
  Result := FileExists(ExpandConstant('{commonpf64}\Tesseract-OCR\tesseract.exe')) or
    FileExists(ExpandConstant('{commonpf32}\Tesseract-OCR\tesseract.exe'));
end;

procedure AddModelFile(const Name, Hash, Size: String);
var
  Count: Integer;
begin
  Count := GetArrayLength(ModelNames);
  SetArrayLength(ModelNames, Count + 1);
  SetArrayLength(ModelHashes, Count + 1);
  SetArrayLength(ModelSizes, Count + 1);
  ModelNames[Count] := Name;
  ModelHashes[Count] := Hash;
  ModelSizes[Count] := Size;
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

// Decimal units, like the sizes in the component names ("2,4 GB").
function FormatSize(const Bytes: Int64): String;
var
  Tenths: Int64;
begin
  if Bytes >= 1000000000 then
  begin
    Tenths := (Bytes * 10) div 1000000000;
    Result := IntToStr(Tenths div 10) + ',' + IntToStr(Tenths mod 10) + ' GB';
  end
  else
    Result := IntToStr(Bytes div 1000000) + ' MB';
end;

function FormatRemaining(const Done, Total: Int64): String;
var
  Elapsed: DWORD;
  Seconds: Int64;
begin
  Result := '';
  Elapsed := GetTickCount - ComponentStartTick;
  if (Elapsed < 3000) or (Done <= 0) or (Done >= Total) then
    Exit;
  Seconds := ((Total - Done) * Elapsed) div (Done * 1000);
  if Seconds < 60 then
    Result := ' · quedan ~' + IntToStr(Seconds) + ' s'
  else
    Result := ' · quedan ~' + IntToStr((Seconds + 59) div 60) + ' min';
end;

procedure SetStatus(const Status: TNewStaticText; const Text: String; const Color: TColor);
begin
  Status.Caption := Text;
  Status.Font.Color := Color;
end;

procedure UpdateTotal(const Done: Int64);
begin
  if TotalBytes > 0 then
    TotalBar.Position := Integer((Done * 1000) div TotalBytes);
end;

function OnDownloadProgress(const Url, FileName: String; const Progress, ProgressMax: Int64): Boolean;
var
  Done, ComponentDone: Int64;
begin
  if FileName <> CurrentFile then
  begin
    if CurrentFile <> '' then
      DoneBytes := DoneBytes + CurrentFileSize;
    CurrentFile := FileName;
    CurrentFileSize := 0;
  end;
  if ProgressMax > 0 then
    CurrentFileSize := ProgressMax
  else if Progress > CurrentFileSize then
    // No Content-Length: count what arrived, so the total never steps back on the next file.
    CurrentFileSize := Progress;
  Done := DoneBytes + Progress;
  ComponentDone := Done - ComponentBase;
  if (CurrentStatus <> nil) and (ComponentTotal > 0) then
  begin
    if ComponentDone >= ComponentTotal then
      // Download then hashes every file: several seconds for the 2.4 GB one.
      SetStatus(CurrentStatus, 'Verificando la integridad (SHA-256)…', clWindowText)
    else
      SetStatus(CurrentStatus, 'Descargando ' + IntToStr(Integer((ComponentDone * 100) div ComponentTotal)) + ' % · ' +
        FormatSize(ComponentDone) + ' de ' + FormatSize(ComponentTotal) + FormatRemaining(ComponentDone, ComponentTotal), clWindowText);
  end;
  UpdateTotal(Done);
  Result := True;
end;

procedure BeginComponent(const Status: TNewStaticText; const Bytes: Int64);
begin
  CurrentStatus := Status;
  ComponentBase := DoneBytes;
  ComponentTotal := Bytes;
  CurrentFile := '';
  CurrentFileSize := 0;
  ComponentStartTick := GetTickCount;
end;

procedure EndComponent;
begin
  // Authoritative: files already in {tmp} from a retry report no progress at all.
  DoneBytes := ComponentBase + ComponentTotal;
  CurrentFile := '';
  CurrentStatus := nil;
  UpdateTotal(DoneBytes);
end;

function AddRow(const Caption: String; const Top: Integer; const Bold: Boolean): TNewStaticText;
begin
  Result := TNewStaticText.Create(DownloadPage);
  Result.Parent := DownloadPage.Surface;
  Result.AutoSize := False;
  Result.Left := 0;
  Result.Top := Top;
  Result.Width := DownloadPage.SurfaceWidth;
  Result.Height := ScaleY(16);
  Result.Caption := Caption;
  if Bold then
    Result.Font.Style := [fsBold];
end;

function AddStatusRow(const Top: Integer): TNewStaticText;
begin
  Result := AddRow('Pendiente', Top, False);
  Result.Left := ScaleX(14);
  Result.Width := DownloadPage.SurfaceWidth - ScaleX(14);
  Result.Font.Color := clGrayText;
end;

procedure InitializeWizard;
var
  Top: Integer;
  TotalLabel: TNewStaticText;
begin
  AddModelFile('config.json', '666903c76b9798caf2c210afd4f6cd60b08a8dbf9800ec8d7a3bc0d2148ac466', '97');
  AddModelFile('vocab.txt', 'd58544679ea4bc6ac563d1f545eb7d474bd6cfa467f0a6e2c1dc1c7d37e3c35d', '93939');
  AddModelFile('decoder_joint-model.onnx', 'e978ddf6688527182c10fde2eb4b83068421648985ef23f7a86be732be8706c1', '72520893');
  AddModelFile('encoder-model.onnx', '98a74b21b4cc0017c1e7030319a4a96f4a9506e50f0708f3a516d02a77c96bb1', '41770866');
  AddModelFile('encoder-model.onnx.data', '9a22d372c51455c34f13405da2520baefb7125bd16981397561423ed32d24f36', '2435420160');

  DownloadPage := CreateDownloadPage(
    'Instalando componentes',
    'Teams Lite descarga lo que necesita para transcribir y leer asistentes. Solo la primera vez.',
    @OnDownloadProgress);
  DownloadPage.ShowBaseNameInsteadOfUrl := True;

  Top := DownloadPage.AbortButton.Top + DownloadPage.AbortButton.Height + ScaleY(14);
  AddRow('Componentes', Top, True);
  AddRow('Modelo de transcripción (Parakeet, 2,4 GB)', Top + ScaleY(24), False);
  ModelStatus := AddStatusRow(Top + ScaleY(41));
  AddRow('Tesseract OCR 5.4 (nombres de asistentes, 48 MB)', Top + ScaleY(64), False);
  TesseractStatus := AddStatusRow(Top + ScaleY(81));
  TotalLabel := AddRow('Progreso total', Top + ScaleY(108), True);
  TotalBar := TNewProgressBar.Create(DownloadPage);
  TotalBar.Parent := DownloadPage.Surface;
  TotalBar.Left := 0;
  TotalBar.Top := TotalLabel.Top + ScaleY(20);
  TotalBar.Width := DownloadPage.SurfaceWidth;
  TotalBar.Height := ScaleY(14);
  TotalBar.Min := 0;
  TotalBar.Max := 1000;
end;

// Raises on failure: the model is required to transcribe.
procedure InstallModelFiles(const Pending: TArrayOfString; const Bytes: Int64);
var
  Index: Integer;
  Source, Target: String;
begin
  if GetArrayLength(Pending) = 0 then
  begin
    SetStatus(ModelStatus, 'Ya instalado ✓', clGreen);
    Exit;
  end;
  BeginComponent(ModelStatus, Bytes);
  SetStatus(ModelStatus, 'Descargando…', clWindowText);
  DownloadPage.SetText('Descargando el modelo de transcripción...', '');
  DownloadPage.Clear;
  for Index := 0 to GetArrayLength(ModelNames) - 1 do
    if ModelPending[Index] then
      DownloadPage.Add('{#ModelBaseUrl}' + ModelNames[Index], ModelNames[Index], ModelHashes[Index]);
  DownloadPage.Download; // verifies each file against its SHA-256
  SetStatus(ModelStatus, 'Instalando…', clWindowText);
  ForceDirectories(ModelDir);
  for Index := 0 to GetArrayLength(Pending) - 1 do
  begin
    Source := ExpandConstant('{tmp}\') + Pending[Index];
    Target := ModelDir + '\' + Pending[Index];
    DeleteFile(Target);
    if not RenameFile(Source, Target) then
      RaiseException('No se pudo guardar ' + Pending[Index] + ' en ' + ModelDir);
  end;
  EndComponent;
  SetStatus(ModelStatus, 'Listo ✓', clGreen);
end;

// Optional: any failure is reported on its status line and installation continues.
procedure InstallTesseract;
var
  ErrorCode: Integer;
begin
  BeginComponent(TesseractStatus, StrToInt64('{#TesseractSize}'));
  try
    SetStatus(TesseractStatus, 'Descargando…', clWindowText);
    DownloadPage.SetText('Descargando Tesseract OCR...', '');
    DownloadPage.Clear;
    DownloadPage.Add('{#TesseractUrl}', 'tesseract-ocr-setup.exe', '{#TesseractHash}');
    DownloadPage.Download;
  except
    Log('Tesseract download failed: ' + GetExceptionMessage);
    EndComponent;
    // Stopping this optional download skips only Tesseract; Teams Lite still installs.
    if DownloadPage.AbortedByUser then
      SetStatus(TesseractStatus, 'Omitido: descarga detenida (puedes instalarlo después)', clMaroon)
    else
      SetStatus(TesseractStatus, 'Omitido: no se pudo descargar (puedes instalarlo después)', clMaroon);
    Exit;
  end;
  EndComponent;
  SetStatus(TesseractStatus, 'Instalando… acepta el permiso de administrador de Windows', clWindowText);
  DownloadPage.SetText('Instalando Tesseract OCR...', 'Windows pedirá permiso de administrador.');
  DownloadPage.ProgressBar.Style := npbstMarquee;
  try
    // ShellExec (not Exec) so Windows can show the UAC prompt the Tesseract setup requires.
    if not ShellExec('', ExpandConstant('{tmp}\tesseract-ocr-setup.exe'), '/S', '', SW_SHOW, ewWaitUntilTerminated, ErrorCode) then
      Log('Tesseract setup did not run: ' + SysErrorMessage(ErrorCode));
  finally
    DownloadPage.ProgressBar.Style := npbstNormal;
  end;
  if TesseractInstalled then
    SetStatus(TesseractStatus, 'Listo ✓', clGreen)
  else
    SetStatus(TesseractStatus, 'Omitido: no se instaló (permiso rechazado o error); puedes instalarlo después', clMaroon);
end;

function InstallComponents: Boolean;
var
  Index: Integer;
  Pending: TArrayOfString;
  PendingBytes: Int64;
  WantTesseract: Boolean;
begin
  Result := True;
  TotalBar.Position := 0;
  DoneBytes := 0;
  SetStatus(ModelStatus, 'Pendiente', clGrayText);
  WantTesseract := WizardIsTaskSelected('tesseract') and not TesseractInstalled;
  if TesseractInstalled then
    SetStatus(TesseractStatus, 'Ya instalado ✓', clGreen)
  else if WantTesseract then
    SetStatus(TesseractStatus, 'Pendiente', clGrayText)
  else
    SetStatus(TesseractStatus, 'Omitido (no seleccionado)', clGrayText);

  DownloadPage.SetText('Comprobando los componentes ya instalados...', '');
  DownloadPage.SetProgress(0, 0);
  SetStatus(ModelStatus, 'Comprobando el modelo existente…', clWindowText);
  DownloadPage.Show;
  try
    try
      // Hashing an existing 2.4 GB model takes several seconds.
      SetArrayLength(Pending, 0);
      SetArrayLength(ModelPending, GetArrayLength(ModelNames));
      PendingBytes := 0;
      for Index := 0 to GetArrayLength(ModelNames) - 1 do
      begin
        ModelPending[Index] := not ModelFileIsCurrent(Index);
        if ModelPending[Index] then
        begin
          SetArrayLength(Pending, GetArrayLength(Pending) + 1);
          Pending[GetArrayLength(Pending) - 1] := ModelNames[Index];
          PendingBytes := PendingBytes + StrToInt64(ModelSizes[Index]);
        end;
      end;
      TotalBytes := PendingBytes;
      if WantTesseract then
        TotalBytes := TotalBytes + StrToInt64('{#TesseractSize}');

      InstallModelFiles(Pending, PendingBytes);
      if WantTesseract then
        try
          InstallTesseract;
        except
          // Never report a Tesseract problem as a model failure, and never block on it.
          Log('Tesseract installation failed: ' + GetExceptionMessage);
          SetStatus(TesseractStatus, 'Omitido: error inesperado (puedes instalarlo después)', clMaroon);
        end;
      TotalBar.Position := TotalBar.Max;
      DownloadPage.SetText('Componentes listos.', 'A continuación se instala Teams Lite.');
      if not WizardSilent then
        Sleep(1500); // let the user see the final state of every component
    except
      // Logged too: in a /VERYSILENT install the message box is suppressed and only /LOG remains.
      Log('Model installation failed: ' + GetExceptionMessage);
      SetStatus(ModelStatus, 'Error: ' + GetExceptionMessage, clRed);
      if not DownloadPage.AbortedByUser then
        SuppressibleMsgBox('No se pudo instalar el modelo de transcripción: ' + GetExceptionMessage + #13#10#13#10 +
          'Revisa la conexión a internet y el espacio libre en disco, y vuelve a intentarlo.', mbCriticalError, MB_OK, IDOK);
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
    Result := InstallComponents;
end;

function JsonPath(const Path: String): String;
begin
  Result := Path;
  StringChangeEx(Result, '\', '\\', True);
  Result := '"' + Result + '"';
end;

procedure WriteDefaultConfig;
var
  ConfigPath, Base: String;
  Lines: TArrayOfString;
begin
  // An existing config.json holds the user's choices: upgrades keep it.
  ConfigPath := ExpandConstant('{app}\config.json');
  if FileExists(ConfigPath) then
    Exit;
  Base := ExpandConstant('{localappdata}\TeamsLite');
  SetArrayLength(Lines, 15);
  Lines[0] := '{';
  Lines[1] := '  "data_dir": ' + JsonPath(Base + '\data') + ',';
  Lines[2] := '  "sample_rate": 48000,';
  Lines[3] := '  "segment_seconds": 30,';
  Lines[4] := '  "auto_transcribe": true,';
  Lines[5] := '  "parakeet_model_dir": ' + JsonPath(ModelDir) + ',';
  Lines[6] := '  "ocr_enabled": false,';
  Lines[7] := '  "ocr_region": null,';
  // null: the app looks for Tesseract on PATH and in its standard folders each time it runs.
  Lines[8] := '  "tesseract_cmd": null,';
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
  // Only the model this installer downloaded: it can be downloaded again. Other models the
  // user placed next to it, recorded meetings in data\ and Tesseract OCR are always kept.
  if CurUninstallStep = usPostUninstall then
  begin
    DelTree(ModelDir, True, True, True);
    RemoveDir(ExpandConstant('{localappdata}\TeamsLite\models')); // only when it is now empty
  end;
end;
