"""Autonomous smoke test for the ETABS MCP installation.

The installer runs ``etabs-mcp.exe --self-test``.  This module:

1. Finds a local ETABS installation.
2. Launches a dedicated ETABS instance (it never touches an already-open model).
3. Creates a small two-story model from scratch with the ETABS API.
4. Saves it, runs the analysis and reads the base reaction.
5. Unlocks the model and leaves ETABS open, ready for the next request.

Exit codes: ``0`` = pass, ``1`` = fail, ``2`` = ETABS not installed (skip).

The test does not import the MCP server or require its transport to start.
"""

from __future__ import annotations

import contextlib
import ctypes
import json
import os
import re
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Any

SMOKE_FILE_NAME = "etabs-mcp-smoke.EDB"
ETABS_UNITS_KN_M = 6
ETABS_API_PROGID = "CSI.ETABS.API.ETABSObject"
ETABS_LAUNCH_TIMEOUT_S = 180
MODEL_READY_TIMEOUT_S = 300
VERSION_TIMEOUT_S = 60
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


def _standard_program_files() -> list[Path]:
    """MCP clients may omit ProgramFiles from the child environment."""
    system_drive = Path(os.environ.get("SYSTEMROOT", r"C:\Windows")).parent
    return [system_drive / "Program Files", system_drive / "Program Files (x86)"]


def find_etabs_exe() -> Path | None:
    """Return the newest ``ETABS.exe`` found under Program Files, if any."""
    roots = []
    for env_name in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)"):
        root = os.environ.get(env_name)
        if root:
            roots.append(Path(root) / "Computers and Structures")
    if sys.platform == "win32":
        roots.extend(root / "Computers and Structures" for root in _standard_program_files())

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


@contextlib.contextmanager
def _external_program_environment():
    """Do not let Windows programs inherit PyInstaller's private DLLs.

    See https://pyinstaller.org/en/stable/common-issues-and-pitfalls.html
    (Launching External Programs / Windows). Restore our search path even
    when process creation fails; the MCP still needs its bundled libraries.
    """
    env = os.environ.copy()
    bundle = getattr(sys, "_MEIPASS", None)
    setter = None
    if sys.platform == "win32" and bundle:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        setter = kernel32.SetDllDirectoryW
        setter.argtypes = (ctypes.c_wchar_p,)
        setter.restype = ctypes.c_int
        if not setter(None):
            raise ctypes.WinError(ctypes.get_last_error())
        bundle_path = Path(bundle).resolve()
        env["PATH"] = os.pathsep.join(
            part
            for part in env.get("PATH", "").split(os.pathsep)
            if part and not Path(part).resolve().is_relative_to(bundle_path)
        )
    try:
        yield env
    finally:
        if setter is not None:
            setter(str(bundle))


def _etabs_pids() -> set[int]:
    """Return the PIDs of the running ETABS processes."""
    try:
        with _external_program_environment() as env:
            completed = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq ETABS.exe", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
                check=False,
                env=env,
            )
    except OSError:
        return set()

    pids: set[int] = set()
    for line in completed.stdout.splitlines():
        parts = [part.strip().strip('"') for part in line.split('","')]
        if len(parts) >= 2 and parts[0].lower() == "etabs.exe":
            try:
                pids.add(int(parts[1]))
            except ValueError:
                continue
    return pids


def _launch_etabs(exe: Path, timeout_s: float = ETABS_LAUNCH_TIMEOUT_S) -> int:
    """Start ETABS detached from this process and return its PID.

    ``explorer.exe`` starts ETABS from the already-running Windows shell.  A
    direct launch from this PyInstaller bundle makes ETABS inherit the
    bundle's DLL directory and load our ``VCRUNTIME140.dll``, which crashes it
    on some machines.  It also keeps ETABS outside the installer's job object.
    """
    before = _etabs_pids()
    try:
        with _external_program_environment() as env:
            subprocess.Popen(["explorer.exe", str(exe)], close_fds=True, env=env)
    except OSError as exc:
        raise SelfTestError(f"no pude abrir ETABS: {exc}") from exc

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        new_pids = _etabs_pids() - before
        if len(new_pids) > 1:
            raise SelfTestError(f"aparecieron varias instancias; no se seleccionara ninguna: {sorted(new_pids)}")
        if new_pids:
            return new_pids.pop()
        time.sleep(1)
    raise SelfTestError(f"ETABS no aparecio en {timeout_s} s")


