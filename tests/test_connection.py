"""Regressions for real process enumeration and strict instance selection."""

import ctypes
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from etabs_mcp import connection


@pytest.fixture
def com(monkeypatch):
    client = SimpleNamespace(CreateObject=Mock())
    monkeypatch.setattr(connection, "comtypes", SimpleNamespace(client=client), raising=False)
    monkeypatch.setattr(
        connection, "pythoncom", SimpleNamespace(CoInitialize=Mock(), CoUninitialize=Mock()), raising=False
    )
    monkeypatch.setattr(connection, "_cached_progid", None)
    return client


def test_enumerator_excludes_mcp_and_uses_wide_pointer_sized_handles(monkeypatch):
    names = iter([(7, "etabs-mcp.exe"), (8, "ETABS.exe"), (9, "ETABS64.EXE"), (10, "etabs-helper.exe")])

    def advance(handle, pointer):
        try:
            pid, name = next(names)
        except StopIteration:
            return False
        pointer._obj.th32ProcessID = pid
        pointer._obj.szExeFile = name
        return True

    kernel = SimpleNamespace(
        CreateToolhelp32Snapshot=Mock(return_value=2**40),
        Process32FirstW=Mock(side_effect=advance),
        Process32NextW=Mock(side_effect=advance),
        CloseHandle=Mock(),
    )
    monkeypatch.setattr(connection.sys, "platform", "win32")
    monkeypatch.setattr(ctypes, "WinDLL", Mock(return_value=kernel), raising=False)
    assert connection._find_etabs_pids() == [8, 9]
    assert kernel.CreateToolhelp32Snapshot.restype is ctypes.c_void_p
    kernel.CloseHandle.assert_called_once_with(2**40)


def test_selected_pid_is_used_even_if_another_model_is_active(com):
    selected = object()
    helper = SimpleNamespace(GetObjectProcess=Mock(return_value=SimpleNamespace(SapModel=selected)), GetObject=Mock())
    com.CreateObject.return_value = helper
    assert connection.connect_and_run(lambda model: model is selected, pid=222)
    helper.GetObjectProcess.assert_called_once_with(connection._ETABS_API_PROGID, 222)
    helper.GetObject.assert_not_called()


def test_unavailable_selected_pid_never_falls_back(com):
    helper = SimpleNamespace(GetObjectProcess=Mock(side_effect=OSError("closed")), GetObject=Mock())
    com.CreateObject.return_value = helper
    with pytest.raises(OSError, match="PID 222"):
        connection.connect_and_run(lambda model: model, pid=222)
    helper.GetObject.assert_not_called()


def test_registry_keeps_all_processes_when_start_page_is_unavailable(monkeypatch):
    monkeypatch.setattr(connection.sys, "platform", "win32")
    monkeypatch.setattr(connection, "_find_etabs_pids", lambda: [100, 200])

    def read(fn, pid, timeout):
        if pid == 100:
            raise OSError("sign in pending")
        return {"model_path": "second.edb", "etabs_version": "23.3.2", "model_ready": True, "model_state": "loaded"}

    monkeypatch.setattr(connection, "connect_and_run", read)
    instances = connection.InstanceRegistry().get_active_instances()
    assert [i.pid for i in instances] == [100, 200]
    assert not instances[0].connected
    assert instances[1].file_path == "second.edb"


def test_closed_alias_is_not_reassigned_after_pid_reuse(monkeypatch):
    monkeypatch.setattr(connection.sys, "platform", "win32")
    current = [100]
    monkeypatch.setattr(connection, "_find_etabs_pids", lambda: current[:])
    monkeypatch.setattr(connection, "connect_and_run", Mock(side_effect=OSError("pending")))
    registry = connection.InstanceRegistry()
    assert registry.get_active_instances()[0].alias == "etabs1"
    current.clear()
    assert registry.get_active_instances() == []
    current.append(100)
    assert registry.get_active_instances()[0].alias == "etabs2"
    assert registry.resolve("etabs1") == 100  # tombstone, never an active alias
