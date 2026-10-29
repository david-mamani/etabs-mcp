# Instalación

Necesitas Windows 10/11 de 64 bits, ETABS 21+ y un cliente MCP que se ejecute en el mismo equipo. No necesitas Python para usar el ejecutable.

## Ejecutable y skill

Descarga `etabs-mcp.exe` de la [última versión publicada](https://github.com/david-mamani/etabs-mcp/releases/latest). Guárdalo en `%LOCALAPPDATA%\etabs-mcp\etabs-mcp.exe`.

Instala la skill incluida desde PowerShell:

```powershell
& "$env:LOCALAPPDATA\etabs-mcp\etabs-mcp.exe" --install-skill
```

Se guarda en `%USERPROFILE%\.agents\skills\etabs-mcp-api`. Este comando no abre ETABS. Conserva una copia al actualizar una skill gestionada y no sobrescribe cambios locales. Puedes elegir otra carpeta con `--skill-dir`.

La skill prohíbe Computer Use durante toda tarea de ETABS, incluidos instalación, diagnóstico e imágenes. Es una instrucción para el asistente; no desactiva las herramientas generales del cliente.

## Codex

Usa un chat **local**. Si tienes instalada la línea de comandos de Codex, registra el servidor desde PowerShell:

```powershell
codex mcp add etabs -- "$env:LOCALAPPDATA\etabs-mcp\etabs-mcp.exe"
```

También puedes añadirlo a `%USERPROFILE%\.codex\config.toml`. Sustituye `TU_USUARIO` por tu usuario de Windows y conserva las demás entradas del archivo:

```toml
[mcp_servers.etabs]
command = 'C:\Users\TU_USUARIO\AppData\Local\etabs-mcp\etabs-mcp.exe'
startup_timeout_sec = 60
tool_timeout_sec = 300
```

Usa una sola entrada `mcp_servers.etabs`. Si ya existe, actualízala. Reconecta el cliente para cargar las herramientas nuevas.

Referencia: [configuración de MCP en Codex](https://learn.chatgpt.com/docs/extend/mcp).

## OpenCode

El instalador descarga el ejecutable, instala la skill y configura **OpenCode**:

```powershell
irm https://raw.githubusercontent.com/david-mamani/etabs-mcp/main/install.ps1 | iex
```

Guarda el ejecutable en `%LOCALAPPDATA%\etabs-mcp` y respalda `opencode.json` antes de modificarlo. También abre una instancia nueva de ETABS, crea y analiza un modelo de prueba y lo deja abierto.

Si ejecutas el script desde un archivo, `-SkipConfig` omite la configuración de OpenCode y `-SkipSelfTest` omite esa prueba. No requiere permisos de administrador. El script no configura Codex ni Claude.

Para registrar el ejecutable manualmente, incorpora esta entrada a `opencode.json`. Sustituye `TU_USUARIO` y conserva las demás entradas:

```json
{
  "mcp": {
    "etabs": {
      "type": "local",
      "command": ["C:\\Users\\TU_USUARIO\\AppData\\Local\\etabs-mcp\\etabs-mcp.exe"],
      "enabled": true,
      "timeout": 300000
    }
  }
}
```

Referencia: [servidores MCP de OpenCode](https://opencode.ai/docs/mcp-servers/).

## Claude Code

Con el ejecutable descargado, regístralo desde PowerShell:

```powershell
claude mcp add --transport stdio --scope user etabs -- "$env:LOCALAPPDATA\etabs-mcp\etabs-mcp.exe"
```

Referencia: [configuración de MCP en Claude Code](https://code.claude.com/docs/en/mcp).

## Claude Desktop

Descarga `etabs-mcp.mcpb` de [Releases](https://github.com/david-mamani/etabs-mcp/releases/latest) e instálalo como extensión local en Claude Desktop para Windows. El paquete incluye el servidor; la skill para agentes se instala por separado con `--install-skill` cuando el cliente admite esa carpeta de skills.

## Comprobar la conexión

Después de configurar el cliente, pide:

```text
Prepara ETABS desde el MCP y confirma la conexión, sin computer-use.
```

El agente debe llamar primero a `discover_api` y cargar `etabs-core` con `read_skills`. `prepare_session` puede abrir ETABS y preparar un modelo guardado. Para un proyecto nuevo, usa `new_model=True`; para uno existente, selecciona su alias con `list_instances`.

Desde PowerShell puedes consultar una instancia abierta:

```powershell
& "$env:LOCALAPPDATA\etabs-mcp\etabs-mcp.exe" --status
```

Si hay varias, especifica `--pid` y `--expected-model`. Si ETABS solicita autenticación, complétala tú. El asistente no debe automatizar ese diálogo.

## Desde el código fuente

Para desarrollo necesitas Python 3.11+:

```powershell
git clone https://github.com/david-mamani/etabs-mcp.git
cd etabs-mcp
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\etabs-mcp.exe
```

Ejecuta `build.ps1` para compilar el `.exe` y empaquetar el `.mcpb`. Consulta sus opciones en el [script de compilación](../build.ps1).
