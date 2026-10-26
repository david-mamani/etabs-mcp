#Requires -Version 5.1
<#
.SYNOPSIS
    Instala el servidor MCP de ETABS en esta PC (Windows) y lo registra en OpenCode.

.DESCRIPTION
    Descarga etabs-mcp.exe desde el ultimo release de GitHub, lo deja en
    %LOCALAPPDATA%\etabs-mcp\ y agrega/actualiza el servidor "etabs" en
    opencode.json. Respalda la configuracion antes de tocarla.

    Uso rapido (sin descargar nada a mano):
        irm https://raw.githubusercontent.com/david-mamani/etabs-mcp/main/install.ps1 | iex

.PARAMETER InstallDir
    Carpeta donde queda etabs-mcp.exe. Por defecto %LOCALAPPDATA%\etabs-mcp.

.PARAMETER ConfigPath
    Ruta del opencode.json. Por defecto %USERPROFILE%\.config\opencode\opencode.json.

.PARAMETER ExpectedSha256
    Si se indica, valida el hash SHA256 del ejecutable descargado.

.PARAMETER SkipConfig
    Solo descarga el exe; no toca opencode.json.

.PARAMETER SkipSelfTest
    No ejecuta la prueba autonoma con ETABS (crear, analizar y guardar un modelo).

.EXAMPLE
    .\install.ps1 -SkipConfig
#>
[CmdletBinding()]
param(
    [string]$InstallDir = (Join-Path $env:LOCALAPPDATA 'etabs-mcp'),
    [string]$ConfigPath = (Join-Path $env:USERPROFILE '.config\opencode\opencode.json'),
    [string]$ExpectedSha256,
    [switch]$SkipConfig,
    [switch]$SkipSelfTest
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch { }

$Repo = 'david-mamani/etabs-mcp'
$DownloadUrl = "https://github.com/$Repo/releases/latest/download/etabs-mcp.exe"

function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Ok($msg) { Write-Host "    OK: $msg" -ForegroundColor Green }
function Warn($msg) { Write-Host "    WARN: $msg" -ForegroundColor Yellow }
function Fail($msg) { Write-Host "`nERROR: $msg" -ForegroundColor Red; exit 1 }

# ---- 1. Entorno --------------------------------------------------------------
Step "Verificando entorno"

if (-not $IsWindows -and $env:OS -ne 'Windows_NT') {
    Fail "Este servidor solo funciona en Windows (requiere ETABS + COM)."
}
if (-not [Environment]::Is64BitOperatingSystem) {
    Fail "Se requiere Windows de 64 bits."
}
Ok "Windows 64 bits"

$etabsDirs = @(Get-ChildItem 'C:\Program Files\Computers and Structures' -Directory -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -like 'ETABS*' })
if ($etabsDirs.Count -gt 0) {
    Ok "ETABS detectado: $($etabsDirs[0].FullName)"
} else {
    Warn "No encontre ETABS en 'C:\Program Files\Computers and Structures'. Instalalo antes de usar el MCP."
}

# ---- 2. Descarga -------------------------------------------------------------
Step "Descargando etabs-mcp.exe desde el ultimo release"

New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
# Limpia binarios viejos de reemplazos en caliente anteriores (si ya no estan en uso).
Get-ChildItem $InstallDir -Filter 'etabs-mcp.old-*.exe' -File -ErrorAction SilentlyContinue |
    ForEach-Object { try { Remove-Item $_.FullName -Force -ErrorAction Stop } catch { } }
$exePath = Join-Path $InstallDir 'etabs-mcp.exe'
$tmpPath = "$exePath.download"

try {
    Invoke-WebRequest -Uri $DownloadUrl -OutFile $tmpPath -UseBasicParsing
} catch {
    Fail "No pude descargar el ejecutable: $($_.Exception.Message)`nRevisa el release: https://github.com/$Repo/releases/latest"
}

$sizeMb = [math]::Round((Get-Item $tmpPath).Length / 1MB, 1)
if ($sizeMb -lt 20) {
    Remove-Item $tmpPath -Force -ErrorAction SilentlyContinue
    Fail "El archivo descargado pesa solo $sizeMb MB; la descarga esta incompleta."
}

