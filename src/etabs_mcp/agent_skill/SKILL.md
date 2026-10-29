---
name: etabs-mcp-api
description: Crear, modificar, consultar y visualizar modelos de ETABS con el MCP etabs, exclusivamente mediante su API. Usar para tareas de ETABS, incluidos arranque, malla, pisos, cargas, análisis, resultados e imágenes. Nunca usar Computer Use.
---

# ETABS exclusivamente por API

## Prohibición absoluta

NUNCA uses Computer Use, computer-use, computeruse, Usar PC, control de pantalla ni automatización del escritorio. BAJO NINGUNA CIRCUNSTANCIA dentro de una tarea de ETABS, incluidos instalación, diagnóstico, vistas, capturas, verificación y recuperación de errores.

No uses `cua`, `sky`, control remoto, ratón, teclado, OCR, capturas del escritorio ni automatización de ventanas, menús o diálogos. No invoques estas funciones mediante otro MCP, una skill, scripts de Python o PowerShell, comandos, bibliotecas de Windows ni agentes delegados. Que la API falle o no tenga una función nunca habilita esas alternativas.

Si una operación no está disponible mediante la API, explica el límite y continúa con la parte compatible. No pidas permiso para usar Computer Use. Si ETABS requiere autenticación, informa al usuario para que la complete; nunca automatices ese diálogo. No instales un conector de escritorio como alternativa.

Esta regla aplica a todo el flujo de ETABS. La skill no desactiva herramientas generales del cliente y no debe presentarse como un bloqueo técnico global.

## Flujo de trabajo

- Usa el MCP `etabs`. Llama a `discover_api` y carga `etabs-core` con `read_skills`. Carga las guías específicas cuando sean necesarias.
- Si no aparece el MCP, comprueba su configuración con archivos o terminal. El ejecutable incluye su entorno. No instales Python global ni componentes ajenos. Tras instalarlo, ejecuta `etabs-mcp.exe --install-skill` y reconecta el cliente si necesita cargar herramientas nuevas.
- Para un edificio nuevo llama a `prepare_session(new_model=True, stories=...)`. Incluye alturas y malla cuando se conozcan. Usa el alias devuelto en las operaciones posteriores. No reinicies un archivo existente.
- Para un proyecto existente usa `list_instances`, selecciona el alias y verifica `get_status`. Si hay varias instancias, no elijas una por posición o por ventana activa.
- Descubre métodos con `search_docs`. Ejecuta el código de la API con `execute_code`, usando el objeto `model`. Comprueba los códigos de retorno, las unidades y los conteos. Guarda el resultado por la API.
- Usa `get_model_geometry` para leer geometría. Para imágenes usa `render_model_view` con `view="3d"`, `view="elevation"` o `view="plan", story="Story3"`. Son dibujos de los datos del modelo, no capturas de su ventana. No prometas abrir o distribuir ventanas nativas.
- No desbloquees un modelo con resultados salvo que sea necesario para una modificación autorizada: desbloquear elimina resultados. Distingue un ejemplo conceptual de un modelo analizado o diseñado.

## Ejemplo

«Crea un modelo nuevo de cinco niveles, con recepción en el primero y tres departamentos de dos dormitorios por piso del 2 al 5. Incluye balcones y núcleo de ascensor. Entrega vistas 3D y planta del nivel 3 mediante el MCP».

Resuelve la geometría y las imágenes con las herramientas anteriores. Si algo no está soportado, informa qué falta sin recurrir al escritorio.
