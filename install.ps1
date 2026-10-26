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
$mcpStatus = 'skipped'
$etabsText = 'sin abrir'
$mcpText = 'no comprobado'
$smokeFile = Join-Path $env:LOCALAPPDATA 'etabs-mcp\smoke-test\etabs-mcp-smoke.EDB'
if ($SkipSelfTest) {
    Step "Prueba autonoma omitida (-SkipSelfTest)"
} else {
    Step "Prueba autonoma: creando y analizando un modelo ETABS"
    Write-Host "    ETABS se abre y queda abierto; normalmente tarda 1-3 minutos..." -ForegroundColor Gray

    # El self-test deja ETABS abierto. Si heredara el stdout del instalador, el
    # pipe no se cerraria nunca; por eso se redirige a archivos con un wrapper
    # de cmd y solo se espera al propio cmd.
    $work = Join-Path $env:TEMP ("etabs-mcp-selftest-{0}" -f $PID)
    New-Item -ItemType Directory -Path $work -Force | Out-Null
    $log = Join-Path $work 'selftest.log'
    $runner = Join-Path $work 'run.cmd'
    $codeFile = Join-Path $work 'exit.txt'
    $reportFile = Join-Path $work 'selftest.json'

    $runnerText = @(
        '@echo off',
        ('cd /d "{0}"' -f (Split-Path -Parent $testExe)),
        ('"{0}" --self-test --self-test-report "{1}" > "{2}" 2>&1' -f $testExe, $reportFile, $log),
        ('> "{0}" echo %ERRORLEVEL%' -f $codeFile)
    ) -join "`r`n"
    [System.IO.File]::WriteAllText($runner, $runnerText, [System.Text.Encoding]::ASCII)

    $started = $false
    $finished = $false
    $exitCode = $null
    try {
        $psi = New-Object System.Diagnostics.ProcessStartInfo
        $psi.FileName = $env:ComSpec
        $psi.Arguments = '/c "{0}"' -f $runner
        $psi.UseShellExecute = $false
        $psi.CreateNoWindow = $true
        $proc = [System.Diagnostics.Process]::Start($psi)
        $started = $true
        $finished = $proc.WaitForExit(600000)
        if ($finished) { $exitCode = $proc.ExitCode }
    } catch {
        Warn "No pude ejecutar la prueba: $($_.Exception.Message)"
    }

    if (Test-Path $log) { Get-Content $log | Write-Host }

    $code = ''
    if (Test-Path $codeFile) { $code = [string](Get-Content $codeFile -Raw) }
    $code = $code.Trim()

    if (-not $started) {
        $selfTestStatus = 'fail'
    } elseif (-not $finished) {
        Warn "La prueba excedio 10 minutos y se cancelo."
        $selfTestStatus = 'fail'
    } elseif ($code -eq '0') {
        try {
            $testReport = Get-Content -LiteralPath $reportFile -Raw | ConvertFrom-Json
            if (-not $testReport.success -or -not $testReport.pid -or -not $testReport.model_path) {
                throw 'El informe no identifica el PID y el archivo de prueba.'
            }
            $smokeFile = [string]$testReport.model_path
            $smokePid = [int]$testReport.pid
            Ok "Prueba superada: el archivo ETABS se creo, analizo y guardo."
            $selfTestStatus = 'pass'
            $etabsText = 'abierto con el modelo de prueba'
        } catch {
            Warn "No pude validar el informe de la prueba: $($_.Exception.Message)"
            $selfTestStatus = 'fail'
        }
    } elseif ($code -eq '2') {
        Warn "ETABS no esta instalado; la prueba se omitio. El MCP quedo instalado."
        $selfTestStatus = 'skip'
        $etabsText = 'no instalado'
    } else {
        Warn "La prueba fallo (exit $exitCode). El MCP quedo instalado igual."
        $selfTestStatus = 'fail'
    }

    # No borrar rutas calculadas fuera de la carpeta temporal de esta prueba.
    $resolvedWork = [System.IO.Path]::GetFullPath($work)
    $expectedWork = [System.IO.Path]::GetFullPath((Join-Path $env:TEMP ("etabs-mcp-selftest-{0}" -f $PID)))
    if ($resolvedWork -eq $expectedWork) {
        Remove-Item -LiteralPath $resolvedWork -Recurse -Force -ErrorAction SilentlyContinue
    }
}

# ---- 6. Comprobacion del conector MCP ----------------------------------------
if ($selfTestStatus -eq 'pass') {
    Step "Comprobando el conector MCP contra ETABS"
    $statusOutput = & $testExe --status --pid $smokePid --expected-model $smokeFile 2>&1
    $statusExit = $LASTEXITCODE
    if ($statusOutput) { $statusOutput | Write-Host }
    if ($statusExit -eq 0) {
        Ok "El conector MCP habla con ETABS."
        $mcpStatus = 'pass'
        $mcpText = 'conectado a ETABS'
    } else {
        Warn "El conector MCP no pudo conectar con ETABS (exit $statusExit)."
        $mcpStatus = 'fail'
        $mcpText = 'sin conexion'
    }
}

# ---- 7. Resumen --------------------------------------------------------------
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
Write-Host "  MCP        : $mcpText"
Write-Host "  ETABS      : $etabsText"
Write-Host ""
Write-Host "  Siguiente paso:" -ForegroundColor White
Write-Host "    1. Reinicia OpenCode (o el cliente MCP) para que cargue el servidor." -ForegroundColor Gray
if ($selfTestStatus -eq 'pass') {
    Write-Host "    2. ETABS queda abierto con el modelo de prueba; no hace falta abrirlo." -ForegroundColor Gray
} else {
    Write-Host "    2. Abre ETABS con un modelo cargado." -ForegroundColor Gray
}
Write-Host "    3. Pide: 'llama a get_status' y luego 'llama a discover_api'." -ForegroundColor Gray
Write-Host ""
Write-Host "  Repo: https://github.com/$Repo" -ForegroundColor DarkGray
