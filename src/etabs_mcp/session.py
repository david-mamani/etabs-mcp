"""Prepare a usable ETABS session through COM, without desktop automation."""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from etabs_mcp.connection import InstanceRegistry, _find_etabs_pids, connect_and_run
from etabs_mcp.selftest import _attach_etabs, _external_program_environment, _launch_etabs, find_etabs_exe
from etabs_mcp.status import model_paths_match, read_model_status
from etabs_mcp.version import SERVER_VERSION, check_version_warning

_SESSION_LOCK = threading.Lock()
_MODEL_LOCK = threading.Lock()
SESSION_READY_TIMEOUT_S = 120.0
logger = logging.getLogger(__name__)


class ProtectedModelError(RuntimeError):
    """Stop immediately when an operation could affect an existing model."""


class ModelSaveError(RuntimeError):
    """Do not reset an initialized model again when its destination is unwritable."""


def _check(operation: str, value: Any) -> None:
    ret = value[-1] if isinstance(value, (list, tuple)) else value
    if ret != 0:
        raise RuntimeError(f"{operation} returned {ret!r}")


def _model_root() -> Path:
    return Path.home() / "Documents" / "ETABS-MCP"


def _new_model_path() -> Path:
    root = _model_root()
    folder = root / f"model-{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:12]}"
    folder.mkdir(parents=True, exist_ok=False)
    return folder / "model.EDB"


def _prepare_empty_model(
    model: Any, path: Path, grid: tuple[int, float, float, int, int, float, float], allow_existing: bool = True,
) -> dict:
    # Serialize late COM callbacks too: a timed-out caller must not race a reset.
    with _MODEL_LOCK:
        status = read_model_status(model)
        # A saved file is protected even when it contains no geometry.
        if status["model_path"] or status["model_empty"] is False:
            if not allow_existing:
                raise ProtectedModelError("The new session already contains a model; it was not changed.")
            return {**status, "created_model": False}
        if status["model_empty"] is not True:
            raise RuntimeError("Could not verify that the selected model is empty; it was not reset.")
        if path.exists():
            raise FileExistsError(f"The new model path already exists: {path}")
        logger.info("Initializing an empty ETABS session for %s", path)
        _check("InitializeNewModel", model.InitializeNewModel(6))
        logger.info("ETABS initialization completed; creating grid")
        _check("File.NewGridOnly", model.File.NewGridOnly(*grid))
        logger.info("ETABS grid created")
        counts = [int(obj.Count()) for obj in (model.PointObj, model.FrameObj, model.AreaObj)]
        if any(counts):
            raise ProtectedModelError("The supposedly new grid contains geometry; no save or reset was attempted.")
        try:
            logger.info("Saving the new ETABS working model to %s", path)
            _check("File.Save", model.File.Save(str(path)))
        except Exception as exc:
            raise ModelSaveError(f"ETABS could not save the new model to {path}. Check write access: {exc}") from exc
        _check("View.RefreshView", model.View.RefreshView(0, False))
        logger.info("ETABS working model saved: %s", path)
        result = read_model_status(model)
        if not result["model_ready"] or not model_paths_match(result["model_path"], path):
            raise RuntimeError("ETABS did not confirm the newly saved model.")
        return {**result, "created_model": True, "saved_path": str(path)}


