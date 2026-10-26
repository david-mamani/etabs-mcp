# Changelog

## [2.2.2] — 2026-10-25

### Fixed
- El ejecutable compilado limpia temporalmente la búsqueda de bibliotecas de PyInstaller al abrir programas de Windows. ETABS ya no hereda las DLL privadas del servidor durante la prueba.
- La prueba inicial usa un único hilo con COM inicializado de forma explícita. El diagnóstico trata `(Untitled)` como un modelo sin guardar y no como un archivo cargado.
- Descubrimiento de todos los procesos reales de ETABS. El servidor MCP ya no se confunde con `ETABS.exe`. La enumeración usa nombres exactos y handles de Windows de 64 bits.
- Conexión estricta al PID seleccionado con `GetObjectProcess`. Una instancia no disponible no cambia el destino a otra ventana. Los alias cerrados no se reutilizan.
- Diagnóstico con PID, `connected`, `model_ready`, `model_empty` y `model_state`. El instalador comprueba el mismo PID y archivo que creó la prueba, incluido un nombre de archivo alternativo.
- Recuperación del reinicio por autenticación durante el arranque de la prueba. Solo se acepta una instancia nueva y vacía; nunca se inicializa un modelo existente.
- Las 16 barras de la prueba reciben la sección `SMOKE`. Se confirma cada asignación con `GetSection` antes del análisis.
- Índice regenerado desde la biblioteca de tipos de ETABS 23.3.2. Conserva los parámetros de entrada/salida y su orden. La documentación CHM solo aporta descripciones y no sustituye las firmas oficiales.
- Valores de losas y muros corregidos: `ShellThin=1`, `ShellThick=2`, `Membrane=3`, `Specified=1`. Ejemplos de creación de barras y áreas corregidos en todas las guías afectadas.

### Added
- Referencias de contratos y enumeraciones generadas con versión y SHA-256 de la biblioteca de tipos.
- Pruebas de regresión de selección, diagnóstico, autenticación, secciones y llamadas MCP. Comprobaciones automáticas en Windows y Linux.


## [2.2.1] — 2026-10-25

### Changed
- **El self-test deja ETABS abierto** con el modelo de prueba (2 pisos, 12 nodos, 16 frames) desbloqueado y visible, en vez de cerrarlo. La instalación encadena directamente con el uso del MCP.
- `install.ps1` ejecuta el self-test con un wrapper de `cmd` y salida a archivos: el instalador ya no espera a que ETABS cierre el pipe y termina en cuanto acaba la prueba.
- Nueva comprobación `etabs-mcp.exe --status`: se conecta al ETABS abierto por el mismo camino que el servidor MCP e informa versión, archivo y estado del modelo. El instalador la ejecuta tras el `PASS` y muestra las líneas `MCP` y `ETABS` en el resumen.

### Fixed
- ETABS 23 podía aceptar `InitializeNewModel` antes de estar listo y luego fallar `File.NewGridOnly` con `Invalid pointer`. Ahora ambas llamadas se reintentan juntas, como exige el arranque real de ETABS.
- La prueba detecta si ETABS se cierra durante la creación del modelo en vez de esperar el timeout completo.
- La versión de ETABS se lee después de crear el modelo (al arrancar, `GetVersion` devuelve una cadena vacía).

---

## [2.2.0] — 2026-10-25

### Added
- **Prueba automática de instalación** (`etabs-mcp.exe --self-test`): abre una instancia nueva de ETABS, crea un modelo de 2 pisos y 6 × 6 m (12 nodos, 16 frames), aplica carga vertical y horizontal, corre el análisis, verifica las reacciones en la base, guarda `%LOCALAPPDATA%\etabs-mcp\smoke-test\etabs-mcp-smoke.EDB` y cierra ETABS al terminar. No requiere Python ni pasos manuales.
- `install.ps1` ejecuta la prueba automáticamente al finalizar y muestra `PASS`, `FAIL` o `SKIP` con la ruta del archivo creado. Se puede omitir con `-SkipSelfTest`.

### Fixed
- La creación de un modelo desde cero ahora llama a `SapModel.InitializeNewModel` antes de `File.NewGridOnly`. Sin esa llamada, ETABS 23 devolvía `Invalid pointer` al crear un modelo desde la Start Page.
- `install.ps1` reemplaza el `.exe` en caliente cuando hay procesos `etabs-mcp` en ejecución: renombra el binario anterior y deja el nuevo en `etabs-mcp.exe`, en vez de dejarlo como `etabs-mcp.new.exe`.

### Changed
- Versión 2.2.0 en `version.py`, `pyproject.toml` y el manifiesto `.mcpb`.
- README actualizado con la prueba automática y el prompt de instalación por IA en dos líneas.
- `mcpb/manifest.json`: descripción larga corregida (estaba doblemente codificada).

---

## [2.1.1] — 2026-10-25

