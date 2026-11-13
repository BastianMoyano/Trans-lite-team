param(
    # Also build dist\installer\TeamsLite-Setup-<version>.exe with Inno Setup 6.
    [switch]$Installer
)
$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python)) {
    throw "No se encontró el entorno virtual en $Python"
}

Push-Location $Root
try {
    # The bundle is built outside OneDrive: its sync locks folders, so PyInstaller cannot wipe
    # the previous build (WinError 5), and it would upload ~250 MB on every build.
    $BuildRoot = Join-Path $env:LOCALAPPDATA "TeamsLite-build"
    $DistPath = Join-Path $BuildRoot "dist"
    & $Python -m PyInstaller --noconfirm --clean --workpath (Join-Path $BuildRoot "work") --distpath $DistPath TeamsLite.spec
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller finalizó con código $LASTEXITCODE"
    }

    $Output = Join-Path $DistPath "TeamsLite"
    $SourceConfig = if (Test-Path -LiteralPath "config.json") { "config.json" } else { "config.example.json" }
    $Config = Get-Content -LiteralPath $SourceConfig -Raw | ConvertFrom-Json
    # data_dir must resolve outside dist\: PyInstaller --noconfirm wipes the output folder on every build.
    foreach ($Property in @("data_dir", "parakeet_model_dir", "wevisone_model_dir")) {
        $Value = $Config.$Property
        if ($Value -and -not [System.IO.Path]::IsPathRooted($Value)) {
            $Config.$Property = [System.IO.Path]::GetFullPath((Join-Path $Root $Value))
        }
    }
    $DistRoot = [System.IO.Path]::GetFullPath($DistPath).TrimEnd('\') + '\'
    $DataDir = [System.IO.Path]::GetFullPath($Config.data_dir).TrimEnd('\') + '\'
    if ($DataDir.StartsWith($DistRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "data_dir no puede estar dentro de dist\: se borraría en el próximo build ($($Config.data_dir))"
    }
    $Json = $Config | ConvertTo-Json -Depth 10
    [System.IO.File]::WriteAllText((Join-Path $Output "config.json"), $Json, (New-Object System.Text.UTF8Encoding $false))

    # TeamsLite.exe is a windowed app: PowerShell neither waits for it nor sets $LASTEXITCODE.
    # A startup crash shows a modal error dialog; never let it hang an unattended build.
    $Smoke = Start-Process -FilePath (Join-Path $Output "TeamsLite.exe") -ArgumentList "--help" -PassThru
    if (-not $Smoke.WaitForExit(120000)) {
        Stop-Process -Id $Smoke.Id -Force
        throw "El ejecutable no terminó la prueba de inicio en 120 s (probable diálogo de error al arrancar)"
    }
    if ($Smoke.ExitCode -ne 0) {
        throw "El ejecutable no superó la prueba de inicio (código $($Smoke.ExitCode))"
    }
    Write-Host "Ejecutable creado en: $(Join-Path $Output 'TeamsLite.exe')"

    if ($Installer) {
        $Iscc = (Get-Command "ISCC.exe" -ErrorAction SilentlyContinue).Source
        if (-not $Iscc) {
            $Iscc = @(
                (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe"),
                (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"),
                (Join-Path $env:ProgramFiles "Inno Setup 6\ISCC.exe")
            ) | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
        }
        if (-not $Iscc) {
            throw "No se encontró Inno Setup 6 (ISCC.exe). Instálalo con: winget install -e --id JRSoftware.InnoSetup"
        }
        $Version = [regex]::Match((Get-Content -LiteralPath "pyproject.toml" -Raw), '(?m)^version\s*=\s*"([^"]+)"').Groups[1].Value
        if (-not $Version) {
            throw "No se encontró la versión en pyproject.toml"
        }
        & $Iscc "/DAppVersion=$Version" "/DSourceDir=$Output" (Join-Path $Root "installer\TeamsLite.iss")
        if ($LASTEXITCODE -ne 0) {
            throw "Inno Setup finalizó con código $LASTEXITCODE"
        }
        Write-Host "Instalador creado en: $(Join-Path $Root "dist\installer\TeamsLite-Setup-$Version.exe")"
    }
}
finally {
    Pop-Location
}