if ($ExpectedSha256) {
    $actual = (Get-FileHash $tmpPath -Algorithm SHA256).Hash
    if ($actual -ne $ExpectedSha256.ToUpperInvariant()) {
        Remove-Item $tmpPath -Force -ErrorAction SilentlyContinue
        Fail "SHA256 no coincide.`n  esperado: $($ExpectedSha256.ToUpperInvariant())`n  obtenido: $actual"
    }
    Ok "SHA256 verificado"
}

# Reemplaza el binario. Si esta en uso, Windows no deja sobrescribirlo pero si
# renombrarlo: los procesos que ya corrian siguen usando el archivo renombrado.
$testExe = $exePath
$placed = $false
if (Test-Path $exePath) {
    try {
        Move-Item $tmpPath $exePath -Force -ErrorAction Stop
        $placed = $true
        Ok "$exePath ($sizeMb MB)"
    } catch {
        $oldExe = Join-Path $InstallDir ("etabs-mcp.old-{0}.exe" -f (Get-Date -Format 'yyyyMMdd-HHmmss'))
        try {
            Rename-Item $exePath $oldExe -Force -ErrorAction Stop
            Move-Item $tmpPath $exePath -Force -ErrorAction Stop
            $placed = $true
            Ok "Reemplazado en caliente; los procesos anteriores siguen usando $oldExe"
            Warn "Reinicia OpenCode para que cargue el binario nuevo."
        } catch {
            if (-not (Test-Path $exePath) -and (Test-Path $oldExe)) {
                try { Rename-Item $oldExe $exePath -Force -ErrorAction Stop } catch { }
            }
        }
    }
} else {
    Move-Item $tmpPath $exePath -Force -ErrorAction Stop
    $placed = $true
    Ok "$exePath ($sizeMb MB)"
}

if (-not $placed) {
    $testExe = Join-Path $InstallDir 'etabs-mcp.new.exe'
    Move-Item $tmpPath $testExe -Force
    Warn "No pude reemplazar etabs-mcp.exe; quedo como etabs-mcp.new.exe."
    Warn "Cierra OpenCode y renombra etabs-mcp.new.exe a etabs-mcp.exe."
}

# ---- 3. Prueba de arranque ---------------------------------------------------
Step "Probando el ejecutable"

try {
    $help = & $testExe --help 2>&1 | Out-String
} catch {
    Fail "El ejecutable no arranco: $($_.Exception.Message)"
}
if ($LASTEXITCODE -ne 0 -or $help -notmatch 'etabs-mcp') {
    Fail "El ejecutable no responde como se espera (exit $LASTEXITCODE)."
}
if ($help -match 'ETABS (version \S+ is below|21)') { }
Ok "Arranca correctamente"

# ---- 4. Configuracion de OpenCode -------------------------------------------
if ($SkipConfig) {
    Step "Configuracion omitida (-SkipConfig)"
} else {
    Step "Registrando el servidor MCP en opencode.json"

    $configExisted = Test-Path $ConfigPath
    if ($configExisted) {
        $backup = "$ConfigPath.bak-etabs-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
        Copy-Item $ConfigPath $backup -Force
        Ok "Respaldo: $backup"
        $raw = Get-Content $ConfigPath -Raw
    } else {
        New-Item -ItemType Directory -Path (Split-Path $ConfigPath) -Force | Out-Null
        $raw = ''
    }

    try {
        if ([string]::IsNullOrWhiteSpace($raw)) {
            $cfg = [pscustomobject]@{}
        } else {
            $cfg = $raw | ConvertFrom-Json
        }
    } catch {
        Fail "opencode.json no es JSON valido; no lo toco. Editalo a mano y vuelve a correr con -SkipConfig."
    }

    if (-not $cfg.PSObject.Properties['mcp']) {
        $cfg | Add-Member -NotePropertyName mcp -NotePropertyValue ([pscustomobject]@{})
    }

    # Soporta la forma documentada (mcp.etabs) y la forma anidada (mcp.servers.etabs).
    $container = $cfg.mcp
    if (-not $container.PSObject.Properties['etabs'] -and
        $container.PSObject.Properties['servers'] -and
        $container.servers.PSObject.Properties['etabs']) {
        $container = $container.servers
    }

    $entry = [pscustomobject]@{
        type    = 'local'
        command = @($exePath)
        enabled = $true
    }

    if ($container.PSObject.Properties['etabs']) {
        $container.etabs = $entry
        Ok "Servidor 'etabs' actualizado"
    } else {
        $container | Add-Member -NotePropertyName etabs -NotePropertyValue $entry
        Ok "Servidor 'etabs' agregado"
    }

    $json = $cfg | ConvertTo-Json -Depth 100
    [System.IO.File]::WriteAllText($ConfigPath, $json, (New-Object System.Text.UTF8Encoding($false)))
    Ok "Configuracion: $ConfigPath"
}