Fork mantenido por David Mamani, basado en [Shekh-Muhsen/ETABS-MCP](https://github.com/Shekh-Muhsen/ETABS-MCP) v2.1.0.

### Fixed
- **Compatibilidad con `mcp >= 2.3`** — la librería renombró `McpError` a `MCPError` y el import directo rompía el arranque del servidor (`ImportError: cannot import name 'McpError'`). Ahora se importa `MCPError` con fallback a `McpError` en `src/etabs_mcp/file_io/helpers.py`.

### Changed
- `fastmcp` pasa de `>=3.2.3,<4` a `>=3.2.3,<5` (el binario se compila y prueba con fastmcp 4.1.0).
- README reescrito en español con instalación para OpenCode, Claude Desktop y desde código fuente.
- Se deja de versionar `etabs-mcp.mcpb` y los `__pycache__/*.pyc`; los binarios se publican como *assets* del release.
- Se agrega `LICENSE` con la atribución MIT del proyecto original.

---

## [2.1.0] - 2026-07-11

### Added — Load Setup & Combination Skills (from plugin source)

Two new workflow skills derived from the `Form1.Tab.LoadSetup.cs` and `Form1.Tab.ComboEditor.cs` plugin tabs:

- **`workflow-load-patterns-cases`** — Idempotent setup of all standard load patterns and cases: gravity SuperDead patterns (PWL, FF), Live patterns (Live_G, Live_W, LLR), seismic ELF auto patterns EX/EY (ASCE 7-05), wind auto patterns WX/WY (ASCE 7-05), composite cases DL (all Dead+SuperDead) and LLA (all Live), RS function (ASCE 7-05 design spectrum from SDS/SD1/TL), RS cases Spec X / Spec Y, modal Eigen case, and Ev = 0.2·SDS × all Dead patterns. Only creates items not already present.
- **`workflow-load-combinations`** — Full ASCE 7-05 / BNBC 2020 combination library (68 combos) with role mapping (D→DL, L→LLA, Lr→LLR, Ex→EX, Ey→EY, Ev→Ev, Wx→WX, Wy→WY, Sx→Spec X, Sy→Spec Y): SLS gravity (3 combos), SLS wind (8 combos), SLS seismic ELF ±Ev (24 combos), ULS gravity (2), ULS wind (8), ULS seismic RS 100%+30% ±Ev (16), ULS seismic ELF 100%+30% ±Ev (16). Includes helpers for reading, editing, and bulk-adding cases to combos via `RespCombo` API.

### Changed
- Version bumped to 2.1.0.
- README skill table updated with v2.1 load setup section.

---

## [2.0.0] - 2026-07-11

### Added — Code Check Workflow Skills (from plugin source)

Six new workflow skills derived from the CSiNET8PluginMhn plugin source, covering BNBC 2020 / ASCE 7 structural code checks:

- **`workflow-drift-check-bnbc`** — Dual-method inter-story drift check: ETABS `StoryDrifts` API result and independent hand calc `(δᵢ − δ_below) / h`. Applies Cd/I amplification and compares against BNBC 2020 Table 6.2.21 / ASCE 7-05 Table 12.12-1 occupancy-based allowables.
- **`workflow-irregularity-check`** — Vertical irregularity checks from the `Story Stiffness` and `Centers Of Mass And Rigidity` database tables: soft story (K < 0.7·K_above or K < 0.8·avg3; extreme at 0.6/0.7 thresholds), mass irregularity (m > 1.5×adjacent story, roof exempt), and CM-CR eccentricity per diaphragm.
- **`workflow-otm-check`** — Overturning moment hand calculation per BNBC 2020 §2.5.7: reads story heights from the model, computes T = Ct·hn^m, spectral Sa, base shear V = Sa/(R/I)·W, vertical distribution exponent k, story lateral forces Fx, and cumulative OTMx. Foundation OTM = 0.75×base OTM check vs 2/3·M_DL.
- **`workflow-story-forces`** — Reads the `Story Forces` results table (Location = Bottom) for story P, VX, VY, T, MX, MY in kN/kN·m with unit conversion from lb_in_F. Appends foundation OTM = 0.75×MX/MY columns.
- **`workflow-mass-participation`** — Extracts `ModalParticipatingMassRatios` for a selected modal case, reports per-mode periods and UX/UY/UZ/RX/RY/RZ cumulative sums, compares modes defined vs actually run, and flags whether the ASCE 7 / BNBC 2020 90% mass participation requirement is met.
- **`workflow-torsion-check`** — Torsional irregularity check using user-specified edge joint pairs: reads `JointDispl` for each pair, computes δ_max/δ_avg per story, classifies Type 1a (>1.2) / Type 1b (>1.4) per ASCE 7-16 §12.3.2.1, and computes Ax = min(3.0, (δ_max/(1.2·δ_avg))²).

### Changed
- Version bumped to 2.0.0 across `version.py`, `pyproject.toml`, `mcpb/manifest.json`, and `README.md`.
- README skill table updated with new v2.0 skills section.

---

## [1.0.0] - 2026-06-01

### Added
- Initial release: geometry, loads, analysis, results, and design skills.
