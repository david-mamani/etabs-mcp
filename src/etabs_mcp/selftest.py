"""Autonomous smoke test for the ETABS MCP installation.

The installer runs ``etabs-mcp.exe --self-test``.  This module:

1. Finds a local ETABS installation.
2. Launches a dedicated ETABS instance (it never touches an already-open model).
3. Creates a small two-story model from scratch with the ETABS API.
4. Saves it, runs the analysis and reads the base reaction.
5. Leaves the model on disk and closes the ETABS instance it created.

Exit codes: ``0`` = pass, ``1`` = fail, ``2`` = ETABS not installed (skip).

The module deliberately depends only on ``comtypes`` and the standard library,
so it can run even if the MCP server itself cannot start.
"""

from __future__ import annotations

import contextlib
import os
import re
import time
import traceback
from pathlib import Path
from typing import Any

SMOKE_FILE_NAME = "etabs-mcp-smoke.EDB"
ETABS_UNITS_KN_M = 6
MODEL_READY_TIMEOUT_S = 300
NEW_MODEL_TIMEOUT_S = 90
APPLIED_FX_KN = 100.0
APPLIED_FZ_KN = -100.0

# Same order used by etabs_mcp.connection.  Kept here so the self-test does not
# import the server package (and therefore cannot be broken by server changes).
_HELPER_PROGIDS = (
    "ETABSv1.Helper",
    "ETABSv23.Helper",
    "ETABSv22.Helper",
    "ETABSv21.Helper",
    "ETABSv20.Helper",
    "ETABSv19.Helper",
    "ETABSv18.Helper",
    "ETABSv17.Helper",
    "ETABS2016.Helper",
    "ETABS2015.Helper",
)

_CORNERS = ((0.0, 0.0), (6.0, 0.0), (6.0, 6.0), (0.0, 6.0))
_ELEVATIONS = (0.0, 3.0, 6.0)

_EXPECTED_STORIES = 2
_EXPECTED_POINTS = 12


class SelfTestError(RuntimeError):
    """Raised when a self-test step does not produce the expected result."""


def _log(message: str = "") -> None:
    print(message, flush=True)


def _step(number: int, total: int, message: str) -> None:
    _log(f"[{number}/{total}] {message}")


def _ok(value: Any) -> bool:
    """Return True when a CSI API return value contains a zero return code."""
    if isinstance(value, (tuple, list)):
        if not value:
            return False
        value = value[-1]
    try:
        return int(value) == 0
    except (TypeError, ValueError):
        return False


def _check(label: str, value: Any) -> Any:
    if not _ok(value):
        raise SelfTestError(f"{label} devolvio {value!r}")
    return value


def _version_key(name: str) -> tuple[int, ...]:
    numbers = re.findall(r"\d+", name)
    return tuple(int(n) for n in numbers) if numbers else (0,)


def find_etabs_exe() -> Path | None:
    """Return the newest ``ETABS.exe`` found under Program Files, if any."""
    roots = []
    for env_name in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)"):
        root = os.environ.get(env_name)
        if root:
            roots.append(Path(root) / "Computers and Structures")

    candidates: list[tuple[tuple[int, ...], Path]] = []
    seen: set[str] = set()
    for root in roots:
        if not root.is_dir():
            continue
        try:
            entries = list(root.iterdir())
        except OSError:
            continue
        for entry in entries:
            if not entry.is_dir() or not entry.name.upper().startswith("ETABS"):
                continue
            exe = entry / "ETABS.exe"
            if not exe.is_file():
                continue
            key = str(exe).lower()
            if key in seen:
                continue
            seen.add(key)
            candidates.append((_version_key(entry.name), exe))

    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def _create_oapi(exe: Path) -> Any:
    import comtypes.client

    errors: list[str] = []
    for progid in _HELPER_PROGIDS:
        try:
            helper = comtypes.client.CreateObject(progid)
            oapi = helper.CreateObject(str(exe))
            if oapi is not None:
                return oapi
            errors.append(f"{progid}: CreateObject devolvio None")
        except Exception as exc:
            errors.append(f"{progid}: {exc}")
    raise SelfTestError(
        "No se pudo crear una instancia de ETABS. "
        "Verifica que ETABS este instalado y con licencia.\n  " + "\n  ".join(errors)
    )


