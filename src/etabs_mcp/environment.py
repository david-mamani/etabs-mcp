"""Restore Windows paths that MCP clients intentionally omit from child envs."""

from __future__ import annotations

import os
import sys
from collections.abc import MutableMapping
from pathlib import Path


def _program_directories(system_root: Path) -> dict[str, str]:
    drive = system_root.parent
    defaults = {
        "PROGRAMFILES": str(drive / "Program Files"),
        "PROGRAMW6432": str(drive / "Program Files"),
        "PROGRAMFILES(X86)": str(drive / "Program Files (x86)"),
    }
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion") as key:
            for name, value_name in (
                ("PROGRAMFILES", "ProgramFilesDir"),
                ("PROGRAMW6432", "ProgramW6432Dir"),
                ("PROGRAMFILES(X86)", "ProgramFilesDir (x86)"),
            ):
                try:
                    value, _ = winreg.QueryValueEx(key, value_name)
                    if isinstance(value, str) and value:
                        defaults[name] = os.path.expandvars(value)
                except OSError:
                    pass
    except OSError:
        pass
    return defaults


def ensure_windows_environment(env: MutableMapping[str, str] | None = None) -> dict[str, str]:
    """Fill missing CSI-required paths in this process only; keep user overrides.

    Standard MCP stdio clients omit ProgramFiles, COMSPEC and TMP. CSI's COM
    helper can connect with that reduced environment but fail to initialize a
    model. Reading registered Windows directories fixes that without changing
    system settings or requiring the client to forward its whole environment.
    """
    if sys.platform != "win32":
        return {}
    env = os.environ if env is None else env
    system_root = Path(env.get("SYSTEMROOT", r"C:\Windows"))
    defaults = _program_directories(system_root)
    defaults["COMSPEC"] = str(system_root / "System32" / "cmd.exe")
    defaults["TMP"] = env.get("TEMP", str(Path.home() / "AppData" / "Local" / "Temp"))
    added = {}
    for name, value in defaults.items():
        if not env.get(name):
            env[name] = value
            added[name] = value
    return added