def _run_model_preparation(pid: int, path: Path, grid: tuple, allow_existing: bool) -> dict:
    """Isolate ETABS initialization from the async MCP host, as in --self-test."""
    command = [sys.executable]
    if not getattr(sys, "frozen", False):
        command.extend(["-m", "etabs_mcp.main"])
    payload = json.dumps({"pid": pid, "path": str(path), "grid": grid, "allow_existing": allow_existing})
    with _external_program_environment() as env:
        try:
            completed = subprocess.run(
                [*command, "--prepare-model", payload], capture_output=True, text=True,
                encoding="utf-8", errors="replace", env=env, timeout=90,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except subprocess.TimeoutExpired:
            raise TimeoutError("The preparation helper timed out; check get_status before retrying.") from None
    try:
        response = json.loads(completed.stdout)
    except (ValueError, TypeError) as exc:
        raise RuntimeError(f"Preparation helper failed: {completed.stderr[-1500:]}") from exc
    if completed.returncode or not response.get("success"):
        logger.error("ETABS preparation helper: %s", completed.stderr[-2000:])
        error = str(response.get("error", "Preparation helper failed"))
        error_class = {
            "ProtectedModelError": ProtectedModelError, "ModelSaveError": ModelSaveError,
            "FileExistsError": FileExistsError, "TimeoutError": TimeoutError,
        }.get(response.get("error_type"), RuntimeError)
        raise error_class(error)
    return response["result"]


def run_preparation_cli(payload: str) -> int:
    """Internal JSON worker: one STA and a retained cOAPI, with no MCP event loop."""
    result_box: list[Any] = [None]

    def worker() -> None:
        import pythoncom

        pythoncom.CoInitialize()
        try:
            request = json.loads(payload)
            pid, path, grid = int(request["pid"]), Path(request["path"]), tuple(request["grid"])
            if pid <= 0 or not path.is_absolute() or path.suffix.casefold() != ".edb" or len(grid) != 7:
                raise ValueError("Invalid preparation request")
            if not 1 <= grid[0] <= 200 or not all(0 < value <= 1000 for value in grid[1:]):
                raise ValueError("Invalid grid parameters")
            # CSI's in-process COM helper must not load DLLs from PyInstaller's
            # private bundle while it initializes the external application.
            with _external_program_environment():
                application = _attach_etabs(pid, timeout_s=5.0)
                result_box[0] = _prepare_empty_model(application.SapModel, path, grid, bool(request["allow_existing"]))
        except Exception as exc:
            result_box[0] = exc
        finally:
            pythoncom.CoUninitialize()

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    thread.join(timeout=80)
    value = result_box[0]
    if thread.is_alive() or value is None:
        value = TimeoutError("ETABS initialization is still pending; check the selected instance before retrying.")
    if isinstance(value, Exception):
        print(json.dumps({"success": False, "error": str(value), "error_type": type(value).__name__}), flush=True)
        return 1
    print(json.dumps({"success": True, "result": value}), flush=True)
    return 0


def prepare_etabs_session(
    registry: InstanceRegistry,
    instance: str | None = None,
    new_model: bool = False,
    stories: int = 1,
    story_height: float = 3.0,
    first_story_height: float = 3.0,
    grid_lines_x: int = 2,
    grid_lines_y: int = 2,
    spacing_x: float = 6.0,
    spacing_y: float = 6.0,
) -> dict[str, Any]:
    """Open ETABS if needed; initialize only a verified empty, unsaved session."""
    if not 1 <= stories <= 200:
        raise ValueError("stories must be between 1 and 200")
    if not (2 <= grid_lines_x <= 100 and 2 <= grid_lines_y <= 100):
        raise ValueError("grid_lines_x and grid_lines_y must be between 2 and 100")
    if not all(0 < value <= 1000 for value in (story_height, first_story_height, spacing_x, spacing_y)):
        raise ValueError("Heights and spacings must be positive and at most 1000 metres")
    if new_model and instance is not None:
        raise ValueError("For a new model, omit instance: a separate ETABS process protects existing work.")

    with _SESSION_LOCK:
        active = registry.get_active_instances()
        preexisting = {item.pid for item in active}
        launched = new_model or not active
        if launched:
            if instance is not None:
                raise ValueError(f"{instance!r} is no longer running; no other instance was selected.")
            exe = find_etabs_exe()
            if exe is None:
                raise FileNotFoundError("ETABS is not installed in Program Files. Install ETABS before retrying.")
            pid = _launch_etabs(exe, timeout_s=35.0)
        else:
            candidates = active if instance is None else [item for item in active if item.alias == instance]
            if len(candidates) != 1:
                raise ValueError(f"Specify one running instance: {[item.alias for item in active]}")
            pid = candidates[0].pid

        grid = (stories, story_height, first_story_height, grid_lines_x, grid_lines_y, spacing_x, spacing_y)
        path: Path | None = None
        # ETABS may expose COM before its licensed model object is ready.
        # A cold start can take more than the first initialization attempt.
        deadline = time.monotonic() + SESSION_READY_TIMEOUT_S
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            # An ETABS sign-in may restart only the process that this call launched.
            running = set(_find_etabs_pids())
            if pid not in running:
                replacements = running - preexisting
                if launched and len(replacements) == 1:
                    pid = replacements.pop()
                else:
                    raise RuntimeError("The selected ETABS process closed; retry after completing ETABS sign-in.")
            try:
                status = connect_and_run(read_model_status, pid, timeout=5.0)
                if status["model_path"] or status["model_empty"] is False:
                    if launched:
                        raise ProtectedModelError("The newly opened process already contains a model; it was not changed.")
                    result = {**status, "created_model": False}
                else:
                    path = path or _new_model_path()
                    result = _run_model_preparation(pid, path, grid, allow_existing=not launched)
                if not result["model_ready"]:
                    raise RuntimeError("The session is not ready for modelling.")
                result.update(success=True, alias=registry.assign_alias(pid), pid=pid,
                              server_version=SERVER_VERSION, launched=launched, computer_use_required=False,
                              view_management="The COM API refreshes existing views; arrange plan/elevation windows manually.")
                warning = check_version_warning(result["etabs_version"])
                if warning:
                    result["warning"] = warning
                return result
            except (ProtectedModelError, ModelSaveError, FileExistsError):
                raise
            except TimeoutError:
                # The COM call may still complete. Never start another reset automatically.
                raise TimeoutError(f"ETABS PID {pid}: preparation may still be running; wait and check get_status.") from None
            except Exception as exc:
                last_error = exc
                # Retry only while the same/newly restarted session remains empty.
                # A saved file or any geometry is checked again before InitializeNewModel.
                time.sleep(0.5)
        raise RuntimeError(f"ETABS PID {pid} is not ready. Complete any ETABS sign-in, then retry. {last_error}")
