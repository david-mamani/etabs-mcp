# Vistas desde la API

`render_model_view` genera dibujos PNG desde los datos de ETABS. No captura su ventana ni crea o distribuye ventanas nativas. No usa automatización del escritorio.

| Vista | Parámetros |
| --- | --- |
| 3D isométrica | `view="3d"` |
| Elevación frontal | `view="elevation"` |
| Planta del nivel 3 | `view="plan", story="Story3"` |

El cliente recibe la imagen PNG y sus metadatos: archivo, modelo, instancia, tipo de vista y `desktop_access=false`. Los archivos usan nombres únicos dentro de `Documents/ETABS-MCP/exports`.

`get_model_geometry` permite leer el mismo conjunto de puntos, barras, áreas, huecos y pisos. Conserva las unidades presentes, el bloqueo y los resultados de análisis. Los datos exportados se expresan en metros. Evita modificar el modelo simultáneamente mientras se lee su geometría.

Las vistas muestran líneas de barras y superficies de áreas. No representan espesores de secciones, texturas, armaduras, deformadas ni verificaciones de diseño. La planta corta los muros a media altura del piso y dibuja sus huecos. La vista sólida aplica ocultación por profundidad y triangula contornos poligonales. El renderizado no sustituye una revisión estructural.

## Referencia funcional revisada

La idea de generar vistas fuera de la ventana se contrastó con [Moha2005269/etabs-mcp v0.3.2](https://github.com/Moha2005269/etabs-mcp/tree/v0.3.2), publicado el 18 de octubre de 2026 bajo Apache-2.0. Esta implementación usa el puente COM existente del proyecto y un renderizador propio. No incorpora los módulos de capturas, menús, diálogos ni control de ventanas de aquel repositorio.

La base original del proyecto continúa siendo [Shekh-Muhsen/ETABS-MCP](https://github.com/Shekh-Muhsen/ETABS-MCP), bajo MIT.
