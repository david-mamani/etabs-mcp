"""Shared model diagnostics and the installer's check of the exact test file."""

from __future__ import annotations

from pathlib import Path
from typing import Any

TIMEOUT_S = 20.0


def normalize_model_path(path: str | None) -> str | None:
    """ETABS also uses '(Untitled)' for a new, unsaved model."""
    if not path or path.strip().casefold() in {"untitled", "(untitled)"}:
        return None
    return path


def read_model_status(model: Any) -> dict[str, Any]:
    """Separate COM connectivity from a readable, loaded or unsaved model."""
    result: dict[str, Any] = {
        "connected": True,
        "etabs_version": "unknown",
        "model_path": None,
        "model_locked": None,
        "model_ready": False,
        "model_state": "unavailable",
        "model_empty": None,
    }
    try:
        version = model.GetVersion()
        result["etabs_version"] = str(version[0] if isinstance(version, (list, tuple)) else version)
        result["model_path"] = normalize_model_path(model.GetModelFilename(True))
        result["model_locked"] = bool(model.GetModelIsLocked())
        counts = [int(obj.Count()) for obj in (model.PointObj, model.FrameObj, model.AreaObj)]
        result["model_empty"] = not any(counts)
        result["model_ready"] = bool(result["model_path"] or any(counts))
        result["model_state"] = "loaded" if result["model_path"] else "unsaved" if any(counts) else "empty"
    except Exception as exc:
        result["model_error"] = str(exc)
    return result


def model_paths_match(actual: str | None, expected: str | Path) -> bool:
    """Accept ETABS's .$et working filename only in the expected folder."""
    actual = normalize_model_path(actual)
    if not actual:
        return False
    actual_path, expected_path = Path(actual).resolve(), Path(expected).resolve()
    if str(actual_path).casefold() == str(expected_path).casefold():
        return True
    return (
        actual_path.suffix.lower() == ".$et"
        and expected_path.suffix.lower() == ".edb"
        and str(actual_path.parent).casefold() == str(expected_path.parent).casefold()
        and actual_path.stem.casefold() == expected_path.stem.casefold()
    )


def run_status(pid: int | None = None, expected_model: str | None = None) -> int:
    """Require explicit selection when several ETABS processes are running."""
    from etabs_mcp.connection import InstanceRegistry, connect_and_run

    try:
        instances = InstanceRegistry().get_active_instances()
        matches = [i for i in instances if pid is None or i.pid == pid]
        if expected_model:
            matches = [i for i in matches if model_paths_match(i.file_path, expected_model)]
        if len(matches) != 1:
            available = ", ".join(f"PID {i.pid}: {i.file_path or i.model_state}" for i in instances)
            raise ValueError(f"Se esperaba una instancia inequívoca; encontradas {len(matches)}. {available}")
        target = matches[0]
        status = connect_and_run(read_model_status, target.pid, timeout=TIMEOUT_S)
        if not status["model_ready"]:
            raise ValueError(f"PID {target.pid}: modelo {status['model_state']}; {status.get('model_error', '')}")
        if expected_model and not model_paths_match(status["model_path"], expected_model):
            raise ValueError(f"PID {target.pid}: el archivo cambió; no coincide con {expected_model}")
    except Exception as exc:
        print(f"FAIL: el conector MCP no pudo validar ETABS: {exc}", flush=True)
        return 1
    print("OK: conector MCP conectado al modelo ETABS esperado", flush=True)
    print(f"    PID    : {target.pid}", flush=True)
    print(f"    ETABS  : {status['etabs_version']}", flush=True)
    print(f"    Modelo : {status['model_path'] or '(sin guardar)'}", flush=True)
    print(f"    Estado : {status['model_state']}; model_ready={status['model_ready']}", flush=True)
    return 0