def _attach_etabs(pid: int, timeout_s: float = ETABS_LAUNCH_TIMEOUT_S) -> Any:
    """Attach to a specific ETABS process through its COM API."""
    import comtypes.client

    last_error: BaseException | str | None = None
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if pid not in _etabs_pids():
            raise SelfTestError(f"ETABS (PID {pid}) se cerro durante la conexion")
        for progid in _HELPER_PROGIDS:
            try:
                helper = comtypes.client.CreateObject(progid)
                oapi = helper.GetObjectProcess(ETABS_API_PROGID, pid)
                if oapi is not None:
                    return oapi
                last_error = f"{progid}: GetObjectProcess devolvio None"
            except Exception as exc:
                last_error = exc
        time.sleep(2)
    raise SelfTestError(
        f"no pude conectar con ETABS (PID {pid}). "
        f"Verifica que ETABS este instalado y con licencia. Ultimo error: {last_error}"
    )


def _prepare_test_session(pid: int, preexisting: set[int]) -> tuple[int, Any, Any]:
    """Recover an authentication restart without attaching to an existing model.

    Only one newly appeared, empty process may replace our launched process.
    No model creation is retried after geometry has started to be added.
    """
    deadline = time.monotonic() + MODEL_READY_TIMEOUT_S
    last_error: BaseException | str | None = None
    oapi = None
    checked_empty = False
    last_reported = None
    from etabs_mcp.status import normalize_model_path

    _log("    Si ETABS solicita Sign In, completa el inicio de sesion manualmente.")
    while time.monotonic() < deadline:
        running = _etabs_pids()
        if pid not in running:
            candidates = running - preexisting
            if len(candidates) > 1:
                raise SelfTestError(f"reinicio ambiguo; no se seleccionara un modelo: {sorted(candidates)}")
            oapi = None
            checked_empty = False
            if not candidates:
                time.sleep(2)
                continue
            previous = pid
            pid = candidates.pop()
            _log(f"    ETABS se reinicio tras autenticar: PID {previous} -> {pid}")
        try:
            if oapi is None:
                oapi = _attach_etabs(pid, timeout_s=2.0)
            model = oapi.SapModel
            if not checked_empty:
                filename = normalize_model_path(model.GetModelFilename(True))
                counts = [int(obj.Count()) for obj in (model.PointObj, model.FrameObj, model.AreaObj)]
                if filename or any(counts):
                    raise SelfTestError(f"PID {pid} contiene un modelo; la prueba no lo modificara")
                checked_empty = True
            if _ok(_create_new_model(model)):
                return pid, oapi, model
            last_error = "la API devolvio un codigo distinto de cero"
        except SelfTestError as exc:
            if "contiene un modelo" in str(exc):
                raise
            last_error = exc
            oapi = None
        except Exception as exc:
            last_error = exc
            oapi = None
        if str(last_error) != last_reported:
            _log(f"    esperando ETABS (PID {pid}): {last_error}")
            last_reported = str(last_error)
        time.sleep(2)
    raise SelfTestError(f"ETABS no estuvo listo en {MODEL_READY_TIMEOUT_S} s: {last_error}")


def _create_new_model(model: Any) -> Any:
    """Initialize a new model and create the grid in one retryable step.

    ETABS 23 sometimes accepts ``InitializeNewModel`` before its internal model
    object is ready.  Calling it again right before ``File.NewGridOnly`` makes
    the grid creation succeed, so both calls are retried together.
    """
    result = model.InitializeNewModel(ETABS_UNITS_KN_M)
    if not _ok(result):
        return result
    return model.File.NewGridOnly(2, 3.0, 3.0, 2, 2, 6.0, 6.0)


def _read_version(model: Any) -> str:
    """Return the ETABS version, retrying while the API still reports empty."""
    deadline = time.monotonic() + VERSION_TIMEOUT_S
    version = ""
    while time.monotonic() < deadline:
        result = model.GetVersion()
        _check("GetVersion", result)
        version = str(result[0]) if result else ""
        if version:
            return version
        time.sleep(2)
    return version or "desconocida"


def _build_model(model: Any) -> list[list[str]]:
    """Build on the dedicated, initialized session returned by preparation."""
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
                model.FrameObj.AddByPoint(points[level][corner], points[level + 1][corner], "", PropName="SMOKE"),
            )
    for level in (1, 2):
        for corner in range(4):
            _check(
                "FrameObj.AddByPoint (viga)",
                model.FrameObj.AddByPoint(
                    points[level][corner],
                    points[level][(corner + 1) % 4],
                    "",
                    PropName="SMOKE",
                ),
            )

    _verify_frame_sections(model)

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


def _verify_frame_sections(model: Any) -> None:
    frames = _check("FrameObj.GetNameList", model.FrameObj.GetNameList())
    if int(frames[0]) != 16:
        raise SelfTestError(f"se esperaban 16 barras; se encontraron {frames[0]}")
    for name in frames[1]:
        section = _check(f"FrameObj.GetSection({name})", model.FrameObj.GetSection(name))
        if section[0] != "SMOKE":
            raise SelfTestError(f"barra {name}: se esperaba SMOKE; se obtuvo {section[0]}")