def _start_etabs(exe: Path) -> Any:
    last_error: BaseException | str | None = None
    for attempt in range(1, 4):
        oapi = None
        try:
            oapi = _create_oapi(exe)
            if _ok(oapi.ApplicationStart()):
                return oapi
            last_error = "ApplicationStart devolvio un codigo distinto de cero"
        except Exception as exc:  # ETABS may still be shutting down / COM busy
            last_error = exc
        if oapi is not None:
            _close(oapi)
        if attempt < 3:
            _log(f"    ETABS no arranco (intento {attempt}/3): {last_error}")
            time.sleep(5)
    raise SelfTestError(f"ETABS no arranco tras 3 intentos: {last_error}")


def _wait_for_new_model(model: Any, timeout_s: int, label: str, create) -> None:
    """Retry a model-creation call until ETABS has finished starting up."""
    deadline = time.monotonic() + timeout_s
    last_error: BaseException | str | None = None
    while time.monotonic() < deadline:
        try:
            if _ok(create()):
                return
            last_error = "la API devolvio un codigo distinto de cero"
        except Exception as exc:
            last_error = exc
        time.sleep(2)
    raise SelfTestError(f"{label} no estuvo listo en {timeout_s} s: {last_error}")


def _build_model(model: Any) -> list[list[str]]:
    _log("    creando grilla y pisos...")
    _wait_for_new_model(
        model,
        MODEL_READY_TIMEOUT_S,
        "InitializeNewModel",
        lambda: model.InitializeNewModel(ETABS_UNITS_KN_M),
    )
    _wait_for_new_model(
        model,
        NEW_MODEL_TIMEOUT_S,
        "File.NewGridOnly",
        lambda: model.File.NewGridOnly(2, 3.0, 3.0, 2, 2, 6.0, 6.0),
    )
    _check("SetPresentUnits", model.SetPresentUnits(ETABS_UNITS_KN_M))

    stories = model.Story.GetStories()
    _check("Story.GetStories", stories)
    if int(stories[0]) != _EXPECTED_STORIES:
        raise SelfTestError(f"se esperaban 2 pisos y se crearon {int(stories[0])}")

    _log("    agregando nodos, columnas y vigas...")
    points: list[list[str]] = []
    for elevation in _ELEVATIONS:
        level: list[str] = []
        for x, y in _CORNERS:
            result = model.PointObj.AddCartesian(x, y, elevation)
            _check(f"PointObj.AddCartesian({x}, {y}, {elevation})", result)
            level.append(str(result[0]))
        points.append(level)

    if len(points) * 4 != _EXPECTED_POINTS:
        raise SelfTestError(f"se esperaban {_EXPECTED_POINTS} nodos")

    for name in points[0]:
        _check(
            f"PointObj.SetRestraint({name})",
            model.PointObj.SetRestraint(name, (True,) * 6),
        )

    _check(
        "PropFrame.SetRectangle",
        model.PropFrame.SetRectangle("SMOKE", "4000Psi", 0.4, 0.4),
    )

    for level in (0, 1):
        for corner in range(4):
            _check(
                "FrameObj.AddByPoint (columna)",
                model.FrameObj.AddByPoint(
                    points[level][corner], points[level + 1][corner], "SMOKE"
                ),
            )
    for level in (1, 2):
        for corner in range(4):
            _check(
                "FrameObj.AddByPoint (viga)",
                model.FrameObj.AddByPoint(
                    points[level][corner],
                    points[level][(corner + 1) % 4],
                    "SMOKE",
                ),
            )

    self_weight = model.LoadPatterns.GetSelfWTMultiplier("Dead")
    _check("LoadPatterns.GetSelfWTMultiplier", self_weight)
    if abs(float(self_weight[0])) < 1e-9:
        _check(
            "LoadPatterns.SetSelfWTMultiplier",
            model.LoadPatterns.SetSelfWTMultiplier("Dead", 1.0),
        )

    # A vertical load makes the model exercise gravity, a horizontal load gives
    # a reaction that can be checked exactly against the applied value.
    _check(
        "PointObj.SetLoadForce",
        model.PointObj.SetLoadForce(
            points[2][0],
            "Dead",
            (APPLIED_FX_KN, 0.0, APPLIED_FZ_KN, 0.0, 0.0, 0.0),
            True,
        ),
    )
    return points


def _output_path() -> Path:
    local_appdata = os.environ.get("LOCALAPPDATA")
    base = Path(local_appdata) if local_appdata else Path.home() / "AppData" / "Local"
    out_dir = base / "etabs-mcp" / "smoke-test"
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir / SMOKE_FILE_NAME


