"""Connection check used by ``etabs-mcp --status``.

It attaches to a running ETABS instance through the same code path used by the
MCP tools (``etabs_mcp.connection.connect_and_run``), so an ``OK`` here means
the server can talk to ETABS.

Exit codes: ``0`` = connected, ``1`` = could not connect.
"""

from __future__ import annotations

from typing import Any

TIMEOUT_S = 20.0


def _read_status(model: Any) -> dict[str, Any]:
    try:
        raw_version = model.GetVersion()
        if isinstance(raw_version, (list, tuple)):
            version = str(raw_version[0]) if raw_version else "desconocida"
        else:
            version = str(raw_version)
    except Exception:
        version = "desconocida"

    try:
        path = model.GetModelFilename(True)
    except Exception:
        path = None

    try:
        locked = bool(model.GetModelIsLocked())
    except Exception:
        locked = False

    return {"version": version, "path": path, "locked": locked}


def run_status() -> int:
    """Print the MCP connection status and return an exit code (0/1)."""
    from etabs_mcp.connection import connect_and_run

    try:
        status = connect_and_run(_read_status, timeout=TIMEOUT_S)
    except Exception as exc:
        print(f"FAIL: el conector MCP no pudo hablar con ETABS: {exc}", flush=True)
        return 1

    print("OK: conector MCP conectado a ETABS", flush=True)
    print(f"    ETABS  : {status['version']}", flush=True)
    print(f"    Modelo : {status['path'] or '(sin guardar)'}", flush=True)
    state = "bloqueado (analizado)" if status["locked"] else "editable"
    print(f"    Estado : {state}", flush=True)
    return 0