def _output_path() -> Path:
    local_appdata = os.environ.get("LOCALAPPDATA")
    base = Path(local_appdata) if local_appdata else Path.home() / "AppData" / "Local"
    out_dir = base / "etabs-mcp" / "smoke-test"
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir / SMOKE_FILE_NAME


def _save_model(model: Any) -> Path:
    out = _output_path()
    if out.exists():
        # Preserve previous tests, including files still open in another ETABS.
        out = out.with_name(f"{out.stem}-{time.time_ns()}{out.suffix}")

    _log(f"    guardando {out} ...")
    _check("File.Save", model.File.Save(str(out)))
    if not out.is_file() or out.stat().st_size < 5_000:
        raise SelfTestError(f"el archivo guardado no existe o es demasiado pequeno: {out}")
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
        raise SelfTestError(f"reaccion horizontal distinta de la carga aplicada: {total_fx:.2f} kN")
    if max(abs(value) for value in fy) > 1.0:
        raise SelfTestError(f"reaccion FY inesperada: {fy!r}")
    return total_fz, total_fx


def _close(oapi: Any) -> None:
    with contextlib.suppress(Exception):
        oapi.ApplicationExit(False)


def _leave_open(oapi: Any, model: Any) -> None:
    """Unlock the smoke model and make sure the ETABS window is visible."""
    with contextlib.suppress(Exception):
        model.SetModelIsLocked(False)
    with contextlib.suppress(Exception):
        oapi.Unhide()


def _print_banner(version: str) -> None:
    _log("=" * 64)
    _log(f" ETABS MCP {version} - prueba automatica de instalacion")
    _log("=" * 64)


def run_self_test(server_version: str, report_path: str | None = None) -> int:
    """Keep every test COM proxy on one explicitly initialized STA thread."""
    import pythoncom

    outcome = [1]

    def worker() -> None:
        pythoncom.CoInitialize()
        try:
            outcome[0] = _run_self_test(server_version, report_path)
        finally:
            pythoncom.CoUninitialize()

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join()
    return outcome[0]


def _run_self_test(server_version: str, report_path: str | None = None) -> int:
    """Run the full smoke test and return an exit code (0/1/2)."""
    _print_banner(server_version)
    oapi = None
    try:
        if report_path:
            Path(report_path).write_text(json.dumps({"success": False}), encoding="utf-8")
        exe = find_etabs_exe()
        if exe is None:
            _log("SKIP: no se encontro ETABS en Program Files.")
            _log("      El MCP quedo instalado, pero la prueba necesita ETABS 21+.")
            return 2

        _step(1, 7, f"ETABS encontrado: {exe}")
        _step(2, 7, "abriendo ETABS...")
        preexisting = _etabs_pids()
        pid = _launch_etabs(exe)

        _step(3, 7, "esperando licencia y creando el modelo...")
        pid, oapi, model = _prepare_test_session(pid, preexisting)
        points = _build_model(model)
        version = _read_version(model)
        _log(f"    ETABS {version}: {len(points) * 4} nodos, 16 frames, 2 pisos")

        _step(4, 7, "guardando el archivo de prueba...")
        out = _save_model(model)
        _log(f"    {out} ({out.stat().st_size / 1024:.0f} KB)")

        _step(5, 7, "analizando el modelo...")
        _run_analysis(model)

        _step(6, 7, "leyendo las reacciones en la base...")
        total_fz, total_fx = _read_base_reaction(model)
        _log(f"    FZ = {total_fz:.2f} kN   FX = {total_fx:.2f} kN")

        _step(7, 7, "dejando ETABS abierto...")
        from etabs_mcp.status import model_paths_match, read_model_status

        # This process owns an STA proxy on its main thread. Validate it here;
        # the installer checks the independent MCP connection in --status after
        # this process has exited and released its COM objects.
        connector_status = read_model_status(model)
        if not connector_status["model_ready"] or not model_paths_match(connector_status["model_path"], out):
            raise SelfTestError(f"el conector MCP no identifica el archivo de prueba del PID {pid}")
        if report_path:
            Path(report_path).write_text(
                json.dumps(
                    {
                        "success": True,
                        "pid": pid,
                        "model_path": str(out),
                        "etabs_version": version,
                        "server_version": server_version,
                    }
                ),
                encoding="utf-8",
            )
        _leave_open(oapi, model)
        oapi = None

        _log("")
        _log("PASS: modelo creado, analizado y guardado correctamente.")
        _log("      ETABS queda abierto con el modelo de prueba.")
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
