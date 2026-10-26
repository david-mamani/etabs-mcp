"""Discover real ETABS processes and connect to the explicitly selected PID."""

from __future__ import annotations

import ctypes
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar

from etabs_mcp.status import read_model_status
from etabs_mcp.version import check_version_warning

if sys.platform == "win32":
    import comtypes.client
    import pythoncom

_ETABS_EXE_NAMES = frozenset({"etabs.exe", "etabs64.exe"})
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
_ETABS_API_PROGID = "CSI.ETABS.API.ETABSObject"
_cached_progid: str | None = None
_cached_progid_lock = threading.Lock()


def _find_etabs_pids() -> list[int]:
    """Enumerate exact ETABS executable names with pointer-sized Win32 handles."""
    if sys.platform != "win32":
        return []
    from ctypes import wintypes

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    for name in ("Process32FirstW", "Process32NextW"):
        fn = getattr(kernel32, name)
        fn.argtypes = (wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W))
        fn.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL

    snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    pids = []
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        if kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
            while True:
                if entry.szExeFile.lower() in _ETABS_EXE_NAMES:
                    pids.append(int(entry.th32ProcessID))
                if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                    break
    finally:
        kernel32.CloseHandle(snapshot)
    return sorted(pids)


def _get_etabs_model(pid: int = 0) -> Any:
    """Connect by PID; an explicit PID NEVER falls back to the active object.

    COM objects stay on the STA thread that creates them. Only the working
    helper's ProgID is cached, never an ETABS object or model.
    """
    global _cached_progid
    with _cached_progid_lock:
        cached = _cached_progid
    candidates = ([cached] if cached else []) + [p for p in _HELPER_PROGIDS if p != cached]
    last_error = None
    for progid in candidates:
        try:
            helper = comtypes.client.CreateObject(progid)
            oapi = helper.GetObjectProcess(_ETABS_API_PROGID, pid) if pid else helper.GetObject(_ETABS_API_PROGID)
            if oapi is None:
                raise OSError(f"{progid} returned no ETABS object")
            model = oapi.SapModel
            if model is None:
                raise OSError(f"{progid} returned no SapModel")
            with _cached_progid_lock:
                _cached_progid = progid
            return model
        except Exception as exc:
            last_error = exc
    with _cached_progid_lock:
        _cached_progid = None
    target = f"ETABS PID {pid}" if pid else "the active ETABS instance"
    raise OSError(f"Could not connect to {target}. Last error: {last_error}")


@dataclass(frozen=True)
class ETABSInstance:
    alias: str
    pid: int
    file_path: str | None
    version: str
    warning: str | None = None
    connected: bool = True
    model_ready: bool = False
    model_state: str = "unavailable"
    error: str | None = None

    def asdict(self) -> dict[str, Any]:
        result = {
            "alias": self.alias,
            "pid": self.pid,
            "file_path": self.file_path,
            "version": self.version,
            "connected": self.connected,
            "model_ready": self.model_ready,
            "model_state": self.model_state,
        }
        if self.warning:
            result["warning"] = self.warning
        if self.error:
            result["error"] = self.error
        return result


class InstanceRegistry:
    """Assign monotonic aliases; keep closed aliases as tombstones."""

    def __init__(self) -> None:
        self._pid_to_alias: dict[int, str] = {}
        self._alias_to_pid: dict[str, int] = {}
        self._next_num = 1
        self._lock = threading.Lock()

    def assign_alias(self, pid: int) -> str:
        with self._lock:
            if pid not in self._pid_to_alias:
                alias = f"etabs{self._next_num}"
                self._next_num += 1
                self._pid_to_alias[pid] = alias
                self._alias_to_pid[alias] = pid
            return self._pid_to_alias[pid]

    def resolve(self, alias: str) -> int | None:
        with self._lock:
            return self._alias_to_pid.get(alias)

    def get_active_instances(self) -> list[ETABSInstance]:
        if sys.platform != "win32":
            return []
        pids = _find_etabs_pids()
        with self._lock:
            self._pid_to_alias = {p: a for p, a in self._pid_to_alias.items() if p in pids}
        result = []
        for pid in pids:
            alias = self.assign_alias(pid)
            try:
                status = connect_and_run(read_model_status, pid, timeout=5.0)
                result.append(
                    ETABSInstance(
                        alias=alias,
                        pid=pid,
                        file_path=status["model_path"],
                        version=status["etabs_version"],
                        model_ready=status["model_ready"],
                        model_state=status["model_state"],
                        error=status.get("model_error"),
                        warning=check_version_warning(status["etabs_version"]),
                    )
                )
            except Exception as exc:
                # A start page or sign-in dialog must not hide the other models.
                result.append(
                    ETABSInstance(
                        alias=alias,
                        pid=pid,
                        file_path=None,
                        version="unknown",
                        connected=False,
                        error=str(exc),
                    )
                )
        return result


T = TypeVar("T")


def connect_and_run(fn: Callable[[Any], T], pid: int = 0, timeout: float = 120.0) -> T:
    """Run a callback in an STA thread against the requested ETABS process."""
    result_box: list[Any] = [None]
    done = threading.Event()

    def _worker() -> None:
        try:
            pythoncom.CoInitialize()
            try:
                result_box[0] = fn(_get_etabs_model(pid))
            finally:
                pythoncom.CoUninitialize()
        except Exception as exc:
            result_box[0] = exc
        finally:
            done.set()

    threading.Thread(target=_worker, daemon=True).start()
    if not done.wait(timeout=timeout):
        raise TimeoutError(f"ETABS PID {pid}: COM call did not complete within {timeout}s")
    value = result_box[0]
    if isinstance(value, Exception):
        raise value
    return value