# ---- 5. Prueba autonoma con ETABS --------------------------------------------
$selfTestStatus = 'skipped'
$smokeFile = Join-Path $env:LOCALAPPDATA 'etabs-mcp\smoke-test\etabs-mcp-smoke.EDB'
if ($SkipSelfTest) {
    Step "Prueba autonoma omitida (-SkipSelfTest)"
} else {
    Step "Prueba autonoma: creando y analizando un modelo ETABS"
    Write-Host "    ETABS se abre y se cierra solo; normalmente tarda 1-3 minutos..." -ForegroundColor Gray

    $log = Join-Path $env:TEMP ("etabs-mcp-selftest-{0}.log" -f (Get-Date -Format 'yyyyMMdd-HHmmss'))
    $errLog = "$log.err"
    $proc = $null
    $started = $false
    $finished = $false
    try {
        $proc = Start-Process -FilePath $testExe -ArgumentList '--self-test' -NoNewWindow -PassThru `
            -RedirectStandardOutput $log -RedirectStandardError $errLog
        $started = $true
        $finished = $proc.WaitForExit(600000)
    } catch {
        Warn "No pude ejecutar la prueba: $($_.Exception.Message)"
    }

    if (-not $started) {
        $selfTestStatus = 'fail'
    } elseif (-not $finished) {
        if ($proc) { try { $proc.Kill() } catch { } }
        Warn "La prueba excedio 10 minutos y se cancelo."
        if (Test-Path $log) { Get-Content $log | Write-Host }
        if (Test-Path $errLog) { Get-Content $errLog | Write-Host }
        $selfTestStatus = 'fail'
    } else {
        if (Test-Path $log) { Get-Content $log | Write-Host }
        if (Test-Path $errLog) {
            $errText = Get-Content $errLog -Raw
            if (-not [string]::IsNullOrWhiteSpace($errText)) { Write-Host $errText -ForegroundColor DarkGray }
        }

        # WaitForExit(ms) no siempre publica ExitCode; la sobrecarga sin
        # argumentos refresca la informacion del proceso.
        try { $proc.WaitForExit() } catch { }
        $exitCode = $proc.ExitCode

        if ($exitCode -eq 0) {
            Ok "Prueba superada: el archivo ETABS se creo, analizo y guardo."
            $selfTestStatus = 'pass'
        } elseif ($exitCode -eq 2) {
            Warn "ETABS no esta instalado; la prueba se omitio. El MCP quedo instalado."
            $selfTestStatus = 'skip'
        } else {
            Warn "La prueba fallo (exit $exitCode). El MCP quedo instalado igual."
            $selfTestStatus = 'fail'
        }
    }

    Remove-Item $log, $errLog -Force -ErrorAction SilentlyContinue
}

# ---- 6. Resumen --------------------------------------------------------------
Step "Listo"
$selfTestText = if ($selfTestStatus -eq 'pass') {
    'PASS - modelo ETABS creado y analizado'
} elseif ($selfTestStatus -eq 'skip') {
    'SKIP - ETABS no instalado'
} elseif ($selfTestStatus -eq 'fail') {
    'FAIL - revisa el detalle de arriba'
} else {
    'omitida (-SkipSelfTest)'
}

Write-Host ""
Write-Host "  Ejecutable : $exePath"
if ($testExe -ne $exePath) { Write-Host "  Prueba con : $testExe" -ForegroundColor Yellow }
if (-not $SkipConfig) { Write-Host "  Config     : $ConfigPath" }
Write-Host "  Prueba     : $selfTestText"
if ($selfTestStatus -eq 'pass') { Write-Host "  Archivo    : $smokeFile" }
Write-Host ""
Write-Host "  Siguiente paso:" -ForegroundColor White
Write-Host "    1. Reinicia OpenCode (o el cliente MCP) para que cargue el servidor." -ForegroundColor Gray
Write-Host "    2. Abre ETABS con un modelo cargado." -ForegroundColor Gray
Write-Host "    3. Pide: 'llama a get_status' y luego 'llama a discover_api'." -ForegroundColor Gray
Write-Host ""
Write-Host "  Repo: https://github.com/$Repo" -ForegroundColor DarkGray
