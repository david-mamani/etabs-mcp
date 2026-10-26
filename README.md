# ETABS MCP Server

[![Version](https://img.shields.io/badge/version-2.2.2-blue)](./CHANGELOG.md)
[![ETABS](https://img.shields.io/badge/ETABS-21%2B-orange)](#requisitos)
[![Platform](https://img.shields.io/badge/plataforma-Windows-lightgrey)](#requisitos)
[![License](https://img.shields.io/badge/licencia-MIT-green)](./LICENSE)

> Servidor [MCP](https://modelcontextprotocol.io) que conecta un agente (OpenCode, Claude Desktop, Claude Code, Cursor…) con un modelo **CSI ETABS** abierto, usando la API COM oficial.

Le hablas en lenguaje natural y el agente busca el método correcto de la API, escribe el código, lo ejecuta contra tu modelo y te devuelve los resultados. Sin scripts, sin buscar firmas de métodos a mano.

Este repositorio es un fork mantenido de [Shekh-Muhsen/ETABS-MCP](https://github.com/Shekh-Muhsen/ETABS-MCP) v2.1.0 (MIT), con el arreglo de compatibilidad para `mcp >= 2.3` y documentación de instalación en español. Ver [créditos](#créditos).

---

## Qué puedes pedirle

| Área | Ejemplo de prompt |
|---|---|
| Sismo | *"Verifica la participación de masa modal, ¿cumple el 90%?"* |
| Derivas | *"Extrae las derivas de entrepiso para los combos sísmicos y marca lo que pase h/400"* |
| Cortante basal | *"¿Cuál es el cortante basal en EQX y EQY?"* |
| Resultados | *"Corre el análisis y saca las fuerzas de los piers en todos los pisos"* |
| Diseño | *"Corre el diseño de concreto y muéstrame los DCR más desfavorables"* |
| Espectro | *"Escala el espectro por 0.85 del cortante estático y vuelve a correr"* |
| Auditoría | *"Lista todas las secciones de frame usadas y sus asignaciones"* |

---

## Requisitos

| Requisito | Versión |
|---|---|
| Sistema operativo | Windows 10/11 (64 bits) |
| CSI ETABS | **21.0 o superior**, instalado. El MCP puede abrirlo y preparar un archivo nuevo |
| Cliente MCP | OpenCode, Claude Desktop, Claude Code, Cursor, etc. |
| Python (solo si compilas) | 3.11+ |

> No necesitas abrir ETABS ni cargar un archivo de antemano. `prepare_session` abre ETABS y prepara un modelo guardado mediante su API COM, sin ratón, teclado ni `computer-use`. Si ETABS solicita iniciar sesión, el usuario completa esa autenticación.
>
> La conexión es por COM (`ETABSv1`), así que no funciona en Linux/macOS ni sin ETABS instalado.

---

## Instalación

### Instalación automática (recomendada, un comando)

```powershell
irm https://raw.githubusercontent.com/david-mamani/etabs-mcp/main/install.ps1 | iex
```

Qué hace:

1. Descarga `etabs-mcp.exe` del último release.
2. Lo deja en `%LOCALAPPDATA%\etabs-mcp\etabs-mcp.exe` (si el binario está en uso, lo reemplaza en caliente).
3. Respalda tu `opencode.json` y registra el servidor `etabs` (si ya existe, solo actualiza la ruta).
4. Prueba que el ejecutable arranque.
5. **Ejecuta una prueba real con ETABS**: abre ETABS, crea un archivo `.EDB`, lo analiza y lo guarda.
6. **Comprueba el conector MCP** (`etabs-mcp.exe --status`) contra ese ETABS y lo deja abierto.

No requiere permisos de administrador. Opciones: `-InstallDir`, `-ConfigPath`, `-SkipConfig`, `-SkipSelfTest` y `-ExpectedSha256` (si lo corres desde un archivo en vez de `irm`).

### Prueba automática de instalación

Al terminar, el instalador ejecuta `etabs-mcp.exe --self-test`. Esa prueba:

- Abre una instancia **nueva** de ETABS (no toca un modelo que ya tengas abierto).
- Crea un modelo de 2 pisos, 12 nodos y 16 frames (columnas y vigas de 6 × 6 m).
- Asigna la sección rectangular `SMOKE` de 0.40 × 0.40 m a las 16 barras y confirma cada asignación.
- Aplica una carga vertical y una horizontal, corre el análisis y verifica las reacciones en la base.
- Guarda el archivo en `%LOCALAPPDATA%\etabs-mcp\smoke-test\etabs-mcp-smoke.EDB`.
- Deja ETABS abierto con el modelo de prueba.

Después ejecuta `etabs-mcp.exe --status --pid <PID> --expected-model <archivo>`, con los datos del informe JSON de la prueba. Comprueba el mismo proceso y archivo por el camino que usa el servidor MCP. El resultado se imprime como `PASS`, `FAIL` o `SKIP` (si ETABS no está instalado), con una línea `MCP` y otra `ETABS`. El archivo de prueba queda en disco para que lo abras cuando quieras. También puedes correrlo a mano:

```powershell
& "$env:LOCALAPPDATA\etabs-mcp\etabs-mcp.exe" --self-test
& "$env:LOCALAPPDATA\etabs-mcp\etabs-mcp.exe" --status
```

La prueba necesita ETABS 21+ instalado y una licencia disponible. No requiere Python ni permisos de administrador.

### Que lo instale una IA

Pega esto en OpenCode, Claude Code o cualquier agente con terminal:

```text
Instala este MCP en mi PC: https://github.com/david-mamani/etabs-mcp
Cuando termine, deja ETABS abierto y pregúntame qué quiero modelar.
```

El agente corre el instalador, que descarga el `.exe`, respalda el `opencode.json`, registra el servidor, abre ETABS, crea y analiza el modelo de prueba, comprueba el conector MCP y deja ETABS abierto. No hay pasos manuales.

El flujo completo es:

1. Das el prompt.
2. Se instala el MCP.
3. Se abre ETABS y se comprueba el conector MCP.
4. ETABS se queda abierto y el agente escribe en el chat:

```text
pásame una imagen de lo que quieras modelar y lo modelaré en ETABS
```

### Opción A — Claude Desktop (un solo archivo)

1. Descarga `etabs-mcp.mcpb` desde [Releases](../../releases/latest).
2. Abre Claude Desktop → **Settings** → **Extensions**.
3. Arrastra el `.mcpb` sobre la ventana (o "Install from file").
4. Abre ETABS con un modelo.
5. Nueva conversación: pídele que llame a `discover_api`.

### Opción B — OpenCode / cualquier cliente con config JSON

Descarga `etabs-mcp.exe` del [release](../../releases/latest) y guárdalo, por ejemplo, en `C:\Tools\etabs-mcp\etabs-mcp.exe`.

En `opencode.json` (global: `~/.config/opencode/opencode.json`):

```json
{
  "mcp": {
    "etabs": {
      "type": "local",
      "command": ["C:\\Tools\\etabs-mcp\\etabs-mcp.exe"],
      "enabled": true
    }
  }
}
```

En Claude Desktop (`claude_desktop_config.json`), el mismo binario en modo `stdio`:

```json
{
  "mcpServers": {
    "etabs": {
      "command": "C:\\Tools\\etabs-mcp\\etabs-mcp.exe",
      "args": []
    }
  }
}
```

Reinicia el cliente y llama a `list_instances`. Si hay varias ventanas, pasa el alias elegido a `get_status` y `execute_code`. Cada alias conserva su PID; una conexión fallida nunca selecciona otra ventana.

### Opción C — Desde código fuente

```powershell
git clone https://github.com/david-mamani/etabs-mcp.git
cd etabs-mcp
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .

# stdio (clientes MCP)
etabs-mcp

# HTTP local con bearer token (opcional)
etabs-mcp --transport http --port 18121 --token <tu-token>

# Prueba automatica con ETABS (crea, analiza y guarda un modelo, deja ETABS abierto)
etabs-mcp --self-test

# Comprueba que el MCP puede hablar con el ETABS abierto
etabs-mcp --status
```

### Limitar acceso a archivos

Por defecto el servidor solo puede leer/escribir en los directorios que le permitas explícitamente con `--allowed-dirs`:

```powershell
etabs-mcp --allowed-dirs "C:\Proyectos\Edificio" "C:\Export"
```

---

## Herramientas expuestas

| Herramienta | Qué hace |
|---|---|
| `discover_api` | Lista las skills disponibles. **Llamar primero.** |
| `read_skills` | Carga una o más skills por nombre (guía paso a paso). |
| `list_instances` | Instancias de ETABS abiertas (alias, PID, archivo, versión). |
| `get_status` | Estado de la conexión, versión de ETABS, si el modelo está bloqueado, versión del servidor. |
| `prepare_session` | Abre ETABS y prepara un modelo. `new_model=True` crea otra instancia y un archivo único; `stories` y los parámetros de malla definen los pisos y ejes. |
| `execute_code` | Ejecuta Python en un sandbox contra el `SapModel` conectado. |
| `search_docs` | Búsqueda BM25 sobre **1 407 métodos y propiedades** de la API de ETABS. |

**Sandbox:** el código corre restringido — sin `import`, sin acceso al sistema de archivos, sin shell. Solo están disponibles `model` (`SapModel`), `json` y `math`. Todo el procesamiento es local; no se envía nada a la nube.

### Empezar sin controlar el escritorio

Para crear un edificio, llama primero a `prepare_session(new_model=True, stories=5)` y usa el `alias` devuelto en `execute_code`. Puedes indicar alturas, número de ejes y separaciones en metros. El archivo se guarda en una carpeta única dentro de `%USERPROFILE%\Documents\ETABS-MCP`; no hace falta copiar una plantilla fija.

Con `new_model=False`, un archivo cargado o un modelo con geometría se conserva. Una sesión vacía y sin guardar recibe un modelo nuevo. La herramienta no inicializa un archivo existente, aunque esté vacío.

La API COM de ETABS 23.3.2 solo expone `RefreshView` y `RefreshWindow` para las vistas. Crear ventanas de planta/elevación y distribuirlas con ChatGPT es una operación manual. El MCP informa este límite y no recurre a `computer-use`. Organizar la pantalla no es necesario para crear, modificar o analizar el modelo.

---

## Skills incluidas (47)

| Familia | Contenido |
|---|---|
| Núcleo ETABS | `etabs-core`, `etabs-geometry`, `etabs-sections`, `etabs-materials`, `etabs-loads`, `etabs-analysis`, `etabs-results`, `etabs-design`, `etabs-errors` |
| Tablas y utilidades | `etabs-database-tables` (165 tablas), `etabs-rs-function`, `etabs-truss-types`, `etabs-auto-seismic` |
| Flujos de trabajo | `workflow-seismic-check`, `workflow-modal-report`, `workflow-mass-participation`, `workflow-story-drift`, `workflow-base-shear`, `workflow-frame-forces`, `workflow-story-forces`, `workflow-gravity-report`, `workflow-design-concrete`, `workflow-model-audit`, `workflow-modifiers`, `workflow-rs-scaling`, `workflow-torsion-check`, `workflow-irregularity-check`, `workflow-otm-check`, `workflow-drift-check-bnbc`, `workflow-steel-roof-truss`, `workflow-tool-test` |
| Cargas y combos | `workflow-load-patterns-cases`, `workflow-load-combinations` |
| Normas | `ibc2012-de10-dual-system`, `ibc2012-de11-diaphragm-class`, `ibc2012-de12-torsion-irreg`, `ibc2012-de13-reentrant-corner`, `ibc2012-de14-diaphragm-discont`, `ibc2012-de18-soft-story`, `ibc2012-de33-force-distribution`, `ibc2012-de34-torsion-amplification`, `ibc2012-de35-story-drift`, `ibc2012-de36-pdelta`, `ibc2012-seismic-params`, `bnbc2020-seismic-params`, `bnbc2020-load-combinations` |

### Agregar skills sin recompilar

Crea una carpeta `etabs_skills\` junto al `.exe`, con una subcarpeta por skill y un `SKILL.md` dentro:

```
etabs-mcp.exe
etabs_skills\
  mi-skill\
    SKILL.md
```

```markdown
---
name: mi-skill
description: Qué hace, en una línea
---

# Mi Skill

...instrucciones para el agente...
```

El servidor reescanea esa carpeta en cada `discover_api`.

---

## Compilar el ejecutable

```powershell
pip install pyinstaller rank_bm25 numpy fastmcp uvicorn comtypes pywin32 packaging

# exe -> dist\etabs-mcp.exe  y  paquete -> etabs-mcp.mcpb
.\build.ps1

# Solo empaquetar (reusa dist\etabs-mcp.exe)
.\build.ps1 -SkipPyInstaller

# Compilar y copiar a la extensión de Claude Desktop
.\build.ps1 -Install
```

La versión del `manifest.json` se sincroniza automáticamente desde `src/etabs_mcp/version.py` (`SERVER_VERSION`).

### Estructura

```
src/etabs_mcp/
  main.py                  ← entrada CLI (stdio / http)
  server.py                ← herramientas MCP, búsqueda BM25, lifespan
  version.py               ← SERVER_VERSION (fuente única)
  connection.py            ← conexión COM, InstanceRegistry
  skills.py                ← SkillsManager (descubrimiento + sidecar)
  sandbox/executor.py      ← sandbox de Python restringido
  file_io/                 ← helpers CSV/XLSX y validación de rutas
  etabs_skills/            ← 47 skills en Markdown
  etabs_api_index.json     ← índice de 1 407 métodos y propiedades de la API
mcpb/
  etabs-mcp.spec           ← spec de PyInstaller
  manifest.json            ← manifiesto del paquete .mcpb
build.ps1                  ← build + empaquetado en un comando
install.ps1                ← instalador de un comando (exe + opencode.json)
build_api_index.py         ← índice desde el CHM de ETABS
build_api_index_typelib.py ← índice desde ETABSv1.tlb
```

### Regenerar el índice de la API

```powershell
py build_api_index_typelib.py --merge
.\build.ps1 -SkipPyInstaller
```

---

## Limitaciones conocidas

| Problema | Solución |
|---|---|
| `SetTableForEditingArray` — comtypes no pasa listas de Python como SAFEARRAY ByRef | Usar los métodos del modelo (p. ej. `model.FrameObj.SetSection()`) |
| Resultados tras abrir un `.edb` — el modelo queda bloqueado y sin resultados en memoria | Desbloquear → fijar flags → `RunAnalysis()` |
| `StepNum` en modal Ritz devuelve 0 | Usar el índice de fila `i+1` como número de modo |
| Modelos grandes analizados desde MCP | Correr el análisis desde la UI de ETABS (F5); COM puede expirar |
| `SetModelIsLocked(False)` borra los resultados | No desbloquear entre `RunAnalysis()` y la lectura de resultados |

---

## Créditos

- Proyecto original: [Shekh-Muhsen/ETABS-MCP](https://github.com/Shekh-Muhsen/ETABS-MCP) (MIT).
- Este fork: David Mamani — agrega compatibilidad con `mcp >= 2.3`, limpia artefactos del repo y documenta la instalación en español.
- CSI, ETABS y SAP2000 son marcas de Computers & Structures, Inc. Este proyecto no está afiliado ni respaldado por CSI.

## Licencia

MIT — ver [LICENSE](./LICENSE). Puedes usarlo, modificarlo y redistribuirlo conservando el aviso de copyright original.

## Diagnóstico y contratos de la API

`list_instances` enumera los PID reales, incluidos procesos en la pantalla de inicio o sin conexión disponible. No incluye `etabs-mcp.exe`.

`get_status` devuelve `connected` para la conexión COM y `model_ready` para un modelo legible con archivo o geometría. `model_state` distingue `loaded`, `unsaved`, `empty` y `unavailable`. `model_empty` indica si no hay nodos, barras ni áreas. Un modelo vacío puede recibir instrucciones de creación mediante `execute_code`; el diagnóstico no bloquea esa operación.

Con varias instancias, selecciona un alias explícito. Desde la terminal puedes usar `etabs-mcp --status --pid 1234 --expected-model "C:\Proyectos\modelo.edb"`. El archivo de trabajo `.$et` se acepta solo si coincide la carpeta y el nombre del `.edb` esperado.

La prueba inicial espera hasta cinco minutos si ETABS solicita iniciar sesión. Completa la autenticación manualmente. Si ETABS se reinicia, la prueba solo acepta un nuevo proceso vacío y falla si la selección es ambigua.

El índice y los valores de enumeración se validaron con la biblioteca de tipos de ETABS **23.3.2**. Para regenerarlos y actualizar las referencias de las guías:

```powershell
python build_api_index_typelib.py --etabs-version 23.3.2 --update-guides
```

El archivo `src/etabs_mcp/etabs_api_metadata.json` registra la versión, el SHA-256 de la biblioteca y las enumeraciones. Los parámetros `inout` aparecen tanto en los argumentos como en la respuesta. En creación de barras o áreas, conserva `Name=""` antes de `PropName`. `AreaObj.AddByCoord` devuelve las coordenadas, el nombre del área y el código de retorno: usa `respuesta[-2]` para el nombre.