def _save_model(model: Any) -> Path:
    out = _output_path()
    try:
        if out.exists():
            out.unlink()
    except OSError:
        # Another process may be holding the previous smoke file.  Use a
        # timestamped name instead of failing the installation.
        out = out.with_name(
            f"{out.stem}-{time.strftime('%Y%m%d-%H%M%S')}{out.suffix}"
        )

    _log(f"    guardando {out} ...")
    _check("File.Save", model.File.Save(str(out)))
    if not out.is_file() or out.stat().st_size < 5_000:
        raise SelfTestError(
            f"el archivo guardado no existe o es demasiado pequeno: {out}"
        )
    return out


def _run_analysis(model: Any) -> None:
    _log("    ejecutando el analisis...")
    _check("Analyze.RunAnalysis", model.Analyze.RunAnalysis())
    if not model.GetModelIsLocked():
        raise SelfTestError("el analisis termino pero el modelo no quedo bloqueado")


def _read_base_reaction(model: Any) -> tuple[float, float]:
    setup = model.Results.Setup
    _check("Results.Setup.DeselectAllCases", setup.DeselectAllCasesAndCombosForOutput())
    _check("Results.Setup.SetCaseSelectedForOutput", setup.SetCaseSelectedForOutput("Dead"))

    result = model.Results.BaseReact()
    _check("Results.BaseReact", result)
    count = int(result[0])
    if count < 1:
        raise SelfTestError("Results.BaseReact no devolvio filas para el caso Dead")

    fz = [float(value) for value in result[6][:count]]
    fx = [float(value) for value in result[4][:count]]
    fy = [float(value) for value in result[5][:count]]
    total_fz = sum(fz)
    total_fx = sum(fx)
    if not (100.0 < total_fz < 2000.0):
        raise SelfTestError(
            f"reaccion vertical fuera de rango: {total_fz:.2f} kN "
            f"(se esperaba la carga de {abs(APPLIED_FZ_KN):.0f} kN + peso propio)"
        )
    if abs(abs(total_fx) - abs(APPLIED_FX_KN)) > 1.0:
        raise SelfTestError(
            f"reaccion horizontal distinta de la carga aplicada: {total_fx:.2f} kN"
        )
    if max(abs(value) for value in fy) > 1.0:
        raise SelfTestError(f"reaccion FY inesperada: {fy!r}")
    return total_fz, total_fx


def _close(oapi: Any) -> None:
    with contextlib.suppress(Exception):
        oapi.ApplicationExit(False)


def _print_banner(version: str) -> None:
    _log("=" * 64)
    _log(f" ETABS MCP {version} - prueba automatica de instalacion")
    _log("=" * 64)


def run_self_test(server_version: str) -> int:
    """Run the full smoke test and return an exit code (0/1/2)."""
    _print_banner(server_version)
    oapi = None
    try:
        exe = find_etabs_exe()
        if exe is None:
            _log("SKIP: no se encontro ETABS en Program Files.")
            _log("      El MCP quedo instalado, pero la prueba necesita ETABS 21+.")
            return 2

        _step(1, 7, f"ETABS encontrado: {exe}")
        _step(2, 7, "iniciando una instancia nueva de ETABS...")
        oapi = _start_etabs(exe)
        model = oapi.SapModel

        _step(3, 7, "esperando licencia y creando el modelo...")
        version = model.GetVersion()
        _check("GetVersion", version)
        points = _build_model(model)
        _log(f"    ETABS {version[0]}: {len(points) * 4} nodos, 16 frames, 2 pisos")

        _step(4, 7, "guardando el archivo de prueba...")
        out = _save_model(model)
        _log(f"    {out} ({out.stat().st_size / 1024:.0f} KB)")

        _step(5, 7, "analizando el modelo...")
        _run_analysis(model)

        _step(6, 7, "leyendo las reacciones en la base...")
        total_fz, total_fx = _read_base_reaction(model)
        _log(f"    FZ = {total_fz:.2f} kN   FX = {total_fx:.2f} kN")

        _step(7, 7, "cerrando ETABS...")
        _close(oapi)
        oapi = None

        _log("")
        _log("PASS: modelo creado, analizado y guardado correctamente.")
        _log(f"      Archivo de prueba: {out}")
        return 0
    except Exception as exc:
        _log("")
        _log(f"FAIL: {exc}")
        _log("")
        traceback.print_exc()
        return 1
    finally:
        if oapi is not None:
            _close(oapi)
