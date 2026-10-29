# ETABS MCP

Conecta tu asistente de IA con **CSI ETABS** mediante MCP (Model Context Protocol). Crea, modifica y consulta modelos con instrucciones en lenguaje natural, a través de su interfaz de programación (API).

[Descargar](https://github.com/david-mamani/etabs-mcp/releases/latest) · [Instalación](docs/instalacion.md) · [Cambios](CHANGELOG.md)

## Requisitos

- Windows 10 u 11 de 64 bits.
- ETABS 21 o superior instalado.
- Un cliente MCP local: Codex, OpenCode, Claude Code o Claude Desktop.

El ejecutable incluye su entorno de Python. No necesitas instalar Python por separado.

## Instalar con un prompt

Pega esto en un chat **local de Codex**, con acceso a archivos y terminal:

```text
Instala o actualiza el conector MCP de ETABS y su skill en Codex:
https://github.com/david-mamani/etabs-mcp
Configúralo con el .exe de la última versión publicada y verifica la conexión.
Usa archivos, terminal y la API de ETABS. Nunca uses computer-use.
```

La skill `etabs-mcp-api` guía al asistente para trabajar solo por API. El [manual de instalación](docs/instalacion.md) explica cómo instalarla y configurar cada cliente.

## Qué puedes hacer

- Crear modelos con pisos, alturas y malla de ejes.
- Modificar geometría, materiales, secciones, cargas y combinaciones.
- Ejecutar análisis y consultar resultados y diseño.
- Obtener imágenes 3D, de elevación y de planta desde los datos del modelo.

## Ejemplo

Después de instalar el MCP:

```text
Crea un modelo conceptual nuevo de 5 niveles: recepción y oficinas en el primero;
del 2 al 5, tres departamentos por piso con dos dormitorios y balcón cada uno.
Incluye el núcleo y hueco del ascensor. Entrega vistas 3D y planta del nivel 3.
Usa solo el MCP de ETABS, sin computer-use.
```

El MCP puede abrir ETABS y preparar un archivo nuevo mediante `prepare_session`. No necesitas cargar una plantilla de antemano.

## Trabajo por API

El MCP y su skill prohíben Computer Use en todo el flujo de ETABS. Las imágenes son dibujos de la geometría del modelo; no son capturas de su ventana. Consulta el [alcance de las vistas](docs/vistas-api.md).

Las operaciones dependen de lo que exponga la API de tu versión de ETABS. Un modelo conceptual requiere revisión y cálculo antes de usarse como proyecto estructural.

## Desarrollo y créditos

[Código y compilación](docs/instalacion.md#desde-el-código-fuente) · [Skill incluida](src/etabs_mcp/agent_skill/SKILL.md) · [Licencia MIT](LICENSE)

Mantenido por **David Mamani**. Basado en [Shekh-Muhsen/ETABS-MCP](https://github.com/Shekh-Muhsen/ETABS-MCP). Proyecto independiente, sin afiliación con CSI.
